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

# Lifecycle. Guarded transitions (crud.status_transition_error): publish needs a title,
# live only from published/scheduled/armed, ended only from live/degraded/replay_ready,
# archive not while active. rehearsal/ready_to_arm/armed/degraded/ending/processing/
# replay_ready/blocked are the v1.1 canonical-spec states (ZS-PRD-LE-01 Sec. 13) — optional:
# an event that skips straight from published/scheduled to live never touches them.
EVENT_STATUSES = (
    "draft", "published", "scheduled", "rehearsal", "ready_to_arm", "armed", "live",
    "degraded", "ending", "processing", "replay_ready", "ended", "cancelled", "archived", "blocked",
)
EVENT_VISIBILITY = ("public", "private", "unlisted")
ASSIGNMENT_ROLES = ("host", "moderator", "speaker")


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

    # Schedule
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Access / registration
    visibility: Mapped[str] = mapped_column(String(20), default="public", nullable=False)
    registration_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    registration_limit: Mapped[int | None] = mapped_column(Integer)
    # Organizer's own estimate of peak concurrent viewers (doc Sec. 8 "audience qualification
    # band") — distinct from registration_limit, which caps signups, not concurrency. Null
    # means no estimate given; the capacity envelope gate in crud/commercial.py treats that as
    # "assume default band", not as "unlimited".
    expected_audience: Mapped[int | None] = mapped_column(Integer)

    # Feature toggles — stored config the streaming/chat phases will read; behavior not built here.
    waiting_room_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chat_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    qa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    polls_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raise_hand_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_screen_share: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_start_recording: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_end_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # Blast-radius class: standard | high | unrepeatable. Decides which readiness gates are
    # mandatory (see services.ops.event_readiness) and what reaches the Command Center's
    # high-impact list. "unrepeatable" = cannot be re-run (memorial, results broadcast).
    impact: Mapped[str] = mapped_column(String(20), default="standard", nullable=False)

    # ── Commercial classification (ZST-LE-COM-001 Section 27) ──────────────────────────
    # Every event is billing-classified from creation, defaulting to "internal" so existing
    # rows and every event created before the commercial layer existed do NOT silently
    # become billable (doc S1: "Prevents old demos or pilots from accidentally becoming
    # billable"). risk_tier/service_profile_id govern readiness gates in crud/commercial.py;
    # they do not affect streaming/moderation behavior built elsewhere in this file.
    billing_classification: Mapped[str] = mapped_column(String(20), default="internal", nullable=False)
    billing_source: Mapped[str] = mapped_column(String(30), default="direct_zoikostream", nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(4), default="r0", nullable=False)
    service_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("service_profiles.id"))
    commercial_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commercial_accounts.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped["Organization"] = relationship()
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    assignments: Mapped[list["EventAssignment"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class EventAssignment(Base):
    """Per-event role assignment. One table serves host/moderator/speaker — no parallel
    assignment tables. Org membership stays on User.org_id; this only records who fills
    which event role. Assignees must belong to the event's org (enforced in the router)."""

    __tablename__ = "event_assignments"
    __table_args__ = (UniqueConstraint("event_id", "user_id", "role", name="uq_event_user_role"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # host | moderator | speaker
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship(back_populates="assignments")
    user: Mapped["User"] = relationship()


class EventAccessLink(Base):
    """A revocable, shareable link that admits someone outside the org to a PRIVATE event —
    the link-based counterpart to EventRegistration's email-invite grant. The raw token is
    generated once (create/rotate) and only its sha256 hash is stored; see
    crud.event._hash_link_token. `uses`/`last_used_at` are updated by crud.event.find_access_link,
    the same lookup watch_event uses to accept a `link` query param."""

    __tablename__ = "event_access_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship()


class EventRegistration(Base):
    """A registrant for an event — self-serve (registration_required, `invited_by` NULL) or
    host-initiated (`invited_by` set, see routers/events.py invite_viewers). Either way, no
    User row: registrants are identified only by name/email, and access to the watch page's
    stream is granted via a signed token (see security.py), not login. A row also doubles as
    the access grant that lets a specific outside person into a PRIVATE event — see
    watch_event's visibility gate.

    claim_token_hash/claimed_at: a PRIVATE event's personal invite token is a long-lived
    bearer credential (90 days, see create_registration_token) — anyone who gets the URL,
    forwarded or not, could use it. The first browser to present a valid token for a private
    event "claims" this row (crud.claim_registration); watch_event then requires every later
    request to present the matching claim cookie, so a forwarded link stops working for
    anyone but the device that claimed it first. Only the hash is stored, same pattern as
    EventAccessLink.token_hash."""

    __tablename__ = "event_registrations"
    __table_args__ = (UniqueConstraint("event_id", "email", name="uq_event_registration_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    claim_token_hash: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship()
