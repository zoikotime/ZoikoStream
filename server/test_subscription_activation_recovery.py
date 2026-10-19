"""Recovering a paid subscription whose activation webhook never arrived.

── THE BUG, as observed in full ────────────────────────────────────────────────────────
A tenant completed Stripe Checkout and paid. Stripe held:

    session.payment_status = 'paid'
    session.subscription   = 'sub_...'   status 'active'
    session.customer       = 'cus_...'

Our row held `checkout_session_ref` and nothing else — no customer id, no subscription id,
still `trialing` on the old plan. Clicking Upgrade again answered 409 "already has a paid
subscription awaiting activation", so the tenant had paid, was not entitled, and could not
buy their way out.

THREE separate faults had to be fixed before the row could recover:

1. `StripePaymentProvider.retrieve_subscription` DID NOT EXIST. Both subscription_sync and
   maintenance called it. AttributeError is not a PaymentProviderError, so the repair path
   raised instead of repairing.

2. THE BACKSTOP COULD ONLY START FROM WHAT THE WEBHOOK WAS SUPPOSED TO WRITE.
   `needs_provider_sync` began at `has_live_provider_subscription`, which reads
   `stripe_subscription_id` — the exact field the missing webhook should have set. No
   webhook meant no id meant no recovery. The checkout session reference, which we DID hold
   and which Stripe resolves to both ids, was never used to bootstrap the link.

3. THE ROUTE WAS ILLEGAL IN ONE HOP. Stripe says `active`; Section 12 routes
   `trialing -> conversion_pending -> active`. Attempting the single hop produced "Illegal
   subscription transition trialing -> active" and the row stayed stuck.

NOTHING HERE TOUCHES STRIPE. The provider is a double throughout.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(PGUUID, "sqlite")
def _pg_uuid_on_sqlite(type_, compiler, **kw):
    return "CHAR(36)"


from app.db import Base
import app.models  # noqa: F401
from app.models.organization import Organization
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.services import payments as payment_svc
from app.services import subscription_sync

UTC = timezone.utc
SESSION_REF = "cs_test_paid_session"
STRIPE_SUB = "sub_test_live"
STRIPE_CUS = "cus_test_live"
DEV_PRICE = "price_developer_monthly"
BIZ_PRICE = "price_business_monthly"


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.sqlite'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    monkeypatch.setattr(subscription_sync, "settings_stripe_ready", lambda: True)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def world(db, monkeypatch):
    org = Organization(id=uuid.uuid4(), name="Northwind", slug="northwind")
    dev = Plan(id=uuid.uuid4(), name="Developer", slug="developer", price_monthly=49, is_active=True)
    biz = Plan(id=uuid.uuid4(), name="Business", slug="business", price_monthly=249, is_active=True)
    now = datetime.now(UTC)
    sub = Subscription(id=uuid.uuid4(), org_id=org.id, plan_id=dev.id, status="trialing",
                       started_at=now - timedelta(days=1), trial_ends_at=now + timedelta(days=13),
                       billing_interval="monthly", checkout_session_ref=SESSION_REF)
    db.add_all([org, dev, biz, sub])
    db.commit()

    # The approved price configuration the plan is resolved against.
    from app.crud import admin as admin_crud
    monkeypatch.setattr(admin_crud, "resolve_plan_for_provider_price",
                        lambda _db, ids: ((dev if DEV_PRICE in (ids or ()) else
                                           biz if BIZ_PRICE in (ids or ()) else None),
                                          None if set(ids or ()) & {DEV_PRICE, BIZ_PRICE}
                                          else "unmapped"))
    return {"org": org, "sub": sub, "developer": dev, "business": biz}


class _Session:
    def __init__(self, paid=True, terminal=True, sub=STRIPE_SUB, cus=STRIPE_CUS):
        self.paid, self.terminal = paid, terminal
        self.stripe_subscription_id, self.stripe_customer_id = sub, cus
        self.checkout_url = "" if terminal else "https://checkout.stripe.com/c/pay/x"


class _Remote:
    def __init__(self, status="active", prices=(DEV_PRICE,)):
        self.provider_status = status
        self.price_ids = prices
        self.stripe_customer_id = STRIPE_CUS
        self.current_period_end = datetime.now(UTC) + timedelta(days=30)
        self.provider_interval = "month"


class _Provider:
    def __init__(self, session=None, remote=None, session_exc=None, remote_exc=None):
        self._s, self._r = session or _Session(), remote or _Remote()
        self._se, self._re = session_exc, remote_exc
        self.retrieved_sessions, self.retrieved_subs = [], []

    def retrieve_subscription_checkout_session(self, ref):
        if self._se:
            raise self._se
        self.retrieved_sessions.append(ref)
        return self._s

    def retrieve_subscription(self, ref):
        if self._re:
            raise self._re
        self.retrieved_subs.append(ref)
        return self._r


def _sync(db, world, provider, monkeypatch):
    monkeypatch.setattr(payment_svc, "get_provider", lambda _n: provider)
    return subscription_sync.sync_from_provider(db, world["sub"])


# ── the recovery ────────────────────────────────────────────────────────────────────────

def test_a_paid_but_unlinked_checkout_is_recovered(db, world, monkeypatch):
    """The reported state, end to end."""
    out = _sync(db, world, _Provider(), monkeypatch)
    db.refresh(world["sub"])
    assert out["applied"] is True
    assert world["sub"].status == "active"


def test_the_provider_ids_are_recorded(db, world, monkeypatch):
    _sync(db, world, _Provider(), monkeypatch)
    db.refresh(world["sub"])
    assert world["sub"].stripe_subscription_id == STRIPE_SUB
    assert world["sub"].stripe_customer_id == STRIPE_CUS


def test_the_route_walks_the_legal_intermediate(db, world, monkeypatch):
    """Section 12 forbids trialing -> active directly."""
    assert subscription_sync._legal_route("trialing", "active") == \
        ["conversion_pending", "active"]


def test_the_plan_comes_from_the_price_stripe_is_billing(db, world, monkeypatch):
    """NOT from the card the customer clicked. The organization that prompted this clicked
    Business but the paid Stripe price was Developer — activating as Business would have
    granted an entitlement nobody paid for."""
    out = _sync(db, world, _Provider(remote=_Remote(prices=(DEV_PRICE,))), monkeypatch)
    db.refresh(world["sub"])
    assert out["plan_slug"] == "developer"
    assert world["sub"].plan_id == world["developer"].id


def test_a_business_price_activates_business(db, world, monkeypatch):
    _sync(db, world, _Provider(remote=_Remote(prices=(BIZ_PRICE,))), monkeypatch)
    db.refresh(world["sub"])
    assert world["sub"].plan_id == world["business"].id


def test_the_renewal_date_is_taken_from_stripe(db, world, monkeypatch):
    _sync(db, world, _Provider(), monkeypatch)
    db.refresh(world["sub"])
    assert world["sub"].current_period_end is not None


# ── what must NOT be entitled ───────────────────────────────────────────────────────────

def test_an_unpaid_session_links_nothing(db, world, monkeypatch):
    """An abandoned checkout leaves the same reference behind as a completed one. The
    difference between them is the difference between a customer who paid and one who did
    not."""
    out = _sync(db, world, _Provider(session=_Session(paid=False, terminal=False)), monkeypatch)
    db.refresh(world["sub"])
    assert out["applied"] is False
    assert world["sub"].stripe_subscription_id is None
    assert world["sub"].status == "trialing"


def test_an_unpaid_session_grants_no_entitlement_change(db, world, monkeypatch):
    _sync(db, world, _Provider(session=_Session(paid=False, terminal=False)), monkeypatch)
    db.refresh(world["sub"])
    assert world["sub"].plan_id == world["developer"].id


def test_a_paid_session_with_no_subscription_yet_waits(db, world, monkeypatch):
    """Transient: Stripe has taken the money but not attached a subscription. Inventing an id
    would be worse than waiting for the next poll."""
    out = _sync(db, world, _Provider(session=_Session(sub=None)), monkeypatch)
    db.refresh(world["sub"])
    assert out["applied"] is False
    assert world["sub"].stripe_subscription_id is None


def test_an_unmapped_price_refuses_to_activate(db, world, monkeypatch):
    """Activating without knowing WHICH plan was bought would entitle the tenant to whatever
    they had while Stripe bills something else."""
    out = _sync(db, world, _Provider(remote=_Remote(prices=("price_unknown",))), monkeypatch)
    db.refresh(world["sub"])
    assert out["applied"] is False
    assert world["sub"].status == "trialing"


def test_an_unmappable_provider_status_is_not_forced(db, world, monkeypatch):
    out = _sync(db, world, _Provider(remote=_Remote(status="incomplete")), monkeypatch)
    db.refresh(world["sub"])
    assert out["applied"] is False
    assert world["sub"].status == "trialing"


# ── idempotency and narrowness ──────────────────────────────────────────────────────────

def test_running_it_twice_changes_nothing_the_second_time(db, world, monkeypatch):
    first = _sync(db, world, _Provider(), monkeypatch)
    second = _sync(db, world, _Provider(), monkeypatch)
    assert first["applied"] is True
    assert second["applied"] is False
    assert second["outcome"] == subscription_sync.SYNC_ALREADY_CURRENT


def test_linkage_is_not_attempted_once_the_id_is_known(db, world, monkeypatch):
    world["sub"].stripe_subscription_id = STRIPE_SUB
    db.commit()
    provider = _Provider()
    _sync(db, world, provider, monkeypatch)
    assert provider.retrieved_sessions == [], "spent a Stripe call it did not need"


def test_needs_checkout_linkage_is_narrow(db, world):
    sub = world["sub"]
    assert subscription_sync.needs_checkout_linkage(sub) is True
    sub.stripe_subscription_id = STRIPE_SUB
    assert subscription_sync.needs_checkout_linkage(sub) is False
    sub.stripe_subscription_id = None
    sub.checkout_session_ref = None
    assert subscription_sync.needs_checkout_linkage(sub) is False
    assert subscription_sync.needs_checkout_linkage(None) is False


def test_an_entitled_looking_row_still_qualifies(db, world):
    """`trialing` IS an entitled state, so an entitlement test alone would have called this
    row healthy while Stripe was already billing it."""
    assert world["sub"].status == "trialing"
    assert subscription_sync.needs_provider_sync(world["sub"]) is True


# ── provider faults ─────────────────────────────────────────────────────────────────────

def test_an_unreachable_provider_is_reported_not_raised(db, world, monkeypatch):
    out = _sync(db, world, _Provider(
        session_exc=payment_svc.PaymentProviderError("down")), monkeypatch)
    assert out["applied"] is False
    assert out["error"]


def test_a_session_stripe_does_not_know_is_reported(db, world, monkeypatch):
    out = _sync(db, world, _Provider(
        session_exc=payment_svc.ProviderInvalidRequest("no such session")), monkeypatch)
    assert out["outcome"] == subscription_sync.SYNC_UNMAPPABLE
