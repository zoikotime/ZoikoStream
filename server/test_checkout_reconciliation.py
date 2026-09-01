"""Phase 4F — hosted Checkout reconciliation.

Covers the P0 found by Phase 4E real-sandbox verification: Stripe reports
`payment_intent: null` on a freshly created Checkout Session, so requiring a payment reference
at creation rejected every real checkout attempt.

The reference is instead adopted later, from a signature-verified provider event, using the
Checkout Session as the correlation key. These tests assert the real Stripe contract — a
session with no payment reference — and that adoption can never overwrite, never guess, and
never move money on its own.
"""
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud import commercial as crud
from app.db import engine
from app.models import (
    CatalogLine, CatalogVersion, CommercialAccount, Event, EventOrder, Organization,
    Payment, ProviderEvent, User,
)
from app.services import payments as pay
from app.services import payments_stripe_events as stripe_events
from app.services.payments_stripe import StripePaymentProvider

TEST_KEY = "sk_test_placeholder_not_a_real_key"
SUCCESS_URL = "https://app.test/organization/events/e1?tab=commercial&checkout=success"
CANCEL_URL = "https://app.test/organization/events/e1?tab=commercial&checkout=cancelled"
PROVIDER = "stripe"


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="DATABASE_URL not reachable")


# ── event payload builders ─────────────────────────────────────────────────────────────────

def session_event(session_id, *, intent=None, event_type="checkout.session.completed",
                  amount_total=144000, currency="usd", event_id=None, idem_key=None):
    """A Stripe checkout.session.* event. `intent=None` models a session Stripe has not yet
    attached a PaymentIntent to."""
    obj = {"id": session_id, "object": "checkout.session", "payment_intent": intent,
           "amount_total": amount_total, "currency": currency}
    if idem_key:
        obj["metadata"] = {"zoiko_idempotency_key": idem_key}
    return {"id": event_id or f"evt_{uuid.uuid4().hex[:16]}", "type": event_type,
            "created": 1767225600, "data": {"object": obj}}


def intent_event(intent_id, *, event_type="payment_intent.succeeded", amount=144000,
                 currency="usd", event_id=None, idem_key=None):
    obj = {"id": intent_id, "object": "payment_intent", "amount": amount, "currency": currency}
    if idem_key:
        obj["metadata"] = {"zoiko_idempotency_key": idem_key}
    return {"id": event_id or f"evt_{uuid.uuid4().hex[:16]}", "type": event_type,
            "created": 1767225600, "data": {"object": obj}}


def ingest(db, event, *, verified=True):
    """Route a translated event exactly as the webhook endpoint does."""
    t = stripe_events.translate(event)
    raw = json.dumps(event).encode()
    shared = dict(provider=PROVIDER, provider_event_id=t.provider_event_id, raw_body=raw,
                  signature_verified=verified, provider_payment_ref=t.provider_payment_ref,
                  payload=t.payload, occurred_at=t.occurred_at, correlation_id=None)
    if t.is_financial:
        return crud.ingest_provider_event(
            db, event_type=t.generic_event_type, amount=t.amount, currency=t.currency,
            idempotency_key_hint=t.idempotency_key_hint, **shared)
    if t.requires_reconciliation:
        return crud.reconcile_checkout_session(
            db, event_type=t.stripe_event_type, checkout_session_ref=t.checkout_session_ref,
            amount=t.amount, currency=t.currency,
            reason=t.evidence_reason or "x", follow_up_required=t.follow_up_required, **shared)
    return crud.record_provider_event_evidence(
        db, event_type=t.stripe_event_type, reason=t.evidence_reason or "x",
        follow_up_required=t.follow_up_required, **shared)


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. THE REGRESSION — a session with no PaymentIntent must be accepted
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def provider():
    with patch("app.services.payments_stripe.stripe.StripeClient") as client_cls:
        client = MagicMock()
        client_cls.return_value = client
        yield StripePaymentProvider(api_key=TEST_KEY), client


def test_adapter_accepts_a_session_with_no_payment_intent(provider):
    """THE P0. Real Stripe returns payment_intent=None here; this must not raise."""
    p, client = provider
    client.checkout.sessions.create.return_value = SimpleNamespace(
        id="cs_test_real", url="https://checkout.stripe.com/c/pay/cs_test_real",
        payment_intent=None, expires_at=1767225600)
    r = p.create_checkout_session(
        amount=Decimal("1440.00"), currency="USD", idempotency_key="k1",
        success_url=SUCCESS_URL, cancel_url=CANCEL_URL, description="d",
        metadata={"zoiko_event_order_id": "o1"})
    assert r.checkout_session_ref == "cs_test_real"
    assert r.provider_payment_ref is None
    assert r.state == "requires_action"


def test_adapter_still_flattens_a_reference_when_stripe_does_supply_one(provider):
    p, client = provider
    client.checkout.sessions.create.return_value = SimpleNamespace(
        id="cs_x", url="https://checkout.stripe.com/c/pay/cs_x",
        payment_intent="pi_supplied", expires_at=None)
    r = p.create_checkout_session(amount=Decimal("10.00"), currency="USD", idempotency_key="k",
                                  success_url=SUCCESS_URL, cancel_url=CANCEL_URL,
                                  description="d", metadata={})
    assert r.provider_payment_ref == "pi_supplied"


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. TRANSLATION — checkout events correlate, they do not instruct
# ══════════════════════════════════════════════════════════════════════════════════════

def test_checkout_completed_is_reconcilable_and_not_financial():
    t = stripe_events.translate(session_event("cs_1", intent="pi_1"))
    assert t.requires_reconciliation is True
    assert t.is_financial is False, "a completed session must not drive the state machine"
    assert t.generic_event_type is None
    assert t.checkout_session_ref == "cs_1"
    assert t.provider_payment_ref == "pi_1"


def test_checkout_session_amount_is_read_from_amount_total():
    t = stripe_events.translate(session_event("cs_1", intent="pi_1", amount_total=144000))
    assert t.amount == Decimal("1440.00") and t.currency == "USD"


@pytest.mark.parametrize("etype", ["checkout.session.expired",
                                   "checkout.session.async_payment_failed"])
def test_terminal_session_events_are_evidence_only(etype):
    t = stripe_events.translate(session_event("cs_1", event_type=etype))
    assert t.requires_reconciliation is False and t.is_financial is False


def test_async_payment_succeeded_is_reconcilable():
    t = stripe_events.translate(
        session_event("cs_1", intent="pi_1", event_type="checkout.session.async_payment_succeeded"))
    assert t.requires_reconciliation is True and t.is_financial is False


def test_session_ref_requires_the_cs_prefix():
    """A payload whose object is not a session cannot be treated as one."""
    t = stripe_events.translate(intent_event("pi_1"))
    assert t.checkout_session_ref is None


def test_idempotency_key_hint_is_carried_from_intent_metadata():
    t = stripe_events.translate(intent_event("pi_1", idem_key="checkout:o:1:10"))
    assert t.idempotency_key_hint == "checkout:o:1:10"


def test_missing_metadata_yields_no_hint():
    assert stripe_events.translate(intent_event("pi_1")).idempotency_key_hint is None


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. RECONCILIATION AGAINST REAL RECORDS
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestReconciliation:

    @pytest.fixture
    def ctx(self):
        """A real priced, accepted, tax-determined order: 1200.00 + 240.00 tax = 1440.00."""
        with Session(engine) as db:
            org = Organization(name=f"rc-{uuid.uuid4().hex[:8]}")
            other_org = Organization(name=f"rx-{uuid.uuid4().hex[:8]}")
            db.add_all([org, other_org])
            db.flush()
            user = User(org_id=org.id, full_name="RC", email=f"rc-{uuid.uuid4().hex[:8]}@t.test",
                        username=f"rc{uuid.uuid4().hex[:8]}", password_hash="x", role="org_admin")
            staff = User(org_id=org.id, full_name="Fin", email=f"rf-{uuid.uuid4().hex[:8]}@t.test",
                         username=f"rf{uuid.uuid4().hex[:8]}", password_hash="x",
                         role="super_admin")
            db.add_all([user, staff])
            db.flush()
            event = Event(org_id=org.id, created_by=user.id, title="Reconciliation test")
            account = CommercialAccount(org_id=org.id)
            catalog = CatalogVersion(version_label="v1", vertical=f"rc-{uuid.uuid4().hex[:6]}",
                                     status="published")
            db.add_all([event, account, catalog])
            db.flush()
            line = CatalogLine(catalog_version_id=catalog.id, service_code="MEM-MANAGED",
                               name="Managed", unit_price=Decimal("1200.00"), currency="USD",
                               unit_basis="per_event", tax_treatment="standard_rate")
            db.add(line)
            db.flush()
            order = crud.create_order(
                db, event, commercial_account=account, catalog_version=catalog,
                purchaser_type="organization", purchaser_id=user.id, service_profile=None,
                cancellation_policy=None, currency="USD",
                idempotency_key=f"ord-{uuid.uuid4().hex[:10]}")
            crud.add_order_line(db, order, line, quantity=Decimal(1))
            crud.submit_order_for_acceptance(db, order)
            crud.accept_order(db, order, staff, terms_version="tos-v1")
            crud.record_tax_determination(db, order, staff, tax_amount=Decimal("240.00"),
                                          treatment="standard_rate", jurisdiction="GB",
                                          source="finance_manual")
            db.refresh(order)
            ids = SimpleNamespace(org_id=org.id, other_org_id=other_org.id, user_id=user.id,
                                  staff_id=staff.id, event_id=event.id, order_id=order.id,
                                  account_id=account.id, catalog_id=catalog.id, line_id=line.id,
                                  total=order.total_amount,
                                  sess=f"cs_test_{uuid.uuid4().hex[:14]}",
                                  intent=f"pi_test_{uuid.uuid4().hex[:14]}",
                                  intent2=f"pi_other_{uuid.uuid4().hex[:13]}")
        yield ids
        with Session(engine) as db:
            for sql, p in [
                # unmatched_settlements references provider_events, so it must go first.
                ("DELETE FROM unmatched_settlements WHERE provider_payment_ref IN (:a,:b)",
                 {"a": ids.intent, "b": ids.intent2}),
                ("DELETE FROM unmatched_settlements WHERE provider_event_id IN "
                 "(SELECT id FROM provider_events WHERE provider_payment_ref IN (:a,:b))",
                 {"a": ids.intent, "b": ids.intent2}),
                ("DELETE FROM provider_events WHERE payment_id IN "
                 "(SELECT id FROM payments WHERE event_order_id=:o)", {"o": ids.order_id}),
                ("DELETE FROM provider_events WHERE provider_payment_ref IN (:a,:b)",
                 {"a": ids.intent, "b": ids.intent2}),
                ("DELETE FROM audit_logs WHERE org_id=:g", {"g": ids.org_id}),
                ("DELETE FROM payments WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM payment_schedules WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM event_order_versions WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM event_order_lines WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM commercial_state_transitions WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM event_orders WHERE id=:o", {"o": ids.order_id}),
                ("DELETE FROM catalog_lines WHERE id=:l", {"l": ids.line_id}),
                ("DELETE FROM events WHERE id=:e", {"e": ids.event_id}),
                ("DELETE FROM catalog_versions WHERE id=:c", {"c": ids.catalog_id}),
                ("DELETE FROM commercial_accounts WHERE id=:a", {"a": ids.account_id}),
                ("DELETE FROM users WHERE org_id=:g", {"g": ids.org_id}),
                ("DELETE FROM organizations WHERE id IN (:g,:x)",
                 {"g": ids.org_id, "x": ids.other_org_id}),
            ]:
                db.execute(text(sql), p)
            db.commit()

    # ── helpers ───────────────────────────────────────────────────────────────────────────

    def _start_checkout(self, db, ids, *, intent=None, session=None):
        """Run the real start_hosted_checkout with the Stripe boundary stubbed."""
        order = db.get(EventOrder, ids.order_id)
        staff = db.get(User, ids.staff_id)
        fake = pay.CheckoutSessionResult(
            checkout_session_ref=session or ids.sess,
            checkout_url=f"https://checkout.stripe.com/c/pay/{session or ids.sess}",
            provider_payment_ref=intent, state="requires_action")
        prov = MagicMock()
        prov.name = PROVIDER
        prov.create_checkout_session.return_value = fake
        with patch.object(crud.payment_svc, "get_provider", return_value=prov):
            return crud.start_hosted_checkout(
                db, order, actor=staff, success_url=SUCCESS_URL, cancel_url=CANCEL_URL,
                provider_name=PROVIDER), prov

    def _payment(self, db, ids):
        return db.scalar(select_payment(ids.order_id))

    # ── 2. payment created with a session ref and NO payment ref ──────────────────────────

    def test_checkout_creates_payment_with_session_ref_and_null_payment_ref(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            p = db.get(Payment, out["payment"].id)
            assert p.checkout_session_ref == ctx.sess
            assert p.provider_payment_ref is None
            assert p.state == "requires_action", "creating a session collects nothing"
            assert p.amount == ctx.total == Decimal("1440.00")
            assert p.currency == "USD"

    def test_checkout_no_longer_raises_when_provider_gives_no_reference(self, ctx):
        """Directly the P0: this call used to raise ValueError -> HTTP 400."""
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx, intent=None)
            assert out["checkout_url"].startswith("https://checkout.stripe.com/")
            assert out["payment"].provider_payment_ref is None

    def test_missing_session_reference_still_fails_closed(self, ctx):
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            staff = db.get(User, ctx.staff_id)
            prov = MagicMock()
            prov.name = PROVIDER
            prov.create_checkout_session.return_value = pay.CheckoutSessionResult(
                checkout_session_ref="", checkout_url="https://x/y", provider_payment_ref=None)
            with patch.object(crud.payment_svc, "get_provider", return_value=prov):
                with pytest.raises(ValueError, match="checkout session reference"):
                    crud.start_hosted_checkout(
                        db, order, actor=staff, success_url=SUCCESS_URL,
                        cancel_url=CANCEL_URL, provider_name=PROVIDER)

    def test_idempotency_key_is_sent_as_metadata_for_out_of_order_recovery(self, ctx):
        with Session(engine) as db:
            _, prov = self._start_checkout(db, ctx)
            meta = prov.create_checkout_session.call_args.kwargs["metadata"]
            assert meta["zoiko_idempotency_key"].startswith("checkout:")

    # ── 9. double click ───────────────────────────────────────────────────────────────────

    def test_double_click_reuses_the_same_payment_and_session(self, ctx):
        with Session(engine) as db:
            first, _ = self._start_checkout(db, ctx)
            second, _ = self._start_checkout(db, ctx)
            assert first["payment"].id == second["payment"].id
            assert second["reused"] is True
            assert db.scalar(text_count(ctx.order_id)) == 1, "no duplicate payment row"

    def test_two_clicks_send_byte_identical_parameters_to_the_provider(self, ctx):
        """A real provider compares the WHOLE request against a reused idempotency key and
        rejects it if anything changed. A mocked provider returns the same session regardless,
        so only an equality check on the outgoing arguments can catch this — a per-request
        correlation id in the metadata made the second Pay click fail against real Stripe.
        """
        with Session(engine) as db:
            _, prov_a = self._start_checkout(db, ctx)
            first = dict(prov_a.create_checkout_session.call_args.kwargs)
            _, prov_b = self._start_checkout(db, ctx)
            second = dict(prov_b.create_checkout_session.call_args.kwargs)
            assert first == second, (
                "identical Pay clicks must produce an identical provider request; differing "
                f"keys: {[k for k in first if first.get(k) != second.get(k)]}"
            )

    def test_checkout_metadata_contains_no_per_request_values(self, ctx):
        with Session(engine) as db:
            _, prov = self._start_checkout(db, ctx)
            meta = prov.create_checkout_session.call_args.kwargs["metadata"]
            assert "zoiko_correlation_id" not in meta
            assert set(meta) == {"zoiko_event_order_id", "zoiko_order_version",
                                 "zoiko_idempotency_key"}

    # ── 3. adoption ───────────────────────────────────────────────────────────────────────

    def test_completed_session_attaches_the_payment_intent(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            res = ingest(db, session_event(ctx.sess, intent=ctx.intent))
            assert res["applied"] is True
            assert res["result"]["reason"] == "provider_reference_adopted"
            db.expire_all()
            p = db.get(Payment, pid)
            assert p.provider_payment_ref == ctx.intent
            assert p.checkout_session_ref == ctx.sess
            assert p.state == "requires_action", "adoption must not move state"

    # ── 4. replay ─────────────────────────────────────────────────────────────────────────

    def test_repeated_completed_event_is_idempotent(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            ev = session_event(ctx.sess, intent=ctx.intent)
            first = ingest(db, ev)
            again = ingest(db, ev)                       # same event id -> duplicate
            assert first["applied"] is True
            assert again["duplicate"] is True
            db.expire_all()
            assert db.get(Payment, pid).provider_payment_ref == ctx.intent

    def test_a_second_distinct_completed_event_rebinding_the_same_ref_is_a_noop(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            res = ingest(db, session_event(ctx.sess, intent=ctx.intent))   # new event id
            assert res["applied"] is False
            assert res["result"]["reason"] == "already_reconciled"
            db.expire_all()
            assert db.get(Payment, pid).provider_payment_ref == ctx.intent

    # ── 5. conflict ───────────────────────────────────────────────────────────────────────

    def test_a_different_payment_intent_for_a_bound_session_is_refused(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            res = ingest(db, session_event(ctx.sess, intent=ctx.intent2))
            assert res["applied"] is False
            assert res["result"]["reason"] == "reconciliation_conflict"
            assert res["processing_status"] == "rejected"
            db.expire_all()
            p = db.get(Payment, pid)
            assert p.provider_payment_ref == ctx.intent, "NEVER silently overwritten"

    def test_conflict_is_recorded_as_provider_evidence(self, ctx):
        with Session(engine) as db:
            self._start_checkout(db, ctx)
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            ev = session_event(ctx.sess, intent=ctx.intent2)
            ingest(db, ev)
            rec = db.scalar(
                text_event(ev["id"]))
            assert rec is not None and rec.processing_status == "rejected"

    # ── 6/7. the state machine still owns state ───────────────────────────────────────────

    def test_succeeded_after_reconciliation_marks_the_right_payment_paid(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            # authorization first: requires_action -> pending
            ingest(db, intent_event(ctx.intent,
                                    event_type="payment_intent.amount_capturable_updated"))
            db.expire_all()
            assert db.get(Payment, pid).state == "pending"
            ingest(db, intent_event(ctx.intent, event_type="payment_intent.succeeded"))
            db.expire_all()
            p = db.get(Payment, pid)
            assert p.state == "paid"
            assert p.amount == Decimal("1440.00"), "amount never rewritten by an event"

    def test_payment_failed_after_reconciliation_marks_failed(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            ingest(db, intent_event(ctx.intent, event_type="payment_intent.payment_failed"))
            db.expire_all()
            assert db.get(Payment, pid).state == "failed"

    def test_authorization_never_becomes_paid_directly(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            ingest(db, intent_event(ctx.intent,
                                    event_type="payment_intent.amount_capturable_updated"))
            db.expire_all()
            assert db.get(Payment, pid).state == "pending", "authorization != settlement"

    # ── 8. nothing but a verified event may pay ───────────────────────────────────────────

    def test_a_completed_session_alone_does_not_pay_anything(self, ctx):
        """The browser returning to success_url is exactly this event and no more."""
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            db.expire_all()
            assert db.get(Payment, pid).state != "paid"

    def test_unverified_signature_cannot_reconcile(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            res = ingest(db, session_event(ctx.sess, intent=ctx.intent), verified=False)
            assert res["applied"] is False
            assert res["processing_status"] == "rejected"
            db.expire_all()
            assert db.get(Payment, pid).provider_payment_ref is None

    # ── 10. amount authority ──────────────────────────────────────────────────────────────

    def test_a_session_whose_total_disagrees_is_refused(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            res = ingest(db, session_event(ctx.sess, intent=ctx.intent, amount_total=100))
            assert res["applied"] is False and res["processing_status"] == "rejected"
            db.expire_all()
            p = db.get(Payment, pid)
            assert p.provider_payment_ref is None, "no binding on a disputed amount"
            assert p.amount == Decimal("1440.00")

    def test_amount_comes_only_from_the_commercial_chain(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            assert out["payment"].amount == Decimal("1440.00")

    # ── 11. cross-tenant ──────────────────────────────────────────────────────────────────

    def test_an_unknown_session_is_never_attached_to_a_payment(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            res = ingest(db, session_event(f"cs_not_ours_{uuid.uuid4().hex[:8]}",
                                           intent=ctx.intent2))
            assert res["applied"] is False
            assert res["result"]["reason"] == "checkout_session_unmatched"
            db.expire_all()
            assert db.get(Payment, pid).provider_payment_ref is None

    def test_a_reference_bound_to_another_payment_cannot_be_stolen(self, ctx):
        """Two payments, one reference: the second binding must be refused."""
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            order = db.get(EventOrder, ctx.order_id)
            other = Payment(event_order_id=order.id, provider=PROVIDER,
                            checkout_session_ref=f"cs_second_{uuid.uuid4().hex[:10]}",
                            amount=Decimal("1440.00"), currency="USD",
                            state="requires_action", idempotency_key=str(uuid.uuid4()))
            db.add(other)
            db.commit()
            res = ingest(db, session_event(other.checkout_session_ref, intent=ctx.intent))
            assert res["result"]["reason"] == "reconciliation_conflict"
            db.expire_all()
            assert db.get(Payment, other.id).provider_payment_ref is None

    # ── 3 (ordering). intent event arriving BEFORE the session event ──────────────────────

    def test_intent_event_arriving_first_is_recovered_by_exact_key(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            key = db.get(Payment, pid).idempotency_key
            res = ingest(db, intent_event(
                ctx.intent, event_type="payment_intent.amount_capturable_updated",
                idem_key=key))
            assert res["applied"] is True
            db.expire_all()
            p = db.get(Payment, pid)
            assert p.provider_payment_ref == ctx.intent
            assert p.state == "pending"

    def test_intent_event_arriving_first_without_a_hint_stays_unmatched(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            res = ingest(db, intent_event(ctx.intent, event_type="payment_intent.succeeded"))
            assert res["applied"] is False
            assert res["result"]["reason"] == "unmatched_settlement"
            db.expire_all()
            assert db.get(Payment, pid).state != "paid", "never guessed onto a payment"

    def test_a_hint_cannot_rebind_a_payment_that_already_has_a_reference(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            key = db.get(Payment, pid).idempotency_key
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            res = ingest(db, intent_event(ctx.intent2,
                                          event_type="payment_intent.succeeded", idem_key=key))
            assert res["result"]["reason"] == "unmatched_settlement"
            db.expire_all()
            p = db.get(Payment, pid)
            assert p.provider_payment_ref == ctx.intent
            assert p.state != "paid"

    # ── 13. duplicates remain harmless ────────────────────────────────────────────────────

    def test_duplicate_intent_events_do_not_double_apply(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            ingest(db, session_event(ctx.sess, intent=ctx.intent))
            ingest(db, intent_event(ctx.intent,
                                    event_type="payment_intent.amount_capturable_updated"))
            ev = intent_event(ctx.intent, event_type="payment_intent.succeeded")
            ingest(db, ev)
            again = ingest(db, ev)
            assert again["duplicate"] is True
            db.expire_all()
            assert db.get(Payment, pid).state == "paid"

    def test_expired_session_records_evidence_and_pays_nothing(self, ctx):
        with Session(engine) as db:
            out, _ = self._start_checkout(db, ctx)
            pid = out["payment"].id
            res = ingest(db, session_event(ctx.sess, event_type="checkout.session.expired"))
            assert res["applied"] is False
            db.expire_all()
            p = db.get(Payment, pid)
            assert p.state == "requires_action" and p.provider_payment_ref is None


# ── small query helpers (kept out of the class for readability) ───────────────────────────

def select_payment(order_id):
    from sqlalchemy import select
    return select(Payment).where(Payment.event_order_id == order_id)


def text_count(order_id):
    return text("SELECT COUNT(*) FROM payments WHERE event_order_id = :o").bindparams(o=order_id)


def text_event(provider_event_id):
    from sqlalchemy import select
    return select(ProviderEvent).where(ProviderEvent.provider_event_id == provider_event_id)
