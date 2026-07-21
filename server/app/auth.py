import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from app.models import User, Organization
from .schemas import (
    ForgotPasswordIn,
    LoginIn,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
)
from .security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    email = data.email.lower()
    username = data.username.lower()
    exists = db.scalar(
        select(User).where(or_(User.email == email, func.lower(User.username) == username))
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email or username already taken")

    org = Organization(name=data.organization_name)
    db.add(org)
    db.flush()  # assign org.id before creating the user

    # The designated super-admin email registers as super_admin; everyone else is
    # the org_admin of the organization they just created.
    role = "super_admin" if email == settings.SUPER_ADMIN_EMAIL.lower() else "org_admin"
    user = User(
        org_id=org.id,
        full_name=data.full_name,
        email=email,
        username=data.username,
        password_hash=hash_password(data.password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=create_access_token(user, remember=False), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    ident = data.identifier.strip().lower()
    user = db.scalar(
        select(User).where(or_(User.email == ident, func.lower(User.username) == ident))
    )
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    return TokenOut(
        access_token=create_access_token(user, remember=data.remember),
        user=UserOut.model_validate(user),
    )


@router.post("/forgot-password")
def forgot_password(data: ForgotPasswordIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    # Always return 200 so the endpoint can't be used to probe which emails exist.
    resp = {"message": "If that email exists, a reset link has been sent."}
    if user is None:
        return resp

    user.reset_token = secrets.token_urlsafe(32)
    user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()
    # ponytail: no email service yet — return the token so dev can complete the flow.
    # Wire this to real email (Resend/SES) before launch and drop reset_token from the response.
    resp["dev_reset_token"] = user.reset_token
    return resp


@router.post("/reset-password")
def reset_password(data: ResetPasswordIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.reset_token == data.token))
    expires = user.reset_token_expires if user else None
    if user is None or expires is None or expires < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")

    user.password_hash = hash_password(data.password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()
    return {"message": "Password updated. You can now log in."}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)
