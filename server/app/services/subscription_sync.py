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
from .payments_stripe_events import _SUBSCRIPTION_STATUS_STATE

log = logging.getLogger("zoikostream.billing.sync")


def _local_interval(provider_interval: str | None) -> str | None:
    """Stripe's cadence word in OUR vocabulary, or None for one we do not model.

    Defined here rather than imported so this module depends only on the config vocabulary it
    is translating into — a reconciliation sweep elsewhere may come and go without taking the
    on-demand repair path down with it.
    """
    from ..config import ANNUAL, MONTHLY
    return {"month": MONTHLY, "year": ANNUAL}.get(provider_interval or "")

# Outcomes, reported to the caller so the console can say something true rather than spin.
SYNC_APPLIED = "applied"            # provider state differed and was applied
SYNC_ALREADY_CURRENT = "current"    # nothing to do
SYNC_NOT_NEEDED = "not_needed"      # this row is not in the stuck shape
SYNC_UNMAPPABLE = "unmappable"      # Stripe's answer has no Section 12 / plan counterpart
SYNC_REFUSED = "refused"            # the state machine rejected the transition
SYNC_PROVIDER_ERROR = "provider_error"


def needs_checkout_linkage(sub: Subscription | None) -> bool:
    """True when a checkout was started for this row and NO provider subscription was ever
    recorded against it.

    THE CASE THIS EXISTS FOR, observed in full: a tenant completed Stripe Checkout and paid.
    Stripe held `session.payment_status='paid'`, a customer, and an ACTIVE subscription. Our
    row held `checkout_session_ref` and nothing else — no customer id, no subscription id,
    still `trialing` on the old plan — because the webhook never arrived.

    Nothing could repair it. `needs_provider_sync` below starts from
    `has_live_provider_subscription`, which reads `stripe_subscription_id`; that is the very
    field the missing webhook was supposed to write. So the backstop could only run once the
    thing it was backing up had already succeeded, and the reconciliation sweep
    (maintenance.reconcile_stripe_subscriptions) deliberately only RECORDS this divergence as
    `checkout_paid_without_linkage` rather than repairing it.

    The checkout session reference is the way back in: Stripe's session carries the customer
    and subscription it created, so the link can be rebuilt from the one identifier we do
    hold. Self-limiting in the same way as below — once `stripe_subscription_id` is written
    this is False forever.
    """
    if sub is None or sub.stripe_subscription_id:
        return False
    return bool(getattr(sub, "checkout_session_ref", None))


def needs_provider_sync(sub: Subscription | None) -> bool:
    """True for exactly the divergence above: we hold a LIVE provider subscription, but our own
    state does not entitle the tenant to anything.

    Deliberately narrow. It is the condition for spending a Stripe API call, so it must not be
    true for a healthy row — an active subscription answers False and is never re-read. It is
    also self-limiting: a successful sync moves the row into an entitled state, after which this
    returns False forever.

    It ALSO answers True for an unlinked paid checkout (`needs_checkout_linkage`), because that
    row is stuck in a way no local state can resolve either — and, unlike the case above, it
    can be stuck while still looking entitled. The organization that prompted this was
    `trialing` on Developer, which IS an entitled state, so an entitlement test alone would
    have called it healthy while Stripe was already billing it.
    """
    if needs_checkout_linkage(sub):
        return True
    if not has_live_provider_subscription(sub):
        return False
    return normalize_subscription_state(sub.status) not in {
        normalize_subscription_state(s) for s in SUBSCRIPTION_ENTITLED_STATES
    }


def _legal_route(current: str | None, target: str) -> list[str]:
    """The Section 12 steps from `current` to `target`, or [] when there is no short one.

    One hop when the table allows it, otherwise one intermediate. Returns [] rather than
    guessing when neither reaches — the caller then reports the refusal instead of forcing a
    state the document does not permit.
    """
    from ..models.subscription import SUBSCRIPTION_TRANSITIONS

    here = normalize_subscription_state(current)
    there = normalize_subscription_state(target)
    if here == there:
        return []
    if there in SUBSCRIPTION_TRANSITIONS.get(here, ()):
        return [there]
    for middle in SUBSCRIPTION_TRANSITIONS.get(here, ()):
        if there in SUBSCRIPTION_TRANSITIONS.get(middle, ()):
            return [middle, there]
    # Unreachable in two steps. The applier is still called once with the target so the
    # refusal — and its reason — comes from the one place that owns that decision.
    return [there]


def _link_from_checkout_session(db: Session, sub: Subscription, provider) -> dict | None:
    """Copy the provider ids from a PAID checkout session onto `sub`.

    Returns None when the caller should carry on to the normal sync, or a result dict when it
    must stop (nothing to link, not paid, or the provider could not be read).

    The paid test is the safety property. A checkout that was opened and abandoned leaves the
    same `checkout_session_ref` behind as one that was completed, and the difference between
    them is the difference between a customer who paid and one who did not.
    """
    ref = sub.checkout_session_ref
    try:
        session = provider.retrieve_subscription_checkout_session(ref)
    except payment_svc.ProviderInvalidRequest:
        log.error("subscription %s cites checkout session %s that Stripe does not know",
                  sub.id, ref)
        return {"outcome": SYNC_UNMAPPABLE, "applied": False,
                "error": "Stripe has no record of this checkout. Please contact support."}
    except payment_svc.PaymentProviderError as exc:
        log.warning("checkout linkage could not reach Stripe: %s", type(exc).__name__)
        return {"outcome": SYNC_PROVIDER_ERROR, "applied": False,
                "error": "Couldn't reach Stripe to confirm your payment. Please try again."}

    if not session.paid:
        # Nothing was bought, so there is nothing to link and nothing to entitle. Reported as
        # the honest state rather than an error: an open session is a checkout in progress.
        return {"outcome": SYNC_NOT_NEEDED, "applied": False, "state": sub.status,
                "error": None if not session.terminal
                         else "That checkout was not completed. Please start a new one."}

    if not session.stripe_subscription_id:
        # Paid, but Stripe has not attached a subscription yet. Transient; the next poll or the
        # webhook resolves it, and inventing an id here would be worse than waiting.
        log.warning("checkout session %s is paid but carries no subscription id yet", ref)
        return {"outcome": SYNC_PROVIDER_ERROR, "applied": False,
                "error": "Your payment is confirmed and the subscription is still being set "
                         "up. Please refresh in a moment."}

    sub.stripe_subscription_id = session.stripe_subscription_id
    if session.stripe_customer_id and not sub.stripe_customer_id:
        sub.stripe_customer_id = session.stripe_customer_id
    db.commit()
    log.info("subscription %s linked to Stripe subscription from checkout session %s",
             sub.id, ref)
    return None


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

    # Rebuild the missing link first, when there is one to rebuild. Reads the checkout session
    # we DO hold and copies across the ids Stripe created, so the retrieve below has something
    # to ask about.
    #
    # It grants nothing. No status and no plan is written here: the row is only pointed at the
    # provider objects, and the Section 12 machine below still decides what that means. An
    # UNPAID session is refused outright, so a started-but-abandoned checkout can never link
    # itself into an entitlement.
    if needs_checkout_linkage(sub):
        linked = _link_from_checkout_session(db, sub, provider)
        if linked is not None:
            return linked

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

    # Walk the LEGAL route, which is not always one hop.
    #
    # Stripe reports a destination; Section 12 defines how a subscription may reach it. A row
    # sitting in `trialing` whose Stripe subscription is `active` cannot go straight there —
    # the table routes it `trialing -> conversion_pending -> active`, which is precisely the
    # two webhooks that went missing (`checkout.session.completed`, then
    # `customer.subscription.created`). Attempting the single hop is what produced
    # "Illegal subscription transition trialing -> active" and left a paying customer stuck.
    #
    # This is NOT a bypass. Every step still goes through `apply_subscription_provider_event`,
    # which refuses anything the table does not permit, and the plan is still applied only on
    # the successful move into `active`. It replays the route the webhooks would have taken,
    # which is what this module exists to do — "a pulled fact and a pushed fact are treated
    # identically; the only difference is who asked".
    #
    # Bounded to ONE intermediate on purpose. Section 12's graph needs no more than that to
    # reach any state a provider actually reports, and an unbounded search would be a way to
    # arrive somewhere the table never intended.
    applied, error = False, None
    for step in _legal_route(sub.status, target):
        applied, error = admin_crud.apply_subscription_provider_event(
            db, sub,
            new_state=step,
            plan=plan,
            stripe_customer_id=remote.stripe_customer_id,
            current_period_end=remote.current_period_end,
            # Marked as a PULL so the audit trail distinguishes it from a delivered webhook.
            reason="stripe:reconcile_on_read",
            actor=actor,
        )
        if not applied:
            break
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
