"""Pull one subscription's state from Stripe and apply it, for when the webhook did not arrive.

WHY THIS EXISTS
---------------
Ledger 1 advances a subscription in exactly one place: `customer.subscription.created` /
`.updated`, handled in routers/commercial.py. `checkout.session.completed` deliberately does
NOT activate — it binds the provider ids and moves the row to `conversion_pending`, because
Section 18 forbids unlocking on a completed checkout alone. Section 12 then offers exactly one
edge out of that state, `conversion_pending -> active`, and only the subscription event drives
it. The purchased plan is applied at that same moment and nowhere else.

So when the subscription event does not arrive, the row stops dead:

    stripe_subscription_id   set          -> has_live_provider_subscription() is TRUE
    status                   conversion_pending
                                          -> NOT in SUBSCRIPTION_ENTITLED_STATES
    plan_id                  the OLD plan -> the purchase is not reflected

which the console renders as "you have a subscription we cannot identify": no plan is entitled,
so no card can say Current Plan, and every card falls back to an inquiry. Meanwhile checkout
correctly refuses a second purchase with 409. The tenant has paid, cannot use what they paid
for, and cannot buy their way out. Nothing local can resolve it, because the missing fact is
not late — it is absent.

That is routine, not exotic: webhooks are undelivered in local development without
`stripe listen`, and are dropped, delayed or failed in any environment. Stripe's own guidance
is to treat the webhook as one delivery mechanism and reconcile against the API as the backstop.
payments_stripe.retrieve_subscription_checkout_session already exists for exactly this reason on
the checkout-guard side; this is the same move for the activation side.

WHAT IT IS NOT
--------------
Not a second source of commercial truth, and not a new lifecycle. It reads the SAME facts the
webhook would have carried, from the same provider, and applies them through the SAME state
machine (`apply_subscription_provider_event`), which still refuses any transition Section 12
does not permit and still applies the plan only on a successful move into `active`. A pulled
fact and a pushed fact are treated identically — the only difference is who asked.

It writes nothing to Stripe, and it can never create a subscription: the only call it makes is
a read of a subscription id our own row already holds.

Distinct from `maintenance.reconcile_stripe_subscriptions`, which sweeps every linked row and
deliberately only REPORTS divergence as audit evidence. This repairs ONE row, on demand, and
only the specific divergence described above.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..crud import admin as admin_crud
from ..models import Subscription
from ..models.subscription import (
    SUBSCRIPTION_ENTITLED_STATES,
    has_live_provider_subscription,
    normalize_subscription_state,
)
from . import payments as payment_svc
from .maintenance import _local_interval
from .payments_stripe_events import _SUBSCRIPTION_STATUS_STATE

log = logging.getLogger("zoikostream.billing.sync")

# Outcomes, reported to the caller so the console can say something true rather than spin.
SYNC_APPLIED = "applied"            # provider state differed and was applied
SYNC_ALREADY_CURRENT = "current"    # nothing to do
SYNC_NOT_NEEDED = "not_needed"      # this row is not in the stuck shape
SYNC_UNMAPPABLE = "unmappable"      # Stripe's answer has no Section 12 / plan counterpart
SYNC_REFUSED = "refused"            # the state machine rejected the transition
SYNC_PROVIDER_ERROR = "provider_error"


def needs_provider_sync(sub: Subscription | None) -> bool:
    """True for exactly the divergence above: we hold a LIVE provider subscription, but our own
    state does not entitle the tenant to anything.

    Deliberately narrow. It is the condition for spending a Stripe API call, so it must not be
    true for a healthy row — an active subscription answers False and is never re-read. It is
    also self-limiting: a successful sync moves the row into an entitled state, after which this
    returns False forever.
    """
    if not has_live_provider_subscription(sub):
        return False
    return normalize_subscription_state(sub.status) not in {
        normalize_subscription_state(s) for s in SUBSCRIPTION_ENTITLED_STATES
    }


def sync_from_provider(db: Session, sub: Subscription, *, actor=None) -> dict:
    """Read this subscription from Stripe and apply what it says. Returns a result dict.

    Never raises for a provider fault: the caller is a read path serving a person who is
    already confused about their billing, and replacing "confirming…" with a 500 helps nobody.
    The fault is returned as `{"outcome": SYNC_PROVIDER_ERROR, "error": ...}` so the console can
    say what actually went wrong instead of showing a pending state that will never clear.
    """
    if not settings_stripe_ready():
        return {"outcome": SYNC_PROVIDER_ERROR, "applied": False,
                "error": "Stripe is not configured for this deployment."}
    try:
        provider = payment_svc.get_provider("stripe")
    except payment_svc.PaymentProviderError as exc:
        log.warning("subscription sync unavailable: %s", type(exc).__name__)
        return {"outcome": SYNC_PROVIDER_ERROR, "applied": False,
                "error": "The payment provider is not reachable right now."}

    try:
        remote = provider.retrieve_subscription(sub.stripe_subscription_id)
    except payment_svc.ProviderInvalidRequest:
        # Stripe has no record of the id our row claims. NOT repaired here and NOT deleted —
        # the row is the evidence, exactly as the reconciliation sweep treats it.
        log.error("subscription %s claims a Stripe subscription Stripe does not know",
                  sub.id)
        return {"outcome": SYNC_UNMAPPABLE, "applied": False,
                "error": "Stripe has no record of this subscription. Please contact support."}
    except payment_svc.PaymentProviderError as exc:
        log.warning("subscription sync could not reach Stripe: %s", type(exc).__name__)
        return {"outcome": SYNC_PROVIDER_ERROR, "applied": False,
                "error": "Couldn't reach Stripe to confirm your subscription. Please try again."}

    # Status, in OUR vocabulary, through the one existing mapping. An unmapped Stripe status
    # ("incomplete", "paused", …) has no Section 12 counterpart and is NOT forced into one.
    target = _SUBSCRIPTION_STATUS_STATE.get(remote.provider_status or "")
    if target is None:
        return {"outcome": SYNC_UNMAPPABLE, "applied": False,
                "provider_status": remote.provider_status,
                "error": "Your payment hasn't completed at Stripe yet."}

    # Plan, resolved from the price Stripe is ACTUALLY billing and cross-checked against the
    # approved price configuration — never from provider metadata, never from the card clicked.
    plan, plan_error = admin_crud.resolve_plan_for_provider_price(db, remote.price_ids)
    if plan is None and target == "active":
        # Activating without knowing WHICH plan was bought would entitle the tenant to whatever
        # they previously had while Stripe bills something else. Refused rather than guessed.
        log.error("subscription %s: Stripe price %s resolves to no approved plan (%s)",
                  sub.id, list(remote.price_ids), plan_error)
        return {"outcome": SYNC_UNMAPPABLE, "applied": False,
                "error": "This subscription's price isn't mapped to a plan. Please contact support."}

    if normalize_subscription_state(sub.status) == target and (
            plan is None or sub.plan_id == plan.id):
        return {"outcome": SYNC_ALREADY_CURRENT, "applied": False, "state": sub.status}

    applied, error = admin_crud.apply_subscription_provider_event(
        db, sub,
        new_state=target,
        plan=plan,
        stripe_customer_id=remote.stripe_customer_id,
        current_period_end=remote.current_period_end,
        # Marked as a PULL so the audit trail distinguishes it from a delivered webhook.
        reason="stripe:reconcile_on_read",
        actor=actor,
    )
    if not applied:
        # The state machine refused: Stripe's answer is not reachable from where we are. Audited
        # inside the applier; surfaced here so the console stops waiting on it.
        db.commit()          # provider ids/period recorded even on a refusal — see the applier
        return {"outcome": SYNC_REFUSED, "applied": False, "state": sub.status,
                "error": error or "This subscription can't be updated automatically."}

    # Cadence, recorded when Stripe reports one and we have none. Bookkeeping only: it grants
    # nothing, and an existing value is never overwritten from here.
    interval = _local_interval(remote.provider_interval)
    if interval and not sub.billing_interval:
        sub.billing_interval = interval

    db.commit()
    log.info("subscription %s synced from Stripe: state=%s plan=%s",
             sub.id, sub.status, plan.slug if plan else None)
    return {"outcome": SYNC_APPLIED, "applied": True, "state": sub.status,
            "plan_slug": plan.slug if plan else None}


def settings_stripe_ready() -> bool:
    """Imported lazily so this module stays importable in environments without Stripe set up."""
    from ..config import settings
    return settings.stripe_configured()
