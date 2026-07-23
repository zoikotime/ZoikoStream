import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.email import send_member_invite_email
from app.models import User
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


def _get_org_member(db: Session, user: User, member_id: str) -> User:
    member = db.scalar(select(User).where(User.id == member_id, User.org_id == user.org_id))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return member


@router.get("", response_model=list[MemberOut])
def list_members(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_org_admin(user)
    return db.scalars(
        select(User).where(User.org_id == user.org_id, User.id != user.id).order_by(User.created_at.desc())
    ).all()


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def invite_member(
    data: MemberInviteIn,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_org_admin(user)
    email = data.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already taken")

    # Direct-create: no invite-token/accept flow yet — the account is created immediately
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
    db.commit()
    db.refresh(member)

    org_name = user.organization.name if user.organization else "your organization"
    background.add_task(send_member_invite_email, member.email, member.full_name, member.role, org_name, temp_password)
    return member


@router.post("/{member_id}/resend", response_model=MemberOut)
def resend_credentials(
    member_id: str,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_org_admin(user)
    member = _get_org_member(db, user, member_id)

    temp_password = secrets.token_urlsafe(9)
    member.password_hash = hash_password(temp_password)
    member.is_active = True
    db.commit()
    db.refresh(member)

    org_name = user.organization.name if user.organization else "your organization"
    background.add_task(send_member_invite_email, member.email, member.full_name, member.role, org_name, temp_password)
    return member


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    member_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_org_admin(user)
    member = _get_org_member(db, user, member_id)
    member.is_active = False
    db.commit()
