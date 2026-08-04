import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import organization as invite_crud
from ..db import get_db
from ..email import send_reset_otp_email, send_welcome_email
from ..models import Organization, User
from ..ratelimit import rate_limit
from ..schemas import (
    ForgotPasswordIn,
    LoginIn,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
    VerifyOtpIn,
)
from ..security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

OTP_TTL_MINUTES = 10

# Per-IP budgets on the unauthenticated surface. Generous enough that a person fumbling
# their password never notices, tight enough that a 4-digit OTP (10k combinations) can no
# longer be walked in one sitting. See ratelimit.py for the per-worker caveat.
_LOGIN_LIMIT = rate_limit("login", limit=10, window=60.0)
_OTP_REQUEST_LIMIT = rate_limit("otp-request", limit=5, window=300.0)
_OTP_VERIFY_LIMIT = rate_limit("otp-verify", limit=10, window=300.0)
_REGISTER_LIMIT = rate_limit("register", limit=5, window=300.0)


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED,
             dependencies=[_REGISTER_LIMIT])
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

    # An invited person who uses the SIGNUP FORM instead of clicking their link must not end up
    # with a NEW organization: their email would then belong to another org, their invitation
    # could never be accepted, and re-inviting them would 409 forever.
    #
    # But CONSUMING the invitation here is far worse, and this endpoint did exactly that until
    # it was caught: a second accept path keyed on the EMAIL ADDRESS ALONE. RegisterIn carries
    # no token and this route proves no ownership of the address, so anyone who knew an invited
    # corporate address could take that seat with a password of their choosing — bypassing the
    # 256-bit token entirely. It also honoured a grant from an inviter who had since been
    # demoted or deleted, assigned onto a soft-deleted event, and wrote no audit row, because
    # none of _open_invitation_or_refuse's five re-validations ran here.
    #
    # So: REFUSE, and write nothing. The invitation stays pending and redeemable through its
    # token, which POST /organization/invitations/accept already exchanges for an account and a
    # session — with every one of those checks applied.
    invitation = invite_crud.find_open_invitation_for_email(db, email)
    if invitation is not None:
        org = db.get(Organization, invitation.org_id)
        # A suspended org is the one case where we fall through: it cannot grow the tenant it
        # froze, so let this person create their own organization instead of stranding them.
        if org is not None and org.status != "suspended":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You already have a pending invitation. Please use the invitation link we "
                "emailed you to join — it sets up your account.",
            )

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
    db.commit()
    db.refresh(user)

    # Fire the welcome email after the response is sent — mail latency/outage never
    # delays or breaks signup (send_welcome_email is best-effort and logs its own errors).
    background.add_task(send_welcome_email, user.email, user.full_name)

    # Include organization name in response
    user_out = UserOut.model_validate(user)
    user_out.organization_name = user.organization.name if user.organization else None

    return TokenOut(access_token=create_access_token(user, remember=False), user=user_out)


@router.post("/login", response_model=TokenOut, dependencies=[_LOGIN_LIMIT])
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


@router.post("/forgot-password", dependencies=[_OTP_REQUEST_LIMIT])
def forgot_password(data: ForgotPasswordIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    # Always return 200 so the endpoint can't be used to probe which emails exist.
    resp = {"message": "If that email exists, a 4-digit code has been sent."}
    if user is None:
        return resp

    otp = f"{secrets.randbelow(10000):04d}"  # zero-padded 4-digit code
    user.reset_token = otp
    user.reset_token_expires = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)
    db.commit()
    # ponytail: 4-digit OTP = 10k combos; the short TTL is the only brute-force guard.
    # Add an attempt counter (lock after ~5 tries) before this is a production reset path.
    background.add_task(send_reset_otp_email, user.email, user.full_name, otp)
    return resp


def _valid_otp_user(db: Session, email: str, otp: str) -> User:
    """Look up the user by email and check the OTP is correct and unexpired.
    Same generic error for wrong-email / wrong-otp / expired so nothing leaks."""
    user = db.scalar(select(User).where(User.email == email.lower()))
    expires = user.reset_token_expires if user else None
    if user is None or user.reset_token != otp or expires is None or expires < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")
    return user


@router.post("/verify-otp", dependencies=[_OTP_VERIFY_LIMIT])
def verify_otp(data: VerifyOtpIn, db: Session = Depends(get_db)):
    _valid_otp_user(db, data.email, data.otp)  # raises 400 if bad — code stays valid for the reset step
    return {"message": "Code verified."}


@router.post("/reset-password", dependencies=[_OTP_VERIFY_LIMIT])
def reset_password(data: ResetPasswordIn, db: Session = Depends(get_db)):
    user = _valid_otp_user(db, data.email, data.otp)
    user.password_hash = hash_password(data.password)
    user.reset_token = None  # single-use: consume the OTP
    user.reset_token_expires = None
    db.commit()
    return {"message": "Password updated. You can now log in."}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    user_out = UserOut.model_validate(user)
    user_out.organization_name = user.organization.name if user.organization else None
    return user_out
