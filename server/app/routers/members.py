import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.email import send_added_to_org_email, send_member_invite_email
from app.models import Membership, User
from app.schemas import MemberInviteIn, MemberOut
from app.security import get_current_user, hash_password

router = APIRouter(prefix="/organization/members", tags=["Members"])


def _require_org_admin(user: User) -> None:
    if user.role != "org_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only organization admins can manage members")


def _unique_username(db: Session, email: str) -> str:
    base = email.split("@")[0].lower()
    username = base
    counter = 1
    while db.scalar(select(User).where(func.lower(User.username) == username)):
        username = f"{base}{counter}"
        counter += 1
    return username


def _member_out(user: User, membership: Membership) -> MemberOut:
    # role/is_active come from the Membership (this org), not the User's own globally
    # -active org/role -- those can differ once an account belongs to more than one org.
    return MemberOut(id=user.id, full_name=user.full_name, email=user.email, role=membership.role, is_active=membership.is_active)


def _get_org_membership(db: Session, user: User, member_id: str) -> tuple[User, Membership]:
    row = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(User.id == member_id, Membership.org_id == user.org_id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return row[0], row[1]


@router.get("", response_model=list[MemberOut])
def list_members(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    rows = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == user.org_id, Membership.is_active.is_(True), User.id != user.id)
        .order_by(User.created_at.desc())
    ).all()
    return [_member_out(u, m) for u, m in rows]


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def invite_member(
    data: MemberInviteIn,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_org_admin(user)
    email = data.email.lower()
    org_name = user.organization.name if user.organization else "your organization"

    existing = db.scalar(select(User).where(User.email == email))

    if existing:
        # Already a member here (active or previously removed) -- reactivate rather than
        # erroring twice for a simple re-add, but a currently-active membership is a
        # real conflict.
        membership = db.scalar(select(Membership).where(Membership.user_id == existing.id, Membership.org_id == user.org_id))
        if membership and membership.is_active:
            raise HTTPException(status.HTTP_409_CONFLICT, "This person is already a member of your organization")

        if membership:
            membership.is_active = True
            membership.role = data.role
        else:
            membership = Membership(user_id=existing.id, org_id=user.org_id, role=data.role, is_active=True)
            db.add(membership)

        db.commit()
        db.refresh(membership)

        # They already have a login elsewhere -- no new password, just point them at
        # switching into this org.
        background.add_task(send_added_to_org_email, existing.email, existing.full_name, data.role, org_name)
        return _member_out(existing, membership)

    # Direct-create: no invite-token/accept flow yet -- the account is created immediately
    # and credentials are emailed to the new member.
    temp_password = secrets.token_urlsafe(9)
    member = User(
        org_id=user.org_id,
        full_name=data.full_name,
        email=email,
        username=_unique_username(db, email),
        password_hash=hash_password(temp_password),
        role=data.role,
    )
    db.add(member)
    db.flush()  # assign member.id before the membership row references it
    membership = Membership(user_id=member.id, org_id=user.org_id, role=data.role, is_active=True)
    db.add(membership)
    db.commit()
    db.refresh(member)
    db.refresh(membership)

    background.add_task(send_member_invite_email, member.email, member.full_name, member.role, org_name, temp_password)
    return _member_out(member, membership)


@router.post("/{member_id}/resend", response_model=MemberOut)
def resend_credentials(
    member_id: str,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_org_admin(user)
    member, membership = _get_org_membership(db, user, member_id)

    temp_password = secrets.token_urlsafe(9)
    member.password_hash = hash_password(temp_password)
    membership.is_active = True
    db.commit()
    db.refresh(member)
    db.refresh(membership)

    org_name = user.organization.name if user.organization else "your organization"
    background.add_task(send_member_invite_email, member.email, member.full_name, membership.role, org_name, temp_password)
    return _member_out(member, membership)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    member_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_org_admin(user)
    _member, membership = _get_org_membership(db, user, member_id)
    # Deactivates their membership in *this* org only -- other orgs they belong to (and
    # their global account/login) are untouched.
    membership.is_active = False
    db.commit()
