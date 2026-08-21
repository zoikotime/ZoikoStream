"""Stripe webhook event translation (Phase 4B).

The ONLY place Stripe event names exist. crud/commercial.py and models/commercial.py stay
provider-neutral: they speak the generic vocabulary in crud.PROVIDER_EVENT_STATE_MAP, and
this module translates Stripe's names and payload shapes into it.

    Stripe webhook -> verify signature -> translate (here) -> provider_events
                   -> generic event -> payment state machine -> audit / reconciliation

What this module does NOT do — those decisions belong to the commercial domain:
  * no pricing, tax, discount, capacity, seller identity or invoice totals
  * no Payment.state assignment (it returns a translation; the state machine decides)
  * no RefundCredit creation (a webhook may confirm provider-side refund evidence, never
    authorize a remedy — that stays behind maker-checker)
  * no dispute outcome invention (see DISPUTE_EVENTS below)

Amounts arrive from Stripe in MINOR units and are converted to the commercial Decimal scale
before anything compares them, so the domain never adopts minor units.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import stripe

from .payments import ProviderInvalidRequest, from_minor_units

log = logging.getLogger(__name__)

PROVIDER_NAME = "stripe"


# ── Stripe event type -> generic event name ───────────────────────────────────────────────
# Every target is a key of crud.PROVIDER_EVENT_STATE_MAP, so the generic state machine stays
# authoritative. Mapped from Stripe's documented PaymentIntent/Charge semantics — deliberately
# NOT a blind name-to-state translation.
#
#   amount_capturable_updated  the intent became authorized-and-capturable. THIS is the real
#                              "authorization succeeded" signal, not `created`.
#   processing                 provider is working; outcome not yet known -> pending
#   requires_action            payer must do something (SCA/3DS) -> requires_action
#   succeeded                  captured/settled -> paid
#   payment_failed             the attempt definitively failed
#   canceled                   the intent is void and can never succeed
_PAYMENT_INTENT_EVENTS = {
    "payment_intent.amount_capturable_updated": "authorization_succeeded",
    "payment_intent.processing": "authorization_succeeded",
    "payment_intent.requires_action": "action_required",
    "payment_intent.succeeded": "capture_succeeded",
    "payment_intent.payment_failed": "capture_failed",
    "payment_intent.canceled": "authorization_failed",
}

# Refund evidence. `charge.refunded` carries the amounts needed to tell a full refund from a
# partial one, so the generic event is chosen from the payload rather than the name.
_REFUND_EVENTS = frozenset({"charge.refunded"})

# Dispute events are recorded as EVIDENCE ONLY in this phase.
#
# The payment state machine could represent `disputed`/`reversed`, but the PaymentDispute
# case workflow (evidence packages, reserve amounts, case ownership) is deliberately not wired
# up yet. Moving a Payment to `disputed` with no corresponding dispute case would leave a
# half-state that nothing owns, and inventing a won/lost outcome from a webhook is exactly
# what the standard forbids. So: retain the evidence, flag it for follow-up, mutate nothing.
_DISPUTE_EVENTS = frozenset({
    "charge.dispute.created", "charge.dispute.updated", "charge.dispute.closed",
    "charge.dispute.funds_withdrawn", "charge.dispute.funds_reinstated",
})

# Informational refund lifecycle events. `refund.updated` fires for transitions that are not
# themselves money movement; the authoritative refund evidence is `charge.refunded`, so these
# are retained without effect rather than double-counted.
_INFORMATIONAL_EVENTS = frozenset({"refund.created", "refund.updated", "refund.failed"})

# Checkout session lifecycle. These carry NO financial instruction — the authorization signal
# is still `payment_intent.amount_capturable_updated` and settlement is still
# `payment_intent.succeeded`. What they uniquely carry is the CORRELATION: a hosted session has
# no payment reference until the payer submits, so this is where the real PaymentIntent id
# first becomes knowable for a session we created.
#
# Reconcilable: the payer acted, so Stripe has attached a PaymentIntent to the session.
_CHECKOUT_RECONCILE_EVENTS = frozenset({
    "checkout.session.completed", "checkout.session.async_payment_succeeded",
})
# Terminal-but-unpaid session outcomes. Evidence only: there is nothing to adopt (an expired
# session never produced a payment) and nothing to settle.
_CHECKOUT_EVIDENCE_EVENTS = frozenset({
    "checkout.session.expired", "checkout.session.async_payment_failed",
})


@dataclass
class TranslatedEvent:
    """A Stripe event expressed in provider-neutral terms.

    `generic_event_type` is None when the event carries no financial instruction — the caller
    then records it as evidence instead of processing it (never guesses a state).
    """

    provider_event_id: str
    stripe_event_type: str
    generic_event_type: str | None
    provider_payment_ref: str | None
    amount: Decimal | None
    currency: str | None
    occurred_at: datetime | None
    payload: dict
    evidence_reason: str | None = None
    follow_up_required: bool = False
    # The hosted checkout session this event is about, when it is about one. This is the
    # correlation key for a payment whose provider reference is not yet known.
    checkout_session_ref: str | None = None
    # True when the event can bind a real payment reference onto an awaiting payment. Never a
    # financial instruction — binding identity is not moving money.
    requires_reconciliation: bool = False
    # Our own server-generated key, echoed back by the provider in metadata we set at session
    # creation. Lets an out-of-order payment_intent.* event find its payment by EXACT unique
    # key instead of a guess. Never a value the payer's browser can influence.
    idempotency_key_hint: str | None = None

    @property
    def is_financial(self) -> bool:
        return self.generic_event_type is not None


class StripeSignatureError(Exception):
    """Signature missing/malformed/invalid, or the payload is not a Stripe event."""


def verify_and_parse(raw_body: bytes, signature_header: str | None, webhook_secret: str) -> dict:
    """Verify the Stripe signature against the EXACT raw bytes and return the event.

    Uses the SDK's own `construct_event`, which checks the v1 HMAC and the timestamp
    tolerance. Deliberately no custom signature implementation, and deliberately no
    json.loads -> json.dumps round trip: reserialization can reorder keys or change spacing
    and would invalidate a legitimate signature (or, worse, be made to validate one).

    Raises StripeSignatureError for every failure mode so the caller cannot accidentally treat
    a verification failure as a processing failure.
    """
    if not webhook_secret:
        raise StripeSignatureError("Stripe webhook secret is not configured")
    if not signature_header:
        raise StripeSignatureError("Missing Stripe-Signature header")
    try:
        # Verification runs on the EXACT bytes received. construct_event also enforces the
        # timestamp tolerance, so a captured payload cannot be replayed indefinitely.
        stripe.Webhook.construct_event(
            payload=raw_body, sig_header=signature_header, secret=webhook_secret,
        )
    except stripe.SignatureVerificationError as exc:
        raise StripeSignatureError("Stripe signature verification failed") from exc
    except ValueError as exc:
        raise StripeSignatureError("Malformed Stripe webhook payload") from exc

    # Only now — AFTER the bytes are proven authentic — parse them into a plain dict. The
    # hazard this avoids is reserializing BEFORE verifying (which can invalidate a legitimate
    # signature); parsing verified bytes afterwards is safe. Deliberately NOT dict(event):
    # construct_event returns a stripe.Event, which is not a mapping in SDK v15 and would let
    # a Stripe object travel into the application.
    try:
        as_dict = json.loads(raw_body)
    except (ValueError, TypeError) as exc:
        raise StripeSignatureError("Malformed Stripe webhook payload") from exc
    if not isinstance(as_dict, dict) or not as_dict.get("id") or not as_dict.get("type"):
        raise StripeSignatureError("Stripe payload is missing an event id or type")
    return as_dict


def _object_of(event: dict) -> dict:
    obj = ((event.get("data") or {}).get("object")) or {}
    return obj if isinstance(obj, dict) else {}


def _intent_ref(obj: dict) -> str | None:
    """The PaymentIntent id, which is what Payment.provider_payment_ref holds.

    Matching is by this identifier ONLY. Never by amount, currency or customer email — fuzzy
    matching on a financial mutation is how one tenant's money lands on another's ledger.
    """
    intent = obj.get("payment_intent")
    if isinstance(intent, str) and intent:
        return intent
    if isinstance(intent, dict):
        return intent.get("id")
    # For payment_intent.* events the object IS the intent.
    obj_id = obj.get("id")
    return obj_id if isinstance(obj_id, str) and obj_id.startswith("pi_") else None


def _session_ref(obj: dict) -> str | None:
    """The Checkout Session id, when the event's object IS a checkout session.

    Prefix-checked rather than inferred from the event name so a payload whose object is not
    actually a session can never be treated as one.
    """
    obj_id = obj.get("id")
    return obj_id if isinstance(obj_id, str) and obj_id.startswith("cs_") else None


def _idempotency_key_hint(obj: dict) -> str | None:
    """Our own idempotency key, read back from metadata WE set when creating the session.

    Only ever used to find a payment by exact unique key. It is not an authorization and not a
    source of any commercial value — a forged one still has to survive signature verification,
    and even then it can only ever point at a payment that already exists.
    """
    meta = obj.get("metadata")
    if not isinstance(meta, dict):
        return None
    key = meta.get("zoiko_idempotency_key")
    return key if isinstance(key, str) and key else None


def translate(event: dict) -> TranslatedEvent:
    """Stripe event -> TranslatedEvent. Never raises on an unknown type: unknown events are
    returned as non-financial evidence so a new Stripe event type can never break the
    endpoint or invent a financial state."""
    event_id = event.get("id")
    event_type = event.get("type") or ""
    if not event_id:
        raise ProviderInvalidRequest("Stripe event is missing an id")
    obj = _object_of(event)
    # Stripe's own event timestamp, kept distinct from our received_at (Section 26).
    created = event.get("created")
    occurred_at = (datetime.fromtimestamp(created, tz=timezone.utc)
                   if isinstance(created, int) else None)

    common = dict(provider_event_id=event_id, stripe_event_type=event_type,
                  occurred_at=occurred_at, payload=event)

    # ── PaymentIntent lifecycle ──────────────────────────────────────────────────────────
    if event_type in _PAYMENT_INTENT_EVENTS:
        amount, currency = _amount_currency(obj)
        return TranslatedEvent(
            generic_event_type=_PAYMENT_INTENT_EVENTS[event_type],
            provider_payment_ref=_intent_ref(obj), amount=amount, currency=currency,
            # Carried so an intent event that arrives BEFORE its session event can still find
            # its payment by exact key rather than being parked as unmatched.
            idempotency_key_hint=_idempotency_key_hint(obj), **common,
        )

    # ── Checkout session lifecycle: correlation, never a financial instruction ────────────
    if event_type in _CHECKOUT_RECONCILE_EVENTS or event_type in _CHECKOUT_EVIDENCE_EVENTS:
        reconcilable = event_type in _CHECKOUT_RECONCILE_EVENTS
        amount, currency = _session_amount_currency(obj)
        return TranslatedEvent(
            # None: the payment state machine is driven by payment_intent.* only. A completed
            # session means the payer acted, NOT that money settled — under manual capture the
            # funds are merely authorized at this point.
            generic_event_type=None,
            provider_payment_ref=_intent_ref(obj),
            checkout_session_ref=_session_ref(obj),
            requires_reconciliation=reconcilable,
            idempotency_key_hint=_idempotency_key_hint(obj),
            amount=amount, currency=currency,
            evidence_reason=("checkout_session_reconcilable" if reconcilable
                             else f"checkout_session_terminal:{event_type}"),
            **common,
        )

    # ── Refund evidence ──────────────────────────────────────────────────────────────────
    if event_type in _REFUND_EVENTS:
        generic = _refund_generic_event(obj)
        # The refund amount, not the charge amount — and never compared against the payment's
        # own amount, because a partial refund legitimately differs from it.
        return TranslatedEvent(
            generic_event_type=generic, provider_payment_ref=_intent_ref(obj),
            amount=None, currency=None, **common,
        )

    # ── Disputes: evidence only, this phase ──────────────────────────────────────────────
    if event_type in _DISPUTE_EVENTS:
        return TranslatedEvent(
            generic_event_type=None, provider_payment_ref=_intent_ref(obj),
            amount=None, currency=None,
            evidence_reason="dispute_event_requires_follow_up", follow_up_required=True,
            **common,
        )

    if event_type in _INFORMATIONAL_EVENTS:
        return TranslatedEvent(
            generic_event_type=None, provider_payment_ref=_intent_ref(obj),
            amount=None, currency=None,
            evidence_reason="informational_refund_lifecycle_event", **common,
        )

    # ── Anything else ────────────────────────────────────────────────────────────────────
    # Recorded, never acted on. Stripe adds event types over time and a new non-financial one
    # must not break this endpoint or produce an invented state.
    return TranslatedEvent(
        generic_event_type=None, provider_payment_ref=_intent_ref(obj),
        amount=None, currency=None,
        evidence_reason=f"unhandled_stripe_event_type:{event_type}", **common,
    )


def _amount_currency(obj: dict) -> tuple[Decimal | None, str | None]:
    """Intent amount in the COMMERCIAL scale, for validation against Payment.amount.

    Returns (None, None) rather than a guess when the payload lacks usable values — the
    downstream check treats absence as silence, not as agreement.
    """
    raw_amount, currency = obj.get("amount"), obj.get("currency")
    if not isinstance(currency, str) or not currency:
        return None, None
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, int):
        return None, currency.upper()
    try:
        return from_minor_units(raw_amount, currency), currency.upper()
    except ProviderInvalidRequest:
        # Unknown/malformed currency: report the currency so the mismatch check can fail
        # closed on it, but do not fabricate an amount.
        return None, currency.upper()


def _session_amount_currency(obj: dict) -> tuple[Decimal | None, str | None]:
    """Session total in the COMMERCIAL scale. A Checkout Session states `amount_total`, not
    `amount`, so it needs its own reader rather than a shared one that would silently return
    None and skip the agreement check."""
    return _amount_currency({"amount": obj.get("amount_total"), "currency": obj.get("currency")})


def _refund_generic_event(charge_obj: dict) -> str:
    """Full vs partial refund, decided from the charge's own amounts.

    Reading provider evidence, not computing commercial policy: Stripe states both the charge
    total and how much of it has been refunded. Defaults to the PARTIAL event when the payload
    is unclear, because `part_refunded` is the weaker claim and the commercial layer refines
    the final state from amounts it already knows.
    """
    amount = charge_obj.get("amount")
    refunded = charge_obj.get("amount_refunded")
    if isinstance(amount, int) and isinstance(refunded, int) and amount > 0:
        return "refunded" if refunded >= amount else "refund_partial"
    return "refund_partial"
