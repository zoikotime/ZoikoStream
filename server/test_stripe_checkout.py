"""Phase 4C — Stripe hosted Checkout backend tests.

The Stripe SDK boundary is mocked, so nothing here needs credentials or network. Assertions
check the EXACT arguments handed to Stripe (amount, currency, capture behaviour, metadata,
idempotency key, mode, URLs) rather than merely that a call happened — "it was called" would
pass even if we sent the wrong amount.

`needs_db` tests build real commercial records so the amount/currency genuinely come from the
database chain, and skip when DATABASE_URL is unreachable.
"""
import concurrent.futures
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import stripe
from sqlalchemy import text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.crud import commercial as crud
from app.db import engine
from app.models import (
    CatalogLine, CatalogVersion, CommercialAccount, Event, EventOrder, Organization,
    Payment, User,
)
from app.services import payments as pay
from app.services.payments_stripe import StripePaymentProvider

TEST_KEY = "sk_test_placeholder_not_a_real_key"
SUCCESS_URL = "https://app.test/organization/events/e1?tab=commercial&checkout=success"
CANCEL_URL = "https://app.test/organization/events/e1?tab=commercial&checkout=cancelled"


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


def _session(id_="cs_test_1", url="https://checkout.stripe.com/c/pay/cs_test_1",
             payment_intent=None, expires_at=None):
    """A freshly created Stripe Checkout Session.

    `payment_intent` defaults to None because that is what Stripe ACTUALLY returns at session
    creation — the PaymentIntent does not exist until the payer submits. This stub previously
    defaulted to "pi_test_1", which no real Stripe response ever contains at this point, and
    that fiction is what allowed a P0 (every real checkout attempt rejected) to pass 57 tests.
    Tests that need a bound reference now have to say so explicitly.
    """
    return SimpleNamespace(id=id_, url=url, payment_intent=payment_intent,
                           expires_at=expires_at or 1767225600)


@pytest.fixture
def provider():
    with patch("app.services.payments_stripe.stripe.StripeClient") as client_cls:
        client = MagicMock()
        client_cls.return_value = client
        yield StripePaymentProvider(api_key=TEST_KEY), client


def _create(p, client, *, amount="250.00", currency="USD", key="k1", metadata=None):
    client.checkout.sessions.create.return_value = _session()
    return p.create_checkout_session(
        amount=Decimal(amount), currency=currency, idempotency_key=key,
        success_url=SUCCESS_URL, cancel_url=CANCEL_URL, description="Live Event order 1",
        metadata=metadata if metadata is not None else {"zoiko_event_order_id": "o1"},
    )


def _params(client) -> dict:
    return client.checkout.sessions.create.call_args.kwargs["params"]


# ══════════════════════════════════════════════════════════════════════════════════════
# 1-5, 21, 25. SESSION CONFIGURATION — exact arguments sent to Stripe
# ══════════════════════════════════════════════════════════════════════════════════════

def test_valid_checkout_session_is_created(provider):
    p, client = provider
    result = _create(p, client)
    assert result.checkout_session_ref == "cs_test_1"
    assert result.checkout_url.startswith("https://checkout.stripe.com/")
    # No payment reference at creation, because Stripe does not supply one until the payer
    # submits. The session reference is what makes this reconcilable, and it is present.
    assert result.provider_payment_ref is None


def test_mode_is_payment(provider):
    p, client = provider
    _create(p, client)
    assert _params(client)["mode"] == "payment"


def test_authoritative_amount_is_sent_in_minor_units(provider):
    p, client = provider
    _create(p, client, amount="250.00", currency="USD")
    item = _params(client)["line_items"][0]
    assert item["price_data"]["unit_amount"] == 25000
    assert item["quantity"] == 1


def test_authoritative_currency_is_sent(provider):
    p, client = provider
    _create(p, client, amount="50000.00", currency="INR")
    price_data = _params(client)["line_items"][0]["price_data"]
    assert price_data["currency"] == "inr"
    assert price_data["unit_amount"] == 5000000


def test_zero_decimal_currency_is_not_scaled(provider):
    p, client = provider
    _create(p, client, amount="1000", currency="JPY")
    assert _params(client)["line_items"][0]["price_data"]["unit_amount"] == 1000


def test_manual_capture_is_configured(provider):
    """Keeps authorization and capture distinct (doc D3). Verified as supported by this SDK's
    Checkout params: Literal["automatic","automatic_async","manual"]."""
    p, client = provider
    _create(p, client)
    assert _params(client)["payment_intent_data"]["capture_method"] == "manual"


def test_no_stripe_price_id_is_used(provider):
    """Pricing must stay in the published CatalogVersion — a Price ID would move it to Stripe."""
    p, client = provider
    _create(p, client)
    item = _params(client)["line_items"][0]
    assert "price" not in item, "used a Stripe Price ID instead of inline price_data"
    assert "price_data" in item
    # And no Product/Price object is created on Stripe's side.
    client.prices.create.assert_not_called()
    client.products.create.assert_not_called()


def test_success_and_cancel_urls_are_sent(provider):
    p, client = provider
    _create(p, client)
    params = _params(client)
    assert params["success_url"] == SUCCESS_URL
    assert params["cancel_url"] == CANCEL_URL


def test_missing_return_urls_are_refused(provider):
    p, client = provider
    for kwargs in ({"success_url": ""}, {"cancel_url": "  "}):
        with pytest.raises(pay.ProviderInvalidRequest):
            p.create_checkout_session(
                amount=Decimal("10.00"), currency="USD", idempotency_key="k",
                success_url=kwargs.get("success_url", SUCCESS_URL),
                cancel_url=kwargs.get("cancel_url", CANCEL_URL),
                description="d", metadata={})
    client.checkout.sessions.create.assert_not_called()


# ── 13/16. metadata ───────────────────────────────────────────────────────────────────────

def test_metadata_carries_safe_internal_references(provider):
    p, client = provider
    meta = {"zoiko_event_order_id": "o-1", "zoiko_correlation_id": "corr-1",
            "zoiko_order_version": "2"}
    _create(p, client, metadata=meta)
    params = _params(client)
    assert params["metadata"] == meta
    # Mirrored onto the PaymentIntent, because session metadata does NOT propagate to the
    # intent — without this, payment_intent.* events would carry no reference to us.
    assert params["payment_intent_data"]["metadata"] == meta


def test_metadata_values_are_stringified(provider):
    p, client = provider
    _create(p, client, metadata={"a": 1, "b": uuid.uuid4()})
    for value in _params(client)["metadata"].values():
        assert isinstance(value, str)


def test_metadata_drops_none_values(provider):
    p, client = provider
    _create(p, client, metadata={"keep": "x", "drop": None})
    assert "drop" not in _params(client)["metadata"]


def test_metadata_contains_no_secrets(provider):
    p, client = provider
    _create(p, client)
    blob = str(_params(client)["metadata"])
    for forbidden in ("sk_test", "sk_live", "whsec_", TEST_KEY):
        assert forbidden not in blob


# ── 13/14. idempotency ────────────────────────────────────────────────────────────────────

def test_idempotency_key_is_passed_to_stripe(provider):
    p, client = provider
    _create(p, client, key="commercial-key-1")
    assert "commercial-key-1" in client.checkout.sessions.create.call_args.kwargs["options"]["idempotency_key"]


def test_same_key_produces_the_same_provider_identity(provider):
    p, client = provider
    _create(p, client, key="stable")
    first = client.checkout.sessions.create.call_args.kwargs["options"]["idempotency_key"]
    _create(p, client, key="stable")
    second = client.checkout.sessions.create.call_args.kwargs["options"]["idempotency_key"]
    assert first == second


# ── 10/11. amount validation & state ──────────────────────────────────────────────────────

@pytest.mark.parametrize("amount", ["0", "-1.00"])
def test_invalid_amount_is_refused_before_calling_stripe(provider, amount):
    p, client = provider
    with pytest.raises(pay.ProviderInvalidRequest):
        p.create_checkout_session(amount=Decimal(amount), currency="USD", idempotency_key="k",
                                   success_url=SUCCESS_URL, cancel_url=CANCEL_URL,
                                   description="d", metadata={})
    client.checkout.sessions.create.assert_not_called()


def test_unrepresentable_amount_is_refused(provider):
    p, client = provider
    with pytest.raises(pay.ProviderInvalidRequest):
        p.create_checkout_session(amount=Decimal("10.999"), currency="USD", idempotency_key="k",
                                   success_url=SUCCESS_URL, cancel_url=CANCEL_URL,
                                   description="d", metadata={})
    client.checkout.sessions.create.assert_not_called()


def test_bad_currency_is_refused_before_calling_stripe(provider):
    p, client = provider
    with pytest.raises(pay.ProviderInvalidRequest):
        p.create_checkout_session(amount=Decimal("10.00"), currency="DOLLARS",
                                   idempotency_key="k", success_url=SUCCESS_URL,
                                   cancel_url=CANCEL_URL, description="d", metadata={})
    client.checkout.sessions.create.assert_not_called()


def test_session_creation_never_returns_a_settled_state(provider):
    """Creating a session collects nothing — it must never look paid."""
    p, client = provider
    result = _create(p, client)
    assert result.state == "requires_action"
    assert result.state not in ("paid", "partially_paid")


def test_session_without_a_url_is_refused(provider):
    p, client = provider
    client.checkout.sessions.create.return_value = _session(url=None)
    with pytest.raises(pay.ProviderInvalidRequest, match="no URL"):
        p.create_checkout_session(amount=Decimal("10.00"), currency="USD", idempotency_key="k",
                                   success_url=SUCCESS_URL, cancel_url=CANCEL_URL,
                                   description="d", metadata={})


def test_expanded_payment_intent_object_is_flattened_to_its_id(provider):
    p, client = provider
    client.checkout.sessions.create.return_value = _session(
        payment_intent=SimpleNamespace(id="pi_expanded"))
    result = p.create_checkout_session(amount=Decimal("10.00"), currency="USD",
                                       idempotency_key="k", success_url=SUCCESS_URL,
                                       cancel_url=CANCEL_URL, description="d", metadata={})
    assert result.provider_payment_ref == "pi_expanded"


# ── 15. provider errors ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("exc,expected", [
    (stripe.AuthenticationError("bad key"), pay.ProviderNotConfigured),
    (stripe.InvalidRequestError("bad param", None), pay.ProviderInvalidRequest),
    (stripe.RateLimitError("slow"), pay.ProviderUnavailable),
    (stripe.APIConnectionError("timeout"), pay.ProviderStateUnknown),
    (stripe.APIError("boom"), pay.ProviderUnavailable),
])
def test_stripe_errors_are_translated(provider, exc, expected):
    p, client = provider
    client.checkout.sessions.create.side_effect = exc
    with pytest.raises(expected):
        p.create_checkout_session(amount=Decimal("10.00"), currency="USD", idempotency_key="k",
                                   success_url=SUCCESS_URL, cancel_url=CANCEL_URL,
                                   description="d", metadata={})


def test_no_raw_stripe_exception_escapes(provider):
    p, client = provider
    client.checkout.sessions.create.side_effect = stripe.APIError("boom")
    with pytest.raises(pay.PaymentProviderError):
        _create(p, client)


# ── mock provider parity ──────────────────────────────────────────────────────────────────

def test_mock_provider_implements_checkout_with_the_same_validation():
    m = pay.MockPaymentProvider()
    r = m.create_checkout_session(amount=Decimal("10.00"), currency="USD", idempotency_key="k",
                                  success_url=SUCCESS_URL, cancel_url=CANCEL_URL,
                                  description="d", metadata={})
    assert r.state == "requires_action" and r.checkout_url and r.provider_payment_ref
    # Same fail-closed amount validation as production, so the mock is not a laxer path.
    with pytest.raises(pay.ProviderInvalidRequest):
        m.create_checkout_session(amount=Decimal("-1"), currency="USD", idempotency_key="k",
                                   success_url=SUCCESS_URL, cancel_url=CANCEL_URL,
                                   description="d", metadata={})


def test_authorize_capture_refund_signatures_unchanged():
    """Phase 4C added a method; it must not have altered the existing three."""
    import inspect as _i
    assert list(_i.signature(pay.PaymentProvider.authorize).parameters) == [
        "self", "amount", "currency", "idempotency_key", "simulate_failure"]
    assert list(_i.signature(pay.PaymentProvider.capture).parameters) == [
        "self", "provider_payment_ref", "idempotency_key"]
    assert list(_i.signature(pay.PaymentProvider.refund).parameters) == [
        "self", "provider_payment_ref", "amount", "idempotency_key"]


# ── 19/20. no secrets, no leaked provider object ──────────────────────────────────────────

def test_checkout_result_leaks_no_provider_object(provider):
    p, client = provider
    result = _create(p, client)
    assert type(result) is pay.CheckoutSessionResult
    assert set(result.__dataclass_fields__) == {
        "checkout_session_ref", "checkout_url", "provider_payment_ref", "state", "expires_at"}
    for value in vars(result).values():
        assert not isinstance(value, (MagicMock, SimpleNamespace))


def test_api_key_never_logged_during_checkout(provider, caplog):
    import logging
    p, client = provider
    with caplog.at_level(logging.DEBUG):
        _create(p, client)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert TEST_KEY not in logged and "sk_test" not in logged


def test_checkout_response_schema_exposes_no_secret():
    import app.schemas.commercial as sc
    src = code_only(sc).lower()
    for token in ("secret", "api_key", "whsec"):
        assert token not in src


# ══════════════════════════════════════════════════════════════════════════════════════
# AMOUNT/CURRENCY FROM THE DATABASE + ENDPOINT BEHAVIOUR (real Postgres)
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestCheckoutAgainstRealCommercialRecords:

    @pytest.fixture
    def ctx(self):
        """A real priced, accepted, tax-determined order — so the amount genuinely comes from
        CatalogLine -> EventOrderLine -> EventOrder.total_amount."""
        with Session(engine) as db:
            org = Organization(name=f"co-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="CO", email=f"co-{uuid.uuid4().hex[:8]}@t.test",
                        username=f"co{uuid.uuid4().hex[:8]}", password_hash="x",
                        role="org_admin")
            other_user = User(org_id=org.id, full_name="Fin",
                              email=f"fin-{uuid.uuid4().hex[:8]}@t.test",
                              username=f"fin{uuid.uuid4().hex[:8]}", password_hash="x",
                              role="super_admin")
            db.add_all([user, other_user])
            db.flush()
            event = Event(org_id=org.id, created_by=user.id, title="Checkout test")
            account = CommercialAccount(org_id=org.id)
            catalog = CatalogVersion(version_label="v1", vertical=f"co-{uuid.uuid4().hex[:6]}",
                                     status="published")
            db.add_all([event, account, catalog])
            db.flush()
            line = CatalogLine(catalog_version_id=catalog.id, service_code="MEM-MANAGED",
                               name="Managed memorial", unit_price=Decimal("1200.00"),
                               currency="USD", unit_basis="per_event",
                               tax_treatment="standard_rate")
            db.add(line)
            db.flush()
            order = crud.create_order(
                db, event, commercial_account=account, catalog_version=catalog,
                purchaser_type="organization", purchaser_id=user.id, service_profile=None,
                cancellation_policy=None, currency="USD",
                idempotency_key=f"ord-{uuid.uuid4().hex[:10]}")
            crud.add_order_line(db, order, line, quantity=Decimal(1))
            crud.submit_order_for_acceptance(db, order)
            crud.accept_order(db, order, other_user, terms_version="tos-v1")
            crud.record_tax_determination(
                db, order, other_user, tax_amount=Decimal("240.00"), treatment="standard_rate",
                jurisdiction="GB", source="finance_manual")
            db.refresh(order)
            ids = SimpleNamespace(org_id=org.id, user_id=user.id, staff_id=other_user.id,
                                  event_id=event.id, order_id=order.id, account_id=account.id,
                                  catalog_id=catalog.id, line_id=line.id,
                                  expected_total=order.total_amount)
        yield ids
        with Session(engine) as db:
            for sql, params in [
                ("DELETE FROM provider_events WHERE payment_id IN "
                 "(SELECT id FROM payments WHERE event_order_id=:o)", {"o": ids.order_id}),
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
                ("DELETE FROM organizations WHERE id=:g", {"g": ids.org_id}),
            ]:
                db.execute(text(sql), params)
            db.commit()

    def test_amount_comes_from_the_commercial_chain(self, ctx):
        """1200.00 line + 240.00 determined tax = 1440.00 — no client input anywhere."""
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            amount, currency = crud.order_payable_amount(db, order)
            assert order.subtotal == Decimal("1200.00")
            assert order.tax_amount == Decimal("240.00")
            assert amount == Decimal("1440.00") == order.total_amount
            assert currency == "USD"

    def test_outstanding_excludes_money_already_captured(self, ctx):
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            db.add(Payment(event_order_id=order.id, provider="mock",
                           provider_payment_ref=f"r{uuid.uuid4().hex[:8]}",
                           amount=Decimal("440.00"), currency="USD", state="paid",
                           idempotency_key=str(uuid.uuid4())))
            db.commit()
            amount, _ = crud.order_payable_amount(db, order)
            assert amount == Decimal("1000.00")

    def test_fully_paid_order_is_refused(self, ctx):
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            db.add(Payment(event_order_id=order.id, provider="mock",
                           provider_payment_ref=f"r{uuid.uuid4().hex[:8]}",
                           amount=order.total_amount, currency="USD", state="paid",
                           idempotency_key=str(uuid.uuid4())))
            db.commit()
            with pytest.raises(ValueError, match="already paid in full"):
                crud.order_payable_amount(db, order)

    def test_tax_not_determined_blocks_checkout(self, ctx):
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            crud._clear_tax_determination(order)
            db.commit()
            with pytest.raises(ValueError, match="no tax determination"):
                crud.order_payable_amount(db, order)

    @pytest.mark.parametrize("bad_status", ["draft", "pending_acceptance"])
    def test_unaccepted_order_is_refused(self, ctx, bad_status):
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            order.status = bad_status
            db.commit()
            with pytest.raises(ValueError, match="accepted"):
                crud.order_payable_amount(db, order)

    @pytest.mark.parametrize("bad_status", ["canceled", "terminated"])
    def test_cancelled_order_is_refused(self, ctx, bad_status):
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            order.status = bad_status
            db.commit()
            with pytest.raises(ValueError, match="cannot be paid"):
                crud.order_payable_amount(db, order)

    def test_a_persisted_order_cannot_lack_a_currency(self, ctx):
        """Stronger than the application guard: the DB itself forbids it, so the
        "missing currency" state is unreachable for a persisted order (doc L2)."""
        with Session(engine) as db:
            with pytest.raises(Exception) as exc:
                db.execute(text("UPDATE event_orders SET currency = NULL WHERE id = :o"),
                           {"o": ctx.order_id})
                db.commit()
            assert "not-null" in str(exc.value).lower() or "null value" in str(exc.value).lower()
            db.rollback()

    # ── checkout start: payment record + idempotency ───────────────────────────────────
    def _start(self, db, ctx, provider_name="mock"):
        return crud.start_hosted_checkout(
            db, db.get(EventOrder, ctx.order_id), actor=db.get(User, ctx.user_id),
            success_url=SUCCESS_URL, cancel_url=CANCEL_URL, provider_name=provider_name)

    def test_checkout_creates_an_unpaid_payment_with_the_authoritative_amount(self, ctx):
        with Session(engine) as db:
            result = self._start(db, ctx)
            payment = result["payment"]
            assert payment.amount == ctx.expected_total == Decimal("1440.00")
            assert payment.currency == "USD"
            # NOT paid — creating a session collects nothing.
            assert payment.state == "requires_action"
            assert payment.state not in ("paid", "partially_paid")
            assert result["checkout_url"]

    def test_repeated_pay_clicks_create_one_payment(self, ctx):
        with Session(engine) as db:
            first = self._start(db, ctx)
            second = self._start(db, ctx)
            assert second["reused"] is True
            assert second["payment"].id == first["payment"].id
            n = db.scalar(text("SELECT count(*) FROM payments WHERE event_order_id=:o")
                          .bindparams(o=ctx.order_id))
            assert n == 1

    def test_concurrent_pay_clicks_create_one_payment(self, ctx):
        def click():
            with Session(engine) as db:
                try:
                    r = self._start(db, ctx)
                    return "ok" if not r["reused"] else "reused"
                except Exception as e:
                    return f"err:{type(e).__name__}"

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = [f.result() for f in [pool.submit(click) for _ in range(4)]]

        with Session(engine) as db:
            n = db.scalar(text("SELECT count(*) FROM payments WHERE event_order_id=:o")
                          .bindparams(o=ctx.order_id))
        assert n == 1, f"{n} payments created; results={results}"

    def test_checkout_is_audited(self, ctx):
        with Session(engine) as db:
            result = self._start(db, ctx)
            actions = db.scalars(text("SELECT action FROM audit_logs WHERE correlation_id=:c")
                                 .bindparams(c=result["correlation_id"])).all()
            assert "commercial.payment.checkout_started" in actions

    def test_unconfigured_provider_fails_closed(self, ctx, monkeypatch):
        from app.config import settings as app_settings
        monkeypatch.setattr(app_settings, "STRIPE_SECRET_KEY", "")
        with Session(engine) as db:
            with pytest.raises(pay.ProviderNotConfigured):
                self._start(db, ctx, provider_name="stripe")
            n = db.scalar(text("SELECT count(*) FROM payments WHERE event_order_id=:o")
                          .bindparams(o=ctx.order_id))
            assert n == 0, "a payment was created despite the provider being unavailable"


# ══════════════════════════════════════════════════════════════════════════════════════
# ENDPOINT: schema, tenancy, no client-supplied financials
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("field,value,match", [
    ("currency", None, "no currency"),
    ("tax_amount", None, "no tax determination"),
    ("total_amount", Decimal("0"), "no positive payable total"),
    ("total_amount", None, "no positive payable total"),
])
def test_amount_guards_are_defence_in_depth(field, value, match):
    """The application guards, exercised directly on an in-memory order. Some of these states
    are unreachable for a PERSISTED order because the schema forbids them (see
    test_a_persisted_order_cannot_lack_a_currency) — the guards still exist so a detached or
    partially-built order cannot slip through."""
    order = SimpleNamespace(
        id=uuid.uuid4(), status="accepted", currency="USD",
        subtotal=Decimal("1200.00"), tax_amount=Decimal("240.00"),
        total_amount=Decimal("1440.00"), order_version=1,
    )
    setattr(order, field, value)
    with pytest.raises(ValueError, match=match):
        crud.order_payable_amount(SimpleNamespace(), order)


def test_request_schema_accepts_no_financial_fields():
    """The client cannot express an amount, currency, tax, discount or seller."""
    from app.schemas.commercial import CheckoutSessionCreate
    fields = set(CheckoutSessionCreate.model_fields)
    for forbidden in ("amount", "currency", "tax", "tax_amount", "discount", "seller",
                      "seller_legal_entity_id", "total", "price"):
        assert forbidden not in fields, f"client can supply {forbidden!r}"
    assert fields == {"provider_name"}


def test_client_supplied_financials_are_ignored_not_honoured():
    """Even if a caller sends them, they cannot reach the domain."""
    from app.schemas.commercial import CheckoutSessionCreate
    parsed = CheckoutSessionCreate.model_validate(
        {"provider_name": "stripe", "amount": "1.00", "currency": "JPY", "tax_amount": "0"})
    assert not hasattr(parsed, "amount")
    assert not hasattr(parsed, "currency")


def test_endpoint_is_org_scoped_and_reads_the_amount_server_side():
    from app.routers import commercial as router_mod
    endpoint = next(r.endpoint for r in router_mod.router.routes
                    if r.path == "/commercial/orders/{order_id}/payments/checkout-session")
    src = code_only(endpoint)
    assert "_get_order_or_404" in src, "endpoint is not org-scoped"
    assert "start_hosted_checkout" in src
    # Return URLs are built from configured APP_URL, never taken from the request.
    assert "settings.APP_URL" in src
    assert "data.success_url" not in src and "data.cancel_url" not in src


def test_endpoint_response_contains_no_provider_secret():
    from app.schemas.commercial import CheckoutSessionOut
    fields = set(CheckoutSessionOut.model_fields)
    assert fields == {"checkout_url", "payment_id", "amount", "currency", "state", "reused"}
    for forbidden in ("secret", "api_key", "session_object", "raw"):
        assert not any(forbidden in f for f in fields)


def test_commercial_domain_still_has_no_stripe_knowledge():
    """Phase 4C must not have leaked Stripe into the domain."""
    from app.crud import commercial as crud_mod
    from app.models import commercial as models_mod
    for module in (crud_mod, models_mod):
        src = code_only(module).lower()
        assert "import stripe" not in src
        assert "payments_stripe" not in src
        assert "checkout.stripe.com" not in src


def test_no_hardcoded_commercial_values_in_the_checkout_path():
    from app.services import payments_stripe
    src = code_only(payments_stripe)
    for token in ("prices.create", "products.create",
                  "DEFAULT_CURRENCY", "TAX_RATE", "GST", "0.18", "zoiko_tech_inc",
                  "DEFAULT_CAPACITY", "price_monthly"):
        assert token not in src, f"hardcoded commercial value: {token!r}"

    # The Live Event checkout path itself stays Price-ID-free — its amount comes from the
    # catalog. Ledger 1's subscription checkout takes a Price ID by design (it is the approved
    # commercial fact), but it must never carry a hardcoded ONE: assert it only ever receives
    # the id as a parameter, with no literal Stripe price in the source.
    import re
    ledger2 = code_only(payments_stripe.StripePaymentProvider.create_checkout_session)
    assert "price_id" not in ledger2, "Live Event checkout must not take a Stripe Price ID"
    assert not re.search(r"price_[A-Za-z0-9]{6,}", src),         "a literal Stripe Price ID is hardcoded in the adapter"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
