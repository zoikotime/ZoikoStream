import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .email import send_reset_otp_email, send_welcome_email
from .models import Membership, Organization, User
from .schemas import (
    ForgotPasswordIn,
    LoginIn,
    MembershipOut,
    RegisterIn,
    ResetPasswordIn,
    SwitchOrgIn,
    TokenOut,
    UserOut,
    VerifyOtpIn,
)
from .security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    email = data.email.lower()

    # Auto-generate username from email if not provided (use part before @)
    if data.username:
        username = data.username.lower()
    else:
        # Use email prefix as username; make it unique by appending a number if needed
        base_username = email.split("@")[0].lower()
        username = base_username
        counter = 1
        while db.scalar(select(User).where(func.lower(User.username) == username)):
            username = f"{base_username}{counter}"
            counter += 1

    # Check if email already exists
    exists = db.scalar(select(User).where(User.email == email))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already taken")

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
        username=username,
        password_hash=hash_password(data.password),
        role=role,
    )
    db.add(user)
    db.flush()  # assign user.id before the membership row references it
    db.add(Membership(user_id=user.id, org_id=org.id, role=role, is_active=True))
    db.commit()
    db.refresh(user)

    # Fire the welcome email after the response is sent — mail latency/outage never
    # delays or breaks signup (send_welcome_email is best-effort and logs its own errors).
    background.add_task(send_welcome_email, user.email, user.full_name)

    # Include organization name in response
    user_out = UserOut.model_validate(user)
    user_out.organization_name = user.organization.name if user.organization else None

    return TokenOut(access_token=create_access_token(user, remember=False), user=user_out)


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

    # Include organization name in response
    user_out = UserOut.model_validate(user)
    user_out.organization_name = user.organization.name if user.organization else None

    return TokenOut(
        access_token=create_access_token(user, remember=data.remember),
        user=user_out,
    )


@router.post("/forgot-password")
def forgot_password(data: ForgotPasswordIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    # Always return 200 so the endpoint can't be used to probe which emails exist.
    resp = {"message": "If that email exists, a 4-digit code has been sent."}
    if user is None:
        return resp

    otp = f"{secrets.randbelow(10000):04d}"  # zero-padded 4-digit code
    user.reset_token = otp
    user.reset_token_expires = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)
    user.reset_attempts = 0  # fresh code, fresh attempt budget
    db.commit()
    background.add_task(send_reset_otp_email, user.email, user.full_name, otp)
    return resp


def _valid_otp_user(db: Session, email: str, otp: str) -> User:
    """Look up the user by email and check the OTP is correct, unexpired, and not
    locked out from too many wrong guesses. Same generic error for wrong-email /
    wrong-otp / expired / locked-out so nothing leaks about which case applies.

    A wrong guess counts against the attempt budget even so; once OTP_MAX_ATTEMPTS is
    hit the code is dead regardless of whether the *next* guess would've been correct --
    request a new one via /forgot-password.
    """
    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")
    user = db.scalar(select(User).where(User.email == email.lower()))
    expires = user.reset_token_expires if user else None
    if user is None or expires is None or expires < datetime.now(timezone.utc):
        raise invalid

    if user.reset_attempts >= OTP_MAX_ATTEMPTS:
        raise invalid

    if user.reset_token != otp:
        user.reset_attempts += 1
        db.commit()
        raise invalid

    return user


@router.post("/verify-otp")
def verify_otp(data: VerifyOtpIn, db: Session = Depends(get_db)):
    _valid_otp_user(db, data.email, data.otp)  # raises 400 if bad — code stays valid for the reset step
    return {"message": "Code verified."}


@router.post("/reset-password")
def reset_password(data: ResetPasswordIn, db: Session = Depends(get_db)):
    user = _valid_otp_user(db, data.email, data.otp)
    user.password_hash = hash_password(data.password)
    user.reset_token = None  # single-use: consume the OTP
    user.reset_token_expires = None
    user.reset_attempts = 0
    db.commit()
    return {"message": "Password updated. You can now log in."}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    user_out = UserOut.model_validate(user)
    user_out.organization_name = user.organization.name if user.organization else None
    return user_out


@router.get("/memberships", response_model=list[MembershipOut])
def memberships(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at)
    ).all()
    return [
        MembershipOut(
            org_id=m.org_id,
            organization_name=m.organization.name if m.organization else "Organization",
            role=m.role,
            is_active=m.is_active,
        )
        for m in rows
    ]


@router.post("/switch-org", response_model=UserOut)
def switch_org(data: SwitchOrgIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.org_id == data.org_id,
            Membership.is_active.is_(True),
        )
    )
    if not membership:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You aren't a member of that organization")

    user.org_id = membership.org_id
    user.role = membership.role
    db.commit()
    db.refresh(user)

    user_out = UserOut.model_validate(user)
    user_out.organization_name = user.organization.name if user.organization else None
    return user_out
