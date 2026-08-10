"""Commercial/billing domain for Live Events — implements the data model from
ZST-LE-COM-001 (ZoikoStream_Live_Events_Commercial_Billing_Operating_Standard.docx),
Section 27 "Minimum Engineering Data Model" and Section 28 "Canonical State Machines".

Three-ledger doctrine (doc Section 3): this module is Ledger 2 (Live Event order) plus
the registries it depends on. Ledger 1 (platform subscription) stays in subscription.py/
plan.py — untouched, and never satisfies a Ledger 2 balance. Ledger 3 (audience/organizer
commerce) is out of scope entirely: no model here lets an audience member pay anything
(doc H3/H4) — that is a deliberately separate, unbuilt product.

No prices, tax rates, cancellation percentages or SLA numbers are hard-coded anywhere in
this module (doc: "No hidden overages; no invented prices; no invented taxes; no invented
service commitments" — Section 33). Every monetary/policy value lives in a versioned
registry row (CatalogLine, ServiceProfile, CancellationPolicy) that must be populated by
Commercial/Finance before it can be used — the engine reads policy, it does not decide it.

RBAC note (doc Section 25): the app's existing role ladder (models.user.ROLES) has no
concept of Zoiko-internal staff roles (Sales, Finance/Billing Ops, Live Events Operations,
Support, Security/Privacy) — it only distinguishes customer-org roles from the platform
super_admin. Rather than bolt on a second parallel role system, commercial endpoints map:
  * customer-side "commercial acceptance" (doc A3/A4)      -> require_org_admin
  * Zoiko-side financial/catalog authority (doc T1-T3)      -> require_super_admin
  * maker-checker (doc "Maker-checker rule")                -> approver_id column, code
    enforces requested_by != approved_by wherever both exist on a row
This is a real simplification versus the doc's full matrix — every approval is still
attributed to a named actor and audited (AuditLog via crud.commercial), just not gated by
a Sales/Finance/Ops sub-role that doesn't otherwise exist in this app.

Payments are processor-neutral (doc P1): see services/payments.py for the adapter
interface. No real processor is wired up — MockPaymentProvider is the only implementation
until a production merchant account exists (doc Section 26 "Merchant & finance" gate).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .event import Event
    from .organization import Organization
    from .platform_ops import Incident

# ── Shared vocab (doc Section 27 footer + Section 28) ──────────────────────────────────

RISK_TIERS = ("r0", "r1", "r2", "r3")
BILLING_CLASSIFICATIONS = ("commercial", "internal", "demo", "pilot", "sponsored", "complimentary", "qa", "sandbox")
BILLING_SOURCES = ("direct_zoikostream", "zoiko_one", "partner", "contract")
PURCHASER_TYPES = ("organization", "individual")
ENTITLEMENT_STATES = ("provisional", "active", "restricted", "expired", "revoked")

CATALOG_STATUSES = ("draft", "published", "retired")
SERVICE_PROFILE_STATUSES = ("draft", "published", "retired")
POLICY_STATUSES = ("draft", "published", "retired")

QUOTE_STATUSES = ("draft", "issued", "accepted", "expired", "withdrawn", "superseded")
ORDER_STATUSES = ("draft", "pending_acceptance", "accepted", "active", "completed", "canceled", "terminated")
CAPACITY_STATES = ("unrequested", "soft_held", "hard_reserved", "consumed", "released", "expired")
PAYMENT_STATES = (
    "requires_action", "pending", "partially_paid", "paid", "failed",
    "refunded", "part_refunded", "reversed", "disputed", "unmatched",
)
FINANCIAL_READINESS_STATES = ("not_due", "due", "satisfied", "financial_hold", "approved_exception")
READINESS_STATES = ("not_started", "in_progress", "pass", "conditional_pass", "fail")
REPLAY_STATES = ("not_available", "validating", "ready_for_review", "published", "withheld", "expired", "deleted_preserved")
CANCELLATION_STATES = (
    "requested", "policy_calculated", "approval_required", "approved",
    "capacity_released", "financial_settled", "closed",
)
INCIDENT_REVIEW_STATES = (
    "pending_review", "facts_confirmed", "commercial_review", "approved", "declined",
    "credit_refund_executed", "closed",
)
CAUSE_DOMAINS = (
    "zoiko_platform", "customer_venue", "contribution_device",
    "third_party_provider", "audience_device", "force_majeure", "mixed_unknown",
)
REMEDY_TYPES = ("credit", "refund", "fee_waiver")
REMEDY_STATUSES = ("pending", "approved", "executed", "declined")
CHANGE_ORDER_STATUSES = ("draft", "pending_acceptance", "accepted", "rejected")
EXCEPTION_TYPES = (
    "price_override", "waiver", "exceptional_cancellation",
    "financial_hold_override", "risk_tier_reduction", "complimentary_event",
)
EXCEPTION_STATUSES = ("requested", "approved", "declined", "expired")

_MONEY = Numeric(12, 2)


def _id_col() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ── A. Commercial account (doc Section 5/A) ─────────────────────────────────────────────

class CommercialAccount(Base):
    """The billing entity behind an org (doc A1: organization_id != purchaser_id != seller
    legal entity). One org may hold more than one — e.g. an enterprise operating under
    several legal entities/markets (doc A5) — so this is not unique on org_id."""

    __tablename__ = "commercial_accounts"

    id: Mapped[uuid.UUID] = _id_col()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    billing_classification: Mapped[str] = mapped_column(String(20), default="internal", nullable=False)
    billing_source: Mapped[str] = mapped_column(String(30), default="direct_zoikostream", nullable=False)
    # No legal-entity registry exists yet (doc L1) — stored as a config string until one does.
    seller_legal_entity_id: Mapped[str] = mapped_column(String(80), default="zoiko_tech_inc", nullable=False)
    billing_contact_name: Mapped[str | None] = mapped_column(String(120))
    billing_contact_email: Mapped[str | None] = mapped_column(String(255))
    tax_id: Mapped[str | None] = mapped_column(String(80))
    tax_country: Mapped[str | None] = mapped_column(String(80))
    payment_terms_days: Mapped[int | None] = mapped_column(Integer)
    # doc D4: credit/account-terms is Finance-approved, never invented by event operations.
    credit_status: Mapped[str] = mapped_column(String(20), default="none", nullable=False)  # none|approved|suspended
    credit_limit: Mapped[Decimal | None] = mapped_column(_MONEY)
    credit_terms_version: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship()


# ── Registries: catalog, service profile, cancellation policy (doc T1/T2, Section 4 P0) ─
# Every row here is Commercial/Finance-authored config, not application logic. An event
# order can only reference a PUBLISHED version; nothing here ships with real prices.

class CatalogVersion(Base):
    """A versioned, publishable price book (doc B2: 'no hard-coded fallback price... exists'
    — Section 26 acceptance checklist). Publishing creates a new version; accepted orders
    keep referencing their original version (doc T4) even after a newer one publishes."""

    __tablename__ = "catalog_versions"

    id: Mapped[uuid.UUID] = _id_col()
    version_label: Mapped[str] = mapped_column(String(40), nullable=False)
    vertical: Mapped[str] = mapped_column(String(60), nullable=False)  # memorials | worship | weddings | ...
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["CatalogLine"]] = relationship(back_populates="catalog_version", cascade="all, delete-orphan")


class CatalogLine(Base):
    """One priceable SKU within a catalog version. `unit_price` is nullable — a line with
    no price cannot be added to an order (crud.commercial enforces this; doc B2/L4: missing
    price/tax fails closed, it is never assumed to be zero or free)."""

    __tablename__ = "catalog_lines"

    id: Mapped[uuid.UUID] = _id_col()
    catalog_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_versions.id"), nullable=False, index=True)
    service_code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    unit_basis: Mapped[str] = mapped_column(String(30), default="per_event", nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(_MONEY)
    currency: Mapped[str | None] = mapped_column(String(3))
    tax_treatment: Mapped[str | None] = mapped_column(String(40))  # resolved by Finance/Tax, not inferred (doc L4)
    is_addon: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    catalog_version: Mapped["CatalogVersion"] = relationship(back_populates="lines")


class ServiceProfile(Base):
    """Versioned operational class binding risk tier to mandatory readiness controls
    (doc Section 10/F). Risk tier alone is not a marketing label — it is this row."""

    __tablename__ = "service_profiles"

    id: Mapped[uuid.UUID] = _id_col()
    version_label: Mapped[str] = mapped_column(String(40), nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(4), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    requires_backup_contribution: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_dual_recording: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_preview_return: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_command_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_reserved_capacity: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # R3 only
    requires_change_freeze: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)       # R3 only
    requires_full_rehearsal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)      # R3 only
    assured_event_eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CancellationPolicy(Base):
    """One row in the versioned cancellation matrix (doc E1: 'No code or support macro may
    invent a percentage not present in policy'). Keyed by vertical/risk tier/lead time —
    `refund_percentage` is nullable on purpose: an unconfigured combination must block
    cancellation processing rather than silently default to 0% or 100%."""

    __tablename__ = "cancellation_policies"

    id: Mapped[uuid.UUID] = _id_col()
    version_label: Mapped[str] = mapped_column(String(40), nullable=False)
    vertical: Mapped[str] = mapped_column(String(60), nullable=False)
    risk_tier: Mapped[str | None] = mapped_column(String(4))  # NULL = applies to all tiers
    lead_time_min_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    lead_time_max_hours: Mapped[int | None] = mapped_column(Integer)  # NULL = no upper bound
    refund_percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # 0-100
    nonrecoverable_cost_percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── B/C. Quote -> Order -> Capacity (doc Sections 6-7) ──────────────────────────────────

class Quote(Base):
    """DRAFT -> ISSUED -> ACCEPTED | EXPIRED | WITHDRAWN | SUPERSEDED (doc Section 28).
    Only an ACCEPTED quote may seed an EventOrder; crud.commercial enforces that a
    superseded quote can never later be accepted."""

    __tablename__ = "commercial_quotes"

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    catalog_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_versions.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    commercial_notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship()


class EventOrder(Base):
    """The commercial contract for one event (doc A2). event_id may exist before an order
    does, but the event cannot reach CONFIRMED/READY without an ACCEPTED order unless
    billing_classification is explicitly non-commercial (doc S1).

    order_version increments on every ChangeOrder application; the prior version's row is
    never overwritten in place (doc T4 — corrections are additive)."""

    __tablename__ = "event_orders"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_event_order_idempotency_key"),)

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    commercial_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("commercial_accounts.id"), nullable=False)
    purchaser_type: Mapped[str] = mapped_column(String(20), default="organization", nullable=False)
    purchaser_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commercial_quotes.id"))
    catalog_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_versions.id"), nullable=False)
    service_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("service_profiles.id"))
    cancellation_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cancellation_policies.id"))
    order_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(4), default="r0", nullable=False)
    billing_classification: Mapped[str] = mapped_column(String(20), default="commercial", nullable=False)
    billing_source: Mapped[str] = mapped_column(String(30), default="direct_zoikostream", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    terms_version: Mapped[str | None] = mapped_column(String(40))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Every order-creating command must carry one (doc Section 26 "Engineering integrity" +
    # Section 4 P0 blocker #10) — a retried request with the same key returns the same row
    # instead of creating a duplicate order.
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    event: Mapped["Event"] = relationship()
    commercial_account: Mapped["CommercialAccount"] = relationship()
    lines: Mapped[list["EventOrderLine"]] = relationship(back_populates="event_order", cascade="all, delete-orphan")


class EventOrderLine(Base):
    """One priced/entitlement-bearing line on an order — copied from a CatalogLine at
    order-acceptance time (doc B2) so a later catalog edit can never retroactively rewrite
    an accepted order's economics (doc T4, billing invariant #9)."""

    __tablename__ = "event_order_lines"

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    catalog_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("catalog_lines.id"))
    service_code: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=1, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    tax_treatment: Mapped[str | None] = mapped_column(String(40))
    is_addon: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_complimentary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    entitlement_effect: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event_order: Mapped["EventOrder"] = relationship(back_populates="lines")


class CapacityReservation(Base):
    """UNREQUESTED -> SOFT_HELD -> HARD_RESERVED -> CONSUMED | RELEASED | EXPIRED (doc
    Section 28). CONFIRMED requires a HARD_RESERVED row for every resource_type the
    service profile requires (doc C3) — the capacity service is authoritative, not a
    courtesy count."""

    __tablename__ = "capacity_reservations"

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    event_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_orders.id"))
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. production_operator, dual_recording
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    region: Mapped[str | None] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="unrequested", nullable=False)
    soft_hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hard_reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship()


# ── D. Payment schedule, invoices, payments, remedies (doc Sections 8, 15, 20, 27) ──────

class PaymentSchedule(Base):
    """A due-date milestone on an order (doc D1/D6). `required_before_ready` is what the
    READY gate checks (doc D2) — an R2/R3 event cannot reach READY while a required
    milestone remains unsatisfied without an approved CommercialException."""

    __tablename__ = "payment_schedules"

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    milestone: Mapped[str] = mapped_column(String(60), nullable=False)  # deposit | final | overage | ...
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    required_before_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="not_due", nullable=False)  # FINANCIAL_READINESS_STATES
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Invoice(Base):
    """One legal financial document, one currency, one seller entity (doc L2). Issued
    invoices are immutable — corrections happen via RefundCredit/ChangeOrder, never an
    UPDATE to `total_amount` (doc Section 26 checklist)."""

    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("number", name="uq_invoice_number"),)

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    ledger: Mapped[str] = mapped_column(String(20), default="live_event", nullable=False)
    seller_legal_entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    number: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. ZST-LE-INV-000123
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    issue_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft|issued|paid|void
    document_reference: Mapped[str | None] = mapped_column(String(200))  # hash/pointer to rendered doc
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    """Provider-neutral payment record (doc P1). `provider`/`provider_payment_ref` namespace
    external IDs; state transitions are driven only by services.payments adapters, never by
    a client-supplied status. idempotency_key makes a replayed webhook a no-op (doc billing
    invariant, Section 28: 'Provider webhook replay must be idempotent')."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_idempotency_key"),
        UniqueConstraint("provider", "provider_payment_ref", name="uq_payment_provider_ref"),
    )

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id"))
    provider: Mapped[str] = mapped_column(String(30), default="mock", nullable=False)
    provider_payment_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    method_type: Mapped[str | None] = mapped_column(String(30))
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="requires_action", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefundCredit(Base):
    """A money-out (or waived) remedy — always traceable to a policy version and, for
    incident-driven remedies, an EventIncident (doc K1/K4). `requested_by`/`approved_by`
    are two different users wherever the amount exceeds crud.commercial's maker-checker
    threshold; see module docstring."""

    __tablename__ = "refund_credits"

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    source_payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_incidents.id"))
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # credit | refund | fee_waiver
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(60), nullable=False)
    policy_version: Mapped[str | None] = mapped_column(String(60))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── G. Change orders (doc Section 11) ────────────────────────────────────────────────────

class ChangeOrder(Base):
    """An approved post-acceptance commercial delta (doc G1). Applying one increments the
    parent EventOrder.order_version; it never edits prior lines in place."""

    __tablename__ = "change_orders"

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    prior_order_version: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[dict] = mapped_column(JSON, nullable=False)  # {added_lines, removed_lines, field deltas}
    price_delta: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    service_impact: Mapped[str | None] = mapped_column(String(300))
    risk_impact: Mapped[str | None] = mapped_column(String(300))
    capacity_impact: Mapped[str | None] = mapped_column(String(300))
    customer_acceptance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── I/J. Readiness & replay entitlement (doc Sections 13-14) ────────────────────────────

class ReadinessCheck(Base):
    """One required-by-risk-tier gate (doc I3). READY is computed from the full set of
    these passing (or an unexpired exception), never a single boolean."""

    __tablename__ = "readiness_checks"

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    check_code: Mapped[str] = mapped_column(String(60), nullable=False)  # backup_contribution | dual_recording | ...
    required_by_risk_tier: Mapped[str | None] = mapped_column(String(4))
    evidence_reference: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="not_started", nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exception_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commercial_exceptions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship()


class ReplayEntitlement(Base):
    """Post-event access grant, independent of the live entitlement (doc H6/J2). Publishing
    is never automatic on live-end — it requires this row to reach `published`."""

    __tablename__ = "replay_entitlements"

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(20), default="audience", nullable=False)  # audience | customer
    publish_state: Mapped[str] = mapped_column(String(20), default="not_available", nullable=False)
    download_permission: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship()


# ── K. Incidents & exceptions (doc Sections 15, 25 maker-checker) ───────────────────────

class EventIncident(Base):
    """A service-failure record feeding the remedy workflow (doc K1-K6). Deliberately a
    separate table from models.platform_ops.Incident, which drives the public Status
    Publication Service for platform-wide degradation — this one is event/order-scoped
    commercial evidence. `platform_incident_id` links the two for correlation (doc Section
    30 'Event correlation') without merging their purposes; doc K5: public status is never
    itself authoritative for a refund decision."""

    __tablename__ = "event_incidents"

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    event_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_orders.id"))
    platform_incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id"))
    severity: Mapped[str] = mapped_column(String(10), default="sev3", nullable=False)
    cause_domain: Mapped[str] = mapped_column(String(30), default="mixed_unknown", nullable=False)
    affected_service: Mapped[str | None] = mapped_column(String(120))
    impact_description: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSON)
    review_state: Mapped[str] = mapped_column(String(30), default="pending_review", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped["Event"] = relationship()
    platform_incident: Mapped["Incident"] = relationship()


class CommercialException(Base):
    """Generic maker-checker override request (doc Section 25 'Maker-checker rule'): price
    override, waiver, exceptional cancellation, financial-hold override, risk-tier
    reduction, complimentary event. `approver_id` must differ from `requested_by` — enforced
    in crud.commercial, not the schema, since the rule is about two humans, not two ids."""

    __tablename__ = "commercial_exceptions"

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    event_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_orders.id"))
    exception_type: Mapped[str] = mapped_column(String(40), nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    rationale: Mapped[str | None] = mapped_column(Text)
    amount_exposure: Mapped[Decimal | None] = mapped_column(_MONEY)
    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False)
    expiry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── O. Partner attribution (doc Section 19) ──────────────────────────────────────────────

class PartnerAttribution(Base):
    """Referral/reseller source on a lead/order (doc O1/O5). No partner registry exists yet,
    so `partner_id` is a free-text identifier until one is built — this never changes the
    legal purchaser, only records who sourced the deal."""

    __tablename__ = "partner_attributions"

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    event_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_orders.id"))
    partner_id: Mapped[str] = mapped_column(String(120), nullable=False)
    agreement_version: Mapped[str | None] = mapped_column(String(60))
    referral_source: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="referral", nullable=False)  # referral | reseller
    commission_basis: Mapped[str | None] = mapped_column(String(120))
    commission_state: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
