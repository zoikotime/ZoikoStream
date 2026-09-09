"""Event planning domain (ZST-EC-001 LVE-002, LVE-004, LVE-005).

Three lifecycles the product genuinely lacked, added as authoritative state rather than as
email templates. Each row is the record a message is *derived from*; nothing here is written
by a sender, and every notification marker lives beside the state it describes so a claim and
its evidence cannot drift apart.

  * **EventIntake** (LVE-002) - the customer-completed brief for one event. No intake concept
    existed anywhere in this codebase, so this is new rather than a rename of something.

  * **EventPlanningRequirement** (LVE-004) - one row per planning category per event. Three of
    the four categories are EVALUATED from committed state (see services/event_planning.py):
    contributors from EventAssignment/ContributorSession, audience access from the Event's own
    visibility/registration columns, recording and replay from `recording_enabled` and the
    ReplayEntitlement. The fourth, accessibility, has no backing domain at all and is
    therefore operator-attested only - it is never auto-completed, and its message says
    plainly that Zoiko Steam does not itself produce captions or translation.

  * **EventRehearsal** (LVE-005) - a scheduled rehearsal. `ServiceProfile.requires_full_rehearsal`
    and the `full_rehearsal` readiness check already recorded that a rehearsal was REQUIRED,
    and ContributorSession.rehearsal_complete recorded that one contributor had done theirs,
    but nothing recorded a rehearsal being scheduled, held, or needing a repeat.

Deliberately NOT modelled: a REMINDER_DUE state. A reminder is derived from `scheduled_at`
(LVE-005) or `due_at` (LVE-002) plus a durable marker column, because a reminder is a
communication event, not a business state the event is *in*.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# -- LVE-002 ----------------------------------------------------------------------------
INTAKE_STATES = ("open", "in_progress", "incomplete", "completed", "reopened")

# The sections a customer must supply. Each maps to real Event columns that
# services/event_planning.py validates - none is a decorative checklist item.
INTAKE_SECTIONS = ("schedule", "audience", "contributors", "delivery")
INTAKE_SECTION_LABELS = {
    "schedule": "Event date, time and timezone",
    "audience": "Audience size and access model",
    "contributors": "Speakers and contributors",
    "delivery": "Recording and delivery requirements",
}

# -- LVE-004 ----------------------------------------------------------------------------
PLANNING_CATEGORIES = ("contributors", "audience_access", "accessibility", "recording_replay")
PLANNING_CATEGORY_LABELS = {
    "contributors": "Contributors",
    "audience_access": "Audience access",
    "accessibility": "Accessibility",
    "recording_replay": "Recording and replay",
}
PLANNING_STATES = ("incomplete", "in_progress", "complete", "blocked", "not_applicable")
# States that mean nothing is outstanding, so no action-required message is owed.
PLANNING_SETTLED = ("complete", "not_applicable")

# -- LVE-005 ----------------------------------------------------------------------------
REHEARSAL_STATES = ("scheduled", "completed", "needs_repeat", "canceled")
# How far ahead of `scheduled_at` the single governed reminder goes out. One threshold, in one
# place, so "did we already remind them" is a marker column rather than a recomputation.
REHEARSAL_REMINDER_HOURS = 24
# How far ahead of `due_at` the single governed intake reminder goes out.
INTAKE_REMINDER_HOURS = 48


class _EventScoped(Base):
    """Shared identity + scoping, matching models/live.py's own convention: org_id is copied
    from the event so a query can be org-isolated without a join."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class EventIntake(_EventScoped):
    """The customer's event brief. One per event."""

    __tablename__ = "event_intakes"
    __table_args__ = (UniqueConstraint("event_id", name="uq_event_intake"),)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Section keys still outstanding, from validate(). Stored so the Incomplete message names
    # the same sections the validator actually failed, rather than a second opinion.
    outstanding_sections: Mapped[list | None] = mapped_column(JSON, default=list)
    # Customer-safe reason for a reopen. Operator-supplied and length-capped; internal notes
    # have no home here on purpose.
    reopen_reason: Mapped[str | None] = mapped_column(String(300))
    reopened_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    opened_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    incomplete_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventPlanningRequirement(_EventScoped):
    """One planning category for one event."""

    __tablename__ = "event_planning_requirements"
    __table_args__ = (UniqueConstraint("event_id", "category", name="uq_event_planning_category"),)

    category: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="incomplete", nullable=False)
    # The specific person who owes this action. LVE-004 requires the action to reach its
    # assigned owner and NOT every stakeholder, so this is the routing key.
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    blocking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What is actually outstanding, computed by evaluate() from committed state.
    outstanding: Mapped[list | None] = mapped_column(JSON, default=list)
    # Bumped on each reopen so a re-notification is possible exactly once per cycle without
    # nulling the marker and losing the fact that an earlier one was sent.
    notify_cycle: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    action_notified_cycle: Mapped[int | None] = mapped_column(Integer)


class EventRehearsal(_EventScoped):
    """A scheduled rehearsal for one event."""

    __tablename__ = "event_rehearsals"

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Copied from Event.timezone at scheduling time so the record stays readable even if the
    # event is later re-zoned - the rehearsal happened in the zone it was announced in.
    timezone_name: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="scheduled", nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Operator-recorded outcome. Customer-safe summary only.
    outcome: Mapped[str | None] = mapped_column(Text)
    validated_capabilities: Mapped[list | None] = mapped_column(JSON, default=list)
    outstanding_issues: Mapped[list | None] = mapped_column(JSON, default=list)
    repeat_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    repeat_reason: Mapped[str | None] = mapped_column(String(300))
    next_rehearsal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    scheduled_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    repeat_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventLifecycleEvent(Base):
    """Append-only ledger of LVE communication transitions.

    Same purpose as MediaAssetEvent for the MED families: the durable answer to "which
    transition did we actually announce, and when", independent of the marker columns above
    (which answer "may we send this one"). Kept in one table across LVE-001..005 because the
    question asked of it is always the same shape.
    """

    __tablename__ = "event_lifecycle_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    family: Mapped[str] = mapped_column(String(10), nullable=False)      # LVE-001 .. LVE-005
    transition: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 index=True)
