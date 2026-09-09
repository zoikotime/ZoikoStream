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

RBAC note (doc Section 25): models.user.ROLES now carries billing_admin (customer-side)
and User.staff_commercial_role (nullable — sales/finance_ops/live_ops/support/security,
meaningful only on a super_admin row). security.commercial_can/require_commercial
implement the doc's actual per-action matrix (accept/change/refund_approve/write_off/
media_access); routers/commercial.py's module docstring has the full endpoint mapping.
An unscoped super_admin (staff_commercial_role NULL) still has full access — every
existing account's behavior is unchanged unless a role is explicitly assigned.
Maker-checker (doc "Maker-checker rule") is separate from the RBAC matrix: it's the
approver_id column, enforced in crud.commercial as requested_by != approved_by wherever
both exist on a row, regardless of which role either actor holds.

Payments are processor-neutral (doc P1): see services/payments.py for the adapter
interface. No real processor is wired up — MockPaymentProvider is the only implementation
until a production merchant account exists (doc Section 26 "Merchant & finance" gate).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text,
    UniqueConstraint, func, text,
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
# Canonical capacity audit vocabulary (doc Section 30). Every movement of committed inventory
# emits exactly one of these against the AuditLog, carrying actor, timestamp, the event/order
# it was for, and a reason. Named separately from CAPACITY_STATES because an audit event is a
# TRANSITION ("released") while a state is a condition ("released") — and because
# SOFT_HOLD_EXPIRED and RELEASED both land a reservation in a non-holding state but are
# operationally different facts (a lapse versus a decision).
CAPACITY_AUDIT_EVENTS = (
    "soft_hold_created", "soft_hold_expired", "hard_reserved", "released", "consumed",
)
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
# doc P4: "a distinct dispute state with evidence package, financial reserve/adjustment and
# case ownership... not the same as a refund." won/lost map onto Payment.state's existing
# "paid"/"reversed" (see PAYMENT_STATES above) — this table is the case record; Payment
# just reflects the current money state.
DISPUTE_STATES = ("opened", "evidence_required", "evidence_submitted", "won", "lost", "withdrawn")
EXCEPTION_TYPES = (
    "price_override", "waiver", "exceptional_cancellation",
    "financial_hold_override", "risk_tier_reduction", "complimentary_event",
    # doc Section 25 names both in the RBAC matrix ("write_off" was already an ACTION in
    # security.COMMERCIAL_ACTIONS with no code path behind it). A write-off reduces what the
    # customer owes without money moving; a discount reduces the price before it is owed.
    # Both are governed the same way as every other override: requested, then approved by a
    # different human, with evidence — never a direct edit to an amount.
    "write_off", "discount",
)
EXCEPTION_STATUSES = ("requested", "approved", "declined", "expired")

SELLER_ENTITY_STATUSES = ("draft", "active", "suspended", "retired")
CAPACITY_POOL_STATUSES = ("draft", "active", "suspended", "retired")

# ── Provider event processing lifecycle (doc P1 "raw evidence retention") ────────────────
# RECEIVED -> PROCESSING -> PROCESSED | REJECTED | FAILED, plus REPLAYED for a redelivery of
# an event whose identity we have already seen. REJECTED is a deliberate refusal (illegal
# state transition, amount/currency mismatch); FAILED is an unexpected processing error.
# Both are retained — a provider event is never deleted (doc: financial evidence is additive).
PROVIDER_EVENT_STATUSES = ("received", "processing", "processed", "rejected", "failed", "replayed")

# ── Canonical Payment state machine (doc Section 28) ─────────────────────────────────────
# The webhook path used to apply `payment.state = state_map[event_type]` from ANY current
# state, so a provider event could drive failed -> paid or refunded -> paid. This graph is
# the single authority on what may follow what; crud.payment_transition_error is the only
# gate, used by both the human (capture/refund) and provider (webhook) paths.
#
# Same-state transitions are handled separately as idempotent no-ops (a redelivered
# "captured" event must not capture twice) — they are deliberately NOT listed here, because
# "allowed" and "already there, do nothing" are different answers.
PAYMENT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    # Provider needs more from the payer (3DS/SCA-style) before it can authorize.
    "requires_action": ("pending", "failed"),
    # Authorized but NOT settled (doc D3: authorization is not settlement).
    "pending": ("paid", "partially_paid", "failed", "requires_action"),
    # Settled in full.
    "paid": ("partially_paid", "part_refunded", "refunded", "disputed", "reversed"),
    # Settled short of the full amount (doc D6: never collapse partial into PAID).
    "partially_paid": ("paid", "part_refunded", "refunded", "disputed", "reversed"),
    "part_refunded": ("refunded", "disputed", "reversed"),
    # A dispute is decided by the card network, not by us: won -> funds retained, lost ->
    # funds clawed back (doc P4).
    "disputed": ("paid", "part_refunded", "reversed"),
    # Money returned or clawed back. `refunded -> disputed` stays reachable because a
    # cardholder can still dispute a transaction that was already refunded.
    "refunded": ("disputed",),
    "reversed": ("disputed",),
    # Settlement money we could not attribute to a Payment. Only a controlled reconciliation
    # may move it (crud.match_settlement) — never a provider event.
    "unmatched": (),
    # Terminal: a declined authorization cannot later become a successful payment. This is
    # the transition the old code silently permitted.
    "failed": (),
}

# ── Canonical commercial lifecycle (doc Section 28 "Event commercial lifecycle") ─────────
# DRAFT -> QUOTED -> ORDER_ACCEPTED -> FINANCIAL_HOLD -> CAPACITY_HELD -> CONFIRMED
#       -> READY -> LIVE -> COMPLETED, with CANCELED reachable from anything pre-COMPLETED.
#
# This state is DERIVED, never stored as a writable field. The authoritative facts already
# live in EventOrder.status, PaymentSchedule.status, CapacityReservation.state, ReadinessCheck
# and Event.status; a second writable column would be a second truth, and the doc's "no state
# is manually bypassable" requirement is satisfied precisely because there is no setter to
# bypass — crud.commercial.commercial_lifecycle_state computes it and CommercialStateTransition
# records every observed change.
#
# EventOrder.lifecycle_state is a CACHE of the last computed value, kept only so a transition
# can be detected and logged. Nothing reads it as authority (see crud.sync_lifecycle).
COMMERCIAL_LIFECYCLE_STATES = (
    "draft", "quoted", "order_accepted", "financial_hold", "capacity_held",
    "confirmed", "ready", "live", "completed", "canceled",
)

# What may legally follow what. Backwards moves are deliberately permitted where the
# underlying facts can legitimately regress: a change order clears the tax determination and
# reopens FINANCIAL_HOLD, a reschedule releases capacity and drops CONFIRMED back to
# ORDER_ACCEPTED, and a failed readiness check un-READYs an event. What is NOT permitted is
# skipping the gates — nothing reaches CONFIRMED without passing through capacity, and nothing
# reaches LIVE except from READY.
COMMERCIAL_LIFECYCLE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("quoted", "order_accepted", "canceled"),
    # A quote may be superseded back to draft, or accepted into an order.
    "quoted": ("draft", "order_accepted", "canceled"),
    # Acceptance splits on what is outstanding: money due -> FINANCIAL_HOLD, money satisfied
    # but capacity not yet committed -> CAPACITY_HELD.
    "order_accepted": ("financial_hold", "capacity_held", "confirmed", "canceled"),
    "financial_hold": ("order_accepted", "capacity_held", "confirmed", "canceled"),
    "capacity_held": ("order_accepted", "financial_hold", "confirmed", "canceled"),
    # CONFIRMED means paid/credited AND capacity hard-reserved. READY additionally means every
    # readiness gate passed. Both can regress if a fact regresses.
    "confirmed": ("ready", "capacity_held", "financial_hold", "order_accepted", "canceled"),
    "ready": ("live", "confirmed", "capacity_held", "financial_hold", "canceled"),
    # Only from READY. This is the transition golive_block_reason guards.
    "live": ("completed", "ready", "canceled"),
    # Terminal for the commercial record. A post-completion correction is a new change
    # order/credit against a completed order, never a return to an earlier state (doc T4).
    "completed": (),
    "canceled": (),
}

_MONEY = Numeric(12, 2)


def _id_col() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ── L. Seller legal entity registry (doc L1, P2) ────────────────────────────────────────

class SellerLegalEntity(Base):
    """The Zoiko selling entity that legally invoices a purchaser (doc L1:
    "seller_legal_entity_id is mandatory on issued financial documents"; doc P2: "Provider
    merchant/account ID must match seller_legal_entity_id").

    Previously `seller_legal_entity_id` was a bare configuration string defaulting to
    "zoiko_tech_inc" with no registry behind it, so the application silently assumed one
    seller forever and nothing validated the value. This table is that registry.

    `code` — not the UUID — is what CommercialAccount/Invoice reference, so the existing
    string values ("zoiko_tech_inc") keep resolving once Finance registers them; no data
    backfill was required to introduce this.

    Ships EMPTY and rows default to status="draft". Only an ACTIVE entity may issue an
    invoice (crud.resolve_seller_entity), so verified legal/tax/merchant identity is a
    deliberate Finance action, never a code default (doc Section 26 "Merchant & finance":
    "Zoiko Tech Inc. selling/merchant identity, bank/payout and invoice details verified").
    No legal or tax facts are invented here.
    """

    __tablename__ = "seller_legal_entities"
    __table_args__ = (UniqueConstraint("code", name="uq_seller_legal_entity_code"),)

    id: Mapped[uuid.UUID] = _id_col()
    code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(String(80))
    # Transaction currency facts. NULL = not yet established; doc L2 requires one currency
    # per legal financial document, so this constrains what an order under this entity may use.
    default_currency: Mapped[str | None] = mapped_column(String(3))
    supported_currencies: Mapped[list | None] = mapped_column(JSON)
    # Tax registration metadata only — the identifiers themselves are Finance/Tax's to supply.
    tax_registration_id: Mapped[str | None] = mapped_column(String(80))
    tax_registration_country: Mapped[str | None] = mapped_column(String(80))
    invoice_number_prefix: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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
    # References SellerLegalEntity.code (doc L1). Nullable with NO default: an account must
    # be assigned a REGISTERED, ACTIVE seller entity before it can be invoiced. It used to
    # default to the literal "zoiko_tech_inc" with no registry behind it, which silently
    # assumed one seller forever — crud.resolve_seller_entity now fails closed instead.
    seller_legal_entity_id: Mapped[str | None] = mapped_column(String(80), index=True)
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
    # doc Section 10: managed delivery is not optional at the higher tiers — the event must be
    # run by an assigned Zoiko command owner, not self-served by the customer. Distinct from
    # requires_command_owner, which is satisfied by a manual attestation: managed_only makes the
    # command-owner gate NON-WAIVABLE for this profile even if the flag above is unset, which is
    # what "managed-only launch" means operationally.
    managed_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Whether an order under this profile MAY be sold as an Assured Event. Eligibility is not
    # the same as election: crud.accept_order requires this to be true before EventOrder
    # .assured_event can be set, and evaluate_readiness then applies the stricter gate set.
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
    # NULL = tax not determined for this quote (option A of doc L4's two readings: a quote is
    # a non-binding estimate, so it may be issued before Finance determines tax — but it must
    # then say so rather than show a silent 0.00). It previously defaulted to 0, which
    # presented an undetermined tax as a quoted zero. Nothing downstream copies this value:
    # the order carries its own determination, and issue_invoice gates on that.
    tax_amount: Mapped[Decimal | None] = mapped_column(_MONEY)
    commercial_notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # -- LVE-001 (ZST-EC-001) ------------------------------------------------------------
    # One marker per proposal transition. Durable rather than process-local, so a retry, a
    # second worker or a redeploy cannot re-announce a transition already communicated.
    proposal_ready_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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

    # ── Tax determination (doc L4/L6, Section 27) ──────────────────────────────────────
    # tax_amount is NULL until a determination is recorded. It used to be `default=0,
    # nullable=False`, which made "Finance has not determined tax yet" structurally
    # indistinguishable from "no tax is due" — and since nothing ever computed it, every
    # order and every invoice carried zero tax by construction. NULL now means UNDETERMINED
    # and crud.issue_invoice refuses to issue against it (doc L4: "Missing tax determination
    # blocks invoice issuance for live commercial events").
    #
    # A legitimate zero-tax outcome is still fully representable — but only explicitly, as a
    # determination whose tax_amount is 0.00 with a real tax_treatment (exempt, zero-rated,
    # reverse-charge, out-of-scope, ...). The treatment vocabulary is Finance/Tax's to define,
    # not this module's, so no set of codes is hard-coded here (doc L6: no permanent
    # free-text tax overrides; determination is versioned and evidenced).
    tax_amount: Mapped[Decimal | None] = mapped_column(_MONEY)
    tax_treatment: Mapped[str | None] = mapped_column(String(60))
    tax_jurisdiction: Mapped[str | None] = mapped_column(String(80))
    tax_source: Mapped[str | None] = mapped_column(String(80))       # who/what determined it
    tax_rule_version: Mapped[str | None] = mapped_column(String(60))  # versioned rule basis
    # Mandatory when tax_amount == 0 (crud.record_tax_determination): a zero must always
    # carry the reason it is zero — exempt, zero-rated, reverse-charge, out-of-scope — so
    # "0.00 because Finance determined so" can never be confused with "0.00 by omission".
    tax_exemption_reason: Mapped[str | None] = mapped_column(String(200))
    tax_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tax_determined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tax_determined_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    # Provisional while tax is undetermined: equals subtotal until a determination lands,
    # then recomputed as subtotal + tax_amount (crud.record_tax_determination).
    total_amount: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(4), default="r0", nullable=False)
    # doc Section 10 "Assured Event": an elected, contractually-stronger service commitment.
    # Only settable when the bound ServiceProfile is assured_event_eligible (crud.accept_order),
    # and it tightens the readiness gate set rather than being a label (crud.evaluate_readiness).
    assured_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    billing_classification: Mapped[str] = mapped_column(String(20), default="commercial", nullable=False)
    billing_source: Mapped[str] = mapped_column(String(30), default="direct_zoikostream", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # Last OBSERVED value of the derived commercial lifecycle state. A cache for change
    # detection only — crud.commercial_lifecycle_state recomputes from the underlying facts on
    # every read and nothing treats this column as authority. See COMMERCIAL_LIFECYCLE_STATES.
    lifecycle_state: Mapped[str | None] = mapped_column(String(20))
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
    # Frozen alongside the price so a line's commercial basis is fully self-describing:
    # catalog_version (via the parent order) + service_code + unit_price + currency (parent
    # order, one per document per doc L2) + unit_basis + tax_treatment. Without this the
    # "per_event vs per_hour" meaning of a price could only be recovered from a CatalogLine
    # that may since have been edited.
    unit_basis: Mapped[str | None] = mapped_column(String(30))
    is_addon: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_complimentary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    entitlement_effect: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event_order: Mapped["EventOrder"] = relationship(back_populates="lines")


class EventOrderVersion(Base):
    """Immutable snapshot of an order's commercial state at one accepted version (doc
    Section 27 `event_order_version`; doc Section 28: "Commercial corrections create new
    versions/change orders; do not overwrite accepted history").

    EventOrder.order_version was only ever a counter on a row that got mutated in place, so
    the economics of version N were lost the moment version N+1 was applied. Each acceptance
    (and each applied change order) now writes one row here and never touches an earlier one.

    `snapshot` holds the full order header + every line as at this version, so historical
    truth survives even if a line row is later altered by some future code path.
    """

    __tablename__ = "event_order_versions"
    __table_args__ = (UniqueConstraint("event_order_id", "order_version", name="uq_event_order_version"),)

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    order_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL = the original acceptance; set = the change order that produced this version.
    change_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("change_orders.id"))
    catalog_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("catalog_versions.id"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    tax_amount: Mapped[Decimal | None] = mapped_column(_MONEY)
    total_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommercialStateTransition(Base):
    """Append-only log of the derived commercial lifecycle (doc Section 28).

    One row per OBSERVED state change, written by crud.sync_lifecycle after any operation that
    could move the state. Because the state itself is derived from the underlying facts, this
    table is the only place lifecycle history exists — and it is insert-only: a transition is
    never updated or deleted, so "how did this order reach CONFIRMED" is answerable forever.

    `blocking_reasons` captures WHY the state is what it is at the moment of transition (the
    readiness evaluator's reasons, the financial state, the missing capacity), so a historical
    state is self-explaining without re-deriving facts that have since changed.

    `illegal` marks a transition that COMMERCIAL_LIFECYCLE_TRANSITIONS does not permit. Such a
    transition is still recorded rather than dropped: it means the underlying facts moved in a
    way the model did not anticipate, which is a data-integrity alarm worth keeping, not an
    error to swallow. The state is reported as computed either way — the log tells the truth
    about what happened.
    """

    __tablename__ = "commercial_state_transitions"

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_orders.id"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20), nullable=False)
    # What operation observed the change — "order.accept", "payment.captured", "capacity.reserve",
    # "readiness.evaluate", "order.cancel", "event.golive", ...
    trigger: Mapped[str] = mapped_column(String(60), nullable=False)
    # Human rationale where the operation carried one (a cancellation reason, a reschedule
    # reason, a write-off justification). Distinct from `trigger`, which names the mechanical
    # operation, and from `blocking_reasons`, which is the machine-derived gate list.
    reason: Mapped[str | None] = mapped_column(Text)
    illegal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocking_reasons: Mapped[list | None] = mapped_column(JSON)
    financial_state: Mapped[str | None] = mapped_column(String(30))
    capacity_satisfied: Mapped[bool | None] = mapped_column(Boolean)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventReschedule(Base):
    """A moved event window, with the original preserved (doc Section 9 "Reschedule").

    Deliberately NOT an update to Event.start_time alone: the doc requires the original
    commercial and operational history to survive, and a bare field update destroys the very
    fact a reschedule dispute turns on ("what were we contracted to deliver, and when").

    A reschedule RELEASES the capacity held for the old window — it cannot silently carry it,
    because capacity is a time-specific resource and the old reservation does not cover the new
    time (doc C1). Re-holding against the new window is a separate, explicit step, so an event
    mid-reschedule is visibly not capacity-backed rather than appearing confirmed.

    No reschedule fee is computed here. There is no published reschedule-fee policy registry,
    and inventing a percentage is exactly what doc E1 forbids — so any commercial consequence
    is raised as a normal ChangeOrder and referenced by `change_order_id`.
    """

    __tablename__ = "event_reschedules"

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    event_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_orders.id"), index=True)
    # The window as it stood before this reschedule — the historical obligation.
    previous_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    new_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Which order version was effective when the move happened, so the commercial basis of the
    # original commitment is recoverable even after later change orders.
    order_version: Mapped[int | None] = mapped_column(Integer)
    # Reservations released by this move, as {reservation_id: resource_type}. Kept so "what did
    # we give up" is answerable without inferring it from release timestamps.
    released_reservations: Mapped[dict | None] = mapped_column(JSON)
    # Lead-time hours to the ORIGINAL start at the moment of the request. The cancellation
    # policy band a reschedule would have fallen into is a commercial fact worth freezing.
    lead_time_hours_at_request: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    change_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("change_orders.id"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapacityPool(Base):
    """Finite, time-bounded inventory of one constrained resource (doc C4: "Can an event be
    oversold? No. The system must fail closed when capacity is unavailable"; doc Section 4
    P0 blocker #8 "Implement capacity reservation and release").

    This is the missing authority the previous phase's audit flagged: CapacityReservation
    recorded intent faithfully but had nothing to check against, so oversubscription was
    undetectable. A reservation now requires a matching ACTIVE pool whose window contains
    the requested window, and is granted only under a row lock on that pool.

    Scoped by seller legal entity + resource type + region + time window, because live event
    capacity is a time-specific resource (an operator booked 14:00-16:00 Tuesday is not
    available to another event in that window). Deliberately NOT a global counter.

    `total_capacity` is the only stored figure. Reserved/consumed/available are DERIVED from
    the reservation rows (crud.pool_utilisation) rather than denormalized counters, so the
    two can never drift apart — the reservations are the single source of truth.

    Ships EMPTY with status defaulting to "draft": with no approved pool, every capacity
    request fails closed. No capacity number is assumed anywhere (the previous
    DEFAULT_CAPACITY_ENVELOPE = 500 constant is gone and is not replaced here).
    """

    __tablename__ = "capacity_pools"

    id: Mapped[uuid.UUID] = _id_col()
    seller_legal_entity_id: Mapped[str | None] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    region: Mapped[str | None] = mapped_column(String(20))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CapacityReservation(Base):
    """UNREQUESTED -> SOFT_HELD -> HARD_RESERVED -> CONSUMED | RELEASED | EXPIRED (doc
    Section 28). CONFIRMED requires a HARD_RESERVED row for every resource_type the
    service profile requires (doc C3) — the capacity service is authoritative, not a
    courtesy count.

    `quantity` is the RESERVED amount (what the pool has committed); `requested_quantity`
    records what was asked for, so a partial grant or a refusal is auditable rather than
    silently rewritten. Rows that hold inventory are exactly those in SOFT_HELD,
    HARD_RESERVED and CONSUMED — see crud.pool_utilisation.
    """

    __tablename__ = "capacity_reservations"

    id: Mapped[uuid.UUID] = _id_col()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    event_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_orders.id"))
    # Which accepted order version authorised this reservation (doc Section 27 "source order").
    event_order_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_order_versions.id"))
    # NULL only for legacy rows created before pools existed; new reservations always bind
    # to the pool their inventory came out of.
    capacity_pool_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("capacity_pools.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. production_operator, dual_recording
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_quantity: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    region: Mapped[str | None] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="unrequested", nullable=False)
    soft_hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hard_reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
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
    # How much captured money is attributed to this milestone. Previously absent, so
    # _reconcile_schedule marked the oldest milestone "satisfied" on ANY capture amount —
    # a $1 capture satisfied a $50,000 deposit (CF-5). `satisfied` now requires
    # allocated_amount >= amount, and a short allocation leaves the milestone unsatisfied
    # with the partial figure visible (doc D6: outstanding balance stays explicit).
    allocated_amount: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="not_due", nullable=False)  # FINANCIAL_READINESS_STATES
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Invoice(Base):
    """One legal financial document, one currency, one seller entity (doc L2). Issued
    invoices are immutable — corrections happen via RefundCredit/ChangeOrder, never an
    UPDATE to `total_amount` (doc Section 26 checklist)."""

    __tablename__ = "invoices"
    # Unique PER SELLER ENTITY, not globally (doc Section 3 "Invoice numbering": "Do not
    # reuse invoice sequences across platform commercial accounts, Live Event orders or
    # organizer/audience commerce"). Each legal entity keeps its own series, which is normal
    # multi-entity accounting practice — so the constraint is the pair, and the allocator
    # (InvoiceNumberSequence) is keyed the same way. The old global UNIQUE(number) made
    # per-entity series impossible.
    # One LIVE invoice per event order. `issue_invoice` had no duplicate guard in code, and the
    # constraint above does not imply one — it makes NUMBERS unique per seller, not DOCUMENTS
    # per order — so two concurrent POSTs to /orders/{id}/invoices produced two valid invoices
    # for the same order, each with its own number. The reconciliation control
    # `list_orders_missing_invoice` already treats invoice existence as binary per order, so
    # one-per-order is the model's existing assumption; this enforces it in the database, where
    # concurrent requests cannot race past it.
    #
    # PARTIAL on state <> 'void': `state` declares draft|issued|paid|void, so voiding a document
    # and issuing a replacement is a path the model already allows. A blanket unique would
    # forbid that re-issue — a behaviour change, not an integrity fix — so the index excludes
    # voided rows and leaves the void-then-reissue path exactly as the model defines it.
    __table_args__ = (
        UniqueConstraint("seller_legal_entity_id", "number", name="uq_invoice_seller_number"),
        Index("uq_invoice_active_per_order", "event_order_id",
              unique=True, postgresql_where=text("state <> 'void'")),
    )

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    ledger: Mapped[str] = mapped_column(String(20), default="live_event", nullable=False)
    seller_legal_entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    number: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. ZST-LE-INV-000123
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    # Stays NOT NULL: an issued invoice always has a determined tax amount, because
    # crud.issue_invoice refuses to issue while the order's determination is NULL. The
    # accompanying facts are SNAPSHOTTED off the order rather than read through the FK — an
    # issued invoice is immutable (doc Section 26), so a later re-determination on the order
    # must not retroactively rewrite the tax basis of a document already sent to a customer.
    tax_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    tax_treatment: Mapped[str | None] = mapped_column(String(60))
    tax_jurisdiction: Mapped[str | None] = mapped_column(String(80))
    tax_source: Mapped[str | None] = mapped_column(String(80))
    tax_rule_version: Mapped[str | None] = mapped_column(String(60))
    tax_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tax_exemption_reason: Mapped[str | None] = mapped_column(String(200))
    total_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    issue_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft|issued|paid|void
    document_reference: Mapped[str | None] = mapped_column(String(200))  # hash/pointer to rendered doc
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProviderEvent(Base):
    """One inbound payment-provider event, with its raw evidence (doc P1: "Use provider
    adapters with normalized payment states and raw evidence retention").

    This is the idempotency boundary for everything a provider tells us. Before it existed,
    the webhook handler deduplicated on `Payment.idempotency_key` — and then OVERWROTE that
    column, destroying the key `authorize_payment` deduplicates on, so replaying an
    authorization created a SECOND Payment row for the same order (CF-1).

    Identity is `(provider, provider_event_id)` with a DATABASE unique constraint, not an
    application-level existence check: two concurrent deliveries of the same event race, and
    only the database can arbitrate. crud.ingest_provider_event uses INSERT ... ON CONFLICT
    DO NOTHING so the loser of that race is told "already known" instead of double-applying.

    Provider-neutral on purpose: no provider-specific column exists, and `payload` holds
    whatever shape the provider sent. Nothing here assumes any particular processor's event
    names, statuses or payload schema.

    Never deleted. A rejected or failed event is retained as evidence of what was received
    and why it was refused.
    """

    __tablename__ = "provider_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_provider_event_identity"),
    )

    id: Mapped[uuid.UUID] = _id_col()
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    # The provider's own event identifier — the unit of idempotency. Distinct from
    # provider_payment_ref, which identifies the PAYMENT an event is about; one payment
    # legitimately produces many events.
    provider_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    # What the payment ref in the payload pointed at, resolved at processing time. NULL when
    # nothing matched (see UnmatchedSettlement).
    payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"), index=True)
    provider_payment_ref: Mapped[str | None] = mapped_column(String(120), index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Raw provider evidence. Payment credentials are never stored (doc P6/R1) — the ingest
    # path redacts known-sensitive keys before this is written (crud._redact_payload).
    payload: Mapped[dict | None] = mapped_column(JSON)
    # sha256 of the exact bytes received, computed BEFORE redaction, so the stored evidence
    # can still be tied back to what the provider actually signed.
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processing_status: Mapped[str] = mapped_column(String(20), default="received", nullable=False)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_error: Mapped[str | None] = mapped_column(Text)
    # What the event actually did, so a replay can return the ORIGINAL outcome rather than
    # recomputing one: {"applied": bool, "from_state": ..., "to_state": ..., "reason": ...}
    processing_result: Mapped[dict | None] = mapped_column(JSON)
    # Ties this event to the Payment/Invoice/Order/Event/AuditLog chain it touched
    # (doc Section 30 "Event correlation").
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UnmatchedSettlement(Base):
    """Provider settlement money we could not attribute to a Payment (doc P5: "Route to
    reconciliation exception queue; never auto-allocate by guess").

    Previously such an event returned None and nothing was persisted — the settlement simply
    vanished, and `Payment.state == "unmatched"` was never written by any code path, so the
    reconciliation queue that reads it was permanently empty (CF-8).

    Deliberately its own table rather than a ReconciliationException: that model carries a
    reference to an object that already exists, whereas the whole problem here is that no
    such object could be found. This holds the provider's money facts until a human matches
    them, and matching is an explicit, audited action — never a guess.
    """

    __tablename__ = "unmatched_settlements"

    id: Mapped[uuid.UUID] = _id_col()
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_payment_ref: Mapped[str | None] = mapped_column(String(120), index=True)
    provider_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("provider_events.id"))
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(_MONEY)
    currency: Mapped[str | None] = mapped_column(String(3))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # open -> matched | written_off. Never auto-advanced.
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    matched_payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"))
    matched_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvoiceNumberSequence(Base):
    """Atomic invoice-number allocator, one row per (ledger, seller legal entity).

    Replaces a read-then-increment over the invoices table ("SELECT the latest, parse its
    trailing digits, add one"), which handed the same number to two concurrent callers and
    turned the loser into an unhandled IntegrityError 500.

    Allocation is a single INSERT ... ON CONFLICT DO UPDATE ... RETURNING (see
    crud._allocate_invoice_number), so the increment and the read of the allocated value
    happen in one atomic statement under Postgres row locking. No application-level lock,
    no retry loop, and no gap-free guarantee (a rolled-back transaction burns a number,
    which is normal and preferable to reusing one).
    """

    __tablename__ = "invoice_number_sequences"

    # f"{ledger}:{seller_legal_entity_id}" — matches Invoice's UNIQUE(seller entity, number).
    scope: Mapped[str] = mapped_column(String(140), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Payment(Base):
    """Provider-neutral payment record (doc P1). `provider`/`provider_payment_ref` namespace
    external IDs; state transitions are driven only by services.payments adapters, never by
    a client-supplied status. idempotency_key makes a replayed webhook a no-op (doc billing
    invariant, Section 28: 'Provider webhook replay must be idempotent')."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_idempotency_key"),
        UniqueConstraint("provider", "provider_payment_ref", name="uq_payment_provider_ref"),
        # A provider checkout session drives at most ONE payment. This is the correlation
        # authority for the hosted-checkout flow, so the database — not application code —
        # guarantees a session can never fan out to two payment rows.
        UniqueConstraint("provider", "checkout_session_ref",
                         name="uq_payment_provider_checkout_session"),
    )

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id"))
    provider: Mapped[str] = mapped_column(String(30), default="mock", nullable=False)
    # NULLABLE on purpose. A provider-hosted checkout session legitimately has no payment
    # reference until the payer submits — Stripe reports `payment_intent: null` on a freshly
    # created Checkout Session. The reference is adopted later from a verified provider event.
    # Both unique constraints above stay correct because Postgres treats NULLs as distinct,
    # so many awaiting-payer rows coexist while a bound reference is still unique per provider.
    provider_payment_ref: Mapped[str | None] = mapped_column(String(120))
    # The hosted-checkout session this payment came from, when it came from one. Populated at
    # session creation and never changed afterwards: it is how an inbound event is matched
    # back to this payment before any payment reference exists.
    checkout_session_ref: Mapped[str | None] = mapped_column(String(120))
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


class PaymentDispute(Base):
    """A chargeback case (doc P4/Section 20). Deliberately separate from RefundCredit: "a
    chargeback is not the same as a refund" and "disputed payment does not silently rewrite
    the original invoice or delivered event record" — this row is pure case evidence/
    tracking; the underlying Payment/Invoice amounts are never edited by anything here.
    `provider_dispute_ref` is the provider's own case ID (services.payments.DisputeResult);
    `case_owner_id` is doc P4's "case ownership" (typically Finance/Billing Ops)."""

    __tablename__ = "payment_disputes"

    id: Mapped[uuid.UUID] = _id_col()
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), nullable=False, index=True)
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_dispute_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(60), nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # doc P4 "financial reserve/adjustment" — what the provider holds back while the case is
    # open. Distinct from `amount` so a partial-reserve provider policy can be represented.
    reserve_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="opened", nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSON)
    evidence_due_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    case_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── G. Change orders (doc Section 11) ────────────────────────────────────────────────────

class ChangeOrder(Base):
    """An approved post-acceptance commercial delta (doc G1). Applying one increments the
    parent EventOrder.order_version; it never edits prior lines in place.

    Full provenance (doc Section 25): who asked (`requested_by`), who accepted
    (`approved_by`), when (`created_at`/`accepted_at`), why (`reason`), what lines moved
    (`changes` before, `applied_lines` after) and the amount (`price_delta`). `requested_by`
    and `reason` were absent, so a change order recorded WHAT changed and the approver, but
    never who initiated it or on what grounds.
    """

    __tablename__ = "change_orders"

    id: Mapped[uuid.UUID] = _id_col()
    event_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_orders.id"), nullable=False, index=True)
    prior_order_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # REQUESTED plan: {"add_lines": [...], "remove_line_ids": [...]}. Validated against the
    # order's own published catalog version before the row is written (crud._plan_change_order_lines).
    changes: Mapped[dict] = mapped_column(JSON, nullable=False)
    # APPLIED result, written at acceptance: the line rows actually created and destroyed, with
    # their amounts. `changes` is intent; this is what happened. Kept separately because a plan
    # re-validated at apply time can legitimately be refused, and because the removed rows are
    # deleted from event_order_lines — this is where their identity survives outside the
    # previous version's snapshot.
    applied_lines: Mapped[dict | None] = mapped_column(JSON)
    price_delta: Mapped[Decimal] = mapped_column(_MONEY, default=0, nullable=False)
    # Mandatory rationale. A commercial delta with no stated reason is not auditable.
    reason: Mapped[str | None] = mapped_column(Text)
    service_impact: Mapped[str | None] = mapped_column(String(300))
    risk_impact: Mapped[str | None] = mapped_column(String(300))
    capacity_impact: Mapped[str | None] = mapped_column(String(300))
    customer_acceptance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # The approved CommercialException that authorised a revenue REDUCTION or a price override.
    # An increase needs none; a decrease may not exist without one (crud.create_change_order),
    # so this column is the evidence link for "which approval permitted this reduction".
    approval_exception_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commercial_exceptions.id"))
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

    # ── Replay watermark (BRD table 53: publication "applies... watermark policy" —
    # crud.commercial.publish_replay/services.delivery's shared watermark ticker) ────────
    # "not_applicable" until a publish is actually requested — an unpublished entitlement
    # has nothing to burn yet, so it must not show as a stuck "pending"/"failed" watermark.
    watermark_status: Mapped[str] = mapped_column(String(20), default="not_applicable", nullable=False)
    watermarked_file_key: Mapped[str | None] = mapped_column(String(500))
    watermark_error: Mapped[str | None] = mapped_column(Text)
    # Which LiveRecording the watermarked copy was actually burned from (crud.event.
    # list_replay_candidates' own selection — primary-first, validation-aware) — recorded
    # so routers/events.py::watch_event can still show a real duration without re-deriving
    # the selection a second time.
    source_recording_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # ── MED-009 replay lifecycle (ZST-EC-001) ───────────────────────────────────
    # `publish_state` is the authoritative state; these record the WITHDRAWAL that moves it
    # to "withheld", which previously had no writer at all (the state was declared in
    # REPLAY_STATES and never set by anything).
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    withdraw_reason: Mapped[str | None] = mapped_column(String(200))
    # The state this row held before the current one — what the MED-009 access-change
    # notice reports as "previous access" without having to guess it.
    previous_publish_state: Mapped[str | None] = mapped_column(String(20))
    state_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    prepared_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_changed_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    withdrawn_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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

    # -- LVE-010 operational lifecycle (ZST-EC-001) --------------------------------------
    # Added to THIS record rather than beside it: an interruption is already a service-failure
    # fact, and a parallel table would let the two disagree about whether an event was held.
    # `review_state` stays the commercial remedy workflow; `operational_state` is what the
    # customer is told about while it is happening.
    #
    # Cancellation is NOT derivable from any of this. A BroadcastSession ending, a producer
    # disconnecting, a recording stopping and a LiveKit room closing are all ordinary, and
    # services/event_ops.py requires Event.status == "cancelled" before it will say canceled.
    operational_state: Mapped[str | None] = mapped_column(String(20))
    reason_category: Mapped[str | None] = mapped_column(String(40))
    delay_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hold_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Only ever set when an operator explicitly commits to a time. Nothing derives it, so the
    # message can promise an update only when somebody actually promised one.
    next_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Customer-safe summary. Investigation detail belongs in `evidence`, which is never mailed.
    customer_summary: Mapped[str | None] = mapped_column(String(300))

    delayed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hold_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_ready_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    # Which specific gate this exception overrides (e.g. "financial_readiness"). An exception
    # is narrowly scoped by (exception_type, overridden_gate, order/event) — it is never a
    # blanket "skip payment" switch, and the readiness evaluator only consults an exception
    # whose scope matches the gate that is actually failing.
    overridden_gate: Mapped[str | None] = mapped_column(String(60))
    # Readiness verdict at request time, so the audit answers "what was overridden".
    previous_state: Mapped[str | None] = mapped_column(String(60))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_notes: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Financial period-close & reconciliation (doc Section 29) ────────────────────────────

PERIOD_STATUSES = ("open", "closed")
RECONCILIATION_CATEGORIES = (
    "capacity_without_order",       # doc 29 "Capacity-to-order": confirmed/high-risk event, no hard reservation
    "order_without_capacity",       # ... or a hard reservation with no active commercial order
    "order_without_invoice",        # doc 29 "Order-to-invoice": billable accepted order/change order, no invoice
    "event_without_classification", # doc 29 "Event-to-order": no billing_classification/source + no order/waiver
    "unmatched_settlement",         # doc 29 "Daily payment": provider settlement with no matching invoice/order
    # doc L1: an accepted commercial order whose account has no ACTIVE registered seller entity
    # can be taxed and PAID and then never invoiced (issue_invoice fails closed on it). Caught
    # at period close so the entity is assigned before anyone is charged, not after.
    "order_without_seller_entity",
)
RECONCILIATION_STATUSES = ("open", "investigating", "resolved", "accepted_risk")


class FinancialPeriod(Base):
    """Freeze/materialize a period's commercial snapshot for accounting export (doc 29
    "Period close"). `snapshot` is computed once — at PREPARE time, below — and never
    recomputed live — "subsequent corrections are separately dated" (doc 29): a correction
    after close becomes a NEW period's activity, it never mutates a closed period's numbers.

    Maker-checker (doc Section 25 principle, extended here): closing a period is a two-step
    state machine, `open -> pending_close -> closed`, mirroring approve_commercial_exception /
    approve_refund_credit — one actor prepares (freezes the snapshot, files exceptions), a
    DIFFERENT actor confirms (locks it). There is still no reopen path from `closed` — the
    two-step only gates who may lock it, not whether it can be unlocked."""

    __tablename__ = "financial_periods"
    __table_args__ = (UniqueConstraint("label", name="uq_financial_period_label"),)

    id: Mapped[uuid.UUID] = _id_col()
    label: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "2026-08"
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # "open" -> "pending_close" -> "closed". Widened from String(10): "pending_close" is 13 chars.
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    snapshot: Mapped[dict | None] = mapped_column(JSON)
    # Maker: who froze the snapshot and requested the close.
    prepared_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Checker: who confirmed the lock. Must differ from prepared_by — enforced in
    # crud.confirm_period_close, not here (a DB column cannot compare itself to a sibling row).
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReconciliationException(Base):
    """One entry in doc 29's "exception queue with an owner and resolution record" —
    "none [evidence source] is allowed to silently replace the ZoikoStream Live Events
    commercial ledger. Differences enter an exception queue..." `reference_type` +
    `reference_id` point at whatever object the mismatch concerns (an Event, EventOrder,
    CapacityReservation, Payment, ...) without a hard FK, since the category set spans
    several unrelated tables.

    Maker-checker (doc Section 25 principle, extended here): a TERMINAL resolution
    ("resolved" / "accepted_risk") is a two-step state machine — `resolve_exception` proposes
    it (status becomes "pending_review", `proposed_status` records the intended outcome,
    `prepared_by` records who proposed it), and `confirm_exception_resolution` finalizes it,
    refusing a confirmer who is the same person as `prepared_by`. "investigating" is NOT
    terminal and applies immediately — it is a claim to work the item, not a financial or
    accounting decision, so nothing to dual-control."""

    __tablename__ = "reconciliation_exceptions"

    id: Mapped[uuid.UUID] = _id_col()
    period_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("financial_periods.id"), index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # "open" -> "investigating" (optional, non-terminal) -> "pending_review" (proposed terminal
    # outcome, awaiting a different confirmer) -> "resolved" | "accepted_risk".
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    # Maker: who proposed the terminal outcome below, while status == "pending_review".
    prepared_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proposed_status: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
