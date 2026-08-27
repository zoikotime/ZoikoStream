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

# Dispute (chargeback) events. Previously evidence-only, which meant a real chargeback left
# the ledger reading `paid` — the PaymentDispute case workflow existed and nothing reached it.
#
# Now translated into dispute FACTS and handed to crud.ingest_dispute_event, which owns the
# case record and the payment transition. This module still decides nothing: it reports the
# network's own status and lets the commercial layer map it, because a won/lost outcome is the
# card network's determination and inventing one is exactly what the standard forbids.
_DISPUTE_EVENTS = frozenset({
    "charge.dispute.created", "charge.dispute.updated", "charge.dispute.closed",
    "charge.dispute.funds_withdrawn", "charge.dispute.funds_reinstated",
})

# Stripe's dispute.status -> our DISPUTE_STATES (models.commercial). Stripe's `warning_*`
# statuses are early-warning notices (an inquiry, not yet a formal chargeback); they are
# tracked as the same case so the timeline is continuous.
#
# `warning_closed` maps to `withdrawn`: the network dropped the inquiry without it becoming a
# chargeback, which is a withdrawal of the challenge, NOT a win — a win means we contested a
# real dispute and kept the funds, and conflating the two would overstate our dispute record.
STRIPE_DISPUTE_STATUS_MAP = {
    "warning_needs_response": "evidence_required",
    "warning_under_review": "evidence_submitted",
    "warning_closed": "withdrawn",
    "needs_response": "evidence_required",
    "under_review": "evidence_submitted",
    "won": "won",
    "lost": "lost",
}

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
    # ── Dispute (chargeback) facts, when this event is about one ──────────────────────────
    # Kept as its own payload rather than squeezed into generic_event_type/amount, because a
    # dispute is not a payment state instruction (doc P4: "a chargeback is not the same as a
    # refund"). The commercial layer decides what a dispute status means for the payment; this
    # module only reports what the network said.
    dispute: dict | None = None

    @property
    def is_financial(self) -> bool:
        return self.generic_event_type is not None

    @property
    def is_dispute(self) -> bool:
        return self.dispute is not None


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

    # ── Disputes: translated into case facts, applied by the commercial layer ────────────
    if event_type in _DISPUTE_EVENTS:
        dispute = _dispute_fields(obj)
        if dispute is None:
            # A dispute event we cannot identify (no dispute id) cannot be attached to a case.
            # Retained as evidence rather than guessed onto a payment (doc P5).
            return TranslatedEvent(
                generic_event_type=None, provider_payment_ref=_intent_ref(obj),
                amount=None, currency=None,
                evidence_reason="dispute_event_without_identifier", follow_up_required=True,
                **common,
            )
        return TranslatedEvent(
            # Still None: the payment's resulting state is derived from the DISPUTE status by
            # crud.ingest_dispute_event, not from the generic event map. A dispute is a case
            # with an outcome, not a payment instruction (doc P4).
            generic_event_type=None,
            provider_payment_ref=dispute["provider_payment_ref"],
            amount=dispute["amount"], currency=dispute["currency"],
            dispute=dispute, evidence_reason=f"dispute:{dispute['provider_status']}",
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


def _dispute_fields(obj: dict) -> dict | None:
    """Extract the dispute facts from a `charge.dispute.*` event object, or None.

    Returns None when the object carries no dispute id — without one there is no case identity
    to be idempotent against, so the caller retains it as evidence rather than attaching it to
    a guessed payment.

    Everything here is REPORTED, not interpreted. `provider_status` is Stripe's own string and
    `status` is its mapping into our vocabulary; the payment consequence is decided in
    crud.ingest_dispute_event. `reserve_amount` is what the network is holding back — on a
    `funds_withdrawn` event that is the full disputed amount, and before withdrawal it is zero,
    which is a materially different fact from the disputed amount itself (doc P4 "financial
    reserve/adjustment").
    """
    dispute_ref = obj.get("id")
    if not isinstance(dispute_ref, str) or not dispute_ref:
        return None

    currency = obj.get("currency")
    currency = currency.upper() if isinstance(currency, str) else None
    amount = None
    minor = obj.get("amount")
    if isinstance(minor, int) and currency:
        try:
            amount = from_minor_units(minor, currency)
        except ProviderInvalidRequest:
            amount = None

    provider_status = obj.get("status") if isinstance(obj.get("status"), str) else None

    # Stripe reports the deadline on the evidence_details sub-object.
    evidence_due_by = None
    details = obj.get("evidence_details")
    if isinstance(details, dict) and isinstance(details.get("due_by"), int):
        evidence_due_by = datetime.fromtimestamp(details["due_by"], tz=timezone.utc)

    # `payment_intent` is the reference our Payment rows are keyed on; `charge` is the fallback
    # Stripe still populates on older API versions.
    payment_ref = obj.get("payment_intent")
    if not isinstance(payment_ref, str) or not payment_ref:
        payment_ref = _intent_ref(obj)

    # Funds are withheld once the network actually pulls them. Before that the dispute is open
    # but nothing has moved.
    withdrawn = bool(obj.get("balance_transactions")) or provider_status in ("lost",)
    reserve_amount = amount if (withdrawn and amount is not None) else None

    return {
        "provider_dispute_ref": dispute_ref,
        "provider_payment_ref": payment_ref,
        "provider_status": provider_status,
        "status": STRIPE_DISPUTE_STATUS_MAP.get(provider_status or ""),
        "reason_code": obj.get("reason") if isinstance(obj.get("reason"), str) else None,
        "amount": amount,
        "currency": currency,
        "reserve_amount": reserve_amount,
        "evidence_due_by": evidence_due_by,
    }


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
