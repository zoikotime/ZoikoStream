"""COM-006 -> COM-008 - billing, payments and entitlements (ZST-EC-001).

The assertions the spec calls out as most important each have a dedicated test:

    COM-006  invoice issuance produces a real notice   test_issued_invoice_is_announced
    COM-006  processor references are masked           test_processor_references_are_masked
    COM-007  "no charge" only when proven              test_no_charge_claim_requires_proof
    COM-007  overdue/resolved from real state          test_overdue_only_from_real_state
    COM-008  limits never silently block               test_first_limit_crossing_notifies_once
    COM-008  provisional vs reconciled                 test_provisional_and_reconciled_labels
    ALL      no invented policy                        test_no_threshold_or_expiry_invented

Run with `python test_commerce_comms.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import app.email as email_mod
from app.db import SessionLocal
from app.models import (
    CHARGE_MAY_BE_AUTHORIZED,
    CHARGE_NO_CHARGE,
    CHARGE_UNKNOWN,
    PAYMENT_METHOD_STORAGE_SUPPORTED,
    USAGE_WARNING_THRESHOLD_PERCENT,
    CatalogVersion,
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
    Plan,
    RefundCredit,
    Subscription,
    UsageReport,
    User,
)
from app.security import hash_password
from app.services import commerce_comms as cc

PASSWORD = "correct-horse-battery"

INVOICE = email_mod.COM_006_INVOICE_SUBJECT
RECEIVED = email_mod.COM_006_RECEIVED_SUBJECT
REFUND = email_mod.COM_006_REFUND_SUBJECT
CREDIT = email_mod.COM_006_CREDIT_SUBJECT
FAILED = email_mod.COM_007_FAILED_SUBJECT
OVERDUE = email_mod.COM_007_OVERDUE_SUBJECT
RESOLVED = email_mod.COM_007_RESOLVED_SUBJECT
DISPUTE_OPEN = email_mod.COM_007_DISPUTE_OPENED_SUBJECT
CHANGED = email_mod.COM_008_CHANGED_SUBJECT
REPORT = email_mod.COM_008_REPORT_SUBJECT
CORRECTED = email_mod.COM_008_CORRECTED_SUBJECT

def _provider_ref() -> str:
    """A unique processor reference whose last four characters are stable.

    `payments` carries a unique constraint on provider_payment_ref, so a fixture cannot
    reuse one value; the tail is fixed so the masking assertions still check a known suffix.
    """
    return f"pi_3{uuid.uuid4().hex[:20]}91C4"


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "payload": json or {}})
        if self.fail:
            import httpx
            raise httpx.ConnectError("simulated Resend outage")
        return _Resp()

    @property
    def subjects(self):
        return [c["payload"]["subject"] for c in self.calls]

    def of(self, subject):
        hits = [c["payload"] for c in self.calls if c["payload"]["subject"] == subject]
        assert hits, f"no message with subject {subject!r}; got {self.subjects}"
        return hits[-1]

    def to(self, subject):
        return sorted(c["payload"]["to"][0].lower()
                      for c in self.calls if c["payload"]["subject"] == subject)

    def count(self, subject):
        return sum(1 for s in self.subjects if s == subject)

    def blob(self):
        return "".join(c["payload"].get("text", "") + c["payload"].get("html", "")
                       for c in self.calls)


_LEAKS: list[str] = []


def _deny(url, headers=None, json=None, timeout=None):
    _LEAKS.append((json or {}).get("subject", "?"))
    raise RuntimeError("outbound email attempted outside a capture context")


email_mod.httpx.post = _deny


def _capture(fail=False):
    cap = Captured(fail=fail)
    return cap, patch.object(email_mod.httpx, "post", cap)


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def _new_email(tag="com"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    def __init__(self, max_users=3):
        db = SessionLocal()
        try:
            plan = Plan(name=f"Plan {uuid.uuid4().hex[:6]}",
                        slug=f"plan-{uuid.uuid4().hex[:8]}",
                        max_users=max_users, max_storage_gb=10, max_streaming_hours=20,
                        currency="GBP")
            db.add(plan)
            db.flush()
            self.plan_id, self.plan_name = plan.id, plan.name

            org = Organization(name=f"Com Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "org_admin", self.owner_email, "Org Owner")
            self.member_email = _new_email("member")
            self.member_id = self._u(db, "viewer", self.member_email, "Member")
            self.host_email = _new_email("tech")
            self.host_id = self._u(db, "host", self.host_email, "Technical Admin")
            org.owner_user_id = self.owner_id
            self.billing_email = _new_email("billing")

            db.add(Subscription(org_id=org.id, plan_id=plan.id, status="active"))
            account = CommercialAccount(org_id=org.id,
                                        billing_contact_email=self.billing_email,
                                        billing_contact_name="Billing Desk")
            db.add(account)
            db.flush()
            self.account_id = account.id

            ev = Event(org_id=org.id, created_by=self.owner_id, title="Com Summit",
                       status="scheduled", timezone="Europe/London",
                       start_time=_now() + timedelta(days=10))
            db.add(ev)
            db.flush()
            self.event_id = ev.id

            catalog = db.query(CatalogVersion).first()
            if catalog is None:
                catalog = CatalogVersion(vertical="events", version_label="v1",
                                         status="published")
                db.add(catalog)
                db.flush()
            order = EventOrder(event_id=ev.id, commercial_account_id=account.id,
                               catalog_version_id=catalog.id, currency="GBP",
                               order_version=1, status="accepted",
                               idempotency_key=uuid.uuid4().hex)
            db.add(order)
            db.flush()
            self.order_id = order.id
            self.account_id = account.id
            db.commit()
        finally:
            db.close()

    def _u(self, db, role, email, name):
        user = User(org_id=self.org_id, full_name=name, role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.flush()
        return user.id

    def order(self):
        """A fresh accepted order.

        `uq_invoice_active_per_order` (models/commercial.py) is a PARTIAL unique index: at
        most one non-void invoice per order, so that voiding and re-issuing stays possible
        while two live invoices for one order do not. A fixture that hung every invoice off
        one order was only ever passing because that rule did not exist yet — so each
        invoice now gets its own order, which is also what production does.
        """
        db = SessionLocal()
        try:
            catalog = db.query(CatalogVersion).first()
            order = EventOrder(event_id=self.event_id, commercial_account_id=self.account_id,
                               catalog_version_id=catalog.id, currency="GBP",
                               order_version=1, status="accepted",
                               idempotency_key=uuid.uuid4().hex)
            db.add(order)
            db.commit()
            return order.id
        finally:
            db.close()

    def invoice(self, *, state="issued", due_days=14, total="500.00", order_id=None):
        db = SessionLocal()
        try:
            inv = Invoice(event_order_id=order_id or self.order(),
                          seller_legal_entity_id="zoiko_uk",
                          number=f"ZST-LE-INV-{uuid.uuid4().hex[:6].upper()}",
                          currency="GBP", subtotal=Decimal(total),
                          tax_amount=Decimal("0.00"), total_amount=Decimal(total),
                          issue_date=_now(),
                          due_date=_now() + timedelta(days=due_days), state=state)
            db.add(inv)
            db.commit()
            return inv.id
        finally:
            db.close()

    def payment(self, *, state="paid", invoice_id=None, amount="500.00",
                authorized=False, captured=True, failure=None):
        db = SessionLocal()
        try:
            ref = _provider_ref()
            self.last_provider_ref = ref
            p = Payment(event_order_id=self.order_id, invoice_id=invoice_id,
                        provider="stripe", provider_payment_ref=ref,
                        method_type="card", amount=Decimal(amount), currency="GBP",
                        state=state, idempotency_key=uuid.uuid4().hex,
                        failure_reason=failure)
            if authorized:
                p.authorized_at = _now()
            if captured and state == "paid":
                p.captured_at = _now()
            db.add(p)
            db.commit()
            return p.id
        finally:
            db.close()

    def add_members(self, n):
        db = SessionLocal()
        try:
            for _ in range(n):
                self._u(db, "viewer", _new_email("extra"), "Extra")
            db.commit()
        finally:
            db.close()

    def org(self, db):
        return db.get(Organization, self.org_id)

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(CommerceNotice).filter(CommerceNotice.org_id == self.org_id).delete()
            db.query(OrgEntitlementState).filter(
                OrgEntitlementState.org_id == self.org_id).delete()
            db.query(UsageReport).filter(UsageReport.org_id == self.org_id).delete()
            for p in db.query(Payment).filter(Payment.event_order_id == self.order_id).all():
                db.query(PaymentDispute).filter(PaymentDispute.payment_id == p.id).delete()
            db.query(RefundCredit).filter(
                RefundCredit.event_order_id == self.order_id).delete()
            db.query(Payment).filter(Payment.event_order_id == self.order_id).delete()
            db.query(Invoice).filter(Invoice.event_order_id == self.order_id).delete()
            db.commit()
            db.query(EventOrder).filter(EventOrder.id == self.order_id).delete()
            db.commit()
            ev = db.get(Event, self.event_id)
            if ev is not None:
                db.delete(ev)
            db.commit()
            db.query(Subscription).filter(Subscription.org_id == self.org_id).delete()
            db.query(CommercialAccount).filter(
                CommercialAccount.id == self.account_id).delete()
            org = db.get(Organization, self.org_id)
            if org is not None:
                org.owner_user_id = None
            db.commit()
            db.query(User).filter(User.org_id == self.org_id).delete()
            db.query(Organization).filter(Organization.id == self.org_id).delete()
            db.query(Plan).filter(Plan.id == self.plan_id).delete()
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


RESULTS = []


def run(fn, **kw):
    world = World(**kw)
    try:
        fn(world)
        RESULTS.append((fn.__name__, None))
        print(f"ok  {fn.__name__}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((fn.__name__, exc))
        print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    finally:
        world.cleanup()


# ══ COM-006 ═════════════════════════════════════════════════════════════════════════════

def test_issued_invoice_is_announced(w):
    """1, 2, 3, 4, 5, 6 - and a draft invoice announces nothing."""
    draft = w.invoice(state="draft")
    db = SessionLocal()
    try:
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_invoice_available(db, _Bg(), db.get(Invoice, draft)) is False
        assert cap.calls == [], "a draft invoice must not be announced"
    finally:
        db.close()

    iid = w.invoice(state="issued", total="1250.00")
    db = SessionLocal()
    try:
        inv = db.get(Invoice, iid)
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_invoice_available(db, _Bg(), inv) is True
        payload = cap.of(INVOICE)
        got = cap.to(INVOICE)
        # 3 - billing contact, not the event owner as a substitute.
        assert w.billing_email.lower() in got, f"billing contact missing: {got}"
        text = payload["text"]
        assert inv.number in text, "the customer-facing invoice number is shown in full"
        assert "GBP 1250.00" in text, text
        assert "Due date" in text and "Issue date" in text
        # 6 - customer billing surface, not Super Admin.
        assert "/organization/billing" in text
        for bad in ("/admin", "super_admin"):
            assert bad not in text
    finally:
        db.close()


def test_processor_references_are_masked(w):
    """7, 8 - the four identifier classes are treated differently."""
    iid = w.invoice()
    pid = w.payment(state="paid", invoice_id=iid)
    db = SessionLocal()
    try:
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_payment_received(db, _Bg(), db.get(Payment, pid)) is True
        blob = cap.blob()
        # The processor reference must never appear in full.
        assert w.last_provider_ref not in blob, "the full provider reference leaked"
        assert "••••91C4" in blob, blob[-400:]
        # And no raw internal UUID either.
        assert str(pid) not in blob and str(w.order_id) not in blob
        # But the business reference IS shown.
        assert db.get(Invoice, iid).number in blob
        assert cc.mask_reference(w.last_provider_ref) == "••••91C4"
        assert cc.mask_reference(None) == "Not available"
        assert cc.mask_reference("ab") == "••••ab"
    finally:
        db.close()


def test_pending_payment_is_never_reported_as_received(w):
    """9, 10."""
    iid = w.invoice()
    db = SessionLocal()
    try:
        for state in ("pending", "requires_action", "partially_paid", "failed"):
            pid = w.payment(state=state, invoice_id=iid, captured=False)
            cap, ctx = _capture()
            with ctx:
                assert cc.notify_payment_received(db, _Bg(), db.get(Payment, pid)) is False, (
                    f"'{state}' must not be reported as received")
            assert cap.calls == []
    finally:
        db.close()


def test_refund_and_credit_note_are_not_conflated(w):
    """11, 12, 13 - RefundCredit.type is a real domain, so both variants are real."""
    db = SessionLocal()
    try:
        refund = RefundCredit(event_order_id=w.order_id, type="refund",
                              amount=Decimal("100.00"), reason_code="service_failure",
                              status="executed", provider_ref="re_3PqAbC91C4")
        credit = RefundCredit(event_order_id=w.order_id, type="credit",
                              amount=Decimal("50.00"), reason_code="goodwill",
                              status="approved")
        db.add_all([refund, credit])
        db.commit()

        cap, ctx = _capture()
        with ctx:
            assert cc.notify_refund_or_credit(db, _Bg(), refund) == "refund_issued"
            assert cc.notify_refund_or_credit(db, _Bg(), credit) == "credit_note"
        refund_text = cap.of(REFUND)["text"]
        credit_text = cap.of(CREDIT)["text"]
        assert "returned to your original payment method" in refund_text
        # The credit note must NOT promise money back.
        assert "is not a refund to your payment method" in credit_text
        assert "returned to your original payment method" not in credit_text
        assert "••••91C4" in refund_text and "re_3PqAbC91C4" not in refund_text
    finally:
        db.close()


def test_com_006_html_text_dedup_and_provider_failure(w):
    """14, 15, 16."""
    iid = w.invoice()
    db = SessionLocal()
    try:
        inv = db.get(Invoice, iid)
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_invoice_available(db, _Bg(), inv) is True
            n = len(cap.calls)
            assert cc.notify_invoice_available(db, _Bg(), inv) is False   # 15 dedup
        assert len(cap.calls) == n
        payload = cap.of(INVOICE)
        assert payload["html"].strip() and payload["text"].strip()

        # 16 - a provider outage must not undo financial state.
        iid2 = w.invoice()
        inv2 = db.get(Invoice, iid2)
        capf, ctxf = _capture(fail=True)
        with ctxf:
            cc.notify_invoice_available(db, _Bg(), inv2)
        db.expire_all()
        assert db.get(Invoice, iid2).state == "issued"
    finally:
        db.close()


# ══ COM-007 ═════════════════════════════════════════════════════════════════════════════

def test_no_charge_claim_requires_proof(w):
    """1, 2, 3, 4 - the correction to the unconditional claim."""
    db = SessionLocal()
    try:
        # Proven no charge: failed with no authorization.
        clean = db.get(Payment, w.payment(state="failed", captured=False,
                                          failure="card_declined"))
        assert cc.charge_position(clean) == CHARGE_NO_CHARGE
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_payment_failed(db, _Bg(), clean) is True
        text = cap.of(FAILED)["text"]
        assert "no charge was recorded" in text.lower(), text

        # Authorized then failed: a hold may exist, so the strong claim is forbidden.
        held = db.get(Payment, w.payment(state="failed", authorized=True, captured=False))
        assert cc.charge_position(held) == CHARGE_MAY_BE_AUTHORIZED
        cap2, ctx2 = _capture()
        with ctx2:
            assert cc.notify_payment_failed(db, _Bg(), held) is True
        text2 = cap2.of(FAILED)["text"]
        assert "no charge" not in text2.lower(), (
            "an authorized payment must never claim no charge exists")
        assert "may temporarily show an authorization" in text2

        # Ambiguous state: safe wording only.
        unknown = db.get(Payment, w.payment(state="failed", captured=False))
        unknown.state = "unmatched"
        db.commit()
        assert cc.charge_position(unknown) == CHARGE_UNKNOWN
    finally:
        db.close()


def test_failure_category_hides_processor_detail(w):
    """5, 12 - a raw decline code is never mailed."""
    db = SessionLocal()
    try:
        p = db.get(Payment, w.payment(
            state="failed", captured=False,
            failure="card_declined: do_not_honor (issuer code 05)"))
        cap, ctx = _capture()
        with ctx:
            cc.notify_payment_failed(db, _Bg(), p)
        blob = cap.blob()
        assert "The payment was declined" in blob
        for raw in ("do_not_honor", "issuer code 05", "card_declined"):
            assert raw not in blob, f"raw decline detail leaked: {raw}"
    finally:
        db.close()


def test_method_expiring_is_not_fabricated(w):
    """6 - there is no stored payment method, so the variant does not exist."""
    assert PAYMENT_METHOD_STORAGE_SUPPORTED is False
    assert not hasattr(email_mod, "send_payment_method_expiring_email"), (
        "a method-expiring template appeared without any stored expiry data")
    for column in Payment.__table__.columns:
        for field in ("exp_month", "exp_year", "last4", "card_"):
            assert field not in column.name, (
                f"payment-method data appeared ({column.name}); COM-007 must be revisited")


def test_overdue_only_from_real_state(w):
    """7, 8 - and paid/void/disputed invoices are excluded."""
    db = SessionLocal()
    try:
        future = db.get(Invoice, w.invoice(due_days=14))
        owed, why = cc.overdue_position(db, future)
        assert owed is False and "not yet due" in why

        overdue = db.get(Invoice, w.invoice(due_days=-5))
        owed, why = cc.overdue_position(db, overdue)
        assert owed is True, why
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_invoice_overdue(db, _Bg(), overdue) is True
        assert w.billing_email.lower() in cap.to(OVERDUE)

        # 8 - a paid invoice never receives an overdue reminder.
        paid = db.get(Invoice, w.invoice(state="paid", due_days=-5))
        owed, why = cc.overdue_position(db, paid)
        assert owed is False and "paid" in why
        for state in ("void", "draft"):
            inv = db.get(Invoice, w.invoice(state=state, due_days=-5))
            owed, why = cc.overdue_position(db, inv)
            assert owed is False, f"{state}: {why}"
    finally:
        db.close()


def test_open_dispute_suspends_collection(w):
    """8, 11 - and the dispute itself notifies."""
    db = SessionLocal()
    try:
        iid = w.invoice(due_days=-10)
        pid = w.payment(state="disputed", invoice_id=iid)
        dispute = PaymentDispute(payment_id=pid, event_order_id=w.order_id,
                                 provider="stripe",
                                 provider_dispute_ref="dp_3PqZZZ91C4",
                                 reason_code="fraudulent", amount=Decimal("500.00"),
                                 currency="GBP", reserve_amount=Decimal("0.00"),
                                 status="opened")
        db.add(dispute)
        db.commit()
        owed, why = cc.overdue_position(db, db.get(Invoice, iid))
        assert owed is False and "dispute" in why, why

        cap, ctx = _capture()
        with ctx:
            assert cc.notify_dispute(db, _Bg(), dispute) == "dispute_opened"
        blob = cap.blob()
        assert "••••91C4" in blob and "dp_3PqZZZ91C4" not in blob
        assert "fraudulent" not in blob.lower(), "provider reason code must not be mailed"
    finally:
        db.close()


def test_resolved_requires_actual_resolution(w):
    """9, 10 - a retry starting is never a resolution."""
    db = SessionLocal()
    try:
        iid = w.invoice()
        failed = db.get(Payment, w.payment(state="failed", invoice_id=iid, captured=False))
        cap, ctx = _capture()
        with ctx:
            cc.notify_payment_failed(db, _Bg(), failed)
            # A fresh attempt in flight is not a resolution.
            retry = db.get(Payment, w.payment(state="pending", invoice_id=iid,
                                              captured=False))
            assert cc.notify_billing_resolved(db, _Bg(), payment=retry) is False

            retry.state, retry.captured_at = "paid", _now()
            db.commit()
            assert cc.notify_billing_resolved(db, _Bg(), payment=retry) is True
        assert cap.count(RESOLVED) >= 1
        assert "No further action is needed" in cap.of(RESOLVED)["text"]
    finally:
        db.close()


def test_resolved_needs_a_prior_problem(w):
    """9 - nothing to resolve if nothing was ever reported."""
    db = SessionLocal()
    try:
        clean = db.get(Payment, w.payment(state="paid"))
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_billing_resolved(db, _Bg(), payment=clean) is False
        assert cap.calls == []
    finally:
        db.close()


def test_com_007_dedup_and_html_text(w):
    """14, 15."""
    db = SessionLocal()
    try:
        p = db.get(Payment, w.payment(state="failed", captured=False))
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_payment_failed(db, _Bg(), p) is True
            n = len(cap.calls)
            assert cc.notify_payment_failed(db, _Bg(), p) is False
        assert len(cap.calls) == n
        payload = cap.of(FAILED)
        assert payload["html"].strip() and payload["text"].strip()
    finally:
        db.close()


# ══ COM-008 ═════════════════════════════════════════════════════════════════════════════

def test_entitlement_service_stays_authoritative(w):
    """1 - no seat or usage rule is duplicated in the comms layer."""
    import inspect

    source = inspect.getsource(cc)
    assert "org_svc.entitlements" in source, "the entitlement service must be called"
    for rule in ("max_users", "max_storage_gb", "max_streaming_hours", "_streaming_hours"):
        assert rule not in source, f"entitlement rule {rule!r} duplicated in commerce_comms"


def test_first_limit_crossing_notifies_once(w):
    """2, 3, 4, 5 - the seat-limit case."""
    # World starts with 3 users and max_users=3, so it is already at the limit.
    db = SessionLocal()
    try:
        org = w.org(db)
        transitions = cc.evaluate_entitlements(db, org)
        assert transitions.get("Members") == "limit_reached", transitions
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_limit_reached(db, _Bg(), org, "Members",
                                           blocked_action="Invitations blocked") is True
            n = len(cap.calls)
            # 4 - a repeated 409 at the same limit must not mail again.
            for _ in range(3):
                cc.evaluate_entitlements(db, org)
                assert cc.notify_limit_reached(db, _Bg(), org, "Members") is False
        assert len(cap.calls) == n, "repeated blocked attempts must not spam"
        subject = email_mod.COM_008_LIMIT_SUBJECT.format(metric="members")
        got = cap.to(subject)
        # 9 - owner and billing admins, not ordinary members.
        assert w.owner_email.lower() in got
        assert w.member_email.lower() not in got, "ordinary members must not be mailed"
    finally:
        db.close()


def test_dropping_below_and_returning_notifies_again(w):
    """5 - a genuine new crossing is a new communication event."""
    db = SessionLocal()
    try:
        org = w.org(db)
        cc.evaluate_entitlements(db, org)
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_limit_reached(db, _Bg(), org, "Members") is True
        # Drop below by raising the ceiling, then return to it.
        plan = db.get(Plan, w.plan_id)
        plan.max_users = 10
        db.commit()
        assert cc.evaluate_entitlements(db, org).get("Members") == "recovered"
        plan.max_users = 3
        db.commit()
        assert cc.evaluate_entitlements(db, org).get("Members") == "limit_reached"
        cap2, ctx2 = _capture()
        with ctx2:
            assert cc.notify_limit_reached(db, _Bg(), org, "Members") is True, (
                "a new crossing may notify again")
        assert cap2.count(email_mod.COM_008_LIMIT_SUBJECT.format(metric="members")) >= 1
    finally:
        db.close()


def test_no_threshold_or_expiry_invented(w):
    """6, 7 - no 80/90 anywhere, and no approaching-limit template."""
    assert USAGE_WARNING_THRESHOLD_PERCENT is None
    assert cc.approaching_supported() is False
    assert not hasattr(email_mod, "send_entitlement_approaching_email"), (
        "an approaching-limit template appeared without a configured threshold")
    import inspect
    source = inspect.getsource(cc)
    for invented in ("0.8", "0.9", "80", "90"):
        assert f"percent > {invented}" not in source
        assert f">= {invented}" not in source


def test_entitlement_change_shows_before_and_after(w):
    """8 - and makes no price claim."""
    db = SessionLocal()
    try:
        org = w.org(db)
        # First observation is a baseline, not a change.
        assert cc.notify_entitlement_changed(db, _Bg(), org) is False
        plan = db.get(Plan, w.plan_id)
        plan.max_users = 30
        db.commit()
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_entitlement_changed(db, _Bg(), org) is True
        text = cap.of(CHANGED)["text"]
        assert "Members: 3 -> 30" in text, text
        assert "entitlements only" in text
        for price_claim in ("your price", "you will be charged", "new monthly cost"):
            assert price_claim not in text.lower(), price_claim
    finally:
        db.close()


def test_provisional_and_reconciled_labels(w):
    """10, 11, 12 - the state is read from FinancialPeriod, not decided here."""
    db = SessionLocal()
    try:
        org = w.org(db)
        # No period: provisional.
        report = cc.build_usage_report(db, org, period=None)
        assert report.state == "provisional"
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_usage_report(db, _Bg(), org, report) is True
        text = cap.of(REPORT)["text"]
        assert "Provisional" in text
        assert "not final billed usage" in text

        open_period = FinancialPeriod(label="2027-01", period_start=_now(),
                                      period_end=_now() + timedelta(days=30),
                                      status="open")
        closed = FinancialPeriod(label="2026-12", period_start=_now() - timedelta(days=60),
                                 period_end=_now() - timedelta(days=30), status="closed")
        db.add_all([open_period, closed])
        db.commit()
        assert cc.build_usage_report(db, org, period=open_period).state == "provisional"
        rec = cc.build_usage_report(db, org, period=closed)
        assert rec.state == "reconciled", "a closed period is reconciled"
        cap2, ctx2 = _capture()
        with ctx2:
            cc.notify_usage_report(db, _Bg(), org, rec)
        text2 = cap2.of(REPORT)["text"]
        assert "Reconciled" in text2 and "reconciled and final" in text2
        db.delete(open_period)
        db.delete(closed)
        db.commit()
    finally:
        db.close()


def test_correction_shows_before_and_after(w):
    """13 - and never silently overwrites a communicated figure."""
    db = SessionLocal()
    try:
        org = w.org(db)
        report = cc.build_usage_report(db, org, period=None)
        # Before anything was communicated, a change is an amendment, not a correction.
        assert cc.correct_usage_report(db, report, figures={"Members": {"used": 4}}) is False
        cap, ctx = _capture()
        with ctx:
            cc.notify_usage_report(db, _Bg(), org, report)
        assert cc.correct_usage_report(
            db, report, figures={"Members": {"used": 9, "limit": 3, "unit": "seats"}},
            reason="Reconciliation adjustment") is True
        assert report.superseded_figures is not None, (
            "the previously communicated figures must be preserved")
        cap2, ctx2 = _capture()
        with ctx2:
            assert cc.notify_usage_corrected(db, _Bg(), org, report) is True
        text = cap2.of(CORRECTED)["text"]
        assert "4 -> 9" in text, text
    finally:
        db.close()


def test_com_008_no_leakage_html_text_dedup(w):
    """14, 15, 16."""
    db = SessionLocal()
    try:
        org = w.org(db)
        report = cc.build_usage_report(db, org, period=None)
        cap, ctx = _capture()
        with ctx:
            assert cc.notify_usage_report(db, _Bg(), org, report) is True
            n = len(cap.calls)
            assert cc.notify_usage_report(db, _Bg(), org, report) is False
        assert len(cap.calls) == n
        payload = cap.of(REPORT)
        assert payload["html"].strip() and payload["text"].strip()
        blob = cap.blob()
        for secret in ("api_key", "sk_live", "secret", str(w.org_id), "/admin"):
            assert secret not in blob, f"{secret} must not appear"
    finally:
        db.close()


# ══ cross-cutting ═══════════════════════════════════════════════════════════════════════

def test_families_classified_and_no_secret_parameters(w):
    from app.services import notifications
    import inspect

    for family in ("COM-006", "COM-007", "COM-008"):
        assert family in notifications.FAMILY_CLASS, family
    for mandatory in ("COM-006", "COM-007"):
        assert notifications.is_mandatory(mandatory) is True, mandatory

    senders = ["send_invoice_available_email", "send_payment_received_email",
               "send_refund_issued_email", "send_credit_note_email",
               "send_payment_problem_email", "send_dispute_email",
               "send_entitlement_limit_email", "send_entitlement_changed_email",
               "send_usage_report_email", "send_usage_corrected_email"]
    banned = ("card_number", "cvv", "token", "secret", "api_key", "password")
    for name in senders:
        for param in inspect.signature(getattr(email_mod, name)).parameters:
            assert not any(b in param.lower() for b in banned), f"{name}({param})"


def test_legacy_failed_email_no_longer_claims_no_charge(w):
    """The specific INCORRECT finding: the unconditional sentence is gone."""
    import inspect

    source = inspect.getsource(email_mod.send_payment_failed_email)
    body = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    assert "No charge was made" not in body, (
        "the unconditional no-charge claim is still present in the legacy sender")
    cap, ctx = _capture()
    with ctx:
        email_mod.send_payment_failed_email(
            w.billing_email, "Billing", "Com Summit", "500.00", "GBP", None,
            "https://example.com/o")
    assert "no charge" not in cap.blob().lower()


TESTS = [
    test_issued_invoice_is_announced,
    test_processor_references_are_masked,
    test_pending_payment_is_never_reported_as_received,
    test_refund_and_credit_note_are_not_conflated,
    test_com_006_html_text_dedup_and_provider_failure,
    test_no_charge_claim_requires_proof,
    test_failure_category_hides_processor_detail,
    test_method_expiring_is_not_fabricated,
    test_overdue_only_from_real_state,
    test_open_dispute_suspends_collection,
    test_resolved_requires_actual_resolution,
    test_resolved_needs_a_prior_problem,
    test_com_007_dedup_and_html_text,
    test_entitlement_service_stays_authoritative,
    test_first_limit_crossing_notifies_once,
    test_dropping_below_and_returning_notifies_again,
    test_no_threshold_or_expiry_invented,
    test_entitlement_change_shows_before_and_after,
    test_provisional_and_reconciled_labels,
    test_correction_shows_before_and_after,
    test_com_008_no_leakage_html_text_dedup,
    test_families_classified_and_no_secret_parameters,
    test_legacy_failed_email_no_longer_claims_no_charge,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
