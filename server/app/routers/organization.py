"""Org self-service API — the backend for the Organization Profile & Settings pages.

Isolation: the target org is ALWAYS the caller's `user.org_id` (from the JWT); org_id is
never read from the request body. Super admin isn't blocked — it operates on its own org
here and uses /admin/* for cross-org management. Reads: any member. Writes + sensitive
reads (security, developer): org admin (require_org_admin already admits super_admin)."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import organization as crud
from ..db import get_db
from ..email import send_invitation_email
from ..models import Organization, User
from ..schemas.admin import AdminUserOut, Page, UserUpdate
from ..schemas.auth import TokenOut, UserOut
from ..schemas.organization import (
    InvitationAccept,
    InvitationAction,
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

# Roles an org admin may assign/invite. Excludes super_admin (platform-only, never via this API).
ORG_ASSIGNABLE_ROLES = ("org_admin", "host", "moderator", "speaker", "viewer")

router = APIRouter(prefix="/organization", tags=["organization"])


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


# ── Invitations (admin management) ───────────────────────────────────────────────

def _invite_url(token: str) -> str:
    base = (settings.CORS_ORIGINS.split(",")[0].strip() or "https://zoikostream.com").rstrip("/")
    return f"{base}/accept-invite?token={token}"


def _inv_out(inv, token: str | None = None, url: str | None = None) -> InvitationOut:
    return InvitationOut(
        id=inv.id, email=inv.email, role=inv.role, status=inv.status,
        invited_by=inv.inviter.email if inv.inviter else None,
        expires_at=inv.expires_at, accepted_at=inv.accepted_at, created_at=inv.created_at,
        invite_token=token, invite_url=url,
    )


@router.get("/invitations", response_model=Page)
def list_invitations(
    status_: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    items, total = crud.list_invitations(db, admin.org_id, status=status_, page=page, page_size=page_size)
    return Page(items=[_inv_out(i) for i in items], total=total, page=page, page_size=page_size)


@router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
def create_invitation(data: InvitationCreate, background: BackgroundTasks,
                      admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    email = data.email.lower()
    if data.role not in ORG_ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid role")
    if crud.user_email_taken(db, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with that email already exists")
    if crud.pending_invite_exists(db, admin.org_id, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "A pending invitation for that email already exists")
    inv, raw = crud.create_invitation(db, admin.org_id, email, data.role, admin.id)
    url = _invite_url(raw)
    org_name = admin.organization.name if admin.organization else "your organization"
    background.add_task(send_invitation_email, email, org_name, admin.full_name, url)
    return _inv_out(inv, token=raw, url=url)  # raw token returned once, for delivery


@router.patch("/invitations/{invitation_id}", response_model=InvitationOut)
def update_invitation(invitation_id: uuid.UUID, data: InvitationAction, background: BackgroundTasks,
                      admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    inv = crud.get_invitation(db, admin.org_id, invitation_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if inv.status == "accepted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Invitation already accepted")
    if data.action == "resend":
        inv, raw = crud.resend_invitation(db, inv)
        url = _invite_url(raw)
        org_name = admin.organization.name if admin.organization else "your organization"
        background.add_task(send_invitation_email, inv.email, org_name, admin.full_name, url)
        return _inv_out(inv, token=raw, url=url)
    crud.set_invitation_status(db, inv, "cancelled" if data.action == "cancel" else "expired")
    return _inv_out(inv)


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invitation(invitation_id: uuid.UUID, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    inv = crud.get_invitation(db, admin.org_id, invitation_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    crud.delete_invitation(db, inv)


# ── Invitation accept / reject (public — the invitee holds a token, not a session) ──

@router.get("/invitations/preview", response_model=InvitationPreview)
def preview_invitation(token: str, db: Session = Depends(get_db)):
    inv = crud.find_invitation_by_token(db, token)
    if inv is None or inv.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or already-used invitation")
    if inv.expires_at < datetime.now(timezone.utc):
        crud.set_invitation_status(db, inv, "expired")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitation has expired")
    return InvitationPreview(
        email=inv.email, role=inv.role,
        organization_name=inv.organization.name if inv.organization else "your organization",
    )


@router.post("/invitations/accept", response_model=TokenOut)
def accept_invitation(data: InvitationAccept, db: Session = Depends(get_db)):
    inv = crud.find_invitation_by_token(db, data.token)
    if inv is None or inv.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or already-used invitation")
    if inv.expires_at < datetime.now(timezone.utc):
        crud.set_invitation_status(db, inv, "expired")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitation has expired")
    if crud.user_email_taken(db, inv.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered")
    if data.username and crud.username_taken(db, data.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
    username = (data.username or crud.unique_username(db, inv.email.split("@")[0])).lower()
    user = crud.accept_invitation(db, inv, data.full_name, username, hash_password(data.password))
    out = UserOut.model_validate(user)
    out.organization_name = user.organization.name if user.organization else None
    return TokenOut(access_token=create_access_token(user, remember=False), user=out)


@router.post("/invitations/reject")
def reject_invitation(data: InvitationReject, db: Session = Depends(get_db)):
    inv = crud.find_invitation_by_token(db, data.token)
    if inv is None or inv.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or already-used invitation")
    crud.set_invitation_status(db, inv, "rejected")
    return {"message": "Invitation declined."}
