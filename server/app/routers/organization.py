import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.email import send_invitation_email
from app.models import Invitation, Membership, Organization, User
from app.schemas import (
    InvitationActionIn,
    InvitationCreateIn,
    InvitationOut,
    OrgUserOut,
    OrgUserUpdateIn,
)
from app.schemas.org_settings import (
    NOTIFICATIONS_DEFAULTS,
    SECURITY_DEFAULTS,
    OrgBrandingOut,
    OrgBrandingUpdateIn,
    OrgDomainOut,
    OrgDomainUpdateIn,
    OrgNotificationsOut,
    OrgNotificationsUpdateIn,
    OrgProfileOut,
    OrgProfileUpdateIn,
    OrgSecurityOut,
    OrgSecurityUpdateIn,
)
from app.security import get_current_user, hash_password

router = APIRouter(prefix="/organization", tags=["Organization Members"])

INVITE_TTL_DAYS = 7


def _require_org_admin(user: User) -> None:
    if user.role != "org_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only organization admins can manage members")


def _base_url() -> str:
    from app.config import settings
    return (settings.CORS_ORIGINS.split(",")[0].strip() or "https://zoikostream.com").rstrip("/")


# ── Users ─────────────────────────────────────────────────────────────────────────
@router.get("/users")
def list_org_users(page_size: int = 50, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    rows = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == user.org_id, Membership.is_active.is_(True))
        .order_by(Membership.created_at.desc())
        .limit(page_size)
    ).all()
    items = [
        OrgUserOut(id=u.id, full_name=u.full_name, email=u.email, role=m.role, is_active=m.is_active, created_at=m.created_at)
        for u, m in rows
    ]
    return {"items": items}


@router.patch("/users/{member_id}", response_model=OrgUserOut)
def update_org_user(member_id: str, data: OrgUserUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    if member_id == str(user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't change your own role")

    row = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(User.id == member_id, Membership.org_id == user.org_id, Membership.is_active.is_(True))
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    member, membership = row
    if member.role == "super_admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Can't change a super admin's role")

    membership.role = data.role
    if member.org_id == user.org_id:  # this org is their currently-active one -- keep the enforced global role in sync
        member.role = data.role
    db.commit()
    db.refresh(membership)
    return OrgUserOut(id=member.id, full_name=member.full_name, email=member.email, role=membership.role, is_active=membership.is_active, created_at=membership.created_at)


@router.delete("/users/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_org_user(member_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    if member_id == str(user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't remove yourself")

    membership = db.scalar(
        select(Membership).where(Membership.user_id == member_id, Membership.org_id == user.org_id, Membership.is_active.is_(True))
    )
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    # Deactivates their membership in *this* org only -- other orgs they belong to (and
    # their global account/login) are untouched.
    membership.is_active = False
    db.commit()


# ── Invitations ───────────────────────────────────────────────────────────────────
def _invitation_out(inv: Invitation) -> InvitationOut:
    return InvitationOut(
        id=inv.id, email=inv.email, role=inv.role, status=inv.status,
        invited_by=inv.invited_by.full_name if inv.invited_by else None,
        expires_at=inv.expires_at, created_at=inv.created_at,
    )


def _send_invite(inv: Invitation, org_name: str, background: BackgroundTasks, raw_token: str) -> None:
    link = f"{_base_url()}/accept-invitation?token={raw_token}&email={inv.email}"
    background.add_task(send_invitation_email, inv.email, inv.role, org_name, link)


@router.get("/invitations")
def list_invitations(page_size: int = 50, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    rows = db.scalars(
        select(Invitation).where(Invitation.org_id == user.org_id).order_by(Invitation.created_at.desc()).limit(page_size)
    ).all()
    return {"items": [_invitation_out(i) for i in rows]}


@router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
def create_invitation(data: InvitationCreateIn, background: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    email = data.email.lower()
    org_name = user.organization.name if user.organization else "your organization"

    already_member = db.execute(
        select(Membership).join(User, User.id == Membership.user_id).where(User.email == email, Membership.org_id == user.org_id, Membership.is_active.is_(True))
    ).first()
    if already_member:
        raise HTTPException(status.HTTP_409_CONFLICT, "This person is already a member of your organization")

    raw_token = secrets.token_urlsafe(32)
    existing_pending = db.scalar(select(Invitation).where(Invitation.email == email, Invitation.org_id == user.org_id, Invitation.status == "pending"))
    if existing_pending:
        existing_pending.role = data.role
        existing_pending.token_hash = hash_password(raw_token)
        existing_pending.expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
        existing_pending.invited_by_id = user.id
        invitation = existing_pending
    else:
        invitation = Invitation(
            org_id=user.org_id, email=email, role=data.role,
            token_hash=hash_password(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS),
            invited_by_id=user.id,
        )
        db.add(invitation)

    db.commit()
    db.refresh(invitation)
    _send_invite(invitation, org_name, background, raw_token)
    return _invitation_out(invitation)


@router.patch("/invitations/{invitation_id}", response_model=InvitationOut)
def act_on_invitation(invitation_id: str, data: InvitationActionIn, background: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    invitation = db.scalar(select(Invitation).where(Invitation.id == invitation_id, Invitation.org_id == user.org_id))
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if invitation.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a pending invitation can be resent")

    raw_token = secrets.token_urlsafe(32)
    invitation.token_hash = hash_password(raw_token)
    invitation.expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    db.commit()
    db.refresh(invitation)

    org_name = user.organization.name if user.organization else "your organization"
    _send_invite(invitation, org_name, background, raw_token)
    return _invitation_out(invitation)


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_invitation(invitation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    invitation = db.scalar(select(Invitation).where(Invitation.id == invitation_id, Invitation.org_id == user.org_id))
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if invitation.status == "accepted":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation was already accepted")
    invitation.status = "cancelled"
    db.commit()


# ── Org Settings: profile / security / notifications / domain / branding ──────────
# Only these five tabs have a backend -- permissions, API keys, integrations,
# domain-verify, and the danger zone are deliberately local-only (see Settings.jsx's
# own "API ⇄ form-state mapping" comment); nothing here should grow endpoints for those
# until that frontend actually calls them.
def _get_org(user: User, db: Session) -> Organization:
    org = db.get(Organization, user.org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


@router.get("/profile", response_model=OrgProfileOut)
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    return _get_org(user, db)


@router.patch("/profile", response_model=OrgProfileOut)
def update_profile(data: OrgProfileUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    org = _get_org(user, db)
    for field, value in data.model_dump().items():
        setattr(org, field, value)
    db.commit()
    db.refresh(org)
    return org


@router.get("/security", response_model=OrgSecurityOut)
def get_security(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    org = _get_org(user, db)
    return {**SECURITY_DEFAULTS, **(org.security or {})}


@router.patch("/security", response_model=OrgSecurityOut)
def update_security(data: OrgSecurityUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    org = _get_org(user, db)
    org.security = data.model_dump()
    db.commit()
    return org.security


@router.get("/notifications", response_model=OrgNotificationsOut)
def get_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    org = _get_org(user, db)
    return {**NOTIFICATIONS_DEFAULTS, **(org.notifications or {})}


@router.patch("/notifications", response_model=OrgNotificationsOut)
def update_notifications(data: OrgNotificationsUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    org = _get_org(user, db)
    org.notifications = data.model_dump()
    db.commit()
    return org.notifications


@router.get("/domain", response_model=OrgDomainOut)
def get_domain(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    return _get_org(user, db)


@router.patch("/domain", response_model=OrgDomainOut)
def update_domain(data: OrgDomainUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    org = _get_org(user, db)
    if data.domain != org.domain:
        # No DNS-verify endpoint yet (see Settings.jsx) -- any domain change drops back
        # to unverified rather than fabricating a "still verified" state.
        org.domain_verified = False
    org.domain = data.domain
    db.commit()
    db.refresh(org)
    return org


@router.get("/branding", response_model=OrgBrandingOut)
def get_branding(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    return _get_org(user, db)


@router.patch("/branding", response_model=OrgBrandingOut)
def update_branding(data: OrgBrandingUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    org = _get_org(user, db)
    org.primary_color = data.primary_color
    db.commit()
    db.refresh(org)
    return org
