"""Request/response models for the commercial/billing API (/commercial/*, /events/{id}/
commercial/*). Mirrors schemas/event.py's conventions: ConfigDict(from_attributes=True) on
every *Out, Literal for caller-chosen enums, plain str for server-computed workflow states
(the source of truth for valid values is the state machine in crud/commercial.py, not the
schema — duplicating it as a Literal would just be two places to keep in sync)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RiskTier = Literal["r0", "r1", "r2", "r3"]
PurchaserType = Literal["organization", "individual"]
BillingClassification = Literal["commercial", "internal", "demo", "pilot", "sponsored", "complimentary", "qa", "sandbox"]
BillingSource = Literal["direct_zoikostream", "zoiko_one", "partner", "contract"]
RemedyType = Literal["credit", "refund", "fee_waiver"]
CauseDomain = Literal[
    "zoiko_platform", "customer_venue", "contribution_device",
    "third_party_provider", "audience_device", "force_majeure", "mixed_unknown",
]
IncidentSeverity = Literal["sev1", "sev2", "sev3", "sev4"]
ReadinessResult = Literal["not_started", "in_progress", "pass", "conditional_pass", "fail"]


# ── Catalog ───────────────────────────────────────────────────────────────────────────

class CatalogLineCreate(BaseModel):
    service_code: str = Field(..., max_length=60)
    name: str = Field(..., max_length=200)
    description: str | None = None
    unit_basis: str = Field("per_event", max_length=30)
    unit_price: Decimal | None = Field(None, ge=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    tax_treatment: str | None = Field(None, max_length=40)
    is_addon: bool = False


class CatalogLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    catalog_version_id: uuid.UUID
    service_code: str
    name: str
    description: str | None = None
    unit_basis: str
    unit_price: Decimal | None = None
    currency: str | None = None
    tax_treatment: str | None = None
    is_addon: bool
    created_at: datetime | None = None


class CatalogVersionCreate(BaseModel):
    vertical: str = Field(..., max_length=60)
    version_label: str = Field(..., max_length=40)
    notes: str | None = None


class CatalogVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    vertical: str
    version_label: str
    status: str
    effective_at: datetime | None = None
    published_at: datetime | None = None
    notes: str | None = None
    created_at: datetime | None = None
    lines: list[CatalogLineOut] = []


# ── Service profiles ──────────────────────────────────────────────────────────────────

class ServiceProfileCreate(BaseModel):
    version_label: str = Field(..., max_length=40)
    risk_tier: RiskTier
    name: str = Field(..., max_length=120)
    description: str | None = None
    requires_backup_contribution: bool = False
    requires_dual_recording: bool = False
    requires_preview_return: bool = False
    requires_command_owner: bool = False
    requires_reserved_capacity: bool = False
    requires_change_freeze: bool = False
    requires_full_rehearsal: bool = False
    assured_event_eligible: bool = False


class ServiceProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_label: str
    risk_tier: str
    name: str
    description: str | None = None
    requires_backup_contribution: bool
    requires_dual_recording: bool
    requires_preview_return: bool
    requires_command_owner: bool
    requires_reserved_capacity: bool
    requires_change_freeze: bool
    requires_full_rehearsal: bool
    assured_event_eligible: bool
    status: str
    effective_at: datetime | None = None
    created_at: datetime | None = None


# ── Cancellation policy ───────────────────────────────────────────────────────────────

class CancellationPolicyCreate(BaseModel):
    version_label: str = Field(..., max_length=40)
    vertical: str = Field(..., max_length=60)
    risk_tier: RiskTier | None = None
    lead_time_min_hours: int = Field(..., ge=0)
    lead_time_max_hours: int | None = Field(None, ge=0)
    refund_percentage: Decimal | None = Field(None, ge=0, le=100)
    nonrecoverable_cost_percentage: Decimal | None = Field(None, ge=0, le=100)


class CancellationPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_label: str
    vertical: str
    risk_tier: str | None = None
    lead_time_min_hours: int
    lead_time_max_hours: int | None = None
    refund_percentage: Decimal | None = None
    nonrecoverable_cost_percentage: Decimal | None = None
    status: str
    effective_at: datetime | None = None
    created_at: datetime | None = None


# ── Commercial account ────────────────────────────────────────────────────────────────

class CommercialAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    org_id: uuid.UUID
    billing_classification: str
    billing_source: str
    seller_legal_entity_id: str
    billing_contact_name: str | None = None
    billing_contact_email: str | None = None
    tax_id: str | None = None
    tax_country: str | None = None
    payment_terms_days: int | None = None
    credit_status: str
    credit_limit: Decimal | None = None
    credit_terms_version: str | None = None
    created_at: datetime | None = None


# ── Quote ─────────────────────────────────────────────────────────────────────────────

class QuoteCreate(BaseModel):
    catalog_version_id: uuid.UUID
    currency: str = Field(..., min_length=3, max_length=3)
    amount: Decimal = Field(..., ge=0)
    tax_amount: Decimal = Field(Decimal(0), ge=0)
    valid_until: datetime | None = None
    notes: str | None = None


class QuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_id: uuid.UUID
    version: int
    catalog_version_id: uuid.UUID
    currency: str
    amount: Decimal
    tax_amount: Decimal
    commercial_notes: str | None = None
    status: str
    valid_until: datetime | None = None
    issued_at: datetime | None = None
    accepted_at: datetime | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime | None = None


# ── Event order ───────────────────────────────────────────────────────────────────────

class OrderCreate(BaseModel):
    catalog_version_id: uuid.UUID
    purchaser_type: PurchaserType = "organization"
    purchaser_id: uuid.UUID | None = None
    service_profile_id: uuid.UUID | None = None
    cancellation_policy_id: uuid.UUID | None = None
    currency: str = Field(..., min_length=3, max_length=3)
    quote_id: uuid.UUID | None = None
    billing_classification: BillingClassification = "commercial"
    billing_source: BillingSource = "direct_zoikostream"
    idempotency_key: str | None = Field(None, max_length=100)


class OrderLineCreate(BaseModel):
    catalog_line_id: uuid.UUID
    quantity: Decimal = Field(Decimal(1), gt=0)
    is_addon: bool = False
    is_complimentary: bool = False


class OrderLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_order_id: uuid.UUID
    catalog_line_id: uuid.UUID | None = None
    service_code: str
    description: str | None = None
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    tax_treatment: str | None = None
    is_addon: bool
    is_complimentary: bool
    entitlement_effect: str | None = None
    created_at: datetime | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_id: uuid.UUID
    commercial_account_id: uuid.UUID
    purchaser_type: str
    purchaser_id: uuid.UUID | None = None
    quote_id: uuid.UUID | None = None
    catalog_version_id: uuid.UUID
    service_profile_id: uuid.UUID | None = None
    cancellation_policy_id: uuid.UUID | None = None
    order_version: int
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    risk_tier: str
    billing_classification: str
    billing_source: str
    status: str
    terms_version: str | None = None
    accepted_at: datetime | None = None
    accepted_by: uuid.UUID | None = None
    idempotency_key: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    lines: list[OrderLineOut] = []


class OrderAccept(BaseModel):
    terms_version: str | None = None


# ── Capacity ──────────────────────────────────────────────────────────────────────────

class CapacityHoldCreate(BaseModel):
    resource_type: str = Field(..., max_length=60)
    window_start: datetime | None = None
    window_end: datetime | None = None
    quantity: int = Field(1, ge=1)
    region: str | None = Field(None, max_length=20)
    hold_minutes: int = Field(30, ge=1, le=1440)


class CapacityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_id: uuid.UUID
    event_order_id: uuid.UUID | None = None
    resource_type: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    quantity: int
    region: str | None = None
    state: str
    soft_hold_expires_at: datetime | None = None
    hard_reserved_at: datetime | None = None
    released_at: datetime | None = None
    release_reason: str | None = None
    created_at: datetime | None = None


# ── Payment schedule, payments, invoices ─────────────────────────────────────────────

class PaymentScheduleCreate(BaseModel):
    milestone: str = Field(..., max_length=60)
    amount: Decimal = Field(..., gt=0)
    due_at: datetime | None = None
    required_before_ready: bool = True


class PaymentScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_order_id: uuid.UUID
    milestone: str
    due_at: datetime | None = None
    amount: Decimal
    required_before_ready: bool
    status: str
    created_at: datetime | None = None


class PaymentAuthorizeCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    idempotency_key: str = Field(..., max_length=100)
    provider_name: str = Field("mock", max_length=30)
    simulate_failure: bool = False


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_order_id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    provider: str
    provider_payment_ref: str
    method_type: str | None = None
    amount: Decimal
    currency: str
    state: str
    authorized_at: datetime | None = None
    captured_at: datetime | None = None
    settled_at: datetime | None = None
    failure_reason: str | None = None
    created_at: datetime | None = None


class PaymentWebhookIn(BaseModel):
    provider_name: str = Field("mock", max_length=30)
    provider_payment_ref: str = Field(..., max_length=120)
    event_type: Literal["capture_succeeded", "capture_failed", "refunded", "disputed", "reversed"]
    idempotency_key: str = Field(..., max_length=100)


class InvoiceCreate(BaseModel):
    due_date: datetime | None = None


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_order_id: uuid.UUID
    ledger: str
    seller_legal_entity_id: str
    number: str
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    issue_date: datetime | None = None
    due_date: datetime | None = None
    state: str
    document_reference: str | None = None
    created_at: datetime | None = None


# ── Change orders ─────────────────────────────────────────────────────────────────────

class ChangeOrderCreate(BaseModel):
    changes: dict = Field(default_factory=dict)
    price_delta: Decimal = Decimal(0)
    service_impact: str | None = None
    risk_impact: str | None = None
    capacity_impact: str | None = None


class ChangeOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_order_id: uuid.UUID
    prior_order_version: int
    changes: dict
    price_delta: Decimal
    service_impact: str | None = None
    risk_impact: str | None = None
    capacity_impact: str | None = None
    customer_acceptance: bool
    accepted_at: datetime | None = None
    approved_by: uuid.UUID | None = None
    effective_at: datetime | None = None
    status: str
    created_at: datetime | None = None


# ── Cancellation ──────────────────────────────────────────────────────────────────────

class CancelOrderRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class CancellationResult(BaseModel):
    order: OrderOut
    policy_version: str
    refund_amount: Decimal
    refund_credit_id: uuid.UUID | None = None


# ── Refunds / credits ─────────────────────────────────────────────────────────────────

class RefundCreditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_order_id: uuid.UUID
    source_payment_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None
    type: str
    amount: Decimal
    reason_code: str
    policy_version: str | None = None
    requested_by: uuid.UUID | None = None
    approved_by: uuid.UUID | None = None
    provider_ref: str | None = None
    status: str
    created_at: datetime | None = None


# ── Disputes / chargebacks (doc Section 20/P4) ────────────────────────────────────────

class DisputeOpenCreate(BaseModel):
    reason_code: str = Field(..., max_length=60)
    amount: Decimal | None = Field(None, gt=0)  # None = full payment amount


class DisputeEvidenceCreate(BaseModel):
    evidence: dict = Field(..., min_length=1)


class DisputeResolveCreate(BaseModel):
    won: bool


class PaymentDisputeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    payment_id: uuid.UUID
    event_order_id: uuid.UUID
    provider: str
    provider_dispute_ref: str
    reason_code: str
    amount: Decimal
    currency: str
    reserve_amount: Decimal
    status: str
    evidence: dict | None = None
    evidence_due_by: datetime | None = None
    case_owner_id: uuid.UUID | None = None
    opened_at: datetime | None = None
    resolved_at: datetime | None = None


# ── Financial period-close (doc Section 29) ───────────────────────────────────────────

class PeriodCreate(BaseModel):
    label: str = Field(..., max_length=20)
    period_start: datetime
    period_end: datetime


class FinancialPeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    label: str
    period_start: datetime
    period_end: datetime
    status: str
    snapshot: dict | None = None
    closed_by: uuid.UUID | None = None
    closed_at: datetime | None = None
    created_at: datetime | None = None


class ReconciliationExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    period_id: uuid.UUID | None = None
    category: str
    reference_type: str
    reference_id: uuid.UUID
    description: str
    status: str
    owner_id: uuid.UUID | None = None
    resolution_notes: str | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class ExceptionResolveCreate(BaseModel):
    status: Literal["resolved", "accepted_risk", "investigating"]
    resolution_notes: str | None = None


# ── Incidents & remedies ──────────────────────────────────────────────────────────────

class IncidentCreate(BaseModel):
    severity: IncidentSeverity = "sev3"
    cause_domain: CauseDomain = "mixed_unknown"
    affected_service: str | None = Field(None, max_length=120)
    impact_description: str | None = None
    evidence: dict | None = None
    event_order_id: uuid.UUID | None = None
    platform_incident_id: uuid.UUID | None = None


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_id: uuid.UUID
    event_order_id: uuid.UUID | None = None
    platform_incident_id: uuid.UUID | None = None
    severity: str
    cause_domain: str
    affected_service: str | None = None
    impact_description: str | None = None
    evidence: dict | None = None
    review_state: str
    created_by: uuid.UUID | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None


class RemedyProposeCreate(BaseModel):
    event_order_id: uuid.UUID
    remedy_type: RemedyType
    amount: Decimal = Field(..., gt=0)
    reason_code: str = Field(..., max_length=60)
    policy_version: str | None = None


# ── Readiness ─────────────────────────────────────────────────────────────────────────

class ReadinessCheckCreate(BaseModel):
    check_code: str = Field(..., max_length=60)
    status: ReadinessResult
    evidence_reference: str | None = Field(None, max_length=300)
    exception_id: uuid.UUID | None = None


class ReadinessCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_id: uuid.UUID
    check_code: str
    required_by_risk_tier: str | None = None
    evidence_reference: str | None = None
    status: str
    actor_id: uuid.UUID | None = None
    completed_at: datetime | None = None
    exception_id: uuid.UUID | None = None
    created_at: datetime | None = None


class ReadinessEvaluation(BaseModel):
    ready: bool
    blocking_reasons: list[str]
    required_checks: list[str]


# ── Replay entitlement ────────────────────────────────────────────────────────────────

class ReplayEntitlementCreate(BaseModel):
    scope: Literal["audience", "customer"] = "audience"
    expires_at: datetime | None = None


class ReplayEntitlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_id: uuid.UUID
    scope: str
    publish_state: str
    download_permission: bool
    expires_at: datetime | None = None
    created_at: datetime | None = None
    # Whether the watermarked file viewers will actually be served is ready yet -- distinct
    # from publish_state, since publish_replay queues the burn without waiting for it (a
    # real recording can run hours). See routers/events.py::watch_event's own gate.
    watermark_status: str
    watermark_error: str | None = None


# ── Reconciliation ────────────────────────────────────────────────────────────────────

class ReconciliationReport(BaseModel):
    reservations_without_order: list[CapacityOut]
    reservations_with_missing_order: list[CapacityOut]
    unmatched_settlements: list[PaymentOut]
    orders_missing_invoice: list[OrderOut]
