"""Ledger 1 subscription checkout — Stripe-hosted, ZST-COM-PLAN-001 Sections 12/13/18.

The chain under test:
    plan slug -> server-resolved approved Stripe Price ID -> subscription-mode Checkout Session
    -> signed webhook -> Section 12 state machine -> Billing reflects the confirmed state.

NO REAL STRIPE CALLS. The provider is replaced with a fake at the `get_provider` boundary, and
webhook tests sign their own payloads with a throwaway secret — the same approach the existing
Ledger 2 suites use. `_testsupport.code_only` is used for the structural assertions.

Nothing here asserts a price, currency, interval or trial length: those are ZST-COM-PRICE-001
facts, deliberately absent. What IS asserted is that the code cannot invent them.
"""
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.config import settings
from app.crud import admin as admin_crud
from app.db import engine
from app.models import AuditLog, Organization, Plan, Subscription, User


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")

APPROVED_PRICE = "price_TESTonly_not_a_real_stripe_price"


# ══════════════════════════════════════════════════════════════════════════════════════
# Price resolution — the authority boundary
# ══════════════════════════════════════════════════════════════════════════════════════

def _plan(slug="developer"):
    return SimpleNamespace(slug=slug, name=slug.title(), is_active=True)


def test_price_map_is_empty_until_an_operator_configures_it(monkeypatch):
    """Fail-closed default. ZST-COM-PLAN-001 Section 23 makes ZST-COM-PRICE-001 a prerequisite
    "before public numeric pricing and live charging" — so shipping with no mapping must mean
    no charging, not a default price."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", "")
    assert settings.subscription_price_map() == {}


def test_an_unpriced_plan_cannot_be_sold(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", "")
    with pytest.raises(ValueError, match="No approved Stripe price is configured"):
        admin_crud.resolve_subscription_price_id(_plan())


def test_a_configured_plan_resolves_to_its_approved_price(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", f"developer={APPROVED_PRICE}")
    assert admin_crud.resolve_subscription_price_id(_plan()) == APPROVED_PRICE


def test_one_plans_price_is_never_used_for_another(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", f"developer={APPROVED_PRICE}")
    with pytest.raises(ValueError, match="business"):
        admin_crud.resolve_subscription_price_id(_plan("business"))


@pytest.mark.parametrize("bad", [
    "developer=prod_123",        # a Product id, not a Price
    "developer=1999",            # an amount
    "developer=",                # empty
    "developer",                 # no separator
    "=price_abc",                # no plan
])
def test_malformed_price_configuration_is_dropped_not_guessed(monkeypatch, bad):
    """A half-parsed mapping that silently charged the wrong thing would be worse than none."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", bad)
    assert settings.subscription_price_map() == {}


def test_no_literal_stripe_price_id_exists_in_source():
    """The mapping is CONFIGURATION (Section 18: no price embedded in service constants)."""
    import re
    from app import config
    from app.crud import admin as admin_mod
    from app.services import payments_stripe
    # A real Stripe Price ID is `price_` + a long mixed alphanumeric containing digits
    # (e.g. price_1QxYzAbC...). Requiring a digit means a legitimate identifier such as
    # `price_monthly` (the Plan column) or `price_map` cannot satisfy it.
    literal = re.compile(r"\bprice_(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{10,}\b")
    for module in (config, admin_mod, payments_stripe):
        found = literal.findall(code_only(module))
        assert not found, f"{module.__name__} hardcodes a Stripe Price ID: {found}"


# ══════════════════════════════════════════════════════════════════════════════════════
# Checkout session creation — subscription mode, server-side price
# ══════════════════════════════════════════════════════════════════════════════════════

@dataclass
class _FakeResult:
    checkout_session_ref: str
    checkout_url: str
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None


class _FakeProvider:
    """Stands in for Stripe at the get_provider() boundary — no network, no SDK."""

    def __init__(self):
        self.calls = []

    def create_subscription_checkout_session(self, **kw):
        self.calls.append(kw)
        return _FakeResult(checkout_session_ref=f"cs_test_{uuid.uuid4().hex[:12]}",
                           checkout_url="https://checkout.stripe.com/c/pay/cs_test_x")


def test_adapter_uses_subscription_mode_not_payment_mode():
    """Ledger 1 must create a recurring Subscription, not a one-off PaymentIntent."""
    from app.services import payments_stripe
    src = code_only(payments_stripe.StripePaymentProvider.create_subscription_checkout_session)
    assert '"mode": "subscription"' in src or "'mode': 'subscription'" in src
    assert "capture_method" not in src, "capture_method belongs to payment mode only"


def test_adapter_refuses_a_missing_price_id():
    from app.services.payments_stripe import StripePaymentProvider
    from app.services.payments import ProviderInvalidRequest
    provider = StripePaymentProvider.__new__(StripePaymentProvider)
    with pytest.raises(ProviderInvalidRequest, match="approved Stripe Price ID"):
        provider.create_subscription_checkout_session(
            price_id="", idempotency_key="k", success_url="https://a", cancel_url="https://b",
            metadata={})


def test_adapter_requires_both_return_urls():
    from app.services.payments_stripe import StripePaymentProvider
    from app.services.payments import ProviderInvalidRequest
    provider = StripePaymentProvider.__new__(StripePaymentProvider)
    with pytest.raises(ProviderInvalidRequest, match="success and a cancel URL"):
        provider.create_subscription_checkout_session(
            price_id=APPROVED_PRICE, idempotency_key="k", success_url="", cancel_url="https://b",
            metadata={})


def test_checkout_route_accepts_only_canonical_identifiers():
    """The browser must have no field through which to influence what it is CHARGED.

    Widened from `fields == {"plan_slug"}` when the approved price book introduced annual
    billing. The guarantee is unchanged and is what is asserted here: every accepted field is a
    canonical IDENTIFIER the server resolves against its own configuration, and no field is
    price-BEARING. `billing_interval` is a closed two-value vocabulary (see
    test_the_request_body_accepts_only_a_closed_interval_vocabulary) that chooses between
    prices an operator already approved — it cannot name, alter or introduce one, and an
    unconfigured cadence is refused rather than substituted.
    """
    from app.schemas.organization import SubscriptionCheckoutCreate
    fields = set(SubscriptionCheckoutCreate.model_fields)
    assert fields == {"plan_slug", "billing_interval"}
    # No price-bearing field. `interval` alone is deliberately NOT forbidden any more — the
    # cadence selector legitimately contains that word — but everything that could carry an
    # amount still is.
    for forbidden in ("amount", "price", "price_id", "currency", "unit_amount", "coupon",
                      "discount", "trial_days", "trial_period_days"):
        assert forbidden not in fields


def test_route_never_returns_the_price_id_to_the_browser():
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    # code_only normalizes string quoting, so match the bare key rather than a quoted form.
    assert "checkout_url" in src
    assert "price_id" not in src.split("return")[-1], \
        "the response must not echo the Stripe Price ID back to the browser"


def test_route_derives_the_organization_from_the_session_not_the_body():
    """One tenant must not be able to open checkout against another's subscription."""
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    assert "get_my_org_admin" in src
    assert "data.org_id" not in src


def test_starting_checkout_does_not_change_subscription_state():
    """Section 18: nothing is unlocked because a checkout began."""
    from app.crud import admin as admin_mod
    src = code_only(admin_mod.record_subscription_checkout_started)
    assert "checkout_session_ref" in src
    assert ".status =" not in src, "starting checkout must not assign a subscription state"


# ══════════════════════════════════════════════════════════════════════════════════════
# Webhook -> Section 12 state machine
# ══════════════════════════════════════════════════════════════════════════════════════

def _sub_event(event_type, *, sub_id, status, customer="cus_test_1", org_id=None):
    return {
        "id": f"evt_{uuid.uuid4().hex[:16]}", "object": "event", "type": event_type,
        "created": int(datetime.now(timezone.utc).timestamp()),
        "data": {"object": {"id": sub_id, "object": "subscription", "status": status,
                            "customer": customer,
                            "metadata": {"org_id": str(org_id)} if org_id else {}}},
    }


def test_subscription_events_translate_to_documented_states():
    from app.services import payments_stripe_events as ev
    cases = [("active", "active"), ("trialing", "trialing"),
             ("past_due", "past_due"), ("unpaid", "past_due")]
    for stripe_status, expected in cases:
        facts = ev.subscription_facts(
            _sub_event("customer.subscription.updated", sub_id="sub_1", status=stripe_status))
        assert facts["state"] == expected


def test_a_deleted_subscription_is_canceled_regardless_of_status():
    from app.services import payments_stripe_events as ev
    facts = ev.subscription_facts(
        _sub_event("customer.subscription.deleted", sub_id="sub_1", status="active"))
    assert facts["state"] == "canceled"


@pytest.mark.parametrize("stripe_status", ["incomplete", "incomplete_expired", "paused"])
def test_unmapped_stripe_statuses_yield_no_state_rather_than_a_guess(stripe_status):
    """Section 12 has no state for a half-finished signup; inventing one would be a commercial
    rule this code has no authority to add."""
    from app.services import payments_stripe_events as ev
    facts = ev.subscription_facts(
        _sub_event("customer.subscription.updated", sub_id="sub_1", status=stripe_status))
    assert facts["state"] is None


def test_a_payment_mode_checkout_is_not_treated_as_a_subscription():
    """Ledger separation: an event-order session must never reach the subscription path."""
    from app.services import payments_stripe_events as ev
    event = {"id": "evt_1", "type": "checkout.session.completed",
             "data": {"object": {"id": "cs_1", "mode": "payment"}}}
    assert ev.checkout_subscription_facts(event) is None


def test_a_subscription_mode_checkout_is_correlation_only():
    """It carries ids to match on, and deliberately no state — Section 18."""
    from app.services import payments_stripe_events as ev
    event = {"id": "evt_1", "type": "checkout.session.completed",
             "data": {"object": {"id": "cs_1", "mode": "subscription",
                                 "subscription": "sub_1", "customer": "cus_1",
                                 "metadata": {}}}}
    facts = ev.checkout_subscription_facts(event)
    assert facts["stripe_subscription_id"] == "sub_1"
    assert "state" not in facts


def test_webhook_handler_routes_through_the_state_machine():
    from app.routers import commercial
    src = code_only(commercial._handle_subscription_event)
    assert "apply_subscription_provider_event" in src
    assert ".status =" not in src, "the webhook must not assign a state directly"


def test_webhook_matches_only_on_provider_references():
    """Never on amount, email or customer name — fuzzy matching is how one tenant's billing
    lands on another's ledger."""
    from app.crud import admin as admin_mod
    src = code_only(admin_mod.subscription_by_provider_ref)
    for forbidden in ("email", "amount", "name", "ilike"):
        assert forbidden not in src


def test_signature_verification_still_precedes_subscription_handling():
    from app.routers import commercial
    src = code_only(commercial.stripe_webhook)
    assert src.index("verify_and_parse") < src.index("_handle_subscription_event")


# ── State transitions applied through Section 12 ──────────────────────────────────────

class _Stub:
    def __init__(self):
        self.added = []

    def add(self, o):
        self.added.append(o)

    def commit(self):
        pass

    def refresh(self, o):
        pass


def _sub(status="trialing"):
    return Subscription(id=uuid.uuid4(), org_id=uuid.uuid4(), plan_id=uuid.uuid4(),
                        status=status, seats=1)


def test_a_legal_provider_transition_is_applied_and_audited():
    sub, db = _sub("conversion_pending"), _Stub()
    applied, error = admin_crud.apply_subscription_provider_event(
        db, sub, new_state="active", stripe_subscription_id="sub_1",
        stripe_customer_id="cus_1", reason="stripe:customer.subscription.created")
    assert applied and error is None
    assert sub.status == "active"
    assert sub.stripe_subscription_id == "sub_1"
    entry = next(o for o in db.added if isinstance(o, AuditLog))
    assert entry.action == "subscription.transition"


def test_an_illegal_provider_transition_is_refused_not_forced():
    """A payment event must never drag a subscription into a state Section 12 forbids."""
    sub, db = _sub("canceled"), _Stub()
    applied, error = admin_crud.apply_subscription_provider_event(
        db, sub, new_state="active", reason="stripe:customer.subscription.updated")
    assert applied is False
    assert "Illegal subscription transition" in error
    assert sub.status == "canceled", "a refused transition must not mutate the row"
    entry = next(o for o in db.added if isinstance(o, AuditLog))
    assert entry.action == "subscription.transition_rejected"


def test_a_replayed_event_is_an_idempotent_no_op():
    sub, db = _sub("active"), _Stub()
    applied, error = admin_crud.apply_subscription_provider_event(db, sub, new_state="active")
    assert applied is False and error is None
    assert not [o for o in db.added if isinstance(o, AuditLog)], \
        "a replay must not write a second transition row"


def test_cancellation_stamps_the_cancellation_time():
    sub, db = _sub("active"), _Stub()
    admin_crud.apply_subscription_provider_event(db, sub, new_state="canceled")
    assert sub.status == "canceled" and sub.cancelled_at is not None


# ══════════════════════════════════════════════════════════════════════════════════════
# Success redirect is NOT confirmation
# ══════════════════════════════════════════════════════════════════════════════════════

def test_checkout_status_reads_the_backend_not_the_query_string():
    from app.routers import organization
    src = code_only(organization.subscription_checkout_status)
    assert "subscription_by_provider_ref" in src
    assert "payment_confirmed" in src
    # It must not accept a status/plan from the caller.
    assert "data.status" not in src


def test_checkout_status_is_pending_until_a_webhook_binds_the_subscription():
    from app.routers import organization
    src = code_only(organization.subscription_checkout_status)
    assert "stripe_subscription_id is not None" in src, \
        "confirmation must depend on a webhook-supplied reference, not on the redirect"


@needs_db
class TestCheckoutStatusAgainstPostgres:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            org = Organization(name=f"sc-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="Buyer", email=f"b-{uuid.uuid4().hex[:8]}@t.test",
                       username=f"b{uuid.uuid4().hex[:8]}", password_hash="x", role="org_admin")
            plan = Plan(name="Developer", slug=f"dev-{uuid.uuid4().hex[:6]}", is_active=True,
                        currency="USD")
            db.add_all([user, plan])
            db.flush()
            sub = Subscription(org_id=org.id, plan_id=plan.id, status="trialing", seats=1)
            db.add(sub)
            db.commit()
            ids = SimpleNamespace(org=org, org_id=org.id, user=user, plan=plan, sub_id=sub.id)
            yield db, ids
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM plans WHERE id=:p"), {"p": plan.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.commit()

    def test_checkout_start_records_the_session_without_activating(self, ctx):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.record_subscription_checkout_started(
            db, sub, checkout_session_ref="cs_test_abc", actor=ids.user)
        db.commit()
        refreshed = db.get(Subscription, ids.sub_id)
        assert refreshed.checkout_session_ref == "cs_test_abc"
        assert refreshed.status == "trialing", "starting checkout must not activate anything"
        assert refreshed.stripe_subscription_id is None

    def test_a_subscription_is_found_by_its_session_reference(self, ctx):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.record_subscription_checkout_started(db, sub, checkout_session_ref="cs_find_me")
        db.commit()
        found = admin_crud.subscription_by_provider_ref(db, checkout_session_ref="cs_find_me")
        assert found is not None and found.id == ids.sub_id

    def test_an_unknown_session_reference_matches_nothing(self, ctx):
        db, _ = ctx
        assert admin_crud.subscription_by_provider_ref(
            db, checkout_session_ref="cs_never_issued") is None

    def test_the_full_webhook_confirmation_path_activates_through_section_12(self, ctx):
        """End-to-end at the data layer: checkout started -> webhook binds and activates."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.record_subscription_checkout_started(db, sub, checkout_session_ref="cs_flow")
        db.commit()

        # Correlation (checkout.session.completed) — binds ids, changes no state.
        found = admin_crud.subscription_by_provider_ref(db, checkout_session_ref="cs_flow")
        found.stripe_subscription_id = "sub_flow_1"
        db.commit()
        assert db.get(Subscription, ids.sub_id).status == "trialing"

        # Activation (customer.subscription.created) — trialing -> conversion_pending -> active,
        # each step legal under Section 12.
        for state in ("conversion_pending", "active"):
            applied, error = admin_crud.apply_subscription_provider_event(
                db, found, new_state=state, reason="stripe:test")
            assert applied and error is None, error
        db.commit()
        assert db.get(Subscription, ids.sub_id).status == "active"


# ══════════════════════════════════════════════════════════════════════════════════════
# Section 12 conversion path + purchased-plan assignment
#
# The graph transcribed in models/subscription.py offers exactly ONE route from a trial to a
# paid subscription: TRIALING -> CONVERSION_PENDING -> ACTIVE. These tests pin both edges to
# the real provider events that drive them, and pin the plan to the price actually billed.
# ══════════════════════════════════════════════════════════════════════════════════════

def _audit_for(org_id):
    from sqlalchemy import select
    return select(AuditLog).where(AuditLog.org_id == org_id)


def _conv_event(stripe_sub_id, status_, *, price_id=None, event_id=None, event_type=None,
               metadata=None, customer="cus_t"):
    """A customer.subscription.* event shaped like Stripe's real payload."""
    items = {"object": "list", "data": []}
    if price_id:
        items["data"] = [{"id": "si_1", "object": "subscription_item",
                          "price": {"id": price_id, "object": "price"}}]
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:16]}", "object": "event",
        "type": event_type or "customer.subscription.updated",
        "created": 1, "livemode": False,
        "data": {"object": {"id": stripe_sub_id, "object": "subscription", "status": status_,
                            "customer": customer, "livemode": False, "items": items,
                            "metadata": metadata or {}}},
    }


def _conv_completed(session_ref, stripe_sub_id, *, event_id=None, metadata=None):
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:16]}", "object": "event",
        "type": "checkout.session.completed", "created": 1, "livemode": False,
        "data": {"object": {"id": session_ref, "object": "checkout_session",
                            "mode": "subscription", "status": "complete",
                            "payment_status": "paid", "subscription": stripe_sub_id,
                            "customer": "cus_t", "livemode": False,
                            "metadata": metadata or {}}},
    }


def test_conversion_pending_grants_no_entitlement():
    """The whole reason checkout.session.completed may enter this state. Section 18: "No feature
    may be unlocked because a card authorization succeeded if the subscription/order activation
    did not complete." If this ever became an entitled state, that guarantee would break."""
    from app.models import SUBSCRIPTION_ENTITLED_STATES
    assert "conversion_pending" not in SUBSCRIPTION_ENTITLED_STATES


def test_the_only_route_from_trialing_to_active_runs_through_conversion_pending():
    """Pins the Section 12 graph itself, so the implementation below cannot be 'fixed' later by
    quietly adding a trialing -> active edge."""
    from app.models.subscription import SUBSCRIPTION_TRANSITIONS
    assert "active" not in SUBSCRIPTION_TRANSITIONS["trialing"]
    assert "conversion_pending" in SUBSCRIPTION_TRANSITIONS["trialing"]
    assert SUBSCRIPTION_TRANSITIONS["conversion_pending"] == ("active",)


def test_direct_trialing_to_active_is_still_refused():
    from app.models import subscription_transition_error
    assert subscription_transition_error("trialing", "active") is not None


def test_the_router_asks_the_state_machine_before_advancing():
    """The conversion step must be graph-driven, not a hardcoded `if status == 'trialing'`."""
    from app.routers import commercial
    src = code_only(commercial._handle_subscription_event)
    assert "subscription_transition_error" in src
    assert "conversion_pending" in src


def test_plan_is_resolved_from_the_price_not_from_metadata():
    """metadata.plan_slug is a label; the price is the sale. Guards against a future edit that
    reads the cheaper-looking metadata field."""
    from app.routers import commercial
    src = code_only(commercial._handle_subscription_event)
    assert "resolve_plan_for_provider_price" in src
    assert "plan_slug" not in src.split("resolve_plan_for_provider_price")[0], \
        "the plan must not be taken from provider metadata before the price is consulted"


def test_org_id_from_provider_metadata_is_never_used_to_locate_a_tenant():
    from app.routers import commercial
    src = code_only(commercial._handle_subscription_event)
    assert "subscription_by_provider_ref" in src
    assert "org_id" not in src.split("subscription_by_provider_ref")[0], \
        "the tenant must come from our own provider reference, never from event metadata"


# ── price -> plan resolution ──────────────────────────────────────────────────────────────

@needs_db
class TestPlanResolutionFromPrice:
    @pytest.fixture
    def plans(self):
        with Session(engine) as db:
            dev = Plan(name="VDeveloper", slug=f"vdev-{uuid.uuid4().hex[:6]}", is_active=True)
            biz = Plan(name="VBusiness", slug=f"vbiz-{uuid.uuid4().hex[:6]}", is_active=True)
            db.add_all([dev, biz])
            db.commit()
            db.refresh(dev)
            db.refresh(biz)
            yield db, dev, biz
            db.execute(text("DELETE FROM plans WHERE id IN (:a,:b)"), {"a": dev.id, "b": biz.id})
            db.commit()

    def test_an_approved_price_resolves_to_its_plan(self, plans, monkeypatch):
        db, dev, biz = plans
        monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                            f"{dev.slug}=price_devX1,{biz.slug}=price_bizY2")
        plan, err = admin_crud.resolve_plan_for_provider_price(db, ["price_devX1"])
        assert err is None and plan.id == dev.id
        plan, err = admin_crud.resolve_plan_for_provider_price(db, ["price_bizY2"])
        assert err is None and plan.id == biz.id

    def test_a_price_no_operator_approved_resolves_to_nothing(self, plans, monkeypatch):
        """A subscription created by hand in the Stripe dashboard must not move a tenant."""
        db, dev, biz = plans
        monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", f"{dev.slug}=price_devX1")
        plan, err = admin_crud.resolve_plan_for_provider_price(db, ["price_somethingElse"])
        assert plan is None and err.startswith("unapproved_price:")

    def test_a_subscription_billing_two_approved_plans_is_refused(self, plans, monkeypatch):
        db, dev, biz = plans
        monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                            f"{dev.slug}=price_devX1,{biz.slug}=price_bizY2")
        plan, err = admin_crud.resolve_plan_for_provider_price(db, ["price_devX1", "price_bizY2"])
        assert plan is None and err.startswith("ambiguous_price_mapping:")

    def test_no_price_at_all_resolves_to_nothing(self, plans, monkeypatch):
        db, dev, _ = plans
        monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", f"{dev.slug}=price_devX1")
        assert admin_crud.resolve_plan_for_provider_price(db, [])[1] == "no_price_on_subscription"

    def test_configuration_naming_an_absent_plan_resolves_to_nothing(self, plans, monkeypatch):
        db, _, _ = plans
        monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", "ghost-plan=price_ghost")
        plan, err = admin_crud.resolve_plan_for_provider_price(db, ["price_ghost"])
        assert plan is None and err == "no_plan_for_slug:ghost-plan"

    def test_resolution_is_the_exact_inverse_of_the_forward_authority(self, plans, monkeypatch):
        """resolve_subscription_price_id and resolve_plan_for_provider_price must agree, or a
        purchase could be priced from one mapping and booked against another."""
        db, dev, biz = plans
        monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                            f"{dev.slug}=price_devX1,{biz.slug}=price_bizY2")
        for plan_row in (dev, biz):
            price = admin_crud.resolve_subscription_price_id(plan_row)
            back, err = admin_crud.resolve_plan_for_provider_price(db, [price])
            assert err is None and back.id == plan_row.id


# ── the full conversion, driven by real webhook events ────────────────────────────────────

@needs_db
class TestConversionThroughTheWebhook:
    @pytest.fixture
    def ctx(self, monkeypatch):
        from app.routers import commercial
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            dev = Plan(name="WDeveloper", slug=f"wdev-{tag}", is_active=True)
            biz = Plan(name="WBusiness", slug=f"wbiz-{tag}", is_active=True)
            org_a = Organization(name=f"wa-{tag}")
            org_b = Organization(name=f"wb-{tag}")
            db.add_all([dev, biz, org_a, org_b])
            db.flush()
            # Both tenants start on Developer, mid-trial.
            sub_a = Subscription(org_id=org_a.id, plan_id=dev.id, status="trialing",
                                 checkout_session_ref=f"cs_a_{tag}")
            # B is already CORRELATED to a Stripe subscription but has never been advanced to
            # conversion_pending — the exact shape needed to prove that a bound subscription
            # still cannot jump straight from trialing to active.
            sub_b = Subscription(org_id=org_b.id, plan_id=dev.id, status="trialing",
                                 checkout_session_ref=f"cs_b_{tag}",
                                 stripe_subscription_id=f"sub_b_{tag}")
            db.add_all([sub_a, sub_b])
            db.commit()
            db.refresh(sub_a)
            db.refresh(sub_b)
            monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                                f"{dev.slug}=price_wdev_{tag},{biz.slug}=price_wbiz_{tag}")
            ids = SimpleNamespace(tag=tag, dev=dev, biz=biz, org_a=org_a, org_b=org_b,
                                  sub_a=sub_a.id, sub_b=sub_b.id,
                                  dev_price=f"price_wdev_{tag}", biz_price=f"price_wbiz_{tag}",
                                  handle=commercial._handle_subscription_event)
            yield db, ids
            for oid in (org_a.id, org_b.id):
                db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})
            db.execute(text("DELETE FROM plans WHERE id IN (:a,:b)"), {"a": dev.id, "b": biz.id})
            db.execute(text("DELETE FROM provider_events WHERE provider='stripe' "
                            "AND provider_event_id LIKE :p"), {"p": f"%{tag}%"})
            db.commit()

    def _fire(self, ids, db, event):
        return ids.handle(db, event, correlation_id="corr-test",
                          raw_body=json.dumps(event).encode())

    def test_checkout_completed_advances_only_to_conversion_pending(self, ctx):
        db, ids = ctx
        self._fire(ids, db, _conv_completed(f"cs_a_{ids.tag}", f"sub_a_{ids.tag}",
                                             event_id=f"evt_c1_{ids.tag}"))
        sub = db.get(Subscription, ids.sub_a)
        db.refresh(sub)
        assert sub.status == "conversion_pending"
        assert sub.stripe_subscription_id == f"sub_a_{ids.tag}"
        # Still on the plan it started on: nothing is purchased until activation.
        assert sub.plan_id == ids.dev.id

    def test_the_full_purchase_reaches_active_and_applies_the_bought_plan(self, ctx):
        """Developer trial -> buys BUSINESS -> ends active ON BUSINESS."""
        db, ids = ctx
        self._fire(ids, db, _conv_completed(f"cs_a_{ids.tag}", f"sub_a_{ids.tag}",
                                             event_id=f"evt_c2_{ids.tag}"))
        sub = db.get(Subscription, ids.sub_a)
        db.refresh(sub)
        assert sub.status == "conversion_pending" and sub.plan_id == ids.dev.id

        self._fire(ids, db, _conv_event(f"sub_a_{ids.tag}", "active", price_id=ids.biz_price,
                                       event_id=f"evt_a2_{ids.tag}"))
        db.refresh(sub)
        assert sub.status == "active"
        assert sub.plan_id == ids.biz.id, "the purchased plan must take effect at activation"

    def test_a_developer_purchase_lands_on_developer(self, ctx):
        db, ids = ctx
        self._fire(ids, db, _conv_completed(f"cs_a_{ids.tag}", f"sub_a_{ids.tag}",
                                             event_id=f"evt_c3_{ids.tag}"))
        self._fire(ids, db, _conv_event(f"sub_a_{ids.tag}", "active", price_id=ids.dev_price,
                                       event_id=f"evt_a3_{ids.tag}"))
        sub = db.get(Subscription, ids.sub_a)
        db.refresh(sub)
        assert sub.status == "active" and sub.plan_id == ids.dev.id

    def test_activation_without_the_conversion_step_is_refused(self, ctx):
        """Skipping checkout.session.completed leaves the tenant trialing — Section 12 has no
        trialing -> active edge, and the webhook must not manufacture one."""
        db, ids = ctx
        res = self._fire(ids, db, _conv_event(f"sub_b_{ids.tag}", "active",
                                             price_id=ids.biz_price,
                                             event_id=f"evt_skip_{ids.tag}"))
        sub = db.get(Subscription, ids.sub_b)
        db.refresh(sub)
        assert sub.status == "trialing"
        assert res["applied"] is False and res["error"]
        assert sub.plan_id == ids.dev.id, "a refused transition must not apply the plan"

    def test_an_unapproved_price_activates_without_moving_the_plan(self, ctx):
        """A dashboard-created subscription: the lifecycle fact is real, the plan is not ours."""
        db, ids = ctx
        self._fire(ids, db, _conv_completed(f"cs_a_{ids.tag}", f"sub_a_{ids.tag}",
                                             event_id=f"evt_c4_{ids.tag}"))
        res = self._fire(ids, db, _conv_event(f"sub_a_{ids.tag}", "active",
                                             price_id="price_not_ours",
                                             event_id=f"evt_a4_{ids.tag}"))
        sub = db.get(Subscription, ids.sub_a)
        db.refresh(sub)
        assert sub.status == "active"
        assert sub.plan_id == ids.dev.id
        assert res["plan_error"].startswith("unapproved_price:")
        logged = db.scalars(_audit_for(ids.org_a.id)).all()
        assert any(a.action == "subscription.plan_unresolved" for a in logged)

    def test_a_purchase_cannot_move_another_organizations_subscription(self, ctx):
        """Tenant isolation. The event names org B in metadata but carries org A's provider
        reference; the row is located by OUR reference, so B must be untouched."""
        db, ids = ctx
        self._fire(ids, db, _conv_completed(f"cs_a_{ids.tag}", f"sub_a_{ids.tag}",
                                             event_id=f"evt_c5_{ids.tag}"))
        self._fire(ids, db, _conv_event(
            f"sub_a_{ids.tag}", "active", price_id=ids.biz_price,
            event_id=f"evt_x5_{ids.tag}",
            metadata={"org_id": str(ids.org_b.id), "plan_slug": ids.biz.slug}))
        a = db.get(Subscription, ids.sub_a)
        db.refresh(a)
        b = db.get(Subscription, ids.sub_b)
        db.refresh(b)
        assert a.status == "active" and a.plan_id == ids.biz.id
        assert b.status == "trialing", "another tenant must not be moved"
        assert b.plan_id == ids.dev.id
        # B's own provider reference is untouched — A's event never reached across.
        assert b.stripe_subscription_id == f"sub_b_{ids.tag}"

    def test_replaying_the_activation_does_not_re_apply_anything(self, ctx):
        db, ids = ctx
        self._fire(ids, db, _conv_completed(f"cs_a_{ids.tag}", f"sub_a_{ids.tag}",
                                             event_id=f"evt_c6_{ids.tag}"))
        ev = _conv_event(f"sub_a_{ids.tag}", "active", price_id=ids.biz_price,
                        event_id=f"evt_a6_{ids.tag}")
        self._fire(ids, db, ev)
        again = self._fire(ids, db, ev)
        sub = db.get(Subscription, ids.sub_a)
        db.refresh(sub)
        assert again.get("duplicate") is True
        assert sub.status == "active" and sub.plan_id == ids.biz.id

    def test_an_abandoned_conversion_leaves_the_tenant_on_its_original_plan(self, ctx):
        """conversion_pending reached, activation never arrives: no entitlement, no plan move."""
        from app.models import SUBSCRIPTION_ENTITLED_STATES
        db, ids = ctx
        self._fire(ids, db, _conv_completed(f"cs_a_{ids.tag}", f"sub_a_{ids.tag}",
                                             event_id=f"evt_c7_{ids.tag}"))
        sub = db.get(Subscription, ids.sub_a)
        db.refresh(sub)
        assert sub.status == "conversion_pending"
        assert sub.status not in SUBSCRIPTION_ENTITLED_STATES
        assert sub.plan_id == ids.dev.id


def test_subscription_checkout_names_the_stripe_provider_explicitly():
    """get_provider() with no argument returns the deterministic SIMULATOR. This route once
    called it bare, so it was handed a MockPaymentProvider — which has no
    create_subscription_checkout_session at all, and the endpoint 500'd before ever reaching
    Stripe. Naming the provider is also what makes it fail closed: get_provider("stripe")
    raises ProviderNotConfigured without a secret key, where the bare call silently simulates."""
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    assert "get_provider(stripe)" in src.replace("'", "").replace('"', ""), \
        "the subscription checkout must request the Stripe provider by name"


def test_the_simulator_offers_no_subscription_checkout():
    """Guards the other half: if a mock subscription checkout were ever added, a
    misconfigured deployment could show a customer a 'successful' purchase that charged
    nothing. Ledger 1 has exactly one provider, and the route 503s when it is absent."""
    from app.services.payments import get_provider
    assert not hasattr(get_provider("mock"), "create_subscription_checkout_session")


# ══════════════════════════════════════════════════════════════════════════════════════
# Billing interval — Approved Price Book & Stripe Billing Wireframe v1.0
#
# The book publishes two cadences per self-service plan (Developer $49/mo or $490/yr,
# Business $249/mo or $2,490/yr). Each cadence is a DISTINCT Stripe Price, so the cadence
# selects between approved prices and can never introduce one. Nothing here asserts an
# amount: the amounts live on the Stripe Prices, not in this codebase.
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_bare_configuration_form_still_means_monthly(monkeypatch):
    """Every existing deployment's value is `slug=price`. Reading that as anything but monthly
    would silently re-bill live tenants on a different cadence."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", f"developer={APPROVED_PRICE}")
    assert settings.subscription_price_map() == {("developer", "monthly"): APPROVED_PRICE}


def test_an_explicit_interval_is_parsed(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                        f"developer:monthly={APPROVED_PRICE},developer:annual=price_annualDEV1")
    assert settings.subscription_price_map() == {
        ("developer", "monthly"): APPROVED_PRICE,
        ("developer", "annual"): "price_annualDEV1",
    }


@pytest.mark.parametrize("bad", ["developer:weekly=price_x", "developer:=price_x",
                                 "developer:quarterly=price_x"])
def test_an_unrecognised_interval_is_dropped_not_defaulted(monkeypatch, bad):
    """Dropping is the safe direction. Falling back to the other cadence would charge a
    different amount than the one selected."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", bad)
    assert settings.subscription_price_map() in ({}, {("developer", "monthly"): "price_x"})
    assert ("developer", "weekly") not in settings.subscription_price_map()


def test_purchasable_intervals_reports_only_configured_cadences(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                        f"developer={APPROVED_PRICE},business:annual=price_annualBIZ1")
    assert settings.purchasable_intervals("developer") == ["monthly"]
    assert settings.purchasable_intervals("business") == ["annual"]
    assert settings.purchasable_intervals("enterprise") == []


def test_an_unconfigured_cadence_is_refused_never_substituted(monkeypatch):
    """THE money-safety property: an annual request against a monthly-only plan must refuse,
    not quietly bill the monthly price."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", f"developer={APPROVED_PRICE}")
    assert admin_crud.resolve_subscription_price_id(_plan(), "monthly") == APPROVED_PRICE
    with pytest.raises(ValueError, match="annual"):
        admin_crud.resolve_subscription_price_id(_plan(), "annual")


def test_the_refusal_names_the_configuration_an_operator_must_add(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", "")
    with pytest.raises(ValueError, match="developer:annual=price_"):
        admin_crud.resolve_subscription_price_id(_plan(), "annual")


def test_an_unknown_interval_is_refused_by_the_resolver(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", f"developer={APPROVED_PRICE}")
    with pytest.raises(ValueError, match="Unknown billing interval"):
        admin_crud.resolve_subscription_price_id(_plan(), "weekly")


def test_the_request_body_accepts_only_a_closed_interval_vocabulary():
    """A tampered cadence is a 422 at the edge, so the price resolver never has to defend
    against it — and there is still no price/amount field to tamper with."""
    from app.schemas.organization import SubscriptionCheckoutCreate
    fields = set(SubscriptionCheckoutCreate.model_fields)
    assert fields == {"plan_slug", "billing_interval"}
    for forbidden in ("amount", "price", "price_id", "currency", "unit_amount"):
        assert forbidden not in fields
    assert SubscriptionCheckoutCreate(plan_slug="developer").billing_interval == "monthly"
    for good in ("monthly", "annual"):
        assert SubscriptionCheckoutCreate(
            plan_slug="developer", billing_interval=good).billing_interval == good
    for bad in ("weekly", "yearly", "MONTHLY ", "", "annual;drop"):
        with pytest.raises(Exception):
            SubscriptionCheckoutCreate(plan_slug="developer", billing_interval=bad)


def test_the_route_passes_the_interval_to_the_price_authority():
    """Structural: the cadence must reach the resolver, or annual would silently bill monthly."""
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    assert "resolve_subscription_price_id(plan, data.billing_interval)" in src


def test_both_cadences_of_one_plan_resolve_back_to_that_plan(monkeypatch):
    """Reverse resolution collapses the cadence: monthly and annual are two prices for the SAME
    plan, so either must map to it. Otherwise an annual customer's webhook would fail to
    resolve a plan and their subscription would activate without one."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                        f"developer:monthly={APPROVED_PRICE},developer:annual=price_annualDEV1")
    m = settings.subscription_price_map()
    assert set(m.values()) == {APPROVED_PRICE, "price_annualDEV1"}
    slugs = {slug for (slug, _i) in m}
    assert slugs == {"developer"}


# ── the approved 14-day trial ─────────────────────────────────────────────────────────────

def test_the_trial_length_is_the_approved_fourteen_days():
    from app.config import TRIAL_DAYS
    assert TRIAL_DAYS == 14


def test_a_trial_grants_entitlement_without_a_card():
    """Entitlements during trial are ZoikoStream's, not Stripe's: TRIALING is entitled and the
    limits come off the Plan row. Nothing in the trial path touches Stripe."""
    from app.models import SUBSCRIPTION_ENTITLED_STATES
    assert "trialing" in SUBSCRIPTION_ENTITLED_STATES
    # Follows the refactor: the trial rule used to be inline in create_organization and now
    # lives in the ONE shared provisioner that every organization-creating path calls, so this
    # asserts against that. Stronger than before — the guarantee is now checked where it is
    # actually implemented rather than at one of its call sites.
    src = code_only(admin_crud.provision_initial_subscription)
    assert "TRIAL_DAYS" in src, "the trial must carry the approved window"
    assert "trialing" in src
    # Real Stripe USAGE surfaces, not the bare word — the audit meta this function writes
    # deliberately records that no card and no charge are involved.
    for stripe_token in ("StripeClient", "stripe.", "payments_stripe", "get_provider",
                         "checkout.sessions"):
        assert stripe_token not in src, \
            "starting a trial must not touch Stripe — no card may be collected"
    # And the path that creates an organization must go through it.
    assert "provision_initial_subscription" in code_only(admin_crud.create_organization)


def test_a_trial_cannot_convert_itself_into_a_paid_subscription():
    """The book: the trial "must NOT automatically convert". Section 12 enforces it
    structurally — TRIALING has no edge to ACTIVE, so no timer or sweep can charge anyone."""
    from app.models.subscription import SUBSCRIPTION_TRANSITIONS
    assert "active" not in SUBSCRIPTION_TRANSITIONS["trialing"]
    assert set(SUBSCRIPTION_TRANSITIONS["trialing"]) == {"conversion_pending", "trial_expired"}
    # And an expired trial is terminal-for-entitlement, not a silent upgrade.
    assert SUBSCRIPTION_TRANSITIONS["trial_expired"] == ()


def test_enterprise_is_never_self_service_under_any_cadence(monkeypatch):
    """Contract pricing: Enterprise must not enter the self-service checkout on either cadence."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                        f"developer={APPROVED_PRICE},business:annual=price_annualBIZ1")
    assert settings.purchasable_intervals("enterprise") == []
    for interval in ("monthly", "annual"):
        with pytest.raises(ValueError, match="enterprise"):
            admin_crud.resolve_subscription_price_id(_plan("enterprise"), interval)


# ══════════════════════════════════════════════════════════════════════════════════════
# Paid plan changes — the three defects that sat behind the missing PLAN_CHANGE_SCHEDULED
# path. None of these tests asserts an effective date or a proration rule: those are
# undefined by the canonical documents, so the lifecycle itself is deliberately NOT built.
# What is asserted is that the gaps fail safely instead of losing money.
# ══════════════════════════════════════════════════════════════════════════════════════

def test_an_active_subscription_reconciles_to_the_price_stripe_actually_bills():
    """THE dropped-change bug. An already-ACTIVE subscription whose Stripe price moved kept its
    old plan_id forever — applied=False, error=None, no audit row — while Stripe billed the new
    price. The tenant paid for one plan and was entitled to another, and nothing said so."""
    sub, db = _sub("active"), _Stub()
    old_plan_id = sub.plan_id
    new_plan = SimpleNamespace(id=uuid.uuid4(), slug="business")
    applied, error = admin_crud.apply_subscription_provider_event(
        db, sub, new_state="active", plan=new_plan, reason="stripe:customer.subscription.updated")
    assert error is None
    assert applied is True, "a real plan movement must not report itself as a no-op"
    assert sub.plan_id == new_plan.id, "the plan on record must follow the money"
    assert sub.plan_id != old_plan_id
    actions = [o.action for o in db.added if isinstance(o, AuditLog)]
    assert "subscription.plan_reconciled" in actions
    assert "subscription.transition" not in actions, \
        "no Section 12 edge was taken, so this must not be recorded as a transition"


def test_reconciliation_is_not_a_state_transition():
    """The state must stay exactly where it was — reconciling a price is not a lifecycle move."""
    sub, db = _sub("active"), _Stub()
    admin_crud.apply_subscription_provider_event(
        db, sub, new_state="active", plan=SimpleNamespace(id=uuid.uuid4(), slug="business"))
    assert sub.status == "active"


def test_a_genuine_replay_is_still_a_no_op():
    """Regression guard on the fix: same state AND same plan must remain a silent no-op, or
    every Stripe redelivery would write an audit row."""
    sub, db = _sub("active"), _Stub()
    same = SimpleNamespace(id=sub.plan_id, slug="developer")
    applied, error = admin_crud.apply_subscription_provider_event(
        db, sub, new_state="active", plan=same)
    assert applied is False and error is None
    assert not [o for o in db.added if isinstance(o, AuditLog)]


def test_reconciliation_only_happens_in_the_active_state():
    """A price seen while the subscription is NOT active must not move the plan: outside active
    there is no confirmed billing relationship to reconcile against."""
    for state in ("trialing", "past_due"):
        sub, db = _sub(state), _Stub()
        before = sub.plan_id
        admin_crud.apply_subscription_provider_event(
            db, sub, new_state=state, plan=SimpleNamespace(id=uuid.uuid4(), slug="business"))
        assert sub.plan_id == before, f"must not reconcile the plan while {state}"


def test_an_unresolvable_price_never_moves_the_plan():
    """plan=None is what resolve_plan_for_provider_price returns for a price no operator
    approved. It must leave the record alone rather than blanking it."""
    sub, db = _sub("active"), _Stub()
    before = sub.plan_id
    applied, error = admin_crud.apply_subscription_provider_event(
        db, sub, new_state="active", plan=None)
    assert applied is False and error is None and sub.plan_id == before


# ── the double-billing hole ───────────────────────────────────────────────────────────────

def test_checkout_refuses_an_organization_that_already_has_a_live_subscription():
    """This endpoint creates a NEW Stripe subscription on every success. An already-paying
    tenant who clicked another plan card got TWO live subscriptions on one customer and was
    billed for both, while the plan never changed. Refused at the backend, not left to an
    invoice."""
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    assert "stripe_subscription_id" in src, \
        "the route must consider whether a live Stripe subscription already exists"
    assert "already has an active subscription" in src


def test_the_refusal_still_admits_a_terminated_subscription():
    """A canceled/closed/expired tenant must be able to buy again — the guard is about a LIVE
    subscription, not about having ever had one."""
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    for terminal in ("canceled", "closed", "trial_expired"):
        assert terminal in src, f"{terminal} must remain purchasable"


def test_the_backend_agrees_with_what_the_billing_page_tells_the_customer():
    """The page says changing a paid plan is not self-service; the backend used to contradict
    it by silently opening a second subscription. Both must now say the same thing."""
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    assert "contact sales" in src.lower()


# ── recorded billing cadence ──────────────────────────────────────────────────────────────

def test_the_purchased_cadence_is_recorded_on_the_subscription():
    """It used to be resolved to a Price ID and thrown away, so nothing could answer "what is
    this tenant on?" — which Section 16 requires the console to show."""
    sub, db = _sub("trialing"), _Stub()
    admin_crud.record_subscription_checkout_started(
        db, sub, checkout_session_ref="cs_iv_1", billing_interval="annual")
    assert sub.billing_interval == "annual"
    meta = [o.meta for o in db.added if isinstance(o, AuditLog)][-1]
    assert meta["billing_interval"] == "annual"


def test_recording_the_cadence_grants_nothing_and_changes_no_state():
    sub, db = _sub("trialing"), _Stub()
    before_status, before_plan = sub.status, sub.plan_id
    admin_crud.record_subscription_checkout_started(
        db, sub, checkout_session_ref="cs_iv_2", billing_interval="monthly")
    assert sub.status == before_status and sub.plan_id == before_plan
    assert sub.stripe_subscription_id is None


def test_an_unrecorded_cadence_stays_none_rather_than_defaulting_to_monthly():
    """Rows predating the column genuinely have no cadence. Defaulting them would assert
    something about live tenants that nobody verified."""
    sub, db = _sub("active"), _Stub()
    admin_crud.record_subscription_checkout_started(db, sub, checkout_session_ref="cs_iv_3")
    assert sub.billing_interval is None


def test_the_route_records_the_cadence_it_resolved():
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    assert "billing_interval=data.billing_interval" in src


def test_entitlements_expose_the_current_cadence():
    from app.services import org as org_svc
    src = code_only(org_svc.entitlements)
    assert "billing_interval" in src


# ── the lifecycle is deliberately absent, and must stay absent until it is specified ──────

def test_the_only_proration_behaviour_sent_to_stripe_is_none():
    """This guard previously asserted that NO proration policy existed anywhere, because none
    was specified. The approved commercial decision has since supplied one — "Use NO mid-cycle
    proration... Do not use Stripe proration behavior that creates additional mid-cycle
    charges" — so the guard now pins THAT rule instead of the absence of a rule.

    It matters because Stripe's own default is `create_prorations`: omitting the parameter
    would raise a mid-cycle invoice item and silently violate the decision. So the assertion is
    not merely "none appears somewhere" but "none is the only value this codebase ever sends".
    """
    import re
    from app.services import payments_stripe
    # code_only strips docstrings, so the prose above may name the behaviours it forbids
    # without satisfying or tripping the scan — the same convention the rest of this suite uses.
    src = code_only(payments_stripe)
    values = re.findall(r"""["']proration_behavior["']\s*:\s*["'](\w+)["']""", src)
    assert values, "the subscription price change must set proration_behavior explicitly"
    assert set(values) == {"none"}, f"non-approved proration behaviour in use: {set(values)}"
    assert "create_prorations" not in src, "prorations must never be created"
    assert "always_invoice" not in src, "a mid-cycle invoice must never be forced"


def test_the_section_12_graph_still_forbids_an_immediate_paid_plan_change():
    """Why the lifecycle cannot be short-cut: there is no active->active edge, so a plan change
    on a paid subscription MUST pass through PLAN_CHANGE_SCHEDULED. That state is what has no
    effective-date rule, which is precisely why the feature is blocked rather than faked."""
    from app.models.subscription import SUBSCRIPTION_TRANSITIONS
    assert "active" not in SUBSCRIPTION_TRANSITIONS["active"]
    assert SUBSCRIPTION_TRANSITIONS["active"] == ("plan_change_scheduled", "past_due", "canceled")
    assert SUBSCRIPTION_TRANSITIONS["plan_change_scheduled"] == ("active",)


def test_a_pending_plan_change_keeps_the_tenant_entitled():
    """The inverse of what this test asserted before the rules arrived, and deliberately so.

    It used to document a trap: PLAN_CHANGE_SCHEDULED sat OUTSIDE the entitled set, so entering
    it would have cut a paying tenant's access mid-period — an accident of the tuple, not a
    documented rule, which is why the state was left unreachable.

    The approved decision settles it: "The customer retains the CURRENT plan's entitlements...
    Do not remove access merely because a change has been requested." So the state is entitled,
    and this is now the regression guard for that.
    """
    from app.models import SUBSCRIPTION_ENTITLED_STATES
    assert "plan_change_scheduled" in SUBSCRIPTION_ENTITLED_STATES
    # It must NOT be counted as revenue-recognised on its own footing — that tuple is narrower
    # and changing it would move a finance figure nobody authorised.
    from app.models.subscription import SUBSCRIPTION_REVENUE_STATES
    assert "plan_change_scheduled" not in SUBSCRIPTION_REVENUE_STATES


# ══════════════════════════════════════════════════════════════════════════════════════
# Price-ID shape validation — a paste error must never reach a payer
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad", [
    "price_\u2026",              # a documentation elision pasted in verbatim
    "price_1UARwq\u2026",        # a truncated real id
    "price_",                    # prefix only
    "prod_1UARwqELHRR3D2Nn",     # a Product id
    "price_abc def ghijkl",      # embedded whitespace
    "price_1UARwq-ELHRR3D2Nn",   # punctuation Stripe does not use
])
def test_a_placeholder_or_truncated_price_id_is_never_accepted(monkeypatch, bad):
    """These all satisfied the old startswith("price_") check, so they were accepted as approved
    configuration and made the plan render as purchasable — then failed at Stripe with "No such
    price" only AFTER the customer clicked Upgrade. Rejected here, the plan simply shows as
    unpriced, which is the fail-closed direction."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES", f"developer:monthly={bad}")
    assert settings.subscription_price_map() == {}
    assert settings.purchasable_intervals("developer") == []


def test_a_real_price_id_shape_is_still_accepted(monkeypatch):
    """Guard the guard: the validator must not be so strict that real Stripe ids are refused."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                        "developer:monthly=price_1UARwqELHRR3D2NnYJcLXoeH")
    assert settings.subscription_price_map() == {
        ("developer", "monthly"): "price_1UARwqELHRR3D2NnYJcLXoeH"}


def test_one_bad_entry_does_not_poison_the_others(monkeypatch):
    """A half-broken config must still sell what IS validly priced, rather than failing wholesale
    — and must not sell what is not."""
    monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                        "developer:monthly=price_1UARwqELHRR3D2NnYJcLXoeH,"
                        "business:monthly=price_\u2026")
    assert settings.purchasable_intervals("developer") == ["monthly"]
    assert settings.purchasable_intervals("business") == []


# ══════════════════════════════════════════════════════════════════════════════════════
# SCHEDULED PLAN CHANGE — the full approved lifecycle, against real Postgres.
#
# Approved commercial decisions under test:
#   1. NO mid-cycle proration.
#   2. Effective at the subscription's own current_period_end.
#   3. Current entitlements retained until that date; the scheduled plan never early.
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestScheduledPlanChange:
    @pytest.fixture
    def ctx(self, monkeypatch):
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            dev = Plan(name="PCDeveloper", slug=f"pcdev-{tag}", is_active=True,
                       max_users=5, max_storage_gb=50, max_streaming_hours=20)
            biz = Plan(name="PCBusiness", slug=f"pcbiz-{tag}", is_active=True,
                       max_users=30, max_storage_gb=500, max_streaming_hours=200)
            org = Organization(name=f"pc-{tag}")
            other = Organization(name=f"pc-other-{tag}")
            db.add_all([dev, biz, org, other])
            db.flush()
            member = User(org_id=org.id, full_name="PC Member",
                          email=f"pc-{tag}@t.test", username=f"pc{tag}",
                          password_hash="x", role="org_admin")
            db.add(member)
            db.flush()
            period_end = datetime.now(timezone.utc) + timedelta(days=20)
            sub = Subscription(org_id=org.id, plan_id=dev.id, status="active", seats=1,
                               billing_interval="monthly", current_period_end=period_end,
                               stripe_subscription_id=f"sub_pc_{tag}")
            other_sub = Subscription(org_id=other.id, plan_id=dev.id, status="active", seats=1,
                                     billing_interval="monthly",
                                     current_period_end=period_end)
            db.add_all([sub, other_sub])
            db.commit()
            db.refresh(sub); db.refresh(other_sub)
            monkeypatch.setattr(
                settings, "STRIPE_SUBSCRIPTION_PRICES",
                f"{dev.slug}:monthly=price_pcdevmonthly1,{dev.slug}:annual=price_pcdevannual1,"
                f"{biz.slug}:monthly=price_pcbizmonthly1,{biz.slug}:annual=price_pcbizannual1")
            ids = SimpleNamespace(dev=dev, biz=biz, org=org, other=other, sub_id=sub.id,
                                  other_sub_id=other_sub.id, period_end=period_end)
            yield db, ids
            for oid in (org.id, other.id):
                db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})
            db.execute(text("DELETE FROM plans WHERE id IN (:a,:b)"),
                       {"a": dev.id, "b": biz.id})
            db.commit()

    def _actions(self, db, org_id):
        from sqlalchemy import select as sel
        return [a.action for a in db.scalars(
            sel(AuditLog).where(AuditLog.org_id == org_id)).all()]

    # ── requesting ────────────────────────────────────────────────────────────────────────

    def test_an_upgrade_is_scheduled_not_applied(self, ctx):
        """Developer -> Business. The Section 12 state moves; the plan does NOT."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        db.commit(); db.refresh(sub)
        assert sub.status == "plan_change_scheduled"
        assert sub.plan_id == ids.dev.id, "the CURRENT plan must not move at request time"
        assert sub.pending_plan_id == ids.biz.id
        assert sub.pending_billing_interval == "monthly"
        assert "subscription.plan_change_scheduled" in self._actions(db, ids.org.id)

    def test_a_downgrade_is_scheduled_the_same_way(self, ctx):
        """Business -> Developer. No credit, no refund, no immediate loss of the higher plan."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.plan_id = ids.biz.id
        db.commit()
        admin_crud.request_plan_change(db, sub, plan=ids.dev, billing_interval="monthly")
        db.commit(); db.refresh(sub)
        assert sub.status == "plan_change_scheduled"
        assert sub.plan_id == ids.biz.id, "a downgrade must not strip the paid plan early"
        assert sub.pending_plan_id == ids.dev.id

    def test_monthly_to_annual_is_a_scheduled_change(self, ctx):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.dev, billing_interval="annual")
        db.commit(); db.refresh(sub)
        assert sub.status == "plan_change_scheduled"
        assert sub.pending_plan_id == ids.dev.id
        assert sub.pending_billing_interval == "annual"
        assert sub.billing_interval == "monthly", "the current cadence bills until the boundary"

    def test_annual_to_monthly_is_a_scheduled_change(self, ctx):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.billing_interval = "annual"
        db.commit()
        admin_crud.request_plan_change(db, sub, plan=ids.dev, billing_interval="monthly")
        db.commit(); db.refresh(sub)
        assert sub.pending_billing_interval == "monthly"
        assert sub.billing_interval == "annual"

    def test_the_effective_date_is_the_current_period_end(self, ctx):
        """The approved effective date, taken from the subscription's own boundary — never
        computed, never "now + a month"."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        db.commit(); db.refresh(sub)
        assert sub.plan_change_effective_at == sub.current_period_end
        assert sub.plan_change_effective_at == ids.period_end

    def test_a_subscription_without_a_period_end_cannot_be_scheduled(self, ctx):
        """With no boundary there is no honest date to promise, so it refuses rather than
        inventing one."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.current_period_end = None
        db.commit()
        with pytest.raises(admin_crud.PlanChangeError) as e:
            admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        assert e.value.code == "no_period_end"
        db.rollback()

    # ── entitlements ──────────────────────────────────────────────────────────────────────

    def test_entitlements_stay_on_the_current_plan_while_scheduled(self, ctx):
        """Decision 3, end to end through the real entitlements service: a tenant upgrading
        Developer -> Business keeps DEVELOPER limits until the effective date."""
        from app.services import org as org_svc
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        db.commit()
        ent = org_svc.entitlements(db, db.get(Organization, ids.org.id))
        assert ent["plan_slug"] == ids.dev.slug, "the scheduled plan must not be entitled early"
        limits = {i["label"]: i["limit"] for i in ent["items"]}
        assert limits["Storage"] == 50, "Business storage must not apply before the date"
        assert limits["Members"] == 5

    def test_access_is_not_removed_merely_because_a_change_is_pending(self, ctx):
        db, ids = ctx
        from app.models import SUBSCRIPTION_ENTITLED_STATES
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        db.commit(); db.refresh(sub)
        assert sub.status in SUBSCRIPTION_ENTITLED_STATES

    # ── applying at the effective date ────────────────────────────────────────────────────

    def test_nothing_is_due_before_the_effective_date(self, ctx):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        db.commit()
        due = [s.id for s in admin_crud.due_plan_changes(db)]
        assert sub.id not in due

    def test_the_change_applies_once_the_date_arrives(self, ctx):
        """The full transition: plan_change_scheduled -> active on the NEW plan and cadence."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="annual")
        sub.plan_change_effective_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        assert sub.id in [s.id for s in admin_crud.due_plan_changes(db)]
        applied, error = admin_crud.apply_plan_change(db, sub)
        db.commit(); db.refresh(sub)
        assert applied is True and error is None
        assert sub.status == "active"
        assert sub.plan_id == ids.biz.id
        assert sub.billing_interval == "annual"
        assert sub.pending_plan_id is None and sub.plan_change_effective_at is None
        assert "subscription.plan_change_applied" in self._actions(db, ids.org.id)

    def test_new_entitlements_begin_only_after_the_change_is_applied(self, ctx):
        from app.services import org as org_svc
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        sub.plan_change_effective_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        admin_crud.apply_plan_change(db, sub)
        db.commit()
        ent = org_svc.entitlements(db, db.get(Organization, ids.org.id))
        assert ent["plan_slug"] == ids.biz.slug
        assert {i["label"]: i["limit"] for i in ent["items"]}["Storage"] == 500

    def test_applying_is_idempotent(self, ctx):
        """Repeated effective-date processing must not transition twice."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        sub.plan_change_effective_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        assert admin_crud.apply_plan_change(db, sub)[0] is True
        db.commit()
        again, error = admin_crud.apply_plan_change(db, sub)
        db.commit()
        assert again is False and error is None
        assert len([a for a in self._actions(db, ids.org.id)
                    if a == "subscription.plan_change_applied"]) == 1

    def test_a_price_withdrawn_before_the_effective_date_fails_closed(self, ctx, monkeypatch):
        """Re-resolved at apply time on purpose: a cadence an operator has since withdrawn must
        not be charged from a stale copy."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="annual")
        sub.plan_change_effective_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                            f"{ids.dev.slug}:monthly=price_pcdevmonthly1")
        applied, error = admin_crud.apply_plan_change(db, sub)
        db.commit(); db.refresh(sub)
        assert applied is False and error
        assert sub.plan_id == ids.dev.id, "no plan move without an approved price"
        assert "subscription.plan_change_rejected" in self._actions(db, ids.org.id)

    # ── cancelling ────────────────────────────────────────────────────────────────────────

    def test_a_scheduled_change_can_be_cancelled(self, ctx):
        """Section 12's `-> ACTIVE(old version)` outcome. Nothing to reverse, because nothing
        was charged or moved."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        db.commit()
        admin_crud.cancel_plan_change(db, sub)
        db.commit(); db.refresh(sub)
        assert sub.status == "active"
        assert sub.plan_id == ids.dev.id and sub.billing_interval == "monthly"
        assert sub.pending_plan_id is None and sub.plan_change_effective_at is None
        assert "subscription.plan_change_cancelled" in self._actions(db, ids.org.id)

    def test_cancelling_without_a_scheduled_change_is_refused(self, ctx):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        with pytest.raises(admin_crud.PlanChangeError) as e:
            admin_crud.cancel_plan_change(db, sub)
        assert e.value.code == "no_change_scheduled"

    # ── duplicate / invalid requests ──────────────────────────────────────────────────────

    def test_re_requesting_the_same_change_is_an_idempotent_no_op(self, ctx):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        db.commit()
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        db.commit(); db.refresh(sub)
        assert len([a for a in self._actions(db, ids.org.id)
                    if a == "subscription.plan_change_scheduled"]) == 1

    def test_a_conflicting_second_change_is_refused_not_overwritten(self, ctx):
        """Silently replacing a scheduled commitment would discard it with no trace."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        db.commit()
        with pytest.raises(admin_crud.PlanChangeError) as e:
            admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="annual")
        assert e.value.code == "change_already_scheduled"
        db.rollback(); db.refresh(sub)
        assert sub.pending_billing_interval == "monthly", "the original must survive"

    def test_changing_to_the_same_plan_and_cadence_is_refused(self, ctx):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        with pytest.raises(admin_crud.PlanChangeError) as e:
            admin_crud.request_plan_change(db, sub, plan=ids.dev, billing_interval="monthly")
        assert e.value.code == "no_change"

    def test_an_unpriced_cadence_is_refused_at_request_time(self, ctx, monkeypatch):
        db, ids = ctx
        monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                            f"{ids.dev.slug}:monthly=price_pcdevmonthly1")
        sub = db.get(Subscription, ids.sub_id)
        with pytest.raises(admin_crud.PlanChangeError) as e:
            admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        assert e.value.code == "unpriced"

    @pytest.mark.parametrize("bad", ["weekly", "yearly", "", "quarterly", "mo nthly"])
    def test_an_invalid_interval_is_refused(self, ctx, bad):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        with pytest.raises(admin_crud.PlanChangeError) as e:
            admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval=bad)
        assert e.value.code == "invalid_interval"

    @pytest.mark.parametrize("state", ["trialing", "past_due", "canceled", "closed",
                                        "conversion_pending"])
    def test_only_an_active_subscription_can_schedule_a_change(self, ctx, state):
        """A trial converts through checkout, not here; a terminated or dunning subscription
        has no period boundary to change at."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.status = state
        db.commit()
        with pytest.raises(admin_crud.PlanChangeError) as e:
            admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        assert e.value.code == "not_active"
        db.rollback()

    def test_one_tenants_change_never_touches_another(self, ctx):
        """Cross-organization isolation at the data layer."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.request_plan_change(db, sub, plan=ids.biz, billing_interval="monthly")
        sub.plan_change_effective_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        admin_crud.apply_plan_change(db, sub)
        db.commit()
        other = db.get(Subscription, ids.other_sub_id)
        db.refresh(other)
        assert other.status == "active"
        assert other.plan_id == ids.dev.id
        assert other.pending_plan_id is None

    # ── admin governance ──────────────────────────────────────────────────────────────────

    def test_admin_patch_cannot_change_a_paid_plan_immediately(self, ctx):
        """The governance fix: support gets no power the approved rules deny a customer."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        admin_crud.update_subscription(
            db, sub, SimpleNamespace(status=None, seats=None, plan_slug=ids.biz.slug,
                                     billing_interval=None, current_period_end=None))
        db.refresh(sub)
        assert sub.plan_id == ids.dev.id, "an admin plan change must not apply immediately"
        assert sub.status == "plan_change_scheduled"
        assert sub.pending_plan_id == ids.biz.id
        assert sub.plan_change_effective_at == ids.period_end
        assert "subscription.plan_change_scheduled" in self._actions(db, ids.org.id)

    def test_admin_patch_on_a_non_paid_subscription_is_immediate_but_audited(self, ctx):
        """A trial has no billing boundary to schedule against, so immediate is correct — but
        it was previously unaudited, which is the part that was wrong."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.status = "trialing"
        db.commit()
        admin_crud.update_subscription(
            db, sub, SimpleNamespace(status=None, seats=None, plan_slug=ids.biz.slug,
                                     billing_interval=None, current_period_end=None))
        db.refresh(sub)
        assert sub.plan_id == ids.biz.id
        assert "subscription.plan_set_by_admin" in self._actions(db, ids.org.id)

    def test_admin_patch_rejects_an_unknown_plan(self, ctx):
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        with pytest.raises(ValueError, match="Unknown plan"):
            admin_crud.update_subscription(
                db, sub, SimpleNamespace(status=None, seats=None, plan_slug="no-such-plan",
                                         billing_interval=None, current_period_end=None))
        db.rollback()


# ── Stripe reconciliation + webhook authority ─────────────────────────────────────────────

def test_the_provider_swap_sends_no_proration():
    from app.services import payments_stripe
    src = code_only(payments_stripe.StripePaymentProvider.change_subscription_price)
    assert "proration_behavior" in src and "none" in src
    assert "create_prorations" not in src and "always_invoice" not in src


def test_the_provider_swap_replaces_the_item_rather_than_adding_one():
    """Appending an item would bill the customer for BOTH plans."""
    from app.services import payments_stripe
    src = code_only(payments_stripe.StripePaymentProvider.change_subscription_price)
    assert "items" in src and "id" in src, "the existing item id must be reused"


def test_the_provider_swap_is_idempotency_keyed():
    from app.services import payments_stripe
    src = code_only(payments_stripe.StripePaymentProvider.change_subscription_price)
    assert "idempotency_key" in src


def test_the_effective_date_job_applies_locally_before_calling_stripe():
    """Order matters: a Stripe outage must not leave a customer billed for a plan our record
    denies them."""
    from app.services import maintenance
    src = code_only(maintenance.apply_due_plan_changes)
    assert src.index("apply_plan_change") < src.index("change_subscription_price")


def test_a_stripe_failure_is_audited_not_swallowed():
    from app.services import maintenance
    src = code_only(maintenance.apply_due_plan_changes)
    assert "plan_change_provider_failed" in src
    assert "plan_change_provider_synced" in src


def test_the_effective_date_job_is_registered():
    from app.services import maintenance
    assert maintenance.apply_due_plan_changes in maintenance.JOBS


def test_the_webhook_syncs_the_period_end_that_the_effective_date_depends_on():
    from app.routers import commercial
    src = code_only(commercial._handle_subscription_event)
    assert "current_period_end" in src


def test_the_period_end_is_read_from_the_subscription_item():
    """Verified against the live sandbox: on this API version current_period_end exists ONLY on
    items.data[], so a top-level-only read silently yields None and leaves the effective date
    with no anchor."""
    from app.services import payments_stripe_events as ev
    facts = ev.subscription_facts({
        "type": "customer.subscription.updated", "id": "evt_pe_1",
        "data": {"object": {"id": "sub_pe_1", "object": "subscription", "status": "active",
                            "customer": "cus_pe_1", "metadata": {},
                            "items": {"object": "list", "data": [
                                {"id": "si_1", "current_period_end": 1790844720,
                                 "price": {"id": "price_pcdevmonthly1"}}]}}},
    })
    assert facts["current_period_end"] is not None
    assert facts["current_period_end"].year == 2026


def test_the_plan_change_request_body_carries_no_price():
    """Same guarantee as checkout: canonical identifiers only."""
    from app.routers import organization
    src = code_only(organization.schedule_plan_change)
    for forbidden in ("price_id", "unit_amount", "amount", "proration"):
        assert forbidden not in src
    assert "data.plan_slug" in src and "data.billing_interval" in src


def test_the_plan_change_route_derives_the_org_from_the_session():
    """Cross-tenant safety: the organization is never read from the body."""
    from app.routers import organization
    src = code_only(organization.schedule_plan_change)
    assert "get_my_org_admin" in code_only(organization)
    assert "data.org_id" not in src and "org_id=" not in src


# ══════════════════════════════════════════════════════════════════════════════════════
# OPERATIONAL SAFETY — scheduler invocation, concurrency, period-end sync.
#
# These do not change any approved billing rule. They prove the already-approved lifecycle
# is safe to run repeatedly, from a scheduler, on more than one worker at once.
# ══════════════════════════════════════════════════════════════════════════════════════

# ── current_period_end extraction (item 3) ────────────────────────────────────────────────

def _period_event(*, items=None, top_level=None, event_type="customer.subscription.updated",
                  status="active", sub_id="sub_pe", event_id=None):
    obj = {"id": sub_id, "object": "subscription", "status": status,
           "customer": "cus_pe", "livemode": False, "metadata": {},
           "items": {"object": "list", "data": items if items is not None else []}}
    if top_level is not None:
        obj["current_period_end"] = top_level
    return {"id": event_id or f"evt_{uuid.uuid4().hex[:12]}", "object": "event",
            "type": event_type, "created": 1, "livemode": False, "data": {"object": obj}}


def test_item_level_period_end_is_extracted():
    """The API version this account runs puts current_period_end ONLY on the subscription
    item. A top-level-only read silently yields None and leaves the approved effective date
    with no anchor — verified against the live sandbox."""
    from app.services import payments_stripe_events as ev
    facts = ev.subscription_facts(_period_event(
        items=[{"id": "si_1", "current_period_end": 1790844720,
                "price": {"id": "price_devmonthly1"}}]))
    assert facts["current_period_end"] is not None
    assert facts["current_period_end"].tzinfo is not None, "must be timezone-aware UTC"


def test_top_level_period_end_still_works_as_a_fallback():
    """Kept so an older API version, or a replayed historical event, still resolves."""
    from app.services import payments_stripe_events as ev
    facts = ev.subscription_facts(_period_event(items=[], top_level=1790844720))
    assert facts["current_period_end"] is not None


def test_the_item_level_value_wins_over_the_top_level_one():
    from app.services import payments_stripe_events as ev
    facts = ev.subscription_facts(_period_event(
        items=[{"id": "si_1", "current_period_end": 1790844720}], top_level=1))
    assert facts["current_period_end"].year == 2026


def test_a_missing_period_end_is_none_never_fabricated():
    """No "now + a month" fallback anywhere. Absent stays absent, and request_plan_change then
    refuses with no_period_end rather than promising a date nobody can honour."""
    from app.services import payments_stripe_events as ev
    for ev_payload in (_period_event(items=[]),
                       _period_event(items=[{"id": "si_1"}]),
                       _period_event(items=[{"id": "si_1", "current_period_end": None}]),
                       _period_event(items=[{"id": "si_1", "current_period_end": "soon"}])):
        assert ev.subscription_facts(ev_payload)["current_period_end"] is None


def test_a_webhook_without_a_period_end_does_not_erase_a_stored_one():
    """The write is conditional on the value being present. An event that omits the boundary
    (or a differently-shaped one) must leave a good stored value alone."""
    known = datetime(2026, 10, 1, tzinfo=timezone.utc)
    sub, db = _sub("active"), _Stub()
    sub.current_period_end = known
    admin_crud.apply_subscription_provider_event(
        db, sub, new_state="active", current_period_end=None)
    assert sub.current_period_end == known


def test_a_repeated_webhook_does_not_corrupt_the_period_end():
    """Same event twice must leave the same value, not None and not a shifted one."""
    first = datetime(2026, 10, 1, tzinfo=timezone.utc)
    sub, db = _sub("active"), _Stub()
    for _ in range(3):
        admin_crud.apply_subscription_provider_event(
            db, sub, new_state="active", current_period_end=first)
    assert sub.current_period_end == first


def test_a_renewal_moves_the_period_end_forward():
    """Unlike the Stripe ids, this MUST update on every event: the boundary moves each renewal,
    and a stale value would schedule a change for a date already past."""
    old = datetime(2026, 10, 1, tzinfo=timezone.utc)
    new = datetime(2026, 11, 1, tzinfo=timezone.utc)
    sub, db = _sub("active"), _Stub()
    sub.current_period_end = old
    admin_crud.apply_subscription_provider_event(
        db, sub, new_state="active", current_period_end=new)
    assert sub.current_period_end == new


def test_the_reconciliation_script_reuses_the_webhook_extractor():
    """One extractor, so a backfill and the live webhook can never disagree about where the
    period end lives."""
    import reconcile_subscription_periods as recon
    src = code_only(recon)
    assert "_subscription_period_end" in src


def test_the_reconciliation_script_never_fabricates_or_writes_to_stripe():
    import reconcile_subscription_periods as recon
    src = code_only(recon)
    # Read-only against Stripe.
    assert "subscriptions.retrieve" in src
    for mutating in ("subscriptions.update", "subscriptions.create", "subscriptions.cancel",
                     "proration", "prices.create"):
        assert mutating not in src, f"a backfill must not {mutating}"
    # No invented date.
    for fabrication in ("timedelta(", "now() +", "utcnow() +"):
        assert fabrication not in src, "the period end must come from Stripe, never be computed"


def test_the_reconciliation_script_touches_only_null_period_ends():
    """A value already present is left alone — overwriting one could move the effective date of
    a change a customer has already been promised."""
    import reconcile_subscription_periods as recon
    src = code_only(recon)
    assert "current_period_end.is_(None)" in src


def test_the_reconciliation_script_changes_no_plan_or_status():
    import reconcile_subscription_periods as recon
    src = code_only(recon)
    for forbidden in ("plan_id =", "status =", "billing_interval =", "pending_plan_id ="):
        assert forbidden not in src, f"a backfill must not assign {forbidden}"


# ── scheduler invocation (items 1 and 6) ──────────────────────────────────────────────────

def test_the_maintenance_runner_includes_the_plan_change_job():
    from app.services import maintenance
    assert maintenance.apply_due_plan_changes in maintenance.JOBS


def test_the_existing_maintenance_endpoint_runs_every_job():
    """The scheduler entry point already existed and is reused rather than replaced — its own
    docstring names Cloud Scheduler as the intended caller."""
    from app.routers import commercial
    src = code_only(commercial.run_maintenance)
    assert "maintenance.run_all" in src


def test_the_maintenance_endpoint_requires_finance_authority():
    """An unauthenticated scheduler URL would let anyone trigger billing work."""
    from app.routers import commercial
    src = code_only(commercial.run_maintenance)
    assert "require_commercial" in src and "reconcile" in src


def test_one_failing_job_does_not_stop_the_others():
    from app.services import maintenance
    src = code_only(maintenance.run_all)
    assert "except" in src and "error" in src


def test_the_job_reports_contention_rather_than_hiding_it():
    from app.services import maintenance
    src = code_only(maintenance.apply_due_plan_changes)
    assert "contended" in src


# ── concurrency (item 5) ──────────────────────────────────────────────────────────────────

def test_the_due_query_claims_rows_with_skip_locked():
    from app.crud import admin as ac
    src = code_only(ac.due_plan_changes)
    assert "skip_locked=True" in src


def test_each_row_is_re_claimed_individually():
    """The batch lock is dropped by create_audit_log's internal commit, so the per-row claim is
    what actually prevents double application."""
    from app.crud import admin as ac
    src = code_only(ac.claim_due_plan_change)
    assert "skip_locked=True" in src
    assert "pending_plan_id" in src, "the claim must re-assert that a change is still pending"
    assert "plan_change_effective_at" in src, "and that it is actually due"
    from app.services import maintenance
    job = code_only(maintenance.apply_due_plan_changes)
    assert "claim_due_plan_change" in job


def test_the_claim_refuses_a_change_that_is_not_yet_due():
    """Re-checked at claim time, so a candidate list built just before the boundary cannot
    apply a change early."""
    from app.crud import admin as ac
    src = code_only(ac.claim_due_plan_change)
    assert "<=" in src


@needs_db
class TestPlanChangeConcurrency:
    @pytest.fixture
    def ctx(self, monkeypatch):
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            dev = Plan(name="CCDev", slug=f"ccdev-{tag}", is_active=True, max_users=5,
                       max_storage_gb=50, max_streaming_hours=20)
            biz = Plan(name="CCBiz", slug=f"ccbiz-{tag}", is_active=True, max_users=30,
                       max_storage_gb=500, max_streaming_hours=200)
            org = Organization(name=f"cc-{tag}")
            db.add_all([dev, biz, org]); db.flush()
            sub = Subscription(org_id=org.id, plan_id=dev.id, status="plan_change_scheduled",
                               seats=1, billing_interval="monthly",
                               current_period_end=datetime.now(timezone.utc) - timedelta(days=1),
                               pending_plan_id=biz.id, pending_billing_interval="monthly",
                               plan_change_effective_at=datetime.now(timezone.utc)
                               - timedelta(minutes=5))
            db.add(sub); db.commit(); db.refresh(sub)
            monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                                f"{dev.slug}:monthly=price_ccdevmonthly1,"
                                f"{biz.slug}:monthly=price_ccbizmonthly1")
            yield db, SimpleNamespace(dev=dev, biz=biz, org=org, sub_id=sub.id)
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM plans WHERE id IN (:a,:b)"), {"a": dev.id, "b": biz.id})
            db.commit()

    def test_a_second_worker_cannot_claim_a_locked_row(self, ctx):
        """The core protection, against real Postgres: while one transaction holds the claim,
        another gets nothing rather than a duplicate."""
        db, ids = ctx
        with Session(engine) as a, Session(engine) as b:
            first = admin_crud.claim_due_plan_change(a, ids.sub_id)
            assert first is not None, "the first worker must get the row"
            second = admin_crud.claim_due_plan_change(b, ids.sub_id)
            assert second is None, "a concurrent worker must skip a locked row"
            a.rollback()

    def test_the_row_becomes_claimable_again_once_released(self, ctx):
        db, ids = ctx
        with Session(engine) as a:
            assert admin_crud.claim_due_plan_change(a, ids.sub_id) is not None
            a.rollback()
        with Session(engine) as b:
            assert admin_crud.claim_due_plan_change(b, ids.sub_id) is not None

    def test_an_already_applied_change_is_not_claimable(self, ctx):
        """After one worker applies and commits, the predicate no longer matches — which is the
        case the batch lock could not cover."""
        db, ids = ctx
        with Session(engine) as a:
            sub = admin_crud.claim_due_plan_change(a, ids.sub_id)
            admin_crud.apply_plan_change(a, sub)
            a.commit()
        with Session(engine) as b:
            assert admin_crud.claim_due_plan_change(b, ids.sub_id) is None

    def test_two_concurrent_sweeps_apply_the_change_exactly_once(self, ctx):
        """The real race, two threads through the real job against real Postgres."""
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from app.services import maintenance
        db, ids = ctx
        barrier = threading.Barrier(2)

        def sweep():
            with Session(engine) as s:
                barrier.wait(timeout=10)
                return maintenance.apply_due_plan_changes(s)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result(timeout=60) for f in [pool.submit(sweep), pool.submit(sweep)]]

        assert sum(r["applied"] for r in results) == 1, \
            f"the change must be applied exactly once, got {[r['applied'] for r in results]}"
        db.expire_all()
        sub = db.get(Subscription, ids.sub_id)
        assert sub.plan_id == ids.biz.id and sub.status == "active"
        assert sub.pending_plan_id is None
        rows = [a for a in db.scalars(
            select(AuditLog).where(AuditLog.org_id == ids.org.id)).all()
            if a.action == "subscription.plan_change_applied"]
        assert len(rows) == 1, f"exactly one applied audit row, got {len(rows)}"

    def test_a_future_change_is_never_swept(self, ctx):
        db, ids = ctx
        from app.services import maintenance
        sub = db.get(Subscription, ids.sub_id)
        sub.plan_change_effective_at = datetime.now(timezone.utc) + timedelta(days=3)
        db.commit()
        with Session(engine) as s:
            result = maintenance.apply_due_plan_changes(s)
        assert result["applied"] == 0
        db.expire_all()
        sub = db.get(Subscription, ids.sub_id)
        assert sub.plan_id == ids.dev.id, "entitlements must be preserved before the date"
        assert sub.status == "plan_change_scheduled"

    def test_repeated_sweeps_are_idempotent(self, ctx):
        db, ids = ctx
        from app.services import maintenance
        with Session(engine) as s:
            first = maintenance.apply_due_plan_changes(s)
        with Session(engine) as s:
            second = maintenance.apply_due_plan_changes(s)
        assert first["applied"] == 1 and second["applied"] == 0
        rows = [a for a in db.scalars(
            select(AuditLog).where(AuditLog.org_id == ids.org.id)).all()
            if a.action == "subscription.plan_change_applied"]
        assert len(rows) == 1

    def test_a_stripe_failure_leaves_the_local_change_applied_and_audited(self, ctx,
                                                                          monkeypatch):
        """The deliberate ordering: our record moves first. A provider outage must not leave a
        customer billed for a plan our record denies them — so the local change stands, the
        failure is recorded, and reconciliation happens on retry or on the next webhook."""
        from app.services import maintenance, payments as payment_svc
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.stripe_subscription_id = f"sub_cc_{uuid.uuid4().hex[:8]}"
        db.commit()

        class _Boom:
            def change_subscription_price(self, *a, **kw):
                raise RuntimeError("stripe is down")

        monkeypatch.setattr(payment_svc, "get_provider", lambda name=None: _Boom())
        monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dummy_for_this_test")
        with Session(engine) as s:
            result = maintenance.apply_due_plan_changes(s)

        assert result["applied"] == 1, "the local change must still stand"
        assert result["provider_failed"] == 1, "and the provider failure must be reported"
        db.expire_all()
        sub = db.get(Subscription, ids.sub_id)
        assert sub.plan_id == ids.biz.id, "the customer is on the plan they asked for"
        assert sub.pending_plan_id is None, "and the pending fields are cleared"
        actions = [a.action for a in db.scalars(
            select(AuditLog).where(AuditLog.org_id == ids.org.id)).all()]
        assert "subscription.plan_change_applied" in actions
        assert "subscription.plan_change_provider_failed" in actions, \
            "the disagreement with Stripe must be recorded, never silent"
