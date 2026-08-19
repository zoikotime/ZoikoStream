"""Commercial/billing API for Live Events (ZST-LE-COM-001) — /commercial/*.

RBAC mapping (doc Section 25 "Commercial & Event RBAC — Canonical Access Matrix";
security.commercial_can/require_commercial implement the actual per-action matrix):
  * require_commercial("accept") gates customer-side commercial acceptance: accepting a
    quote, accepting an order, accepting a change order, authorizing payment. Both
    org_admin (Organization Owner) and billing_admin (Billing Admin) pass this.
  * require_commercial("change") gates change-order creation/cancellation-request —
    customer roles plus Zoiko sales/finance_ops/live_ops staff, per the doc's "Change
    authority" column.
  * require_commercial("refund_approve") gates refund/credit approve+execute — Zoiko
    finance_ops only (plus an unscoped super_admin, see security.py). Maker-checker
    (approver != requester) is still enforced separately in crud.commercial.
  * require_commercial("media_access") gates replay publication — customer host role
    plus Zoiko live_ops staff.
  * require_super_admin still gates the remaining Zoiko-side authority the doc's Section-25
    table doesn't break out to a specific staff row: catalog, service profiles,
    cancellation policy, quote issuance, order construction, capacity, payment
    schedule/invoice issuance, readiness checks, incidents, reconciliation (doc T1-T3
    "who owns X"). An unscoped super_admin (staff_commercial_role is NULL) also passes
    every require_commercial() gate — see security.commercial_can.
  * Plain get_current_user (any org member) gates read-only views scoped to their org's
    events, same as routers/events.py.
  * POST /commercial/webhooks/payments has NO user auth — a payment provider calls it
    directly. It is protected by signature verification (services.payments) instead.

Every event-scoped route resolves the event through the caller's org (or super_admin
bypass) via _get_event_or_404, mirroring routers/events.py exactly — a commercial object
belonging to another org must 404, not leak existence.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import commercial as crud
from ..db import get_db
from ..email import (
    send_cancellation_email, send_change_order_accepted_email, send_order_accepted_email,
    send_payment_failed_email, send_payment_receipt_email, send_refund_credit_email,
    send_replay_available_email,
)
from ..models import (
    CancellationPolicy, CapacityReservation, CatalogLine, CatalogVersion, ChangeOrder,
    CommercialAccount, Event, EventIncident, EventOrder, FinancialPeriod, Invoice, Payment,
    PaymentDispute, Quote, ReadinessCheck, ReconciliationException, RefundCredit,
    ReplayEntitlement, ServiceProfile, User,
)
from ..schemas.commercial import (
    CancelOrderRequest, CancellationPolicyCreate, CancellationPolicyOut, CancellationResult,
    CapacityHoldCreate, CapacityOut, CatalogLineCreate, CatalogLineOut, CatalogVersionCreate,
    CatalogVersionOut, ChangeOrderCreate, ChangeOrderOut, CommercialAccountOut, DisputeEvidenceCreate,
    DisputeOpenCreate, DisputeResolveCreate, ExceptionResolveCreate, FinancialPeriodOut, IncidentCreate,
    IncidentOut, InvoiceCreate, InvoiceOut, OrderAccept, OrderCreate, OrderLineCreate, OrderLineOut,
    OrderOut, PaymentAuthorizeCreate, PaymentDisputeOut, PaymentOut, PaymentScheduleCreate,
    PaymentScheduleOut, PaymentWebhookIn, PeriodCreate, QuoteCreate, QuoteOut,
    ReadinessCheckCreate, ReadinessCheckOut, ReadinessEvaluation, ReconciliationExceptionOut,
    ReconciliationReport, RefundCreditOut, RemedyProposeCreate, ReplayEntitlementCreate,
    ReplayEntitlementOut, ServiceProfileCreate, ServiceProfileOut,
)
from ..security import get_current_user, org_scoped, require_commercial, require_org_admin, require_super_admin

router = APIRouter(prefix="/commercial", tags=["commercial"])


def _get_event_or_404(db: Session, user: User, event_id: uuid.UUID) -> Event:
    """Org-scoped for everyone except super_admin (org_scoped's bypass) — the commerce
    console needs a super_admin to manage any org's event, not just their own, the same way
    routers/admin.py's cross-org endpoints already work. crud.event.get_event is a hard
    org_id filter with no such bypass, so this queries Event directly instead of reusing it."""
    stmt = select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
    ev = db.scalar(org_scoped(stmt, Event, user))
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return ev


def _get_order_or_404(db: Session, user: User, order_id: uuid.UUID) -> EventOrder:
    order = db.get(EventOrder, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    _get_event_or_404(db, user, order.event_id)  # org-scopes the lookup
    return order


def _order_contact(db: Session, order: EventOrder) -> tuple[str, str] | None:
    """Who to send a doc Q2 lifecycle email to for this order: the purchaser user if one is
    on the order, else the commercial account's billing contact. None if neither is set —
    callers must skip sending rather than fail the request over a missing contact."""
    if order.purchaser_id:
        purchaser = db.get(User, order.purchaser_id)
        if purchaser and purchaser.email:
            return purchaser.email, purchaser.full_name
    account = db.get(CommercialAccount, order.commercial_account_id)
    if account and account.billing_contact_email:
        return account.billing_contact_email, account.billing_contact_name or "there"
    return None


def _order_url(order: EventOrder) -> str:
    return f"{settings.APP_URL.rstrip('/')}/organization/events/{order.event_id}?tab=commercial"


# ── Catalog (Zoiko-side authority — doc T1) ──────────────────────────────────────────────

@router.get("/catalog-versions", response_model=list[CatalogVersionOut])
def list_catalog_versions(vertical: str | None = None, status_: str | None = None,
                           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.list_catalog_versions(db, vertical=vertical, status=status_)


@router.post("/catalog-versions", response_model=CatalogVersionOut, status_code=status.HTTP_201_CREATED)
def create_catalog_version(data: CatalogVersionCreate, admin: User = Depends(require_super_admin),
                            db: Session = Depends(get_db)):
    return crud.create_catalog_version(db, vertical=data.vertical, version_label=data.version_label, notes=data.notes)


@router.post("/catalog-versions/{catalog_version_id}/lines", response_model=CatalogLineOut, status_code=status.HTTP_201_CREATED)
def add_catalog_line(catalog_version_id: uuid.UUID, data: CatalogLineCreate,
                      admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    cv = db.get(CatalogVersion, catalog_version_id)
    if cv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catalog version not found")
    try:
        return crud.add_catalog_line(db, cv, **data.model_dump())
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/catalog-versions/{catalog_version_id}/publish", response_model=CatalogVersionOut)
def publish_catalog_version(catalog_version_id: uuid.UUID, admin: User = Depends(require_super_admin),
                             db: Session = Depends(get_db)):
    cv = db.get(CatalogVersion, catalog_version_id)
    if cv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catalog version not found")
    try:
        return crud.publish_catalog_version(db, cv, admin)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ── Service profiles (doc Section 10/F) ──────────────────────────────────────────────────

@router.get("/service-profiles", response_model=list[ServiceProfileOut])
def list_service_profiles(risk_tier: str | None = None, status_: str | None = None,
                           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.list_service_profiles(db, risk_tier=risk_tier, status=status_)


@router.post("/service-profiles", response_model=ServiceProfileOut, status_code=status.HTTP_201_CREATED)
def create_service_profile(data: ServiceProfileCreate, admin: User = Depends(require_super_admin),
                            db: Session = Depends(get_db)):
    return crud.create_service_profile(db, **data.model_dump())


@router.post("/service-profiles/{service_profile_id}/publish", response_model=ServiceProfileOut)
def publish_service_profile(service_profile_id: uuid.UUID, admin: User = Depends(require_super_admin),
                             db: Session = Depends(get_db)):
    profile = db.get(ServiceProfile, service_profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service profile not found")
    return crud.publish_service_profile(db, profile, admin)


# ── Cancellation policy (doc Section 9/E) ────────────────────────────────────────────────

@router.get("/cancellation-policies", response_model=list[CancellationPolicyOut])
def list_cancellation_policies(vertical: str | None = None, status_: str | None = None,
                                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.list_cancellation_policies(db, vertical=vertical, status=status_)


@router.post("/cancellation-policies", response_model=CancellationPolicyOut, status_code=status.HTTP_201_CREATED)
def create_cancellation_policy(data: CancellationPolicyCreate, admin: User = Depends(require_super_admin),
                                db: Session = Depends(get_db)):
    return crud.create_cancellation_policy(db, **data.model_dump())


@router.post("/cancellation-policies/{policy_id}/publish", response_model=CancellationPolicyOut)
def publish_cancellation_policy(policy_id: uuid.UUID, admin: User = Depends(require_super_admin),
                                 db: Session = Depends(get_db)):
    policy = db.get(CancellationPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cancellation policy not found")
    try:
        return crud.publish_cancellation_policy(db, policy, admin)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ── Commercial account (doc A1) ──────────────────────────────────────────────────────────

@router.get("/accounts/me", response_model=CommercialAccountOut)
def get_my_commercial_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.get_or_create_commercial_account(db, user.org_id)


# ── Quotes (create/issue: Zoiko Sales; accept: customer — doc A3/A4) ────────────────────

@router.get("/events/{event_id}/quotes", response_model=list[QuoteOut])
def list_quotes(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    return db.query(Quote).filter(Quote.event_id == ev.id).order_by(Quote.version.desc()).all()


@router.post("/events/{event_id}/quotes", response_model=QuoteOut, status_code=status.HTTP_201_CREATED)
def create_quote(event_id: uuid.UUID, data: QuoteCreate, admin: User = Depends(require_super_admin),
                  db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin, event_id)
    cv = db.get(CatalogVersion, data.catalog_version_id)
    if cv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catalog version not found")
    return crud.create_quote(
        db, ev, catalog_version=cv, amount=data.amount, tax_amount=data.tax_amount, currency=data.currency,
        created_by=admin, valid_until=data.valid_until, notes=data.notes,
    )


@router.post("/events/{event_id}/quotes/{quote_id}/issue", response_model=QuoteOut)
def issue_quote(event_id: uuid.UUID, quote_id: uuid.UUID, admin: User = Depends(require_super_admin),
                 db: Session = Depends(get_db)):
    _get_event_or_404(db, admin, event_id)
    quote = db.get(Quote, quote_id)
    if quote is None or quote.event_id != event_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found")
    try:
        crud.supersede_open_quotes(db, event_id, except_quote_id=quote.id)
        return crud.issue_quote(db, quote)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/events/{event_id}/quotes/{quote_id}/accept", response_model=QuoteOut)
def accept_quote(event_id: uuid.UUID, quote_id: uuid.UUID, user: User = Depends(require_commercial("accept")),
                  db: Session = Depends(get_db)):
    _get_event_or_404(db, user, event_id)
    quote = db.get(Quote, quote_id)
    if quote is None or quote.event_id != event_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found")
    try:
        return crud.accept_quote(db, quote)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ── Event orders (construction: Zoiko-side; acceptance: customer) ───────────────────────

@router.get("/events/{event_id}/orders", response_model=list[OrderOut])
def list_orders(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    return db.query(EventOrder).filter(EventOrder.event_id == ev.id).order_by(EventOrder.order_version.desc()).all()


@router.get("/events/{event_id}/orders/current", response_model=OrderOut)
def get_current_order(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    order = crud.get_current_order(db, ev.id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No order exists for this event yet")
    return order


@router.post("/events/{event_id}/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(event_id: uuid.UUID, data: OrderCreate, admin: User = Depends(require_super_admin),
                  db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin, event_id)
    cv = db.get(CatalogVersion, data.catalog_version_id)
    if cv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catalog version not found")
    service_profile = db.get(ServiceProfile, data.service_profile_id) if data.service_profile_id else None
    cancellation_policy = db.get(CancellationPolicy, data.cancellation_policy_id) if data.cancellation_policy_id else None
    quote = db.get(Quote, data.quote_id) if data.quote_id else None
    account = crud.get_or_create_commercial_account(db, ev.org_id)
    return crud.create_order(
        db, ev, commercial_account=account, catalog_version=cv, purchaser_type=data.purchaser_type,
        purchaser_id=data.purchaser_id, service_profile=service_profile, cancellation_policy=cancellation_policy,
        currency=data.currency, idempotency_key=data.idempotency_key or crud.new_idempotency_key(),
        quote=quote, billing_classification=data.billing_classification, billing_source=data.billing_source,
    )


@router.post("/events/{event_id}/orders/{order_id}/lines", response_model=OrderLineOut, status_code=status.HTTP_201_CREATED)
def add_order_line(event_id: uuid.UUID, order_id: uuid.UUID, data: OrderLineCreate,
                    admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    _get_event_or_404(db, admin, event_id)
    order = db.get(EventOrder, order_id)
    if order is None or order.event_id != event_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    line = db.get(CatalogLine, data.catalog_line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catalog line not found")
    try:
        return crud.add_order_line(db, order, line, quantity=data.quantity, is_addon=data.is_addon,
                                    is_complimentary=data.is_complimentary)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/events/{event_id}/orders/{order_id}/submit", response_model=OrderOut)
def submit_order(event_id: uuid.UUID, order_id: uuid.UUID, admin: User = Depends(require_super_admin),
                  db: Session = Depends(get_db)):
    _get_event_or_404(db, admin, event_id)
    order = db.get(EventOrder, order_id)
    if order is None or order.event_id != event_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    try:
        return crud.submit_order_for_acceptance(db, order)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/events/{event_id}/orders/{order_id}/accept", response_model=OrderOut)
def accept_order(event_id: uuid.UUID, order_id: uuid.UUID, data: OrderAccept, background: BackgroundTasks,
                  user: User = Depends(require_commercial("accept")), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    order = db.get(EventOrder, order_id)
    if order is None or order.event_id != event_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    try:
        order = crud.accept_order(db, order, user, terms_version=data.terms_version)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    contact = _order_contact(db, order)
    if contact:
        background.add_task(send_order_accepted_email, contact[0], contact[1], ev.title,
                             str(order.total_amount), order.currency, _order_url(order))
    return order


@router.post("/events/{event_id}/orders/{order_id}/activate", response_model=OrderOut)
def activate_order(event_id: uuid.UUID, order_id: uuid.UUID, admin: User = Depends(require_super_admin),
                    db: Session = Depends(get_db)):
    _get_event_or_404(db, admin, event_id)
    order = db.get(EventOrder, order_id)
    if order is None or order.event_id != event_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    try:
        return crud.activate_order(db, order)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/events/{event_id}/orders/{order_id}/cancel", response_model=CancellationResult)
def cancel_order(event_id: uuid.UUID, order_id: uuid.UUID, data: CancelOrderRequest, background: BackgroundTasks,
                  user: User = Depends(require_commercial("change")), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    order = db.get(EventOrder, order_id)
    if order is None or order.event_id != event_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    try:
        result = crud.cancel_order(db, ev, order, user, reason=data.reason)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    contact = _order_contact(db, result["order"])
    if contact:
        background.add_task(send_cancellation_email, contact[0], contact[1], ev.title,
                             str(result["refund_amount"]), result["order"].currency, _order_url(result["order"]))
    return CancellationResult(
        order=result["order"], policy_version=result["policy"].version_label,
        refund_amount=result["refund_amount"],
        refund_credit_id=result["refund_credit"].id if result["refund_credit"] else None,
    )


# ── Capacity (doc Section 7/C) ────────────────────────────────────────────────────────────

@router.get("/events/{event_id}/capacity", response_model=list[CapacityOut])
def list_capacity(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    return db.query(CapacityReservation).filter(CapacityReservation.event_id == ev.id).all()


@router.post("/events/{event_id}/capacity/hold", response_model=CapacityOut, status_code=status.HTTP_201_CREATED)
def soft_hold_capacity(event_id: uuid.UUID, data: CapacityHoldCreate, admin: User = Depends(require_super_admin),
                        db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin, event_id)
    return crud.soft_hold_capacity(db, ev, **data.model_dump())


@router.post("/events/{event_id}/capacity/{reservation_id}/reserve", response_model=CapacityOut)
def hard_reserve_capacity(event_id: uuid.UUID, reservation_id: uuid.UUID, order_id: uuid.UUID,
                           admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    _get_event_or_404(db, admin, event_id)
    reservation = db.get(CapacityReservation, reservation_id)
    order = db.get(EventOrder, order_id)
    if reservation is None or reservation.event_id != event_id or order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation or order not found")
    try:
        return crud.hard_reserve_capacity(db, reservation, order)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/events/{event_id}/capacity/{reservation_id}/release", response_model=CapacityOut)
def release_capacity(event_id: uuid.UUID, reservation_id: uuid.UUID, reason: str = "manual_release",
                      admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    _get_event_or_404(db, admin, event_id)
    reservation = db.get(CapacityReservation, reservation_id)
    if reservation is None or reservation.event_id != event_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found")
    return crud.release_capacity(db, reservation, reason)


# ── Payment schedule, payments, invoices (doc Sections 8, 20, 27) ───────────────────────

@router.get("/orders/{order_id}/payment-schedule", response_model=list[PaymentScheduleOut])
def list_payment_schedule(order_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order_or_404(db, user, order_id)
    return crud.list_payment_schedules(db, order.id)


@router.post("/orders/{order_id}/payment-schedule", response_model=PaymentScheduleOut, status_code=status.HTTP_201_CREATED)
def create_payment_schedule(order_id: uuid.UUID, data: PaymentScheduleCreate,
                             admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    order = _get_order_or_404(db, admin, order_id)
    return crud.create_payment_schedule(db, order, **data.model_dump())


@router.get("/orders/{order_id}/payments", response_model=list[PaymentOut])
def list_payments(order_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order_or_404(db, user, order_id)
    return db.query(Payment).filter(Payment.event_order_id == order.id).all()


@router.post("/orders/{order_id}/payments/authorize", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def authorize_payment(order_id: uuid.UUID, data: PaymentAuthorizeCreate, background: BackgroundTasks,
                       user: User = Depends(require_commercial("accept")), db: Session = Depends(get_db)):
    order = _get_order_or_404(db, user, order_id)
    payment = crud.authorize_payment(
        db, order, amount=data.amount, idempotency_key=data.idempotency_key,
        provider_name=data.provider_name, simulate_failure=data.simulate_failure,
    )
    if payment.state == "failed":
        ev = db.get(Event, order.event_id)
        contact = _order_contact(db, order)
        if contact and ev:
            background.add_task(send_payment_failed_email, contact[0], contact[1], ev.title,
                                 str(payment.amount), payment.currency, payment.failure_reason, _order_url(order))
    return payment


@router.post("/payments/{payment_id}/capture", response_model=PaymentOut)
def capture_payment(payment_id: uuid.UUID, background: BackgroundTasks,
                     admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    order = _get_order_or_404(db, admin, payment.event_order_id)
    try:
        payment = crud.capture_payment(db, payment)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if payment.state == "paid":
        ev = db.get(Event, order.event_id)
        contact = _order_contact(db, order)
        if contact and ev:
            background.add_task(send_payment_receipt_email, contact[0], contact[1], ev.title,
                                 str(payment.amount), payment.currency, _order_url(order))
    return payment


def _get_dispute_or_404(db: Session, user: User, dispute_id: uuid.UUID) -> PaymentDispute:
    dispute = db.get(PaymentDispute, dispute_id)
    if dispute is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute not found")
    _get_order_or_404(db, user, dispute.event_order_id)  # org-scopes the lookup
    return dispute


@router.get("/orders/{order_id}/disputes", response_model=list[PaymentDisputeOut])
def list_disputes(order_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order_or_404(db, user, order_id)
    return db.query(PaymentDispute).filter(PaymentDispute.event_order_id == order.id).all()


@router.post("/payments/{payment_id}/disputes", response_model=PaymentDisputeOut, status_code=status.HTTP_201_CREATED)
def open_dispute(payment_id: uuid.UUID, data: DisputeOpenCreate,
                  admin: User = Depends(require_commercial("refund_approve")), db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    _get_order_or_404(db, admin, payment.event_order_id)
    try:
        return crud.open_dispute(db, payment, admin, reason_code=data.reason_code, amount=data.amount)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/disputes/{dispute_id}/evidence", response_model=PaymentDisputeOut)
def submit_dispute_evidence(dispute_id: uuid.UUID, data: DisputeEvidenceCreate,
                             admin: User = Depends(require_commercial("refund_approve")), db: Session = Depends(get_db)):
    dispute = _get_dispute_or_404(db, admin, dispute_id)
    try:
        return crud.submit_dispute_evidence(db, dispute, admin, evidence=data.evidence)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/disputes/{dispute_id}/resolve", response_model=PaymentDisputeOut)
def resolve_dispute(dispute_id: uuid.UUID, data: DisputeResolveCreate,
                     admin: User = Depends(require_commercial("refund_approve")), db: Session = Depends(get_db)):
    dispute = _get_dispute_or_404(db, admin, dispute_id)
    try:
        return crud.resolve_dispute(db, dispute, admin, won=data.won)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/orders/{order_id}/invoices", response_model=list[InvoiceOut])
def list_invoices(order_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order_or_404(db, user, order_id)
    return db.query(Invoice).filter(Invoice.event_order_id == order.id).all()


@router.post("/orders/{order_id}/invoices", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def issue_invoice(order_id: uuid.UUID, data: InvoiceCreate, admin: User = Depends(require_super_admin),
                   db: Session = Depends(get_db)):
    order = _get_order_or_404(db, admin, order_id)
    return crud.issue_invoice(db, order, due_date=data.due_date)


# ── Change orders (doc Section 11/G) ─────────────────────────────────────────────────────

@router.get("/orders/{order_id}/change-orders", response_model=list[ChangeOrderOut])
def list_change_orders(order_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _get_order_or_404(db, user, order_id)
    return db.query(ChangeOrder).filter(ChangeOrder.event_order_id == order.id).all()


@router.post("/orders/{order_id}/change-orders", response_model=ChangeOrderOut, status_code=status.HTTP_201_CREATED)
def create_change_order(order_id: uuid.UUID, data: ChangeOrderCreate, admin: User = Depends(require_commercial("change")),
                         db: Session = Depends(get_db)):
    order = _get_order_or_404(db, admin, order_id)
    return crud.create_change_order(db, order, **data.model_dump())


@router.post("/change-orders/{change_order_id}/accept", response_model=ChangeOrderOut)
def accept_change_order(change_order_id: uuid.UUID, background: BackgroundTasks,
                         user: User = Depends(require_commercial("accept")), db: Session = Depends(get_db)):
    co = db.get(ChangeOrder, change_order_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Change order not found")
    order = _get_order_or_404(db, user, co.event_order_id)
    try:
        co = crud.accept_change_order(db, co, user)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    ev = db.get(Event, order.event_id)
    contact = _order_contact(db, order)
    if contact and ev:
        background.add_task(send_change_order_accepted_email, contact[0], contact[1], ev.title,
                             str(co.price_delta), order.currency, _order_url(order))
    return co


# ── Readiness (doc Section 13/I) ─────────────────────────────────────────────────────────

@router.get("/events/{event_id}/readiness", response_model=ReadinessEvaluation)
def evaluate_readiness(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    order = crud.get_current_order(db, ev.id)
    return crud.evaluate_readiness(db, ev, order)


@router.get("/events/{event_id}/readiness/checks", response_model=list[ReadinessCheckOut])
def list_readiness_checks(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    return db.query(ReadinessCheck).filter(ReadinessCheck.event_id == ev.id).all()


@router.post("/events/{event_id}/readiness/checks", response_model=ReadinessCheckOut, status_code=status.HTTP_201_CREATED)
def record_readiness_check(event_id: uuid.UUID, data: ReadinessCheckCreate, admin: User = Depends(require_super_admin),
                            db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin, event_id)
    return crud.record_readiness_check(db, ev, actor=admin, **data.model_dump())


@router.post("/events/{event_id}/capacity/approve-envelope", response_model=CapacityOut, status_code=status.HTTP_201_CREATED)
def approve_audience_capacity(event_id: uuid.UUID, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Operations approval for an event whose expected_audience exceeds the default 500-
    viewer envelope (doc Sec. 23.3) — clears the envelope_capacity_confirmed readiness gate.
    Self-service events (no commercial order) can hit this gate too, so this is deliberately
    not folded into the order-gated hard_reserve_capacity flow."""
    ev = _get_event_or_404(db, admin, event_id)
    if not ev.expected_audience or ev.expected_audience <= crud.DEFAULT_CAPACITY_ENVELOPE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                             f"Expected audience is within the default {crud.DEFAULT_CAPACITY_ENVELOPE}-viewer envelope — nothing to approve")
    return crud.approve_audience_capacity(db, ev, actor=admin)


# ── Incidents & remedies (doc Section 15/K, maker-checker) ──────────────────────────────

@router.get("/events/{event_id}/incidents", response_model=list[IncidentOut])
def list_incidents(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    return db.query(EventIncident).filter(EventIncident.event_id == ev.id).all()


@router.post("/events/{event_id}/incidents", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def open_incident(event_id: uuid.UUID, data: IncidentCreate, admin: User = Depends(require_super_admin),
                   db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin, event_id)
    fields = data.model_dump()
    order_id = fields.pop("event_order_id")
    order = db.get(EventOrder, order_id) if order_id else None
    return crud.open_incident(db, ev, admin, event_order=order, **fields)


@router.post("/incidents/{incident_id}/remedy", response_model=RefundCreditOut, status_code=status.HTTP_201_CREATED)
def propose_remedy(incident_id: uuid.UUID, data: RemedyProposeCreate, admin: User = Depends(require_super_admin),
                    db: Session = Depends(get_db)):
    incident = db.get(EventIncident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    order = db.get(EventOrder, data.event_order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return crud.propose_remedy(
        db, incident, order, admin, remedy_type=data.remedy_type, amount=data.amount,
        reason_code=data.reason_code, policy_version=data.policy_version,
    )


@router.post("/refund-credits/{refund_credit_id}/approve", response_model=RefundCreditOut)
def approve_refund_credit(refund_credit_id: uuid.UUID, admin: User = Depends(require_commercial("refund_approve")),
                           db: Session = Depends(get_db)):
    rc = db.get(RefundCredit, refund_credit_id)
    if rc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Refund/credit not found")
    try:
        return crud.approve_refund_credit(db, rc, admin)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/refund-credits/{refund_credit_id}/execute", response_model=RefundCreditOut)
def execute_refund_credit(refund_credit_id: uuid.UUID, background: BackgroundTasks,
                           admin: User = Depends(require_commercial("refund_approve")),
                           db: Session = Depends(get_db)):
    rc = db.get(RefundCredit, refund_credit_id)
    if rc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Refund/credit not found")
    try:
        rc = crud.execute_refund_credit(db, rc)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    order = db.get(EventOrder, rc.event_order_id)
    ev = db.get(Event, order.event_id) if order else None
    contact = _order_contact(db, order) if order else None
    if contact and ev:
        background.add_task(send_refund_credit_email, contact[0], contact[1], ev.title,
                             str(rc.amount), order.currency, rc.type, _order_url(order))
    return rc


# ── Replay entitlement (doc Section 14/J) ────────────────────────────────────────────────

@router.get("/events/{event_id}/replay-entitlements", response_model=list[ReplayEntitlementOut])
def list_replay_entitlements(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    return db.query(ReplayEntitlement).filter(ReplayEntitlement.event_id == ev.id).all()


@router.post("/events/{event_id}/replay-entitlements", response_model=ReplayEntitlementOut, status_code=status.HTTP_201_CREATED)
def create_replay_entitlement(event_id: uuid.UUID, data: ReplayEntitlementCreate,
                               admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin, event_id)
    return crud.create_replay_entitlement(db, ev, scope=data.scope, expires_at=data.expires_at)


@router.post("/replay-entitlements/{entitlement_id}/publish", response_model=ReplayEntitlementOut)
def publish_replay(entitlement_id: uuid.UUID, background: BackgroundTasks,
                    admin: User = Depends(require_commercial("media_access")), db: Session = Depends(get_db)):
    ent = db.get(ReplayEntitlement, entitlement_id)
    if ent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Replay entitlement not found")
    try:
        ent = crud.publish_replay(db, ent)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    # "customer" scope = notify the purchaser (doc Q2). "audience" scope would mean every
    # attendee, a broadcast-email concern this module doesn't own — not sent here.
    if ent.scope == "customer":
        ev = db.get(Event, ent.event_id)
        order = crud.get_current_order(db, ent.event_id)
        contact = _order_contact(db, order) if order else None
        if contact and ev:
            watch_url = f"{settings.APP_URL.rstrip('/')}/events/{ent.event_id}/watch"
            background.add_task(send_replay_available_email, contact[0], contact[1], ev.title, watch_url)
    return ent


@router.post("/replay-entitlements/{entitlement_id}/retry-watermark", response_model=ReplayEntitlementOut)
def retry_replay_watermark(entitlement_id: uuid.UUID,
                            admin: User = Depends(require_commercial("media_access")), db: Session = Depends(get_db)):
    """Re-queues a failed watermark burn (services/delivery.py's ticker picks it up on its
    next tick) without touching publish_state -- the publish decision itself isn't in
    question, only whether the file behind it is ready yet."""
    ent = db.get(ReplayEntitlement, entitlement_id)
    if ent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Replay entitlement not found")
    try:
        return crud.retry_replay_watermark(db, ent)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ── Reconciliation (doc Section 29) ──────────────────────────────────────────────────────

@router.get("/reconciliation", response_model=ReconciliationReport)
def reconciliation_report(admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    orphans = crud.list_capacity_orphans(db)
    return ReconciliationReport(
        reservations_without_order=orphans["reservations_without_order"],
        reservations_with_missing_order=orphans["reservations_with_missing_order"],
        unmatched_settlements=crud.list_unmatched_settlements(db),
        orders_missing_invoice=crud.list_orders_missing_invoice(db),
    )


# ── Financial period-close (doc Section 29 "Period close") — Finance ownership, not one of
# Section 25's five RBAC-matrix actions, so gated to require_super_admin like the other
# Zoiko-side registries (catalog, service profiles, ...) rather than require_commercial().

@router.get("/periods", response_model=list[FinancialPeriodOut])
def list_periods(admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return db.query(FinancialPeriod).order_by(FinancialPeriod.period_start.desc()).all()


@router.post("/periods", response_model=FinancialPeriodOut, status_code=status.HTTP_201_CREATED)
def create_period(data: PeriodCreate, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return crud.get_or_create_period(db, label=data.label, period_start=data.period_start, period_end=data.period_end)


@router.post("/periods/{period_id}/close", response_model=FinancialPeriodOut)
def close_period(period_id: uuid.UUID, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    period = db.get(FinancialPeriod, period_id)
    if period is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Period not found")
    try:
        return crud.close_period(db, period, admin)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/periods/{period_id}/exceptions", response_model=list[ReconciliationExceptionOut])
def list_period_exceptions(period_id: uuid.UUID, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return db.query(ReconciliationException).filter(ReconciliationException.period_id == period_id).all()


@router.patch("/exceptions/{exception_id}", response_model=ReconciliationExceptionOut)
def resolve_exception(exception_id: uuid.UUID, data: ExceptionResolveCreate,
                       admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    exception = db.get(ReconciliationException, exception_id)
    if exception is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reconciliation exception not found")
    try:
        return crud.resolve_exception(db, exception, admin, status=data.status, resolution_notes=data.resolution_notes)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ── Payment webhook (public — provider calls this directly, no user JWT) ────────────────

@router.post("/webhooks/payments", status_code=status.HTTP_200_OK)
async def payment_webhook(request: Request, data: PaymentWebhookIn, db: Session = Depends(get_db)):
    """No production provider is configured (services.payments docstring), so there is no
    real signing secret yet — refuse outright rather than accept-and-process, the same
    choice routers/live.py's LiveKit webhook makes when it isn't configured. This endpoint
    is unauthenticated (a real provider calls it directly, no user JWT), so accepting any
    unsigned payload here would let anyone forge a "payment captured" event and mark a real
    order paid by guessing/reading its provider_payment_ref."""
    from ..services.payments import verify_webhook_signature
    if not settings.PAYMENTS_WEBHOOK_SECRET:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Payments provider is not configured")
    body = await request.body()
    signature = request.headers.get("x-webhook-signature")
    if not verify_webhook_signature(body, signature, secret=settings.PAYMENTS_WEBHOOK_SECRET):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")
    payment = crud.ingest_payment_webhook(
        db, provider_name=data.provider_name, provider_payment_ref=data.provider_payment_ref,
        event_type=data.event_type, idempotency_key=data.idempotency_key,
    )
    return {"matched": payment is not None}
