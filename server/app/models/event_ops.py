"""Event operations domain (ZST-EC-001 LVE-006 -> LVE-012).

What is NEW here versus what is REUSED matters, because several of these families already had
authoritative state and only lacked communication:

  REUSED, not reimplemented:
    * readiness      - crud.commercial.evaluate_readiness is THE engine. EventReadinessState
                       below stores only the last ANNOUNCED verdict so a transition can be
                       detected; it never recomputes readiness. Duplicating those rules in the
                       communication layer is exactly what LVE-006 forbids.
    * incidents      - models.commercial.EventIncident is the existing service-failure record.
                       LVE-010's operational state machine is added to it rather than beside it.
    * reports        - models.live.EventReport already generates a real post-event snapshot.
                       LVE-012 adds a delivery/approval lifecycle around it.
    * retention      - MED-011's columns on LiveRecording stay the single retention authority.
                       LVE-012 reads them; it does not keep a second copy.

  GENUINELY NEW:
    * EventScheduleChange - every start_time/timezone mutation now leaves a durable record.
                       models.commercial.EventReschedule exists but is a COMMERCIAL workflow
                       (needs an order, releases capacity, refuses while delivering), so it
                       cannot cover an ordinary edit to a non-commercial event - which is
                       precisely the silent mutation LVE-008 calls SEV-1.
    * EventBrief     - no event-day brief artifact existed in any form.

Deliberately NOT modelled:
    * an audience-access OPENING time. No such column exists anywhere in the product, so
      LVE-009's "Audience access opens soon" has no authoritative trigger and is reported
      unsupported rather than fired off a guess at start_time minus something.
    * a MONITORING event state. EVENT_STATUSES has no such member; MED-005 already owns
      session health. The variant is reported absent rather than invented.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# -- LVE-006 ----------------------------------------------------------------------------
# The communication vocabulary, mapped from the readiness engine's own three verdicts.
# NORMAL_PASS -> passed, EXCEPTION_APPROVED -> conditional (it proceeds, but only because an
# approved exception is carrying it), BLOCKED -> blocked. "regressed" is not a verdict: it is
# the TRANSITION from a settled state back to an unsettled one.
READINESS_VARIANTS = ("passed", "conditional", "blocked", "regressed")
READINESS_SETTLED = ("passed", "conditional")
VERDICT_TO_VARIANT = {
    "NORMAL_PASS": "passed",
    "EXCEPTION_APPROVED": "conditional",
    "BLOCKED": "blocked",
}

# -- LVE-007 ----------------------------------------------------------------------------
BRIEF_STATES = ("draft", "approved", "superseded")

# -- LVE-009 ----------------------------------------------------------------------------
# Only states EVENT_STATUSES actually contains. "monitoring" is absent on purpose.
ACTIVATION_VARIANTS = ("armed", "live")

# -- LVE-010 ----------------------------------------------------------------------------
INCIDENT_OPERATIONAL_STATES = ("delayed", "temporary_hold", "resumed", "canceled")
# Coarse, customer-safe categories. Nothing here names a provider, a subsystem, a person or a
# security finding - the whole point is that the customer learns the shape of the problem
# without the investigation leaking.
INCIDENT_REASON_CATEGORIES = ("technical", "operational", "customer_requested",
                              "safety_security", "other")
INCIDENT_REASON_LABELS = {
    "technical": "Technical",
    "operational": "Operational",
    "customer_requested": "Customer-requested",
    "safety_security": "Safety and security",
    "other": "Other",
}

# -- LVE-011 ----------------------------------------------------------------------------
# The boundary this family exists to preserve. Each step is a DIFFERENT fact.
COMPLETION_VARIANTS = ("ended", "replay_processing", "replay_approval_required",
                       "replay_published", "replay_unavailable")

# -- LVE-012 ----------------------------------------------------------------------------
REPORT_STATES = ("processing", "ready", "failed", "superseded")


class _EventScoped(Base):
    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False,
                                                index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class EventReadinessState(_EventScoped):
    """The last readiness verdict that was ANNOUNCED for one event.

    This is a communication marker, not a readiness authority. It holds no rules and computes
    nothing: services/event_ops.py asks crud.commercial.evaluate_readiness for the verdict and
    uses this row only to answer "has this changed since we last told anyone". Polling the same
    verdict repeatedly therefore sends nothing, which is what LVE-006's dedup requires.
    """

    __tablename__ = "event_readiness_states"
    __table_args__ = (UniqueConstraint("event_id", name="uq_event_readiness_state"),)

    variant: Mapped[str | None] = mapped_column(String(20))
    verdict: Mapped[str | None] = mapped_column(String(30))
    # The blocking reasons as the engine returned them, so the message names the same
    # conditions the gate actually enforced.
    blocking_reasons: Mapped[list | None] = mapped_column(JSON, default=list)
    exceptions_applied: Mapped[list | None] = mapped_column(JSON, default=list)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a settled verdict later becomes unsettled. Kept distinct from `variant` so the
    # Regressed message can state what it fell FROM.
    regressed_from: Mapped[str | None] = mapped_column(String(20))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventScheduleChange(_EventScoped):
    """One committed change to an event's scheduled window or timezone.

    Written by the ONE helper every mutation path funnels through, so a schedule can no longer
    move silently. models.commercial.EventReschedule remains the commercial workflow (capacity
    release, order version, lead-time band) and writes one of these too, so both an ordinary
    edit and a contractual reschedule leave the same durable trail.
    """

    __tablename__ = "event_schedule_changes"

    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    previous_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_timezone: Mapped[str | None] = mapped_column(String(60))
    new_timezone: Mapped[str | None] = mapped_column(String(60))
    reason_category: Mapped[str | None] = mapped_column(String(40))
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What the change actually moved, so downstream re-evaluation and the message agree.
    date_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Outcome of the downstream sweep: which dependent domains were re-evaluated and what
    # happened to each. Recorded so the message reports real consequences rather than a
    # generic "please review everything".
    downstream: Mapped[dict | None] = mapped_column(JSON, default=dict)
    # Governed independently, per LVE-008: each recipient class has its own marker, because
    # "the team was told" must never imply "the audience was told".
    owner_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contributors_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audience_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why the audience was or was not told. Kept so the decision is auditable rather than
    # implicit in whether a marker happens to be null.
    audience_decision: Mapped[str | None] = mapped_column(String(60))


class EventBrief(_EventScoped):
    """A versioned event-day brief. Only an APPROVED version is ever communicated."""

    __tablename__ = "event_briefs"

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # The rendered brief content, built by services/event_closeout.py from committed event
    # state at APPROVAL time - frozen, so a later event edit cannot silently rewrite a brief
    # somebody already acted on.
    content: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventActivationState(_EventScoped):
    """The last activation transition ANNOUNCED for one event.

    Same shape and same reasoning as EventReadinessState: Event.status is the authority, this
    only records what has already been said so a repeated transition is not re-announced.
    """

    __tablename__ = "event_activation_states"
    __table_args__ = (UniqueConstraint("event_id", name="uq_event_activation_state"),)

    variant: Mapped[str | None] = mapped_column(String(20))
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    armed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    live_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventCompletionState(_EventScoped):
    """Which completion/replay transitions have been announced for one event.

    One marker per variant rather than a single "last variant" column, because these are not a
    linear sequence: a replay can go processing -> approval required -> published, or straight
    to unavailable, and each is announced at most once.
    """

    __tablename__ = "event_completion_states"
    __table_args__ = (UniqueConstraint("event_id", name="uq_event_completion_state"),)

    ended_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unavailable_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The last replay publish_state announced, so a later change can be detected without
    # re-deriving it from the entitlement.
    last_replay_state: Mapped[str | None] = mapped_column(String(30))


class PostEventReport(_EventScoped):
    """Delivery lifecycle around models.live.EventReport.

    EventReport already holds the generated snapshot; this row holds the STATE of preparing
    and releasing it, which is what LVE-012 communicates about. Keeping them separate avoids
    changing the meaning of an existing artifact that other code already reads.
    """

    __tablename__ = "post_event_reports"

    report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    report_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="processing", nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_category: Mapped[str | None] = mapped_column(String(60))
    # FALSE, always, until a published threshold policy exists. Recorded rather than assumed
    # so the absence is visible in the data instead of only in a comment - see
    # services/event_closeout.PRIVACY_POLICY_AVAILABLE.
    privacy_suppression_applied: Mapped[bool] = mapped_column(Boolean, default=False,
                                                              nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ready_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
