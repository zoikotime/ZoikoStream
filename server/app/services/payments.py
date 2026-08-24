"""Payment provider abstraction for Live Event orders (ZST-LE-COM-001 Section 20/P).

Processor-neutral by design (doc P1: "Architecture is processor-neutral; production
providers must be approved"). MockPaymentProvider is the ONLY implementation until a real
merchant account exists — see Section 26's "Merchant & finance" go-live gate ("Production
payment provider account and webhook signing configured"), which is deliberately left
unchecked by this module. It never contacts a network; it deterministically simulates
authorize -> capture -> refund so the rest of the commercial engine (capacity, readiness,
invoicing) can be built and exercised end-to-end without a live Stripe/Adyen/etc. account
and without ever moving real money.

Swapping in a real provider later means adding a class implementing PaymentProvider and
extending _PROVIDERS — crud/commercial.py only ever talks to this interface, never to a
concrete provider, so that swap should not touch business logic.
"""

from __future__ import annotations

import hmac
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


# ── Provider-neutral errors (doc P1: normalize provider behaviour) ───────────────────────
# A concrete provider's exception types must never reach the commercial layer, so every
# adapter translates into this hierarchy. The distinction that matters most for money is
# ProviderStateUnknown: a timeout is NOT a decline, and must never be recorded as one.

class PaymentProviderError(Exception):
    """Base for every provider failure the commercial layer may see."""


class ProviderNotConfigured(PaymentProviderError):
    """The requested provider exists but this deployment has no credentials for it.
    Deliberately NOT a fallback to the mock provider — see get_provider."""


class UnknownProvider(PaymentProviderError):
    """No adapter is registered under that name."""


class ProviderInvalidRequest(PaymentProviderError):
    """We asked for something the provider rejects as malformed/impossible. A bug on our
    side, not a payer decline — retrying unchanged will not help."""


class ProviderDeclined(PaymentProviderError):
    """The provider definitively refused the payment (e.g. card declined). Terminal for this
    attempt, and safe to record as a failure because the outcome is known."""


class ProviderUnavailable(PaymentProviderError):
    """A transient provider-side problem (rate limit, 5xx). Retryable; the operation did NOT
    take effect."""


class ProviderStateUnknown(PaymentProviderError):
    """The request may or may not have taken effect — a connection timeout with no response.

    The single most dangerous case in a payment integration: recording this as FAILED can
    orphan money that was actually captured, and recording it as PAID can invent money that
    was not. Callers must leave the payment where it is and reconcile from provider evidence
    (the provider's own webhook / a settlement report) rather than guessing."""


# ── Money: application Decimal -> provider minor units ───────────────────────────────────
# Most providers (Stripe included) take integer minor units, so this conversion sits between
# the commercial Decimal and any adapter. It is provider-neutral on purpose: getting an
# exponent wrong is a 100x mischarge, so this is the one place the rule lives.
#
# Exponents are ISO 4217 facts, not commercial values — the same category as "there are 60
# seconds in a minute". No price, rate or default currency is expressed here.

# ISO 4217 zero-decimal currencies (also Stripe's documented zero-decimal set).
_ZERO_DECIMAL_CURRENCIES = frozenset({
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF",
    "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
})
# ISO 4217 three-decimal currencies.
_THREE_DECIMAL_CURRENCIES = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})


def currency_exponent(currency: str) -> int:
    """Minor-unit exponent for an ISO 4217 alphabetic code.

    Fails closed on anything that is not a well-formed 3-letter code, so a typo or a
    caller-supplied blank can never be silently treated as a 2-decimal currency. Codes
    outside the two exception sets are 2 by ISO 4217 — that is the standard's rule, not a
    guess, and the exception sets above are the complete deviations from it.
    """
    if not isinstance(currency, str):
        raise ProviderInvalidRequest("Currency must be a string ISO 4217 code")
    code = currency.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ProviderInvalidRequest(
            f"Unsupported currency {currency!r}: expected a 3-letter ISO 4217 code"
        )
    if code in _ZERO_DECIMAL_CURRENCIES:
        return 0
    if code in _THREE_DECIMAL_CURRENCIES:
        return 3
    return 2


def to_minor_units(amount: Decimal, currency: str) -> int:
    """Exact Decimal -> integer minor units. No float anywhere in this path.

    REFUSES to round. An amount carrying more precision than the currency can express (a
    2-decimal figure in JPY, or 10.999999 in USD) is a bug upstream, and quietly rounding it
    would turn that bug into a silent mischarge — exactly the "10.999999 must not become an
    unexpected provider amount" hazard. The caller must present an exactly representable
    amount.
    """
    exponent = currency_exponent(currency)
    if not isinstance(amount, Decimal):
        # Never accept a float: 0.1 is not 0.1 in binary and money must not inherit that.
        raise ProviderInvalidRequest(
            f"Amount must be a Decimal, got {type(amount).__name__} — float money is not accepted"
        )
    if amount.is_nan() or amount.is_infinite():
        raise ProviderInvalidRequest("Amount must be a finite Decimal")
    if amount <= 0:
        raise ProviderInvalidRequest(f"Amount must be greater than zero, got {amount}")
    step = Decimal(1).scaleb(-exponent)          # 0.01 / 1 / 0.001
    try:
        if amount != amount.quantize(step):
            raise ProviderInvalidRequest(
                f"Amount {amount} cannot be represented exactly in {currency.upper()} "
                f"({exponent} decimal places) — refusing to round a money value"
            )
    except InvalidOperation as exc:
        raise ProviderInvalidRequest(f"Amount {amount} is not representable in {currency.upper()}") from exc
    minor = (amount.scaleb(exponent)).to_integral_exact()
    return int(minor)


def from_minor_units(minor: int, currency: str) -> Decimal:
    """Integer minor units -> exact Decimal in the currency's own scale.

    The inverse of to_minor_units, needed to compare a provider event's amount against the
    commercial Decimal WITHOUT the commercial side ever adopting minor units. Exact by
    construction (integer scaleb), so the comparison can be an equality check rather than a
    tolerance — a tolerance is how mischarges hide.
    """
    exponent = currency_exponent(currency)
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise ProviderInvalidRequest(f"Minor-unit amount must be an int, got {type(minor).__name__}")
    return Decimal(minor).scaleb(-exponent)


@dataclass
class DisputeResult:
    """A chargeback opened at the provider (doc P4) — deliberately its own shape, not
    PaymentResult: 'a chargeback is not the same as a refund' (doc P4). Real providers push
    this via webhook; MockPaymentProvider exposes it as a direct call since there is no
    webhook source until a real merchant account exists (services.payments module
    docstring)."""

    provider_dispute_ref: str
    status: str  # one of models.commercial.DISPUTE_STATES
    reserve_amount: Decimal
    evidence_due_by: datetime | None = None


@dataclass
class CheckoutSessionResult:
    """Outcome of creating a provider-hosted checkout session.

    Deliberately its own narrow shape rather than the provider's session object: the caller
    needs a URL to redirect to and the references required to reconcile later, and nothing
    else. Leaking the full provider object would put provider internals into the commercial
    layer, which is the thing the whole abstraction exists to prevent.

    `provider_payment_ref` is the underlying PAYMENT reference (e.g. the payment intent the
    session will drive), which is what inbound provider events are matched against. It is
    Optional because not every provider guarantees it exists at session-creation time — the
    caller must decide what to do when it is absent rather than have a value invented here.
    """

    checkout_session_ref: str
    checkout_url: str
    provider_payment_ref: str | None = None
    # Provider-reported state of the session's payment at creation, in OUR vocabulary. Never
    # a settled state: creating a session collects no money.
    state: str = "requires_action"
    expires_at: datetime | None = None


@dataclass
class PaymentResult:
    """Normalized outcome of a provider call — this shape is what crud.commercial writes
    onto a Payment row, never the provider's raw response (doc P6: minimize what's stored,
    keep provider specifics out of the application ledger's core fields)."""

    provider_payment_ref: str
    state: str  # one of models.commercial.PAYMENT_STATES
    authorized_at: datetime | None = None
    captured_at: datetime | None = None
    settled_at: datetime | None = None
    failure_reason: str | None = None


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def authorize(self, *, amount: Decimal, currency: str, idempotency_key: str,
                  simulate_failure: bool = False) -> PaymentResult:
        """Authorization is not settlement (doc D3) — a successful authorize() lands the
        Payment in `pending`, not `paid`."""

    @abstractmethod
    def capture(self, provider_payment_ref: str, *, idempotency_key: str | None = None) -> PaymentResult:
        """`idempotency_key` is optional and provider-level: it lets an adapter make a
        retried capture a no-op AT THE PROVIDER, on top of the application's own guard.
        Added as a keyword with a default so existing callers and MockPaymentProvider are
        unaffected — the business signature is unchanged."""

    @abstractmethod
    def refund(self, provider_payment_ref: str, amount: Decimal, *,
               idempotency_key: str | None = None) -> PaymentResult:
        """Same provider-level idempotency contract as capture(). The key must identify the
        REMEDY, not the amount: two legitimate partial refunds of the same value are
        different operations and must not collide."""

    @abstractmethod
    def open_dispute(self, provider_payment_ref: str, *, amount: Decimal, reason_code: str) -> DisputeResult:
        """Provider-initiated chargeback (doc P4). Real providers reach this via webhook,
        not a direct call — see module docstring."""

    @abstractmethod
    def create_checkout_session(self, *, amount: Decimal, currency: str, idempotency_key: str,
                                 success_url: str, cancel_url: str,
                                 description: str, metadata: dict[str, str],
                                 ) -> CheckoutSessionResult:
        """Create a PROVIDER-HOSTED checkout page for an amount the commercial layer decided.

        Added in Phase 4C as the minimum extension needed for hosted checkout; authorize(),
        capture() and refund() are unchanged. Hosted checkout keeps card entry entirely off
        our origin, so this is the smallest payment surface available to us.

        `amount`/`currency` are authoritative inputs — an implementation must send exactly
        what it is given and must never consult a provider-side price object, product or
        catalog to determine them. `metadata` carries safe internal references (payment/order
        ids, correlation id) for reconciliation; implementations must not put secrets or
        payment credentials in it.

        Implementations must NOT collect money as a side effect of creating a session, and
        must not return a settled state: authorization happens when the payer acts, and
        settlement is confirmed only through provider events.
        """


class MockPaymentProvider(PaymentProvider):
    """Deterministic, in-process simulation. `simulate_failure` on authorize() is the only
    way to exercise the FAILED path without a real provider sandbox — production traffic
    would never pass that flag."""

    name = "mock"

    def authorize(self, *, amount: Decimal, currency: str, idempotency_key: str,
                  simulate_failure: bool = False) -> PaymentResult:
        ref = f"mock_{secrets.token_hex(10)}"
        if simulate_failure:
            return PaymentResult(provider_payment_ref=ref, state="failed", failure_reason="simulated_decline")
        return PaymentResult(provider_payment_ref=ref, state="pending", authorized_at=datetime.now(timezone.utc))

    def capture(self, provider_payment_ref: str, *, idempotency_key: str | None = None) -> PaymentResult:
        # idempotency_key is accepted and ignored: this provider has no remote side to
        # deduplicate against, and the application-level guard already covers replays.
        now = datetime.now(timezone.utc)
        return PaymentResult(provider_payment_ref=provider_payment_ref, state="paid",
                              captured_at=now, settled_at=now)

    def refund(self, provider_payment_ref: str, amount: Decimal, *,
               idempotency_key: str | None = None) -> PaymentResult:
        return PaymentResult(provider_payment_ref=provider_payment_ref, state="refunded",
                              settled_at=datetime.now(timezone.utc))

    def create_checkout_session(self, *, amount: Decimal, currency: str, idempotency_key: str,
                                 success_url: str, cancel_url: str,
                                 description: str, metadata: dict[str, str],
                                 ) -> CheckoutSessionResult:
        """Deterministic simulation. Validates the amount/currency through the same
        conversion the real adapters use, so a bad amount fails identically here — otherwise
        the mock would be a laxer path than production and hide caller bugs.

        The returned URL is an obviously-fake local sentinel: it must never look like a real
        checkout page that someone might try to open.
        """
        to_minor_units(amount, currency)          # same fail-closed validation as production
        session_ref = f"mock_cs_{secrets.token_hex(10)}"
        return CheckoutSessionResult(
            checkout_session_ref=session_ref,
            checkout_url=f"https://mock-provider.invalid/checkout/{session_ref}",
            provider_payment_ref=f"mock_{secrets.token_hex(10)}",
            state="requires_action",              # nothing collected yet
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )

    def open_dispute(self, provider_payment_ref: str, *, amount: Decimal, reason_code: str) -> DisputeResult:
        # 7-day evidence window is a plausible mock default, not a real processor's actual
        # deadline — production providers state their own window in the webhook payload.
        return DisputeResult(
            provider_dispute_ref=f"mock_dp_{secrets.token_hex(8)}",
            status="opened", reserve_amount=amount,
            evidence_due_by=datetime.now(timezone.utc) + timedelta(days=7),
        )


_MOCK = MockPaymentProvider()

# Every provider NAME this application knows how to speak, whether or not this particular
# deployment has credentials for it. Availability is a separate question from recognition:
# get_provider() answers "can I actually use it here", this answers "is it a real name".
PROVIDER_NAMES: tuple[str, ...] = ("mock", "stripe")


def known_providers() -> tuple[str, ...]:
    """Provider names this application recognizes. The webhook endpoint validates against
    this so an inbound event cannot name an arbitrary provider and be accepted."""
    return PROVIDER_NAMES


def provider_available(name: str) -> bool:
    """Whether get_provider(name) would succeed here — for surfacing configuration state
    without triggering an exception."""
    try:
        get_provider(name)
        return True
    except PaymentProviderError:
        return False


def get_provider(name: str = "mock") -> PaymentProvider:
    """Resolve a provider adapter, or FAIL CLOSED.

    This used to `return _PROVIDERS.get(name, _PROVIDERS["mock"])` — an unknown or
    unconfigured name silently became the SIMULATED provider. Harmless while mock was the
    only implementation; actively dangerous now: a caller asking for Stripe in an environment
    with no STRIPE_SECRET_KEY would receive a fabricated "authorized" result and the
    commercial ledger would record a real order as pending payment against money that never
    moved. An outage is recoverable; invented money is not.

    So:
      "mock"        -> the deterministic simulator (always available)
      "stripe"      -> the Stripe adapter, or ProviderNotConfigured if no secret key
      anything else -> UnknownProvider

    Imports the Stripe adapter lazily so the Stripe SDK is only loaded where it is used, and
    so application startup never depends on Stripe being installed or configured.
    """
    key = (name or "").strip().lower()
    if key == "mock":
        return _MOCK
    if key == "stripe":
        from ..config import settings
        if not settings.stripe_configured():
            raise ProviderNotConfigured(
                "Stripe was requested but STRIPE_SECRET_KEY is not configured. Refusing to "
                "fall back to the simulated provider — configure Stripe or use provider "
                "'mock' explicitly."
            )
        from .payments_stripe import StripePaymentProvider
        return StripePaymentProvider(api_key=settings.STRIPE_SECRET_KEY)
    raise UnknownProvider(
        f"Unknown payment provider {name!r}. Known providers: {', '.join(PROVIDER_NAMES)}"
    )


def verify_webhook_signature(payload: bytes, signature: str | None, secret: str | None) -> bool:
    """Fails closed: no configured secret means no provider is wired up yet (doc Section 26
    go-live gate), so there is nothing legitimate this webhook could be — an unsigned/
    forged payload must never be accepted just because verification isn't set up. Callers
    distinguish "not configured" (503) from "bad signature" (401) themselves by checking
    the secret before calling this; this function only ever answers "was that payload
    actually signed with the configured secret."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), payload, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature)
