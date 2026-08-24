import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import identity as identity_crud
from ..db import get_db
from ..email import (
    UnsafeLinkError,
    send_email_verification_email,
    send_reset_otp_email,
    verification_url,
)
from ..models import Organization, User
from ..ratelimit import rate_limit
from ..schemas import (
    ForgotPasswordIn,
    LoginIn,
    RegisterIn,
    RegistrationPendingOut,
    ResendVerificationIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
    VerificationResultOut,
    VerifyEmailIn,
    VerifyOtpIn,
)
from ..security import (
    client_ip,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

OTP_TTL_MINUTES = 10

# Machine-readable codes the client branches on. Kept as constants because the frontend
# matches them exactly — see client/src/pages/auth/Login.jsx.
EMAIL_VERIFICATION_REQUIRED = "EMAIL_VERIFICATION_REQUIRED"
TOKEN_INVALID = "TOKEN_INVALID"
TOKEN_EXPIRED = "TOKEN_EXPIRED"
TOKEN_ALREADY_USED = "TOKEN_ALREADY_USED"

# Per-IP budgets on the unauthenticated surface. Generous enough that a person fumbling
# their password never notices, tight enough that a 4-digit OTP (10k combinations) can no
# longer be walked in one sitting. See ratelimit.py for the per-worker caveat.
_LOGIN_LIMIT = rate_limit("login", limit=10, window=60.0)
_OTP_REQUEST_LIMIT = rate_limit("otp-request", limit=5, window=300.0)
_OTP_VERIFY_LIMIT = rate_limit("otp-verify", limit=10, window=300.0)
_REGISTER_LIMIT = rate_limit("register", limit=5, window=300.0)
# Redemption is guessing-resistant on its own (256-bit token), so this budget is about
# keeping the hash+DB path cheap rather than protecting the secret.
_VERIFY_EMAIL_LIMIT = rate_limit("verify-email", limit=20, window=300.0)
# Resend is the expensive one: it sends mail to an address the caller merely names, so it
# is both a spam vector against third parties and a cost vector. Tightest budget here.
_RESEND_VERIFICATION_LIMIT = rate_limit("resend-verification", limit=3, window=900.0)


def _mask_email(email: str) -> str:
    """`jane.doe@example.com` -> `j******e@example.com`. Enough for the sender to
    recognize their own address, not enough to disclose one they don't already know."""
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    if len(local) <= 2:
        masked = local[0] + "*" * max(len(local) - 1, 1)
    else:
        masked = f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}"
    return f"{masked}@{domain}"


def _issue_verification(db: Session, user: User, ip: str | None, background: BackgroundTasks) -> int:
    """Mint an IDN-001 challenge, supersede any earlier one, and queue the email.

    The URL is built synchronously so a misconfigured APP_URL fails the request instead of
    failing invisibly inside a background task and leaving the account unverifiable.
    Returns the TTL in minutes for the response body.
    """
    challenge, raw = identity_crud.issue_email_verification(db, user, requested_ip=ip)
    try:
        url = verification_url(raw)
    except UnsafeLinkError:
        # Operator misconfiguration, not caller error. Do not echo the bad URL.
        log.exception("Refusing to send IDN-001: APP_URL is unsafe for this environment")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Email verification is not configured correctly. Contact support.",
        )
    expires_at = challenge.expires_at.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    ttl = identity_crud.ttl_minutes()
    background.add_task(send_email_verification_email, user.email, url, ttl, expires_at)
    return ttl


@router.post("/register", response_model=RegistrationPendingOut,
             status_code=status.HTTP_202_ACCEPTED, dependencies=[_REGISTER_LIMIT])
def register(data: RegisterIn, background: BackgroundTasks, request: Request,
             db: Session = Depends(get_db)):
    """Create the account UNVERIFIED and send IDN-001. Returns no session.

    Registration is not activation (ZST-EC-001 IDN-001/IDN-002): proof of control over the
    address is a separate, explicit step. 202 Accepted rather than 201 — the account row
    exists, but the thing the caller wanted (usable access) is still pending.
    """
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
        # Explicit rather than relying on the column default: this is the security-relevant
        # fact of the whole endpoint, so it reads at the call site.
        email_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # IDN-002 ("Account ready") deliberately does NOT fire here. Its trigger is "identity
    # verified and access activated", and sending it now would assert an active account
    # before any verification exists — the exact defect the audit recorded. It belongs on
    # the successful-verification path, and implementing it is out of scope for IDN-001.
    ttl = _issue_verification(db, user, client_ip(request), background)

    return RegistrationPendingOut(
        status=EMAIL_VERIFICATION_REQUIRED,
        email=_mask_email(user.email),
        expires_in_minutes=ttl,
        message="Check your email to verify your address before signing in.",
    )


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
    if not user.email_verified:
        # Credentials were correct, so no session is issued and no token is minted. The
        # code is structured (not prose) because the client renders a specific state with a
        # resend action. Only reachable after a correct password, so it discloses nothing
        # an attacker did not already have.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {
                "code": EMAIL_VERIFICATION_REQUIRED,
                "message": "Verify your email to continue.",
                "email": _mask_email(user.email),
            },
        )


    # Include organization name in response
    user_out = UserOut.model_validate(user)
    user_out.organization_name = user.organization.name if user.organization else None
    
    return TokenOut(
        access_token=create_access_token(user, remember=data.remember),
        user=user_out,
    )


# ── IDN-001 email verification ──────────────────────────────────────────────────────────

@router.post("/verify-email", response_model=VerificationResultOut,
             dependencies=[_VERIFY_EMAIL_LIMIT])
def verify_email(data: VerifyEmailIn, db: Session = Depends(get_db)):
    """Redeem an IDN-001 challenge.

    Deliberately does NOT return a session. The emailed link is a proof-of-control
    artifact, not an authentication credential — minting a token here would turn a link
    sitting in an inbox (and in mail-scanner logs, and in browser history) into a bearer
    credential for the account. The caller signs in normally afterwards.
    """
    outcome, _challenge = identity_crud.resolve_email_verification(db, data.token)

    if outcome == identity_crud.ALREADY_USED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": TOKEN_ALREADY_USED,
             "message": "This verification link has already been used."},
        )
    if outcome == identity_crud.EXPIRED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": TOKEN_EXPIRED,
             "message": "This verification link has expired. Request a new one."},
        )
    if outcome != identity_crud.VALID:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": TOKEN_INVALID, "message": "This verification link is not valid."},
        )

    try:
        identity_crud.consume_email_verification(db, _challenge)
    except ValueError:
        # Lost a race with a concurrent redemption of the same link. Single-use held; the
        # other request won. Report it as already used rather than as a server fault.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": TOKEN_ALREADY_USED,
             "message": "This verification link has already been used."},
        )

    return VerificationResultOut(
        status="verified",
        message="Email verified successfully. You can now continue to Zoiko Steam.",
    )


@router.post("/resend-verification", dependencies=[_RESEND_VERIFICATION_LIMIT])
def resend_verification(data: ResendVerificationIn, background: BackgroundTasks,
                        request: Request, db: Session = Depends(get_db)):
    """Issue a replacement IDN-001 challenge, superseding any earlier one.

    Always answers identically. A caller cannot learn whether the address exists, whether
    it is already verified, or whether anything was sent — the same rule the
    forgot-password endpoint already follows.
    """
    generic = {"message": "If that address needs verification, a new link has been sent."}
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if user is None or user.email_verified or not user.is_active:
        return generic
    _issue_verification(db, user, client_ip(request), background)
    return generic


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
