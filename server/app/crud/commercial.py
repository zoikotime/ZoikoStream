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

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    CancellationPolicy,
    CapacityReservation,
    CatalogLine,
    CatalogVersion,
    ChangeOrder,
    CommercialAccount,
    CommercialException,
    Event,
    EventIncident,
    EventOrder,
    EventOrderLine,
    FinancialPeriod,
    Invoice,
    Payment,
    PaymentDispute,
    PaymentSchedule,
    Quote,
    ReadinessCheck,
    ReconciliationException,
    RefundCredit,
    ReplayEntitlement,
    ServiceProfile,
    User,
)
from ..services import payments as payment_svc


# ── Audit trail (reuses the existing platform-wide AuditLog — doc's "audit_event") ──────

def audit(db: Session, *, actor: User | None, action: str, target_type: str, target_id,
          org_id=None, reason: str | None = None, **meta) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        org_id=org_id,
        meta={"reason": reason, **meta} if reason else (meta or None),
    )
    db.add(entry)
    return entry


def new_idempotency_key() -> str:
    return secrets.token_urlsafe(24)


# ── Catalog registry (doc T1, Section 26 "no hard-coded fallback price") ────────────────

def list_catalog_versions(db: Session, vertical: str | None = None, status: str | None = None):
    stmt = select(CatalogVersion).order_by(CatalogVersion.created_at.desc())
    if vertical:
        stmt = stmt.where(CatalogVersion.vertical == vertical)
    if status:
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
                  tax_amount: Decimal, currency: str, created_by: User, valid_until: datetime | None,
                  notes: str | None = None) -> Quote:
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
    creating a duplicate (doc Section 4 P0 blocker #10, Section 26 checklist)."""
    existing = db.scalar(select(EventOrder).where(EventOrder.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    order = EventOrder(
        event_id=event.id, commercial_account_id=commercial_account.id,
        catalog_version_id=catalog_version.id, quote_id=quote.id if quote else None,
        purchaser_type=purchaser_type, purchaser_id=purchaser_id,
        service_profile_id=service_profile.id if service_profile else None,
        cancellation_policy_id=cancellation_policy.id if cancellation_policy else None,
        currency=currency, subtotal=0, tax_amount=0, total_amount=0, status="draft",
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
    if catalog_line.unit_price is None or not catalog_line.currency:
        raise ValueError("Catalog line has no price/currency set — cannot add to an order (doc B2)")
    unit_price = Decimal(0) if is_complimentary else catalog_line.unit_price
    line_total = unit_price * quantity
    line = EventOrderLine(
        event_order_id=order.id, catalog_line_id=catalog_line.id, service_code=catalog_line.service_code,
        description=catalog_line.name, quantity=quantity, unit_price=unit_price, line_total=line_total,
        tax_treatment=catalog_line.tax_treatment, is_addon=is_addon, is_complimentary=is_complimentary,
    )
    db.add(line)
    order.subtotal = (order.subtotal or Decimal(0)) + line_total
    order.total_amount = order.subtotal + (order.tax_amount or Decimal(0))
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
        event.risk_tier = order.risk_tier
        event.service_profile_id = order.service_profile_id
        event.commercial_account_id = order.commercial_account_id
    audit(db, actor=actor, action="commercial.order.accept", target_type="event_order", target_id=order.id,
          org_id=event.org_id if event else None, total_amount=str(order.total_amount))
    db.commit()
    db.refresh(order)
    return order


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

def soft_hold_capacity(db: Session, event: Event, *, resource_type: str, window_start=None, window_end=None,
                        quantity: int = 1, region: str | None = None, hold_minutes: int = 30,
                        event_order: EventOrder | None = None) -> CapacityReservation:
    reservation = CapacityReservation(
        event_id=event.id, event_order_id=event_order.id if event_order else None,
        resource_type=resource_type, window_start=window_start, window_end=window_end,
        quantity=quantity, region=region, state="soft_held",
        soft_hold_expires_at=datetime.now(timezone.utc) + timedelta(minutes=hold_minutes),
    )
    db.add(reservation)
    db.commit()
    db.refresh(reservation)
    return reservation


def hard_reserve_capacity(db: Session, reservation: CapacityReservation, order: EventOrder) -> CapacityReservation:
    """Requires an accepted/active order — money without operational capacity is not a
    valid delivery commitment, and capacity without an order is equally invalid (doc B4).

    ponytail / known gap: the doc calls for the capacity service to be "authoritative for
    constrained resources" (fail-closed on oversell, doc C4), which implies a bounded
    inventory to check against. Section 27's minimum data model does not define an
    inventory/capacity-pool entity, so this records reservation state faithfully but does
    not yet enforce a ceiling — that needs a CapacityPool table once Operations defines
    real per-region/per-profile limits, and is flagged as a P0 blocker in the doc itself
    (Section 4: "Implement capacity reservation and release")."""
    if order.status not in ("accepted", "active"):
        raise ValueError("Order must be accepted before capacity can be hard-reserved (doc B4)")
    if reservation.state != "soft_held":
        raise ValueError(f"Cannot hard-reserve capacity in state '{reservation.state}'")
    reservation.event_order_id = order.id
    reservation.state = "hard_reserved"
    reservation.hard_reserved_at = datetime.now(timezone.utc)
    reservation.soft_hold_expires_at = None
    db.commit()
    db.refresh(reservation)
    return reservation


def release_capacity(db: Session, reservation: CapacityReservation, reason: str) -> CapacityReservation:
    reservation.state = "released"
    reservation.released_at = datetime.now(timezone.utc)
    reservation.release_reason = reason
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


def financial_readiness_state(db: Session, order: EventOrder) -> str:
    """NOT_DUE -> DUE -> SATISFIED | FINANCIAL_HOLD | APPROVED_EXCEPTION (doc Section 28).
    An order with no required-before-ready schedule rows is trivially satisfied."""
    required = [s for s in list_payment_schedules(db, order.id) if s.required_before_ready]
    if not required:
        return "satisfied"
    now = datetime.now(timezone.utc)
    captured = _captured_amount(db, order.id)
    total_required = sum((s.amount for s in required), Decimal(0))
    if captured >= total_required:
        return "satisfied"
    overdue = any(s.due_at and s.due_at < now for s in required)
    exception = db.scalar(
        select(CommercialException).where(
            CommercialException.event_order_id == order.id,
            CommercialException.exception_type == "financial_hold_override",
            CommercialException.status == "approved",
        )
    )
    if exception is not None:
        return "approved_exception"
    return "financial_hold" if overdue else "due"


def list_payment_schedules(db: Session, order_id) -> list[PaymentSchedule]:
    return db.scalars(select(PaymentSchedule).where(PaymentSchedule.event_order_id == order_id)).all()


def _captured_amount(db: Session, order_id) -> Decimal:
    paid = db.scalars(
        select(Payment).where(Payment.event_order_id == order_id, Payment.state.in_(("paid", "part_refunded")))
    ).all()
    return sum((p.amount for p in paid), Decimal(0))


# ── Payments (doc Section 20/P, Section 28: provider-neutral, idempotent) ───────────────

def authorize_payment(db: Session, order: EventOrder, *, amount: Decimal, idempotency_key: str,
                       provider_name: str = "mock", simulate_failure: bool = False) -> Payment:
    existing = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    provider = payment_svc.get_provider(provider_name)
    result = provider.authorize(amount=amount, currency=order.currency, idempotency_key=idempotency_key,
                                 simulate_failure=simulate_failure)
    payment = Payment(
        event_order_id=order.id, provider=provider.name, provider_payment_ref=result.provider_payment_ref,
        amount=amount, currency=order.currency, state=result.state, idempotency_key=idempotency_key,
        authorized_at=result.authorized_at, failure_reason=result.failure_reason,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def capture_payment(db: Session, payment: Payment) -> Payment:
    if payment.state != "pending":
        raise ValueError(f"Cannot capture a payment in state '{payment.state}'")
    provider = payment_svc.get_provider(payment.provider)
    result = provider.capture(payment.provider_payment_ref)
    payment.state = result.state
    payment.captured_at = result.captured_at
    payment.settled_at = result.settled_at
    _reconcile_schedule(db, payment)
    db.commit()
    db.refresh(payment)
    return payment


def _reconcile_schedule(db: Session, payment: Payment) -> None:
    """Applies a captured payment against the oldest unsatisfied required schedule row —
    a simple FIFO allocation. Real partial-payment allocation rules are Finance policy
    (doc D6), not invented here; this is the scaffold's default."""
    schedules = [
        s for s in list_payment_schedules(db, payment.event_order_id)
        if s.status in ("not_due", "due", "financial_hold")
    ]
    if schedules:
        schedules.sort(key=lambda s: s.due_at or datetime.min.replace(tzinfo=timezone.utc))
        schedules[0].status = "satisfied"


def ingest_payment_webhook(db: Session, *, provider_name: str, provider_payment_ref: str, event_type: str,
                            idempotency_key: str) -> Payment | None:
    """Idempotent by idempotency_key (doc: "Provider webhook replay must be idempotent").
    A ref with no matching Payment row goes to UNMATCHED on nothing — there's no row to
    mark, so the caller should route it to the reconciliation exception queue
    (list_unmatched_settlements) rather than silently drop it."""
    already = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
    if already is not None:
        return already
    payment = db.scalar(
        select(Payment).where(Payment.provider == provider_name, Payment.provider_payment_ref == provider_payment_ref)
    )
    if payment is None:
        return None
    state_map = {
        "capture_succeeded": "paid", "capture_failed": "failed", "refunded": "refunded",
        "disputed": "disputed", "reversed": "reversed",
    }
    new_state = state_map.get(event_type)
    if new_state:
        payment.state = new_state
        payment.idempotency_key = idempotency_key
        if new_state == "paid":
            payment.settled_at = datetime.now(timezone.utc)
            _reconcile_schedule(db, payment)
    db.commit()
    db.refresh(payment)
    return payment


# ── Invoices (doc L2, Section 27) ────────────────────────────────────────────────────────

def issue_invoice(db: Session, order: EventOrder, *, due_date: datetime | None = None) -> Invoice:
    last = db.scalar(select(Invoice).order_by(Invoice.created_at.desc()).limit(1))
    next_n = 1
    if last is not None and last.number.rsplit("-", 1)[-1].isdigit():
        next_n = int(last.number.rsplit("-", 1)[-1]) + 1
    number = f"ZST-LE-INV-{next_n:06d}"
    invoice = Invoice(
        event_order_id=order.id, seller_legal_entity_id=_seller_entity(db, order), number=number,
        currency=order.currency, subtotal=order.subtotal, tax_amount=order.tax_amount,
        total_amount=order.total_amount, issue_date=datetime.now(timezone.utc), due_date=due_date,
        state="issued",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


def _seller_entity(db: Session, order: EventOrder) -> str:
    account = db.get(CommercialAccount, order.commercial_account_id)
    return account.seller_legal_entity_id if account else "zoiko_tech_inc"


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
    order.total_amount = (order.total_amount or Decimal(0)) + change_order.price_delta
    order.subtotal = (order.subtotal or Decimal(0)) + change_order.price_delta
    audit(db, actor=actor, action="commercial.change_order.accept", target_type="change_order",
          target_id=change_order.id, price_delta=str(change_order.price_delta))
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
    reservations = db.scalars(
        select(CapacityReservation).where(
            CapacityReservation.event_id == event.id, CapacityReservation.state == "hard_reserved",
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


def execute_refund_credit(db: Session, refund_credit: RefundCredit) -> RefundCredit:
    if refund_credit.status != "approved":
        raise ValueError("Refund/credit must be approved before it can be executed (maker-checker)")
    if refund_credit.type in ("refund",) and refund_credit.source_payment_id:
        payment = db.get(Payment, refund_credit.source_payment_id)
        provider = payment_svc.get_provider(payment.provider if payment else "mock")
        result = provider.refund(payment.provider_payment_ref if payment else "unknown", refund_credit.amount)
        refund_credit.provider_ref = result.provider_payment_ref
        if payment:
            payment.state = "part_refunded" if refund_credit.amount < payment.amount else "refunded"
    refund_credit.status = "executed"
    if refund_credit.incident_id:
        incident = db.get(EventIncident, refund_credit.incident_id)
        if incident:
            incident.review_state = "credit_refund_executed"
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
    payment.state = "disputed"
    audit(db, actor=actor, action="commercial.dispute.open", target_type="payment_dispute",
          target_id=dispute.id, amount=str(dispute_amount), reason_code=reason_code)
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
    if payment:
        payment.state = "paid" if won else "reversed"
    audit(db, actor=actor, action="commercial.dispute.resolve", target_type="payment_dispute",
          target_id=dispute.id, outcome="won" if won else "lost")
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

    if not capacity_confirmed(db, event):
        reasons.append("required capacity is not hard-reserved")

    if order is not None:
        fin_state = financial_readiness_state(db, order)
        if fin_state not in ("satisfied", "not_due", "approved_exception"):
            reasons.append(f"financial readiness is '{fin_state}'")
    elif event.billing_classification == "commercial":
        reasons.append("commercial event has no accepted order")

    return {"ready": not reasons, "blocking_reasons": reasons, "required_checks": required_codes}


# ── Replay entitlement (doc Section 14/J) ────────────────────────────────────────────────

def create_replay_entitlement(db: Session, event: Event, *, scope: str = "audience",
                               expires_at: datetime | None = None) -> ReplayEntitlement:
    ent = ReplayEntitlement(event_id=event.id, scope=scope, publish_state="not_available", expires_at=expires_at)
    db.add(ent)
    db.commit()
    db.refresh(ent)
    return ent


def publish_replay(db: Session, entitlement: ReplayEntitlement) -> ReplayEntitlement:
    """Publication is never automatic on live-end (doc J2) — this is the one explicit call
    that moves READY_FOR_REVIEW/NOT_AVAILABLE to PUBLISHED."""
    if entitlement.publish_state not in ("not_available", "ready_for_review"):
        raise ValueError(f"Cannot publish replay from state '{entitlement.publish_state}'")
    entitlement.publish_state = "published"
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
