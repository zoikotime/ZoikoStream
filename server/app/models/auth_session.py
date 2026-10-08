"""Server-side sign-in sessions: what makes an idle or abandoned session expire.

Until this existed, a sign-in was a stateless JWT carrying only `sub`, `role` and `exp`
(24 hours, or 30 days with "Remember me"), and nothing on the server knew whether its holder
was still using it. A browser closed in the morning and reopened in the afternoon walked
straight back into the dashboard; signing out only forgot the token in that one browser.

Each sign-in now creates one row here, and the JWT carries an opaque session id (`sid`). Every
authenticated request checks the row (services/auth_sessions.py):

  * revoked            -> signed out (this device), or ended by an administrator
  * absolute expiry    -> the session's maximum length, however active it has been
  * idle expiry        -> no genuine activity for SESSION_IDLE_TIMEOUT_MINUTES

`last_activity_at` moves ONLY when the client reports genuine user activity
(POST /api/auth/session/activity). Ordinary API traffic — dashboard polling, websocket
heartbeats, analytics — never extends a session, so an abandoned tab cannot keep one alive.

The id handed to the client is never stored: the primary key is its sha256, for the same
reason step-up references are hashed (models/stepup.py).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Why a session ended. "idle" and "absolute" are expiries (the user is told their session
# expired); the rest are revocations.
SESSION_END_IDLE = "idle"
SESSION_END_ABSOLUTE = "absolute"
SESSION_END_LOGOUT = "logout"
SESSION_END_REASONS = (SESSION_END_IDLE, SESSION_END_ABSOLUTE, SESSION_END_LOGOUT)


class AuthSession(Base):
    """One sign-in on one browser or device."""

    __tablename__ = "auth_sessions"

    # sha256 (hex) of the session id inside the JWT.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Sessions belong to their user and go with it: deleting a user deletes their sessions.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Server clock, moved only by reported user activity (never by background requests).
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Fixed at sign-in: the platform maximum, shortened by the organization's own policy.
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # "Remember me": whether the client keeps the credential across a browser restart. It
    # never lengthens the session — idle and absolute limits apply either way.
    remember: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(20))

    # Coarse device label for a future "your sessions" view; never used for authorization.
    user_agent: Mapped[str | None] = mapped_column(String(255))
