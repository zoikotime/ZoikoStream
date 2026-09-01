"""Phase 4B — Stripe webhook + provider-event integration tests.

Signatures are GENUINE: each test signs its own raw body with a test secret using Stripe's
own scheme, and verification runs through the real SDK. Nothing is stubbed at the signature
boundary, because that boundary is the entire trust model of this endpoint.

No live Stripe account, no network, no real credentials. Stripe API calls (as opposed to
webhook parsing) are not exercised here at all — this endpoint never calls Stripe.

`needs_db` tests exercise real Postgres semantics — ON CONFLICT arbitration, the unique
constraint, concurrent delivery — and skip when DATABASE_URL is unreachable.
"""
import ast
import concurrent.futures
import hashlib
import hmac
import inspect
import json
import textwrap
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import app.main as main_app
from app.config import settings
from app.crud import commercial as crud
from _testsupport import code_only
from app.db import engine
from app.models import (
    CatalogVersion, CommercialAccount, Event, EventOrder, Organization, Payment,
    ProviderEvent, UnmatchedSettlement, User,
)
from app.services import payments_stripe_events as stripe_events

WEBHOOK_SECRET = "whsec_test_secret_for_unit_tests_only"
ENDPOINT = "/api/commercial/webhooks/stripe"


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


# ── genuine Stripe signature generation ───────────────────────────────────────────────────

def sign(raw: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    """Build a real Stripe-Signature header: v1 = HMAC-SHA256 over "{t}.{raw}"."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + raw
    v1 = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


def intent_event(*, event_id=None, event_type="payment_intent.succeeded", intent_id="pi_test_1",
                 amount=10000, currency="usd", created=None) -> dict:
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": event_type,
        "created": created if created is not None else int(time.time()),
        "data": {"object": {
            "id": intent_id, "object": "payment_intent",
            "amount": amount, "currency": currency, "status": "succeeded",
        }},
    }


def charge_refund_event(*, event_id=None, intent_id="pi_test_1", amount=10000, refunded=10000) -> dict:
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:16]}",
        "object": "event", "type": "charge.refunded", "created": int(time.time()),
        "data": {"object": {
            "id": "ch_test_1", "object": "charge", "payment_intent": intent_id,
            "amount": amount, "amount_refunded": refunded, "currency": "usd",
        }},
    }


def body_of(event: dict) -> bytes:
    """The exact bytes that get signed AND sent — no reserialization in between."""
    return json.dumps(event, separators=(",", ":")).encode()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    with TestClient(main_app.app) as c:
        yield c


def post(client, event: dict, *, secret=WEBHOOK_SECRET, timestamp=None, header=...,
         raw: bytes | None = None):
    body = raw if raw is not None else body_of(event)
    sig = sign(body, secret, timestamp) if header is ... else header
    headers = {"content-type": "application/json"}
    if sig is not None:
        headers["stripe-signature"] = sig
    return client.post(ENDPOINT, content=body, headers=headers)


# ══════════════════════════════════════════════════════════════════════════════════════
# 1-6. SIGNATURE VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════════════

def test_valid_signature_is_accepted(client):
    r = post(client, intent_event(event_type="customer.created"))
    assert r.status_code == 200
    assert r.json()["received"] is True


def test_invalid_signature_is_rejected(client):
    event = intent_event()
    body = body_of(event)
    bad = sign(b'{"tampered":true}')          # signed a DIFFERENT body
    r = client.post(ENDPOINT, content=body,
                    headers={"stripe-signature": bad, "content-type": "application/json"})
    assert r.status_code == 401


def test_missing_signature_is_rejected(client):
    r = post(client, intent_event(), header=None)
    assert r.status_code == 401


@pytest.mark.parametrize("bad", ["", "garbage", "t=,v1=", "v1=deadbeef", "t=abc,v1=xyz",
                                  "t=1,v1=" + "0" * 64])
def test_malformed_signature_is_rejected(client, bad):
    r = post(client, intent_event(), header=bad)
    assert r.status_code == 401


def test_wrong_webhook_secret_is_rejected(client):
    r = post(client, intent_event(), secret="whsec_a_different_secret_entirely")
    assert r.status_code == 401


def test_stale_timestamp_is_rejected(client):
    """Stripe's tolerance check — a captured payload must not replay forever."""
    long_ago = int(time.time()) - 60 * 60 * 24
    r = post(client, intent_event(), timestamp=long_ago)
    assert r.status_code == 401


def test_tampered_body_with_valid_looking_signature_is_rejected(client):
    """Signature is bound to the RAW bytes: change one byte and it must fail."""
    event = intent_event(amount=10000)
    body = body_of(event)
    sig = sign(body)
    tampered = body.replace(b'"amount":10000', b'"amount":99999')
    assert tampered != body
    r = client.post(ENDPOINT, content=tampered,
                    headers={"stripe-signature": sig, "content-type": "application/json"})
    assert r.status_code == 401


def test_malformed_json_is_rejected(client):
    r = post(client, {}, raw=b"this is not json at all")
    assert r.status_code == 401       # signature verifies the bytes, then parsing fails closed


def test_verification_uses_the_raw_body_not_a_reserialization():
    """Guards the specific hazard: json.loads -> json.dumps -> verify would break legitimate
    signatures (and could be gamed). The verifier must receive bytes."""
    src = code_only(stripe_events.verify_and_parse)
    assert "json.dumps" not in src
    assert "construct_event" in src
    assert "payload=raw_body" in src


def test_no_custom_signature_algorithm_is_implemented():
    """The SDK owns the scheme; we must not hand-roll HMAC for Stripe."""
    src = code_only(stripe_events)
    assert "hmac" not in src.lower()
    assert "sha256" not in src.lower()


def test_unconfigured_secret_returns_503(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    with TestClient(main_app.app) as c:
        r = post(c, intent_event())
    assert r.status_code == 503


def test_signature_failure_persists_nothing(client):
    """An unverified body is not evidence — storing it would be a write primitive."""
    event = intent_event(event_id=f"evt_never_{uuid.uuid4().hex[:10]}")
    post(client, event, secret="whsec_wrong")
    if DB_UP:
        with Session(engine) as db:
            found = db.scalar(select(ProviderEvent).where(
                ProviderEvent.provider_event_id == event["id"]))
            assert found is None


# ══════════════════════════════════════════════════════════════════════════════════════
# 10. STRIPE -> GENERIC EVENT MAPPING (pure translation, no DB)
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("stripe_type,generic", [
    ("payment_intent.amount_capturable_updated", "authorization_succeeded"),
    ("payment_intent.processing", "authorization_succeeded"),
    ("payment_intent.requires_action", "action_required"),
    ("payment_intent.succeeded", "capture_succeeded"),
    ("payment_intent.payment_failed", "capture_failed"),
    ("payment_intent.canceled", "authorization_failed"),
])
def test_payment_intent_events_map_to_generic_vocabulary(stripe_type, generic):
    t = stripe_events.translate(intent_event(event_type=stripe_type))
    assert t.generic_event_type == generic
    # Every generic name must exist in the provider-neutral map — no invented events.
    assert generic in crud.PROVIDER_EVENT_STATE_MAP


def test_every_mapped_generic_event_resolves_to_a_real_payment_state():
    from app.models.commercial import PAYMENT_STATES
    for generic in crud.PROVIDER_EVENT_STATE_MAP.values():
        assert generic in PAYMENT_STATES


def test_full_refund_maps_to_refunded():
    t = stripe_events.translate(charge_refund_event(amount=10000, refunded=10000))
    assert t.generic_event_type == "refunded"


def test_partial_refund_maps_to_refund_partial():
    t = stripe_events.translate(charge_refund_event(amount=10000, refunded=2500))
    assert t.generic_event_type == "refund_partial"


def test_ambiguous_refund_payload_takes_the_weaker_claim():
    """Missing amounts must not be read as a FULL refund."""
    event = charge_refund_event()
    event["data"]["object"].pop("amount_refunded")
    assert stripe_events.translate(event).generic_event_type == "refund_partial"


def dispute_event(*, event_type="charge.dispute.created", status="needs_response",
                   dispute_id="dp_test_1", intent_id="pi_test_1", amount=10000,
                   currency="usd", event_id=None) -> dict:
    """A Stripe-shaped `charge.dispute.*` event."""
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:16]}",
        "object": "event", "type": event_type, "created": int(time.time()),
        "data": {"object": {
            "id": dispute_id, "object": "dispute", "payment_intent": intent_id,
            "amount": amount, "currency": currency, "status": status,
            "reason": "fraudulent", "evidence_details": {"due_by": int(time.time()) + 604800},
            "balance_transactions": [],
        }},
    }


@pytest.mark.parametrize("dispute_type", [
    "charge.dispute.created", "charge.dispute.updated", "charge.dispute.closed",
    "charge.dispute.funds_withdrawn", "charge.dispute.funds_reinstated",
])
def test_dispute_events_are_translated_into_case_facts(dispute_type):
    """Section 22 used to say "record, do not half-implement the workflow" — dispute events
    were evidence-only, which meant a real chargeback left the ledger reading `paid`.

    They are now translated into case facts for crud.ingest_dispute_event. Still NOT financial
    instructions: the payment consequence is derived from the case status by the commercial
    layer, never from the generic event map."""
    t = stripe_events.translate(dispute_event(event_type=dispute_type))
    assert t.is_dispute is True
    assert t.is_financial is False
    assert t.generic_event_type is None
    assert t.dispute["provider_dispute_ref"] == "dp_test_1"
    assert t.dispute["provider_payment_ref"] == "pi_test_1"
    assert t.dispute["status"] == "evidence_required"


def test_a_dispute_status_stripe_may_add_later_is_not_guessed():
    """An unmapped status must not silently become "opened" and move a payment."""
    t = stripe_events.translate(dispute_event(status="some_future_stripe_status"))
    assert t.is_dispute is True
    assert t.dispute["status"] is None


def test_a_dispute_object_with_no_identifier_stays_evidence_only():
    """Without a dispute id there is no case identity to be idempotent against, so it cannot
    be attached to a payment."""
    event = dispute_event()
    del event["data"]["object"]["id"]
    t = stripe_events.translate(event)
    assert t.is_dispute is False
    assert t.follow_up_required is True
    assert "dispute" in t.evidence_reason


@pytest.mark.parametrize("unknown", [
    "customer.created", "invoice.paid", "payout.paid", "some.brand.new.event",
])
def test_unknown_event_types_are_evidence_only(unknown):
    t = stripe_events.translate(intent_event(event_type=unknown))
    assert t.is_financial is False
    assert t.generic_event_type is None
    assert "unhandled_stripe_event_type" in t.evidence_reason


@pytest.mark.parametrize("checkout_type", [
    "checkout.session.completed", "checkout.session.expired",
    "checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed",
])
def test_checkout_session_events_never_drive_the_state_machine(checkout_type):
    """Phase 4F moved these out of the 'unknown' bucket so a completed session can bind the
    real PaymentIntent id. The part that must NOT change is that they carry no financial
    instruction: authorization and settlement still arrive as payment_intent.* only, so a
    completed checkout can never by itself mark anything paid."""
    t = stripe_events.translate(intent_event(event_type=checkout_type))
    assert t.is_financial is False
    assert t.generic_event_type is None


def test_stripe_event_timestamp_is_preserved_separately():
    """Section 26: occurred_at is Stripe's, not our clock."""
    fixed = 1767225600
    t = stripe_events.translate(intent_event(created=fixed))
    assert t.occurred_at == datetime.fromtimestamp(fixed, tz=timezone.utc)


def test_amount_is_converted_from_minor_units():
    t = stripe_events.translate(intent_event(amount=123456, currency="usd"))
    assert t.amount == Decimal("1234.56") and t.currency == "USD"


def test_zero_decimal_currency_amount_is_not_divided():
    t = stripe_events.translate(intent_event(amount=1000, currency="jpy"))
    assert t.amount == Decimal("1000") and t.currency == "JPY"


def test_matching_is_by_intent_id_only():
    """Never by amount, currency or customer email — fuzzy financial matching is how one
    tenant's money lands on another's ledger."""
    src = code_only(stripe_events._intent_ref)
    for forbidden in ("amount", "currency", "email", "customer"):
        assert forbidden not in src


def test_translation_never_raises_on_a_weird_payload():
    for payload in [
        {"id": "evt_1", "type": "x", "data": {}},
        {"id": "evt_2", "type": "x", "data": {"object": None}},
        {"id": "evt_3", "type": "payment_intent.succeeded", "data": {"object": {}}},
        {"id": "evt_4", "type": "payment_intent.succeeded",
         "data": {"object": {"amount": "not-an-int", "currency": "usd"}}},
    ]:
        t = stripe_events.translate(payload)
        assert t.provider_event_id == payload["id"]


def test_stripe_event_names_stay_out_of_the_commercial_layer():
    """Section 10/27: Stripe knowledge is confined to the integration layer."""
    from app.crud import commercial as crud_mod
    from app.models import commercial as models_mod
    for module in (crud_mod, models_mod):
        src = code_only(module)
        for token in ("payment_intent.", "charge.dispute", "charge.refunded", "stripe."):
            assert token not in src, f"{module.__name__} contains Stripe vocabulary {token!r}"


def test_mapping_layer_contains_no_commercial_logic():
    """Section 27."""
    src = code_only(stripe_events)
    for token in ("CatalogLine", "EventOrder", "Invoice", "SellerLegalEntity", "CapacityPool",
                  "evaluate_readiness", "golive", "RefundCredit", "tax_", "price",
                  "apply_payment_state", "payment.state"):
        assert token not in src, f"mapping layer contains commercial logic: {token!r}"


# ══════════════════════════════════════════════════════════════════════════════════════
# EVENT IDENTITY, STATE MACHINE, AMOUNTS, MATCHING, TENANCY (real Postgres)
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestStripeWebhookProcessing:

    @pytest.fixture
    def ctx(self, client):
        with Session(engine) as db:
            org = Organization(name=f"sw-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="SW", email=f"sw-{uuid.uuid4().hex[:8]}@t.test",
                        username=f"sw{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            db.add(user)
            db.flush()
            event = Event(org_id=org.id, created_by=user.id, title="Stripe webhook test")
            account = CommercialAccount(org_id=org.id)
            catalog = CatalogVersion(version_label="v1", vertical=f"sw-{uuid.uuid4().hex[:6]}",
                                     status="published")
            db.add_all([event, account, catalog])
            db.flush()
            order = EventOrder(event_id=event.id, commercial_account_id=account.id,
                               catalog_version_id=catalog.id, currency="USD",
                               subtotal=Decimal("100.00"), total_amount=Decimal("100.00"),
                               status="accepted", idempotency_key=str(uuid.uuid4()))
            db.add(order)
            db.flush()
            intent_id = f"pi_test_{uuid.uuid4().hex[:14]}"
            payment = Payment(event_order_id=order.id, provider="stripe",
                              provider_payment_ref=intent_id, amount=Decimal("100.00"),
                              currency="USD", state="pending",
                              idempotency_key=f"auth_{uuid.uuid4().hex[:12]}")
            db.add(payment)
            db.commit()
            ids = SimpleNamespace(org_id=org.id, user_id=user.id, event_id=event.id,
                                  order_id=order.id, payment_id=payment.id,
                                  intent_id=intent_id, account_id=account.id,
                                  catalog_id=catalog.id, auth_key=payment.idempotency_key,
                                  evt_prefix=f"evt_sw_{uuid.uuid4().hex[:10]}_",
                                  miss=f"pi_missing_{uuid.uuid4().hex[:10]}_")
        yield client, ids
        with Session(engine) as db:
            for sql, params in [
                ("DELETE FROM unmatched_settlements WHERE provider_event_id IN "
                 "(SELECT id FROM provider_events WHERE provider_event_id LIKE :e)",
                 {"e": f"{ids.evt_prefix}%"}),
                ("DELETE FROM provider_events WHERE provider_event_id LIKE :e", {"e": f"{ids.evt_prefix}%"}),
                ("DELETE FROM audit_logs WHERE org_id = :o OR actor_id = :u",
                 {"o": ids.org_id, "u": ids.user_id}),
                ("DELETE FROM payment_schedules WHERE event_order_id = :o", {"o": ids.order_id}),
                ("DELETE FROM payments WHERE event_order_id = :o", {"o": ids.order_id}),
                ("DELETE FROM commercial_state_transitions WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM event_orders WHERE id = :o", {"o": ids.order_id}),
                ("DELETE FROM events WHERE id = :e", {"e": ids.event_id}),
                ("DELETE FROM catalog_versions WHERE id = :c", {"c": ids.catalog_id}),
                ("DELETE FROM commercial_accounts WHERE id = :a", {"a": ids.account_id}),
                ("DELETE FROM users WHERE id = :u", {"u": ids.user_id}),
                ("DELETE FROM organizations WHERE id = :o", {"o": ids.org_id}),
            ]:
                db.execute(text(sql), params)
            db.commit()

    def _evt_id(self, ids) -> str:
        return f"{ids.evt_prefix}{uuid.uuid4().hex[:8]}"

    def _send(self, client, ids, **kw):
        kw.setdefault("event_id", self._evt_id(ids))
        kw.setdefault("intent_id", ids.intent_id)
        return post(client, intent_event(**kw))

    # ── 7-10. event identity ─────────────────────────────────────────────────────────────
    def test_new_event_is_stored_with_full_evidence(self, ctx):
        client, ids = ctx
        eid = self._evt_id(ids)
        r = self._send(client, ids, event_id=eid)
        assert r.status_code == 200 and r.json()["applied"] is True
        with Session(engine) as db:
            rec = db.scalar(select(ProviderEvent).where(ProviderEvent.provider_event_id == eid))
            assert rec.provider == "stripe"
            assert rec.event_type == "capture_succeeded"       # generic, not Stripe's name
            assert rec.signature_verified is True
            assert len(rec.payload_hash) == 64
            assert rec.occurred_at is not None                 # Stripe's timestamp
            assert rec.received_at is not None                 # ours, separately
            assert rec.correlation_id
            assert rec.processing_status == "processed"

    def test_duplicate_event_is_idempotent(self, ctx):
        client, ids = ctx
        eid = self._evt_id(ids)
        first = self._send(client, ids, event_id=eid)
        second = self._send(client, ids, event_id=eid)
        assert first.json()["applied"] is True
        assert second.status_code == 200
        assert second.json()["duplicate"] is True and second.json()["applied"] is False
        with Session(engine) as db:
            n = db.scalar(text("SELECT count(*) FROM provider_events WHERE provider_event_id=:e")
                          .bindparams(e=eid))
            assert n == 1

    def test_ten_redeliveries_produce_one_effect(self, ctx):
        client, ids = ctx
        eid = self._evt_id(ids)
        results = [self._send(client, ids, event_id=eid) for _ in range(10)]
        assert all(r.status_code == 200 for r in results)
        assert sum(1 for r in results if r.json().get("applied")) == 1
        with Session(engine) as db:
            assert db.scalar(text("SELECT count(*) FROM provider_events WHERE provider_event_id=:e")
                             .bindparams(e=eid)) == 1
            assert db.get(Payment, ids.payment_id).state == "paid"

    def test_concurrent_duplicate_deliveries_produce_one_effect(self, ctx):
        """The race an `if exists()` check cannot close — only the DB constraint can."""
        client, ids = ctx
        eid = self._evt_id(ids)

        def deliver():
            try:
                r = self._send(client, ids, event_id=eid)
                return r.status_code, r.json()
            except Exception as e:
                return 0, {"error": type(e).__name__}

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = [f.result() for f in [pool.submit(deliver) for _ in range(5)]]

        assert all(code == 200 for code, _ in results), results
        assert sum(1 for _, b in results if b.get("applied")) == 1, results
        with Session(engine) as db:
            assert db.scalar(text("SELECT count(*) FROM provider_events WHERE provider_event_id=:e")
                             .bindparams(e=eid)) == 1
            assert db.get(Payment, ids.payment_id).state == "paid"

    def test_unique_constraint_is_enforced_by_the_database(self, ctx):
        client, ids = ctx
        eid = self._evt_id(ids)
        self._send(client, ids, event_id=eid)
        with Session(engine) as db:
            with pytest.raises(Exception):
                db.execute(text(
                    "INSERT INTO provider_events (id, provider, provider_event_id, event_type, "
                    "received_at, payload_hash, signature_verified, processing_status, "
                    "processing_attempts, correlation_id) VALUES "
                    "(:i,'stripe',:e,'capture_succeeded',NOW(),'x',true,'received',1,'c')"
                ), {"i": str(uuid.uuid4()), "e": eid})
                db.commit()
            db.rollback()

    # ── 11-15. payment intent events ─────────────────────────────────────────────────────
    def test_payment_intent_succeeded_settles_the_payment(self, ctx):
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.succeeded")
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "paid"

    def test_payment_intent_payment_failed_marks_failed(self, ctx):
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.payment_failed")
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "failed"

    def test_payment_intent_canceled_marks_failed(self, ctx):
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.canceled")
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "failed"

    def test_payment_intent_requires_action_is_a_legal_transition(self, ctx):
        client, ids = ctx
        r = self._send(client, ids, event_type="payment_intent.requires_action")
        assert r.json()["applied"] is True
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "requires_action"

    def test_payment_intent_processing_keeps_it_pending(self, ctx):
        """pending -> pending is a same-state replay: recorded, no side effect."""
        client, ids = ctx
        r = self._send(client, ids, event_type="payment_intent.processing")
        assert r.json()["processing_status"] == "replayed"
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "pending"

    # ── 16-19. state machine ─────────────────────────────────────────────────────────────
    def test_invalid_transition_is_rejected_and_payment_untouched(self, ctx):
        """failed -> paid must stay blocked even though Stripe asked for it."""
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.payment_failed")
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "failed"
        eid = self._evt_id(ids)
        r = self._send(client, ids, event_id=eid, event_type="payment_intent.succeeded")
        assert r.status_code == 200                    # final answer, not a retry
        body = r.json()
        assert body["applied"] is False
        assert body["processing_status"] == "rejected"
        assert "illegal payment transition" in body["result"]["reason"]
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "failed"
            rec = db.scalar(select(ProviderEvent).where(ProviderEvent.provider_event_id == eid))
            assert rec is not None and rec.processing_status == "rejected"
            assert rec.processing_error                # retained, explains itself

    def test_duplicate_same_state_event_is_harmless(self, ctx):
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.succeeded")
        r = self._send(client, ids, event_type="payment_intent.succeeded")   # different evt id
        assert r.status_code == 200
        assert r.json()["applied"] is False
        assert r.json()["processing_status"] == "replayed"
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "paid"

    # ── 20-23. amount / currency ─────────────────────────────────────────────────────────
    def test_amount_mismatch_is_blocked(self, ctx):
        client, ids = ctx
        r = self._send(client, ids, amount=999999)        # payment is 100.00
        assert r.status_code == 200
        assert r.json()["applied"] is False
        assert "amount mismatch" in r.json()["result"]["reason"]
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "pending"

    def test_currency_mismatch_is_blocked(self, ctx):
        client, ids = ctx
        r = self._send(client, ids, currency="eur")       # payment is USD
        assert r.json()["applied"] is False
        assert "currency mismatch" in r.json()["result"]["reason"]
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "pending"

    def test_matching_amount_and_currency_are_accepted(self, ctx):
        client, ids = ctx
        r = self._send(client, ids, amount=10000, currency="usd")
        assert r.json()["applied"] is True

    # ── 24-26. matching / unmatched ──────────────────────────────────────────────────────
    def test_matched_payment_is_found_by_intent_id(self, ctx):
        client, ids = ctx
        eid = self._evt_id(ids)
        self._send(client, ids, event_id=eid)
        with Session(engine) as db:
            rec = db.scalar(select(ProviderEvent).where(ProviderEvent.provider_event_id == eid))
            assert rec.payment_id == ids.payment_id

    def test_unmatched_event_is_retained(self, ctx):
        client, ids = ctx
        unknown = f"{ids.miss}{uuid.uuid4().hex[:6]}"
        r = self._send(client, ids, intent_id=unknown)
        assert r.status_code == 200
        assert r.json()["result"]["reason"] == "unmatched_settlement"
        with Session(engine) as db:
            s = db.get(UnmatchedSettlement,
                       uuid.UUID(r.json()["result"]["unmatched_settlement_id"]))
            assert s.status == "open" and s.provider == "stripe"
            assert s.provider_payment_ref == unknown
            assert s.amount == Decimal("100.00") and s.currency == "USD"
            assert s.correlation_id and s.reason
            db.execute(text("DELETE FROM unmatched_settlements WHERE id=:i"), {"i": s.id})
            db.commit()

    def test_unmatched_event_does_not_mutate_any_payment(self, ctx):
        client, ids = ctx
        before = None
        with Session(engine) as db:
            before = db.get(Payment, ids.payment_id).state
        r = self._send(client, ids, intent_id=f"{ids.miss}{uuid.uuid4().hex[:6]}")
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == before
            sid = r.json()["result"].get("unmatched_settlement_id")
            if sid:
                db.execute(text("DELETE FROM unmatched_settlements WHERE id=:i"), {"i": sid})
                db.commit()

    # ── 27-28. tenant security ───────────────────────────────────────────────────────────
    def test_webhook_cannot_choose_a_tenant(self, ctx):
        """Stripe is external: no organization_id is accepted from the payload, and injecting
        one changes nothing about which payment is located."""
        client, ids = ctx
        event = intent_event(event_id=self._evt_id(ids), intent_id=ids.intent_id)
        # Attacker-supplied tenant hints, all of which must be ignored.
        event["data"]["object"]["metadata"] = {
            "organization_id": str(uuid.uuid4()), "org_id": str(uuid.uuid4()),
            "tenant": "some-other-org",
        }
        r = post(client, event)
        assert r.status_code == 200
        with Session(engine) as db:
            # Resolved to OUR payment, by intent id — the metadata had no effect.
            rec = db.scalar(select(ProviderEvent).where(
                ProviderEvent.provider_event_id == event["id"]))
            assert rec.payment_id == ids.payment_id

    def test_cross_tenant_mutation_is_impossible(self, ctx):
        """An event naming an intent that belongs to another org's payment cannot touch ours,
        and vice versa — the intent id IS the tenant boundary."""
        client, ids = ctx
        with Session(engine) as db:
            other_org = Organization(name=f"sw-other-{uuid.uuid4().hex[:8]}")
            db.add(other_org)
            db.flush()
            other_user = User(org_id=other_org.id, full_name="O",
                              email=f"o-{uuid.uuid4().hex[:8]}@t.test",
                              username=f"o{uuid.uuid4().hex[:8]}", password_hash="x",
                              role="org_admin")
            db.add(other_user)
            db.flush()
            other_event = Event(org_id=other_org.id, created_by=other_user.id, title="Other")
            other_account = CommercialAccount(org_id=other_org.id)
            other_catalog = CatalogVersion(version_label="v1",
                                           vertical=f"oth-{uuid.uuid4().hex[:6]}",
                                           status="published")
            db.add_all([other_event, other_account, other_catalog])
            db.flush()
            other_order = EventOrder(event_id=other_event.id,
                                     commercial_account_id=other_account.id,
                                     catalog_version_id=other_catalog.id, currency="USD",
                                     subtotal=Decimal("50.00"), total_amount=Decimal("50.00"),
                                     status="accepted", idempotency_key=str(uuid.uuid4()))
            db.add(other_order)
            db.flush()
            victim_ref = f"pi_victim_{uuid.uuid4().hex[:12]}"
            victim = Payment(event_order_id=other_order.id, provider="stripe",
                             provider_payment_ref=victim_ref, amount=Decimal("50.00"),
                             currency="USD", state="pending",
                             idempotency_key=str(uuid.uuid4()))
            db.add(victim)
            db.commit()
            # Capture PLAIN ids before the session closes — the cleanup block below opens a new
            # session, and touching a detached ORM instance there raises DetachedInstanceError.
            other = SimpleNamespace(
                org_id=other_org.id, user_id=other_user.id, event_id=other_event.id,
                order_id=other_order.id, account_id=other_account.id,
                catalog_id=other_catalog.id, payment_id=victim.id,
            )
        try:
            # An event for OUR intent must not touch the other org's payment.
            self._send(client, ids, event_type="payment_intent.succeeded")
            with Session(engine) as db:
                assert db.get(Payment, other.payment_id).state == "pending"    # untouched
                assert db.get(Payment, ids.payment_id).state == "paid"
        finally:
            with Session(engine) as db:
                for sql, params in [
                    ("DELETE FROM provider_events WHERE payment_id=:p", {"p": other.payment_id}),
                    ("DELETE FROM audit_logs WHERE org_id=:o", {"o": other.org_id}),
                    ("DELETE FROM payments WHERE id=:p", {"p": other.payment_id}),
                    ("DELETE FROM commercial_state_transitions WHERE event_order_id=:o", {"o": other.order_id}),
                    ("DELETE FROM event_orders WHERE id=:o", {"o": other.order_id}),
                    ("DELETE FROM events WHERE id=:e", {"e": other.event_id}),
                    ("DELETE FROM catalog_versions WHERE id=:c", {"c": other.catalog_id}),
                    ("DELETE FROM commercial_accounts WHERE id=:a", {"a": other.account_id}),
                    ("DELETE FROM users WHERE id=:u", {"u": other.user_id}),
                    ("DELETE FROM organizations WHERE id=:o", {"o": other.org_id}),
                ]:
                    db.execute(text(sql), params)
                db.commit()

    # ── 29-31. refunds ───────────────────────────────────────────────────────────────────
    def test_refund_event_moves_a_paid_payment_to_refunded(self, ctx):
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.succeeded")
        r = post(client, charge_refund_event(event_id=self._evt_id(ids), intent_id=ids.intent_id,
                                             amount=10000, refunded=10000))
        assert r.status_code == 200 and r.json()["applied"] is True
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "refunded"

    def test_partial_refund_event_moves_to_part_refunded(self, ctx):
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.succeeded")
        r = post(client, charge_refund_event(event_id=self._evt_id(ids), intent_id=ids.intent_id,
                                             amount=10000, refunded=2500))
        assert r.json()["applied"] is True
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "part_refunded"

    def test_duplicate_refund_event_is_harmless(self, ctx):
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.succeeded")
        eid = self._evt_id(ids)
        ev = charge_refund_event(event_id=eid, intent_id=ids.intent_id)
        first, second = post(client, ev), post(client, ev)
        assert first.json()["applied"] is True
        assert second.json()["duplicate"] is True and second.json()["applied"] is False
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "refunded"

    def test_refund_webhook_creates_no_refund_credit(self, ctx):
        """Section 21: a webhook confirms provider evidence; it never authorizes a remedy."""
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.succeeded")
        post(client, charge_refund_event(event_id=self._evt_id(ids), intent_id=ids.intent_id))
        with Session(engine) as db:
            n = db.scalar(text("SELECT count(*) FROM refund_credits WHERE event_order_id=:o")
                          .bindparams(o=ids.order_id))
            assert n == 0

    # ── 22. disputes: the case record and the payment both move ──────────────────────────
    def test_dispute_event_opens_a_case_and_disputes_the_payment(self, ctx):
        """Replaces the old evidence-only expectation. A chargeback used to be filed and
        forgotten, so the ledger read `paid` while the money was contested."""
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.succeeded")
        eid = self._evt_id(ids)
        r = post(client, dispute_event(event_id=eid, intent_id=ids.intent_id,
                                        dispute_id=f"dp_{uuid.uuid4().hex[:10]}"))
        assert r.status_code == 200 and r.json()["applied"] is True
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "disputed"
            rec = db.scalar(select(ProviderEvent).where(ProviderEvent.provider_event_id == eid))
            assert rec.processing_status == "processed"
            assert db.scalar(text("SELECT count(*) FROM payment_disputes WHERE payment_id=:p")
                             .bindparams(p=ids.payment_id)) == 1
            db.execute(text("DELETE FROM payment_disputes WHERE payment_id=:p"),
                        {"p": ids.payment_id})
            db.commit()

    def test_a_dispute_never_rewrites_the_payment_amount(self, ctx):
        """doc P4: a disputed payment does not silently rewrite the original record. Only the
        STATE moves; the amount and currency are untouched."""
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.succeeded")
        with Session(engine) as db:
            before = db.get(Payment, ids.payment_id)
            amount, currency = before.amount, before.currency
        post(client, dispute_event(event_id=self._evt_id(ids), intent_id=ids.intent_id,
                                    dispute_id=f"dp_{uuid.uuid4().hex[:10]}"))
        with Session(engine) as db:
            after = db.get(Payment, ids.payment_id)
            assert after.amount == amount and after.currency == currency
            db.execute(text("DELETE FROM payment_disputes WHERE payment_id=:p"),
                        {"p": ids.payment_id})
            db.commit()

    # ── 25. unknown events ───────────────────────────────────────────────────────────────
    def test_unknown_event_type_mutates_nothing_and_returns_200(self, ctx):
        client, ids = ctx
        eid = self._evt_id(ids)
        event = intent_event(event_id=eid, event_type="some.future.stripe.event",
                             intent_id=ids.intent_id)
        r = post(client, event)
        assert r.status_code == 200 and r.json()["applied"] is False
        with Session(engine) as db:
            assert db.get(Payment, ids.payment_id).state == "pending"
            rec = db.scalar(select(ProviderEvent).where(ProviderEvent.provider_event_id == eid))
            assert rec.processing_status == "processed"
            assert "unhandled_stripe_event_type" in rec.processing_result["reason"]

    # ── 32-35. audit & correlation ───────────────────────────────────────────────────────
    def test_processing_is_audited_under_one_correlation_id(self, ctx):
        client, ids = ctx
        eid = self._evt_id(ids)
        r = self._send(client, ids, event_id=eid)
        corr = r.json()["correlation_id"]
        with Session(engine) as db:
            actions = db.scalars(text("SELECT action FROM audit_logs WHERE correlation_id=:c")
                                 .bindparams(c=corr)).all()
            assert "commercial.provider_event.processed" in actions
            assert "commercial.payment.transition" in actions
            rec = db.scalar(select(ProviderEvent).where(ProviderEvent.provider_event_id == eid))
            assert rec.correlation_id == corr        # one chain: event -> payment -> audit

    def test_invalid_transition_is_audited(self, ctx):
        client, ids = ctx
        self._send(client, ids, event_type="payment_intent.payment_failed")
        self._send(client, ids, event_type="payment_intent.succeeded")
        with Session(engine) as db:
            actions = db.scalars(text("SELECT action FROM audit_logs WHERE org_id=:o")
                                 .bindparams(o=ids.org_id)).all()
            assert "commercial.payment.transition_rejected" in actions
            assert "commercial.provider_event.rejected" in actions

    def test_unmatched_event_is_audited(self, ctx):
        client, ids = ctx
        r = self._send(client, ids, intent_id=f"{ids.miss}{uuid.uuid4().hex[:6]}")
        with Session(engine) as db:
            actions = db.scalars(text(
                "SELECT action FROM audit_logs WHERE correlation_id=:c"
            ).bindparams(c=r.json()["correlation_id"])).all()
            assert "commercial.settlement.unmatched" in actions
            sid = r.json()["result"].get("unmatched_settlement_id")
            if sid:
                db.execute(text("DELETE FROM unmatched_settlements WHERE id=:i"), {"i": sid})
                db.commit()

    def test_evidence_only_event_is_audited(self, ctx):
        client, ids = ctx
        r = post(client, intent_event(event_id=self._evt_id(ids), event_type="customer.updated",
                                      intent_id=ids.intent_id))
        with Session(engine) as db:
            actions = db.scalars(text("SELECT action FROM audit_logs WHERE correlation_id=:c")
                                 .bindparams(c=r.json()["correlation_id"])).all()
            assert "commercial.provider_event.evidence_recorded" in actions

    # ── 36-38. security ──────────────────────────────────────────────────────────────────
    def test_webhook_secret_never_appears_in_stored_evidence(self, ctx):
        client, ids = ctx
        eid = self._evt_id(ids)
        self._send(client, ids, event_id=eid)
        with Session(engine) as db:
            rec = db.scalar(select(ProviderEvent).where(ProviderEvent.provider_event_id == eid))
            blob = json.dumps(rec.payload or {})
            assert WEBHOOK_SECRET not in blob
            assert "whsec_" not in blob

    def test_prohibited_payment_data_is_redacted_before_storage(self, ctx):
        client, ids = ctx
        eid = self._evt_id(ids)
        event = intent_event(event_id=eid, intent_id=ids.intent_id)
        event["data"]["object"]["payment_method_details"] = {
            "card": {"number": "4242424242424242", "cvc": "123", "exp_month": 12,
                     "exp_year": 2030, "last4": "4242"},
        }
        post(client, event)
        with Session(engine) as db:
            rec = db.scalar(select(ProviderEvent).where(ProviderEvent.provider_event_id == eid))
            blob = json.dumps(rec.payload or {})
            assert "4242424242424242" not in blob
            assert '"cvc": "123"' not in blob and '"cvc":"123"' not in blob


# ══════════════════════════════════════════════════════════════════════════════════════
# STRUCTURAL / SECURITY (no DB)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_endpoint_holds_no_commercial_logic():
    from app.routers import commercial as router_mod
    endpoint = next(r.endpoint for r in router_mod.router.routes
                    if r.path == "/commercial/webhooks/stripe")
    src = code_only(endpoint)
    for token in ("payment.state", "apply_payment_state", "CatalogLine", "EventOrder(",
                  "Invoice(", "tax_", "RefundCredit(", "evaluate_readiness", "golive"):
        assert token not in src, f"webhook endpoint contains commercial logic: {token!r}"
    # It must delegate to the shared provider-event ledger.
    assert "ingest_provider_event" in src or "record_provider_event_evidence" in src


def test_no_separate_stripe_event_table_exists():
    """Section 2: provider_events is the one canonical evidence ledger."""
    from app.db import Base
    for name in Base.metadata.tables:
        assert "stripe" not in name.lower(), f"found a Stripe-specific table: {name}"


def test_webhook_secret_is_never_returned_by_any_schema():
    import app.schemas.commercial as sc
    src = code_only(sc).lower()
    for token in ("webhook_secret", "stripe_secret", "api_key"):
        assert token not in src


def test_no_hardcoded_webhook_secret_in_source():
    from app.services import payments_stripe_events as se
    from app.routers import commercial as router_mod
    for module in (se, router_mod):
        src = code_only(module)
        assert "whsec_" not in src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
