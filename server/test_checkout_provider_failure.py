"""Starting checkout when the Stripe ADAPTER cannot be resolved at all.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
`POST /billing/checkout-session` called `payment_svc.get_provider("stripe")` unwrapped. Every
way that call fails is a deployment fault rather than anything the customer did:

  * ProviderNotConfigured / UnknownProvider — PaymentProviderError subclasses;
  * ModuleNotFoundError — get_provider imports the Stripe SDK lazily, so an image built
    without it fails on the first upgrade click rather than at startup;
  * whatever stripe.StripeClient() raises on a malformed key.

Unwrapped, each left the route as an unhandled exception. The browser received a 500, and
Billing.jsx maps `status >= 500 && status !== 503` to "Something went wrong starting checkout.
Nothing has been charged." — a sentence that describes a misconfigured deployment as a
mystery, and the reported symptom of a Developer -> Business upgrade that never reached
Stripe.

Every other failure on this route already answers with a status the page can explain (404
plan, 409 duplicate, 422 cadence, 502 provider, 503 unconfigured). This was the one gap, and
these tests hold it closed.

NOTHING HERE TOUCHES STRIPE. The adapter is replaced before the route runs, so no Checkout
Session is created and no card is charged.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException


@compiles(PGUUID, "sqlite")
def _pg_uuid_on_sqlite(type_, compiler, **kw):
    return "CHAR(36)"


from app.db import Base
import app.models  # noqa: F401
from app.models.organization import Organization
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.user import User
from app.routers import organization as org_router
from app.schemas.organization import SubscriptionCheckoutCreate
from app.services import payments as payment_svc

UTC = timezone.utc


# ── fixtures ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'checkout.sqlite'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def world(db):
    """An organization entitled to Developer with NO Stripe subscription.

    That is exactly what `provision_initial_subscription` leaves behind, and it is the state
    in the report: Business renders "Upgrade" because Developer is ranked below it, while
    `checkout_locked` is false because no provider subscription exists — so the click takes
    the checkout path rather than the plan-change one.
    """
    org = Organization(id=uuid.uuid4(), name="Northwind", slug="northwind")
    user = User(id=uuid.uuid4(), org_id=org.id, full_name="Ada", username="ada",
                email="ada@example.com", password_hash="x", role="org_admin")
    org.owner_user_id = user.id
    dev = Plan(id=uuid.uuid4(), name="Developer", slug="developer", price_monthly=49, is_active=True)
    biz = Plan(id=uuid.uuid4(), name="Business", slug="business", price_monthly=249, is_active=True)
    now = datetime.now(UTC)
    sub = Subscription(id=uuid.uuid4(), org_id=org.id, plan_id=dev.id, status="trialing",
                       started_at=now - timedelta(days=1),
                       trial_ends_at=now + timedelta(days=13), billing_interval="monthly")
    db.add_all([org, user, dev, biz, sub])
    db.commit()
    return {"org": org, "user": user, "developer": dev, "business": biz, "sub": sub}


class _Session:
    checkout_url = "https://checkout.stripe.com/c/pay/cs_test_fake"
    checkout_session_ref = "cs_test_fake"
    id = "cs_test_fake"
    paid = False
    terminal = False


class _Result:
    checkout_url = _Session.checkout_url
    checkout_session_ref = _Session.checkout_session_ref


class _WorkingProvider:
    def create_subscription_checkout_session(self, **kw):
        self.last = kw
        return _Result()

    def retrieve_subscription_checkout_session(self, ref):
        return _Session()


def _upgrade(db, world, slug="business"):
    return org_router.create_subscription_checkout(
        SubscriptionCheckoutCreate(plan_slug=slug, billing_interval="monthly"),
        org=world["org"], user=world["user"], db=db)


def _provider_raises(monkeypatch, exc):
    def boom(_name):
        raise exc
    monkeypatch.setattr(payment_svc, "get_provider", boom)


# ── the gap this closes ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [
    payment_svc.ProviderNotConfigured("STRIPE_SECRET_KEY is not configured"),
    payment_svc.UnknownProvider("Unknown payment provider 'stripe'"),
    ModuleNotFoundError("No module named 'stripe'"),
])
def test_an_unresolvable_provider_is_503_not_500(db, world, monkeypatch, exc):
    """500 is what made the console say "Something went wrong"; 503 is a refusal it explains."""
    _provider_raises(monkeypatch, exc)
    with pytest.raises(HTTPException) as caught:
        _upgrade(db, world)
    assert caught.value.status_code == 503, \
        f"{type(exc).__name__} still escapes as an unhandled 500"


def test_the_refusal_says_nothing_was_charged(db, world, monkeypatch):
    _provider_raises(monkeypatch, payment_svc.ProviderNotConfigured("no key"))
    with pytest.raises(HTTPException) as caught:
        _upgrade(db, world)
    assert "charged" in caught.value.detail.lower()


def test_the_refusal_leaks_no_configuration(db, world, monkeypatch):
    """This reaches a browser. A provider message must not carry key material or internals."""
    _provider_raises(monkeypatch, payment_svc.ProviderNotConfigured("STRIPE_SECRET_KEY=sk_test_zzz"))
    with pytest.raises(HTTPException) as caught:
        _upgrade(db, world)
    detail = caught.value.detail
    assert "sk_test" not in detail and "STRIPE_SECRET_KEY" not in detail


def test_the_diagnosis_is_logged_for_an_operator(db, world, monkeypatch, caplog):
    """§10: the log must identify WHY, since the customer-facing sentence deliberately cannot."""
    import logging
    _provider_raises(monkeypatch, payment_svc.ProviderNotConfigured("no key"))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(HTTPException):
            _upgrade(db, world)
    assert any("ProviderNotConfigured" in r.getMessage() for r in caplog.records), \
        "an operator cannot diagnose this without the exception type in the log"


def test_nothing_is_persisted_when_the_provider_cannot_be_resolved(db, world, monkeypatch):
    """No session reference may be bound to a checkout that never existed."""
    _provider_raises(monkeypatch, payment_svc.ProviderNotConfigured("no key"))
    with pytest.raises(HTTPException):
        _upgrade(db, world)
    db.refresh(world["sub"])
    assert world["sub"].checkout_session_ref is None


# ── the upgrade itself still works ──────────────────────────────────────────────────────

def test_developer_to_business_reaches_stripe_and_returns_a_url(db, world, monkeypatch):
    """The reported scenario, end to end against a stubbed adapter."""
    provider = _WorkingProvider()
    monkeypatch.setattr(payment_svc, "get_provider", lambda _n: provider)
    out = _upgrade(db, world)
    assert out["checkout_url"].startswith("https://checkout.stripe.com/")


def test_the_business_price_is_the_one_sent_to_stripe(db, world, monkeypatch):
    """The browser sends a SLUG; the price is resolved server-side from configuration."""
    from app.config import settings
    priced = settings.subscription_price_map().get(("business", "monthly"))
    if not priced:
        pytest.skip("no approved Business monthly price configured in this environment")
    provider = _WorkingProvider()
    monkeypatch.setattr(payment_svc, "get_provider", lambda _n: provider)
    _upgrade(db, world)
    assert provider.last["price_id"] == priced


def test_the_checkout_reference_is_recorded_for_the_webhook(db, world, monkeypatch):
    monkeypatch.setattr(payment_svc, "get_provider", lambda _n: _WorkingProvider())
    _upgrade(db, world)
    db.refresh(world["sub"])
    assert world["sub"].checkout_session_ref == "cs_test_fake"


def test_starting_checkout_grants_no_entitlement(db, world, monkeypatch):
    """Section 18: only a verified provider event may activate. The plan must not move."""
    monkeypatch.setattr(payment_svc, "get_provider", lambda _n: _WorkingProvider())
    before = world["sub"].status
    _upgrade(db, world)
    db.refresh(world["sub"])
    assert world["sub"].status == before
    assert world["sub"].plan_id == world["developer"].id


# ── the safety rules are untouched ──────────────────────────────────────────────────────

def test_a_live_provider_subscription_still_refuses_a_second_checkout(db, world, monkeypatch):
    """The money bug this guard exists for: two live Stripe subscriptions on one customer."""
    world["sub"].status = "active"
    world["sub"].stripe_subscription_id = "sub_live_123"
    db.commit()
    monkeypatch.setattr(payment_svc, "get_provider", lambda _n: _WorkingProvider())
    with pytest.raises(HTTPException) as caught:
        _upgrade(db, world)
    assert caught.value.status_code == 409


def test_an_unknown_plan_is_still_a_404(db, world, monkeypatch):
    monkeypatch.setattr(payment_svc, "get_provider", lambda _n: _WorkingProvider())
    with pytest.raises(HTTPException) as caught:
        _upgrade(db, world, slug="no-such-plan")
    assert caught.value.status_code == 404


def test_a_provider_fault_during_creation_is_still_502(db, world, monkeypatch):
    """Resolving the adapter and USING it fail differently and must stay distinguishable:
    503 means we never reached Stripe, 502 means Stripe itself refused."""
    class _Failing(_WorkingProvider):
        def create_subscription_checkout_session(self, **kw):
            raise payment_svc.PaymentProviderError("Stripe is unreachable")

    monkeypatch.setattr(payment_svc, "get_provider", lambda _n: _Failing())
    with pytest.raises(HTTPException) as caught:
        _upgrade(db, world)
    assert caught.value.status_code == 502
