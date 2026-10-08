"""Sign-in session lifecycle: start, check, record activity, revoke (models/auth_session.py).

The server decides whether a session is alive; the client never does. Every authenticated
request runs `check()`, which refuses a session that is:

  revoked   -> 401 SESSION_REVOKED   (signed out on this device)
  too old   -> 401 SESSION_EXPIRED   (reason "absolute": past its maximum length)
  idle      -> 401 SESSION_EXPIRED   (reason "idle": no reported activity for the idle window)
  unknown   -> 401 SESSION_EXPIRED   (reason "reauth": a token from before sessions existed,
                                      or one whose session no longer exists)

Idle time is measured from `last_activity_at`, which only `touch()` moves — and only the
explicit activity endpoint calls `touch()`, after the client has seen genuine interaction.
Ordinary requests (polling, heartbeats) are checked but never extend anything.

`_now()` is the one clock. Tests patch it; nothing else reads time for session decisions.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..config import settings
from ..models import SESSION_END_ABSOLUTE, SESSION_END_IDLE, AuthSession, User

log = logging.getLogger(__name__)

SESSION_EXPIRED = "SESSION_EXPIRED"
SESSION_REVOKED = "SESSION_REVOKED"
REASON_REAUTH = "reauth"

_EXPIRY_REASONS = (SESSION_END_IDLE, SESSION_END_ABSOLUTE, REASON_REAUTH)
_MESSAGES = {
    SESSION_END_IDLE: "Your session expired due to inactivity. Please sign in again.",
    SESSION_END_ABSOLUTE: "Your session reached its maximum length. Please sign in again.",
    REASON_REAUTH: "Please sign in again.",
}
_REVOKED_MESSAGE = "You have been signed out. Please sign in again."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    # Postgres returns aware datetimes; a driver that does not is read as UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _hash(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()


def idle_timeout() -> timedelta:
    return timedelta(minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES)


class SessionInvalid(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}:{reason}")
        self.code = code
        self.reason = reason


def start(db: Session, user: User, *, remember: bool, user_agent: str | None = None) -> tuple[str, AuthSession]:
    """Create a session and return (session id for the JWT, row). Commits.

    The absolute limit is fixed now: the platform maximum, shortened by the organization's
    own session_timeout policy (services/org_policy.py). "Remember me" changes nothing here.
    """
    from . import org_policy   # local import: org_policy imports security, which imports this

    now = _now()
    sid = secrets.token_urlsafe(32)                    # 256 bits; never stored, only hashed
    lifetime = org_policy.session_lifetime(getattr(user, "organization", None), remember=remember)
    row = AuthSession(
        id=_hash(sid), user_id=user.id, created_at=now, last_activity_at=now,
        absolute_expires_at=now + lifetime, remember=bool(remember),
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(row)
    db.commit()
    return sid, row


def _end(db: Session, row: AuthSession, reason: str) -> None:
    """Record why a session ended. Best effort: a failure to persist must never turn an
    expired session into a working one (the caller raises regardless)."""
    row.revoked_at = _now()
    row.revoked_reason = reason
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - the 401 still stands; the next request re-derives it
        db.rollback()
        log.warning("could not record the end of session (reason=%s)", reason)


def check(db: Session, sid: str | None, user: User) -> AuthSession:
    """The live session for this token, or SessionInvalid. Never extends anything."""
    if not sid:
        raise SessionInvalid(SESSION_EXPIRED, REASON_REAUTH)
    row = db.get(AuthSession, _hash(sid))
    if row is None or row.user_id != user.id:
        raise SessionInvalid(SESSION_EXPIRED, REASON_REAUTH)
    if row.revoked_at is not None:
        reason = row.revoked_reason or "revoked"
        raise SessionInvalid(SESSION_EXPIRED if reason in _EXPIRY_REASONS else SESSION_REVOKED, reason)
    now = _now()
    if now >= _aware(row.absolute_expires_at):
        _end(db, row, SESSION_END_ABSOLUTE)
        raise SessionInvalid(SESSION_EXPIRED, SESSION_END_ABSOLUTE)
    if now - _aware(row.last_activity_at) >= idle_timeout():
        _end(db, row, SESSION_END_IDLE)
        raise SessionInvalid(SESSION_EXPIRED, SESSION_END_IDLE)
    return row


def touch(db: Session, row: AuthSession) -> AuthSession:
    """Record genuine user activity on a session `check()` has just accepted.

    Throttled: within SESSION_ACTIVITY_MIN_INTERVAL_SECONDS of the last recorded activity
    nothing is written, so an active user costs at most one UPDATE per interval however many
    reports arrive. The absolute limit is untouched — activity can never outlast it.
    """
    now = _now()
    if now - _aware(row.last_activity_at) >= timedelta(seconds=settings.SESSION_ACTIVITY_MIN_INTERVAL_SECONDS):
        row.last_activity_at = now
        db.commit()
    return row


def revoke(db: Session, row: AuthSession, reason: str) -> None:
    """End one session (this device). Other sessions of the same user are untouched."""
    if row.revoked_at is None:
        row.revoked_at = _now()
        row.revoked_reason = reason
        db.commit()


def status_of(row: AuthSession) -> dict:
    """What a client needs to schedule its warning: deadlines on the SERVER clock, plus the
    server's own `now` so the client can correct for a skewed local clock."""
    now = _now()
    last = _aware(row.last_activity_at)
    absolute = _aware(row.absolute_expires_at)
    return {
        "idle_timeout_seconds": int(idle_timeout().total_seconds()),
        "last_activity_at": last,
        "idle_expires_at": min(last + idle_timeout(), absolute),
        "absolute_expires_at": absolute,
        "server_now": now,
        "remember": bool(row.remember),
    }


def http_error(exc: SessionInvalid) -> HTTPException:
    """The structured 401 the client branches on. Never a 5xx: an ended session is an
    authentication event, not a server fault."""
    message = _MESSAGES.get(exc.reason, _REVOKED_MESSAGE if exc.code == SESSION_REVOKED else _MESSAGES[REASON_REAUTH])
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail={"code": exc.code, "reason": exc.reason, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )
