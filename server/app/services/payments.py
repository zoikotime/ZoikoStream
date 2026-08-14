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
from decimal import Decimal


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

    @abstractmethod
    def open_dispute(self, provider_payment_ref: str, *, amount: Decimal, reason_code: str) -> DisputeResult:
        """Provider-initiated chargeback (doc P4). Real providers reach this via webhook,
        not a direct call — see module docstring."""


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

    def open_dispute(self, provider_payment_ref: str, *, amount: Decimal, reason_code: str) -> DisputeResult:
        # 7-day evidence window is a plausible mock default, not a real processor's actual
        # deadline — production providers state their own window in the webhook payload.
        return DisputeResult(
            provider_dispute_ref=f"mock_dp_{secrets.token_hex(8)}",
            status="opened", reserve_amount=amount,
            evidence_due_by=datetime.now(timezone.utc) + timedelta(days=7),
        )


_PROVIDERS: dict[str, PaymentProvider] = {"mock": MockPaymentProvider()}


def get_provider(name: str = "mock") -> PaymentProvider:
    """Unknown/unconfigured provider names fall back to mock rather than raising — the
    commercial engine must keep working in every environment that has no live merchant
    account configured, per this module's docstring."""
    return _PROVIDERS.get(name, _PROVIDERS["mock"])


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
