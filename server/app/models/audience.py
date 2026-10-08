"""Durable audience records for /organization/audience (services/audience.py).

The live presence ledger (services/bus.py) lives in Redis or process memory and is gone when a
room ends, so it cannot answer "who watched in the last 30 days". These two tables can, and
they hold only what the Audience page needs:

AudienceSession — one row per (event, identified viewer) who actually joined the live stream.
    Reconnects update the same row, so a viewer is counted once per event however many times
    they reconnect. "Identified" means a credential-backed identity: a signed-in viewer who is
    not staff of the organizing organization, a registration, or a per-browser access-link pass.
    An anonymous public viewer has only a disposable per-request identity and is not recorded
    here (they remain in the peak-concurrency figures).

    Device, browser, OS and client are coarse NORMALIZED classes derived from the user agent at
    join time. The user-agent string itself is never stored, nor any IP address.

JoinDenial — one row per refused attempt to watch (an invalid invitation, a private event, a
    full event, a removed viewer...). Event, normalized reason, time, and the registration if
    one was presented. Never a token, an invitation secret, an IP or a user agent.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

DEVICE_TYPES = ("desktop", "mobile", "tablet", "other")
BROWSERS = ("chrome", "safari", "firefox", "edge", "other")
OPERATING_SYSTEMS = ("windows", "macos", "android", "ios", "linux", "other")
CLIENT_TYPES = ("web", "mobile", "embedded", "other")

# Why a join was refused. Closed vocabulary; the Audience page labels each.
DENIAL_REASONS = (
    "invalid_invite",          # an invitation link that is unknown, expired or revoked
    "invalid_link",            # an access link that is unknown, expired, revoked or replaced
    "invite_used_elsewhere",   # a private invite already claimed on another device
    "not_invited",             # a private event, no valid invitation presented
    "event_full",              # registration limit reached
    "at_capacity",             # the live viewer ceiling was reached; the viewer was held
    "event_cancelled",
    "event_unavailable",       # an unpublished (draft) event's link
    "removed_by_host",         # a viewer the host removed tried to rejoin
)


class AudienceSession(Base):
    __tablename__ = "audience_sessions"
    __table_args__ = (UniqueConstraint("event_id", "viewer_key", name="uq_audience_session_viewer"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    # sha256 of the viewer's credential-backed identity: stable per viewer per event, so
    # reconnects collapse into one row, without storing the identity string itself.
    viewer_key: Mapped[str] = mapped_column(String(64), nullable=False)
    registration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_registrations.id", ondelete="SET NULL"), index=True)

    country_code: Mapped[str | None] = mapped_column(String(2))
    device_type: Mapped[str] = mapped_column(String(10), nullable=False, default="other")
    browser: Mapped[str] = mapped_column(String(10), nullable=False, default="other")
    os: Mapped[str] = mapped_column(String(10), nullable=False, default="other")
    client_type: Mapped[str] = mapped_column(String(10), nullable=False, default="web")

    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Rejoins after a real gap, not every poll or reconnect blip (services/audience.py).
    join_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Connected time on the live page, accumulated per live socket connection.
    watch_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class JoinDenial(Base):
    __tablename__ = "join_denials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    registration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_registrations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), index=True)
