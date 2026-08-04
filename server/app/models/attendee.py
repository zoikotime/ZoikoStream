"""The attendee's relationship with one event.

ONE table, deliberately. Registration, bookmarking, reminders, watch history and saved
questions are all "this person, that event" — five tables would mean five permission checks
and five joins to paint one dashboard row. Scoped like every other live table: `org_id` is
copied from the event so a query is org-isolated without a join.

Nothing here is a preference that spans events (language, accessibility, favourite speakers) —
those live on `users.preferences`, because they belong to the person, not to a pairing.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# `registered` is a live sign-up; `cancelled` is kept rather than deleted so a re-registration
# reuses the row and the history stays readable. `attended` is set by the first join, and is what
# separates "signed up" from "actually turned up" — the number an organizer asks for.
REGISTRATION_STATUSES = ("registered", "cancelled", "attended")


class EventRegistration(Base):
    """One attendee's registration for, and history with, one event.

    A row exists as soon as the attendee does ANYTHING with the event — registers, bookmarks it,
    or simply watches. `status` is what registration enforcement reads; the rest is history and
    personalisation that must not be lost when somebody cancels and signs up again.
    """

    __tablename__ = "event_registrations"
    # One row per person per event. The partial-index trick used for invitations is not needed
    # here: a cancelled registration is the same row, re-activated.
    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_registration_event_user"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    # Copied from the event, NOT from the attendee: a public event's audience is legitimately
    # outside the organizing org, and this column is what scopes an organizer's reporting.
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # Who signed up, as they were AT THE TIME. Snapshot columns, so an organizer's registrant list
    # still reads correctly after a member is renamed or soft-deleted. They predate this module —
    # the table was scaffolded (and left empty and unreferenced) for the dummy public form on
    # /e/:id — and they were NOT NULL there; relaxed to nullable because a registration is now
    # keyed on user_id and the names are derived from it.
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))

    status: Mapped[str] = mapped_column(String(16), default="registered", nullable=False)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Personalisation.
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # When to remind them. NULL = no reminder. Stored as an absolute time rather than an offset
    # so a rescheduled event does not silently move somebody's alarm.
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Question ids this attendee saved. A JSON list rather than a join table: it is a personal
    # reading aid on a list that is already capped at HISTORY_LIMIT, and it is only ever read
    # for one attendee at a time.
    question_bookmarks: Mapped[list | None] = mapped_column(JSON, default=list)

    # Watch history. `watch_seconds` is accumulated from heartbeats, so it measures time actually
    # spent watching rather than the span between opening and closing the tab.
    first_joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watch_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    join_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
