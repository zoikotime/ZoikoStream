"""DB access for the org self-service API (/organization/*). Pure queries + partial
updates — no HTTP, no aggregation. Mirrors the style of crud/admin.py."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from ..models import Invitation, Organization, User, WebhookDelivery, WebhookEndpoint

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


def user_email_taken(db, email) -> bool:
    """Any user (any org) already holds this email — email is globally unique on users."""
    return db.scalar(select(User.id).where(func.lower(User.email) == email.lower())) is not None


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


def _expire_stale(db, org_id) -> None:
    """Lazily flip past-due pending invitations to `expired` before we read/act on them."""
    now = datetime.now(timezone.utc)
    stale = db.scalars(
        select(Invitation).where(
            Invitation.org_id == org_id,
            Invitation.status == "pending",
            Invitation.expires_at < now,
        )
    ).all()
    for inv in stale:
        inv.status = "expired"
    if stale:
        db.commit()


def pending_invite_exists(db, org_id, email) -> bool:
    _expire_stale(db, org_id)
    return db.scalar(
        select(Invitation.id).where(
            Invitation.org_id == org_id,
            func.lower(Invitation.email) == email.lower(),
            Invitation.status == "pending",
        )
    ) is not None


def list_invitations(db, org_id, status=None, page=1, page_size=20):
    _expire_stale(db, org_id)
    stmt = select(Invitation).where(Invitation.org_id == org_id)
    if status:
        stmt = stmt.where(Invitation.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(
        stmt.order_by(desc(Invitation.created_at)).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return items, total


def get_invitation(db, org_id, inv_id) -> Invitation | None:
    return db.scalar(select(Invitation).where(Invitation.id == inv_id, Invitation.org_id == org_id))


def create_invitation(db, org_id, email, role, invited_by_id) -> tuple[Invitation, str]:
    """Returns (invitation, raw_token). Only the hash is stored; caller delivers raw_token once."""
    raw = secrets.token_urlsafe(32)
    inv = Invitation(
        org_id=org_id, email=email.lower(), role=role, token_hash=_hash_token(raw),
        status="pending", invited_by_id=invited_by_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv, raw


def resend_invitation(db, inv: Invitation) -> tuple[Invitation, str]:
    """Rotate the token + reset the clock, returning to pending. Old link stops working.

    This is a deliberate re-issue, not a re-send: the product's existing rule is that a
    resent invitation is a NEW offer with a new deadline, and ZST-EC-001 ORG-001 permits
    preserving that as long as it does not silently widen authorization. It does not - the
    role is unchanged, the old link is revoked by the hash rotation, and the new expiry is
    the standard INVITE_TTL_DAYS window rather than an extension bolted onto the old one.

    The ORG-001 notification markers are cleared with it. They describe transitions of the
    offer that has just been replaced, so leaving them set would suppress the notice for the
    new deadline; clearing them lets the new offer be announced (and later reminded about)
    exactly once.
    """
    raw = secrets.token_urlsafe(32)
    inv.token_hash = _hash_token(raw)
    inv.status = "pending"
    inv.accepted_at = None
    inv.expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    inv.invited_notified_at = None
    inv.reminder_sent_at = None
    inv.expired_notified_at = None
    inv.revoked_notified_at = None
    db.commit()
    db.refresh(inv)
    return inv, raw


def rotate_invitation_token(db, inv: Invitation) -> str:
    """Issue a fresh raw token for an invitation WITHOUT touching its expiry or status.

    The reminder needs a working link, and only the token hash is stored, so the original
    raw token no longer exists anywhere by the time a reminder is due. Rotating is the only
    way to produce a usable link - but the deadline the reminder announces must stay the
    deadline the invitation already had, which is why this deliberately does not call
    resend_invitation. The previous link stops working, which is correct: one live link per
    invitation is the same guarantee the resend path already makes.
    """
    raw = secrets.token_urlsafe(32)
    inv.token_hash = _hash_token(raw)
    db.commit()
    db.refresh(inv)
    return raw


def set_invitation_status(db, inv: Invitation, status: str) -> Invitation:
    inv.status = status
    db.commit()
    db.refresh(inv)
    return inv


def delete_invitation(db, inv: Invitation) -> None:
    db.delete(inv)
    db.commit()


def find_invitation_by_token(db, raw: str) -> Invitation | None:
    return db.scalar(select(Invitation).where(Invitation.token_hash == _hash_token(raw)))


def accept_invitation(db, inv: Invitation, full_name, username, password_hash) -> User:
    """Create the member and mark the invite accepted, atomically. Caller has already
    validated the token/status/expiry and that email + username are free.

    The new member is created ALREADY email-verified (ZST-EC-001 IDN-001). That is not a
    shortcut: redeeming an invitation token proves control of the invited address, which is
    exactly the proof an IDN-001 challenge collects, and the token was delivered to that
    address and nowhere else. Sending a second verification email for an address we just
    proved would be a redundant Class A message. The invitee never chooses this address —
    an authorized administrator did — so there is no self-asserted address to verify.
    """
    now = datetime.now(timezone.utc)
    user = User(
        org_id=inv.org_id, email=inv.email, role=inv.role, full_name=full_name,
        username=username, password_hash=password_hash, is_active=True,
        email_verified=True, email_verified_at=now,
    )
    db.add(user)
    inv.status = "accepted"
    inv.accepted_at = now
    db.commit()
    db.refresh(user)
    return user


# ── Webhook endpoints + delivery log (services/webhooks.py is the sender/retrier) ───────

def list_webhook_endpoints(db, org_id) -> list[WebhookEndpoint]:
    return db.scalars(
        select(WebhookEndpoint).where(WebhookEndpoint.org_id == org_id)
        .order_by(WebhookEndpoint.created_at.desc())
    ).all()


def get_webhook_endpoint(db, org_id, endpoint_id) -> WebhookEndpoint | None:
    return db.scalar(
        select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id, WebhookEndpoint.org_id == org_id)
    )


def create_webhook_endpoint(db, org_id, url, label, events, created_by) -> WebhookEndpoint:
    ep = WebhookEndpoint(
        org_id=org_id, url=url, label=label, events=events,
        # Not hash-only like an API key — see models/webhook.py's docstring for why this
        # secret has to stay readable.
        secret=f"whsec_{secrets.token_urlsafe(32)}",
        created_by=created_by,
    )
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return ep


def update_webhook_endpoint(db, ep: WebhookEndpoint, *, url=None, label=None, events=None, enabled=None) -> WebhookEndpoint:
    if url is not None:
        ep.url = url
    if label is not None:
        ep.label = label
    if events is not None:
        ep.events = events
    if enabled is not None:
        ep.enabled = enabled
    db.commit()
    db.refresh(ep)
    return ep


def delete_webhook_endpoint(db, ep: WebhookEndpoint) -> None:
    db.delete(ep)
    db.commit()


def list_webhook_deliveries(db, endpoint_id, limit: int = 50) -> list[WebhookDelivery]:
    return db.scalars(
        select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint_id)
        .order_by(WebhookDelivery.created_at.desc()).limit(limit)
    ).all()
