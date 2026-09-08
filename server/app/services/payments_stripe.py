"""Stripe adapter for the provider-neutral PaymentProvider interface (ZST-LE-COM-001 P1).

This module is the ONLY place in the codebase that knows Stripe exists. It implements the
existing abstraction in services/payments.py — it does not introduce a second payment
architecture, and crud/commercial.py never imports it:

    commercial CRUD -> PaymentProvider (ABC) -> StripePaymentProvider -> Stripe API

What this adapter deliberately does NOT do, because those decisions already belong to the
commercial domain and Stripe is only a processor:

  * decide an amount, price, discount or deposit — it receives an approved Decimal
  * decide a currency — it receives the order/payment's authoritative currency
  * calculate tax — tax determination lives on EventOrder (doc L4)
  * name a seller entity — that comes from SellerLegalEntity via the invoice context
  * touch capacity, readiness or event lifecycle — separate state machines entirely
  * write Payment.state — it returns a normalized PaymentResult and the commercial state
    machine (crud.apply_payment_state) decides whether that transition is legal

There are no Stripe Price IDs, Products or Prices anywhere in this file. Live Event pricing
comes from a published CatalogVersion (Phase 2) and Stripe is told the resulting figure.

Authorization model: PaymentIntents are created with capture_method="manual", so authorize
and capture stay distinct evidence states (doc D3: "Authorization, capture, settlement and
payout are distinct evidence states"). A freshly created intent has no payment method yet, so
it normalizes to `requires_action` until a client-side confirmation happens — that flow is a
later phase, and this adapter reports the provider's actual status rather than pretending.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

import stripe

from .payments import (
    SubscriptionCheckoutResult,
    CheckoutSessionResult,
    DisputeResult,
    PaymentProvider,
    PaymentResult,
    ProviderDeclined,
    ProviderInvalidRequest,
    ProviderNotConfigured,
    ProviderStateUnknown,
    ProviderUnavailable,
    to_minor_units,
)

log = logging.getLogger(__name__)

# Stripe PaymentIntent.status -> our PAYMENT_STATES vocabulary. Mapped from Stripe's
# documented intent lifecycle, NOT invented, and every target is an existing state — no
# Stripe-specific business state is introduced (doc: the generic state machine is
# authoritative).
#
#   requires_payment_method / requires_confirmation / requires_action
#       the payer still has to do something -> our `requires_action`
#   processing            provider is working on it, outcome not yet known -> `pending`
#   requires_capture      AUTHORIZED but not settled -> `pending` (doc D3: auth != settlement)
#   succeeded             settled -> `paid`
#   canceled              the intent will never succeed -> `failed`
_INTENT_STATE = {
    "requires_payment_method": "requires_action",
    "requires_confirmation": "requires_action",
    "requires_action": "requires_action",
    "processing": "pending",
    "requires_capture": "pending",
    "succeeded": "paid",
    "canceled": "failed",
}

# Stripe Refund.status -> our vocabulary. `pending` refunds are not yet money-out, so they do
# not claim a refunded state; the commercial layer decides part_refunded vs refunded from the
# amounts it already knows (crud.execute_refund_credit).
_REFUND_STATE = {
    "succeeded": "refunded",
    "pending": "pending",
    "requires_action": "requires_action",
    "failed": "failed",
    "canceled": "failed",
}


def _utc(ts: int | None) -> datetime | None:
    """Stripe timestamps are Unix seconds; ours are timezone-aware UTC."""
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def _translate(exc: Exception, *, operation: str) -> Exception:
    """Stripe exception -> provider-neutral error (Step 12).

    The critical distinction: a connection error/timeout means the operation's outcome is
    UNKNOWN, not failed. Classifying it as a decline would let the commercial layer record a
    definitive failure for money that may well have moved. Only an actual card decline is
    treated as a decline.
    """
    if isinstance(exc, stripe.CardError):
        # A real, definitive refusal by the issuer.
        return ProviderDeclined(f"{operation} declined: {getattr(exc, 'code', None) or 'card_error'}")
    if isinstance(exc, stripe.AuthenticationError):
        return ProviderNotConfigured(f"Stripe rejected our credentials during {operation}")
    if isinstance(exc, stripe.PermissionError):
        return ProviderNotConfigured(f"Stripe credentials lack permission for {operation}")
    if isinstance(exc, stripe.IdempotencyError):
        # Same key reused with different parameters — our bug, and retrying will not fix it.
        return ProviderInvalidRequest(f"Idempotency key reused with different parameters on {operation}")
    if isinstance(exc, stripe.InvalidRequestError):
        return ProviderInvalidRequest(f"Stripe rejected the {operation} request as invalid")
    if isinstance(exc, stripe.RateLimitError):
        return ProviderUnavailable(f"Stripe rate-limited {operation}; retry later")
    if isinstance(exc, stripe.APIConnectionError):
        # Never a decline. The request may or may not have been applied.
        return ProviderStateUnknown(
            f"Could not reach Stripe to complete {operation}; the outcome is UNKNOWN and must "
            "be reconciled from provider evidence, not assumed"
        )
    if isinstance(exc, stripe.StripeError):
        return ProviderUnavailable(f"Stripe returned an error for {operation}")
    return ProviderUnavailable(f"Unexpected failure during {operation}")


class StripePaymentProvider(PaymentProvider):
    """Implements the existing PaymentProvider abstraction against Stripe.

    Constructed per call by services.payments.get_provider, which supplies the key from
    configuration — the key is never read from module scope, never logged, and never stored
    on anything that gets serialized.
    """

    name = "stripe"

    def __init__(self, api_key: str):
        if not (api_key or "").strip():
            raise ProviderNotConfigured("StripePaymentProvider requires an API key")
        # Per-instance client rather than the module-global stripe.api_key, so a request can
        # never pick up another deployment's/tenant's key from shared module state.
        self._client = stripe.StripeClient(api_key.strip())

    # ── logging ──────────────────────────────────────────────────────────────────────────
    def _log(self, operation: str, **fields) -> None:
        """Structured, secret-free observability. Only provider/object identifiers and
        normalized outcomes — never the API key, never a payload, never card data."""
        log.info(
            "payment_provider=stripe operation=%s %s",
            operation,
            " ".join(f"{k}={v}" for k, v in fields.items() if v is not None),
        )

    # ── authorize ────────────────────────────────────────────────────────────────────────
    def authorize(self, *, amount: Decimal, currency: str, idempotency_key: str,
                  simulate_failure: bool = False) -> PaymentResult:
        """Create a manual-capture PaymentIntent for an amount the commercial layer decided.

        `simulate_failure` is a MockPaymentProvider-only test affordance. Silently ignoring an
        explicit "make this fail" instruction against a real processor would be worse than
        refusing it, so it is rejected rather than dropped.
        """
        if simulate_failure:
            raise ProviderInvalidRequest(
                "simulate_failure is a mock-only affordance and is not supported by the Stripe "
                "provider — use Stripe test-mode cards to exercise failure paths"
            )
        minor = to_minor_units(amount, currency)          # raises on bad amount/currency
        try:
            intent = self._client.payment_intents.create(
                params={
                    "amount": minor,
                    "currency": currency.strip().lower(),
                    # Keeps authorization and capture distinct (doc D3).
                    "capture_method": "manual",
                    # Correlates the Stripe object back to our commercial operation without
                    # putting any commercial VALUE in Stripe. Metadata is a reference, not a
                    # source of truth, and is never read back to decide anything.
                    "metadata": {"zoiko_idempotency_key": idempotency_key},
                },
                # Provider-level idempotency: the SAME commercial operation retried reaches
                # the SAME Stripe intent instead of creating a second one (Step 7).
                options={"idempotency_key": f"authz:{idempotency_key}"},
            )
        except Exception as exc:
            translated = _translate(exc, operation="authorize")
            self._log("authorize_error", error=type(translated).__name__)
            raise translated from exc

        state = _INTENT_STATE.get(intent.status)
        if state is None:
            # An unmapped status must not be guessed into a money state.
            raise ProviderInvalidRequest(f"Unrecognized Stripe intent status {intent.status!r}")
        self._log("authorize", provider_payment_ref=intent.id, stripe_status=intent.status,
                  state=state, minor_units=minor, currency=currency.upper())
        return PaymentResult(
            provider_payment_ref=intent.id,
            state=state,
            # Only an actually-authorized intent carries an authorization timestamp.
            authorized_at=_utc(intent.created) if intent.status == "requires_capture" else None,
            failure_reason=None if state != "failed" else f"stripe_status:{intent.status}",
        )

    # ── capture ──────────────────────────────────────────────────────────────────────────
    def capture(self, provider_payment_ref: str, *, idempotency_key: str | None = None) -> PaymentResult:
        """Capture a previously authorized intent. Returns the normalized result; it does NOT
        set Payment.state — crud.apply_payment_state validates the transition first."""
        if not (provider_payment_ref or "").strip():
            raise ProviderInvalidRequest("capture requires a provider payment reference")
        options = {"idempotency_key": f"capture:{idempotency_key}"} if idempotency_key else None
        try:
            intent = self._client.payment_intents.capture(
                provider_payment_ref, params={}, options=options,
            )
        except Exception as exc:
            translated = _translate(exc, operation="capture")
            self._log("capture_error", provider_payment_ref=provider_payment_ref,
                      error=type(translated).__name__)
            raise translated from exc

        state = _INTENT_STATE.get(intent.status)
        if state is None:
            raise ProviderInvalidRequest(f"Unrecognized Stripe intent status {intent.status!r}")
        captured = _utc(getattr(intent, "created", None))
        self._log("capture", provider_payment_ref=intent.id, stripe_status=intent.status, state=state)
        return PaymentResult(
            provider_payment_ref=intent.id,
            state=state,
            captured_at=captured if state in ("paid", "partially_paid") else None,
            # Stripe "succeeded" means captured; funds settle to the balance separately, and
            # true settlement evidence arrives via provider events, so we do not claim it here.
            settled_at=None,
            failure_reason=None if state != "failed" else f"stripe_status:{intent.status}",
        )

    # ── refund ───────────────────────────────────────────────────────────────────────────
    def refund(self, provider_payment_ref: str, amount: Decimal, *,
               idempotency_key: str | None = None) -> PaymentResult:
        """Low-level provider refund operation ONLY.

        Reached exclusively from crud.execute_refund_credit, i.e. after the RefundCredit
        maker-checker approval. There is deliberately no Stripe refund endpoint and no way to
        call this without an approved remedy.

        `provider_payment_ref` returned here is the Stripe REFUND id, which is what
        RefundCredit.provider_ref records — the refund is its own provider object, distinct
        from the payment it reverses.
        """
        if not (provider_payment_ref or "").strip():
            raise ProviderInvalidRequest("refund requires a provider payment reference")
        # Currency comes from the intent being refunded, so ask Stripe rather than assume one.
        try:
            intent = self._client.payment_intents.retrieve(provider_payment_ref)
        except Exception as exc:
            translated = _translate(exc, operation="refund_lookup")
            self._log("refund_error", provider_payment_ref=provider_payment_ref,
                      error=type(translated).__name__)
            raise translated from exc
        minor = to_minor_units(amount, intent.currency)
        try:
            refund = self._client.refunds.create(
                params={"payment_intent": provider_payment_ref, "amount": minor},
                # Keyed on the REMEDY, not the amount: two legitimate partial refunds of the
                # same value are different operations and must not collide.
                options={"idempotency_key": f"refund:{idempotency_key}"} if idempotency_key else None,
            )
        except Exception as exc:
            translated = _translate(exc, operation="refund")
            self._log("refund_error", provider_payment_ref=provider_payment_ref,
                      error=type(translated).__name__)
            raise translated from exc

        state = _REFUND_STATE.get(refund.status)
        if state is None:
            raise ProviderInvalidRequest(f"Unrecognized Stripe refund status {refund.status!r}")
        self._log("refund", provider_refund_ref=refund.id, payment_intent=provider_payment_ref,
                  stripe_status=refund.status, state=state, minor_units=minor)
        return PaymentResult(
            provider_payment_ref=refund.id,
            state=state,
            settled_at=_utc(getattr(refund, "created", None)) if state == "refunded" else None,
            failure_reason=None if state != "failed" else f"stripe_refund_status:{refund.status}",
        )

    # ── hosted checkout (Phase 4C) ───────────────────────────────────────────────────────
    def create_checkout_session(self, *, amount: Decimal, currency: str, idempotency_key: str,
                                 success_url: str, cancel_url: str,
                                 description: str, metadata: dict[str, str],
                                 ) -> CheckoutSessionResult:
        """Create a Stripe-hosted Checkout Session for an amount the commercial layer decided.

        Card entry happens entirely on Stripe's page, so no payment credential ever reaches
        our origin or our React bundle.

        Two deliberate choices, both verified against this SDK's own parameter definitions
        rather than assumed:

        * `line_items[].price_data` (INLINE) — never `price`. A Stripe Price ID would make
          Stripe the pricing source; the amount here comes from the published CatalogVersion
          chain and Stripe is merely told the result.
        * `payment_intent_data.capture_method="manual"` — keeps authorization and capture
          distinct (doc D3), matching the existing commercial model where capture is a
          separate Finance action. Stripe's Checkout params accept
          Literal["automatic","automatic_async","manual"] for this field.

        Creating a session collects nothing: the returned state is `requires_action` because
        the payer has not acted yet. Settlement is only ever confirmed by a provider event.
        """
        minor = to_minor_units(amount, currency)      # fail-closed on amount/currency
        if not (success_url or "").strip() or not (cancel_url or "").strip():
            raise ProviderInvalidRequest("Checkout requires both a success and a cancel URL")
        # Stripe rejects non-string metadata values; keep it to safe internal references.
        safe_metadata = {str(k): str(v) for k, v in (metadata or {}).items() if v is not None}
        try:
            session = self._client.checkout.sessions.create(
                params={
                    "mode": "payment",
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "line_items": [{
                        "quantity": 1,
                        "price_data": {
                            "currency": currency.strip().lower(),
                            "unit_amount": minor,
                            # product_data avoids creating a persistent Stripe Product, so
                            # nothing price-bearing is ever stored on Stripe's side.
                            "product_data": {"name": description or "ZoikoStream Live Event"},
                        },
                    }],
                    "payment_intent_data": {
                        "capture_method": "manual",
                        # Mirrored onto the PaymentIntent so inbound payment_intent.* events
                        # carry our references even though session metadata does not
                        # propagate to the intent automatically.
                        "metadata": safe_metadata,
                    },
                    "metadata": safe_metadata,
                },
                # Same commercial operation retried -> same Stripe session, not a second one.
                options={"idempotency_key": f"checkout:{idempotency_key}"},
            )
        except Exception as exc:
            translated = _translate(exc, operation="create_checkout_session")
            self._log("checkout_error", error=type(translated).__name__)
            raise translated from exc

        url = getattr(session, "url", None)
        if not url:
            # Without a URL there is nothing to redirect to; refuse rather than hand back a
            # session the caller cannot use.
            raise ProviderInvalidRequest("Stripe returned a checkout session with no URL")
        intent = getattr(session, "payment_intent", None)
        intent_ref = intent if isinstance(intent, str) else getattr(intent, "id", None)
        self._log("checkout_created", checkout_session_ref=session.id,
                  provider_payment_ref=intent_ref, minor_units=minor,
                  currency=currency.upper())
        return CheckoutSessionResult(
            checkout_session_ref=session.id,
            checkout_url=url,
            provider_payment_ref=intent_ref,
            state="requires_action",
            expires_at=_utc(getattr(session, "expires_at", None)),
        )

    def create_subscription_checkout_session(
        self, *, price_id: str, idempotency_key: str, success_url: str, cancel_url: str,
        metadata: dict[str, str], customer_email: str | None = None,
    ) -> "SubscriptionCheckoutResult":
        """Create a Stripe-hosted Checkout Session in SUBSCRIPTION mode (Ledger 1).

        Separate from create_checkout_session() above, which is Ledger 2's one-off event-order
        payment. The two differ in every way that matters and must not be merged:

        * `mode="subscription"` — Stripe creates a recurring Subscription, not a single
          PaymentIntent. The billing interval lives on the Stripe Price, not here.
        * `price` (an approved Stripe Price ID) — NOT `price_data`. This is the opposite choice
          from Ledger 2 and is deliberate. A Live Event amount is computed from our published
          CatalogVersion, so Stripe is told the result. A subscription price is an approved
          commercial fact from ZST-COM-PRICE-001 that Stripe itself holds; inventing an inline
          amount here would make this code the pricing authority, which Section 18 forbids.
        * no `capture_method="manual"` — that parameter belongs to payment mode and is invalid
          for subscription mode. Stripe collects the first invoice itself.

        The caller resolves `price_id` from configuration; this method never chooses a price,
        and it is unreachable without one.
        """
        if not (price_id or "").strip():
            raise ProviderInvalidRequest(
                "A subscription checkout requires an approved Stripe Price ID"
            )
        if not (success_url or "").strip() or not (cancel_url or "").strip():
            raise ProviderInvalidRequest("Checkout requires both a success and a cancel URL")
        safe_metadata = {str(k): str(v) for k, v in (metadata or {}).items() if v is not None}
        params: dict = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items": [{"price": price_id.strip(), "quantity": 1}],
            # Mirrored onto the Subscription so inbound customer.subscription.* events carry
            # our tenant references — session metadata does not propagate automatically, the
            # same gap create_checkout_session() closes for the PaymentIntent.
            "subscription_data": {"metadata": safe_metadata},
            "metadata": safe_metadata,
        }
        if customer_email:
            # Prefills Stripe's own form. Never a substitute for tenant identity, which is
            # carried in metadata and re-derived server-side on the way back.
            params["customer_email"] = customer_email
        try:
            session = self._client.checkout.sessions.create(
                params=params,
                # Same plan purchase retried -> the same Stripe session, not a second
                # subscription. Namespaced apart from Ledger 2's "checkout:" keys.
                options={"idempotency_key": f"sub_checkout:{idempotency_key}"},
            )
        except Exception as exc:
            translated = _translate(exc, operation="create_subscription_checkout_session")
            self._log("subscription_checkout_error", error=type(translated).__name__)
            raise translated from exc

        url = getattr(session, "url", None)
        if not url:
            raise ProviderInvalidRequest("Stripe returned a checkout session with no URL")
        sub = getattr(session, "subscription", None)
        sub_ref = sub if isinstance(sub, str) else getattr(sub, "id", None)
        customer = getattr(session, "customer", None)
        customer_ref = customer if isinstance(customer, str) else getattr(customer, "id", None)
        # No amount/currency is logged: this method never sees one.
        self._log("subscription_checkout_created", checkout_session_ref=session.id,
                  price_id=price_id, stripe_subscription_ref=sub_ref)
        return SubscriptionCheckoutResult(
            checkout_session_ref=session.id,
            checkout_url=url,
            stripe_customer_id=customer_ref,
            stripe_subscription_id=sub_ref,
        )

    def change_subscription_price(self, provider_subscription_ref: str, *, price_id: str,
                                   idempotency_key: str) -> "SubscriptionCheckoutResult":
        """Move an EXISTING Stripe subscription onto `price_id` with NO proration.

        Called only at the effective date, once the billing period the customer already paid
        for has ended. That timing is what makes a plain modify correct here rather than a
        Subscription Schedule: there is no remaining mid-cycle time to prorate, so the swap is
        a clean boundary transition — which is exactly the approved rule.

        `proration_behavior="none"` is the approved commercial decision, not a default:
            "Use NO mid-cycle proration. Do not create prorated credits or charges. Do not use
             Stripe proration behavior that creates additional mid-cycle charges."
        Stripe's own default is `create_prorations`, which WOULD raise an immediate invoice
        item, so leaving this unset would have silently violated the rule. `none` is passed
        explicitly and is asserted by test.

        Reuses the existing subscription's items: the item id is read back and swapped in place,
        so no second Stripe subscription and no second customer is ever created.
        """
        if not (provider_subscription_ref or "").strip():
            raise ProviderInvalidRequest("A subscription price change requires a subscription id")
        if not (price_id or "").strip():
            raise ProviderInvalidRequest(
                "A subscription price change requires an approved Stripe Price ID")
        try:
            existing = self._client.subscriptions.retrieve(provider_subscription_ref.strip())
            items = getattr(getattr(existing, "items", None), "data", None) or []
            if not items:
                raise ProviderInvalidRequest(
                    "The Stripe subscription has no line item to move")
            updated = self._client.subscriptions.update(
                provider_subscription_ref.strip(),
                params={
                    # Replace the single existing item rather than appending one — appending
                    # would bill the customer for BOTH plans.
                    "items": [{"id": items[0].id, "price": price_id.strip()}],
                    "proration_behavior": "none",
                },
                options={"idempotency_key": f"plan_change:{idempotency_key}"},
            )
        except Exception as exc:
            translated = _translate(exc, operation="change_subscription_price")
            self._log("subscription_item_swap_error", error=type(translated).__name__)
            raise translated from exc

        customer = getattr(updated, "customer", None)
        self._log("subscription_item_swapped",
                  stripe_subscription_ref=provider_subscription_ref, price_id=price_id,
                  proration="none")
        return SubscriptionCheckoutResult(
            checkout_session_ref="",
            checkout_url="",
            stripe_customer_id=customer if isinstance(customer, str) else getattr(customer, "id", None),
            stripe_subscription_id=getattr(updated, "id", provider_subscription_ref),
        )

    # ── disputes ─────────────────────────────────────────────────────────────────────────
    def open_dispute(self, provider_payment_ref: str, *, amount: Decimal, reason_code: str) -> DisputeResult:
        """Not callable against Stripe. A dispute is opened by the cardholder's bank, never by
        the merchant — Stripe exposes no create-dispute API. Real disputes arrive as provider
        events and are wired into PaymentDispute in a later phase; refusing here is honest,
        where returning a fabricated DisputeResult would put invented evidence in the ledger.
        """
        raise ProviderInvalidRequest(
            "Disputes cannot be opened via the Stripe API — they originate from the "
            "cardholder's bank and arrive as provider events"
        )
