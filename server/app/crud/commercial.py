"""DB access + business logic for the Live Events commercial/billing layer
(ZST-LE-COM-001). Mirrors crud/event.py's shape: pure queries and orchestration, no HTTP.

Scope note: this module implements the concrete, testable rules from the doc's Section 27
(data model) and Section 28 (state machines) — the parts a schema and a state transition
can actually enforce. It does not implement the ~150 decision-point table (Sections 5-24)
as individual code paths; those are business policy for Commercial/Finance/Legal to
configure via the registries below (CatalogVersion, ServiceProfile, CancellationPolicy),
not logic to hard-code here (doc: "No values are hard-coded into the application").

"vertical" mapping: the doc keys catalog/service-profile/cancellation-policy rows by
"vertical" (memorials, worship, weddings, ...). Event has no dedicated vertical field —
this module reuses Event.category for that purpose rather than adding a parallel column,
since category is already free-text and unused for anything commercial today.

Maker-checker (doc Section 25): enforced here, not in the schema layer — approve_* and
execute_* helpers below reject an approver who is also the requester.
"""

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func as sa_func, select, text
from sqlalchemy.orm import Session

from .event import elevated_risk_tier
from ..models import (
    AuditLog,
    CancellationPolicy,
    CapacityPool,
    CapacityReservation,
    CatalogLine,
    CatalogVersion,
    ChangeOrder,
    CommercialAccount,
    CommercialException,
    CommercialStateTransition,
    ContributorSession,
    Event,
    EventAssignment,
    EventIncident,
    EventOrder,
    EventOrderLine,
    EventOrderVersion,
    EventReschedule,
    FinancialPeriod,
    Invoice,
    Payment,
    PaymentDispute,
    PaymentSchedule,
    CAPACITY_AUDIT_EVENTS,
    COMMERCIAL_LIFECYCLE_STATES,
    COMMERCIAL_LIFECYCLE_TRANSITIONS,
    EXCEPTION_TYPES,
    PAYMENT_STATES,
    PAYMENT_TRANSITIONS,
    ProviderEvent,
    Quote,
    ReadinessCheck,
    ReconciliationException,
    RefundCredit,
    ReplayEntitlement,
    SellerLegalEntity,
    ServiceProfile,
    UnmatchedSettlement,
    User,
)
from ..services import payments as payment_svc
from ..services import platform_settings


# ── Audit trail (reuses the existing platform-wide AuditLog — doc's "audit_event") ──────

def audit(db: Session, *, actor: User | None, action: str, target_type: str, target_id,
          org_id=None, reason: str | None = None, correlation_id: str | None = None,
          **meta) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        org_id=org_id,
        correlation_id=correlation_id,
        meta={"reason": reason, **meta} if reason else (meta or None),
    )
    db.add(entry)
    return entry


def new_idempotency_key() -> str:
    return secrets.token_urlsafe(24)


def new_correlation_id() -> str:
    """One id threaded through a single financial operation's whole chain (doc Section 30).
    Generated at the entry point (webhook receipt, authorize, capture) and passed down, so
    every AuditLog row and ProviderEvent for that operation shares it."""
    return secrets.token_urlsafe(16)


def _order_org_id(db: Session, order_id) -> uuid.UUID | None:
    """Tenant of an order, for audit scoping. Resolved from the order's own event — never
    from caller-supplied input, which is what keeps an unauthenticated webhook from
    attributing activity to a tenant of its choosing."""
    order = db.get(EventOrder, order_id) if order_id else None
    event = db.get(Event, order.event_id) if order else None
    return event.org_id if event else None


# ── Payment state machine (doc Section 28) ───────────────────────────────────────────────

def payment_transition_error(current: str, new: str) -> str | None:
    """None if `current -> new` is legal, else why not. The single authority for both the
    human path (capture/refund) and the provider path (webhook).

    Same-state is NOT an error here — it is an idempotent no-op, and callers must check
    `current == new` themselves before applying anything, so a redelivered event does not
    re-run side effects (see is_same_state_replay)."""
    if current == new:
        return None
    allowed = PAYMENT_TRANSITIONS.get(current)
    if allowed is None:
        return f"unknown payment state '{current}'"
    if new not in PAYMENT_STATES:
        return f"unknown target payment state '{new}'"
    if new not in allowed:
        return (
            f"illegal payment transition '{current}' -> '{new}' "
            f"(allowed from '{current}': {', '.join(allowed) or 'none — terminal state'})"
        )
    return None


def apply_payment_state(db: Session, payment: Payment, new_state: str, *, actor: User | None = None,
                         correlation_id: str | None = None, source: str = "system",
                         **audit_meta) -> tuple[bool, str | None]:
    """Move a Payment through the state machine, or refuse. Returns (applied, error).

    The ONLY function that writes Payment.state outside authorize (which creates the row).
    A refusal mutates nothing and is audited — an illegal transition is evidence of either a
    provider bug or an attack, and must not be silently swallowed.
    """
    previous = payment.state
    if previous == new_state:
        # Idempotent replay: already there. No mutation, no double side effect.
        return False, None
    error = payment_transition_error(previous, new_state)
    if error:
        audit(db, actor=actor, action="commercial.payment.transition_rejected", target_type="payment",
              target_id=payment.id, org_id=_order_org_id(db, payment.event_order_id),
              correlation_id=correlation_id, from_state=previous, to_state=new_state,
              source=source, reason=error, **audit_meta)
        return False, error
    payment.state = new_state
    now = datetime.now(timezone.utc)
    # Timestamps are additive evidence: set once, never cleared by a later transition, so the
    # authorize/capture/settle lineage survives a refund or a dispute (doc D3).
    if new_state in ("paid", "partially_paid") and payment.settled_at is None:
        payment.settled_at = now
        if payment.captured_at is None:
            payment.captured_at = now
    audit(db, actor=actor, action="commercial.payment.transition", target_type="payment",
          target_id=payment.id, org_id=_order_org_id(db, payment.event_order_id),
          correlation_id=correlation_id, from_state=previous, to_state=new_state,
          amount=str(payment.amount), currency=payment.currency, source=source, **audit_meta)
    return True, None


# ── Catalog registry (doc T1, Section 26 "no hard-coded fallback price") ────────────────

def list_catalog_versions(db: Session, vertical: str | None = None, status: str | None = None,
                           published_only: bool = False):
    """`published_only` is what non-Zoiko callers get: draft and retired versions are
    unapproved or withdrawn pricing and must not be visible to customers (doc B2 — only
    approved registry prices are sales-visible; doc T1 — catalog publishing is a controlled
    release). Previously every authenticated user could read every draft price book."""
    stmt = select(CatalogVersion).order_by(CatalogVersion.created_at.desc())
    if vertical:
        stmt = stmt.where(CatalogVersion.vertical == vertical)
    if published_only:
        stmt = stmt.where(CatalogVersion.status == "published")
    elif status:
        stmt = stmt.where(CatalogVersion.status == status)
    return db.scalars(stmt).all()


def create_catalog_version(db: Session, *, vertical: str, version_label: str, notes: str | None) -> CatalogVersion:
    cv = CatalogVersion(vertical=vertical, version_label=version_label, notes=notes, status="draft")
    db.add(cv)
    db.commit()
    db.refresh(cv)
    return cv


def add_catalog_line(db: Session, catalog_version: CatalogVersion, *, service_code: str, name: str,
                      unit_price: Decimal | None, currency: str | None, unit_basis: str = "per_event",
                      description: str | None = None, tax_treatment: str | None = None,
                      is_addon: bool = False) -> CatalogLine:
    if catalog_version.status != "draft":
        raise ValueError("Only a draft catalog version can have lines added")
    line = CatalogLine(
        catalog_version_id=catalog_version.id, service_code=service_code, name=name,
        description=description, unit_basis=unit_basis, unit_price=unit_price, currency=currency,
        tax_treatment=tax_treatment, is_addon=is_addon,
    )
    db.add(line)
    db.commit()
    db.refresh(line)
    return line


def publish_catalog_version(db: Session, catalog_version: CatalogVersion, actor: User) -> CatalogVersion:
    """doc Section 26 checklist item: 'No hard-coded fallback price, tax, discount, deposit
    percentage or service credit exists' — refuse to publish a version carrying any line
    with no price/currency, since that's exactly the invented-value failure mode."""
    unpriced = [l for l in catalog_version.lines if l.unit_price is None or not l.currency]
    if unpriced:
        raise ValueError(f"{len(unpriced)} catalog line(s) have no price/currency set — cannot publish")
    catalog_version.status = "published"
    catalog_version.effective_at = datetime.now(timezone.utc)
    catalog_version.published_by = actor.id
    catalog_version.published_at = datetime.now(timezone.utc)
    audit(db, actor=actor, action="commercial.catalog.publish", target_type="catalog_version",
          target_id=catalog_version.id, vertical=catalog_version.vertical)
    db.commit()
    db.refresh(catalog_version)
    return catalog_version


# ── Service profiles (doc Section 10/F — risk tier is not a marketing label) ────────────

def list_service_profiles(db: Session, risk_tier: str | None = None, status: str | None = None):
    stmt = select(ServiceProfile).order_by(ServiceProfile.created_at.desc())
    if risk_tier:
        stmt = stmt.where(ServiceProfile.risk_tier == risk_tier)
    if status:
        stmt = stmt.where(ServiceProfile.status == status)
    return db.scalars(stmt).all()


def create_service_profile(db: Session, **fields) -> ServiceProfile:
    profile = ServiceProfile(**fields, status="draft")
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def publish_service_profile(db: Session, profile: ServiceProfile, actor: User) -> ServiceProfile:
    profile.status = "published"
    profile.effective_at = datetime.now(timezone.utc)
    audit(db, actor=actor, action="commercial.service_profile.publish", target_type="service_profile",
          target_id=profile.id, risk_tier=profile.risk_tier)
    db.commit()
    db.refresh(profile)
    return profile


# ── Cancellation policy (doc E1 — "No code or support macro may invent a percentage") ───

def list_cancellation_policies(db: Session, vertical: str | None = None, status: str | None = None):
    stmt = select(CancellationPolicy).order_by(CancellationPolicy.created_at.desc())
    if vertical:
        stmt = stmt.where(CancellationPolicy.vertical == vertical)
    if status:
        stmt = stmt.where(CancellationPolicy.status == status)
    return db.scalars(stmt).all()


def create_cancellation_policy(db: Session, **fields) -> CancellationPolicy:
    policy = CancellationPolicy(**fields, status="draft")
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


def publish_cancellation_policy(db: Session, policy: CancellationPolicy, actor: User) -> CancellationPolicy:
    if policy.refund_percentage is None:
        raise ValueError("Cannot publish a cancellation policy with no refund_percentage configured")
    policy.status = "published"
    policy.effective_at = datetime.now(timezone.utc)
    audit(db, actor=actor, action="commercial.cancellation_policy.publish", target_type="cancellation_policy",
          target_id=policy.id, vertical=policy.vertical)
    db.commit()
    db.refresh(policy)
    return policy


def find_cancellation_policy(db: Session, *, vertical: str, risk_tier: str, lead_time_hours: float) -> CancellationPolicy | None:
    """Returns None (never a guessed percentage) when no published row matches — the
    caller must fail closed, exactly as doc E1 requires."""
    candidates = db.scalars(
        select(CancellationPolicy).where(
            CancellationPolicy.vertical == vertical,
            CancellationPolicy.status == "published",
            CancellationPolicy.lead_time_min_hours <= lead_time_hours,
        )
    ).all()
    matches = [
        p for p in candidates
        if (p.risk_tier is None or p.risk_tier == risk_tier)
        and (p.lead_time_max_hours is None or lead_time_hours < p.lead_time_max_hours)
    ]
    if not matches:
        return None
    # Prefer a risk-tier-specific row over a blanket (risk_tier=NULL) one.
    matches.sort(key=lambda p: (p.risk_tier is None, p.created_at), reverse=False)
    return matches[0]


# ── Commercial account (doc A1) ──────────────────────────────────────────────────────────

def get_or_create_commercial_account(db: Session, org_id) -> CommercialAccount:
    account = db.scalar(select(CommercialAccount).where(CommercialAccount.org_id == org_id))
    if account is not None:
        return account
    account = CommercialAccount(org_id=org_id)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


# ── Quote (doc Section 6/B, state machine: DRAFT->ISSUED->ACCEPTED|EXPIRED|WITHDRAWN|SUPERSEDED)

def create_quote(db: Session, event: Event, *, catalog_version: CatalogVersion, amount: Decimal,
                  tax_amount: Decimal | None, currency: str, created_by: User, valid_until: datetime | None,
                  notes: str | None = None) -> Quote:
    """`tax_amount=None` means tax is not yet determined and the quote says so (doc L4);
    it is never coerced to zero. The catalog version must be published — a quote is a
    sales-visible price, so it may only come from an approved price book (doc B2)."""
    if catalog_version.status != "published":
        raise ValueError(
            f"Catalog version '{catalog_version.version_label}' is '{catalog_version.status}', not "
            "published — a quote may only be priced from an approved, published catalog (doc B2)"
        )
    prior = db.scalars(
        select(Quote).where(Quote.event_id == event.id).order_by(Quote.version.desc())
    ).first()
    quote = Quote(
        event_id=event.id, version=(prior.version + 1 if prior else 1),
        catalog_version_id=catalog_version.id, currency=currency, amount=amount, tax_amount=tax_amount,
        commercial_notes=notes, status="draft", valid_until=valid_until, created_by=created_by.id,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


def issue_quote(db: Session, quote: Quote) -> Quote:
    if quote.status != "draft":
        raise ValueError(f"Cannot issue a quote in status '{quote.status}'")
    quote.status = "issued"
    quote.issued_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(quote)
    return quote


def accept_quote(db: Session, quote: Quote, *, actor: User | None = None) -> Quote:
    """Only ACCEPTED can seed an order; a superseded/expired quote can never later be
    accepted (doc Section 28)."""
    if quote.status != "issued":
        raise ValueError(f"Cannot accept a quote in status '{quote.status}' — it must be issued first")
    if quote.valid_until and quote.valid_until < datetime.now(timezone.utc):
        quote.status = "expired"
        db.commit()
        raise ValueError("Quote has expired")
    quote.status = "accepted"
    quote.accepted_at = datetime.now(timezone.utc)
    event = db.get(Event, quote.event_id)
    if event is not None:
        audit(db, actor=actor, action="commercial.quote.accept", target_type="commercial_quote",
              target_id=quote.id, org_id=event.org_id, amount=str(quote.amount),
              currency=quote.currency, quote_version=quote.version)
        sync_lifecycle(db, event, get_current_order(db, event.id), actor=actor,
                        trigger="quote.accept")
    db.commit()
    db.refresh(quote)
    return quote


def supersede_open_quotes(db: Session, event_id, except_quote_id=None) -> None:
    """A fresh quote supersedes any still-open ones for the same event so exactly one can
    ever be accepted (billing invariant #1's quote-side counterpart)."""
    open_quotes = db.scalars(
        select(Quote).where(
            Quote.event_id == event_id, Quote.status.in_(("draft", "issued")),
            Quote.id != except_quote_id if except_quote_id else True,
        )
    ).all()
    for q in open_quotes:
        q.status = "superseded"
    db.commit()


# ── Event order (doc Section 7/C, Section 28: DRAFT->PENDING_ACCEPTANCE->ACCEPTED->ACTIVE)

def get_current_order(db: Session, event_id) -> EventOrder | None:
    """The one order version considered "effective" for the event right now (billing
    invariant #1) — prefers active, then accepted, then the most recent of anything else."""
    orders = db.scalars(
        select(EventOrder).where(EventOrder.event_id == event_id).order_by(EventOrder.order_version.desc())
    ).all()
    if not orders:
        return None
    for wanted in ("active", "accepted", "pending_acceptance", "draft"):
        for o in orders:
            if o.status == wanted:
                return o
    return orders[0]


def create_order(db: Session, event: Event, *, commercial_account: CommercialAccount,
                  catalog_version: CatalogVersion, purchaser_type: str, purchaser_id,
                  service_profile: ServiceProfile | None, cancellation_policy: CancellationPolicy | None,
                  currency: str, idempotency_key: str, quote: Quote | None = None,
                  billing_classification: str = "commercial", billing_source: str = "direct_zoikostream") -> EventOrder:
    """Idempotent: replaying the same idempotency_key returns the existing order instead of
    creating a duplicate (doc Section 4 P0 blocker #10, Section 26 checklist).

    The catalog version must be PUBLISHED (doc B2: "Missing active catalog mapping blocks
    checkout/quote acceptance"). Without this an order could be priced from a draft or
    retired price book — i.e. from unapproved commercial values."""
    if catalog_version.status != "published":
        raise ValueError(
            f"Catalog version '{catalog_version.version_label}' is '{catalog_version.status}', not "
            "published — an order may only be priced from an approved, published catalog (doc B2)"
        )
    existing = db.scalar(select(EventOrder).where(EventOrder.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    order = EventOrder(
        event_id=event.id, commercial_account_id=commercial_account.id,
        catalog_version_id=catalog_version.id, quote_id=quote.id if quote else None,
        purchaser_type=purchaser_type, purchaser_id=purchaser_id,
        service_profile_id=service_profile.id if service_profile else None,
        cancellation_policy_id=cancellation_policy.id if cancellation_policy else None,
        # tax_amount is deliberately NOT set: NULL means "no tax determination yet", which
        # issue_invoice refuses to invoice against (doc L4). It used to be initialized to 0
        # and never recomputed, which structurally produced zero-tax invoices.
        currency=currency, subtotal=0, total_amount=0, status="draft",
        risk_tier=service_profile.risk_tier if service_profile else "r0",
        billing_classification=billing_classification, billing_source=billing_source,
        idempotency_key=idempotency_key,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def add_order_line(db: Session, order: EventOrder, catalog_line: CatalogLine, *, quantity: Decimal = Decimal(1),
                    is_addon: bool = False, is_complimentary: bool = False) -> EventOrderLine:
    """Freezes the catalog line's price into the order line at add-time (doc B2, billing
    invariant #9: a later catalog edit can never retroactively rewrite an accepted order).
    Adding lines to an order that's already ACCEPTED must go through a ChangeOrder instead
    (doc G1) — enforced here, not left to the caller to remember."""
    if order.status not in ("draft", "pending_acceptance"):
        raise ValueError("Order already accepted — use a ChangeOrder to modify its scope (doc G1)")
    # The line must come from THIS order's catalog version. Without this check the router
    # could hand over any CatalogLine by id, so a price could silently originate from a
    # different — possibly draft or retired — price book than the one the order records,
    # breaking the traceability chain (order.catalog_version_id -> line -> price).
    if catalog_line.catalog_version_id != order.catalog_version_id:
        raise ValueError(
            "Catalog line belongs to a different catalog version than this order — a line's price "
            "must be traceable to the order's own approved catalog version (doc B1/B2)"
        )
    if catalog_line.unit_price is None or not catalog_line.currency:
        raise ValueError("Catalog line has no price/currency set — cannot add to an order (doc B2)")
    # One currency per legal financial document (doc L2). A GBP-priced line on a USD order
    # would produce a subtotal in no currency at all.
    if catalog_line.currency != order.currency:
        raise ValueError(
            f"Catalog line is priced in {catalog_line.currency} but this order is in "
            f"{order.currency} — one currency per order/document (doc L2)"
        )
    unit_price = Decimal(0) if is_complimentary else catalog_line.unit_price
    line_total = unit_price * quantity
    line = EventOrderLine(
        event_order_id=order.id, catalog_line_id=catalog_line.id, service_code=catalog_line.service_code,
        description=catalog_line.name, quantity=quantity, unit_price=unit_price, line_total=line_total,
        tax_treatment=catalog_line.tax_treatment, unit_basis=catalog_line.unit_basis,
        is_addon=is_addon, is_complimentary=is_complimentary,
    )
    db.add(line)
    order.subtotal = (order.subtotal or Decimal(0)) + line_total
    # Provisional total while tax is undetermined (tax_amount IS NULL). Any existing
    # determination is invalidated by a scope change — the tax basis was computed against a
    # different subtotal — so clear it and force a re-determination before invoicing.
    if order.tax_amount is not None:
        _clear_tax_determination(order)
    order.total_amount = order.subtotal
    db.commit()
    db.refresh(line)
    return line


def submit_order_for_acceptance(db: Session, order: EventOrder) -> EventOrder:
    if order.status != "draft":
        raise ValueError(f"Cannot submit an order in status '{order.status}'")
    if not order.lines:
        raise ValueError("Cannot submit an order with no lines (doc B1: no charge without an order line)")
    order.status = "pending_acceptance"
    db.commit()
    db.refresh(order)
    return order


def accept_order(db: Session, order: EventOrder, actor: User, *, terms_version: str | None = None) -> EventOrder:
    """Enforces billing invariant #1: exactly one accepted order version is effective for
    an event at a time — refuses acceptance if another order for the same event is already
    accepted/active."""
    if order.status != "pending_acceptance":
        raise ValueError(f"Cannot accept an order in status '{order.status}'")
    other_effective = db.scalar(
        select(EventOrder).where(
            EventOrder.event_id == order.event_id, EventOrder.id != order.id,
            EventOrder.status.in_(("accepted", "active")),
        )
    )
    if other_effective is not None:
        raise ValueError("Another order is already accepted/active for this event")

    # ── Service profile binding (doc Section 10/F) ──
    # Validated only when a profile IS attached: whether one is REQUIRED is a risk-tier
    # question, and it is enforced at the readiness gate (evaluate_readiness) rather than here,
    # because a profile is legitimately assigned after acceptance while the event is scoped.
    # What must never happen is acceptance against a draft/retired profile or one for a
    # different tier — that would bind the order to controls nobody approved.
    profile = db.get(ServiceProfile, order.service_profile_id) if order.service_profile_id else None
    if profile is not None:
        if profile.status != "published":
            raise ValueError(
                f"Service profile '{profile.version_label}' is '{profile.status}', not published — "
                "an order may only be bound to an approved operational profile (doc F1)"
            )
        if profile.risk_tier != order.risk_tier:
            raise ValueError(
                f"Service profile '{profile.version_label}' is for {profile.risk_tier.upper()} but "
                f"this order is {order.risk_tier.upper()} — the profile defines the tier's controls "
                "and the two cannot disagree (doc Section 10)"
            )

    # ── Assured Event election (doc Section 10) ──
    if getattr(order, "assured_event", False):
        if profile is None:
            raise ValueError(
                "An Assured Event order must be bound to a service profile — the assurance is the "
                "profile's control set, not a label"
            )
        if not profile.assured_event_eligible:
            raise ValueError(
                f"Service profile '{profile.version_label}' is not Assured-Event-eligible. Assured "
                "Event cannot be elected against a profile Commercial has not approved for it"
            )

    # ── Currency must be one the selling entity can actually invoice (doc L2) ──
    # Checked here as well as at invoice time. The invoice check alone was too late: by then the
    # order had been accepted AND paid in a currency the seller entity cannot issue a document
    # in, leaving collected money that can never be invoiced.
    account = db.get(CommercialAccount, order.commercial_account_id)
    if account is not None and account.seller_legal_entity_id:
        entity = db.scalar(
            select(SellerLegalEntity).where(SellerLegalEntity.code == account.seller_legal_entity_id)
        )
        if entity is not None and entity.default_currency and order.currency != entity.default_currency:
            if order.currency not in (entity.supported_currencies or []):
                raise ValueError(
                    f"Order currency {order.currency} is not supported by seller entity "
                    f"'{entity.code}' (default {entity.default_currency}). Accepting it would "
                    "produce an order that cannot be invoiced (doc L2)"
                )

    order.status = "accepted"
    order.accepted_at = datetime.now(timezone.utc)
    order.accepted_by = actor.id
    order.terms_version = terms_version
    # Keep Event's commercial classification in sync with its effective order (doc S1).
    event = db.get(Event, order.event_id)
    if event is not None:
        event.billing_classification = order.billing_classification
        event.billing_source = order.billing_source
        # An order can raise an event's risk tier but never accept it below the category's
        # floor (doc: memorials "cannot be downgraded below the category minimum").
        event.risk_tier = elevated_risk_tier(event.category, order.risk_tier)
        event.service_profile_id = order.service_profile_id
        event.commercial_account_id = order.commercial_account_id
    snapshot_order_version(db, order, actor=actor)
    audit(db, actor=actor, action="commercial.order.accept", target_type="event_order", target_id=order.id,
          org_id=event.org_id if event else None, total_amount=str(order.total_amount),
          order_version=order.order_version, risk_tier=order.risk_tier,
          assured_event=getattr(order, "assured_event", False),
          service_profile_id=None if not order.service_profile_id else str(order.service_profile_id))
    if event is not None:
        sync_lifecycle(db, event, order, actor=actor, trigger="order.accept")
    db.commit()
    db.refresh(order)
    return order


# ── Immutable order versions (doc Section 27 event_order_version, Section 28) ─────────────

def snapshot_order_version(db: Session, order: EventOrder, *, actor: User | None = None,
                            change_order: ChangeOrder | None = None) -> EventOrderVersion:
    """Freeze the order's current commercial state as an immutable EventOrderVersion row.

    Called on acceptance and on every applied change order. Idempotent per
    (order, order_version): re-running returns the existing row rather than writing a
    second snapshot of the same version, so a retried acceptance cannot fork history.

    The snapshot is a self-contained copy of the header + every line — not a set of FKs —
    because the point is to survive later mutation of the live rows (doc T4: "A policy change
    must not rewrite historical customer obligations")."""
    existing = db.scalar(
        select(EventOrderVersion).where(
            EventOrderVersion.event_order_id == order.id,
            EventOrderVersion.order_version == order.order_version,
        )
    )
    if existing is not None:
        return existing
    version = EventOrderVersion(
        event_order_id=order.id, order_version=order.order_version,
        change_order_id=change_order.id if change_order else None,
        catalog_version_id=order.catalog_version_id, currency=order.currency,
        subtotal=order.subtotal or Decimal(0), tax_amount=order.tax_amount,
        total_amount=order.total_amount or Decimal(0),
        created_by=actor.id if actor else None,
        snapshot={
            "order": {
                "id": str(order.id),
                "order_version": order.order_version,
                "status": order.status,
                "currency": order.currency,
                "subtotal": str(order.subtotal or Decimal(0)),
                "tax_amount": None if order.tax_amount is None else str(order.tax_amount),
                "tax_treatment": order.tax_treatment,
                "tax_jurisdiction": order.tax_jurisdiction,
                "tax_source": order.tax_source,
                "tax_rule_version": order.tax_rule_version,
                "tax_exemption_reason": order.tax_exemption_reason,
                "total_amount": str(order.total_amount or Decimal(0)),
                "risk_tier": order.risk_tier,
                "billing_classification": order.billing_classification,
                "billing_source": order.billing_source,
                "terms_version": order.terms_version,
                "catalog_version_id": str(order.catalog_version_id),
                "service_profile_id": None if order.service_profile_id is None else str(order.service_profile_id),
                "cancellation_policy_id": (
                    None if order.cancellation_policy_id is None else str(order.cancellation_policy_id)
                ),
                "accepted_at": order.accepted_at.isoformat() if order.accepted_at else None,
                "accepted_by": None if order.accepted_by is None else str(order.accepted_by),
            },
            "lines": [
                {
                    "id": str(line.id),
                    "catalog_line_id": None if line.catalog_line_id is None else str(line.catalog_line_id),
                    "service_code": line.service_code,
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                    "unit_basis": line.unit_basis,
                    "line_total": str(line.line_total),
                    "tax_treatment": line.tax_treatment,
                    "is_addon": line.is_addon,
                    "is_complimentary": line.is_complimentary,
                }
                for line in order.lines
            ],
        },
    )
    db.add(version)
    return version


def list_order_versions(db: Session, order_id) -> list[EventOrderVersion]:
    return db.scalars(
        select(EventOrderVersion)
        .where(EventOrderVersion.event_order_id == order_id)
        .order_by(EventOrderVersion.order_version)
    ).all()


def activate_order(db: Session, order: EventOrder, *, actor: User | None = None) -> EventOrder:
    """ACCEPTED -> ACTIVE. The full confirmation gate — no partial activation.

    This originally checked ONLY `status == "accepted"` while its own docstring claimed the
    router had already confirmed capacity and payment. It hadn't, so ACTIVE was reachable with
    no reserved capacity and an overdue deposit, and the status told an operator nothing.

    ACTIVE now means CONFIRMED-and-READY: the order is the platform's committed promise to
    deliver, so every gate that go-live consults must already hold. The four refusals are
    raised individually rather than as one combined verdict, because "activation failed" is
    useless to an operator — they need to know WHICH gate:

      1. capacity      — required resources are not hard-reserved (doc B4/C3)
      2. financial     — a required milestone is overdue (doc D2)
      3. profile       — the risk tier's service profile is missing/unpublished/mismatched
      4. readiness     — a mandatory readiness check has not passed (doc I3)

    Gates 3 and 4 come from evaluate_readiness, which is the same authority golive_block_reason
    uses — so ACTIVE and go-live can never disagree about whether an event is deliverable.
    That is the point: it closes the path where an order was marked ACTIVE, everyone treated it
    as confirmed, and go-live then refused it.

    An approved, correctly-scoped CommercialException clears its gate here exactly as it does
    at go-live (evaluate_readiness reports it under `exceptions_applied`) — the governed
    override path, not a bypass.
    """
    if order.status != "accepted":
        raise ValueError(f"Cannot activate an order in status '{order.status}'")
    event = db.get(Event, order.event_id)
    if event is None:
        raise ValueError("Order references an event that no longer exists")

    # ── 1. Capacity ──
    if not capacity_confirmed(db, event):
        raise ValueError(
            "Cannot activate: the operational capacity this order's service profile requires is "
            "not hard-reserved. Paying for an event does not confirm it (doc B4) — reserve "
            "capacity first, or an active order would promise delivery the platform has not "
            "committed to"
        )
    # ── 2. Financial readiness ──
    financial_state = financial_readiness_state(db, order)
    if financial_state == "financial_hold":
        raise ValueError(
            "Cannot activate: this order is in financial hold (a required payment milestone is "
            "overdue). Collect it, or raise and approve a `financial_hold_override` commercial "
            "exception (doc D2)"
        )
    if financial_state == "due":
        raise ValueError(
            "Cannot activate: a required payment milestone is still outstanding. An ACTIVE order "
            "is a delivery commitment, so the money behind it must be settled, credited or "
            "covered by an approved exception first (doc D2)"
        )

    # ── 3 & 4. Service profile requirements + mandatory readiness checks ──
    evaluation = evaluate_readiness(db, event, order)
    if not evaluation["ready"]:
        # Financial/capacity already passed above, so anything left is a profile or readiness
        # gate. Reported verbatim: the operator needs the specific outstanding item.
        raise ValueError(
            "Cannot activate: readiness is not satisfied — "
            + "; ".join(evaluation["blocking_reasons"])
        )

    order.status = "active"
    audit(db, actor=actor, action="commercial.order.activate", target_type="event_order",
          target_id=order.id, org_id=event.org_id, financial_state=financial_state,
          order_version=order.order_version, readiness_verdict=evaluation["verdict"],
          exceptions_applied=evaluation.get("exceptions_applied") or None)
    sync_lifecycle(db, event, order, actor=actor, trigger="order.activate",
                    reason="all confirmation gates satisfied", evaluation=evaluation)
    db.commit()
    db.refresh(order)
    return order


# ── Canonical commercial lifecycle (doc Section 28) ──────────────────────────────────────
# DRAFT -> QUOTED -> ORDER_ACCEPTED -> FINANCIAL_HOLD -> CAPACITY_HELD -> CONFIRMED -> READY
#       -> LIVE -> COMPLETED, plus CANCELED.
#
# DERIVED, never assigned. The facts that determine it already exist and are already
# individually guarded (EventOrder.status through its own transitions, PaymentSchedule through
# capture/allocation, CapacityReservation through the pool lock, Event.status through
# status_transition_error + golive_block_reason). Adding a writable lifecycle column would
# create a second truth that could disagree with all of them — and something that can be
# written can be written to the wrong value. There is no setter here, which is precisely how
# the doc's "no state is manually bypassable" requirement is met: the only way to reach
# CONFIRMED is to satisfy the facts CONFIRMED is defined as.

# Event lifecycle states that mean the commercial engagement is over, one way or another.
_EVENT_STATES_COMPLETED = ("processing", "replay_ready", "ended", "archived")
_EVENT_STATES_LIVE = ("live", "degraded", "ending")


def commercial_lifecycle_state(db: Session, event: Event, order: EventOrder | None = None, *,
                                evaluation: dict | None = None) -> dict:
    """The event's current commercial lifecycle state, derived from committed facts.

    Returns the state plus the evidence behind it, so a caller (and the transition log) can
    answer "why is it here" without re-deriving anything:

        {"state", "financial_state", "capacity_satisfied", "capacity_held",
         "blocking_reasons", "readiness_verdict"}

    `evaluation` lets a caller that has already run evaluate_readiness pass it in rather than
    paying for it twice — the go-live path does exactly that.
    """
    # ── Terminal states first: nothing below can override a cancelled or delivered event ──
    if (order is not None and order.status in ("canceled", "terminated")) or event.status == "cancelled":
        return {"state": "canceled", "financial_state": None, "capacity_satisfied": None,
                "capacity_held": False, "blocking_reasons": [], "readiness_verdict": None}
    if (order is not None and order.status == "completed") or event.status in _EVENT_STATES_COMPLETED:
        return {"state": "completed", "financial_state": None, "capacity_satisfied": None,
                "capacity_held": False, "blocking_reasons": [], "readiness_verdict": None}
    if event.status in _EVENT_STATES_LIVE:
        return {"state": "live", "financial_state": None, "capacity_satisfied": None,
                "capacity_held": False, "blocking_reasons": [], "readiness_verdict": None}

    # ── Pre-delivery: the state is whatever gate is still outstanding ──
    if order is None or order.status in ("draft", "pending_acceptance"):
        # QUOTED means a real, live quote is on the table — a draft quote nobody has issued is
        # not a commercial position, and a superseded/expired one is no longer one either.
        quoted = db.scalar(
            select(Quote.id).where(
                Quote.event_id == event.id,
                Quote.status.in_(("issued", "accepted")),
            )
        ) is not None
        return {"state": "quoted" if quoted else "draft", "financial_state": None,
                "capacity_satisfied": None, "capacity_held": False,
                "blocking_reasons": [], "readiness_verdict": None}

    evaluation = evaluation if evaluation is not None else evaluate_readiness(db, event, order)
    financial_state = financial_readiness_state(db, order)
    capacity_satisfied = capacity_confirmed(db, event)
    # Any hard reservation at all — distinguishes "capacity is being assembled" from "no
    # capacity has been committed", which is the difference between CAPACITY_HELD and
    # ORDER_ACCEPTED.
    capacity_held = db.scalar(
        select(CapacityReservation.id).where(
            CapacityReservation.event_id == event.id,
            CapacityReservation.state.in_(("hard_reserved", "consumed")),
        )
    ) is not None

    base = {
        "financial_state": financial_state,
        "capacity_satisfied": capacity_satisfied,
        "capacity_held": capacity_held,
        "blocking_reasons": list(evaluation.get("blocking_reasons") or []),
        "readiness_verdict": evaluation.get("verdict"),
    }

    if evaluation.get("ready"):
        # READY implies confirmed: evaluate_readiness already required financial readiness and
        # capacity, so this cannot be reached with either outstanding.
        return {"state": "ready", **base}
    if financial_state not in ("satisfied", "approved_exception", "not_due"):
        return {"state": "financial_hold", **base}
    if capacity_satisfied:
        return {"state": "confirmed", **base}
    if capacity_held:
        return {"state": "capacity_held", **base}
    return {"state": "order_accepted", **base}


def sync_lifecycle(db: Session, event: Event, order: EventOrder | None, *, trigger: str,
                    actor: User | None = None, correlation_id: str | None = None,
                    reason: str | None = None,
                    evaluation: dict | None = None) -> dict:
    """Recompute the lifecycle state and record it if it moved. Returns the computed snapshot.

    Called after every operation that can move the state. Does NOT commit — it flushes and
    leaves the transaction to its caller, so the transition lands atomically with the change
    that caused it. A transition can never be recorded for an operation that then rolled back.

    An illegal transition (one COMMERCIAL_LIFECYCLE_TRANSITIONS does not allow) is RECORDED,
    not rejected. The state is derived from facts that have already been individually
    validated, so an unexpected jump means the model's understanding of those facts is
    incomplete — that is worth an alarm in the log, not an exception that would roll back a
    legitimate commercial operation on a bookkeeping technicality.
    """
    snapshot = commercial_lifecycle_state(db, event, order, evaluation=evaluation)
    state = snapshot["state"]
    previous = order.lifecycle_state if order is not None else None
    if previous == state:
        return snapshot

    illegal = not lifecycle_transition_allowed(previous, state)

    db.add(CommercialStateTransition(
        event_order_id=order.id if order is not None else None, event_id=event.id,
        from_state=previous, to_state=state, trigger=trigger, reason=reason, illegal=illegal,
        blocking_reasons=snapshot["blocking_reasons"] or None,
        financial_state=snapshot["financial_state"],
        capacity_satisfied=snapshot["capacity_satisfied"],
        actor_id=actor.id if actor else None, correlation_id=correlation_id,
    ))
    if order is not None:
        order.lifecycle_state = state
    audit(db, actor=actor,
          action="commercial.lifecycle.illegal_transition" if illegal else "commercial.lifecycle.transition",
          target_type="event_order" if order is not None else "event",
          target_id=order.id if order is not None else event.id, org_id=event.org_id,
          correlation_id=correlation_id, from_state=previous, to_state=state, trigger=trigger,
          reason=reason, financial_state=snapshot["financial_state"],
          capacity_satisfied=snapshot["capacity_satisfied"],
          blocking_reasons=snapshot["blocking_reasons"] or None)
    db.flush()
    return snapshot


def lifecycle_transition_allowed(previous: str | None, new: str) -> bool:
    """Whether `previous -> new` is a legal commercial lifecycle move.

    Exposed separately from sync_lifecycle so the forbidden moves the standard names are
    checkable directly — DRAFT->LIVE, QUOTED->COMPLETED and CANCELED->READY are all False here.

    Note how those three are actually PREVENTED rather than merely detected: the state is
    derived, so the only way to reach LIVE is for Event.status to become live, and
    golive_block_reason refuses that for an event whose order is not accepted, paid, resourced
    and ready. This function is the graph's opinion; the gates are the enforcement. Both are
    tested.
    """
    if previous is None:
        return True                      # first observation is not a transition
    if previous == new:
        return True                      # idempotent re-observation
    return new in COMMERCIAL_LIFECYCLE_TRANSITIONS.get(previous, ())


def lifecycle_history(db: Session, *, order_id=None, event_id=None) -> list[CommercialStateTransition]:
    """Append-only lifecycle history, oldest first."""
    stmt = select(CommercialStateTransition).order_by(CommercialStateTransition.created_at)
    if order_id is not None:
        stmt = stmt.where(CommercialStateTransition.event_order_id == order_id)
    if event_id is not None:
        stmt = stmt.where(CommercialStateTransition.event_id == event_id)
    return db.scalars(stmt).all()


# ── Capacity reservation (doc Section 7/C, state: UNREQUESTED->SOFT_HELD->HARD_RESERVED->...)

# ── Capacity inventory (doc C2/C4, Section 4 P0 blocker #8) ──────────────────────────────
# States that HOLD inventory against a pool. `released`/`expired` return it; `unrequested`
# never took any. Kept as one tuple so utilisation and the oversell guard can never disagree
# about what counts.
CAPACITY_HOLDING_STATES = ("soft_held", "hard_reserved", "consumed")


def _capacity_audit(db: Session, *, capacity_event: str, reservation: CapacityReservation,
                     actor: User | None = None, reason: str | None = None,
                     correlation_id: str | None = None, event: Event | None = None,
                     **extra) -> None:
    """Emit one canonical capacity audit event (doc Section 30).

    Every movement of committed inventory goes through here, so the four things an auditor
    needs are structurally guaranteed rather than remembered per call site: the ACTOR, the
    TIMESTAMP (AuditLog.created_at), the EVENT/ORDER it was for, and a REASON. Five separate
    audit calls previously assembled these by hand and disagreed about which fields to
    include — release carried a reason, hard_reserve carried an order, neither carried both.

    `capacity_event` is validated against CAPACITY_AUDIT_EVENTS, so a typo is an error rather
    than a silently unqueryable action string.
    """
    if capacity_event not in CAPACITY_AUDIT_EVENTS:
        raise ValueError(
            f"Unknown capacity audit event '{capacity_event}' "
            f"(allowed: {', '.join(CAPACITY_AUDIT_EVENTS)})"
        )
    ev = event if event is not None else db.get(Event, reservation.event_id)
    audit(db, actor=actor, action=f"commercial.capacity.{capacity_event}",
          target_type="capacity_reservation", target_id=reservation.id,
          org_id=ev.org_id if ev is not None else None, correlation_id=correlation_id,
          reason=reason, capacity_event=capacity_event,
          resource_type=reservation.resource_type, quantity=reservation.quantity,
          state=reservation.state, event_id=str(reservation.event_id),
          event_order_id=(None if not reservation.event_order_id
                          else str(reservation.event_order_id)),
          capacity_pool_id=(None if not reservation.capacity_pool_id
                            else str(reservation.capacity_pool_id)),
          **extra)


def list_capacity_pools(db: Session, *, resource_type: str | None = None, status: str | None = None,
                         seller_legal_entity_id: str | None = None):
    stmt = select(CapacityPool).order_by(CapacityPool.window_start.desc())
    if resource_type:
        stmt = stmt.where(CapacityPool.resource_type == resource_type)
    if status:
        stmt = stmt.where(CapacityPool.status == status)
    if seller_legal_entity_id:
        stmt = stmt.where(CapacityPool.seller_legal_entity_id == seller_legal_entity_id)
    return db.scalars(stmt).all()


def create_capacity_pool(db: Session, actor: User, **fields) -> CapacityPool:
    """Operations-authored inventory, created as `draft`. No pool is seeded anywhere and no
    default size exists — an unconfigured resource type simply has no capacity, and requests
    against it fail closed (doc C4)."""
    if fields["window_end"] <= fields["window_start"]:
        raise ValueError("Capacity pool window_end must be after window_start")
    if fields["total_capacity"] < 0:
        raise ValueError("Capacity pool total_capacity cannot be negative")
    pool = CapacityPool(**fields, status="draft", created_by=actor.id)
    db.add(pool)
    audit(db, actor=actor, action="commercial.capacity_pool.create", target_type="capacity_pool",
          target_id=pool.id, resource_type=fields["resource_type"],
          total_capacity=fields["total_capacity"])
    db.commit()
    db.refresh(pool)
    return pool


def activate_capacity_pool(db: Session, pool: CapacityPool, actor: User) -> CapacityPool:
    pool.status = "active"
    audit(db, actor=actor, action="commercial.capacity_pool.activate", target_type="capacity_pool",
          target_id=pool.id, total_capacity=pool.total_capacity)
    db.commit()
    db.refresh(pool)
    return pool


def pool_utilisation(db: Session, pool: CapacityPool) -> dict:
    """Derived reserved/consumed/available for a pool. Computed from the reservation rows
    rather than stored counters, so the pool and its reservations can never drift apart."""
    rows = db.execute(
        select(CapacityReservation.state, sa_func.coalesce(sa_func.sum(CapacityReservation.quantity), 0))
        .where(
            CapacityReservation.capacity_pool_id == pool.id,
            CapacityReservation.state.in_(CAPACITY_HOLDING_STATES),
        )
        .group_by(CapacityReservation.state)
    ).all()
    by_state = {state: int(total) for state, total in rows}
    held = sum(by_state.values())
    return {
        "pool_id": pool.id,
        "resource_type": pool.resource_type,
        "region": pool.region,
        "window_start": pool.window_start,
        "window_end": pool.window_end,
        "status": pool.status,
        "total_capacity": pool.total_capacity,
        "soft_held": by_state.get("soft_held", 0),
        "hard_reserved": by_state.get("hard_reserved", 0),
        "consumed": by_state.get("consumed", 0),
        "reserved_capacity": held,
        "available_capacity": max(pool.total_capacity - held, 0),
    }


def find_capacity_pool(db: Session, *, resource_type: str, window_start: datetime, window_end: datetime,
                        region: str | None = None, seller_legal_entity_id: str | None = None) -> CapacityPool | None:
    """The ACTIVE pool whose window fully CONTAINS the requested window (doc C1: live events
    consume time-specific resources). A pool that only partially overlaps cannot satisfy the
    request — an operator rostered 09:00-12:00 does not cover a 11:00-14:00 event — so
    containment, not overlap, is the test. Returns None when nothing qualifies; the caller
    must then fail closed rather than assume capacity."""
    stmt = select(CapacityPool).where(
        CapacityPool.resource_type == resource_type,
        CapacityPool.status == "active",
        CapacityPool.window_start <= window_start,
        CapacityPool.window_end >= window_end,
    )
    if region is not None:
        stmt = stmt.where(CapacityPool.region == region)
    if seller_legal_entity_id is not None:
        stmt = stmt.where(CapacityPool.seller_legal_entity_id == seller_legal_entity_id)
    # Tightest window first, so a narrowly-scoped pool is preferred over a broad one.
    return db.scalars(stmt.order_by(CapacityPool.window_start.desc(), CapacityPool.window_end)).first()


def _claim_pool_capacity(db: Session, pool_id, quantity: int) -> CapacityPool:
    """Take a row lock on the pool, then verify headroom. Returns the locked pool.

    SELECT ... FOR UPDATE serializes every concurrent claim against the SAME pool, so the
    read of current utilisation and the insert that changes it cannot interleave: the second
    transaction blocks until the first commits, then re-reads and sees the new total. This is
    what makes oversubscription impossible rather than merely unlikely (doc C4 "the system
    must fail closed when capacity is unavailable").

    ponytail: per-pool row lock, not a table lock — concurrency is only serialized between
    requests competing for the same finite resource, which is exactly where it's needed.
    """
    pool = db.scalars(
        select(CapacityPool).where(CapacityPool.id == pool_id).with_for_update()
    ).first()
    if pool is None:
        raise ValueError("Capacity pool not found")
    if pool.status != "active":
        raise ValueError(f"Capacity pool is '{pool.status}', not active — cannot reserve against it")
    available = pool_utilisation(db, pool)["available_capacity"]
    if quantity > available:
        raise ValueError(
            f"Insufficient capacity: requested {quantity}, {available} available in pool "
            f"{pool.resource_type} ({pool.window_start:%Y-%m-%d %H:%M}-{pool.window_end:%H:%M}). "
            "Capacity cannot be oversold (doc C4)"
        )
    return pool


def soft_hold_capacity(db: Session, event: Event, *, resource_type: str, window_start=None, window_end=None,
                        quantity: int = 1, region: str | None = None, hold_minutes: int = 30,
                        event_order: EventOrder | None = None, actor: User | None = None) -> CapacityReservation:
    """Take a governed, expiring hold against real inventory (doc C2: "Capacity may be
    soft-held for a governed period... Soft holds expire automatically").

    Requires an ACTIVE CapacityPool containing the requested window and having headroom.
    Previously this inserted a reservation row unconditionally, with nothing to check
    against — so capacity could be "held" that did not exist.
    """
    if quantity < 1:
        raise ValueError("Capacity quantity must be at least 1")
    if window_start is None or window_end is None:
        raise ValueError(
            "Capacity requires an explicit window (window_start/window_end) — live event "
            "capacity is a time-specific resource and cannot be held against an open period "
            "(doc C1/C5)"
        )
    if window_end <= window_start:
        raise ValueError("Capacity window_end must be after window_start")

    # Sweep lapsed holds first so their inventory is genuinely free before we measure
    # headroom — otherwise an expired hold would refuse a legitimate new one.
    expire_stale_soft_holds(db)
    pool = find_capacity_pool(db, resource_type=resource_type, window_start=window_start,
                              window_end=window_end, region=region)
    if pool is None:
        raise ValueError(
            f"No active capacity pool covers {resource_type}"
            f"{f' in region {region}' if region else ''} for "
            f"{window_start:%Y-%m-%d %H:%M}-{window_end:%Y-%m-%d %H:%M}. Operations must publish "
            "approved capacity inventory before it can be reserved — no capacity is assumed (doc C4)"
        )
    locked = _claim_pool_capacity(db, pool.id, quantity)
    reservation = CapacityReservation(
        event_id=event.id, event_order_id=event_order.id if event_order else None,
        capacity_pool_id=locked.id, resource_type=resource_type,
        window_start=window_start, window_end=window_end,
        requested_quantity=quantity, quantity=quantity, region=region, state="soft_held",
        soft_hold_expires_at=datetime.now(timezone.utc) + timedelta(minutes=hold_minutes),
        created_by=actor.id if actor else None,
    )
    db.add(reservation)
    db.flush()
    # doc Section 30: capacity is a commercial commitment, so every movement of it is audit
    # evidence. NONE of the reservation transitions were audited before — hold, reserve,
    # consume and release all mutated inventory silently, which left "who committed this
    # operator to this event, and when" unanswerable.
    _capacity_audit(db, capacity_event="soft_hold_created", reservation=reservation,
                     actor=actor, event=event,
                     reason=f"governed {hold_minutes}-minute hold taken against pool {locked.id}",
                     region=region, window_start=window_start.isoformat(),
                     window_end=window_end.isoformat(),
                     expires_at=reservation.soft_hold_expires_at.isoformat())
    db.commit()  # releases the pool row lock
    db.refresh(reservation)
    return reservation


def hard_reserve_capacity(db: Session, reservation: CapacityReservation, order: EventOrder, *,
                           actor: User | None = None,
                           correlation_id: str | None = None) -> CapacityReservation:
    """Requires an accepted/active order — money without operational capacity is not a
    valid delivery commitment, and capacity without an order is equally invalid (doc B4).

    Promotion only: the inventory was already claimed at soft-hold time, so this changes the
    hold's character (and stops it expiring) without taking more capacity. That is why it
    needs no second pool check — soft_held and hard_reserved both count as holding, per
    CAPACITY_HOLDING_STATES.

    Deliberately NOT driven by payment state (doc B4: "Does a deposit mean the event is
    confirmed? Not by itself"). The caller supplies an accepted order; nothing here consults
    a Payment.
    """
    if order.status not in ("accepted", "active"):
        raise ValueError("Order must be accepted before capacity can be hard-reserved (doc B4)")
    if reservation.state != "soft_held":
        raise ValueError(f"Cannot hard-reserve capacity in state '{reservation.state}'")
    if reservation.soft_hold_expires_at and reservation.soft_hold_expires_at < datetime.now(timezone.utc):
        reservation.state = "expired"
        reservation.released_at = datetime.now(timezone.utc)
        reservation.release_reason = "soft_hold_expired"
        db.commit()
        raise ValueError("This soft hold has expired — take a new hold before reserving (doc C2)")
    reservation.event_order_id = order.id
    # Bind to the order VERSION that authorised it, so a later change order leaves an
    # auditable trail of which commercial basis the capacity was committed under.
    current_version = db.scalar(
        select(EventOrderVersion).where(
            EventOrderVersion.event_order_id == order.id,
            EventOrderVersion.order_version == order.order_version,
        )
    )
    if current_version is not None:
        reservation.event_order_version_id = current_version.id
    reservation.state = "hard_reserved"
    reservation.hard_reserved_at = datetime.now(timezone.utc)
    reservation.soft_hold_expires_at = None
    event = db.get(Event, reservation.event_id)
    _capacity_audit(db, capacity_event="hard_reserved", reservation=reservation, actor=actor,
                     event=event, correlation_id=correlation_id,
                     reason=f"committed against accepted order version {order.order_version}",
                     order_version=order.order_version)
    # Committing capacity can complete the CONFIRMED gate, so the lifecycle moves with it.
    if event is not None:
        sync_lifecycle(db, event, order, actor=actor, trigger="capacity.hard_reserve",
                        correlation_id=correlation_id)
    db.commit()
    db.refresh(reservation)
    return reservation


def consume_capacity(db: Session, reservation: CapacityReservation, *,
                      actor: User | None = None) -> CapacityReservation:
    """HARD_RESERVED -> CONSUMED once the event has actually used the resource (doc Section
    28). Still holds inventory — consumption is not a release."""
    if reservation.state != "hard_reserved":
        raise ValueError(f"Cannot consume capacity in state '{reservation.state}'")
    reservation.state = "consumed"
    reservation.consumed_at = datetime.now(timezone.utc)
    event = db.get(Event, reservation.event_id)
    _capacity_audit(db, capacity_event="consumed", reservation=reservation, actor=actor,
                     event=event, reason="resource used by the delivered event")
    db.commit()
    db.refresh(reservation)
    return reservation


def release_capacity(db: Session, reservation: CapacityReservation, reason: str, *,
                      actor: User | None = None,
                      correlation_id: str | None = None) -> CapacityReservation:
    """Returns the held quantity to its pool by leaving CAPACITY_HOLDING_STATES. Idempotent:
    releasing an already-released row is a no-op rather than a double credit.

    Deliberately does NOT sync the lifecycle: the two callers that release in bulk
    (cancel_order, reschedule_event) each sync once after the whole batch, so a five-resource
    cancellation records one transition instead of five.
    """
    if reservation.state in ("released", "expired"):
        return reservation
    previous = reservation.state
    reservation.state = "released"
    reservation.released_at = datetime.now(timezone.utc)
    reservation.release_reason = reason
    event = db.get(Event, reservation.event_id)
    _capacity_audit(db, capacity_event="released", reservation=reservation, actor=actor,
                     event=event, correlation_id=correlation_id, reason=reason,
                     from_state=previous)
    db.commit()
    db.refresh(reservation)
    return reservation


def expire_stale_soft_holds(db: Session, *, actor: User | None = None) -> int:
    """Return inventory from soft holds whose governed period has lapsed (doc C2: "Soft holds
    expire automatically").

    Called opportunistically before capacity is read/claimed AND from the operator-invokable
    sweep (`POST /commercial/maintenance/expire-holds`) so an external scheduler can drive it.
    There is still no in-process job runner; the endpoint is what makes the sweep reachable
    without a new hold attempt, which is how a lapsed hold used to keep occupying a pool
    indefinitely on a quiet system.
    """
    now = datetime.now(timezone.utc)
    stale = db.scalars(
        select(CapacityReservation).where(
            CapacityReservation.state == "soft_held",
            CapacityReservation.soft_hold_expires_at.is_not(None),
            CapacityReservation.soft_hold_expires_at < now,
        )
    ).all()
    for reservation in stale:
        reservation.state = "expired"
        reservation.released_at = now
        reservation.release_reason = "soft_hold_expired"
        _capacity_audit(db, capacity_event="soft_hold_expired", reservation=reservation,
                         actor=actor,
                         reason="governed soft-hold period lapsed without a hard reservation",
                         expired_at=now.isoformat())
    if stale:
        db.commit()
    return len(stale)


# Audience qualification band: the peak concurrent-viewer count an event may expect WITHOUT
# an explicit, hard-reserved capacity commitment. Platform-wide (not a paid-tier feature), so
# it applies independently of any commercial ServiceProfile/order — unlike capacity_confirmed
# below, which is profile-scoped.
#
# The band itself is Operations-approved configuration, read from the platform settings
# registry (services/platform_settings.audience_capacity_envelope). It used to be a
# hard-coded `DEFAULT_CAPACITY_ENVELOPE = 500` here, which meant an unapproved number
# silently qualified every event under it — exactly the "no hard-coded fallback" failure the
# standard prohibits (doc Section 26; doc C4 requires failing closed on capacity).
AUDIENCE_CAPACITY_RESOURCE = "audience_capacity"


def envelope_capacity_block_reason(db: Session, event: Event) -> str | None:
    """Why this event fails the audience-capacity gate, or None if it passes.

    Fails closed in two distinct ways, which the caller surfaces verbatim so an operator can
    tell them apart:
      * no approved band is configured -> ANY stated expected audience needs an approved
        reservation. We cannot compare against a limit that does not exist, and we must not
        invent one.
      * a band IS configured and the stated audience exceeds it -> needs an approved
        reservation, same as before.

    An event with no `expected_audience` at all states no audience claim, so there is nothing
    to validate against a band — that is an absent estimate, not missing configuration, and
    it stays out of this gate (unchanged behavior). The profile-driven capacity requirements
    in capacity_confirmed() still apply to it independently.
    """
    if not event.expected_audience:
        return None

    approved = db.scalar(
        select(CapacityReservation.id).where(
            CapacityReservation.event_id == event.id,
            CapacityReservation.resource_type == AUDIENCE_CAPACITY_RESOURCE,
            CapacityReservation.state == "hard_reserved",
        )
    ) is not None
    if approved:
        return None

    envelope = platform_settings.audience_capacity_envelope(db)
    if envelope is None:
        return (
            f"expected audience ({event.expected_audience}) cannot be qualified — no approved "
            "platform audience capacity envelope is configured, and this event has no "
            "approved audience capacity reservation"
        )
    if event.expected_audience <= envelope:
        return None
    return (
        f"expected audience ({event.expected_audience}) exceeds the approved {envelope}-viewer "
        "envelope without an approved capacity reservation"
    )


def approve_audience_capacity(db: Session, event: Event, actor: User) -> CapacityReservation:
    """Operations approval of an event's stated audience size — a direct hard-reserved
    commitment, not a paid commercial resource. Deliberately bypasses hard_reserve_capacity's
    order requirement: that function is for commercial/ServiceProfile resources, this is a
    platform operating approval that self-service events must be able to clear too.

    This is the only way past envelope_capacity_block_reason when no approved envelope is
    configured, which is intentional — with no published band, an explicit human approval is
    the fail-closed path rather than an assumed number."""
    reservation = CapacityReservation(
        event_id=event.id, resource_type=AUDIENCE_CAPACITY_RESOURCE,
        quantity=event.expected_audience or 0, state="hard_reserved",
        hard_reserved_at=datetime.now(timezone.utc),
    )
    db.add(reservation)
    audit(db, actor=actor, action="commercial.capacity.approve_envelope", target_type="event",
          target_id=event.id, org_id=event.org_id, expected_audience=event.expected_audience)
    db.commit()
    db.refresh(reservation)
    return reservation


def capacity_confirmed(db: Session, event: Event) -> bool:
    """CONFIRMED requires HARD_RESERVED for every resource type the service profile
    requires (doc C3). With no profile attached, capacity is trivially satisfied (R0 self-
    service has no mandatory resource types in this scaffold)."""
    if event.service_profile_id is None:
        return True
    required: list[str] = []
    profile = db.get(ServiceProfile, event.service_profile_id)
    if profile is None:
        return True
    if profile.requires_backup_contribution:
        required.append("backup_contribution")
    if profile.requires_dual_recording:
        required.append("dual_recording")
    if profile.requires_reserved_capacity:
        required.append("reserved_capacity")
    if not required:
        return True
    reserved_types = {
        r.resource_type for r in db.scalars(
            select(CapacityReservation).where(
                CapacityReservation.event_id == event.id, CapacityReservation.state == "hard_reserved",
            )
        ).all()
    }
    return all(rt in reserved_types for rt in required)


# ── Payment schedule & financial readiness (doc Section 8/D, Section 28) ────────────────

def create_payment_schedule(db: Session, order: EventOrder, *, milestone: str, amount: Decimal,
                             due_at: datetime | None = None, required_before_ready: bool = True) -> PaymentSchedule:
    schedule = PaymentSchedule(
        event_order_id=order.id, milestone=milestone, due_at=due_at, amount=amount,
        required_before_ready=required_before_ready, status="not_due" if due_at and due_at > datetime.now(timezone.utc) else "due",
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


def active_exception(db: Session, *, exception_type: str, order_id=None, event_id=None,
                      gate: str | None = None) -> CommercialException | None:
    """An APPROVED, UNEXPIRED, correctly-scoped exception, or None.

    Scope is narrow on purpose (doc: no permanent unrestricted override): the exception must
    be approved, must not have passed its expiry, must be attached to THIS order/event, and
    — where it records one — must name the gate it overrides. An exception raised for one
    order can never clear another's gate, and an expired one stops clearing anything.
    """
    stmt = select(CommercialException).where(
        CommercialException.exception_type == exception_type,
        CommercialException.status == "approved",
    )
    if order_id is not None:
        stmt = stmt.where(CommercialException.event_order_id == order_id)
    if event_id is not None:
        stmt = stmt.where(CommercialException.event_id == event_id)
    now = datetime.now(timezone.utc)
    for candidate in db.scalars(stmt).all():
        if candidate.expiry_at is not None and candidate.expiry_at < now:
            continue  # expired: no longer overrides anything
        if gate and candidate.overridden_gate and candidate.overridden_gate != gate:
            continue  # scoped to a different gate
        return candidate
    return None


def financial_readiness_state(db: Session, order: EventOrder) -> str:
    """NOT_DUE -> DUE -> SATISFIED | FINANCIAL_HOLD | APPROVED_EXCEPTION (doc Section 28).
    An order with no required-before-ready schedule rows is trivially satisfied.

    `approved_exception` is only ever returned when the order is ACTUALLY blocked and a
    narrowly-scoped, unexpired exception covers it — so the three outcomes the doc
    distinguishes (normal pass / exception approved / blocked) stay distinguishable. Note
    the earlier version checked neither expiry nor gate scope, and checked for an exception
    before deciding whether the order was even in hold.
    """
    required = [s for s in list_payment_schedules(db, order.id) if s.required_before_ready]
    if not required:
        return "satisfied"
    now = datetime.now(timezone.utc)
    captured = _captured_amount(db, order.id)
    total_required = sum((s.amount for s in required), Decimal(0))
    if captured >= total_required:
        return "satisfied"
    overdue = any(s.due_at and s.due_at < now for s in required)
    blocked_state = "financial_hold" if overdue else "due"
    exception = active_exception(db, exception_type="financial_hold_override",
                                 order_id=order.id, gate="financial_readiness")
    return "approved_exception" if exception is not None else blocked_state


# ── Commercial exceptions: the governed override path (doc Section 25 maker-checker) ──────
# The model existed with no write path at all: nothing in the codebase could create or
# approve one, so `financial_hold_override` was unreachable and the only way past a financial
# hold was the illegitimate go-live bypass (CF-3/CF-4).

EXCEPTION_GATES = ("financial_readiness", "capacity", "readiness_checks", "risk_tier", "pricing")


def request_commercial_exception(db: Session, actor: User, *, exception_type: str,
                                  rationale: str, evidence: dict | None = None,
                                  event: Event | None = None, order: EventOrder | None = None,
                                  overridden_gate: str | None = None,
                                  amount_exposure: Decimal | None = None,
                                  expiry_at: datetime | None = None,
                                  correlation_id: str | None = None) -> CommercialException:
    """Raise an override request. Never self-approving — approval is a separate call by a
    different, specifically-authorized human (see approve_commercial_exception).

    Requires a rationale and a target. Evidence is required for the money-affecting types,
    because "why" without "on what basis" is not an auditable exception (doc Section 25).
    """
    if exception_type not in EXCEPTION_TYPES:
        raise ValueError(f"Unknown exception type '{exception_type}' (allowed: {', '.join(EXCEPTION_TYPES)})")
    if overridden_gate and overridden_gate not in EXCEPTION_GATES:
        raise ValueError(f"Unknown gate '{overridden_gate}' (allowed: {', '.join(EXCEPTION_GATES)})")
    if not (rationale and rationale.strip()):
        raise ValueError("A commercial exception requires a rationale — an unexplained override is not auditable")
    if event is None and order is None:
        raise ValueError("A commercial exception must target an event or an order — it is never global")
    # Money-moving overrides additionally need supporting evidence on file.
    if exception_type in ("financial_hold_override", "price_override", "waiver",
                          "exceptional_cancellation", "complimentary_event") and not evidence:
        raise ValueError(
            f"Exception type '{exception_type}' requires evidence (approval reference, ticket, "
            "signed authority) — a financial override may not rest on free text alone"
        )
    if expiry_at is not None and expiry_at <= datetime.now(timezone.utc):
        raise ValueError("Exception expiry must be in the future")

    correlation_id = correlation_id or new_correlation_id()
    previous_state = None
    if order is not None and overridden_gate == "financial_readiness":
        previous_state = financial_readiness_state(db, order)

    exception = CommercialException(
        event_id=event.id if event is not None else (order.event_id if order is not None else None),
        event_order_id=order.id if order is not None else None,
        exception_type=exception_type, requested_by=actor.id, rationale=rationale.strip(),
        evidence=evidence, overridden_gate=overridden_gate, amount_exposure=amount_exposure,
        expiry_at=expiry_at, previous_state=previous_state, status="requested",
        correlation_id=correlation_id,
    )
    db.add(exception)
    db.flush()
    ev = db.get(Event, exception.event_id) if exception.event_id else None
    audit(db, actor=actor, action="commercial.exception.requested", target_type="commercial_exception",
          target_id=exception.id, org_id=ev.org_id if ev else None, correlation_id=correlation_id,
          exception_type=exception_type, overridden_gate=overridden_gate,
          previous_state=previous_state, reason=rationale.strip(),
          amount_exposure=None if amount_exposure is None else str(amount_exposure),
          expiry_at=expiry_at.isoformat() if expiry_at else None,
          evidence_keys=sorted(evidence.keys()) if evidence else None)
    db.commit()
    db.refresh(exception)
    return exception


def approve_commercial_exception(db: Session, exception: CommercialException, approver: User, *,
                                  notes: str | None = None) -> CommercialException:
    """Maker-checker: the approver must not be the requester (doc Section 25 — "No single
    internal user should be able to create an exceptional price/waiver, approve it, issue/
    refund the money and erase the evidence")."""
    if exception.status != "requested":
        raise ValueError(f"Cannot approve an exception in status '{exception.status}'")
    if exception.requested_by and str(exception.requested_by) == str(approver.id):
        raise ValueError("Maker-checker violation: the approver must differ from the requester")
    if exception.expiry_at is not None and exception.expiry_at < datetime.now(timezone.utc):
        raise ValueError("This exception has already expired and cannot be approved")
    exception.status = "approved"
    exception.approver_id = approver.id
    exception.decided_at = datetime.now(timezone.utc)
    exception.decision_notes = notes
    ev = db.get(Event, exception.event_id) if exception.event_id else None
    audit(db, actor=approver, action="commercial.exception.approved", target_type="commercial_exception",
          target_id=exception.id, org_id=ev.org_id if ev else None,
          correlation_id=exception.correlation_id, exception_type=exception.exception_type,
          overridden_gate=exception.overridden_gate, previous_state=exception.previous_state,
          requested_by=str(exception.requested_by), expiry_at=exception.expiry_at.isoformat() if exception.expiry_at else None,
          resulting_action="gate_override_active", notes=notes)
    db.commit()
    db.refresh(exception)
    return exception


def decline_commercial_exception(db: Session, exception: CommercialException, approver: User, *,
                                  notes: str | None = None) -> CommercialException:
    if exception.status != "requested":
        raise ValueError(f"Cannot decline an exception in status '{exception.status}'")
    exception.status = "declined"
    exception.approver_id = approver.id
    exception.decided_at = datetime.now(timezone.utc)
    exception.decision_notes = notes
    ev = db.get(Event, exception.event_id) if exception.event_id else None
    audit(db, actor=approver, action="commercial.exception.declined", target_type="commercial_exception",
          target_id=exception.id, org_id=ev.org_id if ev else None,
          correlation_id=exception.correlation_id, exception_type=exception.exception_type,
          resulting_action="gate_remains_blocked", notes=notes)
    db.commit()
    db.refresh(exception)
    return exception


def list_commercial_exceptions(db: Session, *, event_id=None, order_id=None, status: str | None = None):
    stmt = select(CommercialException).order_by(CommercialException.created_at.desc())
    if event_id is not None:
        stmt = stmt.where(CommercialException.event_id == event_id)
    if order_id is not None:
        stmt = stmt.where(CommercialException.event_order_id == order_id)
    if status:
        stmt = stmt.where(CommercialException.status == status)
    return db.scalars(stmt).all()


def list_payment_schedules(db: Session, order_id) -> list[PaymentSchedule]:
    return db.scalars(select(PaymentSchedule).where(PaymentSchedule.event_order_id == order_id)).all()


# Payment states whose money we actually hold. Deliberately EXCLUDES `partially_paid`:
# Payment.amount is the AUTHORIZED total, not the settled portion, and no column records the
# short amount — so counting it would overstate collection. Excluding it understates, which
# blocks rather than passes (doc D6: the outstanding balance stays explicit). `reversed`
# (dispute lost) and `refunded` are absent for the same reason they should be: the money is gone.
COLLECTED_PAYMENT_STATES = ("paid", "part_refunded")


def order_settlement(db: Session, order_id) -> dict:
    """Everything settled against one order, NETTED (doc D6, Section 29).

    The previous `_captured_amount` summed gross payments and subtracted nothing, so an
    executed refund left the order still reading as fully collected. Three separate
    calculations consumed that figure — financial readiness, the outstanding payable balance
    and milestone allocation — so a refunded order simultaneously reported "satisfied" to the
    go-live gate, refused re-collection as "already paid in full", and kept its milestones
    marked satisfied. All three are now derived from this one netted view.

    * `gross_captured` — cash the provider settled to us.
    * `refunded`       — EXECUTED refunds only. A pending or approved-but-unexecuted remedy
                          has not moved money and must not reduce the collected figure.
    * `credited`       — executed credits and fee waivers. No cash moved, but the customer no
                          longer owes it, so it satisfies a balance without being collectable.
    * `net`            — what counts as settled for readiness and for what is still owed.
    * `refundable`     — the cash still available to refund. This is the cap a remedy may not
                          exceed; it is NOT `net`, because a credit was never cash and cannot
                          be handed back.
    """
    gross = sum((
        p.amount for p in db.scalars(
            select(Payment).where(
                Payment.event_order_id == order_id,
                Payment.state.in_(COLLECTED_PAYMENT_STATES),
            )
        ).all()
    ), Decimal(0))
    remedies = db.scalars(
        select(RefundCredit).where(
            RefundCredit.event_order_id == order_id,
            RefundCredit.status == "executed",
        )
    ).all()
    # `status == "executed"` is re-checked here, not just in the WHERE clause above. The rule
    # that an unexecuted remedy must not reduce collected money is the whole correctness
    # property of this function, and keeping it visible at the point of summation means it can
    # be asserted without a database — a filter that lives only in SQL is a filter nothing can
    # test in isolation.
    executed = [r for r in remedies if r.status == "executed"]
    refunded = sum((r.amount for r in executed if r.type == "refund"), Decimal(0))
    credited = sum((r.amount for r in executed if r.type in ("credit", "fee_waiver")), Decimal(0))
    return {
        "gross_captured": gross,
        "refunded": refunded,
        "credited": credited,
        "net": max(gross - refunded + credited, Decimal(0)),
        "refundable": max(gross - refunded, Decimal(0)),
    }


def _captured_amount(db: Session, order_id) -> Decimal:
    """Net settled amount for this order — see order_settlement. Kept as the single name the
    readiness, payable-balance and allocation paths call, so all three can never disagree."""
    return order_settlement(db, order_id)["net"]


def payment_refundable_amount(db: Session, payment: Payment) -> Decimal:
    """How much of THIS payment may still be refunded.

    `execute_refund_credit` used to compare a remedy against `payment.amount` alone, so the
    same payment could be refunded repeatedly — three 100% refunds of one payment all passed
    the check. Prior executed refunds against this specific payment are now subtracted.
    """
    already = sum((
        r.amount for r in db.scalars(
            select(RefundCredit).where(
                RefundCredit.source_payment_id == payment.id,
                RefundCredit.type == "refund",
                RefundCredit.status == "executed",
            )
        ).all()
    ), Decimal(0))
    return max(Decimal(payment.amount) - already, Decimal(0))


def _refundable_payments(db: Session, order_id) -> list[tuple[Payment, Decimal]]:
    """(payment, still-refundable amount) for every payment holding cash on this order,
    oldest first. Oldest-first so a refund unwinds collection in the order it was taken."""
    payments = db.scalars(
        select(Payment)
        .where(Payment.event_order_id == order_id, Payment.state.in_(COLLECTED_PAYMENT_STATES))
        .order_by(Payment.created_at)
    ).all()
    out = []
    for p in payments:
        remaining = payment_refundable_amount(db, p)
        if remaining > 0:
            out.append((p, remaining))
    return out


def allocate_refund_across_payments(db: Session, order: EventOrder, amount: Decimal, *,
                                     reason_code: str, policy_version: str | None,
                                     actor: User | None,
                                     incident_id=None) -> list[RefundCredit]:
    """Create PENDING refund remedies covering `amount`, each bound to a real source payment.

    This is the fix for cancellation refunds that never moved money: `cancel_order` used to
    create one RefundCredit with `source_payment_id` left NULL, and `execute_refund_credit`
    only calls the provider when that field is set — so approving and executing it flipped the
    status to `executed` while the payment stayed `paid` and nothing was returned to the payer.

    A refund can only ever come out of a payment that actually holds cash, so the amount is
    split across the refundable payments rather than recorded against the order in the
    abstract. Still PENDING: maker-checker approval is unchanged and unbypassed.
    """
    remaining = Decimal(amount)
    created: list[RefundCredit] = []
    for payment, refundable in _refundable_payments(db, order.id):
        if remaining <= 0:
            break
        take = min(remaining, refundable)
        rc = RefundCredit(
            event_order_id=order.id, source_payment_id=payment.id, incident_id=incident_id,
            type="refund", amount=take, reason_code=reason_code,
            policy_version=policy_version,
            requested_by=actor.id if actor else None, status="pending",
        )
        db.add(rc)
        created.append(rc)
        remaining -= take
    return created


# ── Payments (doc Section 20/P, Section 28: provider-neutral, idempotent) ───────────────

def authorize_payment(db: Session, order: EventOrder, *, idempotency_key: str,
                       amount: Decimal | None = None,
                       provider_name: str = "mock", simulate_failure: bool = False,
                       actor: User | None = None, correlation_id: str | None = None) -> Payment:
    """Authorize the order's OUTSTANDING balance. The server decides the amount.

    `amount` used to be a required caller-supplied figure that went straight to the provider
    and onto the Payment row with no comparison against the order — so a customer role could
    authorize 1.00 against a 1440.00 order, or 999999.00 against any order. It is now
    optional and, when supplied, must EQUAL what the order actually owes:

      omitted        -> the authoritative outstanding balance is used
      equal to it    -> accepted (existing callers that send the right number still work)
      anything else  -> refused

    Refused rather than silently substituted on purpose: quietly charging a figure the caller
    did not ask for is its own hazard, and a loud rejection surfaces the disagreement. Partial
    amounts are therefore unavailable until an approved partial-payment rule exists — picking
    one would mean inventing commercial policy.

    Currency was already server-derived (order.currency) and stays that way. Amount and
    currency now come from the same authority hosted Checkout uses
    (order_payable_amount), so the two payment paths cannot disagree about what is owed.

    Idempotent on `idempotency_key`, which is immutable for the life of the Payment — the
    webhook path used to overwrite it, so replaying an authorize created a SECOND Payment
    (CF-1). The lookup is scoped to THIS order: it was global, so presenting a key belonging
    to another organization's payment returned that payment as the response.
    """
    # Payment.idempotency_key is GLOBALLY unique (uq_payment_idempotency_key), which is the
    # right invariant for an idempotency key — so the lookup stays global and ownership is
    # checked instead. Previously it returned whatever matched, meaning a key belonging to
    # another organization's payment was handed back as this caller's response. Reusing
    # someone else's key is a client error, not a replay: refuse it rather than leak the row
    # (and rather than let the insert die on the constraint with a 500).
    existing = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.event_order_id != order.id:
            raise ValueError(
                "This idempotency key is already in use for a different order. Idempotency "
                "keys are unique per payment operation — use a fresh key."
            )
        return existing

    # Authoritative amount/currency — raises on every state where charging makes no sense
    # (unaccepted, canceled, already paid in full, tax not determined, no currency).
    payable, currency = order_payable_amount(db, order)
    if amount is not None and Decimal(amount) != payable:
        raise ValueError(
            f"Requested amount {amount} does not match the outstanding balance {payable} "
            f"{currency} for this order. The payable amount is determined by the accepted "
            "order, not by the request — omit `amount` to authorize the outstanding balance."
        )
    amount = payable

    correlation_id = correlation_id or new_correlation_id()
    provider = payment_svc.get_provider(provider_name)
    result = provider.authorize(amount=amount, currency=currency, idempotency_key=idempotency_key,
                                 simulate_failure=simulate_failure)
    payment = Payment(
        event_order_id=order.id, provider=provider.name, provider_payment_ref=result.provider_payment_ref,
        amount=amount, currency=order.currency, state=result.state, idempotency_key=idempotency_key,
        authorized_at=result.authorized_at, failure_reason=result.failure_reason,
    )
    db.add(payment)
    db.flush()
    org_id = _order_org_id(db, order.id)
    action = "commercial.payment.authorize_failed" if result.state == "failed" else "commercial.payment.authorize"
    audit(db, actor=actor, action=action, target_type="payment", target_id=payment.id,
          org_id=org_id, correlation_id=correlation_id, amount=str(amount), currency=order.currency,
          provider=provider.name, state=result.state, failure_reason=result.failure_reason)
    db.commit()
    db.refresh(payment)
    return payment


def order_payable_amount(db: Session, order: EventOrder) -> tuple[Decimal, str]:
    """The authoritative amount and currency to collect for this order, or a hard failure.

    THE single source for what a payer owes. Reads only committed commercial facts — never a
    client-supplied figure, never a platform Plan price, never a provider-side price object:

        published CatalogVersion -> EventOrderLine (price frozen at add time)
          -> EventOrder.subtotal + recorded tax determination -> total_amount
          -> (Invoice snapshots that same total when issued)
          -> minus money already captured

    Returns the OUTSTANDING amount, so a partially-paid order asks for the remainder rather
    than the full total again. Fails closed on every state where a payment request would be
    meaningless or unsafe.
    """
    if order.status in ("canceled", "terminated"):
        raise ValueError(f"Order is '{order.status}' and cannot be paid")
    if order.status not in ("accepted", "active", "completed"):
        raise ValueError(
            f"Order is '{order.status}' — it must be accepted before it can be paid (doc B1)"
        )
    if not order.currency:
        raise ValueError("Order has no currency — cannot request payment (doc L2)")
    # Tax must be DETERMINED before we ask anyone for money: the total is subtotal + tax, and
    # an undetermined tax means the total is not yet a commercial fact (doc L4).
    if order.tax_amount is None:
        raise ValueError(
            "Order has no tax determination — the payable total is not yet established. "
            "Record a tax determination before requesting payment (doc L4, fail closed)"
        )
    total = order.total_amount
    if total is None or total <= 0:
        raise ValueError("Order has no positive payable total — nothing to collect")
    outstanding = total - _captured_amount(db, order.id)
    if outstanding <= 0:
        raise ValueError("This order is already paid in full")
    return outstanding, order.currency


def start_hosted_checkout(db: Session, order: EventOrder, *, actor: User, success_url: str,
                          cancel_url: str, provider_name: str,
                          correlation_id: str | None = None) -> dict:
    """Begin a provider-hosted checkout for an order's outstanding balance.

    Deliberately NOT a payment: it creates the Payment record in its initial state and hands
    back a URL. No money is collected here, nothing is marked paid, and the final state comes
    only from provider events through the existing state machine.

    Transaction discipline (Section 15): the commercial state is read and validated, then the
    provider call happens OUTSIDE any open write transaction, and only afterwards is the
    Payment row written. A provider timeout therefore cannot leave a half-written payment, and
    a retry re-uses the same idempotency key so neither side duplicates.
    """
    correlation_id = correlation_id or new_correlation_id()
    amount, currency = order_payable_amount(db, order)      # authoritative; may raise

    # Deterministic per (order, order version, outstanding amount): clicking Pay twice reuses
    # the SAME key, so the provider returns the same session and our own uniqueness check
    # returns the same Payment. A random key per click is exactly what would duplicate.
    idempotency_key = f"checkout:{order.id}:{order.order_version}:{amount}"

    # Same ownership check as authorize_payment: the key column is globally unique, so a bare
    # match could belong to another organization's payment. This key is server-derived from
    # the order, so a cross-order collision should be impossible — the guard is here so that
    # stays true by construction rather than by assumption.
    existing = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
    if existing is not None and existing.event_order_id != order.id:
        raise ValueError("Idempotency key collision across orders; refusing to reuse it")
    provider = payment_svc.get_provider(provider_name)      # fails closed if unconfigured

    # --- provider call: no DB transaction is held open across this ---
    result = provider.create_checkout_session(
        amount=amount, currency=currency, idempotency_key=idempotency_key,
        success_url=success_url, cancel_url=cancel_url,
        description=f"ZoikoStream Live Event order {order.order_version}",
        # Safe internal references only — no secrets, no payment credentials. These let an
        # inbound provider event be traced back without trusting anything the payer supplied.
        # DETERMINISTIC for a given (order, version, amount). Everything here must be stable
        # across retries: a provider that honours an idempotency key compares the whole request
        # and refuses a reused key whose parameters changed. A per-request correlation id used
        # to be sent here, and because it is regenerated on every call it made the SECOND Pay
        # click a parameter mismatch — the click failed instead of returning the same session.
        # Correlation is recorded on our own audit trail and provider_events, where it belongs;
        # a trace id has no business inside an idempotent request body.
        metadata={
            "zoiko_event_order_id": str(order.id),
            "zoiko_order_version": str(order.order_version),
            # Echoed back on the provider's own events. Because this key is unique per payment,
            # an event that arrives before its session event can still be matched by EXACT key
            # instead of being parked as unmatched money.
            "zoiko_idempotency_key": idempotency_key,
        },
    )
    if not result.checkout_session_ref:
        # The SESSION reference — not a payment reference — is what makes this payment
        # reconcilable. A hosted checkout legitimately has no payment reference yet: the
        # provider only creates one once the payer submits. Requiring one here is what made
        # every real checkout attempt fail. Without a session reference, though, nothing could
        # ever be matched back, so that genuinely must fail closed.
        raise ValueError(
            "Provider did not return a checkout session reference; refusing to create a "
            "payment that could not be reconciled"
        )

    if existing is not None:
        # A retry. Reuse the row; the provider returned the same session for the same key.
        return {"payment": existing, "checkout_url": result.checkout_url,
                "checkout_session_ref": result.checkout_session_ref,
                "correlation_id": correlation_id, "reused": True}

    payment = Payment(
        event_order_id=order.id, provider=provider.name,
        # Normally None at this point, and that is correct — it is adopted later from a
        # signature-verified provider event. Stored here only if the provider genuinely
        # supplied one, so no reference is ever invented.
        provider_payment_ref=result.provider_payment_ref,
        checkout_session_ref=result.checkout_session_ref,
        amount=amount, currency=currency,
        # NOT paid. The payer has not acted; only a provider event may advance this.
        state=result.state, idempotency_key=idempotency_key,
    )
    db.add(payment)
    db.flush()
    audit(db, actor=actor, action="commercial.payment.checkout_started", target_type="payment",
          target_id=payment.id, org_id=_order_org_id(db, order.id),
          correlation_id=correlation_id, amount=str(amount), currency=currency,
          provider=provider.name, checkout_session_ref=result.checkout_session_ref,
          state=result.state)
    db.commit()
    db.refresh(payment)
    return {"payment": payment, "checkout_url": result.checkout_url,
            "checkout_session_ref": result.checkout_session_ref,
            "correlation_id": correlation_id, "reused": False}


def capture_payment(db: Session, payment: Payment, *, actor: User | None = None,
                     correlation_id: str | None = None) -> Payment:
    """Capture an authorized payment. The provider's answer still has to clear the state
    machine — apply_payment_state refuses anything illegal rather than assigning it."""
    if payment.state != "pending":
        raise ValueError(f"Cannot capture a payment in state '{payment.state}'")
    correlation_id = correlation_id or new_correlation_id()
    provider = payment_svc.get_provider(payment.provider)
    # Provider-level idempotency on top of the application guard above. Derived from the
    # payment's own immutable authorize key (never rewritten — see authorize_payment), so a
    # retried capture of THIS payment reaches the same provider operation rather than a new one.
    result = provider.capture(payment.provider_payment_ref,
                               idempotency_key=f"{payment.id}:{payment.idempotency_key}")
    applied, error = apply_payment_state(
        db, payment, result.state, actor=actor, correlation_id=correlation_id,
        source="capture", provider=payment.provider,
    )
    if error:
        db.commit()  # keep the rejection audit
        raise ValueError(error)
    if applied:
        if result.captured_at:
            payment.captured_at = result.captured_at
        if result.settled_at:
            payment.settled_at = result.settled_at
        reallocate_schedules(db, payment.event_order_id)
        # Capturing money can satisfy the financial gate and move the order out of
        # FINANCIAL_HOLD — recorded as a lifecycle transition, not inferred later.
        order = db.get(EventOrder, payment.event_order_id)
        event = db.get(Event, order.event_id) if order is not None else None
        if order is not None and event is not None:
            sync_lifecycle(db, event, order, actor=actor, trigger="payment.capture",
                            correlation_id=correlation_id)
    db.commit()
    db.refresh(payment)
    return payment


def reallocate_schedules(db: Session, order_id) -> dict:
    """Re-derive milestone allocation from the order's total captured money (doc D6).

    Idempotent BY CONSTRUCTION: allocation is recomputed from scratch off `_captured_amount`
    every time, so calling this twice for the same capture produces the same result. That is
    what makes a redelivered provider event harmless without needing an allocation ledger.

    Replaces a version that set the oldest milestone to "satisfied" on ANY capture amount —
    a $1 capture satisfied a $50,000 deposit (CF-5). A milestone is now `satisfied` only when
    its allocation covers it in full; a short allocation stays due with the partial figure
    recorded, so the outstanding balance stays explicit.

    Over-allocation is deliberately NOT distributed: money beyond the scheduled total is
    reported as `unallocated` for Finance to handle, because no approved commercial rule says
    where an overpayment should go.
    """
    schedules = list_payment_schedules(db, order_id)
    schedules.sort(key=lambda s: (s.due_at or datetime.max.replace(tzinfo=timezone.utc), str(s.id)))
    remaining = _captured_amount(db, order_id)
    now = datetime.now(timezone.utc)
    for s in schedules:
        take = min(remaining, s.amount) if remaining > 0 else Decimal(0)
        s.allocated_amount = take
        remaining -= take
        if take >= s.amount:
            s.status = "satisfied"
        elif s.due_at and s.due_at < now:
            s.status = "financial_hold"
        elif s.due_at and s.due_at > now:
            s.status = "not_due"
        else:
            s.status = "due"
    return {
        "allocated": sum((s.allocated_amount for s in schedules), Decimal(0)),
        "unallocated": remaining,
        "satisfied": sum(1 for s in schedules if s.status == "satisfied"),
        "outstanding": sum(((s.amount - s.allocated_amount) for s in schedules), Decimal(0)),
    }


# ── Provider events: the webhook idempotency + evidence boundary (doc P1/P5) ──────────────

# Keys never persisted from a provider payload. Provider-neutral: these are the generic names
# for cardholder data and secrets (doc P6/R1 — no raw card data, no sensitive authentication
# data, logs redact payment secrets). Matching is substring-based and case-insensitive so
# variants like "card_number" or "cvc2" are caught without enumerating one provider's schema.
_SENSITIVE_PAYLOAD_KEYS = (
    "card", "pan", "number", "cvv", "cvc", "cvn", "securitycode", "security_code",
    "expiry", "exp_month", "exp_year", "track", "pin", "secret", "password", "token",
    "authorization", "signature", "iban", "account_number", "routing",
)


def _redact_payload(value, _depth: int = 0):
    """Recursively drop sensitive keys before the payload is stored. The hash is taken over
    the ORIGINAL bytes, so redaction never breaks the ability to prove what was received."""
    if _depth > 6:
        return "<max-depth>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(s in str(k).lower().replace("-", "_") for s in _SENSITIVE_PAYLOAD_KEYS):
                out[k] = "<redacted>"
            else:
                out[k] = _redact_payload(v, _depth + 1)
        return out
    if isinstance(value, list):
        return [_redact_payload(v, _depth + 1) for v in value[:50]]
    return value


# Provider-neutral event vocabulary -> the normalized Payment state it drives. Deliberately
# generic: no processor's event names are assumed (doc P1 "processor-neutral"). A provider
# adapter maps ITS names onto these before calling in.
PROVIDER_EVENT_STATE_MAP = {
    "authorization_succeeded": "pending",
    "authorization_failed": "failed",
    # The payer must complete a step (strong-authentication challenge, redirect, ...) before
    # the provider can proceed. A generic name, not a Stripe one — `requires_action` already
    # exists in PAYMENT_STATES and PAYMENT_TRANSITIONS, so this adds no state to the graph.
    "action_required": "requires_action",
    "capture_succeeded": "paid",
    "capture_partial": "partially_paid",
    "capture_failed": "failed",
    "refunded": "refunded",
    "refund_partial": "part_refunded",
    "disputed": "disputed",
    "dispute_won": "paid",
    "dispute_lost": "reversed",
    "reversed": "reversed",
}


def _amount_mismatch_reason(payment: Payment, amount: Decimal | None, currency: str | None) -> str | None:
    """A provider event may never redefine the commercial amount (doc: provider evidence is
    reconciled, it never becomes the commercial record). Currency mismatch always fails
    closed — we do not hold exchange rates and must never silently restate a USD payment as
    EUR. Amount is compared only when the event states one."""
    if currency and currency.upper() != (payment.currency or "").upper():
        return (
            f"currency mismatch: provider event says {currency.upper()}, payment is "
            f"{payment.currency} — refusing to restate (no exchange rate is assumed)"
        )
    if amount is not None and Decimal(amount) != Decimal(payment.amount):
        return (
            f"amount mismatch: provider event says {amount}, payment is {payment.amount} — "
            "a provider event cannot change the commercial amount"
        )
    return None


def _claim_provider_event(db: Session, *, provider: str, provider_event_id: str, event_type: str,
                           raw_body: bytes, signature_verified: bool,
                           provider_payment_ref: str | None, payload: dict | None,
                           occurred_at: datetime | None,
                           correlation_id: str | None) -> tuple[ProviderEvent | None, dict]:
    """Atomically claim `(provider, provider_event_id)` for THIS delivery.

    The single idempotency gate for every inbound provider event, financial or not, so there
    is exactly one evidence ledger and one dedup mechanism no matter which adapter is calling.

    Returns `(record, {})` for the delivery that won the claim, or `(None, response)` for a
    duplicate/redelivery — where `response` is the ORIGINAL outcome, never a recomputed one.

    Idempotency is enforced by the DATABASE (INSERT ... ON CONFLICT DO NOTHING RETURNING),
    not a preceding SELECT: two concurrent deliveries of the same event both attempt the
    insert and exactly one gets a row back. An `if exists(...)` check cannot close that race.
    """
    correlation_id = correlation_id or new_correlation_id()
    now = datetime.now(timezone.utc)
    payload_hash = hashlib.sha256(raw_body or b"").hexdigest()

    inserted_id = db.execute(
        text(
            "INSERT INTO provider_events "
            "(id, provider, provider_event_id, event_type, provider_payment_ref, occurred_at, "
            " received_at, payload, payload_hash, signature_verified, processing_status, "
            " processing_attempts, correlation_id, created_at, updated_at) "
            "VALUES (:id, :provider, :peid, :etype, :pref, :occurred, :received, "
            "        CAST(:payload AS JSON), :phash, :sig, 'processing', 1, :corr, NOW(), NOW()) "
            "ON CONFLICT (provider, provider_event_id) DO NOTHING "
            "RETURNING id"
        ),
        {
            "id": str(uuid.uuid4()), "provider": provider, "peid": provider_event_id,
            "etype": event_type, "pref": provider_payment_ref, "occurred": occurred_at,
            "received": now, "payload": json.dumps(_redact_payload(payload or {})),
            "phash": payload_hash, "sig": signature_verified, "corr": correlation_id,
        },
    ).scalar()

    if inserted_id is None:
        # Lost the race, or a genuine redelivery. Answer from what the first delivery did —
        # never re-apply. This is the "return the already-known processing result" contract.
        db.commit()
        prior = db.scalar(
            select(ProviderEvent).where(
                ProviderEvent.provider == provider,
                ProviderEvent.provider_event_id == provider_event_id,
            )
        )
        return None, {
            "duplicate": True, "applied": False,
            "provider_event_id": str(prior.id) if prior else None,
            "processing_status": prior.processing_status if prior else "processed",
            "result": (prior.processing_result if prior else None) or {},
            "correlation_id": prior.correlation_id if prior else correlation_id,
        }

    db.commit()  # the identity row is now durably claimed by this delivery
    return db.get(ProviderEvent, inserted_id), {}


def record_provider_event_evidence(db: Session, *, provider: str, provider_event_id: str,
                                    event_type: str, raw_body: bytes, signature_verified: bool,
                                    reason: str, provider_payment_ref: str | None = None,
                                    payload: dict | None = None, occurred_at: datetime | None = None,
                                    correlation_id: str | None = None,
                                    follow_up_required: bool = False) -> dict:
    """Store a provider event as EVIDENCE ONLY — no financial effect, by design.

    For events that are genuinely not financial instructions to us: a provider's own
    informational notifications, event types this application does not act on, and event
    classes whose commercial workflow is deliberately not built yet.

    Uses the same table, the same unique constraint and the same claim path as the financial
    ingest, so a redelivery is still exactly-once and nothing is ever silently dropped
    (doc P1 raw-evidence retention). Marked `processed` rather than `rejected`: we DID handle
    it correctly by deciding it has no effect — `rejected` is reserved for a refusal.
    """
    claimed, duplicate_response = _claim_provider_event(
        db, provider=provider, provider_event_id=provider_event_id, event_type=event_type,
        raw_body=raw_body, signature_verified=signature_verified,
        provider_payment_ref=provider_payment_ref, payload=payload, occurred_at=occurred_at,
        correlation_id=correlation_id,
    )
    if claimed is None:
        return duplicate_response
    record = claimed
    result = {"applied": False, "reason": reason, "follow_up_required": follow_up_required}
    record.processing_status = "processed"
    record.processing_result = result
    record.processed_at = datetime.now(timezone.utc)
    audit(db, actor=None, action="commercial.provider_event.evidence_recorded",
          target_type="provider_event", target_id=record.id,
          correlation_id=record.correlation_id, provider=provider, event_type=event_type,
          reason=reason, follow_up_required=follow_up_required)
    db.commit()
    return {"duplicate": False, "applied": False, "provider_event_id": str(record.id),
            "processing_status": "processed", "result": result,
            "correlation_id": record.correlation_id}


def _bind_provider_payment_ref(db: Session, payment: Payment, provider: str,
                                ref: str) -> tuple[bool, str | None]:
    """Attach a provider payment reference to a payment that does not have one yet.

    Returns (changed, conflict_reason). A reference is NEVER overwritten: re-binding the same
    reference is an idempotent no-op, and a different one is a conflict that the caller must
    surface rather than resolve. Silently reassigning a payment reference would let one
    provider object take over the financial identity of another.
    """
    existing = payment.provider_payment_ref
    if existing == ref:
        return False, None
    if existing:
        return False, (
            f"Payment is already bound to provider reference '{existing}'; refusing to "
            f"rebind it to '{ref}'"
        )
    # The (provider, provider_payment_ref) pair is unique. Check explicitly so the caller gets
    # a described conflict instead of an IntegrityError surfacing as a 500.
    other = db.scalar(
        select(Payment).where(Payment.provider == provider, Payment.provider_payment_ref == ref)
    )
    if other is not None:
        return False, (
            f"Provider reference '{ref}' is already bound to a different payment; refusing "
            f"to attach it to a second one"
        )
    payment.provider_payment_ref = ref
    return True, None


def reconcile_checkout_session(db: Session, *, provider: str, provider_event_id: str,
                                event_type: str, raw_body: bytes, signature_verified: bool,
                                checkout_session_ref: str | None,
                                provider_payment_ref: str | None = None,
                                payload: dict | None = None, amount: Decimal | None = None,
                                currency: str | None = None, occurred_at: datetime | None = None,
                                correlation_id: str | None = None,
                                reason: str = "checkout_session_reconciliation",
                                follow_up_required: bool = False) -> dict:
    """Bind the real provider payment reference onto the payment a hosted checkout created.

    This is CORRELATION, not a financial transition. A completed checkout session means the
    payer acted; under manual capture the money is merely authorized, and the authoritative
    authorization/settlement signals remain the provider's payment events. So this function
    never touches Payment.state, never marks anything paid, and deliberately does not consult
    the state machine — there is exactly one state machine and this is not it.

    Reuses the same claim path, the same unique constraint and the same evidence table as every
    other provider event, so a redelivery is exactly-once here too.
    """
    claimed, duplicate_response = _claim_provider_event(
        db, provider=provider, provider_event_id=provider_event_id, event_type=event_type,
        raw_body=raw_body, signature_verified=signature_verified,
        provider_payment_ref=provider_payment_ref, payload=payload, occurred_at=occurred_at,
        correlation_id=correlation_id,
    )
    if claimed is None:
        return duplicate_response
    record = claimed
    correlation_id = record.correlation_id

    def finish(status: str, result: dict, error: str | None = None) -> dict:
        record.processing_status = status
        record.processing_result = result
        record.processing_error = error
        record.processed_at = datetime.now(timezone.utc)
        db.commit()
        return {"duplicate": False, "applied": bool(result.get("applied")),
                "provider_event_id": str(record.id), "processing_status": status,
                "result": result, "correlation_id": correlation_id}

    if not signature_verified:
        audit(db, actor=None, action="commercial.provider_event.rejected",
              target_type="provider_event", target_id=record.id, correlation_id=correlation_id,
              reason="signature_not_verified", provider=provider, event_type=event_type)
        return finish("rejected", {"applied": False, "reason": "signature_not_verified"},
                      "signature not verified")

    if not checkout_session_ref:
        return finish("processed", {"applied": False, "reason": "no_checkout_session_reference",
                                    "follow_up_required": True})

    payment = db.scalar(
        select(Payment).where(Payment.provider == provider,
                              Payment.checkout_session_ref == checkout_session_ref)
    )
    if payment is None:
        # A completed session we have no payment for. Retained for controlled follow-up and
        # never attached to a guessed payment (doc P5).
        audit(db, actor=None, action="commercial.provider_event.evidence_recorded",
              target_type="provider_event", target_id=record.id, correlation_id=correlation_id,
              provider=provider, event_type=event_type, reason="checkout_session_unmatched",
              checkout_session_ref=checkout_session_ref, follow_up_required=True)
        return finish("processed", {"applied": False, "reason": "checkout_session_unmatched",
                                    "follow_up_required": True})

    record.payment_id = payment.id
    org_id = _order_org_id(db, payment.event_order_id)

    # The session total must agree with what we recorded, or the identity binding is refused.
    # Binding a reference whose amount disagrees would attach the wrong money to this order.
    mismatch = _amount_mismatch_reason(payment, amount, currency)
    if mismatch:
        audit(db, actor=None, action="commercial.provider_event.rejected",
              target_type="provider_event", target_id=record.id, org_id=org_id,
              correlation_id=correlation_id, reason=mismatch, provider=provider,
              event_type=event_type, checkout_session_ref=checkout_session_ref)
        return finish("rejected", {"applied": False, "reason": mismatch}, mismatch)

    if not provider_payment_ref:
        # Nothing to adopt yet (e.g. an expired session). Evidence, no effect.
        return finish("processed", {"applied": False, "reason": reason,
                                    "follow_up_required": follow_up_required})

    changed, conflict = _bind_provider_payment_ref(db, payment, provider, provider_payment_ref)
    if conflict:
        audit(db, actor=None, action="commercial.payment.reconciliation_conflict",
              target_type="payment", target_id=payment.id, org_id=org_id,
              correlation_id=correlation_id, provider=provider, event_type=event_type,
              checkout_session_ref=checkout_session_ref,
              provider_payment_ref=provider_payment_ref, reason=conflict)
        return finish("rejected", {"applied": False, "reason": "reconciliation_conflict",
                                   "detail": conflict, "follow_up_required": True}, conflict)

    if not changed:
        return finish("processed", {"applied": False, "reason": "already_reconciled",
                                    "payment_id": str(payment.id)})

    audit(db, actor=None, action="commercial.payment.reconciled", target_type="payment",
          target_id=payment.id, org_id=org_id, correlation_id=correlation_id, provider=provider,
          event_type=event_type, checkout_session_ref=checkout_session_ref,
          provider_payment_ref=provider_payment_ref, state=payment.state)
    return finish("processed", {"applied": True, "reason": "provider_reference_adopted",
                                "payment_id": str(payment.id),
                                "provider_payment_ref": provider_payment_ref})


def ingest_provider_event(db: Session, *, provider: str, provider_event_id: str, event_type: str,
                           raw_body: bytes, signature_verified: bool,
                           provider_payment_ref: str | None = None, payload: dict | None = None,
                           amount: Decimal | None = None, currency: str | None = None,
                           occurred_at: datetime | None = None,
                           correlation_id: str | None = None,
                           idempotency_key_hint: str | None = None) -> dict:
    """Record and process one provider event exactly once.

    Idempotency is enforced by the DATABASE, not by a preceding SELECT: the insert is an
    INSERT ... ON CONFLICT (provider, provider_event_id) DO NOTHING RETURNING id. Two
    concurrent deliveries of the same event both attempt it; exactly one gets a row back and
    processes, the other gets nothing and is answered from the stored result. That closes the
    race an `if exists(...)` check leaves open.

    Returns a deterministic dict — the same event always yields the same answer, whether it
    is being processed for the first time or replayed years later.
    """
    claimed, duplicate_response = _claim_provider_event(
        db, provider=provider, provider_event_id=provider_event_id, event_type=event_type,
        raw_body=raw_body, signature_verified=signature_verified,
        provider_payment_ref=provider_payment_ref, payload=payload, occurred_at=occurred_at,
        correlation_id=correlation_id,
    )
    if claimed is None:
        return duplicate_response
    record = claimed
    correlation_id = record.correlation_id
    # The authoritative receipt time is the one persisted on the claimed event row, not a
    # fresh clock read — anything derived from this event (e.g. an UnmatchedSettlement) must
    # carry the SAME received_at as the evidence it came from.
    now = record.received_at

    def finish(status: str, result: dict, error: str | None = None) -> dict:
        record.processing_status = status
        record.processing_result = result
        record.processing_error = error
        record.processed_at = datetime.now(timezone.utc)
        db.commit()
        return {"duplicate": False, "applied": bool(result.get("applied")),
                "provider_event_id": str(record.id), "processing_status": status,
                "result": result, "correlation_id": correlation_id}

    if not signature_verified:
        # Recorded as evidence, never applied (doc: an unsigned payload must never move money).
        audit(db, actor=None, action="commercial.provider_event.rejected", target_type="provider_event",
              target_id=record.id, correlation_id=correlation_id, reason="signature_not_verified",
              provider=provider, event_type=event_type)
        return finish("rejected", {"applied": False, "reason": "signature_not_verified"},
                      "signature not verified")

    new_state = PROVIDER_EVENT_STATE_MAP.get(event_type)
    if new_state is None:
        audit(db, actor=None, action="commercial.provider_event.rejected", target_type="provider_event",
              target_id=record.id, correlation_id=correlation_id, reason="unknown_event_type",
              provider=provider, event_type=event_type)
        return finish("rejected", {"applied": False, "reason": f"unknown event_type '{event_type}'"},
                      f"unknown event_type '{event_type}'")

    payment = None
    if provider_payment_ref:
        payment = db.scalar(
            select(Payment).where(
                Payment.provider == provider,
                Payment.provider_payment_ref == provider_payment_ref,
            )
        )

    if payment is None and provider_payment_ref and idempotency_key_hint:
        # Out-of-order delivery: a payment event can arrive before the checkout session event
        # that would normally have bound the reference. Recovered by our OWN idempotency key,
        # echoed back in provider metadata we set at session creation — an exact match on a
        # unique column, never a fuzzy match on amount/currency/email. Only a payment still
        # awaiting a reference is eligible, so nothing can be reassigned.
        candidate = db.scalar(
            select(Payment).where(Payment.provider == provider,
                                  Payment.idempotency_key == idempotency_key_hint,
                                  Payment.provider_payment_ref.is_(None))
        )
        if candidate is not None:
            bound, conflict = _bind_provider_payment_ref(
                db, candidate, provider, provider_payment_ref)
            if bound:
                payment = candidate
                audit(db, actor=None, action="commercial.payment.reconciled",
                      target_type="payment", target_id=candidate.id,
                      org_id=_order_org_id(db, candidate.event_order_id),
                      correlation_id=correlation_id, provider=provider, event_type=event_type,
                      provider_payment_ref=provider_payment_ref,
                      reason="adopted_from_out_of_order_payment_event")
            # A conflict deliberately falls through to the unmatched path below: it is money we
            # cannot safely attribute, which is exactly what that path is for.

    if payment is None:
        # Money we cannot attribute. Persisted for controlled matching — never guessed onto
        # an invoice, never dropped (doc P5, CF-8).
        settlement = UnmatchedSettlement(
            provider=provider, provider_payment_ref=provider_payment_ref, provider_event_id=record.id,
            event_type=event_type, amount=amount, currency=currency, occurred_at=occurred_at,
            received_at=now, status="open", correlation_id=correlation_id,
            reason=(
                f"No payment matches provider ref '{provider_payment_ref}' for provider "
                f"'{provider}'" if provider_payment_ref else
                "Provider event carried no payment reference"
            ),
        )
        db.add(settlement)
        db.flush()  # populate the client-side UUID default before it is referenced/returned
        audit(db, actor=None, action="commercial.settlement.unmatched", target_type="unmatched_settlement",
              target_id=settlement.id, correlation_id=correlation_id, provider=provider,
              event_type=event_type, provider_payment_ref=provider_payment_ref,
              amount=None if amount is None else str(amount), currency=currency)
        return finish("processed", {"applied": False, "reason": "unmatched_settlement",
                                    "unmatched_settlement_id": str(settlement.id)})

    record.payment_id = payment.id
    org_id = _order_org_id(db, payment.event_order_id)

    mismatch = _amount_mismatch_reason(payment, amount, currency)
    if mismatch:
        audit(db, actor=None, action="commercial.provider_event.rejected", target_type="provider_event",
              target_id=record.id, org_id=org_id, correlation_id=correlation_id, reason=mismatch,
              payment_id=str(payment.id), provider=provider, event_type=event_type)
        return finish("rejected", {"applied": False, "reason": mismatch}, mismatch)

    if payment.state == new_state:
        # Same-state redelivery of a legitimate event: harmless, and explicitly recorded as a
        # replay rather than silently treated as a fresh application.
        audit(db, actor=None, action="commercial.provider_event.replayed", target_type="provider_event",
              target_id=record.id, org_id=org_id, correlation_id=correlation_id,
              payment_id=str(payment.id), state=new_state, event_type=event_type)
        return finish("replayed", {"applied": False, "reason": "already_in_target_state",
                                    "payment_id": str(payment.id), "state": new_state})

    applied, error = apply_payment_state(
        db, payment, new_state, actor=None, correlation_id=correlation_id,
        source=f"provider_event:{provider}", provider_event_id=str(record.id),
        event_type=event_type,
    )
    if not applied:
        # apply_payment_state already audited the transition refusal; this records the EVENT
        # as rejected too, so the provider-webhook lifecycle is auditable on its own terms
        # and not only via the payment it failed to move.
        audit(db, actor=None, action="commercial.provider_event.rejected", target_type="provider_event",
              target_id=record.id, org_id=org_id, correlation_id=correlation_id, reason=error,
              payment_id=str(payment.id), provider=provider, event_type=event_type,
              from_state=payment.state, to_state=new_state)
        return finish("rejected", {"applied": False, "reason": error,
                                    "payment_id": str(payment.id),
                                    "from_state": payment.state, "to_state": new_state},
                      error)

    allocation = reallocate_schedules(db, payment.event_order_id)
    # A provider event that moved money can move the commercial lifecycle with it (settlement
    # clearing FINANCIAL_HOLD, a reversal dropping it back in). Recorded here so the webhook
    # path and the human capture path produce the same lifecycle history.
    lifecycle_order = db.get(EventOrder, payment.event_order_id)
    lifecycle_event = db.get(Event, lifecycle_order.event_id) if lifecycle_order is not None else None
    if lifecycle_order is not None and lifecycle_event is not None:
        sync_lifecycle(db, lifecycle_event, lifecycle_order, actor=None,
                        trigger=f"provider_event:{event_type}", correlation_id=correlation_id)
    audit(db, actor=None, action="commercial.provider_event.processed", target_type="provider_event",
          target_id=record.id, org_id=org_id, correlation_id=correlation_id,
          payment_id=str(payment.id), to_state=new_state, event_type=event_type,
          allocated=str(allocation["allocated"]), outstanding=str(allocation["outstanding"]))
    return finish("processed", {
        "applied": True, "payment_id": str(payment.id), "to_state": new_state,
        "allocated": str(allocation["allocated"]), "outstanding": str(allocation["outstanding"]),
        "unallocated": str(allocation["unallocated"]),
    })


# ── Unmatched settlement reconciliation (doc P5) ──────────────────────────────────────────

def list_open_unmatched_settlements(db: Session):
    return db.scalars(
        select(UnmatchedSettlement).where(UnmatchedSettlement.status == "open")
        .order_by(UnmatchedSettlement.received_at.desc())
    ).all()


def match_settlement(db: Session, settlement: UnmatchedSettlement, payment: Payment, actor: User, *,
                     notes: str | None = None) -> UnmatchedSettlement:
    """Deliberate human attribution of previously unattributable money (doc P5: "never
    auto-allocate by guess"). Refuses a mismatch rather than restating the payment, and does
    NOT itself move the payment's state — that stays a provider-evidence decision."""
    if settlement.status != "open":
        raise ValueError(f"Settlement is already '{settlement.status}'")
    mismatch = _amount_mismatch_reason(payment, settlement.amount, settlement.currency)
    if mismatch:
        raise ValueError(f"Cannot match: {mismatch}")
    settlement.status = "matched"
    settlement.matched_payment_id = payment.id
    settlement.matched_by = actor.id
    settlement.matched_at = datetime.now(timezone.utc)
    settlement.resolution_notes = notes
    audit(db, actor=actor, action="commercial.settlement.matched", target_type="unmatched_settlement",
          target_id=settlement.id, org_id=_order_org_id(db, payment.event_order_id),
          correlation_id=settlement.correlation_id, payment_id=str(payment.id),
          amount=None if settlement.amount is None else str(settlement.amount))
    db.commit()
    db.refresh(settlement)
    return settlement


# ── Tax determination (doc L1/L4/L6, Section 27) ─────────────────────────────────────────
# Zoiko does not resolve tax itself in this phase. What this layer guarantees is that a tax
# RESULT is explicit, versioned, attributable and present before a legal financial document
# is issued — and that its absence blocks issuance instead of silently meaning zero.

_TAX_DETERMINATION_FIELDS = (
    "tax_amount", "tax_treatment", "tax_jurisdiction", "tax_source",
    "tax_rule_version", "tax_effective_at", "tax_determined_at", "tax_determined_by",
    "tax_exemption_reason",
)


def _clear_tax_determination(order: EventOrder) -> None:
    for field in _TAX_DETERMINATION_FIELDS:
        setattr(order, field, None)


def record_tax_determination(db: Session, order: EventOrder, actor: User, *, tax_amount: Decimal,
                              treatment: str, jurisdiction: str, source: str,
                              rule_version: str | None = None, exemption_reason: str | None = None,
                              effective_at: datetime | None = None) -> EventOrder:
    """Attach a tax determination to an order so it can be invoiced.

    `tax_amount` may legitimately be 0 — a zero-rated, exempt, reverse-charge or
    out-of-scope supply is a real determination, and this is the ONLY way a zero-tax invoice
    can now be produced. What is no longer possible is reaching zero by default.

    `treatment`, `jurisdiction` and `source` are required precisely because a bare amount is
    not a determination (doc L6: exemptions are documented through validated tax facts and
    evidence, never a permanent free-text override). Their vocabularies belong to
    Finance/Tax; this function validates that they are present, not what they say.

    Re-determination is allowed while the order is open and is fully audited, but never after
    the order is closed out, and it never touches an already-issued Invoice — that document
    keeps its own snapshot of the facts as at issue time.
    """
    if order.status in ("canceled", "terminated", "completed"):
        raise ValueError(f"Cannot record a tax determination on a '{order.status}' order")
    if tax_amount is None or tax_amount < 0:
        raise ValueError("Tax determination requires a tax_amount of 0 or more")
    if not (treatment and treatment.strip()):
        raise ValueError("Tax determination requires an explicit tax treatment (doc L6)")
    if not (jurisdiction and jurisdiction.strip()):
        raise ValueError("Tax determination requires an explicit jurisdiction (doc L4)")
    if not (source and source.strip()):
        raise ValueError("Tax determination requires an explicit source (doc D3/L4 evidence)")
    # A zero must state WHY it is zero. This is the line between "Finance determined no tax
    # is due" and "nobody determined anything" — without it a 0.00 determination would be
    # indistinguishable from the structural zero this whole mechanism exists to eliminate
    # (doc L6: exemptions are documented through validated facts, not a bare figure).
    if tax_amount == 0 and not (exemption_reason and exemption_reason.strip()):
        raise ValueError(
            "A zero-tax determination requires an explicit exemption/zero-rating reason "
            "(e.g. exempt, zero-rated, reverse-charge, out-of-scope) — zero tax must be a "
            "stated outcome, never an unexplained amount (doc L6)"
        )
    # NOT gated on a resolved seller legal entity, deliberately. Tax is a fact about a supply
    # between two parties, so determining it before the selling party is known is arguably
    # premature (doc L1/L4) — but making that a hard block here would refuse the determination
    # on every order whose account has not yet been assigned an entity, including the entire
    # existing mock-provider payment path, and the consequence it guards against (issuing a
    # document under an unregistered seller) is ALREADY blocked at the only place it can
    # happen: issue_invoice -> resolve_seller_entity. Enforcing it twice would break working
    # collection to prevent something that cannot occur.
    #
    # What was genuinely missing is VISIBILITY: an accepted commercial order with a determined
    # tax basis and no seller entity can be charged and then never invoiced. That is now a
    # reconciliation control (list_orders_missing_seller_entity, category
    # "order_without_seller_entity") so Finance sees it at period close instead of discovering
    # it when an invoice refuses to issue.
    now = datetime.now(timezone.utc)
    previous = order.tax_amount
    order.tax_amount = tax_amount
    order.tax_treatment = treatment.strip()
    order.tax_jurisdiction = jurisdiction.strip()
    order.tax_source = source.strip()
    order.tax_rule_version = rule_version
    order.tax_exemption_reason = (exemption_reason or "").strip() or None
    order.tax_effective_at = effective_at or now
    order.tax_determined_at = now
    order.tax_determined_by = actor.id
    order.total_amount = (order.subtotal or Decimal(0)) + tax_amount
    audit(db, actor=actor, action="commercial.order.tax_determination", target_type="event_order",
          target_id=order.id, tax_amount=str(tax_amount), treatment=order.tax_treatment,
          jurisdiction=order.tax_jurisdiction, source=order.tax_source,
          rule_version=rule_version, exemption_reason=order.tax_exemption_reason,
          previous_tax_amount=None if previous is None else str(previous))
    db.commit()
    db.refresh(order)
    return order


# ── Invoices (doc L2, Section 27) ────────────────────────────────────────────────────────

def issue_invoice(db: Session, order: EventOrder, *, due_date: datetime | None = None) -> Invoice:
    """Fails closed on an undetermined tax basis (doc L4: "Missing tax determination blocks
    invoice issuance for live commercial events"). The determination's facts are copied onto
    the invoice, not referenced, so the issued document stays immutable."""
    if order.tax_amount is None:
        raise ValueError(
            "Cannot issue an invoice: no tax determination has been recorded for this order. "
            "Record one via POST /commercial/orders/{order_id}/tax-determination first "
            "(a zero-tax outcome is valid, but it must be an explicit determination with a "
            "tax treatment and jurisdiction — doc L4, fail closed)"
        )
    # Registered + ACTIVE seller entity, or refuse (doc L1) — resolved BEFORE a number is
    # allocated so a rejected issue never burns one.
    seller_entity = resolve_seller_entity(db, order)
    if seller_entity.default_currency and order.currency != seller_entity.default_currency:
        supported = seller_entity.supported_currencies or []
        if order.currency not in supported:
            raise ValueError(
                f"Cannot issue: order currency {order.currency} is not supported by seller entity "
                f"'{seller_entity.code}' (doc L2: one currency per legal financial document)"
            )
    ledger = "live_event"
    invoice = Invoice(
        event_order_id=order.id, ledger=ledger, seller_legal_entity_id=seller_entity.code,
        number=_allocate_invoice_number(db, ledger=ledger, seller_entity=seller_entity),
        currency=order.currency, subtotal=order.subtotal, tax_amount=order.tax_amount,
        tax_treatment=order.tax_treatment, tax_jurisdiction=order.tax_jurisdiction,
        tax_source=order.tax_source, tax_rule_version=order.tax_rule_version,
        tax_effective_at=order.tax_effective_at, tax_exemption_reason=order.tax_exemption_reason,
        total_amount=order.total_amount, issue_date=datetime.now(timezone.utc), due_date=due_date,
        state="issued",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


# ── Seller legal entity registry (doc L1/P2) ─────────────────────────────────────────────

def list_seller_entities(db: Session, status: str | None = None):
    stmt = select(SellerLegalEntity).order_by(SellerLegalEntity.legal_name)
    if status:
        stmt = stmt.where(SellerLegalEntity.status == status)
    return db.scalars(stmt).all()


def create_seller_entity(db: Session, actor: User, **fields) -> SellerLegalEntity:
    """Registered as `draft`. Activation is a separate, audited step so verified merchant/
    tax identity is never a side effect of creating a row (doc Section 26 gate)."""
    if db.scalar(select(SellerLegalEntity).where(SellerLegalEntity.code == fields["code"])):
        raise ValueError(f"A seller legal entity with code '{fields['code']}' already exists")
    entity = SellerLegalEntity(**fields, status="draft")
    db.add(entity)
    audit(db, actor=actor, action="commercial.seller_entity.create", target_type="seller_legal_entity",
          target_id=entity.id, code=fields["code"])
    db.commit()
    db.refresh(entity)
    return entity


def activate_seller_entity(db: Session, entity: SellerLegalEntity, actor: User) -> SellerLegalEntity:
    """Only an ACTIVE entity may appear on an issued invoice. Requires the identity fields a
    financial document cannot legally omit — doc L1 makes the seller entity mandatory on
    issued documents, so activating an entity with no legal name or country would just move
    the fail-closed point downstream."""
    missing = [f for f in ("legal_name", "country") if not getattr(entity, f, None)]
    if missing:
        raise ValueError(f"Cannot activate a seller entity missing: {', '.join(missing)}")
    entity.status = "active"
    entity.effective_from = entity.effective_from or datetime.now(timezone.utc)
    audit(db, actor=actor, action="commercial.seller_entity.activate", target_type="seller_legal_entity",
          target_id=entity.id, code=entity.code)
    db.commit()
    db.refresh(entity)
    return entity


def resolve_seller_entity(db: Session, order: EventOrder) -> SellerLegalEntity:
    """The ACTIVE registered seller entity that will legally invoice this order — or a hard
    failure. Never falls back to a literal: the old `_seller_entity` returned the string
    "zoiko_tech_inc" whether or not any such entity existed, so an invoice could be issued
    under an unverified, unregistered seller (doc L1/P2, doc Section 26 "Merchant & finance").
    """
    account = db.get(CommercialAccount, order.commercial_account_id)
    if account is None or not account.seller_legal_entity_id:
        raise ValueError(
            "Cannot issue: this order's commercial account has no seller legal entity assigned. "
            "Assign a registered, active entity (see GET /commercial/seller-entities) first (doc L1)"
        )
    entity = db.scalar(
        select(SellerLegalEntity).where(SellerLegalEntity.code == account.seller_legal_entity_id)
    )
    if entity is None:
        raise ValueError(
            f"Cannot issue: seller legal entity '{account.seller_legal_entity_id}' is not in the "
            "registry. Register it before issuing financial documents under it (doc L1)"
        )
    if entity.status != "active":
        raise ValueError(
            f"Cannot issue: seller legal entity '{entity.code}' is '{entity.status}', not active. "
            "Verified merchant/tax identity must be confirmed and the entity activated first "
            "(doc Section 26 'Merchant & finance')"
        )
    return entity


# ── Invoice numbering (atomic, per ledger + seller entity) ───────────────────────────────

def _allocate_invoice_number(db: Session, *, ledger: str, seller_entity: SellerLegalEntity) -> str:
    """Atomically allocate the next number in this (ledger, seller entity) series.

    One INSERT ... ON CONFLICT DO UPDATE ... RETURNING: Postgres takes a row lock on the
    conflicting sequence row, applies the increment and returns the new value in a single
    statement, so two concurrent callers are serialized by the database and cannot receive
    the same number. Replaces a read-then-increment over `invoices` that could.

    Format is preserved (`ZST-LE-INV-000123`); an entity may override the prefix via
    SellerLegalEntity.invoice_number_prefix. Numbers are unique per entity, matching
    Invoice's UNIQUE(seller_legal_entity_id, number).
    """
    scope = f"{ledger}:{seller_entity.code}"
    next_value = db.execute(
        text(
            "INSERT INTO invoice_number_sequences (scope, last_value) VALUES (:scope, 1) "
            "ON CONFLICT (scope) DO UPDATE "
            "SET last_value = invoice_number_sequences.last_value + 1, updated_at = NOW() "
            "RETURNING last_value"
        ),
        {"scope": scope},
    ).scalar_one()
    prefix = (seller_entity.invoice_number_prefix or "ZST-LE-INV").rstrip("-")
    return f"{prefix}-{next_value:06d}"


# ── Change orders (doc Section 11/G) ─────────────────────────────────────────────────────

def _plan_change_order_lines(db: Session, order: EventOrder, changes: dict) -> tuple[list[dict], list[EventOrderLine], Decimal]:
    """Validate a change order's line operations and compute its true price delta.

    `changes` shape (both keys optional, at least one op required):

        {"add_lines":       [{"catalog_line_id": ..., "quantity": "2",
                              "is_addon": false, "is_complimentary": false}, ...],
         "remove_line_ids": [order_line_id, ...]}

    Every added line goes through the SAME validation `add_order_line` applies — it must come
    from this order's own published catalog version, carry a price, and match the order's
    currency — so a change order can never introduce a price that is not traceable to an
    approved price book (doc B1/B2). That traceability is the whole reason a change order may
    not be a bare number: `accept_change_order` used to just add `price_delta` to the subtotal
    without touching any line, which left `sum(lines) != subtotal` and a delta with no
    provenance whatsoever.
    """
    add_specs = changes.get("add_lines") or []
    remove_ids = changes.get("remove_line_ids") or []
    if not add_specs and not remove_ids:
        raise ValueError(
            "A change order must carry line operations (`add_lines` and/or `remove_line_ids`). "
            "A bare price adjustment has no catalog provenance and would leave the order's "
            "subtotal disagreeing with its own lines — to change a price without changing "
            "scope, raise a `price_override` or `discount` commercial exception instead "
            "(doc B1: no charge without an order line)"
        )

    planned: list[dict] = []
    delta = Decimal(0)
    for spec in add_specs:
        line_id = spec.get("catalog_line_id")
        if not line_id:
            raise ValueError("Each add_lines entry requires a catalog_line_id")
        catalog_line = db.get(CatalogLine, uuid.UUID(str(line_id)))
        if catalog_line is None:
            raise ValueError(f"Catalog line {line_id} not found")
        if catalog_line.catalog_version_id != order.catalog_version_id:
            raise ValueError(
                "Catalog line belongs to a different catalog version than this order — a line's "
                "price must be traceable to the order's own approved catalog version (doc B1/B2)"
            )
        if catalog_line.unit_price is None or not catalog_line.currency:
            raise ValueError("Catalog line has no price/currency set — cannot add to an order (doc B2)")
        if catalog_line.currency != order.currency:
            raise ValueError(
                f"Catalog line is priced in {catalog_line.currency} but this order is in "
                f"{order.currency} — one currency per order/document (doc L2)"
            )
        quantity = Decimal(str(spec.get("quantity", 1)))
        if quantity <= 0:
            raise ValueError("Change order line quantity must be greater than zero")
        is_complimentary = bool(spec.get("is_complimentary", False))
        unit_price = Decimal(0) if is_complimentary else catalog_line.unit_price
        line_total = unit_price * quantity
        planned.append({
            "catalog_line": catalog_line, "quantity": quantity, "unit_price": unit_price,
            "line_total": line_total, "is_addon": bool(spec.get("is_addon", False)),
            "is_complimentary": is_complimentary,
        })
        delta += line_total

    removing: list[EventOrderLine] = []
    for raw_id in remove_ids:
        line = db.get(EventOrderLine, uuid.UUID(str(raw_id)))
        if line is None or line.event_order_id != order.id:
            raise ValueError(f"Order line {raw_id} does not belong to this order")
        removing.append(line)
        delta -= Decimal(line.line_total)

    return planned, removing, delta


def _exception_approver_holds_finance(db: Session, exception: CommercialException) -> bool:
    """Whether the human who approved this exception holds Finance authority.

    doc Section 25: a price OVERRIDE is a Finance decision. The approve endpoint is already
    gated on the `write_off` column, but the exception could have been approved before that
    gating existed, or by an account whose role changed since — so the change-order path
    re-checks the recorded approver rather than trusting that the route must have been correct.
    """
    if exception.approver_id is None:
        return False
    approver = db.get(User, exception.approver_id)
    if approver is None:
        return False
    from ..security import commercial_can
    return commercial_can(approver, "finance") or commercial_can(approver, "write_off")


def create_change_order(db: Session, order: EventOrder, *, changes: dict, price_delta: Decimal,
                         service_impact: str | None = None, risk_impact: str | None = None,
                         capacity_impact: str | None = None, reason: str | None = None,
                         actor: User | None = None) -> ChangeOrder:
    """Raise a post-acceptance commercial delta (doc G1).

    The order must already be accepted — a draft order is still edited directly through
    `add_order_line`, and routing that through a change order would be ceremony.

    `price_delta` is now DERIVED from the line operations, not taken on trust. A caller-supplied
    value is accepted only when it matches, and refused otherwise — the same "refuse rather
    than silently substitute" rule `authorize_payment` uses for amounts, because quietly
    booking a different delta than the caller stated is its own hazard.

    Approval rules (doc Section 25):

      INCREASE (delta > 0)  — allowed on an accepted order, no exception needed. The customer
                              still has to accept the change order itself, which is the
                              acceptance that matters for extra scope.
      DECREASE (delta < 0)  — requires an approved `discount` or `price_override` exception on
                              THIS order. Reducing revenue is a governed act; without this a
                              single actor could create and accept a change order that
                              discounted an order to nothing.
      OVERRIDE (price_override) — additionally requires that the exception was approved by
                              someone holding Finance authority, re-checked against the
                              recorded approver rather than assumed from the route.

    The authorising exception is stored on the row (`approval_exception_id`), so "what
    permitted this reduction" is answerable from the change order itself.
    """
    if order.status not in ("accepted", "active"):
        raise ValueError(
            f"Cannot raise a change order against an order in status '{order.status}' — change "
            "orders exist to amend an ACCEPTED order (doc G1); edit a draft order's lines directly"
        )
    if not (reason and reason.strip()):
        raise ValueError(
            "A change order requires a reason — a commercial delta with no stated grounds is "
            "not auditable (doc Section 25)"
        )
    planned, removing, computed = _plan_change_order_lines(db, order, changes)
    if price_delta is not None and Decimal(price_delta) != computed:
        raise ValueError(
            f"Stated price_delta {price_delta} does not match the {computed} computed from this "
            "change order's line operations. The delta is determined by the catalog-priced lines "
            "being added and removed, not by the request — omit it to accept the computed value"
        )

    authorising: CommercialException | None = None
    if computed < 0:
        discount = active_exception(db, exception_type="discount", order_id=order.id, gate="pricing")
        override = active_exception(db, exception_type="price_override", order_id=order.id,
                                    gate="pricing")
        authorising = discount or override
        if authorising is None:
            raise ValueError(
                f"This change order reduces the order by {abs(computed)} {order.currency}. A "
                "revenue reduction requires an approved `discount` or `price_override` "
                "commercial exception on this order first (doc Section 25 discount approval)"
            )
        # A price OVERRIDE is Finance's call, not Sales'. A plain `discount` is satisfied by the
        # ordinary approval path; an override additionally needs a Finance approver on record.
        if authorising.exception_type == "price_override" and not _exception_approver_holds_finance(db, authorising):
            raise ValueError(
                f"Exception {authorising.id} is a price override but was not approved by an "
                "actor holding Finance authority. A price override requires Finance approval "
                "(doc Section 25)"
            )

    co = ChangeOrder(
        event_order_id=order.id, prior_order_version=order.order_version, changes=changes,
        price_delta=computed, reason=reason.strip(), service_impact=service_impact,
        risk_impact=risk_impact, capacity_impact=capacity_impact,
        requested_by=actor.id if actor else None,
        approval_exception_id=authorising.id if authorising is not None else None,
        status="draft",
    )
    db.add(co)
    db.flush()
    audit(db, actor=actor, action="commercial.change_order.create", target_type="change_order",
          target_id=co.id, org_id=_order_org_id(db, order.id), reason=co.reason,
          price_delta=str(computed), direction="increase" if computed >= 0 else "decrease",
          prior_order_version=order.order_version,
          lines_added=len(planned), lines_removed=len(removing),
          approval_exception_id=None if authorising is None else str(authorising.id),
          approval_exception_type=None if authorising is None else authorising.exception_type)
    db.commit()
    db.refresh(co)
    return co


def _persist_change_order(db: Session, co: ChangeOrder) -> ChangeOrder:
    db.add(co)
    db.commit()
    db.refresh(co)
    return co


def accept_change_order(db: Session, change_order: ChangeOrder, actor: User) -> ChangeOrder:
    """Customer acceptance, then apply: materialize the line changes, bump the order version.

    The prior version's EventOrderVersion snapshot is never touched, so removing a line does
    not destroy history — the removed line survives verbatim in the snapshot of the version
    that carried it (doc T4: corrections are additive). That is what makes deleting the live
    row safe rather than lossy.

    The subtotal is RECOMPUTED from the resulting lines rather than incremented by the delta,
    so the order's total and its own lines can never disagree — the previous implementation
    only moved the total and left every line untouched.
    """
    if change_order.status not in ("draft", "pending_acceptance"):
        raise ValueError(f"Cannot accept a change order in status '{change_order.status}'")
    order = db.get(EventOrder, change_order.event_order_id)
    if order.order_version != change_order.prior_order_version:
        raise ValueError("This change order is stale — a newer version has already been applied")
    if order.status not in ("accepted", "active"):
        raise ValueError(f"Cannot apply a change order to an order in status '{order.status}'")

    # Re-validate at apply time: the catalog or the order's lines may have moved since the
    # change order was raised, and applying a stale plan would be how an unpriced or
    # foreign-catalog line slips in.
    planned, removing, computed = _plan_change_order_lines(db, order, change_order.changes or {})
    if computed != Decimal(change_order.price_delta):
        raise ValueError(
            f"This change order's line operations now compute to {computed}, not the "
            f"{change_order.price_delta} recorded when it was raised — the underlying catalog or "
            "order lines have changed. Raise a fresh change order against the current state"
        )

    # Capture the removed rows' identity BEFORE deleting them — once gone, the only other
    # record is the previous version's snapshot, and `applied_lines` is what makes "which lines
    # did THIS change order touch" answerable without diffing two snapshots.
    removed_evidence = [
        {"id": str(line.id), "service_code": line.service_code,
         "quantity": str(line.quantity), "unit_price": str(line.unit_price),
         "line_total": str(line.line_total)}
        for line in removing
    ]
    for line in removing:
        db.delete(line)
    added_evidence = []
    for spec in planned:
        catalog_line = spec["catalog_line"]
        new_line = EventOrderLine(
            event_order_id=order.id, catalog_line_id=catalog_line.id,
            service_code=catalog_line.service_code, description=catalog_line.name,
            quantity=spec["quantity"], unit_price=spec["unit_price"], line_total=spec["line_total"],
            tax_treatment=catalog_line.tax_treatment, unit_basis=catalog_line.unit_basis,
            is_addon=spec["is_addon"], is_complimentary=spec["is_complimentary"],
        )
        db.add(new_line)
        added_evidence.append(new_line)
    db.flush()
    change_order.applied_lines = {
        "added": [
            {"id": str(l.id), "service_code": l.service_code, "quantity": str(l.quantity),
             "unit_price": str(l.unit_price), "line_total": str(l.line_total)}
            for l in added_evidence
        ],
        "removed": removed_evidence,
    }

    change_order.customer_acceptance = True
    change_order.accepted_at = datetime.now(timezone.utc)
    change_order.approved_by = actor.id
    change_order.effective_at = datetime.now(timezone.utc)
    change_order.status = "accepted"
    order.order_version += 1
    # Authoritative: the sum of the order's actual lines.
    remaining = db.scalars(
        select(EventOrderLine).where(EventOrderLine.event_order_id == order.id)
    ).all()
    order.subtotal = sum((Decimal(l.line_total) for l in remaining), Decimal(0))
    # A price delta moves the tax basis, so any existing determination is stale — clear it
    # and force a re-determination before the changed order can be invoiced again (doc L4).
    _clear_tax_determination(order)
    order.total_amount = order.subtotal
    # The prior version's snapshot already exists and is never touched; this adds the NEW
    # version alongside it, so both remain readable (doc Section 28: corrections are additive).
    #
    # EXPIRE the lines relationship rather than refreshing the order: snapshot_order_version
    # iterates order.lines, which is stale after the inserts/deletes above. db.refresh(order)
    # would reload them — and also discard the still-pending subtotal, order_version and
    # cleared-tax attributes set just above, writing a snapshot (and a row) with the OLD
    # economics. Expiring one relationship reloads exactly what is stale.
    db.expire(order, ["lines"])
    snapshot_order_version(db, order, actor=actor, change_order=change_order)
    audit(db, actor=actor, action="commercial.change_order.accept", target_type="change_order",
          target_id=change_order.id, org_id=_order_org_id(db, order.id),
          reason=change_order.reason,
          price_delta=str(change_order.price_delta), order_version=order.order_version,
          new_subtotal=str(order.subtotal), lines_added=len(planned), lines_removed=len(removing),
          requested_by=None if not change_order.requested_by else str(change_order.requested_by),
          approved_by=str(actor.id),
          approval_exception_id=(None if not change_order.approval_exception_id
                                 else str(change_order.approval_exception_id)),
          # Clearing the determination IS the tax-recalculation trigger: the order cannot be
          # invoiced or charged again until Finance records a new one against the new subtotal.
          tax_determination_cleared=True)
    event = db.get(Event, order.event_id)
    if event is not None:
        sync_lifecycle(db, event, order, actor=actor, trigger="change_order.accept",
                        reason=change_order.reason)
    db.commit()
    db.refresh(change_order)
    return change_order


# ── Cancellation workflow (doc Section 9/E) ──────────────────────────────────────────────

def cancellation_lead_time_hours(event: Event, requested_at: datetime | None = None) -> float:
    """Hours between the request and the event start. `inf` when the event has no start time —
    an unscheduled event is maximally far out, which lands it in the most generous published
    band rather than the least."""
    requested_at = requested_at or datetime.now(timezone.utc)
    if event.start_time is None:
        return float("inf")
    return max((event.start_time - requested_at).total_seconds() / 3600, 0)


def calculate_cancellation(db: Session, event: Event, order: EventOrder, *, requested_at: datetime | None = None):
    """Returns (policy, refund_amount) or (None, None) if no policy is configured for this
    vertical/risk-tier/lead-time — the caller must treat that as a fail-closed block on
    automatic cancellation, not invent a percentage (doc E1).

    `refund_amount` is the POLICY ENTITLEMENT — a percentage of the contracted total. It is
    deliberately NOT capped here, because the policy figure and the refundable figure answer
    different questions and Finance needs to see both; `cancel_order` applies the cap.
    """
    lead_time_hours = cancellation_lead_time_hours(event, requested_at)
    policy = find_cancellation_policy(
        db, vertical=event.category or "unspecified", risk_tier=order.risk_tier, lead_time_hours=lead_time_hours,
    )
    if policy is None:
        return None, None
    refund_amount = (order.total_amount or Decimal(0)) * (policy.refund_percentage / Decimal(100))
    return policy, refund_amount.quantize(Decimal("0.01"))


def cancel_order(db: Session, event: Event, order: EventOrder, actor: User, *, reason: str,
                  correlation_id: str | None = None) -> dict:
    """Cancel an order: release capacity, and raise refund remedies for money actually held.

    Three defects are closed here.

    1. There was NO status guard, so cancelling twice re-ran the whole routine and filed a
       second set of refunds against the same order.
    2. The refund was a percentage of `order.total_amount` with no reference to what had been
       collected, so cancelling an UNPAID order produced a positive refund obligation for money
       never received. The policy entitlement is now capped at the refundable cash.
    3. The RefundCredit was created with `source_payment_id` NULL, which made
       `execute_refund_credit` skip the provider call entirely — the remedy reached `executed`
       with the payment still `paid` and nothing returned. Refunds are now bound to real
       payments via allocate_refund_across_payments.

    Both figures are returned: `policy_refund_amount` (the entitlement) and `refund_amount`
    (what will actually be returned). When the cap bites, that difference is the number
    Finance has to reconcile, so it is surfaced rather than silently collapsed.
    """
    if order.status in ("canceled", "terminated"):
        raise ValueError(f"Order is already '{order.status}' — it cannot be cancelled again")
    if order.status == "completed":
        raise ValueError(
            "This order is completed — the event was delivered. Post-delivery money movement is "
            "a credit or refund remedy against the delivered order, not a cancellation "
            "(doc Section 15/K)"
        )
    correlation_id = correlation_id or new_correlation_id()
    policy, policy_refund = calculate_cancellation(db, event, order)
    if policy is None:
        raise ValueError(
            "No published cancellation policy matches this event's vertical/risk tier/lead time — "
            "cancellation is blocked until Commercial/Finance configures one (doc E1, fail closed)"
        )

    # A refund may never exceed the cash we are actually holding for this order.
    settlement = order_settlement(db, order.id)
    refund_amount = min(policy_refund or Decimal(0), settlement["refundable"])

    order.status = "canceled"
    # Release every state that still holds inventory, not just hard reservations — a soft
    # hold on a canceled order would otherwise keep occupying its pool until it expired
    # (doc E2: "release recoverable capacity").
    reservations = db.scalars(
        select(CapacityReservation).where(
            CapacityReservation.event_id == event.id,
            CapacityReservation.state.in_(("soft_held", "hard_reserved")),
        )
    ).all()
    for r in reservations:
        release_capacity(db, r, reason="order_canceled", actor=actor, correlation_id=correlation_id)

    refund_credits = []
    if refund_amount > 0:
        refund_credits = allocate_refund_across_payments(
            db, order, refund_amount, reason_code="customer_cancellation",
            policy_version=policy.version_label, actor=actor,
        )
    audit(db, actor=actor, action="commercial.order.cancel", target_type="event_order", target_id=order.id,
          org_id=event.org_id, correlation_id=correlation_id, reason=reason,
          policy_version=policy.version_label,
          policy_refund_amount=str(policy_refund), refund_amount=str(refund_amount),
          refundable_at_cancellation=str(settlement["refundable"]),
          capped=bool((policy_refund or Decimal(0)) > refund_amount),
          released_reservations=len(reservations), refund_credits=len(refund_credits))
    sync_lifecycle(db, event, order, actor=actor, trigger="order.cancel",
                    correlation_id=correlation_id, reason=reason)
    db.commit()
    for rc in refund_credits:
        db.refresh(rc)
    return {
        "order": order, "policy": policy,
        # The entitlement the policy computed, before the refundable-cash cap.
        "policy_refund_amount": policy_refund,
        "refund_amount": refund_amount,
        # Preserved for existing callers (routers/commercial.py's CancellationResult); a
        # cancellation can now legitimately produce several remedies, one per source payment.
        "refund_credit": refund_credits[0] if refund_credits else None,
        "refund_credits": refund_credits,
    }


# ── Reschedule (doc Section 9 — move the window, preserve the history) ───────────────────

def reschedule_event(db: Session, event: Event, order: EventOrder | None, actor: User, *,
                     new_start: datetime, new_end: datetime | None, reason: str,
                     correlation_id: str | None = None) -> dict:
    """Move an event's window, releasing the capacity held for the old one.

    Three things make this a workflow rather than a field update.

    1. HISTORY. The previous window is what the customer was contracted to receive, and a bare
       `event.start_time = x` destroys it. EventReschedule keeps the original, the order version
       that was effective, and the lead time at the moment of the request.

    2. CAPACITY. A reservation covers a specific window (doc C1) — an operator rostered for
       Tuesday 14:00 does not cover Thursday. Carrying the old reservation across would leave
       the event appearing capacity-backed by inventory that does not cover it, so every holding
       reservation is RELEASED and must be re-held against the new window. The event visibly
       drops out of CONFIRMED until that happens, which is the honest state.

    3. NO INVENTED FEE. There is no published reschedule-fee policy registry, and inventing a
       percentage is exactly what doc E1 forbids. Any commercial consequence is raised as a
       normal ChangeOrder and linked via `change_order_id`. The lead-time band the request fell
       into is recorded so Commercial can decide on the facts.

    Refuses once the event is delivering or delivered: at that point the thing to move no longer
    exists, and the remedy is cancellation or a credit.
    """
    if new_end is not None and new_end <= new_start:
        raise ValueError("Reschedule end_time must be after start_time")
    if event.status in _EVENT_STATES_LIVE:
        raise ValueError(
            f"Cannot reschedule an event that is '{event.status}' — it is delivering now. End it, "
            "then cancel or credit the order (doc Section 9)"
        )
    if event.status in _EVENT_STATES_COMPLETED or event.status == "cancelled":
        raise ValueError(f"Cannot reschedule a '{event.status}' event")
    if order is not None and order.status in ("canceled", "terminated", "completed"):
        raise ValueError(f"Cannot reschedule against a '{order.status}' order")

    correlation_id = correlation_id or new_correlation_id()
    lead_time = cancellation_lead_time_hours(event)
    previous_start, previous_end = event.start_time, event.end_time

    reservations = db.scalars(
        select(CapacityReservation).where(
            CapacityReservation.event_id == event.id,
            CapacityReservation.state.in_(("soft_held", "hard_reserved")),
        )
    ).all()
    released = {str(r.id): r.resource_type for r in reservations}
    for r in reservations:
        release_capacity(db, r, reason="event_rescheduled", actor=actor,
                          correlation_id=correlation_id)

    event.start_time = new_start
    event.end_time = new_end
    record = EventReschedule(
        event_id=event.id, event_order_id=order.id if order is not None else None,
        previous_start_time=previous_start, previous_end_time=previous_end,
        new_start_time=new_start, new_end_time=new_end, reason=reason,
        order_version=order.order_version if order is not None else None,
        released_reservations=released or None,
        # inf (an event with no start time) is not representable as a Numeric — record NULL.
        lead_time_hours_at_request=(
            None if lead_time == float("inf") else Decimal(str(round(lead_time, 2)))
        ),
        requested_by=actor.id, correlation_id=correlation_id,
    )
    db.add(record)
    db.flush()
    audit(db, actor=actor, action="commercial.event.reschedule", target_type="event",
          target_id=event.id, org_id=event.org_id, correlation_id=correlation_id, reason=reason,
          previous_start=previous_start.isoformat() if previous_start else None,
          new_start=new_start.isoformat(),
          lead_time_hours_at_request=None if lead_time == float("inf") else round(lead_time, 2),
          released_reservations=len(reservations),
          event_order_id=None if order is None else str(order.id))
    lifecycle = sync_lifecycle(db, event, order, actor=actor, trigger="event.reschedule",
                                correlation_id=correlation_id, reason=reason)
    db.commit()
    db.refresh(record)
    return {
        "reschedule": record, "event": event, "order": order,
        "released_reservations": len(reservations),
        "lifecycle_state": lifecycle["state"],
        # Re-holding is a deliberate separate step; say so rather than implying it happened.
        "capacity_requires_rehold": bool(reservations),
    }


def list_reschedules(db: Session, event_id) -> list[EventReschedule]:
    """Append-only reschedule history, oldest first."""
    return db.scalars(
        select(EventReschedule)
        .where(EventReschedule.event_id == event_id)
        .order_by(EventReschedule.created_at)
    ).all()


# ── Write-off (doc Section 25 — the RBAC matrix's fifth financial action) ─────────────────

def execute_write_off(db: Session, exception: CommercialException, actor: User, *,
                      correlation_id: str | None = None) -> RefundCredit:
    """Turn an APPROVED write-off exception into an executed credit against the order.

    `write_off` existed as an RBAC action in security.COMMERCIAL_ACTIONS with no endpoint and
    no code path behind it — the permission gated nothing. A write-off is the decision to stop
    pursuing an amount the customer owes: no cash moves, but the balance must stop being
    outstanding, otherwise the order sits in FINANCIAL_HOLD forever and blocks delivery.

    Implemented as a `fee_waiver` RefundCredit rather than a new mechanism, because
    order_settlement already treats waivers as satisfying a balance without being refundable
    cash. Maker-checker is inherited from the exception it is executing: this cannot run until
    a DIFFERENT human approved that exception, so no single actor can write off a balance.
    """
    if exception.exception_type != "write_off":
        raise ValueError(
            f"Exception {exception.id} is a '{exception.exception_type}', not a write_off"
        )
    if exception.status != "approved":
        raise ValueError(
            f"Cannot execute a write-off from an exception in status '{exception.status}' — it "
            "must be approved by someone other than the requester first (doc Section 25)"
        )
    if exception.event_order_id is None:
        raise ValueError("A write-off exception must target an order")
    if exception.amount_exposure is None or exception.amount_exposure <= 0:
        raise ValueError(
            "A write-off requires a positive amount_exposure — the amount being written off is "
            "the whole substance of the decision and cannot be left unstated"
        )
    order = db.get(EventOrder, exception.event_order_id)
    if order is None:
        raise ValueError("Write-off references an order that no longer exists")
    existing = db.scalar(
        select(RefundCredit).where(
            RefundCredit.event_order_id == order.id,
            RefundCredit.reason_code == f"write_off:{exception.id}",
        )
    )
    if existing is not None:
        # Idempotent: re-executing an approved write-off must not waive the amount twice.
        return existing

    correlation_id = correlation_id or new_correlation_id()
    credit = RefundCredit(
        event_order_id=order.id, type="fee_waiver", amount=exception.amount_exposure,
        reason_code=f"write_off:{exception.id}", policy_version=None,
        requested_by=exception.requested_by, approved_by=exception.approver_id,
        status="executed",
    )
    db.add(credit)
    db.flush()
    reallocate_schedules(db, order.id)
    audit(db, actor=actor, action="commercial.write_off.execute", target_type="refund_credit",
          target_id=credit.id, org_id=_order_org_id(db, order.id), correlation_id=correlation_id,
          amount=str(exception.amount_exposure), exception_id=str(exception.id),
          requested_by=str(exception.requested_by), approved_by=str(exception.approver_id),
          reason=exception.rationale)
    event = db.get(Event, order.event_id)
    if event is not None:
        sync_lifecycle(db, event, order, actor=actor, trigger="write_off.execute",
                        correlation_id=correlation_id, reason=exception.rationale)
    db.commit()
    db.refresh(credit)
    return credit


# ── Ledger 3: audience/organizer commerce (doc H3/H4, Section 3) ─────────────────────────
# Ledger 3 is DELIBERATELY UNBUILT. Nothing in this codebase lets an audience member or an
# organizer pay for anything, and Ledger 2's money is isolated from it by construction:
# a Payment references an EventOrder, an EventOrder is owned by a CommercialAccount, and an
# Invoice carries ledger="live_event".
#
# The risk this guard addresses is not today's code — it is the future feature that wires an
# audience payment into the nearest available payment path, which is Ledger 2's. Separation by
# absence is separation only until someone adds something. So:
#   * AUDIENCE_COMMERCE_ENABLED is the single switch a future Ledger 3 must flip.
#   * assert_audience_commerce_disabled() is the call any such entry point must make.
#   * assert_ledger_isolation() refuses to settle a Ledger 2 invoice with non-Ledger-2 money.
# A regression test asserts no audience-payable route exists (test_commercial_lifecycle.py).

AUDIENCE_COMMERCE_ENABLED = False
LIVE_EVENT_LEDGER = "live_event"
AUDIENCE_COMMERCE_LEDGER = "audience_commerce"


def assert_audience_commerce_disabled(context: str = "audience payment") -> None:
    """Refuse any attempt to take money from an audience member or organizer.

    Ledger 3 is a separate, unbuilt product (doc H3/H4). Until it exists WITH its own payment
    routing, seller identity and invoice series, an audience payment has nowhere legitimate to
    land — and the nearest available landing place is a Live Event order, which is exactly the
    cross-ledger settlement doc Section 3 prohibits.

    Deliberately has no caller today: there is no audience payment path to guard. It is the
    named switch and the refusal a future Ledger 3 entry point must go through, so enabling
    audience commerce is a decision someone makes here rather than a side effect of wiring a
    payment into the nearest available ledger.

    A companion "assert_ledger_isolation(invoice, payment)" was written and then removed: with
    Payment -> EventOrder <- Invoice, cross-ledger settlement is not expressible, so the check
    could never fire and would only have looked like enforcement.
    """
    if not AUDIENCE_COMMERCE_ENABLED:
        raise ValueError(
            f"Audience/organizer commerce is not enabled ({context}). Ledger 3 is a separate "
            "product with its own payment routing and invoice series; it must never settle "
            "against a ZoikoStream Live Event order or a platform subscription (doc Section 3)"
        )


# ── Remedies / refunds / credits (doc Section 15/K, maker-checker) ──────────────────────

def approve_refund_credit(db: Session, refund_credit: RefundCredit, approver: User) -> RefundCredit:
    """Maker-checker: the approver must not be the requester (doc Section 25)."""
    if refund_credit.status != "pending":
        raise ValueError(f"Cannot approve a refund/credit in status '{refund_credit.status}'")
    if refund_credit.requested_by and str(refund_credit.requested_by) == str(approver.id):
        raise ValueError("Maker-checker violation: the approver must differ from the requester")
    refund_credit.approved_by = approver.id
    refund_credit.status = "approved"
    audit(db, actor=approver, action="commercial.refund_credit.approve", target_type="refund_credit",
          target_id=refund_credit.id, amount=str(refund_credit.amount))
    db.commit()
    db.refresh(refund_credit)
    return refund_credit


def execute_refund_credit(db: Session, refund_credit: RefundCredit, *, actor: User | None = None,
                           correlation_id: str | None = None) -> RefundCredit:
    """Move the money for an approved remedy. The payment's resulting state now goes through
    the state machine (a refund against a `failed` payment is refused, not assigned), and the
    execution itself is audited — previously only the APPROVAL was, so the actual money-out
    event left no audit entry."""
    if refund_credit.status != "approved":
        raise ValueError("Refund/credit must be approved before it can be executed (maker-checker)")
    correlation_id = correlation_id or new_correlation_id()
    if refund_credit.type == "refund":
        # A refund with no source payment cannot move money — the provider call below is what
        # returns it, and that needs a payment reference. This used to fall through silently:
        # the remedy reached `executed` with nothing refunded and the payment still `paid`,
        # which is how a cancellation refund became a customer email and no money
        # (see cancel_order / allocate_refund_across_payments).
        if not refund_credit.source_payment_id:
            raise ValueError(
                "This refund is not bound to a source payment, so no money can be returned. "
                "A refund must name the payment it comes out of — raise it against a payment, "
                "or record it as a `credit` if no cash is to be returned"
            )
        payment = db.get(Payment, refund_credit.source_payment_id)
        if payment is None:
            raise ValueError("Refund references a payment that no longer exists")
        # Cap against what is STILL refundable, not the payment's face value: the previous
        # check compared against payment.amount alone, so the same payment could be refunded
        # in full repeatedly.
        refundable = payment_refundable_amount(db, payment)
        if refund_credit.amount > refundable:
            raise ValueError(
                f"Refund of {refund_credit.amount} exceeds the {refundable} still refundable on "
                f"this payment (face value {payment.amount}, already refunded "
                f"{Decimal(payment.amount) - refundable}) — a remedy may not return more than was taken"
            )
        target = "part_refunded" if refund_credit.amount < refundable else "refunded"
        error = payment_transition_error(payment.state, target)
        if error:
            audit(db, actor=actor, action="commercial.payment.transition_rejected", target_type="payment",
                  target_id=payment.id, org_id=_order_org_id(db, payment.event_order_id),
                  correlation_id=correlation_id, from_state=payment.state, to_state=target,
                  source="refund_execute", reason=error)
            db.commit()
            raise ValueError(f"Cannot refund: {error}")
        provider = payment_svc.get_provider(payment.provider)
        result = provider.refund(payment.provider_payment_ref, refund_credit.amount,
                                  idempotency_key=str(refund_credit.id))
        refund_credit.provider_ref = result.provider_payment_ref
        apply_payment_state(db, payment, target, actor=actor, correlation_id=correlation_id,
                            source="refund_execute", refund_credit_id=str(refund_credit.id))

    # Mark executed BEFORE re-deriving allocation. order_settlement only counts remedies in
    # status `executed`, so reallocating first would net this refund out of nothing and leave
    # the milestones showing money that has just been returned.
    refund_credit.status = "executed"
    db.flush()
    order = db.get(EventOrder, refund_credit.event_order_id)
    reallocate_schedules(db, refund_credit.event_order_id)
    if refund_credit.incident_id:
        incident = db.get(EventIncident, refund_credit.incident_id)
        if incident:
            incident.review_state = "credit_refund_executed"
    audit(db, actor=actor, action="commercial.refund_credit.execute", target_type="refund_credit",
          target_id=refund_credit.id, org_id=_order_org_id(db, refund_credit.event_order_id),
          correlation_id=correlation_id, amount=str(refund_credit.amount), type=refund_credit.type,
          provider_ref=refund_credit.provider_ref,
          source_payment_id=None if not refund_credit.source_payment_id else str(refund_credit.source_payment_id))
    # Returning money can un-satisfy financial readiness, which can drop the order out of
    # CONFIRMED — that regression is a recorded lifecycle transition, not a silent change.
    if order is not None:
        event = db.get(Event, order.event_id)
        if event is not None:
            sync_lifecycle(db, event, order, actor=actor, trigger="refund_credit.execute",
                            correlation_id=correlation_id)
    db.commit()
    db.refresh(refund_credit)
    return refund_credit


# ── Disputes / chargebacks (doc Section 20/P4) ───────────────────────────────────────────
# Deliberately NOT built on RefundCredit's maker-checker flow: "a chargeback is not the
# same as a refund" (doc P4) — money movement here is provider-driven (the cardholder's
# bank decides, not Zoiko staff approving a request), so the workflow is case tracking
# (open -> evidence -> won/lost) rather than an internal approve/execute gate. The original
# Payment/Invoice amounts are never edited by any function below (doc P4).

def open_dispute(db: Session, payment: Payment, actor: User, *, reason_code: str,
                  amount: Decimal | None = None) -> PaymentDispute:
    """Records a chargeback case. In production this is triggered by a provider webhook;
    MockPaymentProvider exposes it as a direct call since there is no webhook source until
    a real merchant account exists (services.payments module docstring) — the router gates
    this action to Finance/Billing Ops staff (security.commercial_can 'refund_approve')
    rather than accepting it unauthenticated, unlike a real webhook would be."""
    if payment.state not in ("paid", "part_refunded"):
        raise ValueError(f"Cannot open a dispute on a payment in state '{payment.state}'")
    dispute_amount = amount if amount is not None else payment.amount
    provider = payment_svc.get_provider(payment.provider)
    result = provider.open_dispute(payment.provider_payment_ref, amount=dispute_amount, reason_code=reason_code)
    dispute = PaymentDispute(
        id=uuid.uuid4(), payment_id=payment.id, event_order_id=payment.event_order_id,
        provider=payment.provider, provider_dispute_ref=result.provider_dispute_ref,
        reason_code=reason_code, amount=dispute_amount, currency=payment.currency,
        reserve_amount=result.reserve_amount, status=result.status,
        evidence_due_by=result.evidence_due_by, case_owner_id=actor.id,
    )
    db.add(dispute)
    correlation_id = new_correlation_id()
    # Through the state machine, not a direct assignment — so the move is validated and
    # audited on the same footing as every other payment transition.
    apply_payment_state(db, payment, "disputed", actor=actor, correlation_id=correlation_id,
                        source="dispute_open", dispute_id=str(dispute.id))
    audit(db, actor=actor, action="commercial.dispute.open", target_type="payment_dispute",
          target_id=dispute.id, org_id=_order_org_id(db, payment.event_order_id),
          correlation_id=correlation_id, amount=str(dispute_amount), reason_code=reason_code)
    db.commit()
    db.refresh(dispute)
    return dispute


# Which payment state a dispute status implies. Only won/lost move money: a dispute that is
# merely open means the funds are contested, not returned (doc P4). `withdrawn` (Stripe's
# `warning_closed`) means the inquiry was dropped before becoming a chargeback, so the payment
# returns to the state it was contested from.
_DISPUTE_PAYMENT_STATE = {
    "evidence_required": "disputed",
    "evidence_submitted": "disputed",
    "opened": "disputed",
    "won": "paid",
    "withdrawn": "paid",
    "lost": "reversed",
}


def ingest_dispute_event(db: Session, *, provider: str, provider_event_id: str, event_type: str,
                         raw_body: bytes, signature_verified: bool, dispute: dict,
                         payload: dict | None = None, occurred_at: datetime | None = None,
                         correlation_id: str | None = None,
                         provider_payment_ref: str | None = None,
                         idempotency_key_hint: str | None = None) -> dict:
    # `provider_payment_ref` and `idempotency_key_hint` arrive as part of the shared
    # provider-event envelope the webhook router passes to every ingest function. Accepted so
    # that envelope stays uniform — special-casing the call site is how one ingest path drifts
    # out of step with the others. The DISPUTE payload's own payment reference wins: it is the
    # charge the network is actually disputing, and on a dispute object the envelope's ref is
    # derived from the same field anyway.
    """Apply an inbound chargeback event to its PaymentDispute case and its Payment.

    This is the bridge that was missing. The case workflow, the DISPUTE_STATES vocabulary and
    the `paid -> disputed -> paid|reversed` transitions all existed; dispute events were filed
    as evidence and never reached them, so a real chargeback left the ledger reading `paid`
    while the money was gone.

    Ownership stays where it belongs:
      * the CARD NETWORK decides won/lost — we never invent an outcome, we map Stripe's own
        dispute.status through payments_stripe_events.STRIPE_DISPUTE_STATUS_MAP;
      * the PAYMENT STATE MACHINE decides whether the implied move is legal — a dispute on a
        `failed` payment is refused and audited, not assigned;
      * the INVOICE and the original payment AMOUNT are never touched (doc P4: "a disputed
        payment does not silently rewrite the original invoice or delivered event record").

    Idempotent on two levels: the provider event claim (exactly-once per Stripe event id) and
    the case identity (`provider`, `provider_dispute_ref`), so a redelivered or superseded
    dispute update advances the existing case rather than opening a second one.
    """
    claimed, duplicate_response = _claim_provider_event(
        db, provider=provider, provider_event_id=provider_event_id, event_type=event_type,
        raw_body=raw_body, signature_verified=signature_verified,
        provider_payment_ref=dispute.get("provider_payment_ref"), payload=payload,
        occurred_at=occurred_at, correlation_id=correlation_id,
    )
    if claimed is None:
        return duplicate_response
    record = claimed
    correlation_id = record.correlation_id

    def finish(status: str, result: dict, error: str | None = None) -> dict:
        record.processing_status = status
        record.processing_result = result
        record.processing_error = error
        record.processed_at = datetime.now(timezone.utc)
        db.commit()
        return {"duplicate": False, "applied": bool(result.get("applied")),
                "provider_event_id": str(record.id), "processing_status": status,
                "result": result, "correlation_id": correlation_id}

    if not signature_verified:
        audit(db, actor=None, action="commercial.provider_event.rejected",
              target_type="provider_event", target_id=record.id, correlation_id=correlation_id,
              reason="signature_not_verified", provider=provider, event_type=event_type)
        return finish("rejected", {"applied": False, "reason": "signature_not_verified"},
                      "signature not verified")

    case_status = dispute.get("status")
    if case_status is None:
        # An unmapped Stripe status. Retained rather than guessed — a status we do not
        # understand must not silently become "opened" and move a payment.
        return finish("processed", {"applied": False,
                                    "reason": f"unmapped dispute status "
                                              f"'{dispute.get('provider_status')}'",
                                    "follow_up_required": True})

    payment_ref = dispute.get("provider_payment_ref") or provider_payment_ref
    payment = None
    if payment_ref:
        payment = db.scalar(
            select(Payment).where(Payment.provider == provider,
                                  Payment.provider_payment_ref == payment_ref)
        )
    if payment is None:
        # Disputed money we cannot attribute. Parked for controlled matching exactly like any
        # other unattributable settlement (doc P5) — never guessed onto an order.
        settlement = UnmatchedSettlement(
            provider=provider, provider_payment_ref=payment_ref, provider_event_id=record.id,
            event_type=event_type, amount=dispute.get("amount"),
            currency=dispute.get("currency"), occurred_at=occurred_at,
            received_at=record.received_at, status="open", correlation_id=correlation_id,
            reason=(f"Dispute {dispute['provider_dispute_ref']} references payment "
                    f"'{payment_ref}', which does not exist here"),
        )
        db.add(settlement)
        db.flush()
        audit(db, actor=None, action="commercial.settlement.unmatched",
              target_type="unmatched_settlement", target_id=settlement.id,
              correlation_id=correlation_id, provider=provider, event_type=event_type,
              reason="dispute_for_unknown_payment",
              provider_dispute_ref=dispute["provider_dispute_ref"])
        return finish("processed", {"applied": False, "reason": "dispute_payment_unmatched",
                                    "unmatched_settlement_id": str(settlement.id)})

    record.payment_id = payment.id
    org_id = _order_org_id(db, payment.event_order_id)

    # Currency must agree, or the case would attach the wrong money to this order. Amount is
    # NOT compared: a partial chargeback of a larger payment is legitimate.
    currency = dispute.get("currency")
    if currency and currency.upper() != (payment.currency or "").upper():
        reason = (f"currency mismatch: dispute says {currency.upper()}, payment is "
                  f"{payment.currency}")
        audit(db, actor=None, action="commercial.provider_event.rejected",
              target_type="provider_event", target_id=record.id, org_id=org_id,
              correlation_id=correlation_id, reason=reason, provider=provider,
              event_type=event_type)
        return finish("rejected", {"applied": False, "reason": reason}, reason)

    existing = db.scalar(
        select(PaymentDispute).where(
            PaymentDispute.provider == provider,
            PaymentDispute.provider_dispute_ref == dispute["provider_dispute_ref"],
        )
    )
    disputed_amount = dispute.get("amount") or Decimal(payment.amount)
    reserve = dispute.get("reserve_amount")
    created_case = existing is None
    if existing is None:
        case = PaymentDispute(
            id=uuid.uuid4(), payment_id=payment.id, event_order_id=payment.event_order_id,
            provider=provider, provider_dispute_ref=dispute["provider_dispute_ref"],
            reason_code=dispute.get("reason_code") or "unspecified",
            amount=disputed_amount, currency=payment.currency,
            reserve_amount=reserve if reserve is not None else Decimal(0),
            status=case_status, evidence_due_by=dispute.get("evidence_due_by"),
            # No case owner: a webhook cannot assign a human. Finance picks it up from the
            # dispute list, and the audit entry is the notification.
            case_owner_id=None,
        )
        db.add(case)
        db.flush()
    else:
        case = existing
        # Never rewind a decided case. A redelivered `created` after a `won` must not reopen it.
        if case.status in ("won", "lost", "withdrawn") and case_status not in ("won", "lost"):
            return finish("replayed", {"applied": False, "reason": "case_already_resolved",
                                       "dispute_id": str(case.id), "status": case.status})
        case.status = case_status
        if dispute.get("evidence_due_by"):
            case.evidence_due_by = dispute["evidence_due_by"]
        if reserve is not None:
            case.reserve_amount = reserve
    if case_status in ("won", "lost", "withdrawn") and case.resolved_at is None:
        case.resolved_at = datetime.now(timezone.utc)

    target_state = _DISPUTE_PAYMENT_STATE[case_status]
    applied, error = apply_payment_state(
        db, payment, target_state, actor=None, correlation_id=correlation_id,
        source=f"dispute:{provider}", dispute_id=str(case.id),
        provider_status=dispute.get("provider_status"), event_type=event_type,
    )
    audit(db, actor=None,
          action="commercial.dispute.opened_from_provider" if created_case
                 else "commercial.dispute.updated_from_provider",
          target_type="payment_dispute", target_id=case.id, org_id=org_id,
          correlation_id=correlation_id, provider=provider, event_type=event_type,
          provider_dispute_ref=case.provider_dispute_ref,
          provider_status=dispute.get("provider_status"), status=case_status,
          amount=str(case.amount), reserve_amount=str(case.reserve_amount),
          payment_id=str(payment.id), payment_state=payment.state,
          reason=dispute.get("reason_code"),
          transition_applied=applied, transition_error=error)
    return finish("processed", {
        "applied": True, "dispute_id": str(case.id), "created": created_case,
        "status": case_status, "payment_id": str(payment.id),
        "payment_state": payment.state, "transition_applied": applied,
        "transition_error": error,
    })


def submit_dispute_evidence(db: Session, dispute: PaymentDispute, actor: User, *, evidence: dict) -> PaymentDispute:
    """doc P4 'evidence package'. Submitting evidence doesn't resolve the case — the
    provider/card network decides won/lost, reflected later via resolve_dispute (in
    production, from another webhook)."""
    if dispute.status not in ("opened", "evidence_required"):
        raise ValueError(f"Cannot submit evidence for a dispute in status '{dispute.status}'")
    # APPEND, never merge. A dict merge silently overwrote any key a previous submission had
    # already set, so the evidence a case was actually argued on could be replaced without
    # trace — in the one workflow whose entire purpose is holding evidence. Submissions are now
    # an ordered list, so superseded evidence stays readable alongside what replaced it.
    previous = dispute.evidence or {}
    submissions = list(previous.get("submissions") or [])
    if not submissions and previous:
        # Carry a pre-existing flat evidence dict in as the first submission rather than
        # discarding it (rows written before this change).
        submissions.append({
            "submitted_at": None, "submitted_by": None, "evidence": {
                k: v for k, v in previous.items() if k != "submissions"
            },
        })
    submissions.append({
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "submitted_by": str(actor.id),
        "evidence": evidence,
    })
    dispute.evidence = {"submissions": submissions}
    dispute.status = "evidence_submitted"
    audit(db, actor=actor, action="commercial.dispute.evidence_submitted",
          target_type="payment_dispute", target_id=dispute.id,
          org_id=_order_org_id(db, dispute.event_order_id),
          submission_number=len(submissions), evidence_keys=sorted(evidence.keys()))
    db.commit()
    db.refresh(dispute)
    return dispute


def resolve_dispute(db: Session, dispute: PaymentDispute, actor: User, *, won: bool) -> PaymentDispute:
    """won: the case is closed with funds retained — Payment reverts to 'paid' (the reserve
    doc P4 describes is released). lost: Zoiko loses the funds to the chargeback — Payment
    moves to 'reversed' (PAYMENT_STATES already anticipates this exact case). Either way
    this never touches the original Invoice/Payment amount fields (doc P4)."""
    if dispute.status not in ("opened", "evidence_required", "evidence_submitted"):
        raise ValueError(f"Cannot resolve a dispute in status '{dispute.status}'")
    dispute.status = "won" if won else "lost"
    dispute.resolved_at = datetime.now(timezone.utc)
    payment = db.get(Payment, dispute.payment_id)
    correlation_id = new_correlation_id()
    if payment:
        apply_payment_state(db, payment, "paid" if won else "reversed", actor=actor,
                            correlation_id=correlation_id, source="dispute_resolve",
                            dispute_id=str(dispute.id))
    audit(db, actor=actor, action="commercial.dispute.resolve", target_type="payment_dispute",
          target_id=dispute.id, correlation_id=correlation_id,
          org_id=_order_org_id(db, dispute.event_order_id), outcome="won" if won else "lost")
    db.commit()
    db.refresh(dispute)
    return dispute


# ── Incidents (doc Section 15/K) ─────────────────────────────────────────────────────────

def open_incident(db: Session, event: Event, actor: User, *, severity: str, cause_domain: str,
                   affected_service: str | None = None, impact_description: str | None = None,
                   evidence: dict | None = None, event_order: EventOrder | None = None,
                   platform_incident_id=None) -> EventIncident:
    incident = EventIncident(
        event_id=event.id, event_order_id=event_order.id if event_order else None,
        platform_incident_id=platform_incident_id, severity=severity, cause_domain=cause_domain,
        affected_service=affected_service, impact_description=impact_description, evidence=evidence,
        review_state="pending_review", created_by=actor.id,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    audit(db, actor=actor, action="commercial.incident.open", target_type="event_incident",
          target_id=incident.id, org_id=event.org_id, severity=severity, cause_domain=cause_domain)
    db.commit()
    return incident


def propose_remedy(db: Session, incident: EventIncident, order: EventOrder, actor: User, *,
                    remedy_type: str, amount: Decimal, reason_code: str, policy_version: str | None = None) -> RefundCredit:
    """Operations establishes the facts; only an authorized Commercial/Finance actor may
    then approve money movement (doc K2) — this creates the PENDING request, it never
    auto-approves."""
    incident.review_state = "commercial_review"
    remedy = RefundCredit(
        event_order_id=order.id, incident_id=incident.id, type=remedy_type, amount=amount,
        reason_code=reason_code, policy_version=policy_version, requested_by=actor.id, status="pending",
    )
    db.add(remedy)
    db.commit()
    db.refresh(remedy)
    return remedy


# ── Readiness (doc Section 13/I — READY is computed, never a single boolean) ────────────

_CHECK_CODES = {
    "requires_backup_contribution": "backup_contribution",
    "requires_dual_recording": "dual_recording",
    "requires_preview_return": "preview_return",
    "requires_command_owner": "command_owner",
    "requires_full_rehearsal": "full_rehearsal",
}

# Check codes not driven by a single ServiceProfile boolean.
COMMAND_OWNER_CHECK = "command_owner"
# doc Section 13: an R3 event needs a recorded human sign-off that operations accept the
# delivery plan. Recorded as an ordinary ReadinessCheck rather than a new table — the check
# rows are already append-only, actor-stamped and evidence-bearing, which is exactly what
# operational acceptance needs.
OPERATIONAL_ACCEPTANCE_CHECK = "operational_acceptance"

# Risk tiers at which a commercial order must carry a published ServiceProfile. R0 is the
# self-service tier and legitimately has none; from R1 up, the profile is what DEFINES the
# mandatory controls, so an unprofiled managed event has no requirements at all — which reads
# as "nothing outstanding" when the truth is "nobody said what is required".
_PROFILE_REQUIRED_TIERS = ("r1", "r2", "r3")


def required_readiness_checks(profile: ServiceProfile | None) -> list[str]:
    """Check codes this profile makes mandatory (doc I3)."""
    if profile is None:
        return []
    codes = [code for flag, code in _CHECK_CODES.items() if getattr(profile, flag)]
    # Managed-only means the event is not self-servable: a Zoiko command owner must be on it,
    # whether or not requires_command_owner was separately ticked. That is what makes the gate
    # non-waivable for this profile rather than another optional box (doc Section 10).
    if getattr(profile, "managed_only", False) and COMMAND_OWNER_CHECK not in codes:
        codes.append(COMMAND_OWNER_CHECK)
    if profile.risk_tier == "r3" and OPERATIONAL_ACCEPTANCE_CHECK not in codes:
        codes.append(OPERATIONAL_ACCEPTANCE_CHECK)
    return codes


def assured_event_reasons(order: EventOrder | None, profile: ServiceProfile | None) -> list[str]:
    """Why this order's elected Assured Event commitment is not currently valid (doc Sec. 10).

    `assured_event_eligible` was a stored flag that no code path read — an order could be sold
    as Assured against a profile with no independent recording and no backup contribution path,
    which is precisely the commitment an Assured Event is. Election is validated at acceptance
    (accept_order) and the delivery preconditions are re-validated here, because a profile can
    be superseded between acceptance and delivery.
    """
    if order is None or not getattr(order, "assured_event", False):
        return []
    if profile is None:
        return ["Assured Event has no service profile bound to validate it against"]
    reasons = []
    if not profile.assured_event_eligible:
        reasons.append(
            f"service profile '{profile.version_label}' is not Assured-Event-eligible"
        )
    if not profile.requires_dual_recording:
        reasons.append("Assured Event requires a service profile mandating dual recording")
    if not profile.requires_backup_contribution:
        reasons.append("Assured Event requires a service profile mandating backup contribution")
    return reasons


def record_readiness_check(db: Session, event: Event, *, check_code: str, status: str, actor: User,
                            evidence_reference: str | None = None, exception_id=None) -> ReadinessCheck:
    check = ReadinessCheck(
        event_id=event.id, check_code=check_code, status=status, actor_id=actor.id,
        evidence_reference=evidence_reference, exception_id=exception_id,
        completed_at=datetime.now(timezone.utc) if status in ("pass", "conditional_pass", "fail") else None,
        required_by_risk_tier=event.risk_tier,
    )
    db.add(check)
    db.flush()
    # Operational evidence (doc Section 13/30). Check rows are already append-only — a new
    # attestation never overwrites an earlier one, and evaluate_readiness reads the latest per
    # code — so the audit entry records WHO attested WHAT against WHICH evidence, immutably.
    audit(db, actor=actor, action="commercial.readiness.record", target_type="readiness_check",
          target_id=check.id, org_id=event.org_id, check_code=check_code, status=status,
          risk_tier=event.risk_tier, evidence_reference=evidence_reference,
          exception_id=None if exception_id is None else str(exception_id))
    # Passing (or failing) a required check can move the event in or out of READY.
    sync_lifecycle(db, event, get_current_order(db, event.id), actor=actor,
                    trigger="readiness.record")
    db.commit()
    db.refresh(check)
    return check


def dual_recording_required(db: Session, event: Event) -> bool:
    """Whether this event's service profile mandates independent dual recording (doc
    Section 14.1/17 — required for R2/R3). Read directly off the profile flag rather than
    through required_readiness_checks' broader list: that list feeds the manual-attestation
    ReadinessCheck gate, while this drives real egress orchestration
    (services/broadcast.py._recording_start) and must not wait on anyone attesting anything."""
    if event.service_profile_id is None:
        return False
    profile = db.get(ServiceProfile, event.service_profile_id)
    return bool(profile and profile.requires_dual_recording)


def contributor_readiness_reasons(rows: list[tuple[str, ContributorSession | None]]) -> list[str]:
    """BRD Section 12: "remote contributors: invitation, consent, preflight, return feed,
    role, and rehearsal complete" is a non-waivable arming blocker for ANY event with an
    assigned remote contributor, not just R2/R3 tiers — unlike the ServiceProfile-gated
    checks in the loop above, this runs unconditionally. `rows` is
    [(display_name, session_or_none), ...]."""
    reasons: list[str] = []
    for name, session in rows:
        if session is None or session.state == "removed":
            reasons.append(f"contributor '{name}' has not been invited")
            continue
        if not session.consent_given:
            reasons.append(f"contributor '{name}' has not given consent")
        if not (session.preflight_result or {}).get("passed"):
            reasons.append(f"contributor '{name}' has not completed preflight")
        if not session.rehearsal_complete:
            reasons.append(f"contributor '{name}' has not completed rehearsal")
    return reasons


def technical_readiness_reasons(db: Session, event: Event) -> list[str]:
    """Required delivery infrastructure (doc I3 "configuration ... contribution ... recording").

    Exactly ONE check: the media plane must be configured. Without LiveKit credentials there is
    no room to publish into, so the event physically cannot deliver, and calling it READY is a
    false promise. It is also the only infrastructure fact this layer can verify without
    inventing a threshold.

    Two conditions gate it, both load-bearing:
      * commercial classification only — a demo/internal event legitimately runs against no
        media plane, and blocking those breaks local and QA use for no commercial reason;
      * platform_settings.require_media_plane — an explicit Operations switch, because
        otherwise the readiness verdict would silently depend on ambient env vars and every
        go-live test would fail in any environment without LiveKit credentials (dev and CI
        included). See that function for why it defaults off.

    Notably NOT checked here: bitrate ceilings, region capacity, egress health. Those are
    operational telemetry (services/ops.py) or unconfigured platform settings, and asserting
    them from this function would mean inventing the thresholds the standard forbids.
    """
    if event.billing_classification != "commercial":
        return []
    if not platform_settings.require_media_plane(db):
        return []
    from ..services import livekit
    if not livekit.configured():
        return [
            "delivery infrastructure is not configured (no media plane credentials) — a "
            "commercial event cannot be marked ready to deliver without somewhere to deliver it"
        ]
    return []


def evaluate_readiness(db: Session, event: Event, order: EventOrder | None) -> dict:
    """doc I3: READY is computed from payment/credit, capacity, configuration, rehearsal/
    checks, contribution, recording, access and command evidence — never one flag."""
    reasons: list[str] = []
    profile = db.get(ServiceProfile, event.service_profile_id) if event.service_profile_id else None
    required_codes = required_readiness_checks(profile)

    latest_by_code: dict[str, ReadinessCheck] = {}
    for check in db.scalars(
        select(ReadinessCheck).where(ReadinessCheck.event_id == event.id).order_by(ReadinessCheck.created_at)
    ).all():
        latest_by_code[check.check_code] = check

    for code in required_codes:
        check = latest_by_code.get(code)
        if check is None or check.status not in ("pass", "conditional_pass"):
            reasons.append(f"readiness check '{code}' has not passed")

    speaker_rows = db.execute(
        select(EventAssignment.user_id, User.full_name, User.email)
        .join(User, User.id == EventAssignment.user_id)
        .where(EventAssignment.event_id == event.id, EventAssignment.role == "speaker")
    ).all()
    if speaker_rows:
        sessions = {
            s.user_id: s for s in db.scalars(
                select(ContributorSession).where(ContributorSession.event_id == event.id)
            ).all()
        }
        reasons.extend(contributor_readiness_reasons([
            (full_name or email, sessions.get(user_id)) for user_id, full_name, email in speaker_rows
        ]))

    # Deliberately does NOT sweep lapsed soft holds. Both capacity gates below read only
    # HARD_RESERVED rows, so a lapsed soft hold cannot change this verdict — sweeping here
    # would be a hidden write (expire_stale_soft_holds commits) on what every caller treats as
    # a read, including the readiness GET endpoint. The sweep belongs where it affects an
    # outcome: the claim path (soft_hold_capacity, where a lapsed hold would wrongly refuse a
    # new one) and the operator/scheduler endpoint.
    if not capacity_confirmed(db, event):
        reasons.append("required capacity is not hard-reserved")

    envelope_reason = envelope_capacity_block_reason(db, event)
    if envelope_reason:
        reasons.append(envelope_reason)

    # ── Risk tier must have a published profile behind it (doc Section 10/F) ──
    # R0 self-service legitimately has none. From R1 up the profile IS the control set, so its
    # absence is a missing definition rather than an absence of requirements.
    if event.billing_classification == "commercial" and event.risk_tier in _PROFILE_REQUIRED_TIERS:
        if profile is None:
            reasons.append(
                f"{event.risk_tier.upper()} commercial event has no service profile bound — the "
                "profile defines its mandatory controls and cannot be omitted"
            )
        elif profile.status != "published":
            reasons.append(
                f"service profile '{profile.version_label}' is '{profile.status}', not published"
            )
        elif profile.risk_tier != event.risk_tier:
            reasons.append(
                f"service profile '{profile.version_label}' is for {profile.risk_tier.upper()} but "
                f"this event is {event.risk_tier.upper()}"
            )

    reasons.extend(assured_event_reasons(order, profile))
    reasons.extend(technical_readiness_reasons(db, event))

    # Exceptions that are actively carrying a gate, so the caller can tell NORMAL PASS from
    # EXCEPTION APPROVED from BLOCKED rather than seeing one undifferentiated "ready" flag.
    exceptions_applied: list[dict] = []

    # Commercial acceptance (doc B1/S1): an order that exists but has not been ACCEPTED is not
    # a commitment. This previously keyed only on the order's existence, so a commercial event
    # with a draft order passed the acceptance gate while nobody had agreed to anything.
    if order is not None and order.status in ("accepted", "active", "completed"):
        fin_state = financial_readiness_state(db, order)
        if fin_state == "approved_exception":
            exc = active_exception(db, exception_type="financial_hold_override",
                                   order_id=order.id, gate="financial_readiness")
            exceptions_applied.append({
                "gate": "financial_readiness",
                "exception_id": str(exc.id) if exc else None,
                "expiry_at": exc.expiry_at.isoformat() if exc and exc.expiry_at else None,
                "approved_by": str(exc.approver_id) if exc and exc.approver_id else None,
            })
        elif fin_state not in ("satisfied", "not_due"):
            reasons.append(f"financial readiness is '{fin_state}'")
    elif event.billing_classification == "commercial":
        reasons.append("commercial event has no accepted order")

    return {
        "ready": not reasons,
        "blocking_reasons": reasons,
        "required_checks": required_codes,
        "exceptions_applied": exceptions_applied,
        # NORMAL_PASS | EXCEPTION_APPROVED | BLOCKED — the doc's three distinct outcomes.
        "verdict": ("BLOCKED" if reasons else
                    "EXCEPTION_APPROVED" if exceptions_applied else "NORMAL_PASS"),
    }


# ── The single authoritative go-live gate (doc I3, CF-3) ─────────────────────────────────

# Event lifecycle states that mean "this event is (about to be) delivering in production".
# Any transition INTO one of these must clear commercial readiness. Kept here, next to the
# gate, so a new production state cannot be added without confronting the gate.
PRODUCTION_EVENT_STATES = ("armed", "live", "degraded")


def golive_block_reason(db: Session, event: Event, *, target_state: str = "live") -> str | None:
    """Why this event may NOT enter a production state, or None if it may.

    THE gate. Every path that escalates an event into PRODUCTION_EVENT_STATES calls this —
    the PATCH /events lifecycle route and the host console's socket `golive` handler — so
    there is one readiness decision, not a parallel implementation per entry point.

    Before this existed, `_golive` wrote `ev.status = "live"` from published/scheduled with
    no readiness call at all, and the PATCH route only evaluated readiness for `armed`. An R2
    memorial with an unpaid required milestone and no reserved capacity could go live by
    clicking Go Live (CF-3).

    An approved, unexpired, correctly-scoped CommercialException clears its gate — that is
    the legitimate override, and it is recorded (see golive_readiness for the audit payload).
    """
    if target_state not in PRODUCTION_EVENT_STATES:
        return None
    evaluation = evaluate_readiness(db, event, get_current_order(db, event.id))
    if evaluation["ready"]:
        return None
    return "; ".join(evaluation["blocking_reasons"])


def golive_readiness(db: Session, event: Event) -> dict:
    """Full evaluation for callers that also need to audit HOW go-live was permitted
    (normally vs through an approved exception)."""
    return evaluate_readiness(db, event, get_current_order(db, event.id))


def audit_golive_decision(db: Session, event: Event, evaluation: dict, *, actor: User | None,
                           target_state: str, correlation_id: str | None = None) -> None:
    """Record every production escalation attempt — permitted or refused. A blocked go-live
    is operationally important evidence, not a silent 400."""
    verdict = evaluation.get("verdict")
    action = {
        "BLOCKED": "commercial.golive.blocked",
        "EXCEPTION_APPROVED": "commercial.golive.approved_via_exception",
        "NORMAL_PASS": "commercial.golive.approved",
    }.get(verdict, "commercial.golive.approved")
    audit(db, actor=actor, action=action, target_type="event", target_id=event.id,
          org_id=event.org_id, correlation_id=correlation_id or new_correlation_id(),
          target_state=target_state, verdict=verdict,
          blocking_reasons=evaluation.get("blocking_reasons") or None,
          exceptions_applied=evaluation.get("exceptions_applied") or None)


# ── Replay entitlement (doc Section 14/J) ────────────────────────────────────────────────

def create_replay_entitlement(db: Session, event: Event, *, scope: str = "audience",
                               expires_at: datetime | None = None) -> ReplayEntitlement:
    ent = ReplayEntitlement(event_id=event.id, scope=scope, publish_state="not_available", expires_at=expires_at)
    db.add(ent)
    db.commit()
    db.refresh(ent)
    return ent


def get_replay_entitlement(db: Session, event_id, *, scope: str = "audience") -> ReplayEntitlement | None:
    """Read-only lookup — routers/events.py::watch_event's actual publish gate. None means
    not_available in every way that matters to a viewer (no row yet is exactly as
    unpublished as a row stuck at not_available)."""
    return db.scalar(
        select(ReplayEntitlement).where(ReplayEntitlement.event_id == event_id, ReplayEntitlement.scope == scope)
    )


def replay_access_expired(entitlement: ReplayEntitlement | None, *,
                          now: datetime | None = None) -> bool:
    """Whether this entitlement's retention window has passed (doc Section 14/J).

    `expires_at` was settable and stored but READ BY NOTHING — routers/events.py gated replay
    playback on publish_state and watermark_status alone, so a replay whose retention had
    lapsed was still served indefinitely. This is the check that closes it.

    Also treats an explicit `expired` publish_state as expired, so the maintenance sweep and
    this live check can never disagree about a given row: whichever notices first, access
    stops.
    """
    if entitlement is None:
        return False
    if entitlement.publish_state == "expired":
        return True
    if entitlement.expires_at is None:
        return False                     # no retention window set = no expiry
    return entitlement.expires_at < (now or datetime.now(timezone.utc))


def expire_lapsed_replay_entitlements(db: Session, *, actor: User | None = None) -> int:
    """Mark published replays whose retention window has passed as EXPIRED.

    Only flips the state — the stored object is NOT deleted. Retention lapsing means access
    ends, not that the evidence of what was delivered disappears (doc T4: financial and
    delivery records are additive). Deleting the media is a separate storage-lifecycle decision
    with its own approval, and doing it silently from a sweep would destroy a customer's
    recording on a date nobody confirmed.
    """
    now = datetime.now(timezone.utc)
    lapsed = db.scalars(
        select(ReplayEntitlement).where(
            ReplayEntitlement.publish_state == "published",
            ReplayEntitlement.expires_at.is_not(None),
            ReplayEntitlement.expires_at < now,
        )
    ).all()
    for entitlement in lapsed:
        entitlement.publish_state = "expired"
        event = db.get(Event, entitlement.event_id)
        audit(db, actor=actor, action="commercial.replay.expired",
              target_type="replay_entitlement", target_id=entitlement.id,
              org_id=event.org_id if event is not None else None,
              reason="retention window lapsed", scope=entitlement.scope,
              expires_at=entitlement.expires_at.isoformat())
    if lapsed:
        db.commit()
    return len(lapsed)


def get_or_create_replay_entitlement(db: Session, event: Event, *, scope: str = "audience") -> ReplayEntitlement:
    """Idempotent counterpart to create_replay_entitlement — services/validation.py calls
    this from the recording lifecycle (not a human), so it must never create a duplicate
    row for the same event+scope on a repeated trigger (e.g. record_egress_result firing
    again for the secondary path of a dual recording)."""
    existing = db.scalar(
        select(ReplayEntitlement).where(ReplayEntitlement.event_id == event.id, ReplayEntitlement.scope == scope)
    )
    if existing is not None:
        return existing
    return create_replay_entitlement(db, event, scope=scope)


def advance_to_ready_for_review(db: Session, entitlement: ReplayEntitlement) -> ReplayEntitlement:
    """The automated half of the state machine — moves NOT_AVAILABLE to READY_FOR_REVIEW
    once services/validation.py confirms a real, usable replay source exists. Never touches
    PUBLISHED or any other state (idempotent against being called more than once, and never
    un-publishes something an operator already released)."""
    if entitlement.publish_state == "not_available":
        entitlement.publish_state = "ready_for_review"
        db.commit()
        db.refresh(entitlement)
    return entitlement


def publish_replay(db: Session, entitlement: ReplayEntitlement) -> ReplayEntitlement:
    """Publication is never automatic on live-end (doc J2) — this is the one explicit call
    that moves READY_FOR_REVIEW/NOT_AVAILABLE to PUBLISHED. Also queues the watermark burn
    (services/delivery.py's shared export/replay ticker, BRD "policy watermark" LE-AC-12) —
    the audience-facing URL stays withheld (routers/events.py::watch_event) until that
    finishes, the same "publish now, deliver once ready" split the customer export already
    uses. Doesn't reset an already-`ready` watermark (a re-publish after some other field
    changed shouldn't discard a successful burn and force a re-encode)."""
    # "withheld" is allowed as a source so a withdrawal is reversible: replay_comms.withdraw
    # can move a published replay out of service, and this is how it comes back. Without it
    # a withdrawal would be permanent and the MED-009 access-change transition would have no
    # trigger at all. "expired" is deliberately NOT here — a lapsed availability window is
    # re-opened by setting a new expires_at, not by re-publishing over the old one.
    if entitlement.publish_state not in ("not_available", "ready_for_review", "withheld"):
        raise ValueError(f"Cannot publish replay from state '{entitlement.publish_state}'")
    entitlement.publish_state = "published"
    if entitlement.watermark_status != "ready":
        entitlement.watermark_status = "pending"
        entitlement.watermark_error = None
    db.commit()
    db.refresh(entitlement)
    return entitlement


def retry_replay_watermark(db: Session, entitlement: ReplayEntitlement) -> ReplayEntitlement:
    """Re-queues a failed watermark burn (e.g. a transient GCS/ffmpeg hiccup) without
    touching publish_state — publish_replay's own state guard would otherwise refuse a
    second call once the entitlement is already PUBLISHED."""
    if entitlement.watermark_status != "failed":
        raise ValueError(f"Cannot retry from watermark_status '{entitlement.watermark_status}'")
    entitlement.watermark_status = "pending"
    entitlement.watermark_error = None
    db.commit()
    db.refresh(entitlement)
    return entitlement


# ── Reconciliation (doc Section 29 — read-only views over the ledgers above) ────────────

def list_capacity_orphans(db: Session):
    """Confirmed events without a valid hard reservation, and hard reservations without an
    active order — doc's 'capacity-to-order reconciliation' control."""
    orphan_reservations = db.scalars(
        select(CapacityReservation).where(
            CapacityReservation.state == "hard_reserved", CapacityReservation.event_order_id.is_(None),
        )
    ).all()
    reserved_without_order = [
        r for r in db.scalars(select(CapacityReservation).where(CapacityReservation.state == "hard_reserved")).all()
        if r.event_order_id and db.get(EventOrder, r.event_order_id) is None
    ]
    return {"reservations_without_order": orphan_reservations, "reservations_with_missing_order": reserved_without_order}


def list_unmatched_settlements(db: Session):
    """doc P5/Section 29 "Daily payment reconciliation": provider settlement money that could
    not be attributed to a payment.

    This queried `Payment.state == "unmatched"` — a state NO code path has ever written. The
    unmatched-money record is an `UnmatchedSettlement` row (written by ingest_provider_event),
    not a Payment, so the control was structurally blind: `GET /commercial/reconciliation`
    always reported zero and `close_period` never filed the exception, while the real
    settlements sat in a table only the separate /unmatched-settlements endpoint read.

    Now reads the same OPEN rows that endpoint does, so the report, the period-close exception
    queue and the operator's work list are three views of one fact.
    """
    return list_open_unmatched_settlements(db)


def list_orders_missing_invoice(db: Session):
    """doc 'order-to-invoice reconciliation': every billable accepted order must map to an
    invoice or an explicit noncommercial classification."""
    accepted = db.scalars(
        select(EventOrder).where(
            EventOrder.status.in_(("accepted", "active", "completed")),
            EventOrder.billing_classification == "commercial",
        )
    ).all()
    invoiced_order_ids = {i.event_order_id for i in db.scalars(select(Invoice)).all()}
    return [o for o in accepted if o.id not in invoiced_order_ids]


def list_orders_missing_seller_entity(db: Session):
    """Accepted commercial orders whose commercial account has no ACTIVE registered seller
    legal entity (doc L1, Section 29).

    Such an order can be quoted, accepted, taxed and PAID, and then cannot be invoiced —
    issue_invoice fails closed on it. That is the right refusal at the wrong time: the money
    is already collected. Surfacing it as a reconciliation exception moves the discovery to
    period close, where Finance can assign the entity before anyone is charged.
    """
    accepted = db.scalars(
        select(EventOrder).where(
            EventOrder.status.in_(("accepted", "active", "completed")),
            EventOrder.billing_classification == "commercial",
        )
    ).all()
    out = []
    for order in accepted:
        try:
            resolve_seller_entity(db, order)
        except ValueError:
            out.append(order)
    return out


def list_events_missing_classification(db: Session):
    """doc 'event-to-order reconciliation': every event marked billing_classification=
    'commercial' must have an accepted order or an approved noncommercial reclassification
    — this is the case where it has neither."""
    commercial_events = db.scalars(select(Event).where(Event.billing_classification == "commercial")).all()
    accepted_order_event_ids = {
        o.event_id for o in db.scalars(select(EventOrder).where(EventOrder.status.in_(("accepted", "active", "completed")))).all()
    }
    return [e for e in commercial_events if e.id not in accepted_order_event_ids]


# ── Financial period-close (doc Section 29) ──────────────────────────────────────────────

def get_or_create_period(db: Session, *, label: str, period_start: datetime, period_end: datetime) -> FinancialPeriod:
    period = db.scalar(select(FinancialPeriod).where(FinancialPeriod.label == label))
    if period is None:
        period = FinancialPeriod(id=uuid.uuid4(), label=label, period_start=period_start, period_end=period_end)
        db.add(period)
        db.commit()
        db.refresh(period)
    return period


def _period_snapshot(db: Session, period: FinancialPeriod) -> dict:
    """The doc 29 'freeze/materialize' snapshot — event-order, invoice, payment, refund,
    dispute and deferred/outstanding totals for the period window, computed once at close
    and stored verbatim from then on (never recomputed live against a closed period)."""
    window = (EventOrder.created_at >= period.period_start, EventOrder.created_at < period.period_end)
    orders = db.scalars(select(EventOrder).where(*window)).all()
    invoices = db.scalars(
        select(Invoice).where(Invoice.issue_date >= period.period_start, Invoice.issue_date < period.period_end)
    ).all()
    payments = db.scalars(
        select(Payment).where(Payment.created_at >= period.period_start, Payment.created_at < period.period_end)
    ).all()
    refunds = db.scalars(
        select(RefundCredit).where(RefundCredit.created_at >= period.period_start, RefundCredit.created_at < period.period_end)
    ).all()
    disputes = db.scalars(
        select(PaymentDispute).where(PaymentDispute.opened_at >= period.period_start, PaymentDispute.opened_at < period.period_end)
    ).all()
    paid = [p for p in payments if p.state in ("paid", "part_refunded")]
    outstanding_schedules = db.scalars(
        select(PaymentSchedule).where(PaymentSchedule.status.in_(("due", "not_due")))
    ).all()
    return {
        "orders": {"count": len(orders), "total_amount": str(sum((o.total_amount or Decimal(0)) for o in orders))},
        "invoices": {"count": len(invoices), "total_amount": str(sum((i.total_amount or Decimal(0)) for i in invoices))},
        "payments": {"count": len(payments), "collected_amount": str(sum((p.amount or Decimal(0)) for p in paid))},
        "refunds_credits": {"count": len(refunds), "total_amount": str(sum((r.amount or Decimal(0)) for r in refunds))},
        "disputes": {"count": len(disputes), "total_amount": str(sum((d.amount or Decimal(0)) for d in disputes))},
        # Not period-windowed — an outstanding milestone is a point-in-time fact as of close,
        # not something that happened "during" the period.
        "deferred_outstanding": {
            "count": len(outstanding_schedules),
            "total_amount": str(sum((s.amount or Decimal(0)) for s in outstanding_schedules)),
        },
    }


def _file_exceptions(db: Session, period: FinancialPeriod) -> list[ReconciliationException]:
    """Runs every doc-29 detection control and files an open exception for each finding —
    the persistence + ownership the read-only /commercial/reconciliation report doesn't
    have on its own (doc: 'Differences enter an exception queue with an owner and
    resolution record')."""
    filed = []

    def file(category: str, reference_type: str, reference_id, description: str):
        exc = ReconciliationException(
            id=uuid.uuid4(), period_id=period.id, category=category, reference_type=reference_type,
            reference_id=reference_id, description=description, status="open",
        )
        db.add(exc)
        filed.append(exc)

    orphans = list_capacity_orphans(db)
    for r in orphans["reservations_without_order"]:
        file("capacity_without_order", "capacity_reservation", r.id,
             f"Hard-reserved capacity for event {r.event_id} has no linked order")
    for r in orphans["reservations_with_missing_order"]:
        file("order_without_capacity", "capacity_reservation", r.id,
             f"Capacity reservation {r.id} references an order that no longer exists")
    for order in list_orders_missing_invoice(db):
        file("order_without_invoice", "event_order", order.id,
             f"Accepted commercial order {order.id} has no invoice")
    for event in list_events_missing_classification(db):
        file("event_without_classification", "event", event.id,
             f"Event {event.id} is billing_classification=commercial with no accepted order")
    for order in list_orders_missing_seller_entity(db):
        file("order_without_seller_entity", "event_order", order.id,
             f"Accepted commercial order {order.id} has no active registered seller legal "
             "entity — it cannot be invoiced")
    for settlement in list_unmatched_settlements(db):
        file("unmatched_settlement", "unmatched_settlement", settlement.id,
             f"Provider settlement {settlement.provider_payment_ref or '(no reference)'} "
             f"({settlement.provider}) could not be attributed to any payment: {settlement.reason}")
    return filed


def close_period(db: Session, period: FinancialPeriod, actor: User) -> FinancialPeriod:
    """doc 29 'Period close': freeze the snapshot, file every open reconciliation exception,
    lock the period. 'Subsequent corrections are separately dated' — there is deliberately
    no reopen/edit path here; a correction after close belongs to a later period."""
    if period.status != "open":
        raise ValueError(f"Period '{period.label}' is already {period.status}")
    exceptions = _file_exceptions(db, period)
    period.snapshot = _period_snapshot(db, period)
    period.status = "closed"
    period.closed_by = actor.id
    period.closed_at = datetime.now(timezone.utc)
    audit(db, actor=actor, action="commercial.period.close", target_type="financial_period",
          target_id=period.id, exceptions_filed=len(exceptions))
    db.commit()
    db.refresh(period)
    return period


def resolve_exception(db: Session, exception: ReconciliationException, actor: User, *,
                       status: str, resolution_notes: str | None = None) -> ReconciliationException:
    if status not in ("resolved", "accepted_risk", "investigating"):
        raise ValueError(f"Invalid exception resolution status: '{status}'")
    exception.status = status
    exception.owner_id = actor.id
    if resolution_notes:
        exception.resolution_notes = resolution_notes
    if status in ("resolved", "accepted_risk"):
        exception.resolved_at = datetime.now(timezone.utc)
    audit(db, actor=actor, action="commercial.reconciliation_exception.resolve",
          target_type="reconciliation_exception", target_id=exception.id, status=status)
    db.commit()
    db.refresh(exception)
    return exception
