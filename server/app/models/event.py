import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .organization import Organization
    from .user import User

# Lifecycle. Guarded transitions live in ONE place (crud.status_transition_error) and are
# reused by the API, the bulk endpoint and the broadcast socket actions — see that function
# for the rules. `paused` is a first-class event state (not only a BroadcastSession state)
# because the audience-facing status badge has to distinguish "held" from "ended".
EVENT_STATUSES = (
    "draft", "published", "scheduled", "live", "paused", "ended", "cancelled", "archived",
)

# `private` means organization-members-only (services.viewer.access_for). `invite_only`
# additionally admits anyone holding a valid EventAccessLink token — so "invite only" is
# expressed as a visibility value plus the link table below, not as a parallel flag nobody
# reads.
EVENT_VISIBILITY = ("public", "private", "unlisted", "invite_only")

# Event-team roles. One table serves all six (see EventAssignment). host/moderator/speaker
# are the three the realtime layer grants power to (services.moderation.resolve_ctx);
# producer/cohost/panelist are credited team roles that carry no broadcast authority of
# their own — kept explicit here rather than implied, so nothing silently escalates.
ASSIGNMENT_ROLES = ("host", "moderator", "speaker", "producer", "cohost", "panelist")

# Roles whose assignment confers realtime capability. resolve_ctx reads host/moderator;
# a producer/cohost/panelist assignment is metadata until it is also given one of these.
PRIVILEGED_ASSIGNMENT_ROLES = ("host", "moderator", "speaker")

# Stream quality targets — same vocabulary as services.broadcast.RESOLUTIONS so the value
# stored here can be handed straight to the encoder/egress without a mapping layer.
STREAM_QUALITIES = ("720p", "1080p", "2k", "4k")


class Event(Base):
    """An organization's event (management layer only — no streaming/LiveKit here).
    Belongs to exactly one org; org_id/created_by always come from the JWT. Duration is
    derived from start/end at read time, not stored."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    # Content
    title: Mapped[str | None] = mapped_column(String(200))          # optional as draft; required to publish
    slug: Mapped[str | None] = mapped_column(String(220), index=True)  # unique within org (crud-enforced)
    description: Mapped[str | None] = mapped_column(Text)
    short_description: Mapped[str | None] = mapped_column(String(300))
    banner_image: Mapped[str | None] = mapped_column(String(500))
    thumbnail: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(100))
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    language: Mapped[str | None] = mapped_column(String(40))
    timezone: Mapped[str | None] = mapped_column(String(60))
    # Free text ("New York, USA" / "Online") — the viewer landing page shows it beside the
    # date. Not a structured address: nothing geocodes it, so a single line is the honest shape.
    location: Mapped[str | None] = mapped_column(String(200))

    # Accessibility advertised to attendees BEFORE they join. Stored config only — the
    # caption/translation pipelines are not built, so these say "available", not "running".
    captions_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    translation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Schedule
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Access / registration
    visibility: Mapped[str] = mapped_column(String(20), default="public", nullable=False)
    registration_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    registration_limit: Mapped[int | None] = mapped_column(Integer)
    # Concurrent audience ceiling, distinct from registration_limit (how many may sign up).
    # Stored config: nothing enforces it yet — there is no admission control in the bus
    # (documented in the audit as C7), so this is the organizer's stated intent, not a cap.
    max_participants: Mapped[int | None] = mapped_column(Integer)
    # Optional shared passphrase for the attendee page. Hashed with the SAME bcrypt helpers
    # as user passwords (app.security) — a viewer gate is still a credential.
    access_password_hash: Mapped[str | None] = mapped_column(String(255))

    # Feature toggles — stored config the streaming/chat phases will read; behavior not built here.
    waiting_room_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chat_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    qa_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    polls_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raise_hand_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_screen_share: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_start_recording: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_end_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Whether the finished recording may be watched back. Separate from recording_enabled on
    # purpose: an event can be recorded for compliance and never published as a replay.
    replay_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Encoder/egress target for this event. The live session may override it per-broadcast
    # (BroadcastSession.settings.resolution); this is the event's default.
    stream_quality: Mapped[str] = mapped_column(String(16), default="1080p", nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # Blast-radius class: standard | high | unrepeatable. Decides which readiness gates are
    # mandatory (see services.ops.event_readiness) and what reaches the Command Center's
    # high-impact list. "unrepeatable" = cannot be re-run (memorial, results broadcast).
    impact: Mapped[str] = mapped_column(String(20), default="standard", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped["Organization"] = relationship()
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    assignments: Mapped[list["EventAssignment"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    access_links: Mapped[list["EventAccessLink"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class EventAssignment(Base):
    """Per-event role assignment. One table serves host/moderator/speaker — no parallel
    assignment tables. Org membership stays on User.org_id; this only records who fills
    which event role. Assignees must belong to the event's org (enforced in the router)."""

    __tablename__ = "event_assignments"
    __table_args__ = (UniqueConstraint("event_id", "user_id", "role", name="uq_event_user_role"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # see ASSIGNMENT_ROLES
    # The assignee's PRIVATE notes for this event — a speaker's talking points, visible to nobody
    # else. This row is exactly per-event-per-person, already org-scoped and already the thing
    # that says "you are on this event", so it is the right home; a user_preferences table would
    # be a second place to check the same permission.
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship(back_populates="assignments")
    user: Mapped["User"] = relationship()


class EventAccessLink(Base):
    """A shareable viewer link for one event.

    Deliberately the SAME security shape as models.invitation.Invitation, because it is the
    same problem: the raw token is handed to a human and NEVER stored — only its sha256 hash
    lives here. Revocation is a timestamp rather than a row delete so a revoked link stays
    auditable, and rotation reuses the row (mirroring crud.organization.resend_invitation)
    so "regenerate" doesn't orphan the label or the usage history.

    `org_id` is copied from the event, like every table in models/live.py, so a link can be
    org-isolated without a join.
    """

    __tablename__ = "event_access_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # Human label so an admin can tell two links apart ("Press", "Partners").
    label: Mapped[str | None] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # NULL = no expiry. A past value is refused at redemption time (crud.find_access_link).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Redemption count + last use: the only usage signal available without a registrations
    # table, and enough to answer "is this link circulating?".
    uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship(back_populates="access_links")
