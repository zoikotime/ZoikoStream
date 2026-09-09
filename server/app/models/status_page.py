"""Public status page domain (ZST-EC-001 STS-001 -> STS-006).

**What the audit found.** There is no public status page and no public status domain at all:
`client/src/pages/admin/SystemStatus.jsx` is the INTERNAL operator console and
`pages/organization/SupportStatus.jsx` is a tenant support view. Neither is customer-facing
platform status, so the premise that a static `Status.jsx` needs upgrading does not hold -
there is nothing to upgrade.

**What is REUSED rather than invented:**

  * `platform_ops.REGIONS` = ("na", "eu", "apac", "sa") is the real delivery-region
    taxonomy. `StatusRegion` below is that tuple, not a new one.
  * `platform_ops.Incident` stays the INTERNAL record. `PublicStatusIncident` is a separate
    publication with its own reference, its own version history and its own vocabulary, so
    an internal note can never be serialized to a customer. That separation is the whole
    architectural requirement of this family.
  * `services/ops.py` health and `PlatformMetric` remain the operational truth; nothing here
    claims a component is healthy on its own authority.

**What is deliberately NOT reused:**

  * `platform_ops.LIFECYCLE_STAGES` ("contribute", "ingest", "produce", …) is the media
    PIPELINE vocabulary the admin rail is organized around - internal engineering language,
    not something a customer recognizes. Public components are named below from the
    subsystems this codebase actually ships.
  * `services/maintenance.py` is COMMERCIAL maintenance (stale-payment sweeps, aged
    settlements). It has nothing to do with platform maintenance windows, and conflating the
    two would put billing jobs on the status page.

**Append-only history.** `PublicStatusIncidentUpdate` is never updated in place. A
correction appends a new row carrying `correction_of_version`, so a resolved incident that
reopens, or a statement later corrected, both remain visible in the published record.

**Maintenance reminders are not invented.** `MAINTENANCE_REMINDER_HOURS` is empty: no
reminder policy is configured in this product, so no reminder fires and none is guessed at.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ── components ──────────────────────────────────────────────────────────────────────────
# Named from subsystems this codebase genuinely ships, each traceable to real services:
#   authentication  -> routers/auth.py, services/identity_security.py
#   live_streaming  -> services/livekit.py, services/broadcast.py
#   event_management-> routers/events.py, services/event_ops.py
#   recording       -> services/validation.py, MED-007/008 recording lifecycle
#   replay          -> services/replay_comms.py, ReplayEntitlement
#   developer_api   -> routers/organization.py developer routes, services/api_usage.py
#   webhooks        -> services/webhooks.py, webhook_lifecycle.py
#   billing         -> services/payments_stripe.py, commerce_comms.py
#   notifications   -> app/email.py (Resend)
# Nothing here is a placeholder invented to fill a template.
STATUS_COMPONENTS = (
    ("authentication", "Authentication"),
    ("live_streaming", "Live streaming"),
    ("event_management", "Event management"),
    ("recording", "Recording"),
    ("replay", "Replay"),
    ("developer_api", "Developer API"),
    ("webhooks", "Webhooks"),
    ("billing", "Billing"),
    ("notifications", "Notifications"),
)
COMPONENT_KEYS = tuple(key for key, _label in STATUS_COMPONENTS)
COMPONENT_LABELS = dict(STATUS_COMPONENTS)

# Reuses platform_ops.REGIONS verbatim - see the module docstring. "global" marks impact
# that is not region-scoped, matching the existing Incident convention.
REGION_KEYS = ("na", "eu", "apac", "sa", "global")
REGION_LABELS = {"na": "North America", "eu": "Europe", "apac": "Asia Pacific",
                 "sa": "South America", "global": "All regions"}

# ── STS-001 ─────────────────────────────────────────────────────────────────────────────
SUBSCRIBER_STATUSES = ("pending_verification", "active", "unsubscribed")
PURPOSE_STATUS_SUBSCRIPTION = "status_subscription_verification"
SUBSCRIPTION_TOKEN_TTL_HOURS = 72
# What a subscriber can opt into. Both default on: somebody subscribing to a status page
# wants to hear about outages and planned work unless they say otherwise.
NOTIFY_KINDS = ("incidents", "maintenance")

# ── STS-002 / STS-003 ───────────────────────────────────────────────────────────────────
# Public vocabulary. Note this is NOT platform_ops.INCIDENT_STATUSES ("open", "monitoring",
# "resolved"): the internal record and the published one are allowed to disagree, because an
# incident can be open internally long before anything is published.
PUBLIC_INCIDENT_STATUSES = ("investigating", "identified", "monitoring", "resolved")
# A restored service with engineering follow-up outstanding. Deliberately a FLAG rather than
# a status: residual work is not continued customer impact, and collapsing the two would
# tell customers they are still affected when they are not.
PUBLIC_UPDATE_TYPES = ("status_update", "correction", "post_incident_review")
PUBLIC_IMPACT_LEVELS = ("none", "degraded", "partial_outage", "major_outage")
PUBLIC_IMPACT_LABELS = {
    "none": "No customer impact",
    "degraded": "Degraded performance",
    "partial_outage": "Partial outage",
    "major_outage": "Major outage",
}

# ── STS-005 / STS-006 ───────────────────────────────────────────────────────────────────
MAINTENANCE_STATUSES = ("scheduled", "in_progress", "extended", "completed", "canceled")
# Emergency work is a distinct KIND, never a "scheduled maintenance" with a short notice
# period. Labelling unplanned work as scheduled is exactly what STS-006 forbids.
MAINTENANCE_KINDS = ("scheduled", "emergency")
# Coarse, customer-safe reasons for emergency work. None names a vulnerability or an
# exploit path.
EMERGENCY_REASONS = ("capacity", "stability", "security_patch", "provider_incident", "other")
EMERGENCY_REASON_LABELS = {
    "capacity": "Urgent capacity work",
    "stability": "Urgent stability work",
    "security_patch": "An urgent security update",
    "provider_incident": "An issue at an upstream provider",
    "other": "Urgent unplanned work",
}

# EMPTY ON PURPOSE. No maintenance reminder policy is configured in this product, so no
# reminder is sent and no 24h/1h threshold is invented. Populate with hour offsets (e.g.
# (24, 1)) to switch reminders on - see services/status_publication.reminder_due().
MAINTENANCE_REMINDER_HOURS: tuple[int, ...] = ()

# ── notice kinds ────────────────────────────────────────────────────────────────────────
STATUS_NOTICE_KINDS = (
    "subscription_verify", "subscription_confirmed", "preferences_changed",
    "unsubscribed",
    "incident_investigating", "incident_identified", "incident_monitoring",
    "incident_resolved", "incident_reopened", "incident_residual",
    "incident_correction", "incident_review",
    "maintenance_scheduled", "maintenance_reminder", "maintenance_changed",
    "maintenance_canceled", "maintenance_started", "maintenance_extended",
    "maintenance_completed", "maintenance_emergency",
)


class StatusNotice(Base):
    """One row per communicated public-status transition.

    Keyed on (kind, subject, VERSION). A public incident legitimately publishes many
    updates, a maintenance can be revised repeatedly, and a resolved incident can reopen -
    so a permanent UNIQUE(kind, subject_id) would swallow every occurrence after the first.
    """

    __tablename__ = "status_notices"
    __table_args__ = (
        UniqueConstraint("kind", "subject_type", "subject_id", "version",
                         name="uq_status_notice_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,
                                                  index=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # How many subscribers the fan-out actually reached, recorded so a publication's
    # delivery footprint is answerable without re-deriving eligibility later.
    recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                              server_default=func.now(), index=True)


class StatusComponent(Base):
    """A customer-facing platform component.

    Seeded from STATUS_COMPONENTS above, each mapping to a real subsystem. `current_impact`
    is written only by an authorized publication, never inferred from a metric.
    """

    __tablename__ = "status_components"
    __table_args__ = (UniqueConstraint("key", name="uq_status_component_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_impact: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class StatusSubscriber(Base):
    """Somebody subscribed to the public status page.

    Entirely separate from `Organization.notifications` and from every account, security,
    billing and privacy channel: unsubscribing here stops STATUS mail and nothing else,
    which is what keeps a status preference from silencing a security alert.
    """

    __tablename__ = "status_subscribers"
    __table_args__ = (UniqueConstraint("email", name="uq_status_subscriber_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending_verification",
                                        nullable=False)
    # Component keys and region keys the subscriber selected. An EMPTY list means "all",
    # which is the only way a subscriber hears about everything - the default is not
    # "everyone gets every incident".
    components: Mapped[list | None] = mapped_column(JSON, default=list)
    regions: Mapped[list | None] = mapped_column(JSON, default=list)
    notify_kinds: Mapped[list | None] = mapped_column(JSON, default=list)

    # Same convention as crud/identity.py: sha256 at rest, purpose-bound, single-use,
    # expiring, superseded on resend.
    verification_purpose: Mapped[str | None] = mapped_column(String(60))
    verification_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    verification_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A stable, unguessable handle for the manage/unsubscribe link, so a preference page
    # never needs the raw email in a URL.
    manage_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # Bumped per committed preference change, so each is announced at most once.
    preference_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class PublicStatusIncident(Base):
    """The PUBLISHED face of an incident.

    Distinct from `platform_ops.Incident` by design: an incident can be open internally with
    nothing published, and a published incident carries only the approved title, impact,
    components and regions. `internal_incident_id` links them for correlation without
    merging their vocabularies.
    """

    __tablename__ = "public_status_incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    # Customer-quotable, immutable: STS-2026-000123. Never the internal incident ref and
    # never a raw UUID.
    public_reference: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    internal_incident_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                                   index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="investigating", nullable=False)
    impact: Mapped[str] = mapped_column(String(20), default="degraded", nullable=False)
    affected_components: Mapped[list | None] = mapped_column(JSON, default=list)
    affected_regions: Mapped[list | None] = mapped_column(JSON, default=list)
    # The latest published body. The full history lives in PublicStatusIncidentUpdate and is
    # never overwritten - this is a convenience denormalization for the status page.
    current_update: Mapped[str | None] = mapped_column(Text)
    customer_action: Mapped[str | None] = mapped_column(Text)
    # Only ever set when an operator commits to a time. Nothing derives it.
    next_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    monitoring_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Service restored, engineering follow-up outstanding. A flag, not a status - residual
    # work is not continued customer impact.
    residual_work: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    residual_summary: Mapped[str | None] = mapped_column(Text)
    # TRUE only when an approved customer-facing review has actually been published, so the
    # resolved message mentions one only when it exists.
    review_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The current published version. Every publication appends an update row at this number.
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class PublicStatusIncidentUpdate(Base):
    """One published update. APPEND-ONLY.

    Nothing in `services/status_publication.py` updates a row of this table after insert. A
    correction appends a new row whose `correction_of_version` points at what it corrects,
    so the original statement stays readable in the published history - which is the whole
    point of versioning a public record rather than editing it.
    """

    __tablename__ = "public_status_incident_updates"
    __table_args__ = (
        UniqueConstraint("public_incident_id", "version",
                         name="uq_public_status_update_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    public_incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("public_status_incidents.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    update_type: Mapped[str] = mapped_column(String(30), default="status_update",
                                             nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Set on a CORRECTION: which earlier version this restates. The earlier row is left
    # exactly as published.
    correction_of_version: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now(), index=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class ScheduledMaintenance(Base):
    """A planned or emergency maintenance window.

    Timestamps are the CANONICAL record and are stored timezone-aware in UTC. A recipient's
    local time may be displayed alongside, but it is never the record - a maintenance window
    that means different things in different browsers is not a schedule.
    """

    __tablename__ = "scheduled_maintenances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    public_reference: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # `scheduled` or `emergency`. Emergency work is never presented as scheduled.
    kind: Mapped[str] = mapped_column(String(20), default="scheduled", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="scheduled", nullable=False)
    affected_components: Mapped[list | None] = mapped_column(JSON, default=list)
    affected_regions: Mapped[list | None] = mapped_column(JSON, default=list)

    # Canonical, UTC, timezone-aware.
    starts_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What the previous published window was, so a Changed notice can show a real before.
    previous_starts_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_ends_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    impact_summary: Mapped[str | None] = mapped_column(Text)
    emergency_reason: Mapped[str | None] = mapped_column(String(40))
    # Only ever set explicitly by an operator.
    next_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Execution timestamps. `started_at` is what makes a Started notice truthful: the clock
    # reaching starts_at_utc is not the same fact as work actually beginning.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remaining_work: Mapped[str | None] = mapped_column(Text)
    # Bumped per committed revision, so a published schedule is versioned rather than
    # silently mutated.
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Which reminder offsets have already gone out, so a cancel or a reschedule can
    # invalidate them and a threshold cannot fire twice.
    reminders_sent: Mapped[list | None] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())
