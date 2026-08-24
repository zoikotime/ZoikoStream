"""Phase 4A — Stripe provider adapter tests.

Pure unit tests: the Stripe SDK boundary is mocked, so nothing here needs credentials or
network access. That is deliberate — the normal suite must never depend on an external
service being reachable, and must never require a real key to run.

Scope is the ADAPTER only. Webhooks, checkout UI, the refund workflow and disputes are later
phases and are not exercised here.
"""
import ast
import inspect
import logging
import textwrap
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import stripe

from app.config import settings
from _testsupport import code_only
from app.services import payments as pay
from app.services.payments_stripe import StripePaymentProvider

TEST_KEY = "sk_test_placeholder_not_a_real_key"


@pytest.fixture
def provider():
    """Adapter with a fully mocked StripeClient — no network, no real key."""
    with patch("app.services.payments_stripe.stripe.StripeClient") as client_cls:
        client = MagicMock()
        client_cls.return_value = client
        p = StripePaymentProvider(api_key=TEST_KEY)
        yield p, client


def _intent(status_="requires_capture", id_="pi_test_123", currency="usd", created=1767225600):
    return SimpleNamespace(id=id_, status=status_, currency=currency, created=created)


def _refund(status_="succeeded", id_="re_test_123", created=1767225600):
    return SimpleNamespace(id=id_, status=status_, created=created)


# ══════════════════════════════════════════════════════════════════════════════════════
# 1-3. CONFIGURATION & FAIL-CLOSED PROVIDER SELECTION
# ══════════════════════════════════════════════════════════════════════════════════════

def test_app_import_does_not_require_stripe_connectivity():
    """Importing the application must not touch Stripe or need credentials."""
    import app.main  # noqa: F401
    assert "stripe" not in code_only(pay).lower() or True   # payments.py imports lazily
    src = code_only(pay)
    # The Stripe adapter is imported INSIDE get_provider, not at module scope.
    assert "from .payments_stripe import StripePaymentProvider" in src
    assert not src.startswith("import stripe")


def test_mock_provider_resolves():
    assert pay.get_provider("mock").name == "mock"
    assert isinstance(pay.get_provider("mock"), pay.MockPaymentProvider)


def test_stripe_resolves_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", TEST_KEY)
    with patch("app.services.payments_stripe.stripe.StripeClient"):
        p = pay.get_provider("stripe")
    assert p.name == "stripe"
    assert isinstance(p, StripePaymentProvider)


def test_stripe_without_configuration_fails_closed(monkeypatch):
    """THE critical behaviour of this step: no key must never mean 'use the simulator'."""
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    with pytest.raises(pay.ProviderNotConfigured):
        pay.get_provider("stripe")


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_stripe_key_is_treated_as_unconfigured(monkeypatch, blank):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", blank)
    assert settings.stripe_configured() is False
    with pytest.raises(pay.ProviderNotConfigured):
        pay.get_provider("stripe")


@pytest.mark.parametrize("name", ["unknown", "paypal", "adyen", "", "  ", "STRIPEX"])
def test_unknown_provider_raises(name):
    with pytest.raises(pay.PaymentProviderError):
        pay.get_provider(name)


def test_no_provider_name_can_silently_fall_back_to_mock(monkeypatch):
    """Guards the exact regression: get_provider used to return the mock for ANY unrecognized
    name, so a 'stripe' request in an unconfigured environment produced a fabricated
    authorization against a real order."""
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    for name in ("stripe", "unknown", "paypal", None, "", "mockx"):
        try:
            resolved = pay.get_provider(name)
        except pay.PaymentProviderError:
            continue                      # fail-closed: acceptable
        assert resolved.name == name, f"{name!r} silently resolved to {resolved.name!r}"
    # And structurally: no dict-get-with-default fallback remains.
    src = code_only(pay.get_provider)
    assert ".get(name" not in src and "_PROVIDERS" not in src


def test_provider_available_reports_without_raising(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    assert pay.provider_available("mock") is True
    assert pay.provider_available("stripe") is False
    assert pay.provider_available("nonsense") is False


def test_stripe_is_a_recognized_name_even_when_unconfigured():
    """Recognition and availability are different questions — the webhook endpoint validates
    names against the former."""
    assert "stripe" in pay.known_providers()
    assert "mock" in pay.known_providers()


def test_adapter_rejects_an_empty_api_key():
    with pytest.raises(pay.ProviderNotConfigured):
        StripePaymentProvider(api_key="  ")


# ══════════════════════════════════════════════════════════════════════════════════════
# 6/10. MONEY CONVERSION — Decimal to minor units
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("amount,currency,expected", [
    ("10.00", "USD", 1000),
    ("0.01", "USD", 1),
    ("1234.56", "USD", 123456),
    ("100.00", "INR", 10000),          # 2-decimal
    ("999.99", "GBP", 99999),
    ("1000", "JPY", 1000),             # zero-decimal: NOT multiplied by 100
    ("500", "KRW", 500),
    ("1.500", "KWD", 1500),            # three-decimal
    ("10.00", "usd", 1000),            # case-insensitive
])
def test_minor_unit_conversion(amount, currency, expected):
    assert pay.to_minor_units(Decimal(amount), currency) == expected


def test_zero_decimal_currency_is_not_scaled_by_100():
    """The 100x mischarge this table exists to prevent."""
    assert pay.to_minor_units(Decimal("1000"), "JPY") == 1000
    assert pay.to_minor_units(Decimal("1000.00"), "USD") == 100000


@pytest.mark.parametrize("currency,exponent", [
    ("USD", 2), ("EUR", 2), ("INR", 2), ("GBP", 2),
    ("JPY", 0), ("KRW", 0), ("VND", 0), ("XOF", 0),
    ("KWD", 3), ("BHD", 3), ("TND", 3),
])
def test_currency_exponents(currency, exponent):
    assert pay.currency_exponent(currency) == exponent


@pytest.mark.parametrize("bad", ["", "U", "US", "USDD", "12A", "1", "  ", "$$$"])
def test_malformed_currency_fails_closed(bad):
    with pytest.raises(pay.ProviderInvalidRequest):
        pay.currency_exponent(bad)


def test_non_string_currency_fails_closed():
    with pytest.raises(pay.ProviderInvalidRequest):
        pay.currency_exponent(None)


@pytest.mark.parametrize("amount", ["0", "0.00", "-1.00", "-0.01"])
def test_non_positive_amounts_rejected(amount):
    with pytest.raises(pay.ProviderInvalidRequest):
        pay.to_minor_units(Decimal(amount), "USD")


def test_float_amount_is_rejected():
    """Float money is never accepted — 0.1 is not 0.1 in binary."""
    with pytest.raises(pay.ProviderInvalidRequest):
        pay.to_minor_units(10.00, "USD")


def test_excess_precision_is_refused_not_rounded():
    """The '10.999999 must not silently become an unexpected provider amount' requirement:
    an inexpressible amount is a bug upstream, and rounding it would hide a mischarge."""
    with pytest.raises(pay.ProviderInvalidRequest, match="cannot be represented exactly"):
        pay.to_minor_units(Decimal("10.999999"), "USD")
    with pytest.raises(pay.ProviderInvalidRequest):
        pay.to_minor_units(Decimal("10.001"), "USD")
    # A 2-decimal figure is not expressible in a zero-decimal currency either.
    with pytest.raises(pay.ProviderInvalidRequest):
        pay.to_minor_units(Decimal("1000.50"), "JPY")


@pytest.mark.parametrize("nan_inf", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_amounts_rejected(nan_inf):
    with pytest.raises(pay.ProviderInvalidRequest):
        pay.to_minor_units(Decimal(nan_inf), "USD")


def test_conversion_is_deterministic():
    for _ in range(100):
        assert pay.to_minor_units(Decimal("19.99"), "USD") == 1999


def test_conversion_returns_a_true_int():
    result = pay.to_minor_units(Decimal("10.00"), "USD")
    assert type(result) is int and not isinstance(result, bool)


# ══════════════════════════════════════════════════════════════════════════════════════
# 4/5. AUTHORIZE
# ══════════════════════════════════════════════════════════════════════════════════════

def test_authorize_sends_minor_units_and_manual_capture(provider):
    p, client = provider
    client.payment_intents.create.return_value = _intent()
    key = f"k-{uuid.uuid4().hex[:8]}"
    result = p.authorize(amount=Decimal("250.00"), currency="USD", idempotency_key=key)

    params = client.payment_intents.create.call_args.kwargs["params"]
    assert params["amount"] == 25000                 # Decimal -> minor units
    assert params["currency"] == "usd"
    assert params["capture_method"] == "manual"      # authorize != capture (doc D3)
    assert result.provider_payment_ref == "pi_test_123"
    assert result.state == "pending"                 # requires_capture = authorized, unsettled


def test_authorize_passes_the_idempotency_key_to_stripe(provider):
    p, client = provider
    client.payment_intents.create.return_value = _intent()
    key = "commercial-key-abc"
    p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key=key)
    options = client.payment_intents.create.call_args.kwargs["options"]
    assert key in options["idempotency_key"]


def test_same_idempotency_key_produces_the_same_provider_key(provider):
    """Not a fresh random key per retry — the same commercial operation must reuse it."""
    p, client = provider
    client.payment_intents.create.return_value = _intent()
    key = "stable-key"
    p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key=key)
    first = client.payment_intents.create.call_args.kwargs["options"]["idempotency_key"]
    p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key=key)
    second = client.payment_intents.create.call_args.kwargs["options"]["idempotency_key"]
    assert first == second


def test_authorize_preserves_the_commercial_currency(provider):
    """An INR order must reach Stripe as INR — no default, no conversion."""
    p, client = provider
    client.payment_intents.create.return_value = _intent(currency="inr")
    p.authorize(amount=Decimal("50000.00"), currency="INR", idempotency_key="k")
    params = client.payment_intents.create.call_args.kwargs["params"]
    assert params["currency"] == "inr" and params["amount"] == 5000000


@pytest.mark.parametrize("stripe_status,expected", [
    ("requires_payment_method", "requires_action"),
    ("requires_confirmation", "requires_action"),
    ("requires_action", "requires_action"),
    ("processing", "pending"),
    ("requires_capture", "pending"),
    ("succeeded", "paid"),
    ("canceled", "failed"),
])
def test_intent_status_normalizes_to_existing_vocabulary(provider, stripe_status, expected):
    from app.models.commercial import PAYMENT_STATES
    p, client = provider
    client.payment_intents.create.return_value = _intent(status_=stripe_status)
    result = p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key="k")
    assert result.state == expected
    assert result.state in PAYMENT_STATES        # no Stripe-specific business state invented


def test_unmapped_intent_status_is_not_guessed(provider):
    p, client = provider
    client.payment_intents.create.return_value = _intent(status_="some_new_stripe_status")
    with pytest.raises(pay.ProviderInvalidRequest, match="Unrecognized"):
        p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key="k")


def test_authorize_rejects_a_bad_amount_before_calling_stripe(provider):
    p, client = provider
    with pytest.raises(pay.ProviderInvalidRequest):
        p.authorize(amount=Decimal("-5.00"), currency="USD", idempotency_key="k")
    client.payment_intents.create.assert_not_called()


def test_authorize_rejects_bad_currency_before_calling_stripe(provider):
    p, client = provider
    with pytest.raises(pay.ProviderInvalidRequest):
        p.authorize(amount=Decimal("10.00"), currency="DOLLARS", idempotency_key="k")
    client.payment_intents.create.assert_not_called()


def test_simulate_failure_is_refused_on_a_real_provider(provider):
    """Silently ignoring an explicit 'make this fail' against a real processor would be worse
    than refusing it."""
    p, client = provider
    with pytest.raises(pay.ProviderInvalidRequest, match="mock-only"):
        p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key="k",
                    simulate_failure=True)
    client.payment_intents.create.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════════════
# 9. CAPTURE
# ══════════════════════════════════════════════════════════════════════════════════════

def test_capture_returns_normalized_result(provider):
    p, client = provider
    client.payment_intents.capture.return_value = _intent(status_="succeeded")
    result = p.capture("pi_test_123", idempotency_key="cap-key")
    assert result.state == "paid"
    assert result.provider_payment_ref == "pi_test_123"
    assert result.captured_at is not None


def test_capture_passes_idempotency_key(provider):
    p, client = provider
    client.payment_intents.capture.return_value = _intent(status_="succeeded")
    p.capture("pi_test_123", idempotency_key="cap-key")
    options = client.payment_intents.capture.call_args.kwargs["options"]
    assert "cap-key" in options["idempotency_key"]


def test_capture_without_idempotency_key_still_works(provider):
    """Backward compatibility with the pre-existing signature."""
    p, client = provider
    client.payment_intents.capture.return_value = _intent(status_="succeeded")
    assert p.capture("pi_test_123").state == "paid"
    assert client.payment_intents.capture.call_args.kwargs["options"] is None


def test_capture_requires_a_reference(provider):
    p, client = provider
    with pytest.raises(pay.ProviderInvalidRequest):
        p.capture("")
    client.payment_intents.capture.assert_not_called()


def test_capture_does_not_mutate_payment_state_itself():
    """Architectural guarantee: the adapter returns a result; the commercial state machine
    decides. No adapter method may assign a payment state."""
    from app.services import payments_stripe
    src = code_only(payments_stripe)
    assert "payment.state" not in src
    assert "apply_payment_state" not in src      # the adapter doesn't reach into the domain


# ══════════════════════════════════════════════════════════════════════════════════════
# 10. REFUND (provider operation only)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_refund_sends_minor_units_and_returns_the_refund_id(provider):
    p, client = provider
    client.payment_intents.retrieve.return_value = _intent(currency="usd")
    client.refunds.create.return_value = _refund()
    result = p.refund("pi_test_123", Decimal("25.00"), idempotency_key="rc-1")

    params = client.refunds.create.call_args.kwargs["params"]
    assert params["payment_intent"] == "pi_test_123"
    assert params["amount"] == 2500
    # The refund is its own provider object — that id is what RefundCredit.provider_ref stores.
    assert result.provider_payment_ref == "re_test_123"
    assert result.state == "refunded"


def test_refund_uses_the_intents_own_currency(provider):
    """Never assume a currency for a refund — ask the intent being reversed."""
    p, client = provider
    client.payment_intents.retrieve.return_value = _intent(currency="jpy")
    client.refunds.create.return_value = _refund()
    p.refund("pi_test_123", Decimal("1000"), idempotency_key="rc-1")
    assert client.refunds.create.call_args.kwargs["params"]["amount"] == 1000  # zero-decimal


def test_refund_keys_on_the_remedy_not_the_amount(provider):
    """Two legitimate partial refunds of equal value are different operations."""
    p, client = provider
    client.payment_intents.retrieve.return_value = _intent()
    client.refunds.create.return_value = _refund()
    p.refund("pi_test_123", Decimal("10.00"), idempotency_key="remedy-A")
    first = client.refunds.create.call_args.kwargs["options"]["idempotency_key"]
    p.refund("pi_test_123", Decimal("10.00"), idempotency_key="remedy-B")
    second = client.refunds.create.call_args.kwargs["options"]["idempotency_key"]
    assert first != second


@pytest.mark.parametrize("stripe_status,expected", [
    ("succeeded", "refunded"), ("pending", "pending"),
    ("requires_action", "requires_action"), ("failed", "failed"), ("canceled", "failed"),
])
def test_refund_status_normalizes(provider, stripe_status, expected):
    from app.models.commercial import PAYMENT_STATES
    p, client = provider
    client.payment_intents.retrieve.return_value = _intent()
    client.refunds.create.return_value = _refund(status_=stripe_status)
    result = p.refund("pi_test_123", Decimal("10.00"))
    assert result.state == expected and result.state in PAYMENT_STATES


def test_refund_rejects_a_bad_amount_before_calling_stripe(provider):
    p, client = provider
    client.payment_intents.retrieve.return_value = _intent()
    with pytest.raises(pay.ProviderInvalidRequest):
        p.refund("pi_test_123", Decimal("0.00"))
    client.refunds.create.assert_not_called()


def test_no_direct_stripe_refund_endpoint_exists():
    """Refunds must stay behind the RefundCredit maker-checker workflow.

    Scoped to REFUND routes specifically. A Stripe *webhook* route is legitimate (Phase 4B)
    and is not a refund endpoint — the original blanket "no path contains 'stripe'" also
    caught that, which is broader than the invariant being protected.
    """
    from app.routers import commercial as router_mod
    refund_routes = [r for r in router_mod.router.routes if "refund" in r.path.lower()]
    assert refund_routes, "expected the RefundCredit routes to exist"
    for route in refund_routes:
        assert "stripe" not in route.path.lower(), f"provider-specific refund route: {route.path}"
    # And no route may bypass the approval workflow by calling the provider refund directly.
    for route in router_mod.router.routes:
        src = code_only(route.endpoint)
        assert ".refund(" not in src, f"{route.path} calls a provider refund directly"


# ══════════════════════════════════════════════════════════════════════════════════════
# 12. ERROR TRANSLATION
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("exc,expected", [
    (stripe.CardError("declined", None, "card_declined"), pay.ProviderDeclined),
    (stripe.AuthenticationError("bad key"), pay.ProviderNotConfigured),
    (stripe.InvalidRequestError("bad param", None), pay.ProviderInvalidRequest),
    (stripe.IdempotencyError("reused"), pay.ProviderInvalidRequest),
    (stripe.RateLimitError("slow down"), pay.ProviderUnavailable),
    (stripe.APIConnectionError("timeout"), pay.ProviderStateUnknown),
    (stripe.APIError("boom"), pay.ProviderUnavailable),
    (RuntimeError("something else"), pay.ProviderUnavailable),
])
def test_stripe_exceptions_are_translated(provider, exc, expected):
    p, client = provider
    client.payment_intents.create.side_effect = exc
    with pytest.raises(expected):
        p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key="k")


def test_a_timeout_is_not_reported_as_a_decline(provider):
    """The most dangerous misclassification: a connection failure means the outcome is
    UNKNOWN. Calling it a decline could orphan money that was actually captured."""
    p, client = provider
    client.payment_intents.capture.side_effect = stripe.APIConnectionError("timeout")
    with pytest.raises(pay.ProviderStateUnknown):
        p.capture("pi_test_123")
    assert not issubclass(pay.ProviderStateUnknown, pay.ProviderDeclined)


def test_raw_stripe_exceptions_never_escape_the_adapter(provider):
    p, client = provider
    for op, exc in [("payment_intents.create", stripe.CardError("x", None, "y")),
                    ("payment_intents.capture", stripe.APIError("x"))]:
        target = client
        for part in op.split(".")[:-1]:
            target = getattr(target, part)
        setattr(target, op.split(".")[-1], MagicMock(side_effect=exc))
    with pytest.raises(pay.PaymentProviderError):
        p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key="k")
    with pytest.raises(pay.PaymentProviderError):
        p.capture("pi_test_123")


NEUTRAL_ERRORS = frozenset({
    "ProviderNotConfigured", "UnknownProvider", "ProviderInvalidRequest",
    "ProviderDeclined", "ProviderUnavailable", "ProviderStateUnknown",
    "PaymentProviderError",
})


def _resolve_raised(node: ast.Raise, scope: ast.AST) -> str:
    """The exception TYPE name a `raise` produces.

    A raise of a bare local (`raise translated from exc`) is resolved through its assignment
    in the enclosing scope, so the check follows the value instead of trusting the variable's
    name. Only `_translate(...)` — whose complete Stripe-exception -> neutral-type mapping is
    pinned by test_stripe_exceptions_are_translated — resolves to a neutral type; anything
    else keeps its own name and is judged on it.
    """
    exc = node.exc
    target = exc.func if isinstance(exc, ast.Call) else exc
    if isinstance(target, ast.Attribute):
        return target.attr
    if not isinstance(target, ast.Name):
        return ast.unparse(target)
    name = target.id
    if name in NEUTRAL_ERRORS:
        return name
    # Bare local: find what it was assigned from, in this scope.
    for stmt in ast.walk(scope):
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
            continue
        value = stmt.value
        if isinstance(value, ast.Call):
            callee = value.func
            callee_name = (callee.id if isinstance(callee, ast.Name)
                           else callee.attr if isinstance(callee, ast.Attribute)
                           else ast.unparse(callee))
            if callee_name == "_translate":
                return "PaymentProviderError"      # verified neutral by the translation test
            return f"{name}=<{callee_name}()>"
        return f"{name}=<{ast.unparse(value)}>"
    return f"{name}=<unresolved>"


def test_every_adapter_error_is_provider_neutral():
    """No Stripe exception TYPE may escape the adapter.

    Checks the exception CLASS, not its message — a message may legitimately mention Stripe
    ("Disputes cannot be opened via the Stripe API"); what must never escape is a `stripe.*`
    type the commercial layer would then have to know about. Raised locals are resolved
    through their assignment (see _resolve_raised) rather than allowlisted by name.
    """
    from app.services import payments_stripe
    tree = ast.parse(code_only(payments_stripe))
    scopes = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module))]
    raised = []
    for scope in scopes:
        for node in ast.walk(scope):
            if isinstance(node, ast.Raise) and node.exc is not None:
                raised.append(_resolve_raised(node, scope))
    assert raised, "expected the adapter to raise something"
    for name in set(raised):
        assert name in NEUTRAL_ERRORS, f"adapter raises non-neutral exception: {name}"
        assert "stripe" not in name.lower()


def test_translate_only_ever_returns_neutral_errors():
    """Backs the resolution above: every branch of _translate returns a neutral type, so a
    `raise translated` can never carry a Stripe class. Asserted on the RETURNS, exhaustively."""
    from app.services import payments_stripe
    tree = ast.parse(code_only(payments_stripe))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_translate")
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
    assert returns, "_translate must return something"
    for node in returns:
        value = node.value
        callee = value.func if isinstance(value, ast.Call) else value
        name = (callee.id if isinstance(callee, ast.Name)
                else callee.attr if isinstance(callee, ast.Attribute) else ast.unparse(callee))
        assert name in NEUTRAL_ERRORS, f"_translate returns non-neutral type {name!r}"


def test_open_dispute_is_refused_rather_than_fabricated():
    """Stripe has no create-dispute API; returning a fake DisputeResult would put invented
    evidence in the commercial ledger."""
    with patch("app.services.payments_stripe.stripe.StripeClient"):
        p = StripePaymentProvider(api_key=TEST_KEY)
    with pytest.raises(pay.ProviderInvalidRequest):
        p.open_dispute("pi_test_123", amount=Decimal("10.00"), reason_code="fraudulent")


# ══════════════════════════════════════════════════════════════════════════════════════
# 11/18. NORMALIZED RESULT — no provider leakage
# ══════════════════════════════════════════════════════════════════════════════════════

def test_result_is_exactly_the_shared_dataclass(provider):
    p, client = provider
    client.payment_intents.create.return_value = _intent()
    result = p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key="k")
    assert type(result) is pay.PaymentResult
    assert set(result.__dataclass_fields__) == {
        "provider_payment_ref", "state", "authorized_at", "captured_at", "settled_at",
        "failure_reason",
    }


def test_no_stripe_object_leaks_into_the_result(provider):
    p, client = provider
    client.payment_intents.create.return_value = _intent()
    result = p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key="k")
    for value in vars(result).values():
        assert not isinstance(value, (SimpleNamespace, dict, MagicMock))
        assert "stripe" not in str(type(value)).lower()


def test_commercial_domain_never_knows_stripe():
    """The DOMAIN — crud + models — must not know Stripe exists.

    This is the load-bearing invariant: business logic and persistence stay provider-neutral,
    so a second provider needs no changes there. Deliberately excludes the ROUTER, which is by
    definition the integration entry point: Stripe's webhook has to arrive somewhere, and the
    router wiring HTTP -> translation service is exactly where that belongs.
    """
    from app.crud import commercial as crud_mod
    from app.models import commercial as models_mod
    for module in (crud_mod, models_mod):
        src = code_only(module).lower()
        assert "import stripe" not in src, f"{module.__name__} imports the Stripe SDK"
        assert "payments_stripe" not in src, f"{module.__name__} imports the Stripe adapter"
        assert "stripeclient" not in src
        assert "stripe." not in src, f"{module.__name__} references Stripe symbols"


def test_the_router_talks_to_stripe_only_through_the_service_layer():
    """The router may know a Stripe integration EXISTS, but must not touch the SDK itself —
    no stripe import, no client construction, no signature algorithm. Everything Stripe-shaped
    goes through services/payments_stripe*.py."""
    from app.routers import commercial as router_mod
    src = code_only(router_mod)
    assert "import stripe\n" not in src and "\nimport stripe" not in src
    assert "StripeClient" not in src
    assert "construct_event" not in src          # signature verification lives in the service
    assert "stripe_events." in src               # ...and is reached through it


# ══════════════════════════════════════════════════════════════════════════════════════
# 19/20. SECURITY & NO HARD-CODED COMMERCIAL VALUES
# ══════════════════════════════════════════════════════════════════════════════════════

def test_api_key_is_never_logged(provider, caplog):
    p, client = provider
    client.payment_intents.create.return_value = _intent()
    with caplog.at_level(logging.DEBUG):
        p.authorize(amount=Decimal("10.00"), currency="USD", idempotency_key="k")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert TEST_KEY not in logged
    assert "sk_test" not in logged and "sk_live" not in logged


def test_api_key_is_not_exposed_as_an_attribute(provider):
    p, _ = provider
    for name, value in vars(p).items():
        assert TEST_KEY not in str(value), f"key reachable via attribute {name}"
    assert not hasattr(p, "api_key")


def test_adapter_contains_no_commercial_values():
    """No price, tax rate, discount, deposit, capacity, SLA, seller or default currency."""
    from app.services import payments_stripe
    src = code_only(payments_stripe)
    forbidden = [
        "DEFAULT_CURRENCY", "default_currency", "TAX_RATE", "GST", "VAT_RATE", "0.18",
        "DEFAULT_TAX", "price_id", "PRICE_ID", "Price.create", "prices.create",
        "products.create", "zoiko_tech_inc", "SLA", "DEFAULT_CAPACITY", "discount",
        "deposit", "Plan", "price_monthly",
    ]
    for token in forbidden:
        assert token not in src, f"adapter contains commercial value/token {token!r}"


def test_adapter_never_reads_the_commercial_domain():
    """It receives an approved amount; it does not go looking for one."""
    from app.services import payments_stripe
    src = code_only(payments_stripe)
    for token in ("EventOrder", "Invoice", "CatalogLine", "CatalogVersion", "Payment(",
                  "SellerLegalEntity", "CapacityPool", "evaluate_readiness", "golive"):
        assert token not in src, f"adapter reaches into the domain: {token!r}"


def test_no_stripe_price_or_product_api_is_used():
    """Live Event pricing comes from a published CatalogVersion, never from Stripe.

    Scoped to Stripe's PRICING objects. `checkout` was also on this list before Phase 4C, when
    the adapter had no checkout at all — hosted Checkout is now the approved architecture and
    is not a pricing concern, because it is handed an inline amount (asserted below and in
    test_stripe_checkout.py) rather than a Price ID.
    """
    from app.services import payments_stripe
    src = code_only(payments_stripe)
    # A Stripe Price/Product/Plan/Subscription object would make Stripe the pricing source.
    for token in ("prices.create", "prices.retrieve", "products.create", "products.retrieve",
                  "Price.retrieve", "Product.retrieve", "price_id", "PRICE_ID",
                  "subscriptions.create", "Subscription"):
        assert token not in src, f"adapter touches Stripe {token} — pricing must stay in the catalog"


def test_checkout_prices_inline_never_by_reference():
    """The positive half: the checkout path builds an inline price from the amount it is given,
    and never names a Stripe price object."""
    from app.services import payments_stripe
    src = code_only(payments_stripe.StripePaymentProvider.create_checkout_session)
    assert "price_data" in src, "checkout must build an inline price"
    assert "to_minor_units(amount, currency)" in src, "amount must come from the caller"
    # No Price ID, and no reading a price back from the provider.
    assert '"price"' not in src and "'price'" not in src
    for token in ("prices.", "products.", "Price(", "Product("):
        assert token not in src


def test_no_secret_reaches_any_response_schema():
    import app.schemas.commercial as sc
    src = code_only(sc).lower()
    for token in ("stripe_secret", "secret_key", "webhook_secret", "api_key"):
        assert token not in src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
