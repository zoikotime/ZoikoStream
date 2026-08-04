"""Live-event moderation tables: chat, Q&A, polls, announcements, activity.

Scoped like every other table: `org_id` is copied from the event so a query can be
org-isolated without a join. Presence/participants are deliberately NOT here — they
are ephemeral and live in Redis (services/bus.py).

Moderator actions are audited into the existing `audit_logs` table (models/audit_log.py)
rather than a parallel moderator_actions table — it already carries actor, action,
target, org and a meta blob.

ponytail: pinned/deleted messages are COLUMNS here, not their own tables — "persist
pinned messages / deleted messages" is a flag on the row, and a soft delete keeps the
chat log intact for compliance export.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

MESSAGE_STATUSES = ("approved", "pending", "deleted")
QUESTION_STATUSES = ("pending", "approved", "answered", "dismissed")
POLL_STATUSES = ("draft", "scheduled", "live", "closed")
ANNOUNCEMENT_PRIORITIES = ("normal", "important", "urgent")
# Feed kinds — keep in sync with ACT_ICON in components/moderation/ModeratorSidebar.jsx.
ACTIVITY_KINDS = ("join", "leave", "chat", "qa", "poll", "mod", "system", "role", "recording")

# Retention actions a policy may take, weakest first. Ordered because a sweep applies them in
# this order and "archive at 90 days, delete at 365" must never delete before it archives.
RETENTION_ACTIONS = ("archive", "cold", "delete")


class _EventScoped(Base):
    """Shared identity + scoping columns for every live table."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class LiveMessage(_EventScoped):
    """One chat message. `flags` holds automatic detections (profanity | spam | link |
    duplicate) so moderators can filter without re-scanning text."""

    __tablename__ = "live_messages"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    author_name: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="approved", nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Highlight is NOT pin. Pin is exclusive (one banner above the chat, the whole room sees
    # it); highlight marks any number of messages as worth reading out, so a moderator can
    # queue several for the host without the banner flipping each time.
    highlighted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    flags: Mapped[list | None] = mapped_column(JSON, default=list)
    reactions: Mapped[dict | None] = mapped_column(JSON, default=dict)   # emoji -> count
    reply_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)                       # moderator-only note
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class LiveQuestion(_EventScoped):
    __tablename__ = "live_questions"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    author_name: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    votes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assigned_name: Mapped[str | None] = mapped_column(String(120))       # speaker it's routed to
    flags: Mapped[list | None] = mapped_column(JSON, default=list)
    # The speaker's written answer, and who gave it. Stored rather than derived from chat: the
    # Q&A export and the "questions answered" figure both need to point at a real answer, and
    # "status == answered" alone cannot say what was said or by whom.
    answer_text: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LivePoll(_EventScoped):
    """`options` is [{label, votes}] — the exact shape the console already renders, so
    no per-option table and no mapping layer. `closes_at` drives the countdown; the
    scheduler in routers/live.py flips scheduled -> live and live -> closed."""

    __tablename__ = "live_polls"

    question: Mapped[str] = mapped_column(String(300), nullable=False)
    options: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class LiveAnnouncement(_EventScoped):
    """`delivered_to` is the real connection count at send time — not an estimate."""

    __tablename__ = "live_announcements"

    text: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_to: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class BroadcastSession(_EventScoped):
    """One row per go-live attempt — the durable record of what the host did.

    `settings` holds the live control state (media targets + chat/Q&A/poll toggles). The
    HOT copy lives in the bus (services/bus.state_*) because chat checks it per message;
    this row is what a cold worker rehydrates from and what an audit reads afterwards.

    A pause does not end the session: ended_at is set once, so "how long was this
    broadcast" stays answerable and a resume doesn't create a second row.
    """

    __tablename__ = "broadcast_sessions"

    status: Mapped[str] = mapped_column(String(16), default="preview", nullable=False)
    settings: Mapped[dict | None] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Total time spent paused, so elapsed-live-time excludes it.
    paused_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    peak_viewers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ended_reason: Mapped[str | None] = mapped_column(String(40))   # host | emergency_stop | room_finished
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


BROADCAST_STATUSES = ("preview", "live", "paused", "ended")
RECORDING_STATUSES = ("idle", "recording", "paused", "stopped", "failed")
# Who may open a recording. Mirrors the event visibility vocabulary on purpose — an organizer
# already understands it, and the checks read the same way (see services/media.can_view).
MEDIA_VISIBILITY = ("private", "organization", "event_audience", "public")
# Library grouping. Free-text would fragment into "Webinar"/"webinar"/"Web-inar" across one org,
# and every filter chip in the UI is built from this list.
MEDIA_CATEGORIES = ("Webinar", "Conference", "Training", "Town Hall", "Product", "Internal", "Other")


class LiveRecording(_EventScoped):
    """One row per start→stop recording cycle (a pause/resume stays on the same row, with
    the paused time accumulated). Gives the host console its timer, its recording log and
    its download/backup links from real data rather than a derived guess.

    This row is ALSO the media-library item — there is no separate `media_assets` table. A
    recording and its library entry are the same object with the same lifecycle, and splitting
    them would mean two rows to keep in step for every rename, move, archive and delete.
    """

    __tablename__ = "live_recordings"

    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="recording", nullable=False)
    quality: Mapped[str | None] = mapped_column(String(16))          # 720p | 1080p | 2k | 4k
    egress_id: Mapped[str | None] = mapped_column(String(80))        # LiveKit egress handle
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # BigInteger, not Integer: Postgres INTEGER caps at 2,147,483,647 ≈ 2 GB, which a 1080p
    # multi-hour capture passes comfortably. Now that the egress_ended webhook writes the real
    # size (services/broadcast.record_egress_result) an overflow would be a live 500.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_url: Mapped[str | None] = mapped_column(String(500))
    auto_upload: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # False when LiveKit egress wasn't reachable/configured — the console shows the
    # difference instead of implying a file exists.
    enforced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # ── media library ─────────────────────────────────────────────────────────
    # The bucket key. DISTINCT from file_url, which is whatever LiveKit reported (an s3:// URI, a
    # container path, or nothing at all). Signed URLs are minted from this and only this, so a
    # recording whose key we never assigned can never be handed out as a download.
    storage_key: Mapped[str | None] = mapped_column(String(500))
    # NULL title = "show the event's title". Storing a copy at creation would freeze the name of
    # every past recording the first time an organizer renamed the event.
    title: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    category: Mapped[str | None] = mapped_column(String(40))
    visibility: Mapped[str] = mapped_column(String(20), default="organization", nullable=False)
    # Real duration, from the egress result (FileInfo.duration, nanoseconds) — not
    # stopped_at - started_at, which counts the paused stretches the file does not contain.
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Archive is NOT delete: an archived recording keeps its bytes and its links, drops out of the
    # default library view, and is what a retention policy reaches for first.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Recycle bin. A purge (see routers/media) is what actually removes the object.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Cold storage is a STORAGE CLASS, so it is a fact about the object, not a UI filter. Set when
    # a retention rule transitions it; a cold object may need a restore before it can be fetched.
    storage_class: Mapped[str | None] = mapped_column(String(20))

    # Failed-capture recovery. `retry_of` points at the recording this attempt replaces, so the
    # chain stays inspectable instead of a counter that loses what was retried.
    retry_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Per-recording download override. NULL = inherit the org policy (organizations.media);
    # a value here wins, so one sensitive recording can be locked down without changing the org.
    download_policy: Mapped[str | None] = mapped_column(String(20))   # allowed | disabled | password
    download_password_hash: Mapped[str | None] = mapped_column(String(200))
    download_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watermark: Mapped[bool | None] = mapped_column(Boolean)

    # Transcript segments: [{start_ms, end_ms, speaker, text}]. JSON on the row rather than a
    # segments table — it is read whole, for one recording, and never joined or aggregated.
    transcript: Mapped[dict | None] = mapped_column(JSON)
    # Derived insights (summary/chapters/keywords/moments/…) plus the provenance of each field.
    # See services/media.py: some of these are computed from real event records, and the ones that
    # need a model are marked unavailable rather than invented.
    insights: Mapped[dict | None] = mapped_column(JSON)


class MediaFolder(Base):
    """Library folders, org-scoped, one level of nesting per row via `parent_id`.

    A table rather than a path string on the recording: renaming a folder must not require
    rewriting every row beneath it, and "list the folders" must not mean scanning every recording.
    Deleting a folder never deletes recordings — they fall back to the library root.
    """

    __tablename__ = "media_folders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    colour: Mapped[str | None] = mapped_column(String(20))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaMark(Base):
    """One person's bookmark or note at a timestamp in one recording.

    Bookmarks and notes share a table because they are the same record — a position plus optional
    text. `note` empty means "bookmark"; `note` set means "note". Two tables would double the
    write paths and the permission checks to render one timeline.
    """

    __tablename__ = "media_marks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("live_recordings.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    # Shared marks are visible to anyone who can view the recording; private ones are not. Default
    # private, because a note is a personal working record until its author decides otherwise.
    shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalyticsSnapshot(_EventScoped):
    """Periodic sample of the live counters — this IS the retention graph. Written by the
    sampler in services/broadcast.py, one row per interval per live event.
    ponytail: a plain row per sample, not a timeseries DB. At one row / 15s an 8-hour
    event is ~1900 rows; revisit only if events get much longer or much more frequent."""

    __tablename__ = "analytics_snapshots"

    viewers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    participants: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    on_stage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    messages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    questions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reactions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hands: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Lobby depth. Sampled here so a DASHBOARD can show "3 people waiting" across many events
    # without opening a socket (and a Redis round trip) per event — presence stays the live
    # truth inside the console, this is the 15s-old figure a listing can afford.
    waiting: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# Presentation assets. `slides` means every page is addressable and presenter mode can drive it;
# `file` means it was accepted and can be downloaded but NOT rendered as slides here.
ASSET_KINDS = ("slides", "image", "file")
ASSET_STATUSES = ("pending", "approved", "rejected")
# 25 MB. A hard ceiling rather than a guideline: the bytes live in the row (see below), so an
# unbounded upload is a way to fill a tenant's database.
MAX_ASSET_BYTES = 25 * 1024 * 1024

# What can actually be PRESENTED versus merely stored. PPT/PPTX is accepted because speakers have
# them, but rendering one needs a converter (LibreOffice/unoconv) that is not installed in this
# stack — so it is stored as `file` and the console says to export a PDF. Inventing a slide count
# for a deck nobody can render would be worse than saying no.
ASSET_CONTENT_TYPES = {
    "application/pdf": "slides",
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/gif": "image",
    "image/svg+xml": "image",
    "application/vnd.ms-powerpoint": "file",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "file",
}


class SpeakerAsset(_EventScoped):
    """One presentation file a speaker uploaded for one event.

    The bytes live in this row. ponytail: object storage is the right home for a 25 MB deck, and
    this platform has no bucket configured (the same gap that stops recordings producing a durable
    file). A capped bytea column is honest, tenant-isolated by the same org_id every other table
    uses, and needs no infrastructure that does not exist — swap `data` for a `storage_key` and a
    signed URL the day a bucket appears, and nothing above this line changes.

    `status` exists because "Presentation Approved" is a real workflow step: a host or moderator
    vets what is going on the main screen before it gets there.
    """

    __tablename__ = "speaker_assets"

    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    uploader_name: Mapped[str | None] = mapped_column(String(120))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)        # see ASSET_KINDS
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Page count for a PDF, 1 for an image, NULL when unknown. NULL is a real answer — see
    # crud.speaker.count_pdf_pages for what it can and cannot determine.
    pages: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # Shared with the AUDIENCE as a download. Separate from `status`: approving a deck lets it go
    # on the main screen, sharing it lets 10,000 people fetch the file. A speaker's working draft
    # being presentable must not also make it public.
    shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LiveActivity(_EventScoped):
    """Append-only event timeline, written by ONE helper (services/moderation.record).
    Exists so the feed survives a reconnect and stays searchable/exportable without
    merging five tables at read time. Moderator actions land here AND in audit_logs —
    different readers: this one is the in-console timeline, that one is compliance."""

    __tablename__ = "live_activity"

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    actor_name: Mapped[str | None] = mapped_column(String(120))
    meta: Mapped[dict | None] = mapped_column(JSON)
