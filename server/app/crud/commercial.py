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
    ContributorSession,
    Event,
    EventAssignment,
    EventIncident,
    EventOrder,
    EventOrderLine,
    EventOrderVersion,
    FinancialPeriod,
    Invoice,
    Payment,
    PaymentDispute,
    PaymentSchedule,
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


def accept_quote(db: Session, quote: Quote) -> Quote:
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
          order_version=order.order_version)
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


def activate_order(db: Session, order: EventOrder) -> EventOrder:
    """ACCEPTED -> ACTIVE once capacity is hard-reserved and a payment schedule exists —
    called by the router after those steps, not automatically, since the doc treats payment,
    capacity and readiness as independent gates (doc C3/I3), not one boolean."""
    if order.status != "accepted":
        raise ValueError(f"Cannot activate an order in status '{order.status}'")
    order.status = "active"
    db.commit()
    db.refresh(order)
    return order


# ── Capacity reservation (doc Section 7/C, state: UNREQUESTED->SOFT_HELD->HARD_RESERVED->...)

# ── Capacity inventory (doc C2/C4, Section 4 P0 blocker #8) ──────────────────────────────
# States that HOLD inventory against a pool. `released`/`expired` return it; `unrequested`
# never took any. Kept as one tuple so utilisation and the oversell guard can never disagree
# about what counts.
CAPACITY_HOLDING_STATES = ("soft_held", "hard_reserved", "consumed")


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
    db.commit()  # releases the pool row lock
    db.refresh(reservation)
    return reservation


def hard_reserve_capacity(db: Session, reservation: CapacityReservation, order: EventOrder) -> CapacityReservation:
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
    db.commit()
    db.refresh(reservation)
    return reservation


def consume_capacity(db: Session, reservation: CapacityReservation) -> CapacityReservation:
    """HARD_RESERVED -> CONSUMED once the event has actually used the resource (doc Section
    28). Still holds inventory — consumption is not a release."""
    if reservation.state != "hard_reserved":
        raise ValueError(f"Cannot consume capacity in state '{reservation.state}'")
    reservation.state = "consumed"
    reservation.consumed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(reservation)
    return reservation


def release_capacity(db: Session, reservation: CapacityReservation, reason: str) -> CapacityReservation:
    """Returns the held quantity to its pool by leaving CAPACITY_HOLDING_STATES. Idempotent:
    releasing an already-released row is a no-op rather than a double credit."""
    if reservation.state in ("released", "expired"):
        return reservation
    reservation.state = "released"
    reservation.released_at = datetime.now(timezone.utc)
    reservation.release_reason = reason
    db.commit()
    db.refresh(reservation)
    return reservation


def expire_stale_soft_holds(db: Session) -> int:
    """Return inventory from soft holds whose governed period has lapsed (doc C2: "Soft holds
    expire automatically"). Called opportunistically before capacity is read/claimed rather
    than from a scheduler — there is no job runner in this codebase, and an expired hold that
    is never swept would silently keep occupying a pool."""
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


def _captured_amount(db: Session, order_id) -> Decimal:
    paid = db.scalars(
        select(Payment).where(Payment.event_order_id == order_id, Payment.state.in_(("paid", "part_refunded")))
    ).all()
    return sum((p.amount for p in paid), Decimal(0))


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

def create_change_order(db: Session, order: EventOrder, *, changes: dict, price_delta: Decimal,
                         service_impact: str | None = None, risk_impact: str | None = None,
                         capacity_impact: str | None = None) -> ChangeOrder:
    return _persist_change_order(db, ChangeOrder(
        event_order_id=order.id, prior_order_version=order.order_version, changes=changes,
        price_delta=price_delta, service_impact=service_impact, risk_impact=risk_impact,
        capacity_impact=capacity_impact, status="draft",
    ))


def _persist_change_order(db: Session, co: ChangeOrder) -> ChangeOrder:
    db.add(co)
    db.commit()
    db.refresh(co)
    return co


def accept_change_order(db: Session, change_order: ChangeOrder, actor: User) -> ChangeOrder:
    """Customer acceptance, then apply: bumps the order's version and total rather than
    editing prior lines in place (doc T4 — corrections are additive)."""
    if change_order.status not in ("draft", "pending_acceptance"):
        raise ValueError(f"Cannot accept a change order in status '{change_order.status}'")
    order = db.get(EventOrder, change_order.event_order_id)
    if order.order_version != change_order.prior_order_version:
        raise ValueError("This change order is stale — a newer version has already been applied")
    change_order.customer_acceptance = True
    change_order.accepted_at = datetime.now(timezone.utc)
    change_order.approved_by = actor.id
    change_order.effective_at = datetime.now(timezone.utc)
    change_order.status = "accepted"
    order.order_version += 1
    order.subtotal = (order.subtotal or Decimal(0)) + change_order.price_delta
    # A price delta moves the tax basis, so any existing determination is stale — clear it
    # and force a re-determination before the changed order can be invoiced again (doc L4).
    _clear_tax_determination(order)
    order.total_amount = order.subtotal
    # The prior version's snapshot already exists and is never touched; this adds the NEW
    # version alongside it, so both remain readable (doc Section 28: corrections are additive).
    snapshot_order_version(db, order, actor=actor, change_order=change_order)
    audit(db, actor=actor, action="commercial.change_order.accept", target_type="change_order",
          target_id=change_order.id, price_delta=str(change_order.price_delta),
          order_version=order.order_version)
    db.commit()
    db.refresh(change_order)
    return change_order


# ── Cancellation workflow (doc Section 9/E) ──────────────────────────────────────────────

def calculate_cancellation(db: Session, event: Event, order: EventOrder, *, requested_at: datetime | None = None):
    """Returns (policy, refund_amount) or (None, None) if no policy is configured for this
    vertical/risk-tier/lead-time — the caller must treat that as a fail-closed block on
    automatic cancellation, not invent a percentage (doc E1)."""
    requested_at = requested_at or datetime.now(timezone.utc)
    if event.start_time is None:
        lead_time_hours = float("inf")
    else:
        lead_time_hours = max((event.start_time - requested_at).total_seconds() / 3600, 0)
    policy = find_cancellation_policy(
        db, vertical=event.category or "unspecified", risk_tier=order.risk_tier, lead_time_hours=lead_time_hours,
    )
    if policy is None:
        return None, None
    refund_amount = (order.total_amount or Decimal(0)) * (policy.refund_percentage / Decimal(100))
    return policy, refund_amount.quantize(Decimal("0.01"))


def cancel_order(db: Session, event: Event, order: EventOrder, actor: User, *, reason: str) -> dict:
    policy, refund_amount = calculate_cancellation(db, event, order)
    if policy is None:
        raise ValueError(
            "No published cancellation policy matches this event's vertical/risk tier/lead time — "
            "cancellation is blocked until Commercial/Finance configures one (doc E1, fail closed)"
        )
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
        release_capacity(db, r, reason="order_canceled")
    refund_credit = None
    if refund_amount and refund_amount > 0:
        refund_credit = RefundCredit(
            event_order_id=order.id, type="refund", amount=refund_amount, reason_code="customer_cancellation",
            policy_version=policy.version_label, requested_by=actor.id, status="pending",
        )
        db.add(refund_credit)
    audit(db, actor=actor, action="commercial.order.cancel", target_type="event_order", target_id=order.id,
        org_id=event.org_id, reason=reason, refund_amount=str(refund_amount))
    db.commit()
    if refund_credit:
        db.refresh(refund_credit)
    return {"order": order, "policy": policy, "refund_amount": refund_amount, "refund_credit": refund_credit}


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
    if refund_credit.type in ("refund",) and refund_credit.source_payment_id:
        payment = db.get(Payment, refund_credit.source_payment_id)
        if payment is None:
            raise ValueError("Refund references a payment that no longer exists")
        if refund_credit.amount > payment.amount:
            raise ValueError(
                f"Refund of {refund_credit.amount} exceeds the payment's {payment.amount} — "
                "a remedy may not return more than was taken"
            )
        target = "part_refunded" if refund_credit.amount < payment.amount else "refunded"
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
        reallocate_schedules(db, payment.event_order_id)
    refund_credit.status = "executed"
    if refund_credit.incident_id:
        incident = db.get(EventIncident, refund_credit.incident_id)
        if incident:
            incident.review_state = "credit_refund_executed"
    audit(db, actor=actor, action="commercial.refund_credit.execute", target_type="refund_credit",
          target_id=refund_credit.id, org_id=_order_org_id(db, refund_credit.event_order_id),
          correlation_id=correlation_id, amount=str(refund_credit.amount), type=refund_credit.type,
          provider_ref=refund_credit.provider_ref)
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


def submit_dispute_evidence(db: Session, dispute: PaymentDispute, actor: User, *, evidence: dict) -> PaymentDispute:
    """doc P4 'evidence package'. Submitting evidence doesn't resolve the case — the
    provider/card network decides won/lost, reflected later via resolve_dispute (in
    production, from another webhook)."""
    if dispute.status not in ("opened", "evidence_required"):
        raise ValueError(f"Cannot submit evidence for a dispute in status '{dispute.status}'")
    dispute.evidence = {**(dispute.evidence or {}), **evidence}
    dispute.status = "evidence_submitted"
    audit(db, actor=actor, action="commercial.dispute.evidence_submitted",
          target_type="payment_dispute", target_id=dispute.id)
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


def required_readiness_checks(profile: ServiceProfile | None) -> list[str]:
    if profile is None:
        return []
    return [code for flag, code in _CHECK_CODES.items() if getattr(profile, flag)]


def record_readiness_check(db: Session, event: Event, *, check_code: str, status: str, actor: User,
                            evidence_reference: str | None = None, exception_id=None) -> ReadinessCheck:
    check = ReadinessCheck(
        event_id=event.id, check_code=check_code, status=status, actor_id=actor.id,
        evidence_reference=evidence_reference, exception_id=exception_id,
        completed_at=datetime.now(timezone.utc) if status in ("pass", "conditional_pass", "fail") else None,
        required_by_risk_tier=event.risk_tier,
    )
    db.add(check)
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

    if not capacity_confirmed(db, event):
        reasons.append("required capacity is not hard-reserved")

    envelope_reason = envelope_capacity_block_reason(db, event)
    if envelope_reason:
        reasons.append(envelope_reason)

    # Exceptions that are actively carrying a gate, so the caller can tell NORMAL PASS from
    # EXCEPTION APPROVED from BLOCKED rather than seeing one undifferentiated "ready" flag.
    exceptions_applied: list[dict] = []

    if order is not None:
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
    if entitlement.publish_state not in ("not_available", "ready_for_review"):
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
    """doc P5: unmatched settlements route to a reconciliation exception queue rather than
    being auto-allocated by guess."""
    return db.scalars(select(Payment).where(Payment.state == "unmatched")).all()


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
    for payment in list_unmatched_settlements(db):
        file("unmatched_settlement", "payment", payment.id,
             f"Payment {payment.id} settled unmatched to any order/invoice")
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
