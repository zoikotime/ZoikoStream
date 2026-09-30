"""An organization buying its FIRST subscription.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
`POST /organization/billing/checkout-session` opened with:

    sub = admin_crud.current_subscription_for_update(db, org.id)
    if sub is None:
        raise HTTPException(409, "This organization has no subscription record to upgrade")

so the very first paid subscription was impossible for any organization that did not already
have a row. Registration provisions one (routers/auth.py), which is why newer tenants were
unaffected — but every organization created before that existed reached this line and could
never buy anything. The 409 fired BEFORE Stripe, so checkout never even opened.

The refusal was protecting an implementation detail, not a commercial rule: the subscription
row is what the webhook correlates a completed checkout against. So the row is now created
here when it is missing, through the same approved path registration uses.
"""
from _testsupport import code_only

from app.crud import admin as admin_crud
from app.models.subscription import SUBSCRIPTION_TRANSITIONS


def _checkout_src():
    from app.routers import organization
    return code_only(organization.create_subscription_checkout)


# ── the fix itself ────────────────────────────────────────────────────────────────────────

def test_a_missing_subscription_no_longer_refuses_the_first_purchase():
    src = _checkout_src()
    assert "no subscription record to upgrade" not in src, \
        "a first-time buyer must not be refused for not already being a customer"


def test_the_row_is_created_through_the_one_approved_provisioning_path():
    """Not hand-rolled here. `provision_initial_subscription` is the single implementation of
    the approved rule (plan, state, trial length), shared by registration, the admin console
    and the backfill migration — so this cannot drift from what those produce."""
    src = _checkout_src()
    assert "provision_initial_subscription" in src
    # No inline Subscription construction, which is how a second, divergent rule appears.
    assert "Subscription(" not in src


def test_the_provisioned_state_can_actually_reach_active():
    """The row is only useful if the webhook can move it. Provisioning creates `trialing`, and
    Section 12 routes trialing -> conversion_pending -> active — which is exactly the path
    checkout.session.completed and customer.subscription.created already drive. A state with
    no route to active would leave the payer stranded after paying."""
    assert "conversion_pending" in SUBSCRIPTION_TRANSITIONS["trialing"]
    assert "active" in SUBSCRIPTION_TRANSITIONS["conversion_pending"]


def test_provisioning_creates_no_stripe_object_and_charges_nothing():
    """It must establish a local record ONLY. Creating a provider object here would mean a
    subscription existing at Stripe before the customer had entered any payment details."""
    src = code_only(admin_crud.provision_initial_subscription)
    # Checked as CALLS, not as a substring: the audit row it writes deliberately records
    # `"stripe": "none - no card required, no charge, no Stripe subscription"`, and a blanket
    # search for the word flags that note — which asserts the very thing being tested.
    for forbidden in ("get_provider", "create_subscription_checkout_session",
                      "stripe_subscription_id =", "resolve_subscription_price_id"):
        assert forbidden not in src, f"{forbidden} must not appear in provisioning"
    # And no provider reference is written onto the row it creates.
    assert "stripe_customer_id=" not in src and "stripe_subscription_id=" not in src


def test_the_row_is_re_read_under_the_write_lock_after_provisioning():
    """The rest of the endpoint binds a checkout session to this row, and the duplicate guard
    depends on holding it. Provisioning commits, so the lock taken before it is gone — the row
    has to be re-acquired or two concurrent first purchases could each bind their own session."""
    src = _checkout_src()
    assert src.count("current_subscription_for_update") >= 2, \
        "the row must be re-read under the lock after being created"
    provision_at = src.index("provision_initial_subscription")
    assert src.index("current_subscription_for_update", provision_at) > provision_at


def test_a_plan_that_cannot_be_provisioned_still_refuses_clearly():
    """provision_initial_subscription returns None when the slug is absent from the catalog.
    Continuing with `sub = None` would crash further down; the customer gets a refusal."""
    src = _checkout_src()
    assert "could not be set up for billing" in src


# ── what must NOT have changed ────────────────────────────────────────────────────────────

def test_the_duplicate_subscription_guard_is_untouched():
    """Self-healing a missing row must not become a way past the 409 that stops an existing
    payer being billed twice."""
    src = _checkout_src()
    assert "has_live_provider_subscription" in src
    assert "already has an active subscription" in src


def test_nothing_is_marked_active_by_this_endpoint():
    """Section 18: only a verified provider event may activate. Starting a checkout must not
    move the subscription's state at all."""
    src = _checkout_src()
    assert "status = " not in src and '"active"' not in src


def test_the_organization_still_comes_from_the_session_not_the_request():
    """Provisioning now happens inside this endpoint, so the org it provisions for must still
    be the caller's own — never one named in the body."""
    src = _checkout_src()
    assert ("get_my_org_billing" in src) or ("get_my_org_admin" in src)
    assert "data.org_id" not in src


def test_provisioning_is_skipped_entirely_when_a_row_already_exists():
    """An existing customer must take the unchanged path: the provisioning call sits behind
    the `is None` branch, so it cannot run for anybody who already has a subscription."""
    src = _checkout_src()
    none_at = src.index("if sub is None")
    provision_at = src.index("provision_initial_subscription")
    assert none_at < provision_at, "provisioning must be inside the missing-row branch"
