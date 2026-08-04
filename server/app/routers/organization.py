"""Org self-service API — the backend for the Organization Profile & Settings pages.

Isolation: the target org is ALWAYS the caller's `user.org_id` (from the JWT); org_id is
never read from the request body. Super admin isn't blocked — it operates on its own org
here and uses /admin/* for cross-org management. Reads: any member. Writes + sensitive
reads (security, developer): org admin (require_org_admin already admits super_admin)."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import admin as admin_crud
from ..crud import event as crud_event
from ..crud import organization as crud
from ..db import SessionLocal, get_db
from ..email import (
    send_invitation_accepted_email,
    send_invitation_declined_email,
    send_invitation_email,
    send_invitation_revoked_email,
)
from ..models import (
    DEFAULT_PLATFORM_ROLE_FOR_EVENT_ROLE,
    INVITE_EVENT_ROLES,
    INVITE_PLATFORM_ROLES,
    MAX_RESENDS,
    ORG_ASSIGNABLE_ROLES,
    Organization,
    RESENDABLE_STATUSES,
    User,
    invitation_transition_error,
    status_label,
)
from ..ratelimit import client_ip, rate_limit
from ..schemas.admin import AdminUserOut, Page, UserUpdate
from ..schemas.auth import TokenOut, UserOut
from ..schemas.organization import (
    InvitationAccept,
    InvitationAcceptResult,
    InvitationAction,
    InvitationBulkAction,
    InvitationBulkCreate,
    InvitationBulkResult,
    InvitationCreate,
    InvitationOut,
    InvitationPreview,
    InvitationReject,
    OrgBrandingOut,
    OrgBrandingUpdate,
    OrgDeveloperOut,
    OrgDomainOut,
    OrgDomainUpdate,
    OrgMeOut,
    OrgNotifications,
    OrgProfileOut,
    OrgProfileUpdate,
    OrgSecurity,
)
from ..security import create_access_token, get_current_user, hash_password, require_org_admin
from ..services import org as org_svc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/organization", tags=["organization"])


def _audit(db, actor, request: Request, action: str, **kw) -> None:
    """Every invitation transition writes a compliance row, reusing the platform's single
    audit writer. Called AFTER the transaction it describes has committed — create_audit_log
    commits internally, so splicing it mid-transaction would break the accept path's
    all-or-nothing boundary.

    The IP comes from ratelimit.client_ip, which deliberately ignores X-Forwarded-For:
    trusting that header would write a caller-controlled value into the one record that
    cannot be re-derived.
    """
    try:
        admin_crud.create_audit_log(db, actor=actor, action=action, ip=client_ip(request), **kw)
    except Exception:  # noqa: BLE001
        # The action ALREADY committed. Turning a succeeded accept into a 500 because the audit
        # row failed would be the worst outcome available: the caller retries, the token is
        # gone, and the invitee is stranded holding a consumed invitation. So the failure is
        # logged loudly and the request still succeeds.
        db.rollback()
        log.exception("audit write failed for %s (the action itself succeeded)", action)


def get_my_org(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Organization:
    """The caller's own organization, resolved from the token. The single point where
    org_id is bound — nothing downstream accepts it from the client."""
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


def get_my_org_admin(
    _: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org),
) -> Organization:
    """get_my_org gated to org admin (and above) for mutations/sensitive reads. Reuses the
    same lookup — require_org_admin just adds the 403 gate (super admin passes)."""
    return org


# ── Overview (dashboard) ──────────────────────────────────────────────────────

@router.get("/overview")
def overview(
    range_: str = Query("24h", alias="range", pattern="^(1h|24h|7d|30d)$"),
    workspace: str | None = Query(None, description="workspace slug; only 'production' exists"),
    include_test: bool = Query(True),
    org: Organization = Depends(get_my_org),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Whole-page payload for /organization/dashboard, scoped to the caller's own org.

    Readable by any member: it reports on the organization the caller already belongs to and
    carries no cross-tenant or platform-wide figures. `workspace` is accepted so the console's
    switcher round-trips, but this platform has one implicit workspace per org — an unknown
    slug is a 404 rather than silently returning the default.
    """
    if workspace and workspace not in {w["slug"] for w in org_svc.workspaces(org)}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown workspace '{workspace}'")
    return org_svc.overview(db, org, user, range_=range_, include_test=include_test)


@router.get("/console-state")
def console_state(
    org: Organization = Depends(get_my_org),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Small payload the org shell polls on every page: identity, workspace, nav badges and
    the service-health verdict for the services this org actually uses."""
    return org_svc.console_state(db, org, user)


# ── Identity / Profile ────────────────────────────────────────────────────────

@router.get("/me", response_model=OrgMeOut)
def get_me(org: Organization = Depends(get_my_org)):
    return org


@router.get("/profile", response_model=OrgProfileOut)
def get_profile(org: Organization = Depends(get_my_org)):
    return org


@router.patch("/profile", response_model=OrgProfileOut)
def update_profile(
    data: OrgProfileUpdate,
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    if data.slug is not None and crud.slug_taken(db, data.slug, org.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Slug already taken")
    return crud.apply_fields(db, org, data)


# ── Branding ──────────────────────────────────────────────────────────────────

@router.get("/branding", response_model=OrgBrandingOut)
def get_branding(org: Organization = Depends(get_my_org)):
    return org


@router.patch("/branding", response_model=OrgBrandingOut)
def update_branding(
    data: OrgBrandingUpdate,
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    return crud.apply_fields(db, org, data)


# ── Developer (read-only) ─────────────────────────────────────────────────────

@router.get("/developer", response_model=OrgDeveloperOut)
def get_developer(org: Organization = Depends(get_my_org_admin)):
    return OrgDeveloperOut(api_keys=org.api_keys or [], webhook_urls=org.webhook_urls or [])


# ── Notifications ─────────────────────────────────────────────────────────────

@router.get("/notifications", response_model=OrgNotifications)
def get_notifications(org: Organization = Depends(get_my_org)):
    return OrgNotifications(**(org.notifications or {}))


@router.patch("/notifications", response_model=OrgNotifications)
def update_notifications(
    data: OrgNotifications,
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    merged = crud.merge_json(db, org, "notifications", data.model_dump(exclude_unset=True))
    return OrgNotifications(**merged)


# ── Security ──────────────────────────────────────────────────────────────────

@router.get("/security", response_model=OrgSecurity)
def get_security(org: Organization = Depends(get_my_org_admin)):
    return OrgSecurity(**(org.security or {}))


@router.patch("/security", response_model=OrgSecurity)
def update_security(
    data: OrgSecurity,
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    merged = crud.merge_json(db, org, "security", data.model_dump(exclude_unset=True))
    return OrgSecurity(**merged)


# ── Domain ────────────────────────────────────────────────────────────────────

@router.get("/domain", response_model=OrgDomainOut)
def get_domain(org: Organization = Depends(get_my_org)):
    return org


@router.patch("/domain", response_model=OrgDomainOut)
def update_domain(
    data: OrgDomainUpdate,
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    # Changing the domain drops it back to unverified — a real DNS-verification flow
    # (later phase) is the only thing that sets domain_verified = True. Only react to a
    # domain the client actually sent (exclude_unset), not the None default of an empty body.
    fields = data.model_dump(exclude_unset=True)
    if "domain" in fields and fields["domain"] != org.domain:
        org.domain_verified = False
    return crud.apply_fields(db, org, data)


# ── Members (users) ────────────────────────────────────────────────────────────
# Org-admin only: this is the member-management surface. Every query is scoped to the
# admin's own org_id (from the JWT); org_id is never taken from the client.

def _user_out(u: User, org_name: str | None) -> AdminUserOut:
    out = AdminUserOut.model_validate(u)
    out.organization_name = org_name
    return out


@router.get("/users", response_model=Page)
def list_org_users(
    q: str | None = None,
    role: str | None = Query(None),
    status_: str | None = Query(None, alias="status"),
    sort_by: str = Query("created_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    users, total = crud.list_org_users(db, admin.org_id, q=q, role=role, status=status_,
                                       sort_by=sort_by, order=order, page=page, page_size=page_size)
    org_name = admin.organization.name if admin.organization else None
    return Page(items=[_user_out(u, org_name) for u in users], total=total, page=page, page_size=page_size)


@router.get("/users/{user_id}", response_model=AdminUserOut)
def get_org_user(user_id: uuid.UUID, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    u = crud.get_org_user(db, admin.org_id, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return _user_out(u, admin.organization.name if admin.organization else None)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_org_user(user_id: uuid.UUID, data: UserUpdate,
                    admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    u = crud.get_org_user(db, admin.org_id, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if u.role == "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot modify a super admin")
    if data.role is not None and data.role not in ORG_ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid role")
    # Self-lockout guard: an admin can't demote or deactivate their own account.
    if u.id == admin.id and (data.is_active is False or (data.role and data.role != admin.role)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot demote or deactivate yourself")
    crud.update_org_user(db, u, data)
    return _user_out(u, admin.organization.name if admin.organization else None)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org_user(user_id: uuid.UUID, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    u = crud.get_org_user(db, admin.org_id, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if u.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if u.role == "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete a super admin")
    crud.soft_delete_user(db, u)  # retained in DB, hidden from listings, sessions killed


# ── Invitations ───────────────────────────────────────────────────────────────
#
# Two shapes, one table: event_id NULL = join the organization; event_id set = join the
# organization AND fill one event role (an EventAssignment is created on accept).
#
# Authorization lives in three layers and each one is load-bearing:
#   1. require_org_admin on every management route.
#   2. _authorize_grant — WHICH roles this inviter may grant (models.invitation tables).
#      A rank ladder cannot express "hosts may not invite moderators", because moderator
#      ranks BELOW host; the permitted set is written out per inviter role instead.
#   3. The same check re-runs at ACCEPT. A 7-day credential must be re-authorized when it is
#      consumed, not only when it is issued — the inviter may have been demoted or removed.
#
# Every refusal on the three PUBLIC routes (preview / accept / decline) returns the identical
# response, so possession of a random token reveals exactly one bit: it worked, or it didn't.

# One string for every public refusal. Unknown token, wrong state, expired, deleted event,
# suspended org, demoted inviter, address belonging to another organization — all identical.
_PUBLIC_REFUSAL = "This invitation can't be completed. Ask your administrator for a new one."

# Budgets for the unauthenticated surface, matching routers/auth.py's shape. NOT justified by
# token brute force — a 256-bit token is ~2^255 indexed misses — but by keeping an anonymous
# endpoint from being a free-to-call amplifier.
_ACCEPT_LIMIT = rate_limit("invite-accept", limit=10, window=300.0)
_PREVIEW_LIMIT = rate_limit("invite-preview", limit=30, window=300.0)


def _invite_url(token: str, *, decline: bool = False) -> str:
    """The link the invitee receives. `/accept-invitation` is the SPA's registered route —
    the previous `/accept-invite` matched nothing and every invitation email 404'd.

    The token rides in the FRAGMENT, which browsers never send to a server: it stays out of
    access logs, Referer headers and CDN logs. The page reads it from the hash and POSTs it
    in a body.
    """
    base = (settings.CORS_ORIGINS.split(",")[0].strip() or "https://zoikostream.com").rstrip("/")
    return f"{base}/accept-invitation?decline=1#token={token}" if decline else f"{base}/accept-invitation#token={token}"


def _delivery_state(inv) -> str:
    """not_sent | sent | delivered | failed — derived, never stored as a status.

    `delivered` requires the Resend webhook to have confirmed it. With no webhook configured
    a mailed invitation stays `sent`, which is the honest answer: this server handed the
    message to Resend and does not know what happened next.
    """
    if inv.send_error:
        return "failed"
    if inv.delivered_at:
        return "delivered"
    return "sent" if inv.sent_at else "not_sent"


def _inv_out(inv, token: str | None = None, url: str | None = None) -> InvitationOut:
    has_event = inv.event_id is not None
    return InvitationOut(
        id=inv.id, email=inv.email, role=inv.role, status=inv.status,
        status_label=status_label(inv.status),
        invited_by=(inv.inviter.full_name or inv.inviter.email) if inv.inviter else None,
        invited_by_email=inv.inviter.email if inv.inviter else None,
        expires_at=inv.expires_at, accepted_at=inv.accepted_at, created_at=inv.created_at,
        event_id=inv.event_id,
        # Read through the relationship so a soft-deleted event still shows its name in the
        # history rather than a bare id.
        event_title=(inv.event.title if inv.event else None),
        event_role=inv.event_role, message=inv.message,
        sent_at=inv.sent_at, last_sent_at=inv.last_sent_at, delivered_at=inv.delivered_at,
        send_error=inv.send_error, send_attempts=inv.send_attempts or 0,
        resend_count=inv.resend_count or 0, delivery=_delivery_state(inv),
        # Straight from the transition table — the console shows a button only when the
        # server would accept the action.
        # allow_noop=False so a cancelled row does not advertise "Cancel" purely because it is
        # already cancelled — the flags answer "may this change?", not "is it already there?".
        can_resend=inv.status in RESENDABLE_STATUSES and (inv.resend_count or 0) < MAX_RESENDS,
        can_cancel=invitation_transition_error(inv.status, "cancelled", has_event=has_event,
                                               allow_noop=False) is None,
        can_revoke=invitation_transition_error(inv.status, "revoked", has_event=has_event,
                                               allow_noop=False) is None,
        declined_at=inv.declined_at, revoked_at=inv.revoked_at,
        invite_token=token, invite_url=url,
    )


def _authorize_grant(inviter: User, platform_role: str, event_role: str | None) -> str | None:
    """Which roles may this inviter grant? Returns an error message, or None.

    Used at CREATE and again at ACCEPT. super_admin and org_admin may grant anything in the
    org-assignable set; a host or moderator may bring in people to appear on an event but
    never staff who could run it.
    """
    allowed_platform = INVITE_PLATFORM_ROLES.get(inviter.role)
    if not allowed_platform:
        return "Your role cannot send invitations"
    if platform_role not in allowed_platform:
        return f"You cannot invite someone as {platform_role}"
    if event_role is not None:
        allowed_event = INVITE_EVENT_ROLES.get(inviter.role) or ()
        if event_role not in allowed_event:
            return f"You cannot assign the {event_role} role on an event"
    return None


def _resolve_event(db: Session, org_id, event_id):
    """Resolve an event id to a LIVE event in this org, or raise the events API's own 404.

    crud_event.get_event filters org_id AND deleted_at together, which is exactly the pair
    that matters: without it, an event_id from a request body would create an EventAssignment
    on another tenant's event. Re-run at accept rather than trusting the stored id, because
    the event may have been deleted during the invitation's 7-day life.
    """
    ev = crud_event.get_event(db, org_id, event_id)
    if ev is None:
        # Same string as routers/events.py so this is not an existence oracle.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return ev


def _plan_invitation(db: Session, admin: User, *, email: str, role: str | None,
                     event_id, event_role: str | None) -> tuple[str, object | None]:
    """Shared validation for create and bulk-create. Returns (platform_role, event).

    Raises HTTPException for anything the admin must fix. Kept in ONE function so the bulk
    endpoint cannot drift from the single-create endpoint — the reason the resend guard also
    lives in crud rather than here.
    """
    event = _resolve_event(db, admin.org_id, event_id) if event_id else None

    # An invitation always creates an ORGANIZATION MEMBER, and org membership alone opens
    # every event in the org (services/viewer.access_for -> organization_member), the org
    # dashboard, and the audience-passphrase exemption. So an event invitation with no staff
    # role is never the right tool for an attendee — an access link is.
    if event_id and not event_role:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Pick an event role for this person, or use a viewer access link "
                            "for an attendee — an invitation adds staff to your organization")
    if event_role and not event_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "An event role needs an event")

    # Derive the platform role from the event role unless the admin chose one. Deliberately
    # conservative (cohost/producer/panelist -> speaker): an event role must never silently
    # inflate a platform role, because the platform role outlives the assignment and applies
    # to every OTHER event in the org.
    platform_role = role or (
        DEFAULT_PLATFORM_ROLE_FOR_EVENT_ROLE.get(event_role, "viewer") if event_role else "viewer"
    )
    if platform_role not in ORG_ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid role")

    err = _authorize_grant(admin, platform_role, event_role)
    if err:
        raise HTTPException(status.HTTP_403_FORBIDDEN, err)

    existing = crud.find_user_by_email(db, email)
    if existing is not None:
        if existing.org_id != admin.org_id:
            # A deliberate, documented, authenticated-org-admin-only signal that the address
            # has an account elsewhere. It cannot be closed by rewording (201-vs-409 answers
            # the same question), and one user = one org here, so the invitation genuinely
            # cannot be honoured. Rate-limited at the router; recorded here as accepted risk.
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "That email already has a ZoikoStream account in another "
                                "organization, so it can't be added to yours")
        if existing.deleted_at is None and event_id is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "That person is already a member")
        # Live same-org member + an event invitation is the PRIMARY flow this feature exists
        # for (invite a colleague to host/moderate/speak), so it falls through.

    if crud.open_invite_exists(db, admin.org_id, email, event_id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "There is already an open invitation for that email"
                            + (" on this event" if event_id else ""))
    return platform_role, event


def _deliver(inv_id: uuid.UUID, *, to: str, org_name: str, inviter: str, role_label: str,
             expires: str, accept_url: str, decline_url: str,
             event_title: str | None, event_when: str | None, message: str | None) -> None:
    """Send the invitation email AFTER the response, then record the outcome on the row.

    Runs as a BackgroundTask, which has no Session and no ORM instance — hence the id and a
    fresh session. Recording the result is the whole point: a swallowed failure would leave an
    invitation looking sent forever with nobody able to see why it never arrived.
    """
    ok, message_id, error = send_invitation_email(
        to, org_name, inviter, accept_url, decline_url,
        role_label=role_label, expires=expires, event_title=event_title,
        event_when=event_when, message=message,
    )
    db = SessionLocal()
    try:
        crud.record_send_result(db, inv_id, message_id=message_id,
                               error=None if ok else (error or "Delivery failed"))
    finally:
        db.close()


def _queue_delivery(background: BackgroundTasks, inv, admin: User, raw: str, event) -> None:
    org_name = admin.organization.name if admin.organization else "your organization"
    background.add_task(
        _deliver, inv.id,
        to=inv.email, org_name=org_name,
        inviter=admin.full_name or admin.email,
        role_label=_role_display(inv),
        expires=inv.expires_at.strftime("%d %b %Y"),
        accept_url=_invite_url(raw), decline_url=_invite_url(raw, decline=True),
        event_title=event.title if event else None,
        event_when=event.start_time.strftime("%d %b %Y, %I:%M %p") if (event and event.start_time) else None,
        message=inv.message,
    )


_ROLE_LABELS = {"org_admin": "Organization Admin", "host": "Host", "moderator": "Moderator",
                "speaker": "Speaker", "viewer": "Viewer", "producer": "Producer",
                "cohost": "Co-Host", "panelist": "Panelist"}


def _role_display(inv) -> str:
    """What the invitee is told their role is: the EVENT role when there is one (that is what
    they will actually be doing), otherwise the platform role."""
    key = inv.event_role or inv.role
    return _ROLE_LABELS.get(key, key.replace("_", " ").title())


# ── Invitations: admin management ─────────────────────────────────────────────

@router.get("/invitations", response_model=Page)
def list_invitations(
    status_: str | None = Query(None, alias="status"),
    role: str | None = Query(None),
    event_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, description="match on email"),
    sort_by: str = Query("created_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    items, total = crud.list_invitations(db, admin.org_id, status=status_, role=role,
                                        event_id=event_id, q=q, sort_by=sort_by, order=order,
                                        page=page, page_size=page_size)
    return Page(items=[_inv_out(i) for i in items], total=total, page=page, page_size=page_size)


@router.get("/invitations/stats")
def invitation_stats(admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Status tallies for the console's KPI row, in one grouped query rather than counting a
    client-side page (which would only ever describe the visible 25 rows)."""
    return crud.invitation_counts(db, admin.org_id)


@router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
def create_invitation(data: InvitationCreate, background: BackgroundTasks, request: Request,
                      admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    email = data.email.lower()
    platform_role, event = _plan_invitation(
        db, admin, email=email, role=data.role, event_id=data.event_id, event_role=data.event_role
    )
    try:
        inv, raw = crud.create_invitation(
            db, admin.org_id, email, platform_role, admin.id,
            event_id=data.event_id, event_role=data.event_role, message=data.message,
        )
    except IntegrityError:
        # The partial unique index is the real race-safe guarantee; open_invite_exists above
        # only produces the friendlier message. Report the SAME conflict either way so the
        # index and the pre-check can never appear to disagree.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "There is already an open invitation for that email")

    _audit(db, admin, request, "invitation.created" if data.send_email else "invitation.created_manual",
           target_type="invitation", target_id=inv.id, org_id=admin.org_id,
           meta={"email": email, "role": platform_role, "event_id": str(data.event_id) if data.event_id else None,
                 "event_role": data.event_role, "emailed": data.send_email})

    url = _invite_url(raw)
    if data.send_email:
        _queue_delivery(background, inv, admin, raw, event)
    # The raw token is returned exactly once. There is no recovery endpoint — only the hash
    # is stored, so a lost link is genuinely unrecoverable and must be resent.
    return _inv_out(inv, token=raw, url=url)


@router.post("/invitations/bulk", response_model=InvitationBulkResult,
             status_code=status.HTTP_201_CREATED)
def bulk_create_invitations(data: InvitationBulkCreate, background: BackgroundTasks,
                           request: Request, admin: User = Depends(require_org_admin),
                           db: Session = Depends(get_db)):
    """Invite many addresses with the same settings.

    Per-address outcomes, never all-or-nothing. The rollback inside the loop is MANDATORY: a
    single IntegrityError leaves the Session unusable, and without it the first duplicate
    makes every remaining address fail with an unrelated reason.
    """
    result = InvitationBulkResult()
    sent = 0
    for raw_email in dict.fromkeys(e.lower() for e in data.emails):
        try:
            platform_role, event = _plan_invitation(
                db, admin, email=raw_email, role=data.role,
                event_id=data.event_id, event_role=data.event_role,
            )
            inv, raw = crud.create_invitation(
                db, admin.org_id, raw_email, platform_role, admin.id,
                event_id=data.event_id, event_role=data.event_role, message=data.message,
            )
        except HTTPException as exc:
            result.failed.append({"email": raw_email, "reason": exc.detail})
            continue
        except IntegrityError:
            db.rollback()
            result.failed.append({"email": raw_email,
                                  "reason": "There is already an open invitation for that email"})
            continue
        result.succeeded.append(inv.id)
        if data.send_email:
            _queue_delivery(background, inv, admin, raw, event)
            sent += 1

    # ONE audit row for the batch, not N — the operator performed one action.
    _audit(db, admin, request, "invitation.bulk_created", target_type="invitation",
           org_id=admin.org_id,
           meta={"requested": len(data.emails), "created": len(result.succeeded),
                 "failed": len(result.failed), "emailed": sent,
                 "event_id": str(data.event_id) if data.event_id else None})
    return result


@router.post("/invitations/bulk-action", response_model=InvitationBulkResult)
def bulk_invitation_action(data: InvitationBulkAction, background: BackgroundTasks,
                          request: Request, admin: User = Depends(require_org_admin),
                          db: Session = Depends(get_db)):
    """Bulk resend / cancel / delete. Every guard is reached through the same crud functions
    the single-row endpoints use, so this cannot bypass the transition table."""
    result = InvitationBulkResult()
    for inv_id in dict.fromkeys(data.ids):
        inv = crud.get_invitation(db, admin.org_id, inv_id)
        if inv is None:
            result.failed.append({"id": str(inv_id), "reason": "Invitation not found"})
            continue
        try:
            if data.action == "resend":
                inv, raw = crud.resend_invitation(db, inv)
                event = crud_event.get_event(db, admin.org_id, inv.event_id) if inv.event_id else None
                _queue_delivery(background, inv, admin, raw, event)
            elif data.action == "cancel":
                crud.set_invitation_status(db, inv, "cancelled")
                db.commit()
            else:
                crud.soft_delete_invitation(db, inv)
        except crud.InvitationStateError as exc:
            result.failed.append({"id": str(inv_id), "reason": str(exc)})
            continue
        result.succeeded.append(inv_id)

    _audit(db, admin, request, f"invitation.bulk_{data.action}", target_type="invitation",
           org_id=admin.org_id,
           meta={"count": len(result.succeeded), "failed": len(result.failed)})
    return result


@router.post("/invitations/delete-expired", response_model=InvitationBulkResult)
def delete_expired_invitations(request: Request, admin: User = Depends(require_org_admin),
                               db: Session = Depends(get_db)):
    """Tidy the list. HIDES the rows (deleted_at) rather than destroying them: an invitation
    records who invited whom at what role, and the unique indexes skip hidden rows so this
    never blocks re-inviting the same person."""
    result = InvitationBulkResult()
    for inv_id in crud.expired_invitation_ids(db, admin.org_id):
        inv = crud.get_invitation(db, admin.org_id, inv_id)
        if inv is not None:
            crud.soft_delete_invitation(db, inv)
            result.succeeded.append(inv_id)
    _audit(db, admin, request, "invitation.delete_expired", target_type="invitation",
           org_id=admin.org_id, meta={"count": len(result.succeeded)})
    return result


@router.patch("/invitations/{invitation_id}", response_model=InvitationOut)
def update_invitation(invitation_id: uuid.UUID, data: InvitationAction,
                      background: BackgroundTasks, request: Request,
                      admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    inv = crud.get_invitation(db, admin.org_id, invitation_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    now = datetime.now(timezone.utc)
    try:
        if data.action == "resend":
            inv, raw = crud.resend_invitation(db, inv)
            event = crud_event.get_event(db, admin.org_id, inv.event_id) if inv.event_id else None
            _queue_delivery(background, inv, admin, raw, event)
            _audit(db, admin, request, "invitation.resent", target_type="invitation",
                   target_id=inv.id, org_id=admin.org_id,
                   meta={"email": inv.email, "resend_count": inv.resend_count})
            return _inv_out(inv, token=raw, url=_invite_url(raw))

        if data.action == "cancel":
            crud.set_invitation_status(db, inv, "cancelled")
            db.commit()
            db.refresh(inv)
            _audit(db, admin, request, "invitation.cancelled", target_type="invitation",
                   target_id=inv.id, org_id=admin.org_id, meta={"email": inv.email})
            return _inv_out(inv)

        # revoke — withdraw access AFTER acceptance. Its only effect is removing the event
        # assignment; the transition guard refuses it for an org-membership invitation,
        # because that would mean deleting a User and bypassing the guards on
        # DELETE /organization/users/{id}.
        #
        # ONE transaction for both writes. Committing the status first and then failing to
        # remove the assignment would leave the row reading `revoked` while the person kept
        # the role — the exact state nobody would think to look for.
        member = crud.user_in_org(db, admin.org_id, inv.email)
        event = crud_event.get_event(db, admin.org_id, inv.event_id) if inv.event_id else None
        crud.set_invitation_status(db, inv, "revoked", revoked_at=now)
        removed = False
        if member is not None and inv.event_id and inv.event_role:
            removed = crud_event.remove_assignee_pending(db, inv.event_id, inv.event_role, member.id)
        db.commit()
        db.refresh(inv)
        _audit(db, admin, request, "invitation.revoked", target_type="invitation",
               target_id=inv.id, org_id=admin.org_id,
               meta={"email": inv.email, "event_role": inv.event_role,
                     "assignment_removed": removed})
        if member is not None:
            org_name = admin.organization.name if admin.organization else "the organization"
            background.add_task(send_invitation_revoked_email, inv.email, org_name,
                                _role_display(inv), event.title if event else None)
        return _inv_out(inv)
    except crud.InvitationStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invitation(invitation_id: uuid.UUID, request: Request,
                      admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    inv = crud.get_invitation(db, admin.org_id, invitation_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    crud.soft_delete_invitation(db, inv)
    _audit(db, admin, request, "invitation.deleted", target_type="invitation",
           target_id=inv.id, org_id=admin.org_id, meta={"email": inv.email})


# ── Invitations: the invitee's public surface ──────────────────────────────────
# Three routes, no session required — the invitee holds a token, not an account. Everything
# they can reach is either read-only or consumes the token exactly once.

def _open_invitation_or_refuse(db: Session, token: str):
    """Resolve + fully re-validate a token. Returns (invitation, event | None).

    Re-validation matters because the token is a 7-DAY credential: between issue and use the
    organization can be suspended, the event deleted, and the inviter demoted or removed. All
    of those, plus an unknown/used/expired token, raise the identical refusal.
    """
    inv = crud.find_invitation_by_token(db, token)
    if inv is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _PUBLIC_REFUSAL)

    org = db.get(Organization, inv.org_id)
    # A suspended organization must not be able to GROW the tenant it froze.
    if org is None or org.status == "suspended":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _PUBLIC_REFUSAL)

    # Re-authorize the GRANT, not just the token: if the inviter was demoted or removed, the
    # authority behind this invitation is gone.
    inviter = inv.inviter
    if inviter is None or inviter.deleted_at is not None or not inviter.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _PUBLIC_REFUSAL)
    if _authorize_grant(inviter, inv.role, inv.event_role) is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _PUBLIC_REFUSAL)

    # Re-resolve the event org-scoped; never trust the stored id. Catches a soft-deleted
    # event, and would catch a crafted cross-tenant id.
    event = None
    if inv.event_id:
        event = crud_event.get_event(db, inv.org_id, inv.event_id)
        if event is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, _PUBLIC_REFUSAL)
    return inv, event


def _mask_email(email: str) -> str:
    """b***@example.com — enough for the invitee to confirm which address was invited without
    printing it in full on a shared screen."""
    local, _, domain = email.partition("@")
    return f"{local[0]}{'*' * max(len(local) - 1, 1)}@{domain}" if local else email


@router.get("/invitations/preview", response_model=InvitationPreview,
            dependencies=[_PREVIEW_LIMIT])
def preview_invitation(token: str = Query(..., min_length=16), db: Session = Depends(get_db)):
    """What the invitee sees before deciding.

    STRICTLY side-effect free — no writes of any kind, not even the lazy expiry sweep. Mail
    scanners, link previewers and corporate security proxies fetch every URL in an email, so a
    GET that mutated anything would have invitations consumed by robots before the human read
    them. (This is also why neither email button acts on being opened.)
    """
    inv, event = _open_invitation_or_refuse(db, token)
    org = db.get(Organization, inv.org_id)
    existing = crud.find_user_by_email(db, inv.email)
    return InvitationPreview(
        org_name=org.name,
        org_logo_url=org.logo_url,
        # Display name only. The admin-facing view carries the inviter's email; this one
        # must not — it is readable by anyone holding the token.
        inviter_name=(inv.inviter.full_name or "An administrator") if inv.inviter else None,
        role_label=_role_display(inv),
        event_title=event.title if event else None,
        event_starts_at=event.start_time if event else None,
        event_role_label=_ROLE_LABELS.get(inv.event_role or "", inv.event_role) if inv.event_role else None,
        message=inv.message,
        expires_at=inv.expires_at,
        email_hint=_mask_email(inv.email),
        # Tells the page whether to collect a password or send them to sign in. This does
        # reveal whether the invited address has an account — to the holder of a token issued
        # FOR that address, which is the address's owner. Accepted.
        needs_account=existing is None,
    )


@router.post("/invitations/accept", response_model=InvitationAcceptResult,
             dependencies=[_ACCEPT_LIMIT])
def accept_invitation(data: InvitationAccept, request: Request, background: BackgroundTasks,
                      db: Session = Depends(get_db)):
    """Consume an invitation.

    Three branches, and the difference between them is a security boundary:

      * brand-new address  -> create the account and RETURN A SESSION. The token plus the
        email binding is adequate proof for someone who has no credentials yet.
      * existing account   -> add the event assignment and return `login_required`. NO session
        and NO password change. Otherwise an admin who can read the raw token out of the
        create response could exchange it for a session on somebody else's account.
      * soft-deleted account (same org) -> reactivate the row, keep the existing password,
        return `login_required`. Same reasoning, plus users.email is globally unique so
        inserting over it would be a 500.

    Everything happens in ONE transaction whose gate is crud.claim(): a conditional UPDATE
    that only succeeds from an open state. Two simultaneous accepts cannot both win, and an
    accept racing a decline cannot leave a live member behind a `rejected` row.
    """
    inv, event = _open_invitation_or_refuse(db, data.token)
    now = datetime.now(timezone.utc)
    existing = crud.find_user_by_email(db, inv.email)

    if existing is not None and existing.org_id != inv.org_id:
        # One user, one organization in this schema. Same refusal as everything else.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _PUBLIC_REFUSAL)

    brand_new = existing is None
    if brand_new:
        if not data.full_name or not data.password:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "A name and password are required to create your account")

    # Claim FIRST: it takes the row lock, so from here on nobody else can consume this
    # invitation, and every write below lands in the same transaction as the claim.
    if not crud.claim(db, inv.id, "accepted", accepted_at=now):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _PUBLIC_REFUSAL)

    try:
        if brand_new:
            # `username` is a preference, not a probe: a collision silently gets a suffix
            # rather than a 409, which would have been a global username oracle callable
            # without consuming the invitation.
            username = crud.unique_username(db, (data.username or inv.email.split("@")[0]).lower())
            user = crud.build_member(db, inv, data.full_name, username, hash_password(data.password))
        elif existing.deleted_at is not None:
            # Re-hiring. The platform role from the invitation applies; the password does not
            # change (they sign in with the one they had, or reset it).
            user = crud.reactivate_user(db, existing, role=inv.role)
        else:
            # A live colleague accepting an event invitation. Their platform role is NOT
            # touched — users.role changes only through PATCH /organization/users/{id}, which
            # carries the self-lockout and super-admin guards this path would bypass.
            user = existing

        if inv.event_id and inv.event_role:
            # add_assignee is idempotent, so an admin who already added this person manually
            # while the invitation was open does not turn their accept into a 500.
            crud_event.add_assignee_pending(db, inv.event_id, inv.event_role, user.id)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(user)
    _audit(db, None, request, "invitation.accepted", target_type="invitation",
           target_id=inv.id, org_id=inv.org_id,
           meta={"email": inv.email, "new_account": brand_new,
                 "event_id": str(inv.event_id) if inv.event_id else None,
                 "event_role": inv.event_role})

    # Notify the inviter through the channel that exists. AFTER the response, like every other
    # send in this app: _send allows a 10s timeout, and the invitee should not sit watching a
    # spinner because the inviter's notification is slow to hand off.
    if inv.inviter is not None:
        org = db.get(Organization, inv.org_id)
        background.add_task(
            send_invitation_accepted_email,
            inv.inviter.email, user.full_name or user.email,
            org.name if org else "your organization", _role_display(inv),
            event.title if event else None,
        )

    if brand_new:
        out = UserOut.model_validate(user)
        out.organization_name = user.organization.name if user.organization else None
        return InvitationAcceptResult(
            access_token=create_access_token(user, remember=False),
            user=out.model_dump(mode="json"),
            event_id=inv.event_id,
            message="Welcome to ZoikoStream.",
        )
    return InvitationAcceptResult(
        login_required=True, event_id=inv.event_id,
        message="Invitation accepted. Sign in to continue.",
    )


@router.post("/invitations/reject", dependencies=[_ACCEPT_LIMIT])
def reject_invitation(data: InvitationReject, request: Request, background: BackgroundTasks,
                      db: Session = Depends(get_db)):
    """Decline. Same atomic claim as accept, so a decline racing an accept has exactly one
    winner. Expiry is enforced by the resolver, which the old handler never checked at all."""
    inv, _ = _open_invitation_or_refuse(db, data.token)
    now = datetime.now(timezone.utc)
    if not crud.claim(db, inv.id, "rejected", declined_at=now):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _PUBLIC_REFUSAL)
    db.commit()
    _audit(db, None, request, "invitation.declined", target_type="invitation",
           target_id=inv.id, org_id=inv.org_id, meta={"email": inv.email})
    if inv.inviter is not None:
        org = db.get(Organization, inv.org_id)
        event = crud_event.get_event(db, inv.org_id, inv.event_id) if inv.event_id else None
        background.add_task(send_invitation_declined_email, inv.inviter.email, inv.email,
                            org.name if org else "your organization",
                            event.title if event else None)
    return {"message": "Invitation declined."}
