"""Reopening checkout when the subscription already carries a session reference.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
`retrieve_subscription_checkout_session` was CALLED but never DEFINED.

    routers/organization.py:351   the upgrade click
    services/maintenance.py:461   the reconciliation sweep
    services/subscription_sync.py a comment asserting it "already exists"

StripePaymentProvider defined authorize, capture, refund, create_checkout_session,
create_subscription_checkout_session, change_subscription_price and open_dispute — and not
this one. So the call raised AttributeError, which is NOT a PaymentProviderError, so the
endpoint's `except payment_svc.PaymentProviderError` could not catch it. FastAPI turned it
into a 500, and Billing.jsx maps `status >= 500 && status !== 503` to
"Something went wrong starting checkout. Nothing has been charged."

WHY IT LOOKED INTERMITTENT: the branch only runs when `sub.checkout_session_ref` is set. A
FIRST upgrade click had no reference, created a session, and stored one — so it worked, and
every click after it failed. Reproducing it needed a subscription that had already been
through checkout once, which is why a clean fixture passed while the real organization did
not.

NOTHING HERE TOUCHES STRIPE. The adapter is replaced by a double in the endpoint tests, and
the adapter's own tests drive a fake Stripe client.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
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
from app.models.user import User
from app.routers import organization as org_router
from app.schemas.organization import SubscriptionCheckoutCreate
from app.services import payments as payment_svc
from app.services.payments_stripe import StripePaymentProvider

UTC = timezone.utc
EXISTING_REF = "cs_test_existing_session"


# ── the adapter method itself ───────────────────────────────────────────────────────────

class _FakeSession:
    def __init__(self, **kw):
        self.id = kw.get("id", EXISTING_REF)
        self.status = kw.get("status")
        self.payment_status = kw.get("payment_status")
        self.url = kw.get("url")
        self.subscription = kw.get("subscription")
        self.customer = kw.get("customer")


class _FakeClient:
    def __init__(self, session=None, raises=None):
        self._session, self._raises = session, raises
        outer = self

        class _Sessions:
            def retrieve(self, ref):
                if outer._raises:
                    raise outer._raises
                outer.retrieved = ref
                return outer._session

        class _Checkout:
            sessions = _Sessions()

        self.checkout = _Checkout()
        self.retrieved = None


def _provider(session=None, raises=None):
    p = StripePaymentProvider.__new__(StripePaymentProvider)   # skip the real Stripe client
    p._client = _FakeClient(session, raises)
    p._log = lambda *a, **k: None
    return p


def test_the_method_exists_on_the_adapter():
    """The whole defect in one line: it was called from two places and never defined."""
    assert hasattr(StripePaymentProvider, "retrieve_subscription_checkout_session")


def test_an_open_unpaid_session_is_resumable():
    prov = _provider(_FakeSession(status="open", payment_status="unpaid",
                                  url="https://checkout.stripe.com/c/pay/x"))
    out = prov.retrieve_subscription_checkout_session(EXISTING_REF)
    assert out.paid is False and out.terminal is False
    assert out.checkout_url.startswith("https://checkout.stripe.com/")


def test_a_completed_paid_session_reports_paid():
    prov = _provider(_FakeSession(status="complete", payment_status="paid",
                                  subscription="sub_123", customer="cus_123"))
    out = prov.retrieve_subscription_checkout_session(EXISTING_REF)
    assert out.paid is True
    assert out.stripe_subscription_id == "sub_123"
    assert out.stripe_customer_id == "cus_123"


def test_a_trial_that_collected_no_card_counts_as_paid():
    """`no_payment_required` finishes the session and owes nothing. Treating it as unpaid
    would push the customer through checkout a second time."""
    prov = _provider(_FakeSession(status="complete", payment_status="no_payment_required"))
    assert prov.retrieve_subscription_checkout_session(EXISTING_REF).paid is True


def test_an_expired_session_is_terminal_and_has_no_url():
    """Stripe nulls the URL on expiry, which is why `open` is read from status rather than
    inferred from a URL being present."""
    prov = _provider(_FakeSession(status="expired", payment_status="unpaid", url=None))
    out = prov.retrieve_subscription_checkout_session(EXISTING_REF)
    assert out.terminal is True
    assert out.checkout_url == ""


def test_a_finished_session_is_terminal_even_when_unpaid():
    prov = _provider(_FakeSession(status="complete", payment_status="unpaid"))
    assert prov.retrieve_subscription_checkout_session(EXISTING_REF).terminal is True


def test_an_empty_reference_is_refused_without_calling_stripe():
    prov = _provider(_FakeSession(status="open"))
    with pytest.raises(payment_svc.ProviderInvalidRequest):
        prov.retrieve_subscription_checkout_session("   ")
    assert prov._client.retrieved is None


def test_a_provider_fault_is_translated_not_raised_raw():
    """It must surface as a PaymentProviderError so the endpoint's except clause can see it —
    the exact thing AttributeError bypassed."""
    prov = _provider(raises=RuntimeError("stripe exploded"))
    with pytest.raises(payment_svc.PaymentProviderError):
        prov.retrieve_subscription_checkout_session(EXISTING_REF)


# ── the endpoint behaviour that depends on it ───────────────────────────────────────────

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
    """The REPORTED state: trialing on Developer, no Stripe subscription, and an outstanding
    checkout reference left by an earlier Upgrade click."""
    org = Organization(id=uuid.uuid4(), name="Northwind", slug="northwind")
    user = User(id=uuid.uuid4(), org_id=org.id, full_name="Ada", username="ada",
                email="ada@example.com", password_hash="x", role="org_admin")
    org.owner_user_id = user.id
    dev = Plan(id=uuid.uuid4(), name="Developer", slug="developer", price_monthly=49, is_active=True)
    biz = Plan(id=uuid.uuid4(), name="Business", slug="business", price_monthly=249, is_active=True)
    now = datetime.now(UTC)
    sub = Subscription(id=uuid.uuid4(), org_id=org.id, plan_id=dev.id, status="trialing",
                       started_at=now - timedelta(days=1), trial_ends_at=now + timedelta(days=13),
                       billing_interval="monthly", checkout_session_ref=EXISTING_REF)
    db.add_all([org, user, dev, biz, sub])
    db.commit()
    return {"org": org, "user": user, "sub": sub, "developer": dev}


class _Result:
    def __init__(self, **kw):
        self.checkout_session_ref = kw.get("ref", "cs_test_new")
        self.checkout_url = kw.get("url", "https://checkout.stripe.com/c/pay/new")
        self.paid = kw.get("paid", False)
        self.terminal = kw.get("terminal", False)
        self.stripe_subscription_id = kw.get("sub")
        self.stripe_customer_id = None


class _Adapter:
    def __init__(self, retrieved):
        self._retrieved = retrieved
        self.created = 0

    def retrieve_subscription_checkout_session(self, ref):
        return self._retrieved

    def create_subscription_checkout_session(self, **kw):
        self.created += 1
        self.last = kw
        return _Result()


def _upgrade(db, world, adapter, monkeypatch, slug="business"):
    monkeypatch.setattr(payment_svc, "get_provider", lambda _n: adapter)
    return org_router.create_subscription_checkout(
        SubscriptionCheckoutCreate(plan_slug=slug, billing_interval="monthly"),
        org=world["org"], user=world["user"], db=db)


def test_an_outstanding_session_no_longer_500s(db, world, monkeypatch):
    """The reported failure. Before the fix this raised AttributeError -> 500."""
    adapter = _Adapter(_Result(ref=EXISTING_REF, url="https://checkout.stripe.com/c/pay/old"))
    out = _upgrade(db, world, adapter, monkeypatch)
    assert out["checkout_url"].startswith("https://checkout.stripe.com/")


def test_an_open_session_is_REUSED_not_duplicated(db, world, monkeypatch):
    """One tenant must not accumulate parallel Stripe sessions by clicking Upgrade twice."""
    adapter = _Adapter(_Result(ref=EXISTING_REF, url="https://checkout.stripe.com/c/pay/old"))
    out = _upgrade(db, world, adapter, monkeypatch)
    assert out["reused_existing_session"] is True
    assert adapter.created == 0, "a second Checkout Session was created"


def test_an_expired_session_is_superseded_by_a_new_one(db, world, monkeypatch):
    adapter = _Adapter(_Result(terminal=True, url=""))
    out = _upgrade(db, world, adapter, monkeypatch)
    assert adapter.created == 1
    assert out.get("reused_existing_session") is not True


def test_an_already_paid_session_is_refused_rather_than_charged_again(db, world, monkeypatch):
    """Section 18: the money moved and only the webhook may activate. A new session here
    would be a second charge."""
    adapter = _Adapter(_Result(paid=True, sub="sub_live"))
    with pytest.raises(HTTPException) as caught:
        _upgrade(db, world, adapter, monkeypatch)
    assert caught.value.status_code == 409
    assert adapter.created == 0


def test_a_provider_fault_while_retrieving_is_503_not_500(db, world, monkeypatch):
    class _Failing(_Adapter):
        def retrieve_subscription_checkout_session(self, ref):
            raise payment_svc.PaymentProviderError("Stripe unreachable")

    with pytest.raises(HTTPException) as caught:
        _upgrade(db, world, _Failing(None), monkeypatch)
    assert caught.value.status_code == 503


def test_starting_checkout_still_grants_no_entitlement(db, world, monkeypatch):
    adapter = _Adapter(_Result(ref=EXISTING_REF, url="https://checkout.stripe.com/c/pay/old"))
    before = world["sub"].status
    _upgrade(db, world, adapter, monkeypatch)
    db.refresh(world["sub"])
    assert world["sub"].status == before
    assert world["sub"].plan_id == world["developer"].id


def test_a_live_provider_subscription_still_refuses_a_second_checkout(db, world, monkeypatch):
    world["sub"].stripe_subscription_id = "sub_live_123"
    world["sub"].status = "active"
    db.commit()
    adapter = _Adapter(_Result())
    with pytest.raises(HTTPException) as caught:
        _upgrade(db, world, adapter, monkeypatch)
    assert caught.value.status_code == 409
    assert adapter.created == 0
