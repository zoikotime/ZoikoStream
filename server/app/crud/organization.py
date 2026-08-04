"""DB access for the org self-service API (/organization/*). Pure queries + partial
updates — no HTTP, no aggregation. Mirrors the style of crud/admin.py."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import asc, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Invitation,
    MAX_RESENDS,
    OPEN_STATUSES,
    Organization,
    RESENDABLE_STATUSES,
    User,
    invitation_transition_error,
)

INVITE_TTL_DAYS = 7
# Whitelisted user-list sort columns (prevents arbitrary column injection from the query string).
_USER_SORTS = {
    "created_at": User.created_at,
    "full_name": User.full_name,
    "email": User.email,
    "role": User.role,
}


def slug_taken(db: Session, slug: str, exclude_id: uuid.UUID) -> bool:
    """True if another org already uses this slug (case-insensitive)."""
    return db.scalar(
        select(Organization.id).where(
            func.lower(Organization.slug) == slug.lower(),
            Organization.id != exclude_id,
        )
    ) is not None


def apply_fields(db: Session, org: Organization, data) -> Organization:
    """Set only the fields the client actually supplied (partial patch)."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(org, field, value)
    db.commit()
    db.refresh(org)
    return org


def merge_json(db: Session, org: Organization, attr: str, incoming: dict) -> dict:
    """Merge `incoming` into the JSON column `attr`, preserving keys not sent.
    Reassigns a new dict so SQLAlchemy detects the change on the plain JSON column."""
    current = dict(getattr(org, attr) or {})
    current.update(incoming)
    setattr(org, attr, current)
    db.commit()
    db.refresh(org)
    return current


# ── Organization members (users) ──────────────────────────────────────────────

def list_org_users(db, org_id, q=None, role=None, status=None,
                   sort_by="created_at", order="desc", page=1, page_size=20):
    """Members of one org. Soft-deleted rows are always excluded."""
    stmt = select(User).where(User.org_id == org_id, User.deleted_at.is_(None))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.full_name).like(like),
                              func.lower(User.email).like(like),
                              func.lower(User.username).like(like)))
    if role:
        stmt = stmt.where(User.role == role)
    if status == "active":
        stmt = stmt.where(User.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(User.is_active.is_(False))

    col = _USER_SORTS.get(sort_by, User.created_at)
    stmt = stmt.order_by(asc(col) if order == "asc" else desc(col))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    users = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return users, total


def get_org_user(db, org_id, user_id) -> User | None:
    """A single live member of the org (None if missing, other-org, or soft-deleted)."""
    return db.scalar(
        select(User).where(User.id == user_id, User.org_id == org_id, User.deleted_at.is_(None))
    )


def update_org_user(db, user: User, data) -> User:
    """Apply name/role/status. Reuses schemas.admin.UserUpdate fields."""
    for field in ("full_name", "role", "is_active"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(user, field, val)
    db.commit()
    db.refresh(user)
    return user


def soft_delete_user(db, user: User) -> None:
    """Mark deleted and deactivate (kills their sessions via get_current_user's is_active check)."""
    user.deleted_at = datetime.now(timezone.utc)
    user.is_active = False
    db.commit()


def find_user_by_email(db, email) -> User | None:
    """The user holding this email, INCLUDING a soft-deleted one — users.email is globally
    unique, so a soft-deleted row still owns the address and an INSERT over it is a 500.

    Callers must branch on all three cases explicitly (live+same org, live+other org,
    soft-deleted), which is why this returns the row rather than a boolean. The old
    user_email_taken() collapsed them into one "taken" answer and made re-inviting a
    previously-removed colleague impossible.
    """
    return db.scalar(select(User).where(func.lower(User.email) == email.lower()))


def user_email_taken(db, email) -> bool:
    """Kept for callers that genuinely only need existence (auth.register's 409)."""
    return find_user_by_email(db, email) is not None


def user_in_org(db, org_id, email) -> User | None:
    """A LIVE member of this org holding this email. Separate from find_user_by_email so
    "already on my team" and "exists somewhere on the platform" can never share one check —
    conflating them is what made every event invitation to a colleague a 409."""
    return db.scalar(
        select(User).where(
            func.lower(User.email) == email.lower(),
            User.org_id == org_id,
            User.deleted_at.is_(None),
        )
    )


def reactivate_user(db, user: User, role: str | None = None) -> User:
    """Re-admit a soft-deleted member. Their password_hash is deliberately left ALONE: this
    runs on the public accept path, and letting a token holder set the password of an
    existing account would turn an invitation into a credential reset."""
    user.deleted_at = None
    user.is_active = True
    if role:
        user.role = role
    db.flush()
    return user


def username_taken(db, username) -> bool:
    return db.scalar(select(User.id).where(func.lower(User.username) == username.lower())) is not None


def unique_username(db, base: str) -> str:
    """base, base1, base2, ... — first free username. Mirrors auth.register's scheme."""
    base = base.lower() or "user"
    candidate, n = base, 1
    while username_taken(db, candidate):
        candidate = f"{base}{n}"
        n += 1
    return candidate


# ── Invitations ───────────────────────────────────────────────────────────────

def _hash_token(raw: str) -> str:
    # Tokens are 256-bit random (secrets.token_urlsafe(32)) → sha256 is sufficient; no salt/bcrypt.
    return hashlib.sha256(raw.encode()).hexdigest()


class InvitationStateError(Exception):
    """A refused transition. Raised by crud so create/resend/bulk share one guard — a check
    that lived in the router would be bypassed the moment a bulk endpoint called crud."""


_INVITE_SORTS = {
    "created_at": Invitation.created_at,
    "email": Invitation.email,
    "role": Invitation.role,
    "status": Invitation.status,
    "expires_at": Invitation.expires_at,
    "accepted_at": Invitation.accepted_at,
    "sent_at": Invitation.sent_at,
}


def _live(stmt):
    """Exclude soft-deleted invitations. Applied by every read so a hidden row is hidden
    consistently — and so it never blocks a re-invite (the unique indexes skip it too)."""
    return stmt.where(Invitation.deleted_at.is_(None))


def _expire_stale(db, org_id) -> None:
    """Flip past-due OPEN invitations to `expired` before we read or act on them.

    Reads from OPEN_STATUSES rather than a literal so it can never miss a state the token
    resolver still considers live. Set-based UPDATE (not a row loop) because a busy org's
    list read should not fetch every stale row to mutate it.
    """
    db.execute(
        update(Invitation)
        .where(
            Invitation.org_id == org_id,
            Invitation.status.in_(OPEN_STATUSES),
            Invitation.expires_at < datetime.now(timezone.utc),
            Invitation.deleted_at.is_(None),
        )
        .values(status="expired")
    )
    db.commit()


def open_invite_exists(db, org_id, email, event_id=None) -> bool:
    """Is there already a live invitation for this (org, email, event)?

    Keyed on event_id — inviting one person to two different events is the normal case for an
    internal moderator, and the old email-only check refused it. `event_id=None` renders
    IS NULL, which is exactly the org-membership key.

    This produces the friendly 409. The PARTIAL UNIQUE INDEXES are what actually hold under
    concurrency; their WHERE clause and this filter both derive from OPEN_STATUSES.
    """
    _expire_stale(db, org_id)
    return db.scalar(
        _live(select(Invitation.id)).where(
            Invitation.org_id == org_id,
            func.lower(Invitation.email) == email.lower(),
            Invitation.event_id == event_id,
            Invitation.status.in_(OPEN_STATUSES),
        )
    ) is not None


def list_invitations(db, org_id, status=None, role=None, event_id=None, q=None,
                     sort_by="created_at", order="desc", page=1, page_size=20,
                     include_deleted=False):
    """Server-side search / filter / sort / paginate, matching the events list convention."""
    _expire_stale(db, org_id)
    stmt = select(Invitation).where(Invitation.org_id == org_id)
    if not include_deleted:
        stmt = _live(stmt)
    if status:
        stmt = stmt.where(Invitation.status == status)
    if role:
        # One filter covers both role columns: the console's "Role" facet shows whichever
        # role the row actually carries, so filtering has to look at both.
        stmt = stmt.where(or_(Invitation.role == role, Invitation.event_role == role))
    if event_id is not None:
        stmt = stmt.where(Invitation.event_id == event_id)
    if q:
        stmt = stmt.where(func.lower(Invitation.email).like(f"%{q.lower()}%"))

    col = _INVITE_SORTS.get(sort_by, Invitation.created_at)
    stmt = stmt.order_by(asc(col) if order == "asc" else desc(col))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    # Eager-load what the serializer reads (_inv_out shows the event title and the inviter's
    # name). Measured on a cold session: 18 rows over 6 distinct events cost 10 statements
    # lazily and 5 with these, and the lazy number grows with the variety on the page.
    # selectinload rather than joinedload: two extra IN queries, no row multiplication.
    items = db.scalars(
        stmt.options(selectinload(Invitation.event), selectinload(Invitation.inviter))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return items, total


def get_invitation(db, org_id, inv_id) -> Invitation | None:
    """Org-scoped by construction: an id from another tenant resolves to None -> 404."""
    return db.scalar(
        _live(select(Invitation)).where(Invitation.id == inv_id, Invitation.org_id == org_id)
    )


def invitation_counts(db, org_id) -> dict:
    """Status tallies for the console's KPI row, in one grouped query."""
    _expire_stale(db, org_id)
    rows = db.execute(
        _live(select(Invitation.status, func.count(Invitation.id)))
        .where(Invitation.org_id == org_id)
        .group_by(Invitation.status)
    ).all()
    out = {status: int(n) for status, n in rows}
    # Rows whose email never left the building — an operator needs to see these.
    out["failed_delivery"] = db.scalar(
        _live(select(func.count(Invitation.id))).where(
            Invitation.org_id == org_id, Invitation.send_error.isnot(None),
            Invitation.status.in_(OPEN_STATUSES),
        )
    ) or 0
    return out


def create_invitation(db, org_id, email, role, invited_by_id, *, event_id=None,
                      event_role=None, message=None) -> tuple[Invitation, str]:
    """Returns (invitation, raw_token). Only the hash is stored; the caller delivers the raw
    token exactly once. Authorization (who may grant which role) is the router's — it needs
    the inviter row; this layer records the decision."""
    raw = secrets.token_urlsafe(32)
    inv = Invitation(
        org_id=org_id, email=email.lower(), role=role, token_hash=_hash_token(raw),
        status="pending", invited_by_id=invited_by_id,
        event_id=event_id, event_role=event_role, message=(message or None),
        expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv, raw


def resend_invitation(db, inv: Invitation) -> tuple[Invitation, str]:
    """Rotate the token + reset the clock, returning the row to `pending`.

    The state guard lives HERE, not in the router: single resend, bulk resend and any future
    caller all pass through this function, and the router's old lone `status == "accepted"`
    check would have been silently bypassed by the bulk endpoint.

    Every delivery fact is cleared, because they describe the PREVIOUS attempt — leaving a
    stale send_error or delivered_at behind would have the console reporting the old mail's
    fate for the new link.
    """
    if inv.status not in RESENDABLE_STATUSES:
        raise InvitationStateError(
            f"A {inv.status} invitation cannot be resent — create a new invitation instead"
        )
    if (inv.resend_count or 0) >= MAX_RESENDS:
        raise InvitationStateError(
            f"This invitation has already been resent {MAX_RESENDS} times"
        )
    # Reopening a CLOSED row moves it back into the open set, where the partial unique index
    # applies. If a newer invitation for the same (org, email, event) is already open — which is
    # legal, because closed rows do not block a re-invite — this UPDATE would violate the index
    # and surface as a 500 with an unusable Session. Refuse with a sentence the operator can act
    # on instead.
    if inv.status != "pending" and open_invite_exists(db, inv.org_id, inv.email, inv.event_id):
        raise InvitationStateError(
            "There is already a newer open invitation for that email — cancel it first, or "
            "resend that one instead"
        )
    raw = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    inv.token_hash = _hash_token(raw)   # the previous link dies the instant this commits
    inv.status = "pending"
    inv.accepted_at = None
    inv.declined_at = None
    inv.revoked_at = None
    inv.sent_at = None
    inv.delivered_at = None
    inv.send_error = None
    inv.send_attempts = 0
    inv.last_sent_at = now
    inv.resend_count = (inv.resend_count or 0) + 1
    inv.expires_at = now + timedelta(days=INVITE_TTL_DAYS)
    try:
        db.commit()
    except IntegrityError:
        # Belt and braces behind the check above: the index is the real guarantee, and losing a
        # race to a concurrent create must not leave the Session unusable for the bulk loop.
        db.rollback()
        raise InvitationStateError(
            "There is already an open invitation for that email"
        ) from None
    db.refresh(inv)
    return inv, raw


def claim(db, inv_id, to: str, **cols) -> bool:
    """Atomically move an OPEN invitation to `to`. Returns False if it was not open.

    This is the concurrency guarantee for the whole system. A read-then-check-then-write
    (which is what the code did) lets two simultaneous accepts both pass the check and both
    create a member; worse, an accept racing a decline could leave a live member behind a row
    that reads `rejected` — invisible to revoke, so the access could never be withdrawn.

    The UPDATE takes the row lock and holds it for the rest of the transaction, so the loser
    blocks, re-evaluates against the committed state, and gets 0 rows. Callers MUST perform
    their side effect (create the user, add the assignment) in the SAME transaction.
    """
    res = db.execute(
        update(Invitation)
        .where(
            Invitation.id == inv_id,
            Invitation.status.in_(OPEN_STATUSES),
            Invitation.deleted_at.is_(None),
        )
        .values(status=to, **cols)
    )
    return res.rowcount == 1


def set_invitation_status(db, inv: Invitation, status: str, **cols) -> Invitation:
    """Admin-side transition (cancel / revoke). Guarded by the same pure function the token
    paths use, and applied as a CONDITIONAL update in both branches.

    `allow_noop=False`: re-cancelling an already-cancelled invitation should tell the operator
    so, not silently answer 200 as though it had done something.

    Does NOT commit — the caller owns the transaction, so revoke's status change and its
    EventAssignment deletion land together. A revoke that committed the status first could, if
    the assignment removal then failed, leave the row reading `revoked` while the person kept
    the role.
    """
    err = invitation_transition_error(inv.status, status, has_event=inv.event_id is not None,
                                     allow_noop=False)
    if err:
        raise InvitationStateError(err)
    if inv.status in OPEN_STATUSES:
        if not claim(db, inv.id, status, **cols):
            raise InvitationStateError("That invitation was just changed by someone else")
    else:
        # accepted -> revoked: not an open row, so claim() cannot apply. Still conditional on
        # the status we read, and the rowcount is CHECKED — two concurrent revokes must not
        # both report success and both try to remove the assignment.
        res = db.execute(
            update(Invitation).where(Invitation.id == inv.id, Invitation.status == inv.status)
            .values(status=status, **cols)
        )
        if res.rowcount != 1:
            raise InvitationStateError("That invitation was just changed by someone else")
    return inv


def soft_delete_invitation(db, inv: Invitation) -> None:
    """Hide the row. A hard delete would erase who invited whom at what role with no trace,
    and the audit log records the action, not the invitation's shape."""
    inv.deleted_at = datetime.now(timezone.utc)
    db.commit()


def expired_invitation_ids(db, org_id) -> list:
    """Ids of this org's expired, still-visible invitations — the "Delete expired" target."""
    _expire_stale(db, org_id)
    return list(db.scalars(
        _live(select(Invitation.id)).where(
            Invitation.org_id == org_id, Invitation.status == "expired"
        )
    ).all())


def record_send_result(db, inv_id, *, message_id: str | None, error: str | None) -> None:
    """Write the outcome of ONE delivery attempt. Runs after the response, from a background
    task that owns its own Session, so it takes an id rather than an instance."""
    now = datetime.now(timezone.utc)
    db.execute(
        update(Invitation).where(Invitation.id == inv_id).values(
            provider_message_id=message_id,
            sent_at=now if error is None else None,
            last_sent_at=now,
            send_error=error,
            send_attempts=Invitation.send_attempts + 1,
        )
    )
    db.commit()


def mark_delivered(db, message_id: str) -> int:
    """Resend webhook: delivery confirmed. Matched on provider_message_id ALONE — matching on
    the recipient address would flip every open invitation for that email across ALL
    organizations, which is the one place in this codebase where a write would not be scoped
    by a JWT. Writes a timestamp only; `status` is never touched by an external system."""
    res = db.execute(
        update(Invitation)
        .where(Invitation.provider_message_id == message_id, Invitation.delivered_at.is_(None))
        .values(delivered_at=datetime.now(timezone.utc))
    )
    db.commit()
    return res.rowcount


def mark_send_failed(db, message_id: str, reason: str) -> int:
    """Resend webhook: hard bounce. Recorded as a delivery fact, not a lifecycle change — the
    invitation is still redeemable if the person gets the link another way."""
    res = db.execute(
        update(Invitation)
        .where(Invitation.provider_message_id == message_id)
        .values(send_error=reason[:400], delivered_at=None)
    )
    db.commit()
    return res.rowcount


def find_invitation_by_token(db, raw: str) -> Invitation | None:
    """Resolve a raw token to a REDEEMABLE invitation, or None.

    Open, unexpired and not deleted are all filtered HERE rather than in the router, for two
    reasons. First, every invalid case then collapses to one indistinguishable outcome, so the
    public endpoints cannot be used to tell "no such token" from "already used" from "expired"
    (same discipline as crud.event.find_access_link). Second, it removes the unauthenticated
    WRITE the router used to perform — it flipped the row to `expired` mid-request, which both
    mutated state on an anonymous call and gave that branch its own latency signature.
    """
    if not raw:
        return None
    return db.scalar(
        _live(select(Invitation)).where(
            Invitation.token_hash == _hash_token(raw),
            Invitation.status.in_(OPEN_STATUSES),
            Invitation.expires_at > datetime.now(timezone.utc),
        )
    )


def find_open_invitation_for_email(db, email) -> Invitation | None:
    """The newest live invitation for this address, across every org.

    Used by POST /auth/register so someone who signs up instead of clicking their link still
    lands in the organization that invited them. NOT org-scoped by design — at registration
    time there is no session to scope by, and the address is the only identity available. It
    is a read of a row that was created FOR this address, so it leaks nothing to anyone else.
    """
    return db.scalar(
        _live(select(Invitation))
        .where(
            func.lower(Invitation.email) == email.lower(),
            Invitation.status.in_(OPEN_STATUSES),
            Invitation.expires_at > datetime.now(timezone.utc),
        )
        .order_by(desc(Invitation.created_at))
        .limit(1)
    )


def build_member(db, inv: Invitation, full_name, username, password_hash) -> User:
    """Create the member for a BRAND-NEW account. Flushes but does NOT commit: the caller
    commits once, together with the claim() and the event assignment, so a failure anywhere
    leaves no half-accepted invitation."""
    user = User(
        org_id=inv.org_id, email=inv.email, role=inv.role, full_name=full_name,
        username=username, password_hash=password_hash, is_active=True,
    )
    db.add(user)
    db.flush()
    return user
