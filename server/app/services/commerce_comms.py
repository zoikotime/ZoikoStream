"""Billing, payment and entitlement communications (ZST-EC-001 COM-006, COM-007, COM-008).

**The "No charge was made" correction.** `email.send_payment_failed_email` appended that
sentence unconditionally, on every failure, whatever the provider had actually done. A
declined card and a payment that failed after an authorization was placed are different
facts, and telling a customer no charge exists while their bank shows a pending hold is a
support incident and a trust problem. `charge_position()` derives the claim from
`Payment.state` alone and returns one of three positions; the copy is generated from it, so
the strong claim is only ever made when the committed state supports it.

**Reference masking.** Four different identifier classes were being treated as one:

    Invoice.number             customer-facing business reference  -> shown in full
    EventOrder.order_number    customer-facing business reference  -> shown in full
    Payment.provider_payment_ref  processor identifier             -> masked to last 4
    PaymentDispute.provider_dispute_ref  processor identifier      -> masked to last 4
    any database UUID         internal                             -> never shown whole

`mask_reference()` handles the third and fourth; `short_id()` gives an internal row a
customer-quotable stub without exposing the UUID.

**Recipient resolution.** COM mail goes to BILLING contacts, which is a different population
from the event owner (`event_comms.resolve`) and from the event team (`event_ops.owner_and_team`).
`billing_recipients()` resolves the commercial account's billing contact plus the roles the
authorization matrix already treats as commercially responsible - never an event owner or a
contributor who merely happens to be an admin.

**No billing rule is duplicated here.** Entitlement figures come from
`services.org.entitlements()`; the provisional/reconciled distinction comes from
`FinancialPeriod.status`; overdue comes from `Invoice.due_date` and `Invoice.state`. This
module compares committed state to what was last communicated, and nothing more.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    CHARGE_MAY_BE_AUTHORIZED,
    CHARGE_NO_CHARGE,
    CHARGE_UNKNOWN,
    ENTITLEMENT_METRICS,
    OVERDUE_EXCLUDED_INVOICE_STATES,
    PAYMENT_FAILURE_LABELS,
    USAGE_WARNING_THRESHOLD_PERCENT,
    CommercialAccount,
    CommerceNotice,
    Event,
    EventOrder,
    FinancialPeriod,
    Invoice,
    OrgEntitlementState,
    Organization,
    Payment,
    PaymentDispute,
    RefundCredit,
    UsageReport,
    User,
)
from ..security import commercial_can

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ══ reference presentation ══════════════════════════════════════════════════════════════

def mask_reference(reference: str | None, keep: int = 4) -> str:
    """Show only the tail of a processor identifier.

    A provider payment or dispute reference is enough to look an account up in a processor
    dashboard, so the full value is never mailed. The tail is kept because a customer
    reconciling a bank line needs to be able to match it.
    """
    value = (reference or "").strip()
    if not value:
        return "Not available"
    if len(value) <= keep:
        return "•" * 4 + value
    return "•" * 4 + value[-keep:]


def short_id(row_id) -> str:
    """A quotable stub for an internal row. Never the whole UUID."""
    return str(row_id)[:8].upper() if row_id else "Not available"


def money(amount, currency: str | None) -> str:
    value = Decimal(str(amount or 0)).quantize(Decimal("0.01"))
    return f"{(currency or '').upper()} {value}".strip()


# ══ recipients ══════════════════════════════════════════════════════════════════════════

class BillingRecipients:
    """Billing-responsible people, kept apart from event and contributor audiences."""

    def __init__(self, billing=None, owner=None, admins=None, technical=None):
        self.billing = billing                 # (email, name) | None
        self.owner = owner                     # User | None
        self.admins = list(admins or [])       # billing-authorized users
        self.technical = list(technical or [])  # technical admins (COM-008 only)

    def _pairs(self, include_technical: bool = False):
        out = []
        if self.billing and self.billing[0]:
            out.append((self.billing[0], self.billing[1] or "there"))
        for user in ([self.owner] if self.owner else []) + self.admins + (
                self.technical if include_technical else []):
            if user is not None and getattr(user, "email", None):
                out.append((user.email, user.full_name or "there"))
        return out

    def addresses(self, include_technical: bool = False) -> list[str]:
        seen, out = set(), []
        for address, _n in self._pairs(include_technical):
            key = address.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(address)
        return out

    def name_for(self, address: str, include_technical: bool = False) -> str:
        target = (address or "").strip().lower()
        for candidate, name in self._pairs(include_technical):
            if candidate.strip().lower() == target:
                return name
        return "there"


def _authorized(db: Session, org_id, action: str) -> list[User]:
    """Members the commercial matrix already trusts for one action."""
    if not org_id:
        return []
    members = db.scalars(
        select(User).where(User.org_id == org_id, User.is_active.is_(True),
                           User.role != "super_admin")).all()
    out = []
    for user in members:
        try:
            if commercial_can(user, action):
                out.append(user)
        except ValueError:  # pragma: no cover - constant action name
            continue
    return out


def billing_recipients(db: Session, *, org: Organization | None = None,
                       order: EventOrder | None = None,
                       include_owner: bool = False) -> BillingRecipients:
    """Who may receive a financial notice.

    The commercial account's billing contact is the primary address. `include_owner` is
    off by default because COM-006 says an organization owner is NOT a substitute for a
    billing contact - only COM-007 and COM-008 add them, and only where the family requires.
    """
    billing = None
    account_id = getattr(order, "commercial_account_id", None)
    if account_id:
        account = db.get(CommercialAccount, account_id)
        if account and account.billing_contact_email:
            billing = (account.billing_contact_email, account.billing_contact_name)

    org_id = org.id if org else None
    if org_id is None and order is not None:
        event = db.get(Event, order.event_id) if order.event_id else None
        org_id = event.org_id if event else None
        org = db.get(Organization, org_id) if org_id else None

    owner = None
    if include_owner and org is not None and org.owner_user_id:
        owner = db.get(User, org.owner_user_id)
    # "accept" is the commercial authority the matrix grants org_admin and billing_admin -
    # the two roles that genuinely own money decisions.
    return BillingRecipients(billing=billing, owner=owner,
                             admins=_authorized(db, org_id, "accept"))


def _org_of_order(db: Session, order: EventOrder | None) -> Organization | None:
    if order is None:
        return None
    event = db.get(Event, order.event_id) if order.event_id else None
    return db.get(Organization, event.org_id) if event else None


# ══ shared plumbing ═════════════════════════════════════════════════════════════════════

def _claim(db: Session, *, kind: str, subject_type: str, subject_id, org_id=None,
           order_id=None, sequence: int = 0, detail: str | None = None) -> bool:
    """Durable, single-shot claim of one commerce transition.

    The unique constraint is the guarantee: a concurrent duplicate loses the insert rather
    than racing a read-then-write.
    """
    existing = db.scalar(
        select(CommerceNotice).where(CommerceNotice.kind == kind,
                                     CommerceNotice.subject_type == subject_type,
                                     CommerceNotice.subject_id == subject_id))
    if existing is not None and existing.sequence >= sequence:
        return False
    try:
        db.add(CommerceNotice(kind=kind, subject_type=subject_type, subject_id=subject_id,
                              org_id=org_id, event_order_id=order_id, sequence=sequence,
                              detail=detail))
        db.commit()
        return True
    except Exception:  # noqa: BLE001 - a lost uniqueness race is a successful dedup
        db.rollback()
        log.info("Commerce notice %s/%s already claimed", kind, subject_id)
        return False


def _may_send(family: str, org) -> bool:
    from . import notifications

    return notifications.should_send_operational_notification(family=family, org=org)


def _queue(background, send, people: BillingRecipients, *, include_technical=False,
           **kwargs) -> None:
    """Queue one message per deduplicated billing address.

    Wrapped so a provider failure can never propagate into the caller's transaction: an
    invoice, a payment, a refund, a dispute and an entitlement change must all survive
    Resend being down.
    """
    try:
        for address in people.addresses(include_technical):
            background.add_task(send, address,
                                name=people.name_for(address, include_technical), **kwargs)
    except UnsafeLinkError:
        log.exception("Commerce notice not queued: APP_URL unsafe for this environment")


class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a notice must never break committed money state
            log.exception("Commerce notice failed")


def billing_url() -> str:
    """Customer-facing billing surface. Never Super Admin."""
    return f"{email_mod.public_base_url()}/organization/billing"


# ══ COM-006 — invoice and payment confirmation ══════════════════════════════════════════

def _invoice_context(db: Session, invoice: Invoice):
    order = db.get(EventOrder, invoice.event_order_id)
    org = _org_of_order(db, order)
    event = db.get(Event, order.event_id) if order and order.event_id else None
    return order, org, event


def outstanding_balance(db: Session, invoice: Invoice) -> Decimal | None:
    """Invoice total less everything captured against it.

    Returns None when no payment row exists yet, so the message can omit a balance rather
    than assert one it cannot compute.
    """
    payments = db.scalars(
        select(Payment).where(Payment.invoice_id == invoice.id,
                              Payment.state.in_(("paid", "partially_paid")))).all()
    if not payments:
        return None
    captured = sum((Decimal(str(p.amount)) for p in payments), Decimal("0"))
    return (Decimal(str(invoice.total_amount)) - captured).quantize(Decimal("0.01"))


def notify_invoice_available(db: Session, background, invoice: Invoice) -> bool:
    """Announce a committed, ISSUED invoice.

    Gated on the state: a draft invoice is an internal working document and announcing it
    would ask a customer to pay something not yet issued.
    """
    if invoice.state != "issued":
        return False
    order, org, event = _invoice_context(db, invoice)
    if not _claim(db, kind="invoice_available", subject_type="invoice",
                  subject_id=invoice.id, org_id=org.id if org else None,
                  order_id=invoice.event_order_id):
        return False
    if not _may_send("COM-006", org):
        return False
    people = billing_recipients(db, org=org, order=order)
    if not people.addresses():
        return False

    _queue(background, email_mod.send_invoice_available_email, people,
           # A canonical customer-facing business reference - shown in full on purpose.
           invoice_number=invoice.number,
           order_reference=short_id(invoice.event_order_id),
           event_title=(event.title if event else None) or "your account",
           issue_date=email_mod.billing_date(invoice.issue_date),
           due_date=email_mod.billing_date(invoice.due_date),
           currency=(invoice.currency or "").upper(),
           amount_due=money(invoice.total_amount, invoice.currency),
           subtotal=money(invoice.subtotal, invoice.currency),
           # Tax is only described when the platform actually determined it.
           tax_summary=(f"{money(invoice.tax_amount, invoice.currency)}"
                        + (f" ({invoice.tax_treatment})" if invoice.tax_treatment else "")
                        if invoice.tax_amount is not None else "Not determined"),
           billing_entity=invoice.seller_legal_entity_id or "Zoiko Steam",
           org_name=org.name if org else "your Organization",
           billing_url=billing_url())
    return True


def notify_payment_received(db: Session, background, payment: Payment) -> bool:
    """Announce a definitively captured payment.

    `paid` only. A `pending`, `requires_action` or `partially_paid` payment is provisional
    and must not be reported as received - which is what stops a receipt going out for money
    that has not actually arrived.
    """
    if payment.state != "paid":
        return False
    order = db.get(EventOrder, payment.event_order_id)
    org = _org_of_order(db, order)
    if not _claim(db, kind="payment_received", subject_type="payment",
                  subject_id=payment.id, org_id=org.id if org else None,
                  order_id=payment.event_order_id):
        return False
    if not _may_send("COM-006", org):
        return False
    people = billing_recipients(db, org=org, order=order)
    if not people.addresses():
        return False

    invoice = db.get(Invoice, payment.invoice_id) if payment.invoice_id else None
    balance = outstanding_balance(db, invoice) if invoice else None
    _queue(background, email_mod.send_payment_received_email, people,
           amount=money(payment.amount, payment.currency),
           currency=(payment.currency or "").upper(),
           paid_at=email_mod.billing_date(payment.captured_at or payment.settled_at),
           invoice_number=invoice.number if invoice else "Not invoiced",
           # Masked: this is a processor identifier, not a business reference.
           payment_reference=mask_reference(payment.provider_payment_ref),
           method=(payment.method_type or "Not recorded").title(),
           balance=(money(balance, payment.currency) if balance is not None
                    else "Not available"),
           org_name=org.name if org else "your Organization",
           billing_url=billing_url())
    return True


def notify_refund_or_credit(db: Session, background, remedy: RefundCredit) -> str | None:
    """Announce a refund OR a credit note - never one message for both.

    `RefundCredit.type` already distinguishes them: a refund returns money, a credit is an
    accounting adjustment that may not. Conflating them would tell a customer to expect a
    bank credit that is never coming.
    """
    if remedy.status not in ("approved", "executed"):
        return None
    kind = {"refund": "refund_issued", "credit": "credit_note",
            "fee_waiver": "credit_note"}.get(remedy.type)
    if kind is None:
        return None
    order = db.get(EventOrder, remedy.event_order_id)
    org = _org_of_order(db, order)
    if not _claim(db, kind=kind, subject_type="refund_credit", subject_id=remedy.id,
                  org_id=org.id if org else None, order_id=remedy.event_order_id):
        return None
    if not _may_send("COM-006", org):
        return None
    people = billing_recipients(db, org=org, order=order)
    if not people.addresses():
        return None

    invoice = db.scalar(
        select(Invoice).where(Invoice.event_order_id == remedy.event_order_id)
        .order_by(Invoice.created_at.desc()))
    shared = {
        "amount": money(remedy.amount, invoice.currency if invoice else None),
        "reference": short_id(remedy.id),
        "issued_at": email_mod.billing_date(remedy.created_at),
        "invoice_number": invoice.number if invoice else "Not invoiced",
        # A reason CODE, mapped to safe wording - never an internal note.
        "reason": REMEDY_REASON_LABELS.get(remedy.reason_code, "Commercial adjustment"),
        "org_name": org.name if org else "your Organization",
        "billing_url": billing_url(),
    }
    if kind == "refund_issued":
        _queue(background, email_mod.send_refund_issued_email, people,
               # Masked: a refund provider reference is a processor identifier.
               payment_reference=mask_reference(remedy.provider_ref),
               status=("Sent to your payment provider" if remedy.status == "executed"
                       else "Approved and being processed"),
               **shared)
        return "refund_issued"
    _queue(background, email_mod.send_credit_note_email, people,
           # Stated explicitly, because a credit note is NOT money returned.
           effect=("This is an accounting adjustment applied to your account. It is not a "
                   "refund to your payment method."),
           waiver=remedy.type == "fee_waiver",
           **shared)
    return "credit_note"


REMEDY_REASON_LABELS = {
    "service_failure": "Service did not meet the agreed standard",
    "cancellation": "Event cancellation",
    "goodwill": "Goodwill adjustment",
    "billing_correction": "Billing correction",
    "duplicate_payment": "Duplicate payment",
}


# ══ COM-007 — payment problem lifecycle ═════════════════════════════════════════════════

def charge_position(payment: Payment) -> str:
    """What the platform can honestly say about a debit, from `Payment.state` alone.

    This is the fix for the unconditional "No charge was made". Only `requires_action` and
    `failed` WITHOUT an authorization prove no debit exists; a payment that reached
    authorization may well be showing as a hold on the customer's statement, and one in an
    ambiguous state proves nothing either way.
    """
    if payment.authorized_at is not None and payment.captured_at is None:
        return CHARGE_MAY_BE_AUTHORIZED
    if payment.state in ("requires_action",):
        return CHARGE_NO_CHARGE
    if payment.state == "failed" and payment.authorized_at is None:
        return CHARGE_NO_CHARGE
    return CHARGE_UNKNOWN


CHARGE_SENTENCES = {
    CHARGE_NO_CHARGE: "The payment was not completed and no charge was recorded.",
    CHARGE_MAY_BE_AUTHORIZED: ("The payment was not completed. Your bank may temporarily "
                               "show an authorization, which is released automatically."),
    CHARGE_UNKNOWN: "We could not complete the payment.",
}


def failure_category(payment: Payment) -> str:
    """Map a provider failure onto one customer-safe category.

    `Payment.failure_reason` can carry a raw processor decline string, which is never mailed.
    Only the category label is.
    """
    raw = (payment.failure_reason or "").lower()
    if "authentication" in raw or "3ds" in raw or "action" in raw:
        return "authentication_required"
    if "insufficient" in raw or "funds" in raw:
        return "insufficient_funds"
    if "declin" in raw or "card" in raw:
        return "declined"
    if raw:
        return "processing_error"
    return "unknown"


def notify_payment_failed(db: Session, background, payment: Payment) -> bool:
    if payment.state != "failed":
        return False
    order = db.get(EventOrder, payment.event_order_id)
    org = _org_of_order(db, order)
    if not _claim(db, kind="payment_failed", subject_type="payment", subject_id=payment.id,
                  org_id=org.id if org else None, order_id=payment.event_order_id):
        return False
    if not _may_send("COM-007", org):
        return False
    # COM-007: billing contacts, plus the owner because a failed payment can suspend service.
    people = billing_recipients(db, org=org, order=order, include_owner=True)
    if not people.addresses():
        return False

    invoice = db.get(Invoice, payment.invoice_id) if payment.invoice_id else None
    position = charge_position(payment)
    _queue(background, email_mod.send_payment_problem_email, people,
           variant="failed",
           invoice_number=invoice.number if invoice else "Not invoiced",
           order_reference=short_id(payment.event_order_id),
           amount=money(payment.amount, payment.currency),
           currency=(payment.currency or "").upper(),
           occurred_at=email_mod.billing_date(payment.created_at),
           # Derived from committed state - never a blanket claim.
           charge_note=CHARGE_SENTENCES[position],
           category=PAYMENT_FAILURE_LABELS[failure_category(payment)],
           payment_reference=mask_reference(payment.provider_payment_ref),
           next_action="Update your payment details and retry the payment.",
           due_date="Not applicable",
           resolved_at="Not applicable",
           org_name=org.name if org else "your Organization",
           billing_url=billing_url())
    return True


def overdue_position(db: Session, invoice: Invoice,
                     now: datetime | None = None) -> tuple[bool, str]:
    """Whether an overdue reminder is genuinely owed for this invoice."""
    moment = now or _now()
    if invoice.state in OVERDUE_EXCLUDED_INVOICE_STATES:
        return False, f"the invoice is {invoice.state}"
    if invoice.due_date is None:
        return False, "the invoice has no due date"
    if invoice.due_date >= moment:
        return False, "the invoice is not yet due"
    # Collection is suspended while a dispute is live: chasing a customer who has already
    # disputed the charge is exactly what the policy carve-out exists to prevent.
    disputed = db.scalar(
        select(PaymentDispute)
        .join(Payment, Payment.id == PaymentDispute.payment_id)
        .where(Payment.invoice_id == invoice.id,
               PaymentDispute.status.in_(("opened", "evidence_required",
                                          "evidence_submitted"))))
    if disputed is not None:
        return False, "collection is suspended while a dispute is open"
    balance = outstanding_balance(db, invoice)
    if balance is not None and balance <= 0:
        return False, "the invoice is fully paid"
    return True, "overdue"


def notify_invoice_overdue(db: Session, background, invoice: Invoice,
                           sequence: int = 1) -> bool:
    owed, _why = overdue_position(db, invoice)
    if not owed:
        return False
    order, org, _event = _invoice_context(db, invoice)
    if not _claim(db, kind="invoice_overdue", subject_type="invoice", subject_id=invoice.id,
                  org_id=org.id if org else None, order_id=invoice.event_order_id,
                  sequence=sequence):
        return False
    if not _may_send("COM-007", org):
        return False
    people = billing_recipients(db, org=org, order=order, include_owner=True)
    if not people.addresses():
        return False

    balance = outstanding_balance(db, invoice)
    _queue(background, email_mod.send_payment_problem_email, people,
           variant="overdue",
           invoice_number=invoice.number,
           order_reference=short_id(invoice.event_order_id),
           amount=money(balance if balance is not None else invoice.total_amount,
                        invoice.currency),
           currency=(invoice.currency or "").upper(),
           occurred_at=email_mod.billing_date(invoice.due_date),
           charge_note="",
           category=f"Invoice {invoice.state}",
           payment_reference="Not applicable",
           next_action="Please settle this invoice to avoid interruption.",
           due_date=email_mod.billing_date(invoice.due_date),
           resolved_at="Not applicable",
           org_name=org.name if org else "your Organization",
           billing_url=billing_url())
    return True


def notify_billing_resolved(db: Session, background, *, invoice: Invoice | None = None,
                            payment: Payment | None = None) -> bool:
    """Announce that a previously communicated problem is authoritatively resolved.

    A retry STARTING is not a resolution: the payment must have reached `paid`, or the
    invoice `paid`. There is no path here from "we tried again".
    """
    subject_id = subject_type = None
    resolved_state = None
    if payment is not None and payment.state == "paid":
        subject_id, subject_type = payment.id, "payment"
        resolved_state = "The payment completed successfully."
        order = db.get(EventOrder, payment.event_order_id)
    elif invoice is not None and invoice.state == "paid":
        subject_id, subject_type = invoice.id, "invoice"
        resolved_state = "The invoice is now paid in full."
        order = db.get(EventOrder, invoice.event_order_id)
    else:
        return False

    # Only worth saying if a problem was actually communicated in the first place.
    had_problem = db.scalar(
        select(CommerceNotice).where(
            CommerceNotice.kind.in_(("payment_failed", "invoice_overdue")),
            CommerceNotice.event_order_id == (order.id if order else None)))
    if had_problem is None:
        return False

    org = _org_of_order(db, order)
    if not _claim(db, kind="billing_resolved", subject_type=subject_type,
                  subject_id=subject_id, org_id=org.id if org else None,
                  order_id=order.id if order else None):
        return False
    if not _may_send("COM-007", org):
        return False
    people = billing_recipients(db, org=org, order=order, include_owner=True)
    if not people.addresses():
        return False

    _queue(background, email_mod.send_payment_problem_email, people,
           variant="resolved",
           invoice_number=(invoice.number if invoice else "Not invoiced"),
           order_reference=short_id(order.id if order else None),
           amount=money(payment.amount if payment else (invoice.total_amount if invoice else 0),
                        (payment.currency if payment else None)
                        or (invoice.currency if invoice else None)),
           currency="",
           occurred_at="Not applicable",
           charge_note="",
           category=resolved_state,
           payment_reference=(mask_reference(payment.provider_payment_ref) if payment
                              else "Not applicable"),
           next_action="No further action is needed.",
           due_date="Not applicable",
           resolved_at=email_mod.billing_date(_now()),
           org_name=org.name if org else "your Organization",
           billing_url=billing_url())
    return True


# -- disputes ----------------------------------------------------------------------------

DISPUTE_KIND = {
    "opened": "dispute_opened",
    "evidence_required": "dispute_action_required",
    "won": "dispute_resolved",
    "lost": "dispute_resolved",
    "withdrawn": "dispute_resolved",
}
DISPUTE_OUTCOME = {
    "won": "The dispute was resolved in favour of the original charge.",
    "lost": "The dispute was resolved in the cardholder's favour and the amount was returned.",
    "withdrawn": "The dispute was withdrawn.",
}


def notify_dispute(db: Session, background, dispute: PaymentDispute) -> str | None:
    """Announce a real dispute transition. `evidence_submitted` deliberately sends nothing -
    it is an internal step with no customer action attached."""
    kind = DISPUTE_KIND.get(dispute.status)
    if kind is None:
        return None
    order = db.get(EventOrder, dispute.event_order_id)
    org = _org_of_order(db, order)
    if not _claim(db, kind=kind, subject_type="dispute", subject_id=dispute.id,
                  org_id=org.id if org else None, order_id=dispute.event_order_id,
                  detail=dispute.status):
        return None
    if not _may_send("COM-007", org):
        return None
    people = billing_recipients(db, org=org, order=order, include_owner=True)
    if not people.addresses():
        return None

    _queue(background, email_mod.send_dispute_email, people,
           variant=kind,
           # Masked: a provider dispute reference identifies the case in the processor.
           dispute_reference=short_id(dispute.id),
           provider_reference=mask_reference(dispute.provider_dispute_ref),
           amount=money(dispute.amount, dispute.currency),
           opened_at=email_mod.billing_date(dispute.opened_at),
           evidence_due=(email_mod.billing_date(dispute.evidence_due_by)
                         if dispute.evidence_due_by else None),
           resolved_at=(email_mod.billing_date(dispute.resolved_at)
                        if dispute.resolved_at else "Not applicable"),
           outcome=DISPUTE_OUTCOME.get(dispute.status, "Under review"),
           org_name=org.name if org else "your Organization",
           billing_url=billing_url())
    return kind


# ══ COM-008 — usage and entitlement lifecycle ═══════════════════════════════════════════

def approaching_supported() -> bool:
    """Whether an approaching-limit notice can be sent at all.

    False: no warning threshold is configured anywhere in this product, and choosing 80% or
    90% here would be exactly the invented policy COM-008 forbids.
    """
    return USAGE_WARNING_THRESHOLD_PERCENT is not None


def entitlement_recipients(db: Session, org: Organization, *,
                           metric: str) -> BillingRecipients:
    """Owner + billing admins, plus technical admins for infrastructure metrics.

    A seat limit is a commercial matter; storage and streaming hours are also operational,
    so a technical administrator is added for those. Ordinary members are never included.
    """
    owner = db.get(User, org.owner_user_id) if org.owner_user_id else None
    people = BillingRecipients(owner=owner, admins=_authorized(db, org.id, "accept"))
    if metric in ("Storage", "Streaming hours"):
        people.technical = [u for u in db.scalars(
            select(User).where(User.org_id == org.id, User.is_active.is_(True),
                               User.role == "host")).all()]
    return people


def _state_row(db: Session, org: Organization, metric: str) -> OrgEntitlementState:
    row = db.scalar(
        select(OrgEntitlementState).where(OrgEntitlementState.org_id == org.id,
                                          OrgEntitlementState.metric == metric))
    if row is None:
        row = OrgEntitlementState(org_id=org.id, metric=metric)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def evaluate_entitlements(db: Session, org: Organization) -> dict:
    """Fold the AUTHORITATIVE entitlement figures into the announced state.

    `services.org.entitlements()` is the only source of used/limit. Returns
    {metric: transition} where a transition is "limit_reached", "recovered" or None - so
    repeated failures at the same limit produce nothing.
    """
    from . import org as org_svc

    snapshot = org_svc.entitlements(db, org)
    transitions: dict = {}
    for item in snapshot.get("items") or []:
        metric = item.get("label")
        if metric not in ENTITLEMENT_METRICS:
            continue
        limit, used = item.get("limit"), item.get("used")
        if limit is None:
            continue
        row = _state_row(db, org, metric)
        at_limit = used is not None and used >= limit
        new_state = "limit_reached" if at_limit else "under_limit"
        row.last_used, row.last_limit = used, limit
        if row.state == new_state:
            db.commit()
            transitions[metric] = None
            continue
        # A fresh crossing opens a new notify cycle, so a customer who frees a seat and
        # fills it again is told again - while a repeated 409 at the same limit is not.
        if new_state == "limit_reached":
            row.limit_cycle += 1
            transitions[metric] = "limit_reached"
        else:
            transitions[metric] = "recovered"
        row.state = new_state
        row.changed_at = _now()
        db.commit()
    return transitions


def notify_limit_reached(db: Session, background, org: Organization, metric: str,
                         *, blocked_action: str | None = None) -> bool:
    """Announce a genuine limit crossing, once per crossing."""
    row = _state_row(db, org, metric)
    if row.state != "limit_reached":
        return False
    if row.limit_notified_cycle == row.limit_cycle:
        return False
    if not _may_send("COM-008", org):
        return False
    people = entitlement_recipients(db, org, metric=metric)
    if not people.addresses(include_technical=True):
        return False

    row.limit_notified_cycle = row.limit_cycle
    row.limit_notified_at = _now()
    db.commit()

    _queue(background, email_mod.send_entitlement_limit_email, people,
           include_technical=True,
           org_name=org.name,
           metric=metric,
           used=str(row.last_used),
           limit=str(row.last_limit),
           blocked_action=(blocked_action
                           or "Further use of this entitlement is blocked"),
           next_step=("Free up capacity, or contact your Zoiko Steam team to raise this "
                      "limit."),
           billing_url=billing_url())
    return True


def notify_entitlement_changed(db: Session, background, org: Organization) -> bool:
    """Announce a committed plan/limit change, showing a real before and after.

    Says nothing about price: a limit change is not evidence of a commercial price change,
    and asserting one from feature state would be a fabricated financial claim.
    """
    from . import org as org_svc

    snapshot = org_svc.entitlements(db, org)
    current = {i["label"]: i.get("limit") for i in (snapshot.get("items") or [])
               if i["label"] in ENTITLEMENT_METRICS}
    plan = snapshot.get("plan")
    # One row carries the announced plan snapshot for the whole organization.
    row = _state_row(db, org, "Members")
    previous = dict(row.announced_limits or {})
    previous_plan = row.announced_plan

    if previous_plan is None and not previous:
        # First observation establishes a baseline; there is no change to announce.
        row.announced_plan, row.announced_limits = plan, current
        db.commit()
        return False
    if previous_plan == plan and previous == current:
        return False

    changed = [(metric, previous.get(metric), current.get(metric))
               for metric in ENTITLEMENT_METRICS
               if previous.get(metric) != current.get(metric)]
    row.announced_plan, row.announced_limits = plan, current
    row.changed_notified_at = _now()
    db.commit()

    if not _may_send("COM-008", org):
        return False
    people = entitlement_recipients(db, org, metric="Members")
    if not people.addresses():
        return False

    _queue(background, email_mod.send_entitlement_changed_email, people,
           org_name=org.name,
           previous_plan=previous_plan or "Not recorded",
           current_plan=plan or "Not recorded",
           changes=[f"{m}: {p if p is not None else 'unlimited'} -> "
                    f"{c if c is not None else 'unlimited'}" for m, p, c in changed]
                   or ["Plan changed"],
           effective_at=email_mod.billing_date(_now()),
           billing_note=("This reflects your entitlements only. Any billing change is shown "
                         "on your invoices."),
           billing_url=billing_url())
    return True


# -- usage reports -----------------------------------------------------------------------

def build_usage_report(db: Session, org: Organization, *,
                       period: FinancialPeriod | None = None) -> UsageReport:
    """Create a durable usage artifact.

    The state is READ from `FinancialPeriod.status`, the platform's existing reconciliation
    authority: an open period yields PROVISIONAL, a closed one RECONCILED. Nothing here
    decides that a figure is final.
    """
    from . import org as org_svc

    snapshot = org_svc.entitlements(db, org)
    figures = {i["label"]: {"used": i.get("used"), "limit": i.get("limit"),
                            "unit": i.get("unit")}
               for i in (snapshot.get("items") or [])}
    state = "reconciled" if (period is not None and period.status == "closed") else "provisional"
    latest = db.scalar(
        select(UsageReport).where(UsageReport.org_id == org.id)
        .order_by(UsageReport.version.desc()))
    report = UsageReport(
        org_id=org.id, period_id=period.id if period else None,
        period_label=period.label if period else None,
        period_start=period.period_start if period else None,
        period_end=period.period_end if period else None,
        state=state, version=(latest.version + 1) if latest else 1, figures=figures)
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def correct_usage_report(db: Session, report: UsageReport, *, figures: dict,
                         reason: str | None = None) -> bool:
    """Correct a report without overwriting what was already communicated."""
    if report.ready_notified_at is None:
        # Nothing was communicated yet, so this is an amendment, not a correction.
        report.figures = figures
        db.commit()
        return False
    report.superseded_figures = dict(report.figures or {})
    report.figures = figures
    report.state = "corrected"
    report.correction_reason = (reason or "")[:200] or None
    report.corrected_at = _now()
    db.commit()
    return True


def _report_people(db: Session, org: Organization) -> BillingRecipients:
    return entitlement_recipients(db, org, metric="Storage")


def notify_usage_report(db: Session, background, org: Organization,
                        report: UsageReport) -> bool:
    if report.ready_notified_at is not None:
        return False
    if not _may_send("COM-008", org):
        return False
    people = _report_people(db, org)
    if not people.addresses(include_technical=True):
        return False
    report.ready_notified_at = _now()
    db.commit()

    _queue(background, email_mod.send_usage_report_email, people,
           include_technical=True,
           org_name=org.name,
           period=report.period_label or "Current period",
           # The label the customer sees is the stored state, never a guess.
           state=report.state.title(),
           state_note=(USAGE_PROVISIONAL_NOTE if report.state == "provisional"
                       else USAGE_RECONCILED_NOTE),
           figures=[f"{k}: {v.get('used')} of "
                    f"{v.get('limit') if v.get('limit') is not None else 'unlimited'} "
                    f"{v.get('unit') or ''}".strip()
                    for k, v in (report.figures or {}).items()],
           version=report.version,
           billing_url=billing_url())
    return True


USAGE_PROVISIONAL_NOTE = ("These figures are provisional. They are not final billed usage "
                          "and may change when the period is reconciled.")
USAGE_RECONCILED_NOTE = "These figures are reconciled and final for the period."


def notify_usage_corrected(db: Session, background, org: Organization,
                           report: UsageReport) -> bool:
    if report.state != "corrected" or report.corrected_notified_at is not None:
        return False
    if not _may_send("COM-008", org):
        return False
    people = _report_people(db, org)
    if not people.addresses(include_technical=True):
        return False
    report.corrected_notified_at = _now()
    db.commit()

    previous = report.superseded_figures or {}
    current = report.figures or {}
    _queue(background, email_mod.send_usage_corrected_email, people,
           include_technical=True,
           org_name=org.name,
           period=report.period_label or "Current period",
           changes=[f"{k}: {(previous.get(k) or {}).get('used')} -> "
                    f"{(current.get(k) or {}).get('used')}"
                    for k in current if (previous.get(k) or {}).get("used")
                    != (current.get(k) or {}).get("used")] or ["Figures restated"],
           reason=report.correction_reason or "Reconciliation adjustment",
           corrected_at=email_mod.billing_date(report.corrected_at),
           billing_url=billing_url())
    return True
