from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=True)
_bearer_optional = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user: User, remember: bool) -> str:
    delta = (
        timedelta(days=settings.REMEMBER_TOKEN_DAYS)
        if remember
        else timedelta(hours=settings.ACCESS_TOKEN_HOURS)
    )
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


def org_scoped(stmt, model, user: User):
    """Constrain a select() to the caller's organization. super_admin sees every org.
    The single place org isolation lives — modules call this instead of hand-writing
    `.where(model.org_id == user.org_id)`, so the super_admin bypass stays consistent.
    `model` must expose an `org_id` column."""
    if user.role == "super_admin":
        return stmt
    return stmt.where(model.org_id == user.org_id)
