import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import admin as admin_crud
from ..crud import identity as identity_crud
from ..crud import recovery as recovery_crud
from ..services import auth_sessions
from ..services import org_policy
from ..services import stepup as stepup_svc
from ..db import get_db
from .. import email as email_mod
from ..email import (
    UnsafeLinkError,
    send_email_verification_email,
    send_reset_otp_email,
    verification_url,
)
from ..models import STEP_UP_GOOGLE_LINK, STEP_UP_PURPOSES, STEP_UP_TTL_MINUTES, ALLOW, ALLOW_NEW_CONTEXT, AUTH_METHOD_GOOGLE, AUTH_METHOD_PASSWORD, BLOCK_SUSPICIOUS, IDENTITY_PROVIDER_GOOGLE, SESSION_END_LOGOUT, Organization, User, UserIdentity
from ..services import google_oauth
from ..services import identity_security as idsec
from ..ratelimit import rate_limit
from ..schemas import (
    ChangeRecoveryContactIn,
    StepUpIn,
    StepUpOut,
    ConfirmRecoveryContactIn,
    ForgotPasswordIn,
    LoginIn,
    GoogleCallbackIn,
    GoogleIdentityOut,
    GoogleLinkCompleteIn,
    GoogleStartOut,
    RegisterIn,
    RegistrationPendingOut,
    ResendVerificationIn,
    ChangePasswordIn,
    ResetPasswordIn,
    SessionStatusOut,
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
from ..services import platform_settings

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
# Starting a Google sign-in is cheap and stateless (nothing is stored), but it is still an
# unauthenticated endpoint that mints signed values, so it is bounded like the others.
_GOOGLE_START_LIMIT = rate_limit("google-start", limit=20, window=60.0)
_OTP_REQUEST_LIMIT = rate_limit("otp-request", limit=5, window=300.0)
_OTP_VERIFY_LIMIT = rate_limit("otp-verify", limit=10, window=300.0)
_REGISTER_LIMIT = rate_limit("register", limit=5, window=300.0)
# Redemption is guessing-resistant on its own (256-bit token), so this budget is about
# keeping the hash+DB path cheap rather than protecting the secret.
_VERIFY_EMAIL_LIMIT = rate_limit("verify-email", limit=20, window=300.0)
# Resend is the expensive one: it sends mail to an address the caller merely names, so it
# is both a spam vector against third parties and a cost vector. Tightest budget here.
_RESEND_VERIFICATION_LIMIT = rate_limit("resend-verification", limit=3, window=900.0)
# A signed-in password change still guesses the CURRENT password on every attempt, so it is
# an online guessing surface like login and is limited like one. Slightly tighter than login
# because a legitimate user gets this right on the first or second try — they are typing a
# password they already know.
_PASSWORD_CHANGE_LIMIT = rate_limit("password-change", limit=5, window=300.0)


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


# --- IDN-003 / IDN-004 / IDN-005 dispatch ------------------------------------------------
# The auth router does not decide who gets email. It hands the authoritative event to the
# identity-security layer, which owns the rules, and these three helpers translate a
# committed event into one queued message. Every one of them is fire-and-forget by design:
# the security fact is already committed, and a mail outage must never undo it.

def _utc_stamp(moment: datetime) -> str:
    """Timezone-aware display stamp. Security mail is audit-sensitive, so it shows UTC."""
    return moment.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def _notify_new_sign_in(db: Session, user: User, event, background: BackgroundTasks) -> None:
    """IDN-003. Only for a sign-in already recorded as a new context."""
    if not event.is_new_context:
        return
    if not idsec.claim_notification(db, event):
        return                      # already notified for this exact event
    try:
        background.add_task(
            email_mod.send_new_sign_in_email,
            user.email,
            signed_in_at=_utc_stamp(event.occurred_at),
            authentication_method=_AUTH_METHOD_LABELS.get(event.authentication_method,
                                                          event.authentication_method),
            device=idsec.describe_device(event.browser_family, event.platform_family),
            location=idsec.describe_location(event.approximate_location),
            session_reference=event.session_reference,
        )
    except UnsafeLinkError:
        log.exception("IDN-003 not sent: APP_URL unsafe for this environment")


def _notify_blocked_sign_in(db: Session, user: User, event, background: BackgroundTasks) -> None:
    """IDN-004. One message per security episode, not per blocked attempt."""
    if idsec.block_notice_already_sent(db, user):
        return                      # a burst of identical attempts is one event to a human
    if not idsec.claim_notification(db, event):
        return
    try:
        background.add_task(
            email_mod.send_suspicious_sign_in_email,
            user.email,
            attempted_at=_utc_stamp(event.occurred_at),
            device=idsec.describe_device(event.browser_family, event.platform_family),
            location=idsec.describe_location(event.approximate_location),
            session_reference=event.session_reference,
        )
    except UnsafeLinkError:
        log.exception("IDN-004 not sent: APP_URL unsafe for this environment")


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

    # The Settings console's "Signups enabled" switch (services.platform_settings) — off
    # means no new organizations, full stop. The one exception is the designated super-admin
    # email: signups being off platform-wide must never be able to lock out the account that
    # would otherwise be the only way to turn it back on.
    if email != settings.SUPER_ADMIN_EMAIL.lower() and not platform_settings.signups_enabled(db):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Signups are currently disabled")

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

    # Initial subscription: Developer, `trialing`, 14 days from now (approved Product
    # decision). This path created NO subscription at all before, which is why 2149 existing
    # organizations have none — self-registration is how almost all of them were created, and
    # without a subscription row they can neither be metered against a plan nor buy one
    # (`create_subscription_checkout` returns 409 "no subscription record to upgrade").
    #
    # Added BEFORE the commit below on purpose: the organization, its first user and its
    # subscription then land in ONE transaction, so registration can never persist an
    # organization with a half-written subscription beside it. The helper does not commit for
    # exactly this reason.
    #
    # Touches no payment surface — no card is collected, no Stripe subscription is created and
    # no charge is raised. It is also idempotent, so a retried registration cannot double it.
    admin_crud.provision_initial_subscription(db, org)

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
def login(data: LoginIn, background: BackgroundTasks, request: Request,
          db: Session = Depends(get_db)):
    ident = data.identifier.strip().lower()
    ip, agent = client_ip(request), request.headers.get("user-agent")
    user = db.scalar(
        select(User).where(or_(User.email == ident, func.lower(User.username) == ident))
    )

    # Risk decision BEFORE the password is checked, so a correct password offered during an
    # active guessing run still does not grant access. An unknown address is always ALLOW:
    # branching on it would leak whether the account exists.
    if user is not None and idsec.evaluate_sign_in(db, user) == BLOCK_SUSPICIOUS:
        event = idsec.record_event(db, user=user, outcome="blocked", ip=ip, user_agent=agent,
                                   risk_decision=BLOCK_SUSPICIOUS)
        _notify_blocked_sign_in(db, user, event, background)
        # Returned, not raised. `background` is attached to the RESPONSE object, and raising
        # HTTPException makes FastAPI build a fresh response through the exception handler --
        # which silently discards every queued task, so the IDN-004 notice would never send.
        # Constructing the response here keeps the task attached while emitting exactly the
        # same body and status as a wrong password: a caller must not be able to tell a block
        # from a bad credential, or the block becomes an oracle.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid credentials"},
            background=background,
        )

    if user is None or not verify_password(data.password, user.password_hash):
        # Recorded even for an unknown address (user_id stays NULL) so the failure counter
        # is durable rather than living only in the per-process rate limiter.
        idsec.record_event(db, user=user, outcome="failed", ip=ip, user_agent=agent)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    _require_account_may_sign_in(user)
    return _complete_sign_in(db, user, remember=data.remember, ip=ip, agent=agent,
                             background=background, method=AUTH_METHOD_PASSWORD)


# ── shared by every sign-in method (password, Google) ───────────────────────────────────
# One account gate and one session issuer, so a new sign-in method can never drift from the
# rules password sign-in enforces: disabled accounts, unverified ZoikoStream email, the
# authoritative sign-in record, the new-context notice, and one server-side session.

def _require_account_may_sign_in(user: User) -> None:
    """Raise unless this (already authenticated) account may receive a session."""
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    if not user.email_verified:
        # Credentials were correct, so no session is issued and no token is minted. The
        # code is structured (not prose) because the client renders a specific state with a
        # resend action. Only reachable after a correct credential, so it discloses nothing
        # an attacker did not already have.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {
                "code": EMAIL_VERIFICATION_REQUIRED,
                "message": "Verify your email to continue.",
                "email": _mask_email(user.email),
            },
        )


def _complete_sign_in(db: Session, user: User, *, remember: bool, ip: str | None,
                      agent: str | None, background: BackgroundTasks, method: str) -> TokenOut:
    # Authoritative sign-in record, committed before any mail is queued.
    browser, platform = idsec.parse_user_agent(agent)
    is_new = idsec.is_new_context(db, user, browser, platform)
    event = idsec.record_event(
        db, user=user, outcome="success", ip=ip, user_agent=agent,
        risk_decision=ALLOW_NEW_CONTEXT if is_new else ALLOW, is_new=is_new, method=method,
    )
    _notify_new_sign_in(db, user, event, background)

    # Include organization name in response
    user_out = UserOut.model_validate(user)
    user_out.organization_name = user.organization.name if user.organization else None

    return TokenOut(
        # One server-side session per sign-in (services/auth_sessions.py): its own idle and
        # absolute limits, revocable on its own, independent of the user's other devices.
        access_token=create_access_token(user, remember=remember, db=db, user_agent=agent),
        user=user_out,
    )


# ── Google sign-in (OpenID Connect + PKCE; services/google_oauth.py) ─────────────────────
#
# IDENTITY RULES
#   * Google NEVER creates an account. Organizations are created through Register and people
#     join one by invitation; a Google identity only signs in to an account that exists.
#   * Google signs in ONLY through a stored link to Google's stable account id, the ID
#     token's `sub` (models/user_identity.py). An email match is never enough: addresses get
#     reassigned, `sub` never does. No link, no sign-in — and the refusal is the same whether
#     or not the email belongs to an account, so it is not an account-existence oracle.
#   * A link is made only by the signed-in account holder, after a fresh password step-up,
#     with the Google account whose verified email is the account's own email
#     (google_link_start / google_link_complete). One Google account per ZoikoStream account
#     and vice versa; replacing a link means unlinking first, deliberately.
#   * Google must be authoritative for the email's domain — @gmail.com, or a Workspace
#     account whose `hd` equals the email's domain (google_oauth.google_is_authoritative).
#   * super_admin accounts cannot use Google at all, to sign in or to link: platform-wide
#     authority stays behind the password until Google identity linking has been
#     independently verified in production.
#   * Once a link resolves the account, it is treated exactly like a password sign-in: same
#     risk block, same disabled / unverified-email gates, same session, role and tenant.
#     Google cannot raise a role or move an account between organizations.

# Error code -> HTTP status for Google failures that happen before an account is known.
_GOOGLE_ERROR_STATUS = {
    google_oauth.NOT_CONFIGURED: status.HTTP_503_SERVICE_UNAVAILABLE,
    google_oauth.INVALID_STATE: status.HTTP_400_BAD_REQUEST,
    google_oauth.EXCHANGE_FAILED: status.HTTP_401_UNAUTHORIZED,
    google_oauth.INVALID_TOKEN: status.HTTP_401_UNAUTHORIZED,
    google_oauth.EMAIL_UNVERIFIED: status.HTTP_403_FORBIDDEN,
    google_oauth.DOMAIN_UNVERIFIED: status.HTTP_403_FORBIDDEN,
}
GOOGLE_NOT_LINKED = "GOOGLE_NOT_LINKED"
GOOGLE_ROLE_NOT_PERMITTED = "GOOGLE_ROLE_NOT_PERMITTED"
GOOGLE_ALREADY_LINKED = "GOOGLE_ALREADY_LINKED"
GOOGLE_IDENTITY_IN_USE = "GOOGLE_IDENTITY_IN_USE"
GOOGLE_EMAIL_MISMATCH = "GOOGLE_EMAIL_MISMATCH"

# Roles that may never authenticate with Google. Enforced here, server-side, on every Google
# entry point — hiding a button is not a control.
_GOOGLE_BLOCKED_ROLES = frozenset({"super_admin"})

# Display labels for the IDN-003 new-sign-in email. Display only: SignInEvent keeps the stable
# machine value ("password" / "google").
_AUTH_METHOD_LABELS = {AUTH_METHOD_GOOGLE: "Google sign-in"}


def _google_error(exc: google_oauth.GoogleOAuthError) -> HTTPException:
    return HTTPException(
        _GOOGLE_ERROR_STATUS.get(exc.code, status.HTTP_401_UNAUTHORIZED),
        {"code": exc.code, "message": exc.message},
    )


def _google_refusal(http_status: int, code: str, message: str) -> HTTPException:
    return HTTPException(http_status, {"code": code, "message": message})


def _google_role_permitted(user: User) -> bool:
    return user.role not in _GOOGLE_BLOCKED_ROLES


def _google_role_refusal() -> HTTPException:
    return _google_refusal(status.HTTP_403_FORBIDDEN, GOOGLE_ROLE_NOT_PERMITTED,
                           "Google sign-in isn't available for this account. "
                           "Please sign in with your email and password.")


def _google_link_for(db: Session, user: User) -> UserIdentity | None:
    return db.scalar(select(UserIdentity).where(UserIdentity.user_id == user.id,
                                                UserIdentity.provider == IDENTITY_PROVIDER_GOOGLE))


def _link_session_binding(request: Request) -> str:
    """What binds a link attempt to the session that started it. Derived from the
    server-side session row (itself only a hash of the token's `sid`) and hashed again, so
    the state that travels through Google and the browser never carries a session key."""
    session = request.state.auth_session
    return hashlib.sha256(f"google-link:{session.id}".encode()).hexdigest()[:32]


def _google_identity_status(user: User, link: UserIdentity | None) -> GoogleIdentityOut:
    return GoogleIdentityOut(
        available=google_oauth.is_configured() and _google_role_permitted(user),
        linked=link is not None,
        email=link.email_at_link_time if link else None,
        linked_at=link.linked_at if link else None,
        last_used_at=link.last_used_at if link else None,
    )


def _audit_google_link(db: Session, user: User, action: str, *, email: str, subject: str,
                       hosted_domain: str | None, ip: str | None) -> None:
    """Security history for link/unlink, in the platform audit log. Commits. Google's `sub`
    is recorded only as a short fingerprint: enough to correlate, not a cross-site id."""
    admin_crud.create_audit_log(
        db, actor=user, action=action, target_type="user", target_id=user.id, org_id=user.org_id,
        meta={"provider": IDENTITY_PROVIDER_GOOGLE, "google_email": email,
              "subject_fingerprint": hashlib.sha256(subject.encode()).hexdigest()[:12],
              "hosted_domain": hosted_domain},
        ip=ip,
    )


@router.post("/google/start", response_model=GoogleStartOut, dependencies=[_GOOGLE_START_LIMIT])
def google_start():
    """Step 1: Google's consent URL plus the signed state and PKCE verifier for this attempt.
    503 GOOGLE_SIGN_IN_UNAVAILABLE until all three GOOGLE_* settings are configured."""
    try:
        req = google_oauth.build_authorization_request()
    except google_oauth.GoogleOAuthError as exc:
        raise _google_error(exc) from None
    return GoogleStartOut(authorization_url=req.authorization_url, state=req.state,
                          code_verifier=req.code_verifier)


@router.post("/google/callback", response_model=TokenOut, dependencies=[_LOGIN_LIMIT])
def google_callback(data: GoogleCallbackIn, background: BackgroundTasks, request: Request,
                    db: Session = Depends(get_db)):
    """Step 3: verify everything Google returned and, if this Google account is LINKED to an
    account, sign that account in with the same rules and session as POST /auth/login."""
    ip, agent = client_ip(request), request.headers.get("user-agent")
    try:
        identity = google_oauth.complete(code=data.code, state=data.state,
                                         code_verifier=data.code_verifier)
    except google_oauth.GoogleOAuthError as exc:
        if exc.code == google_oauth.INVALID_TOKEN:
            # A token that fails verification is the one Google outcome that looks like an
            # attack rather than a user or network problem: keep the durable record.
            idsec.record_event(db, user=None, outcome="failed", ip=ip, user_agent=agent,
                               method=AUTH_METHOD_GOOGLE)
        raise _google_error(exc) from None

    # The ONLY way from a Google identity to an account: its stable `sub`. The email address
    # is deliberately not consulted — neither to find an account nor to fall back to one.
    link = db.scalar(select(UserIdentity).where(UserIdentity.provider == IDENTITY_PROVIDER_GOOGLE,
                                                UserIdentity.subject == identity.sub))
    user = db.get(User, link.user_id) if link is not None else None
    if user is None:
        # One answer whether or not the email has an account, so this reveals nothing about
        # which addresses are registered.
        idsec.record_event(db, user=None, outcome="failed", ip=ip, user_agent=agent,
                           method=AUTH_METHOD_GOOGLE)
        raise _google_refusal(
            status.HTTP_403_FORBIDDEN, GOOGLE_NOT_LINKED,
            "This Google account isn't linked to a ZoikoStream account. Sign in with your email "
            "and password, then link Google from Settings > Security.")

    if not _google_role_permitted(user):
        idsec.record_event(db, user=user, outcome="failed", ip=ip, user_agent=agent,
                           method=AUTH_METHOD_GOOGLE)
        raise _google_role_refusal()

    # The same risk decision password sign-in applies, with the same indistinguishable answer.
    if idsec.evaluate_sign_in(db, user) == BLOCK_SUSPICIOUS:
        event = idsec.record_event(db, user=user, outcome="blocked", ip=ip, user_agent=agent,
                                   risk_decision=BLOCK_SUSPICIOUS, method=AUTH_METHOD_GOOGLE)
        _notify_blocked_sign_in(db, user, event, background)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid credentials"},
            background=background,
        )

    _require_account_may_sign_in(user)
    # Committed with the sign-in record inside _complete_sign_in.
    link.last_used_at = datetime.now(timezone.utc)
    return _complete_sign_in(db, user, remember=data.remember, ip=ip, agent=agent,
                             background=background, method=AUTH_METHOD_GOOGLE)


# ── Linking Google to the signed-in account ─────────────────────────────────────────────

@router.get("/google/identity", response_model=GoogleIdentityOut)
def google_identity(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """This account's Google sign-in status. `available` is false when Google sign-in is not
    configured or this account's role may not use it."""
    return _google_identity_status(user, _google_link_for(db, user))


@router.post("/google/link/start", response_model=GoogleStartOut, dependencies=[_GOOGLE_START_LIMIT])
def google_link_start(request: Request,
                      step_up: str | None = Header(None, alias="X-Step-Up"),
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Begin linking a Google account to THIS account. Needs a fresh password step-up
    (purpose google_link): a session alone is not evidence the account holder is asking.

    Every refusal that does not depend on the step-up comes first, so a grant is never spent
    on a request that was going to fail anyway."""
    if not _google_role_permitted(user):
        raise _google_role_refusal()
    if not google_oauth.is_configured():
        raise _google_error(google_oauth.GoogleOAuthError(
            google_oauth.NOT_CONFIGURED, "Google sign-in isn't available yet."))
    if _google_link_for(db, user) is not None:
        raise _google_refusal(status.HTTP_409_CONFLICT, GOOGLE_ALREADY_LINKED,
                              "A Google account is already linked. Unlink it before linking another.")

    outcome = stepup_svc.consume(db, user, reference=step_up, purpose=STEP_UP_GOOGLE_LINK,
                                 spent_on="link Google sign-in")
    if outcome != stepup_svc.OK:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"code": "STEP_UP_REQUIRED", "purpose": STEP_UP_GOOGLE_LINK,
             "message": stepup_svc.describe(outcome)})

    try:
        req = google_oauth.build_authorization_request(
            purpose=google_oauth.STATE_PURPOSE_LINK, user_id=str(user.id),
            session_id=_link_session_binding(request))
    except google_oauth.GoogleOAuthError as exc:
        raise _google_error(exc) from None
    return GoogleStartOut(authorization_url=req.authorization_url, state=req.state,
                          code_verifier=req.code_verifier)


@router.post("/google/link/complete", response_model=GoogleIdentityOut, dependencies=[_LOGIN_LIMIT])
def google_link_complete(data: GoogleLinkCompleteIn, background: BackgroundTasks, request: Request,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Finish a link started by google_link_start in THIS session: verify Google's answer
    exactly as sign-in does, then record the link to Google's `sub`.

    Refused when the Google email is not the account's own email, when this Google account is
    linked to another ZoikoStream account, or when this account already holds a different
    Google account. Never relinks automatically."""
    if not _google_role_permitted(user):
        raise _google_role_refusal()
    ip = client_ip(request)
    try:
        identity = google_oauth.complete(
            code=data.code, state=data.state, code_verifier=data.code_verifier,
            purpose=google_oauth.STATE_PURPOSE_LINK, user_id=str(user.id),
            session_id=_link_session_binding(request))
    except google_oauth.GoogleOAuthError as exc:
        raise _google_error(exc) from None

    if identity.email != (user.email or "").strip().lower():
        raise _google_refusal(
            status.HTTP_409_CONFLICT, GOOGLE_EMAIL_MISMATCH,
            f"That Google account uses a different email address. Choose the Google account "
            f"for {user.email}.")

    owner = db.scalar(select(UserIdentity).where(UserIdentity.provider == IDENTITY_PROVIDER_GOOGLE,
                                                 UserIdentity.subject == identity.sub))
    if owner is not None:
        if owner.user_id == user.id:
            return _google_identity_status(user, owner)        # already linked to this account
        raise _google_refusal(status.HTTP_409_CONFLICT, GOOGLE_IDENTITY_IN_USE,
                              "This Google account is already linked to a different ZoikoStream account.")
    if _google_link_for(db, user) is not None:
        raise _google_refusal(status.HTTP_409_CONFLICT, GOOGLE_ALREADY_LINKED,
                              "A different Google account is already linked. Unlink it before linking another.")

    link = UserIdentity(provider=IDENTITY_PROVIDER_GOOGLE, subject=identity.sub, user_id=user.id,
                        email_at_link_time=identity.email)
    db.add(link)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent link won the race; the database constraints are the final word.
        db.rollback()
        raise _google_refusal(status.HTTP_409_CONFLICT, GOOGLE_ALREADY_LINKED,
                              "This Google account or this ZoikoStream account is already linked.") from None
    _audit_google_link(db, user, "user.google_identity.linked", email=identity.email,
                       subject=identity.sub, hosted_domain=identity.hosted_domain, ip=ip)   # commits
    db.refresh(link)
    background.add_task(
        email_mod.send_security_setting_changed_email, user.email,
        change_description=email_mod.IDN_006_GOOGLE_LINKED,
        changed_at=_utc_stamp(link.linked_at or datetime.now(timezone.utc)),
    )
    return _google_identity_status(user, link)


@router.delete("/google/identity", response_model=GoogleIdentityOut)
def google_unlink(background: BackgroundTasks, request: Request,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove this account's Google link. Removing a way in is always allowed (no step-up,
    any role); it is recorded and the account holder is told."""
    link = _google_link_for(db, user)
    if link is None:
        raise _google_refusal(status.HTTP_404_NOT_FOUND, GOOGLE_NOT_LINKED,
                              "No Google account is linked to this account.")
    email, subject = link.email_at_link_time, link.subject
    db.delete(link)
    _audit_google_link(db, user, "user.google_identity.unlinked", email=email, subject=subject,
                       hosted_domain=None, ip=client_ip(request))                          # commits
    background.add_task(
        email_mod.send_security_setting_changed_email, user.email,
        change_description=email_mod.IDN_006_GOOGLE_UNLINKED,
        changed_at=_utc_stamp(datetime.now(timezone.utc)),
    )
    return _google_identity_status(user, None)


# ── IDN-001 email verification ──────────────────────────────────────────────────────────

def _send_account_ready(db: Session, user: User, background: BackgroundTasks) -> None:
    """Queue IDN-002 if — and only if — the account is genuinely ready.

    Every exit here is silent to the caller by design: verification has already been
    committed, and nothing about delivering a courtesy notice may undo or fail that
    (ZST-EC-001: email communicates confirmed state, it never creates it).

    Three gates, in order:
      1. access actually active — otherwise "your access is active" would be false
      2. the send slot is claimable — one message per account, ever
      3. the link base is safe — a bad base releases the claim so a retry stays possible
    """
    if not identity_crud.access_is_active(db, user):
        # Verified identity, restricted account. Correct outcome: verification stands, no
        # message. Telling a suspended tenant their access is ready is worse than silence.
        log.info("IDN-002 suppressed for %s: access not active", user.id)
        return

    if not identity_crud.claim_account_ready(db, user):
        # Another request already claimed it — a reused link, a double click, a retry.
        return

    try:
        url = email_mod.account_ready_url()
    except UnsafeLinkError:
        # Never attempted, so the claim must not stand or the message is lost forever.
        identity_crud.release_account_ready(db, user)
        log.exception("IDN-002 not sent for %s: APP_URL is unsafe for this environment", user.id)
        return

    background.add_task(email_mod.send_account_ready_email, user.email, url)


@router.post("/verify-email", response_model=VerificationResultOut,
             dependencies=[_VERIFY_EMAIL_LIMIT])
def verify_email(data: VerifyEmailIn, background: BackgroundTasks,
                 db: Session = Depends(get_db)):
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
        user = identity_crud.consume_email_verification(db, _challenge)
    except ValueError:
        # Lost a race with a concurrent redemption of the same link. Single-use held; the
        # other request won. Report it as already used rather than as a server fault.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": TOKEN_ALREADY_USED,
             "message": "This verification link has already been used."},
        )

    # IDN-002 "Account ready" — the one legitimate trigger point. Identity is now verified
    # against authoritative backend state, so the message can truthfully say so.
    _send_account_ready(db, user, background)

    return VerificationResultOut(
        status="verified",
        message="Email verified successfully. You can now continue to Zoiko Stream.",
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


# --- IDN-007 Account recovery lifecycle -------------------------------------------------
# Replaces the previous reset flow entirely. The old one stored a 4-digit code in cleartext
# on users.reset_token with no attempt counter; the code is now 6 digits, hashed, purpose-
# bound, single-use, and backed by a durable AccountRecovery record with a lockout.
#
# Recipients follow the canonical contract: the account holder, plus an APPROVED (verified)
# recovery contact when one exists. An unverified nomination is never copied.

def _recovery_notice(db, user, recovery, background, column, send, **kwargs):
    """Claim one transition's notification, then queue it to every approved recipient."""
    if not recovery_crud.claim_notice(db, recovery, column):
        return                                  # already sent for this transition
    for address in recovery_crud.recovery_recipients(user):
        background.add_task(send, address, **kwargs)


@router.post("/forgot-password", dependencies=[_OTP_REQUEST_LIMIT])
def forgot_password(data: ForgotPasswordIn, background: BackgroundTasks,
                    db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    # Unchanged property: always the same answer, so this cannot probe which addresses exist.
    #
    # `expires_in_minutes` is policy, not account state: identical for an address that
    # exists and one that does not, so it discloses nothing the enumeration guard protects.
    # It is here because the UI used to hardcode its own number and drifted — it told people
    # the code lasted 10 minutes while the challenge actually lived 15. A client that reads
    # the TTL from the server cannot drift from it again.
    resp = {
        "message": "If that email exists, a verification code has been sent.",
        "expires_in_minutes": recovery_crud.RECOVERY_TTL_MINUTES,
        "code_length": recovery_crud.CODE_DIGITS,
    }
    if user is None:
        return resp

    recovery, code, _is_new = recovery_crud.start_recovery(db, user)
    started = _utc_stamp(recovery.started_at)
    expires = _utc_stamp(datetime.now(timezone.utc)
                         + timedelta(minutes=recovery_crud.RECOVERY_TTL_MINUTES))

    # Two distinct messages, per the canonical variants: "recovery started" is the
    # notice-of-record, "additional verification" carries the code. The first is claimed
    # once per recovery, so a resend rotates the code without re-announcing the recovery.
    _recovery_notice(db, user, recovery, background, "started_notified_at",
                     email_mod.send_recovery_started_email,
                     started_at=started, security_reference=str(recovery.id)[:8])
    for address in recovery_crud.recovery_recipients(user):
        background.add_task(email_mod.send_recovery_verification_email, address,
                            code=code, expires_at=expires)
    return resp


def _load_recovery_user(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        # Same error as a wrong code: nothing here may reveal whether the address exists.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")
    return user


def _check_code(db: Session, user: User, code: str):
    outcome, recovery = recovery_crud.verify_code(db, user, code)
    if outcome == recovery_crud.LOCKED:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many incorrect codes. Recovery is locked; request a new code later.",
        )
    if outcome != recovery_crud.VALID:
        # Wrong, expired and unknown all answer identically.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")
    return recovery


@router.post("/verify-otp", dependencies=[_OTP_VERIFY_LIMIT])
def verify_otp(data: VerifyOtpIn, db: Session = Depends(get_db)):
    """Check the code without spending it -- the reset step still needs it."""
    user = _load_recovery_user(db, data.email)
    _check_code(db, user, data.otp)
    return {"message": "Code verified."}


@router.post("/reset-password", dependencies=[_OTP_VERIFY_LIMIT])
def reset_password(data: ResetPasswordIn, background: BackgroundTasks,
                   db: Session = Depends(get_db)):
    user = _load_recovery_user(db, data.email)
    recovery = _check_code(db, user, data.otp)

    # The Organization's password floor is enforced here, not merely displayed. It may
    # only ever be STRICTER than the platform baseline (services/org_policy), so a tenant
    # setting can tighten the rule but never weaken it.
    #
    # Checked BEFORE the code is spent, deliberately. Rejecting the password after consuming
    # the single-use code would burn the recovery attempt on a validation failure and force
    # the user to start recovery again - punishing them for a typo.
    violation = org_policy.password_violation(user.organization, data.password)
    if violation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, violation)

    # Spend the code BEFORE committing the credential: single-use must hold even if the
    # write below fails.
    if not recovery_crud.consume_code(db, user, data.otp):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")

    user.password_hash = hash_password(data.password)
    # Retire the legacy columns as recovery now owns this flow.
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()

    recovery_crud.complete_recovery(db, recovery)
    changed_at = _utc_stamp(datetime.now(timezone.utc))

    # IDN-005 -- the credential itself changed. Fires only here, after the commit.
    background.add_task(
        email_mod.send_credential_changed_email,
        user.email,
        credential_type="password",
        changed_at=changed_at,
        session_effect=email_mod.SESSION_EFFECT_NOT_REVOKED,
        security_reference=str(recovery.id)[:8],
    )
    # IDN-007 "Completed" -- a different family with a different trigger and a wider
    # recipient set (it also reaches the approved recovery contact). Both are correct:
    # one reports the credential change, the other closes the recovery lifecycle.
    _recovery_notice(db, user, recovery, background, "completed_notified_at",
                     email_mod.send_recovery_completed_email,
                     completed_at=changed_at,
                     credentials_reset="Password",
                     session_effect=email_mod.SESSION_EFFECT_NOT_REVOKED,
                     security_reference=str(recovery.id)[:8])
    return {"message": "Password updated. You can now log in."}


@router.post("/recovery/cancel")
def cancel_recovery(background: BackgroundTasks, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Cancel a recovery in flight.

    Authenticated on purpose: being able to sign in is itself proof the account is not lost,
    which is exactly who should be able to call off a recovery. An unauthenticated cancel
    would let an attacker suppress the victim's recovery attempt.
    """
    recovery = recovery_crud.active_recovery(db, user)
    if recovery is None:
        return {"message": "No recovery request is in progress."}
    recovery_crud.cancel_recovery(db, recovery, reason="canceled_by_account_holder")
    _recovery_notice(db, user, recovery, background, "canceled_notified_at",
                     email_mod.send_recovery_canceled_email,
                     canceled_at=_utc_stamp(datetime.now(timezone.utc)),
                     account_state="Active and secured",
                     security_reference=str(recovery.id)[:8])
    return {"message": "Recovery request canceled."}


# --- IDN-006 Recovery-method changes ----------------------------------------------------
# Only the "recovery method changed" variant exists. The MFA variants are not implemented:
# require_2fa is stored and reported but nothing enforces it, so announcing "MFA enabled"
# would assert a protection the platform does not provide. See the reported gap.

@router.post("/recovery-contact", dependencies=[_RESEND_VERIFICATION_LIMIT])
def set_recovery_contact(data: ChangeRecoveryContactIn, background: BackgroundTasks,
                         db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """Nominate a recovery address. Verification is sent to THAT address, not this one."""
    new_address = data.recovery_email.lower()
    if new_address == user.email.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Use an address different from your sign-in email.")
    raw = recovery_crud.start_recovery_contact_change(db, user, new_address)
    try:
        url = email_mod.recovery_contact_url(raw)
    except UnsafeLinkError:
        log.exception("Recovery-contact confirmation not sent: APP_URL unsafe")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "Recovery contact is not configured correctly. Contact support.")
    expires = _utc_stamp(datetime.now(timezone.utc) + timedelta(minutes=60))
    background.add_task(email_mod.send_recovery_contact_verification_email,
                        new_address, confirm_url=url, expires_at=expires)
    return {"message": "Confirm the new address from the email we just sent to it.",
            "pending": recovery_crud.mask_destination(new_address)}


@router.post("/recovery-contact/confirm", dependencies=[_VERIFY_EMAIL_LIMIT])
def confirm_recovery_contact(data: ConfirmRecoveryContactIn, background: BackgroundTasks,
                             db: Session = Depends(get_db)):
    """Redeem the confirmation and commit the change, then fire IDN-006."""
    user = recovery_crud.confirm_recovery_contact(db, data.token)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This confirmation link is not valid or has expired.")
    # IDN-006 goes to the ACCOUNT HOLDER, not the new address: the holder is the party who
    # needs to know their recovery destination moved. Destination is masked.
    background.add_task(
        email_mod.send_security_setting_changed_email,
        user.email,
        change_description=email_mod.IDN_006_RECOVERY_METHOD_CHANGED,
        changed_at=_utc_stamp(user.recovery_email_verified_at),
        masked_destination=recovery_crud.mask_destination(user.recovery_email),
    )
    return {"message": "Recovery address confirmed.",
            "recovery_email": recovery_crud.mask_destination(user.recovery_email)}


@router.post("/step-up", response_model=StepUpOut, dependencies=[_OTP_VERIFY_LIMIT])
def step_up(data: StepUpIn, request: Request, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """Re-verify the password and mint a short-lived, purpose-bound step-up grant.

    ZST-EC-001 ORG-003 / ORG-008. High-risk operations need proof the person at the keyboard
    is still the account holder RIGHT NOW. An existing session cannot supply that — it only
    proves someone authenticated at some point — so this route re-checks the credential and
    hands back a reference valid for one purpose, for a few minutes, once.

    Rate-limited on the same bucket as OTP verification: this is a password oracle if left
    unbounded.
    """
    ip = request.client.host if request.client else None
    outcome, reference = stepup_svc.issue(db, user, password=data.password,
                                          purpose=data.purpose, ip=ip)
    if outcome == stepup_svc.UNKNOWN_PURPOSE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Unsupported purpose. Expected one of {list(STEP_UP_PURPOSES)}")
    if outcome != stepup_svc.OK:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, stepup_svc.describe(outcome))
    return StepUpOut(reference=reference, purpose=data.purpose,
                     expires_in_minutes=STEP_UP_TTL_MINUTES)


@router.patch("/password", dependencies=[_PASSWORD_CHANGE_LIMIT])
def change_password(data: ChangePasswordIn, background: BackgroundTasks,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Change the signed-in user's password.

    The same credential lifecycle as reset-password, minus the recovery code: verify, check
    the organization's policy, hash, commit, then notify. It exists because there was no
    authenticated path at all — "Change Password" in the console sent people to
    /forgot-password, which mails a one-time code to change a password they already know.

    Deliberately NOT taking an email or a user id: the account comes from the bearer token,
    so this endpoint cannot be pointed at somebody else's credential.

    Order matters, and it is the same order reset-password uses:
      * verify the current password FIRST — a caller who cannot prove ownership learns
        nothing about the policy
      * check the policy BEFORE writing, so a rejected password leaves the stored hash
        untouched
      * commit, and only then queue the notification, so no mail can claim a change that
        was rolled back
    """
    # Constant-time comparison inside verify_password (passlib); never a manual == on hashes.
    if not verify_password(data.current_password, user.password_hash or ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")

    # Rejected as a policy failure rather than silently succeeding: a "change" that changes
    # nothing still sends a credential-changed email, which trains people to ignore it.
    if verify_password(data.new_password, user.password_hash or ""):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "New password must be different from your current password")

    # The organization's floor, from the one place that defines it. Never re-implemented
    # here, and never weaker than the platform baseline (services/org_policy).
    violation = org_policy.password_violation(user.organization, data.new_password)
    if violation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, violation)

    user.password_hash = hash_password(data.new_password)
    db.commit()

    # IDN-005, after the commit — same family, same wording and the same session statement
    # the recovery path makes. This platform does not revoke sessions on a credential
    # change, so the mail says so rather than implying a logout that never happens.
    background.add_task(
        email_mod.send_credential_changed_email,
        user.email,
        credential_type="password",
        changed_at=_utc_stamp(datetime.now(timezone.utc)),
        session_effect=email_mod.SESSION_EFFECT_NOT_REVOKED,
        # No audit row exists for a self-service change (nothing persists one yet), so the
        # reference identifies the ACCOUNT for a support conversation — the same id-prefix
        # shape reset-password uses for its recovery row.
        security_reference=str(user.id)[:8],
    )
    # Never the hash, never either password.
    return {"message": "Password updated successfully"}


# ── Sign-in session lifecycle (services/auth_sessions.py) ───────────────────────────────
# Three calls, and only the second ever extends a session:
#   GET  /auth/session           where the session stands (deadlines on the server clock)
#   POST /auth/session/activity  the client saw genuine user activity -> slide the idle window
#   POST /auth/logout            end THIS session (this device); other devices are untouched
# Every other authenticated request is checked against the same limits but never extends
# them, so background polling and an abandoned tab cannot keep a session alive.

@router.get("/session", response_model=SessionStatusOut)
def session_status(request: Request, user: User = Depends(get_current_user)):
    return auth_sessions.status_of(request.state.auth_session)


@router.post("/session/activity", response_model=SessionStatusOut)
def session_activity(request: Request, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Record genuine user activity. Only reachable on a session that is still live (the
    dependency refuses an ended one), and capped by the absolute limit however often it is
    called, so it can postpone idle expiry but never resurrect or outlast a session."""
    session = auth_sessions.touch(db, request.state.auth_session)
    return auth_sessions.status_of(session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sign out this device: the session is revoked server-side, so the token it was issued
    with is refused from now on even if a copy survives somewhere."""
    auth_sessions.revoke(db, request.state.auth_session, SESSION_END_LOGOUT)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """The session endpoint the browser routes from.

    Returns the ACCOUNT identity and nothing about any event. A client deciding where to
    send somebody after login must not be able to read an event role here, because an
    assignment to one broadcast is not a property of the account — it is answered per event
    by GET /events/{event_id}/assignment.
    """
    user_out = UserOut.model_validate(user)
    org = user.organization
    user_out.organization_name = org.name if org else None
    # Platform authority, separated from organization standing.
    user_out.platform_role = "super_admin" if user.role == "super_admin" else None
    # "owner" is not a ROLES value - ownership lives on Organization.owner_user_id - so it
    # can only be reported through this field.
    if org is not None and org.owner_user_id == user.id:
        user_out.organization_role = "owner"
    elif user.role in ("org_admin", "billing_admin"):
        user_out.organization_role = user.role
    elif user.role == "super_admin":
        user_out.organization_role = None
    else:
        # host / moderator / speaker / viewer are ACCOUNT personas, and their standing in the
        # organization is plain membership. Reporting the persona here would re-create the
        # confusion this split exists to remove.
        user_out.organization_role = "member"
    return user_out
