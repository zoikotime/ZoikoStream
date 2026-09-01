import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import EventRegistration, User

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=True)
_bearer_optional = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str | None:
    """Deployed behind Cloud Run, whose frontend terminates the connection and sets
    X-Forwarded-For itself — a caller cannot spoof this hop the way it could with a
    self-managed reverse proxy. Without reading it, request.client.host is Cloud Run's
    proxy address for every caller, not the real one."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user: User, remember: bool) -> str:
    # ZST-EC-001 Phase 10. organizations.security.session_timeout was stored and displayed
    # but never read, so a tenant that set "1 hour" still got the platform default. It is
    # applied here, and only when it SHORTENS the session - the setting exists to tighten a
    # tenant's own sessions, not to let one hold a token longer than the product allows.
    from .services import org_policy   # local import: services import security

    delta = org_policy.session_lifetime(getattr(user, "organization", None),
                                        remember=remember)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "exp": datetime.now(timezone.utc) + delta,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    try:
        payload = jwt.decode(creds.credentials, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload["sub"]
    except (JWTError, KeyError):
        raise unauthorized
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def get_current_user_optional(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_optional),
    db: Session = Depends(get_db),
) -> User | None:
    """Same decode as get_current_user, but returns None instead of 401 when there's no
    token or it doesn't check out — for endpoints a signed-out visitor can also hit
    (public event pages), where a bad/missing token just means "treat as anonymous"."""
    if creds is None:
        return None
    try:
        payload = jwt.decode(creds.credentials, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload["sub"]
    except (JWTError, KeyError):
        return None
    user = db.get(User, user_id)
    return user if user and user.is_active else None


def create_registration_token(registration: EventRegistration) -> str:
    """Scoped access token for an anonymous event registrant — not tied to a User row,
    so it can't go through create_access_token/get_current_user. Long-lived (90 days):
    it only ever unlocks the one already-public event it was issued for."""
    payload = {
        "reg": str(registration.id),
        "event_id": str(registration.event_id),
        "email": registration.email,
        "exp": datetime.now(timezone.utc) + timedelta(days=90),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_registration_payload(token: str, event_id: uuid.UUID) -> dict | None:
    """Full claims (reg id, email) of a registration token if it verifies and matches
    event_id, else None. watch_event needs the reg id to look up the row for private-event
    claim checking (crud.claim_registration); decode_registration_token below covers callers
    that only ever needed the email."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("event_id") != str(event_id):
        return None
    return payload


def decode_registration_token(token: str, event_id: uuid.UUID) -> str | None:
    """Returns the registered email if `token` verifies and matches `event_id`, else None."""
    payload = decode_registration_payload(token, event_id)
    return payload.get("email") if payload else None


def require_super_admin(user: User = Depends(get_current_user)) -> User:
    """Gate an endpoint to the platform super admin. Any other role -> 403."""
    if user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    return user


# Role privilege ladder, lowest to highest. super_admin sits on top: it clears every
# require_* gate and bypasses org isolation. Keep in sync with models.user.ROLES.
_ROLE_RANK = {
    "viewer": 0,
    "speaker": 1,
    "moderator": 2,
    "host": 3,
    "org_admin": 4,
    "super_admin": 5,
}


def require_min_role(minimum: str):
    """Build a reusable dependency that allows `minimum` and every role above it.
    super_admin always passes. Usage in any module: Depends(require_min_role("host"))."""
    threshold = _ROLE_RANK[minimum]

    def _dep(user: User = Depends(get_current_user)) -> User:
        if _ROLE_RANK.get(user.role, -1) < threshold:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"{minimum} access required")
        return user

    return _dep


# Named gates for the common cases; each also admits everything above it in the ladder.
require_org_admin = require_min_role("org_admin")
require_host = require_min_role("host")
require_moderator = require_min_role("moderator")


# ── Commercial RBAC (doc ZST-LE-COM-001 Section 25, "Canonical Access Matrix") ──────────
# Five columns from the doc's table, collapsed to plain yes/no per action. The doc
# qualifies some cells as "within threshold" / "within policy" — that assumes a
# dollar-threshold registry which doesn't exist yet (see commercial.py module docstring's
# "no invented values" doctrine): a future threshold table would REFINE these gates, not
# replace them, so collapsing to yes/no here is honest rather than a guessed number.
COMMERCIAL_ACTIONS = ("accept", "change", "refund_approve", "write_off", "media_access")

# Customer-side rows (Organization Owner == org_admin, Billing Admin, Event Producer/
# Operator == host). Event Organizer/moderator get no commercial authority in the doc.
_CUSTOMER_COMMERCIAL = {
    "org_admin":     {"accept": True,  "change": True,  "refund_approve": False, "write_off": False, "media_access": False},
    "billing_admin": {"accept": True,  "change": True,  "refund_approve": False, "write_off": False, "media_access": False},
    "host":          {"accept": False, "change": False, "refund_approve": False, "write_off": False, "media_access": True},
}

# Zoiko-staff rows. Only reached when a super_admin has staff_commercial_role SET —
# unset means full access (see commercial_can below).
_STAFF_COMMERCIAL = {
    "sales":       {"accept": False, "change": True,  "refund_approve": False, "write_off": False, "media_access": False},
    "finance_ops": {"accept": False, "change": True,  "refund_approve": True,  "write_off": True,  "media_access": False},
    "live_ops":    {"accept": False, "change": True,  "refund_approve": False, "write_off": False, "media_access": True},
    "support":     {"accept": False, "change": False, "refund_approve": False, "write_off": False, "media_access": False},
    "security":    {"accept": False, "change": False, "refund_approve": False, "write_off": False, "media_access": False},
}


def commercial_can(user: User, action: str) -> bool:
    """Whether `user` may perform a Section-25 commercial action. A super_admin with no
    staff_commercial_role assigned gets full access — today's actual behavior for every
    existing account, unchanged. Assigning a role narrows that one staff member down to
    exactly what the doc's matrix grants it."""
    if action not in COMMERCIAL_ACTIONS:
        raise ValueError(f"unknown commercial action: {action!r}")
    if user.role == "super_admin":
        if user.staff_commercial_role is None:
            return True
        return _STAFF_COMMERCIAL.get(user.staff_commercial_role, {}).get(action, False)
    return _CUSTOMER_COMMERCIAL.get(user.role, {}).get(action, False)


def require_commercial(action: str):
    """Build a reusable dependency gating one Section-25 commercial action.
    Usage: Depends(require_commercial("refund_approve"))."""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not commercial_can(user, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Not authorized for commercial action: {action}")
        return user

    return _dep


def org_scoped(stmt, model, user: User):
    """Constrain a select() to the caller's organization. super_admin sees every org.
    The single place org isolation lives — modules call this instead of hand-writing
    `.where(model.org_id == user.org_id)`, so the super_admin bypass stays consistent.
    `model` must expose an `org_id` column."""
    if user.role == "super_admin":
        return stmt
    return stmt.where(model.org_id == user.org_id)
