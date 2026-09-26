"""Pulling a subscription's state from Stripe when the activation webhook never arrived.

THE BUG THESE PIN
-----------------
`checkout.session.completed` binds the Stripe ids and moves the row to `conversion_pending`,
which is deliberately NOT entitled (Section 18). Only `customer.subscription.created/updated`
advances it to `active` and applies the purchased plan. When that event is not delivered — no
`stripe listen` locally, a dropped or failed delivery anywhere else — the row is stranded:

    has_live_provider_subscription -> True   (checkout rightly refuses a second purchase, 409)
    status in ENTITLED_STATES      -> False  (entitlements() reports no plan at all)

so the payer holds a subscription that entitles them to nothing and cannot buy their way out.
Nothing local can fix it, because the missing fact lives at Stripe.

These exercise the pull path against a fake provider — no network, no Stripe test key needed,
and no database: `sync_from_provider` touches only the row handed to it and the price map.
"""

import pytest

from _testsupport import code_only

from app.services import payments as payment_svc
from app.services import subscription_sync as sync_svc


class _Plan:
    def __init__(self, pid, slug):
        self.id, self.slug = pid, slug


class _Sub:
    """Only the attributes the sync path touches."""

    def __init__(self, status="conversion_pending", sub_id="sub_live_1", plan_id="plan-dev"):
        self.id = "row-1"
        self.org_id = "org-1"
        self.status = status
        self.stripe_subscription_id = sub_id
        self.stripe_customer_id = None
        self.plan_id = plan_id
        self.billing_interval = None
        self.current_period_end = None
        self.cancelled_at = None


class _Remote:
    def __init__(self, provider_status="active", price_ids=("price_business_m",),
                 provider_interval="month"):
        self.provider_subscription_ref = "sub_live_1"
        self.provider_status = provider_status
        self.price_ids = price_ids
        self.provider_interval = provider_interval
        self.current_period_end = None
        self.stripe_customer_id = "cus_1"
        self.cancel_at_period_end = False


class _Db:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


@pytest.fixture
def wired(monkeypatch):
    """Stripe configured, a provider that answers from a settable `remote`, and a price map
    that resolves to Business. Each piece is overridable per test."""
    state = {"remote": _Remote(), "raise": None,
             "plan": _Plan("plan-biz", "business"), "plan_error": None,
             "applied": (True, None), "calls": []}

    class _Provider:
        def retrieve_subscription(self, ref):
            state["calls"].append(ref)
            if state["raise"] is not None:
                raise state["raise"]
            return state["remote"]

    monkeypatch.setattr(sync_svc, "settings_stripe_ready", lambda: True)
    monkeypatch.setattr(sync_svc.payment_svc, "get_provider", lambda name: _Provider())
    monkeypatch.setattr(sync_svc.admin_crud, "resolve_plan_for_provider_price",
                        lambda db, price_ids: (state["plan"], state["plan_error"]))

    def _apply(db, sub, *, new_state, plan=None, **kw):
        ok, err = state["applied"]
        if ok:
            sub.status = new_state
            if plan is not None and new_state == "active":
                sub.plan_id = plan.id
        return ok, err

    monkeypatch.setattr(sync_svc.admin_crud, "apply_subscription_provider_event", _apply)
    return state


# ── which rows are even eligible ──────────────────────────────────────────────────────────

def test_only_a_stranded_subscription_is_synced():
    """The gate for spending a Stripe API call. A healthy row must never trigger one."""
    # Stranded: live provider subscription, non-entitled state. The whole point.
    for stuck in ("conversion_pending", "pending_activation", "suspended", "trial_eligible"):
        assert sync_svc.needs_provider_sync(_Sub(status=stuck)) is True, stuck

    # Entitled: nothing to repair, so nothing is read.
    for healthy in ("active", "trialing", "past_due", "plan_change_scheduled"):
        assert sync_svc.needs_provider_sync(_Sub(status=healthy)) is False, healthy

    # No provider subscription at all: this is a normal pre-purchase org.
    assert sync_svc.needs_provider_sync(_Sub(sub_id=None)) is False
    assert sync_svc.needs_provider_sync(None) is False


def test_a_synced_row_stops_being_eligible(wired):
    """Self-limiting: the repair moves the row into an entitled state, so it is never re-read."""
    sub = _Sub()
    assert sync_svc.needs_provider_sync(sub) is True
    sync_svc.sync_from_provider(_Db(), sub)
    assert sub.status == "active"
    assert sync_svc.needs_provider_sync(sub) is False


# ── the repair itself ─────────────────────────────────────────────────────────────────────

def test_an_active_stripe_subscription_activates_the_row_and_applies_the_plan(wired):
    sub = _Sub()
    result = sync_svc.sync_from_provider(_Db(), sub)
    assert result["outcome"] == sync_svc.SYNC_APPLIED
    assert result["applied"] is True
    assert sub.status == "active"
    # The plan comes from the price STRIPE is billing, not from the card the user clicked.
    assert sub.plan_id == "plan-biz"
    assert result["plan_slug"] == "business"


def test_the_plan_comes_from_the_price_stripe_bills(wired):
    """A Developer price must land on Developer even though the row was created elsewhere."""
    wired["plan"] = _Plan("plan-dev", "developer")
    sub = _Sub(plan_id="plan-old")
    result = sync_svc.sync_from_provider(_Db(), sub)
    assert result["plan_slug"] == "developer"
    assert sub.plan_id == "plan-dev"


def test_the_recorded_cadence_is_filled_in_when_absent(wired):
    sub = _Sub()
    sync_svc.sync_from_provider(_Db(), sub)
    assert sub.billing_interval == "monthly"


def test_an_existing_cadence_is_never_overwritten(wired):
    sub = _Sub()
    sub.billing_interval = "annual"
    wired["remote"] = _Remote(provider_interval="month")
    sync_svc.sync_from_provider(_Db(), sub)
    assert sub.billing_interval == "annual", "bookkeeping must not rewrite a recorded cadence"


def test_a_row_already_matching_stripe_is_left_alone(wired):
    sub = _Sub(status="active", plan_id="plan-biz")
    result = sync_svc.sync_from_provider(_Db(), sub)
    assert result["outcome"] == sync_svc.SYNC_ALREADY_CURRENT
    assert result["applied"] is False


# ── what it refuses to guess ──────────────────────────────────────────────────────────────

def test_an_unmapped_stripe_status_is_reported_not_forced(wired):
    """`incomplete`/`paused` have no Section 12 counterpart. Inventing one would be a
    commercial rule this code has no authority to add."""
    wired["remote"] = _Remote(provider_status="incomplete")
    sub = _Sub()
    result = sync_svc.sync_from_provider(_Db(), sub)
    assert result["outcome"] == sync_svc.SYNC_UNMAPPABLE
    assert result["applied"] is False
    assert sub.status == "conversion_pending", "must not activate on an incomplete payment"
    assert result["error"]


def test_activation_is_refused_when_the_price_maps_to_no_plan(wired):
    """Activating without knowing WHICH plan was bought would entitle the tenant to whatever
    they had before while Stripe bills something else."""
    wired["plan"] = None
    wired["plan_error"] = "no approved plan for price_xyz"
    sub = _Sub()
    result = sync_svc.sync_from_provider(_Db(), sub)
    assert result["outcome"] == sync_svc.SYNC_UNMAPPABLE
    assert sub.status == "conversion_pending"


def test_a_subscription_stripe_does_not_know_is_a_finding_not_a_deletion(wired):
    wired["raise"] = payment_svc.ProviderInvalidRequest("no such subscription")
    sub = _Sub()
    result = sync_svc.sync_from_provider(_Db(), sub)
    assert result["outcome"] == sync_svc.SYNC_UNMAPPABLE
    assert sub.stripe_subscription_id == "sub_live_1", "the row is the evidence; never cleared"


def test_an_illegal_transition_is_refused_by_the_state_machine(wired):
    """Section 12 still decides. The pull path has no power the webhook path lacks."""
    wired["applied"] = (False, "trial_expired -> active is not a legal transition")
    sub = _Sub(status="trial_eligible")
    result = sync_svc.sync_from_provider(_Db(), sub)
    assert result["outcome"] == sync_svc.SYNC_REFUSED
    assert result["error"]


# ── failing safely ────────────────────────────────────────────────────────────────────────

def test_a_provider_outage_returns_an_error_rather_than_raising(wired):
    """The caller is a read path serving somebody already confused about their billing;
    replacing 'confirming…' with a 500 helps nobody. But it must not claim success either."""
    wired["raise"] = payment_svc.ProviderUnavailable("stripe down")
    sub = _Sub()
    result = sync_svc.sync_from_provider(_Db(), sub)
    assert result["outcome"] == sync_svc.SYNC_PROVIDER_ERROR
    assert result["applied"] is False
    assert result["error"], "the UI must be able to say what went wrong"
    assert sub.status == "conversion_pending"


def test_no_stripe_configured_is_not_a_crash(monkeypatch):
    monkeypatch.setattr(sync_svc, "settings_stripe_ready", lambda: False)
    result = sync_svc.sync_from_provider(_Db(), _Sub())
    assert result["outcome"] == sync_svc.SYNC_PROVIDER_ERROR
    assert result["applied"] is False


def test_no_error_message_names_a_stripe_identifier(wired):
    """Errors reach an org admin's screen. Which subscription/customer/price this is is for the
    audit log, not the console."""
    for setup in ({"raise": payment_svc.ProviderUnavailable("boom")},
                  {"raise": payment_svc.ProviderInvalidRequest("no such sub_live_1")},
                  {"plan": None, "plan_error": "price_business_m unapproved"},
                  {"remote": _Remote(provider_status="incomplete")}):
        wired.update(setup)
        result = sync_svc.sync_from_provider(_Db(), _Sub())
        message = result.get("error") or ""
        for leak in ("sub_", "cus_", "price_", "cs_test_", "cs_live_"):
            assert leak not in message, f"{leak!r} leaked into {message!r}"
        wired["raise"] = None
        wired["plan"] = _Plan("plan-biz", "business")
        wired["plan_error"] = None
        wired["remote"] = _Remote()


# ── structural guarantees ─────────────────────────────────────────────────────────────────

def test_the_sync_path_can_never_create_a_subscription():
    """Its only provider call is a READ of an id our row already holds."""
    src = code_only(sync_svc.sync_from_provider)
    assert "retrieve_subscription" in src
    for forbidden in ("create_subscription_checkout", "checkout", "create_subscription",
                      "subscriptions.create"):
        assert forbidden not in src, f"{forbidden} must not appear in a read-only sync"


def test_the_sync_path_goes_through_the_section_12_state_machine():
    src = code_only(sync_svc.sync_from_provider)
    assert "apply_subscription_provider_event" in src, \
        "a pulled fact must be applied exactly like a pushed one"
    # Never by direct assignment, which would bypass the transition rules.
    assert "sub.status =" not in src


def test_the_duplicate_checkout_guard_is_untouched():
    """The repair must not become a way around the 409."""
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    assert "has_live_provider_subscription" in src
    assert "already has a paid subscription" in src
    assert "subscription_sync" not in src, "checkout must not sync its way past the guard"
