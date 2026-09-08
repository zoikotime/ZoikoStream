"""Platform operations domain — the tables behind the Super Admin Command Center.

One module for the whole ops domain because these five tables are only ever read
together (services/ops.py builds one payload from all of them) and none of them is
meaningful on its own.

Design notes:
  * Incident is the ONLY source of "was this stage/region degraded", so lifecycle
    availability is computed from real recorded impact windows, never a constant.
  * GovernanceRecord is one table with a `kind` discriminator rather than five
    near-identical tables (legal holds, entitlement overrides, break-glass grants,
    single-path overrides, usage exports all carry the same fields).
  * PlatformMetric is a plain sample store. A writer (services.ops.sample_metrics,
    or a future QoE beacon) appends; the console reads latest + series. Metrics with
    no writer yet simply have no rows — the tile renders "—" instead of a made-up
    number.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# The media lifecycle the console is organized around. Order matters — it is the order
# the rail renders in, and it mirrors the pipeline a broadcast actually travels.
LIFECYCLE_STAGES = (
    "contribute", "ingest", "produce", "secure", "deliver", "understand", "preserve", "platform",
)

# Delivery regions. "global" marks impact that isn't region-scoped.
REGIONS = ("na", "eu", "apac", "sa")

INCIDENT_SEVERITIES = ("sev1", "sev2", "sev3", "sev4")
INCIDENT_KINDS = ("operational", "security", "governance")
INCIDENT_STATUSES = ("open", "monitoring", "resolved")

ALERT_SEVERITIES = ("critical", "high", "monitoring")

GOVERNANCE_KINDS = (
    "legal_hold",              # active preservation obligation on an org's media
    "entitlement_override",    # a plan limit manually lifted, pending approval
    "break_glass",             # emergency elevated grant, reviewed within 72h
    "single_path_override",    # event allowed live without a redundant contribution path
    "usage_export",            # metered usage export delivery (on-time % comes from these)
)
GOVERNANCE_STATUSES = ("open", "pending", "approved", "rejected", "delivered", "failed", "closed")

EVENT_IMPACTS = ("standard", "high", "unrepeatable")


class Incident(Base):
    """A recorded degradation or security event. Open incidents drive the console's
    attention counts; resolved ones are what availability percentages are computed from."""

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)  # e.g. INC-2026-0729-1420
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10), default="sev3", nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="operational", nullable=False)
    stage: Mapped[str | None] = mapped_column(String(20))     # one of LIFECYCLE_STAGES
    region: Mapped[str | None] = mapped_column(String(10))    # one of REGIONS, or NULL = global
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    commander: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization = relationship("Organization")

    # The availability query filters on the impact window and groups by stage+region.
    __table_args__ = (Index("ix_incidents_window", "started_at", "resolved_at"),)


class SessionAlert(Base):
    """An attention item raised against a live event by a human on the ops desk or by a
    monitor that sees something the database cannot (encoder telemetry, upstream route
    loss).

    This table is deliberately NOT the only source of the console's attention list:
    services.ops derives the self-evident cases (paused broadcast, nothing publishing,
    recording not captured) straight from the live-domain rows, because those facts are
    already recorded and duplicating them here would let the two disagree. Rows live here
    only for what cannot be derived."""

    __tablename__ = "session_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), default="monitoring", nullable=False)
    stage: Mapped[str | None] = mapped_column(String(20))
    issue: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event = relationship("Event")
    owner = relationship("User")


class GovernanceRecord(Base):
    """One row per governance/commercial obligation. `kind` selects which console row it
    counts toward; the fields are shared because the obligations genuinely share a shape
    (who, when opened, when due, current state)."""

    __tablename__ = "governance_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id"))
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    detail: Mapped[str | None] = mapped_column(String(300))
    meta: Mapped[dict | None] = mapped_column(JSON)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization = relationship("Organization")


class ElevationSession(Base):
    """Step-up privilege grant. A super admin holds standing read access; high-risk
    actions require an active, scoped, expiring elevation. The console footer shows the
    live countdown and can end it early."""

    __tablename__ = "elevation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(80), nullable=False)   # e.g. "Platform Operations"
    scopes: Mapped[list | None] = mapped_column(JSON, default=list)  # granular capability list
    reason: Mapped[str | None] = mapped_column(String(300))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user = relationship("User")


class PlatformMetric(Base):
    """Append-only metric sample. `name` is free-form so a new collector can start
    writing without a migration; the console asks for the names it knows about and
    tolerates absence (no rows -> no value, rendered as "—")."""

    __tablename__ = "platform_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    region: Mapped[str | None] = mapped_column(String(10))
    value: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_platform_metrics_name_time", "name", "recorded_at"),)


# ── Commercial override (ZST-COM-PLAN-001 Section 14 + Section 19) ───────────────────────

# Section 19's seven controlled exception types, transcribed verbatim from the table headings.
# The vocabulary is closed: an override outside this list would be an exception class the
# document does not authorize.
COMMERCIAL_OVERRIDE_TYPES = (
    "pilot_poc",
    "complimentary_access",
    "sales_demo",
    "incident_continuity",
    "contract_exception",
    "partner_bundle",
    "manual_override",
)

# What an override may target. Section 07 splits commercial rights into FEATURE (boolean
# access) and QUANTITY/limit (count/capacity); Section 14's `commercial_override` names
# "feature/limit override" as the scope. `plan` covers the case the platform can actually
# express today — an administrative plan assignment — which Section 20 requires to go through
# an approved override record rather than a direct database edit.
COMMERCIAL_OVERRIDE_TARGETS = ("feature", "limit", "plan")


class CommercialOverride(Base):
    """An explicit, time-bound, approved and audited commercial exception.

    ZST-COM-PLAN-001 Section 14 ("Minimum Engineering Data Model") specifies this object as:
    "scope, feature/limit override, reason, approver, expiry, audit_ref". Section 19 adds the
    control for the `manual_override` type: "Time-bound, reasoned, independently approved
    where risk requires, immutable audit and automatic expiry."

    Why a distinct table rather than another GovernanceRecord `kind`: GovernanceRecord is the
    console's obligation QUEUE — it tracks that something needs attention and carries no
    authority. This row is the GRANT itself, and it is read to decide access. Section 19's
    heading states the purpose plainly: "Exceptions must be explicit enough that they cannot
    become permanent shadow plans." A grant whose expiry is advisory is exactly that shadow
    plan, so `expires_at` is NOT NULL here and is enforced on read (crud.active_overrides).

    Deliberately NOT included: any price, discount percentage or monetary amount. Section 19's
    `complimentary_access` is "Approved zero-charge commercial record; usage still metered and
    attributable" — the zero-charge decision belongs to Finance and to the price book
    (ZST-COM-PRICE-001), not to this row.
    """

    __tablename__ = "commercial_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # SCOPE — which tenant this applies to. Org-scoped only: Section 19's exceptions are all
    # tenant-level, and an override that applied platform-wide would be a catalog change.
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    override_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # FEATURE/LIMIT OVERRIDE — what is being overridden and to what. `target_key` is the
    # entitlement key (Section 07's stable keys, e.g. `feature.webhooks.live`) or the plan
    # slug; `target_value` is its JSON value so a boolean, a count and a plan identity can all
    # be expressed without a column per shape.
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_key: Mapped[str] = mapped_column(String(120), nullable=False)
    target_value: Mapped[dict | None] = mapped_column(JSON)
    # REASON — Section 19 requires every exception be "reasoned". Enforced non-empty in CRUD.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # MAKER / APPROVER — "independently approved where risk requires". The requester is
    # recorded so crud.assert_distinct_maker_checker can enforce two-person control on
    # approval; both are FKs so a deleted actor cannot orphan the accountability trail.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # EXPIRY — NOT NULL by design. Section 19: "automatic expiry". A nullable expiry would
    # permit the permanent shadow plan the section exists to prevent.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # AUDIT_REF — correlation id threading this override to its AuditLog rows, matching the
    # correlation_id convention already used across the commercial ledger.
    audit_ref: Mapped[str | None] = mapped_column(String(60), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization")

    __table_args__ = (
        # Overrides are read by (tenant, still-valid) on every authorization decision.
        Index("ix_commercial_overrides_org_expiry", "org_id", "expires_at"),
    )
