"""Viewer playback quality (QoE) for /organization/playback (services/playback_qoe.py).

PlaybackSession — one row per PLAYBACK SESSION: one viewer page playing one event in one mode
    (live, over LiveKit/WebRTC, or replay, a progressive file in an HTML5 <video>). Written by
    the viewer's own player through POST /api/playback/qoe, never by the server guessing from
    event status.

    The row is a running SUMMARY, not an event log. The player sends cumulative totals, and
    each beacon replaces the previous totals only when its sequence number is higher, so a
    retried, duplicated or reordered beacon cannot inflate anything (services/playback_qoe
    ingest). A reconnect inside the page continues the same session; a page reload is a new one.

    The id is minted by the server inside the signed token GET /events/{id}/watch hands an
    AUDIENCE member (never staff of the organizing organization, never a contributor's monitor
    feed). The event, and through it the organization, come from that token and the events
    table — a beacon cannot name either.

    Privacy: no IP address, user agent, email, access token, invitation secret or signed URL is
    stored, and failures are a closed vocabulary of reason codes, never error text. viewer_key
    is the same sha256 of a credential-backed identity that audience_sessions.viewer_key holds
    (so the two can be joined), and is NULL for an anonymous viewer.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

PLAYBACK_MODES = ("live", "replay")

# JSONB on PostgreSQL (the ingest merges reason sets with `||` and the aggregate reads them with
# jsonb_object_keys), generic JSON elsewhere. Several test suites build every table on SQLite,
# which has no JSONB type and no `::jsonb` cast — a Postgres-only column type or default here
# made all of them fail at table creation.
FAILURE_REASONS_TYPE = JSON().with_variant(JSONB(), "postgresql")

# Why playback failed, as the player observed it. Closed vocabulary: anything else is refused
# at ingest. Each is produced by a real client/player error (client/src/data/playbackQoe.js).
PLAYBACK_FAILURE_REASONS = (
    "media_connection_failed",    # could not connect to the live media server
    "track_subscription_failed",  # connected, but the broadcaster's track could not be received
    "playback_start_failed",      # the browser refused to start playback for another reason
    "decoder_error",              # the browser could not decode the media
    "network_error",              # the network failed while fetching or reaching media
    "autoplay_blocked",           # the browser required a click before playing
    "stream_unavailable",         # the stream or file was gone or unplayable (e.g. expired link)
    "access_denied",              # the media server refused this viewer's credential
    "timeout",                    # connecting to the live media server timed out
    "unknown",
)


class PlaybackSession(Base):
    __tablename__ = "playback_sessions"
    __table_args__ = (
        # The two read paths: one organization over a window, one event over a window.
        Index("ix_playback_sessions_org_created", "org_id", "created_at"),
        Index("ix_playback_sessions_event_created", "event_id", "created_at"),
        # registration_id is ON DELETE SET NULL: without this, every deleted registration
        # (an organization's hard delete cascades to all of them) scans this table.
        Index("ix_playback_sessions_registration_id", "registration_id"),
        CheckConstraint("mode IN ('live', 'replay')", name="ck_playback_sessions_mode"),
    )

    # Minted by the server inside the signed QoE token; never chosen by the browser.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    viewer_key: Mapped[str | None] = mapped_column(String(64))
    registration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_registrations.id", ondelete="SET NULL"))

    # Highest beacon sequence number applied. A beacon at or below it changes nothing.
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # When the player first rendered media. NULL = this session never started playing.
    playback_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Time from the play attempt to the first rendered media, excluding paused/hidden time.
    startup_ms: Mapped[int | None] = mapped_column(Integer)
    # Watch time after the start: playing plus stalled, while the viewer wanted playback and
    # the player was visible. Excludes pauses, hidden tabs and broadcaster interruptions.
    playback_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rebuffer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rebuffer_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # {reason: true} for each PLAYBACK_FAILURE_REASONS value this session hit (a set, so a
    # retry loop failing the same way a hundred times is still one failure of that kind).
    # The server default is a plain '{}' literal: PostgreSQL coerces it to jsonb, SQLite stores
    # it as JSON text. (`'{}'::jsonb` is PostgreSQL-only syntax.)
    failure_reasons: Mapped[dict] = mapped_column(
        FAILURE_REASONS_TYPE, nullable=False, default=dict, server_default=text("'{}'"))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
