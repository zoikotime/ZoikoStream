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
# live only from published/scheduled, ended only from live, archive not while live.
EVENT_STATUSES = ("draft", "published", "scheduled", "live", "ended", "cancelled", "archived")
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

    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

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
