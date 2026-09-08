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
    # Makes the command-owner readiness gate non-waivable for this profile — the event cannot
    # be self-served (doc Section 10 managed-only launch).
    managed_only: bool = False
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
    managed_only: bool = False
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
    # None = no seller legal entity assigned yet; invoicing is blocked until one is (doc L1).
    seller_legal_entity_id: str | None = None
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
    # No default (was Decimal(0)): omitting tax now means "not determined" and is stored as
    # NULL, never as a quoted zero. A real zero must be sent explicitly (doc L4).
    tax_amount: Decimal | None = Field(None, ge=0)
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
    tax_amount: Decimal | None = None  # None = tax not determined for this quote
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
    unit_basis: str | None = None
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
    # None = no tax determination recorded yet; the order cannot be invoiced in that state
    # (crud.issue_invoice fails closed). total_amount is provisional until then.
    tax_amount: Decimal | None = None
    tax_treatment: str | None = None
    tax_jurisdiction: str | None = None
    tax_source: str | None = None
    tax_rule_version: str | None = None
    tax_effective_at: datetime | None = None
    tax_exemption_reason: str | None = None
    tax_determined_at: datetime | None = None
    total_amount: Decimal
    risk_tier: str
    billing_classification: str
    billing_source: str
    status: str
    assured_event: bool = False
    # Derived commercial lifecycle state as last observed. Read-only and never accepted on
    # input — the state is computed from committed facts (crud.commercial_lifecycle_state), so
    # there is deliberately no field a client could set to move it.
    lifecycle_state: str | None = None
    terms_version: str | None = None
    accepted_at: datetime | None = None
    accepted_by: uuid.UUID | None = None
    idempotency_key: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    lines: list[OrderLineOut] = []


class OrderAccept(BaseModel):
    terms_version: str | None = None
    # Electing an Assured Event is a commercial decision made at acceptance. Validated against
    # the bound service profile's assured_event_eligible flag (crud.accept_order) — a client
    # cannot assert assurance the profile does not support.
    assured_event: bool = False


# ── Commercial lifecycle (doc Section 28) ─────────────────────────────────────────────

class LifecycleTransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_order_id: uuid.UUID | None = None
    event_id: uuid.UUID
    from_state: str | None = None
    to_state: str
    trigger: str
    reason: str | None = None
    illegal: bool
    blocking_reasons: list[str] | None = None
    financial_state: str | None = None
    capacity_satisfied: bool | None = None
    actor_id: uuid.UUID | None = None
    created_at: datetime | None = None


class LifecycleOut(BaseModel):
    """Current derived state plus the evidence behind it and the append-only history."""
    state: str
    financial_state: str | None = None
    capacity_satisfied: bool | None = None
    capacity_held: bool = False
    blocking_reasons: list[str] = []
    readiness_verdict: str | None = None
    # What the lifecycle graph permits from here — so a console can show the next legitimate
    # step rather than guessing it.
    allowed_next: list[str] = []
    history: list[LifecycleTransitionOut] = []


# ── Reschedule (doc Section 9) ────────────────────────────────────────────────────────

class RescheduleCreate(BaseModel):
    new_start_time: datetime
    new_end_time: datetime | None = None
    reason: str = Field(..., min_length=3, max_length=2000)


class RescheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_id: uuid.UUID
    event_order_id: uuid.UUID | None = None
    previous_start_time: datetime | None = None
    previous_end_time: datetime | None = None
    new_start_time: datetime
    new_end_time: datetime | None = None
    reason: str
    order_version: int | None = None
    released_reservations: dict | None = None
    lead_time_hours_at_request: Decimal | None = None
    change_order_id: uuid.UUID | None = None
    requested_by: uuid.UUID | None = None
    created_at: datetime | None = None


class RescheduleResult(BaseModel):
    reschedule: RescheduleOut
    released_reservations: int
    lifecycle_state: str
    # True when capacity was released and must be re-held against the new window before the
    # event can be confirmed again. Stated explicitly so a console cannot imply it carried over.
    capacity_requires_rehold: bool


# ── Capacity ──────────────────────────────────────────────────────────────────────────

class CapacityHoldCreate(BaseModel):
    resource_type: str = Field(..., max_length=60)
    # Mandatory: capacity is drawn from a time-bounded CapacityPool, so an open-ended hold
    # cannot be checked against inventory (doc C1/C5).
    window_start: datetime
    window_end: datetime
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
    requested_quantity: int | None = None
    quantity: int
    capacity_pool_id: uuid.UUID | None = None
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
    """Legacy authorization request. The client no longer names the amount.

    `amount` is OPTIONAL and advisory: omit it to authorize the order's outstanding balance,
    or send the exact outstanding figure to have it confirmed. Any other value is refused
    (crud.authorize_payment) — it was previously REQUIRED and accepted unchecked, which let a
    customer role choose what to pay.

    There is deliberately no `currency`, `tax`, `discount` or `seller` field: those come from
    the accepted order.

    `provider_name` is required. It used to default to "mock", so an HTTP caller that omitted
    it silently got the SIMULATOR against a real order.
    """

    idempotency_key: str = Field(..., max_length=100)
    provider_name: str = Field(..., max_length=30)
    amount: Decimal | None = Field(None, gt=0)
    simulate_failure: bool = False


class CheckoutSessionCreate(BaseModel):
    """Request body for starting a hosted checkout.

    Deliberately carries NO financial fields. There is no `amount`, `currency`, `tax`,
    `discount` or `seller` to send: those come from the committed commercial record, and a
    client that included them would simply have them ignored (extra keys are not accepted into
    this model). The order is identified by the path parameter.
    """

    provider_name: str = Field("stripe", max_length=30)


class CheckoutSessionOut(BaseModel):
    """What the browser is allowed to know. No provider secret, no raw provider object, and
    nothing that implies the payment succeeded — creating a session collects nothing."""

    checkout_url: str
    payment_id: uuid.UUID
    amount: Decimal
    currency: str
    state: str
    # True when a repeated Pay click reused the existing payment/session instead of making
    # another one.
    reused: bool = False


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
    """Provider-neutral inbound event. `event_type` uses the generic vocabulary in
    crud.PROVIDER_EVENT_STATE_MAP — a provider adapter translates its own names onto these,
    so no processor's event naming leaks into the domain.

    `provider_event_id` replaces the old caller-supplied `idempotency_key`: identity now
    belongs to the PROVIDER EVENT (unique per provider in the database), not to the Payment,
    whose own idempotency key must never be rewritten by an inbound event (CF-1).

    `amount`/`currency` are optional but VALIDATED when present — they can only ever confirm
    the commercial record, never redefine it (crud._amount_mismatch_reason)."""

    provider_name: str = Field("mock", max_length=30)
    provider_event_id: str = Field(..., min_length=1, max_length=160)
    provider_payment_ref: str | None = Field(None, max_length=120)
    event_type: Literal[
        "authorization_succeeded", "authorization_failed",
        "capture_succeeded", "capture_partial", "capture_failed",
        "refunded", "refund_partial",
        "disputed", "dispute_won", "dispute_lost", "reversed",
    ]
    amount: Decimal | None = Field(None, ge=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    occurred_at: datetime | None = None
    payload: dict | None = None


class ProviderEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    provider: str
    provider_event_id: str
    event_type: str
    payment_id: uuid.UUID | None = None
    provider_payment_ref: str | None = None
    occurred_at: datetime | None = None
    received_at: datetime
    payload_hash: str
    signature_verified: bool
    processing_status: str
    processing_attempts: int
    processed_at: datetime | None = None
    processing_error: str | None = None
    processing_result: dict | None = None
    correlation_id: str
    created_at: datetime | None = None


class UnmatchedSettlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    provider: str
    provider_payment_ref: str | None = None
    provider_event_id: uuid.UUID | None = None
    event_type: str
    amount: Decimal | None = None
    currency: str | None = None
    occurred_at: datetime | None = None
    received_at: datetime
    status: str
    reason: str
    matched_payment_id: uuid.UUID | None = None
    matched_by: uuid.UUID | None = None
    matched_at: datetime | None = None
    resolution_notes: str | None = None
    correlation_id: str | None = None
    created_at: datetime | None = None


class SettlementMatchCreate(BaseModel):
    payment_id: uuid.UUID
    notes: str | None = None


class CommercialExceptionCreate(BaseModel):
    """A governed override request (doc Section 25). `rationale` is mandatory and evidence is
    mandatory for money-affecting types — enforced in crud.request_commercial_exception,
    which also refuses a request that targets neither an event nor an order."""

    exception_type: Literal[
        "price_override", "waiver", "exceptional_cancellation",
        "financial_hold_override", "risk_tier_reduction", "complimentary_event",
        # write_off: stop pursuing an owed amount (executed via POST
        # /commercial-exceptions/{id}/execute-write-off). discount: authorise a revenue
        # reduction, which crud.create_change_order requires before it will accept a negative
        # price delta. Both are money-affecting, so both require evidence.
        "write_off", "discount",
    ]
    rationale: str = Field(..., min_length=1)
    event_id: uuid.UUID | None = None
    event_order_id: uuid.UUID | None = None
    overridden_gate: Literal["financial_readiness", "capacity", "readiness_checks",
                              "risk_tier", "pricing"] | None = None
    evidence: dict | None = None
    amount_exposure: Decimal | None = Field(None, ge=0)
    expiry_at: datetime | None = None


class ExceptionDecisionCreate(BaseModel):
    notes: str | None = None


class CommercialExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_id: uuid.UUID | None = None
    event_order_id: uuid.UUID | None = None
    exception_type: str
    requested_by: uuid.UUID | None = None
    approver_id: uuid.UUID | None = None
    rationale: str | None = None
    amount_exposure: Decimal | None = None
    status: str
    expiry_at: datetime | None = None
    evidence: dict | None = None
    overridden_gate: str | None = None
    previous_state: str | None = None
    decided_at: datetime | None = None
    decision_notes: str | None = None
    correlation_id: str | None = None
    created_at: datetime | None = None


class SellerLegalEntityCreate(BaseModel):
    """Registers a Zoiko selling entity (doc L1). Created as `draft` — activation is a
    separate call. No legal or tax facts are defaulted; the caller supplies them."""

    code: str = Field(..., min_length=1, max_length=80)
    legal_name: str = Field(..., min_length=1, max_length=200)
    country: str | None = Field(None, max_length=80)
    default_currency: str | None = Field(None, min_length=3, max_length=3)
    supported_currencies: list[str] | None = None
    tax_registration_id: str | None = Field(None, max_length=80)
    tax_registration_country: str | None = Field(None, max_length=80)
    invoice_number_prefix: str | None = Field(None, max_length=30)


class SellerLegalEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    legal_name: str
    country: str | None = None
    default_currency: str | None = None
    supported_currencies: list | None = None
    tax_registration_id: str | None = None
    tax_registration_country: str | None = None
    invoice_number_prefix: str | None = None
    status: str
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    created_at: datetime | None = None


class CapacityPoolCreate(BaseModel):
    """Approved capacity inventory (doc C4). window_start/window_end are mandatory — live
    event capacity is time-specific and a pool with no window could not be checked against
    an event's actual service period."""

    resource_type: str = Field(..., min_length=1, max_length=60)
    window_start: datetime
    window_end: datetime
    total_capacity: int = Field(..., ge=0)
    region: str | None = Field(None, max_length=20)
    seller_legal_entity_id: str | None = Field(None, max_length=80)
    notes: str | None = None


class CapacityPoolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    seller_legal_entity_id: str | None = None
    resource_type: str
    region: str | None = None
    window_start: datetime
    window_end: datetime
    total_capacity: int
    status: str
    notes: str | None = None
    created_at: datetime | None = None


class CapacityPoolUtilisationOut(BaseModel):
    """Derived figures — computed from the reservation rows, never stored (see
    crud.pool_utilisation), so available/reserved can't drift from reality."""

    pool_id: uuid.UUID
    resource_type: str
    region: str | None = None
    window_start: datetime
    window_end: datetime
    status: str
    total_capacity: int
    soft_held: int
    hard_reserved: int
    consumed: int
    reserved_capacity: int
    available_capacity: int


class OrderVersionOut(BaseModel):
    """An immutable accepted-order snapshot (doc Section 27 event_order_version)."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_order_id: uuid.UUID
    order_version: int
    change_order_id: uuid.UUID | None = None
    catalog_version_id: uuid.UUID | None = None
    currency: str
    subtotal: Decimal
    tax_amount: Decimal | None = None
    total_amount: Decimal
    snapshot: dict
    created_by: uuid.UUID | None = None
    created_at: datetime | None = None


class TaxDeterminationCreate(BaseModel):
    """A tax RESULT attached to an order (doc L4/L6). 0.00 is a valid amount — exempt,
    zero-rated, reverse-charge and out-of-scope supplies are real determinations — but the
    treatment/jurisdiction/source that justify it are mandatory, so a zero can never be
    reached by omission. Vocabularies are Finance/Tax's; only presence is validated here."""

    tax_amount: Decimal = Field(..., ge=0)
    treatment: str = Field(..., min_length=1, max_length=60)
    jurisdiction: str = Field(..., min_length=1, max_length=80)
    source: str = Field(..., min_length=1, max_length=80)
    rule_version: str | None = Field(None, max_length=60)
    # REQUIRED when tax_amount == 0 (enforced in crud.record_tax_determination): the reason a
    # zero is zero is what separates a determined zero from an undetermined one.
    exemption_reason: str | None = Field(None, max_length=200)
    effective_at: datetime | None = None


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
    # Always present on an issued invoice — issuance is blocked without a determination.
    # These are a snapshot as at issue time, not a live read of the order.
    tax_amount: Decimal
    tax_treatment: str | None = None
    tax_jurisdiction: str | None = None
    tax_source: str | None = None
    tax_rule_version: str | None = None
    tax_effective_at: datetime | None = None
    tax_exemption_reason: str | None = None
    total_amount: Decimal
    issue_date: datetime | None = None
    due_date: datetime | None = None
    state: str
    document_reference: str | None = None
    created_at: datetime | None = None


# ── Change orders ─────────────────────────────────────────────────────────────────────

class ChangeOrderCreate(BaseModel):
    """`changes` must carry catalog-backed line operations:

        {"add_lines": [{"catalog_line_id": ..., "quantity": "2", "is_addon": false}],
         "remove_line_ids": [order_line_id, ...]}

    `price_delta` is DERIVED from those lines. Send it only to assert what you expect — a value
    that disagrees with the computed delta is refused rather than silently substituted. Omit it
    (null) to accept the computed figure.
    """
    changes: dict = Field(default_factory=dict)
    price_delta: Decimal | None = None
    # Mandatory: a commercial delta with no stated grounds is not auditable (doc Section 25).
    reason: str = Field(..., min_length=3, max_length=2000)
    service_impact: str | None = None
    risk_impact: str | None = None
    capacity_impact: str | None = None


class ChangeOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_order_id: uuid.UUID
    prior_order_version: int
    changes: dict
    # The line rows actually created/destroyed at acceptance. None while still draft.
    applied_lines: dict | None = None
    price_delta: Decimal
    reason: str | None = None
    service_impact: str | None = None
    risk_impact: str | None = None
    capacity_impact: str | None = None
    customer_acceptance: bool
    requested_by: uuid.UUID | None = None
    accepted_at: datetime | None = None
    approved_by: uuid.UUID | None = None
    # The approved exception that authorised a revenue reduction, if this is a decrease.
    approval_exception_id: uuid.UUID | None = None
    effective_at: datetime | None = None
    status: str
    created_at: datetime | None = None


# ── Cancellation ──────────────────────────────────────────────────────────────────────

class CancelOrderRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class CancellationResult(BaseModel):
    order: OrderOut
    policy_version: str
    # What will actually be returned: the policy entitlement capped at the refundable cash.
    refund_amount: Decimal
    # The uncapped policy entitlement. Surfaced separately because when the two differ, that
    # difference is a real commercial fact — the customer is entitled to more than we hold —
    # and collapsing them would hide it from Finance.
    policy_refund_amount: Decimal | None = None
    # First remedy, kept for existing clients. A cancellation now raises one remedy per source
    # payment, so `refund_credit_ids` is the complete list.
    refund_credit_id: uuid.UUID | None = None
    refund_credit_ids: list[uuid.UUID] = []


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
    prepared_by: uuid.UUID | None = None
    prepared_at: datetime | None = None
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
    prepared_by: uuid.UUID | None = None
    prepared_at: datetime | None = None
    proposed_status: str | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class ExceptionResolveCreate(BaseModel):
    status: Literal["resolved", "accepted_risk", "investigating"]
    resolution_notes: str | None = None


class ExceptionConfirmCreate(BaseModel):
    """Body for the maker-checker CONFIRM step. No `status` field — confirming ratifies
    whatever the maker proposed (`proposed_status`), it never chooses a different outcome."""
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


class ReplayWithdrawIn(BaseModel):
    """Why a replay was withdrawn. Free text, shown to the asset owner and publishers, so it
    is length-capped and never rendered as HTML."""

    reason: str | None = Field(None, max_length=200)


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
    # Was list[PaymentOut], fed by a query on `Payment.state == "unmatched"` — a state nothing
    # ever writes, so this array was permanently empty. Unmatched money is an
    # UnmatchedSettlement row, which is what the field now carries.
    unmatched_settlements: list[UnmatchedSettlementOut]
    orders_missing_invoice: list[OrderOut]
    # Accepted commercial orders with no active registered seller entity: chargeable today,
    # un-invoiceable forever (doc L1).
    orders_missing_seller_entity: list[OrderOut] = []
