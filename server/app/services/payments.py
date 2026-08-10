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
from datetime import datetime, timezone
from decimal import Decimal


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
    def capture(self, provider_payment_ref: str) -> PaymentResult:
        ...

    @abstractmethod
    def refund(self, provider_payment_ref: str, amount: Decimal) -> PaymentResult:
        ...


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

    def capture(self, provider_payment_ref: str) -> PaymentResult:
        now = datetime.now(timezone.utc)
        return PaymentResult(provider_payment_ref=provider_payment_ref, state="paid",
                              captured_at=now, settled_at=now)

    def refund(self, provider_payment_ref: str, amount: Decimal) -> PaymentResult:
        return PaymentResult(provider_payment_ref=provider_payment_ref, state="refunded",
                              settled_at=datetime.now(timezone.utc))


_PROVIDERS: dict[str, PaymentProvider] = {"mock": MockPaymentProvider()}


def get_provider(name: str = "mock") -> PaymentProvider:
    """Unknown/unconfigured provider names fall back to mock rather than raising — the
    commercial engine must keep working in every environment that has no live merchant
    account configured, per this module's docstring."""
    return _PROVIDERS.get(name, _PROVIDERS["mock"])


def verify_webhook_signature(payload: bytes, signature: str | None, secret: str | None) -> bool:
    """No production provider is configured, so there is no real signing secret to check
    against (doc Section 26 go-live gate). Once a real provider is wired, this becomes an
    HMAC/constant-time comparison against that provider's documented scheme — the shape
    (bytes payload + header signature + configured secret) is written now so callers don't
    need to change when it does."""
    if secret is None:
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode(), payload, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature)
