"""C4 — duplicate-checkout protection that does not depend on webhook delivery.

THE DEFECT. The guard on POST /organization/billing/checkout-session read exactly one field,
`subscriptions.stripe_subscription_id`, and exactly one thing ever wrote it: the Stripe
subscription webhook. So while webhook delivery was broken that column stayed NULL forever, the
guard was vacuous, and every Upgrade click minted another live Stripe subscription. Measured in
production, not hypothesised: three active $249/month subscriptions on one organization,
$747/month total, none of them recorded locally, plus four settlements of $249 filed as
unattributable money.

Stripe's own idempotency key is not a substitute for a real guard. It collapses an IDENTICAL
retry onto one session, but the key expires after 24 hours and a different plan or cadence is a
different key by construction -- which is exactly why those three duplicates are dated more
than a day apart from each other.

THE FIX under test here has two halves, and both are necessary:
  * a LOCKED read (`current_subscription_for_update`) so two simultaneous requests for one
    organization serialise instead of both reading NULL and both proceeding;
  * an OUTSTANDING-SESSION check that asks the provider directly about the reference we
    recorded ourselves, so the answer does not wait on a delivery.

NO REAL STRIPE CALLS. The provider is replaced at the `get_provider` boundary.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.config import settings
from app.crud import admin as admin_crud
from app.db import engine
from app.models import AuditLog, Organization, Plan, Subscription, User
from app.routers import organization


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


class _CreatedSession:
    """Stands in for SubscriptionCheckoutResult."""

    def __init__(self, ref, url):
        self.checkout_session_ref = ref
        self.checkout_url = url
        self.stripe_customer_id = None
        self.stripe_subscription_id = None


class _SessionState:
    """Provider answer for retrieve_subscription_checkout_session()."""

    def __init__(self, *, paid=False, terminal=False,
                 url="https://checkout.stripe.com/c/pay/cs_open",
                 provider_status="open", payment_status="unpaid", ref="cs_existing",
                 subscription_id=None):
        self.paid = paid
        self.terminal = terminal
        self.checkout_url = url
        self.provider_status = provider_status
        self.provider_payment_status = payment_status
        self.checkout_session_ref = ref
        self.stripe_subscription_id = subscription_id
        self.stripe_customer_id = None


class _GuardProvider:
    """Records what the endpoint asked, and answers with a scripted session state."""

    def __init__(self, state=None, raise_on_retrieve=None):
        self.state = state
        self.raise_on_retrieve = raise_on_retrieve
        self.retrieved = []
        self.created = []

    def retrieve_subscription_checkout_session(self, ref):
        self.retrieved.append(ref)
        if self.raise_on_retrieve is not None:
            raise self.raise_on_retrieve
        return self.state

    def create_subscription_checkout_session(self, **kw):
        self.created.append(kw)
        return _CreatedSession(f"cs_new_{uuid.uuid4().hex[:10]}",
                               "https://checkout.stripe.com/c/pay/cs_new")


# ══════════════════════════════════════════════════════════════════════════════════════
# Behaviour
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestOutstandingCheckoutGuard:
    @pytest.fixture
    def ctx(self, monkeypatch):
        from app.schemas.organization import SubscriptionCheckoutCreate
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            dev = Plan(name="OGDev", slug=f"ogdev-{tag}", is_active=True)
            org = Organization(name=f"og-{tag}")
            db.add_all([dev, org])
            db.flush()
            user = User(email=f"og-{tag}@example.com", full_name="Owner",
                        username=f"og-{tag}", role="org_admin",
                        org_id=org.id, password_hash="x")
            # Mid-trial, a checkout already recorded, and NO stripe_subscription_id -- the exact
            # shape an undelivered webhook leaves behind.
            sub = Subscription(org_id=org.id, plan_id=dev.id, status="trialing", seats=1,
                               checkout_session_ref=f"cs_outstanding_{tag}")
            db.add_all([user, sub])
            db.commit()
            db.refresh(sub)
            db.refresh(user)
            monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                                f"{dev.slug}:monthly=price_ogdev_{tag}")
            monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_guard")
            body = SubscriptionCheckoutCreate(plan_slug=dev.slug, billing_interval="monthly")
            yield db, SimpleNamespace(tag=tag, dev=dev, org=org, user=user, sub_id=sub.id,
                                      body=body, outstanding=f"cs_outstanding_{tag}")
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM plans WHERE id=:p"), {"p": dev.id})
            db.commit()

    def _invoke(self, ids, db, provider, monkeypatch):
        from app.services import payments as payment_svc
        monkeypatch.setattr(payment_svc, "get_provider", lambda name=None: provider)
        return organization.create_subscription_checkout(
            ids.body, org=ids.org, user=ids.user, db=db)

    def test_a_paid_session_awaiting_activation_refuses_a_second_checkout(self, ctx, monkeypatch):
        """THE money bug. The payer has already been charged and the webhook simply has not
        landed; sending them to a second Stripe page is the double charge."""
        db, ids = ctx
        provider = _GuardProvider(_SessionState(paid=True, provider_status="complete",
                                                payment_status="paid",
                                                subscription_id="sub_already_paid"))
        with pytest.raises(HTTPException) as exc:
            self._invoke(ids, db, provider, monkeypatch)
        assert exc.value.status_code == 409
        assert provider.created == [], "no second Stripe session may be created"
        assert provider.retrieved == [ids.outstanding], "the guard must ask about OUR own ref"
        actions = [a.action for a in db.scalars(
            select(AuditLog).where(AuditLog.org_id == ids.org.id)).all()]
        assert "subscription.checkout_refused_already_paid" in actions, \
            "the refusal is evidence, not a silent bounce"

    def test_the_refusal_does_not_move_the_subscription_state(self, ctx, monkeypatch):
        """Section 18: only the verified webhook may advance the lifecycle. A refused duplicate
        must not activate anything -- and must not regress anything either."""
        db, ids = ctx
        provider = _GuardProvider(_SessionState(paid=True))
        with pytest.raises(HTTPException):
            self._invoke(ids, db, provider, monkeypatch)
        sub = db.get(Subscription, ids.sub_id)
        db.refresh(sub)
        assert sub.status == "trialing"
        assert sub.stripe_subscription_id is None, "the guard must not invent a linkage"
        assert sub.checkout_session_ref == ids.outstanding, "the paid ref must survive"

    def test_an_open_session_is_handed_back_instead_of_duplicated(self, ctx, monkeypatch):
        """A double-click, a refresh, or a second tab all resolve to ONE subscription."""
        db, ids = ctx
        provider = _GuardProvider(_SessionState(
            paid=False, terminal=False, ref=ids.outstanding,
            url="https://checkout.stripe.com/c/pay/cs_open"))
        out = self._invoke(ids, db, provider, monkeypatch)
        assert out["checkout_session_ref"] == ids.outstanding
        assert out["checkout_url"] == "https://checkout.stripe.com/c/pay/cs_open"
        assert out["reused_existing_session"] is True
        assert provider.created == [], "an open session must not be replaced"

    def test_an_expired_session_does_not_strand_the_tenant(self, ctx, monkeypatch):
        """Recovery must stay possible: an abandoned tab is not a duplicate."""
        db, ids = ctx
        provider = _GuardProvider(_SessionState(paid=False, terminal=True,
                                                provider_status="expired"))
        out = self._invoke(ids, db, provider, monkeypatch)
        assert len(provider.created) == 1, "an expired session must allow a fresh checkout"
        assert out["checkout_session_ref"].startswith("cs_new_")
        assert out.get("reused_existing_session") is None
        sub = db.get(Subscription, ids.sub_id)
        db.refresh(sub)
        assert sub.checkout_session_ref == out["checkout_session_ref"]

    def test_the_superseded_reference_is_retained_as_evidence(self, ctx, monkeypatch):
        """Overwriting the ref is safe once the old session is dead, but that old ref is the
        only handle a late inbound event could correlate on, so it stays in the trail."""
        db, ids = ctx
        provider = _GuardProvider(_SessionState(paid=False, terminal=True))
        self._invoke(ids, db, provider, monkeypatch)
        started = db.scalars(select(AuditLog).where(
            AuditLog.org_id == ids.org.id,
            AuditLog.action == "subscription.checkout_started")).all()
        assert started, "starting a checkout must be audited"
        assert any((a.meta or {}).get("superseded_checkout_session_ref") == ids.outstanding
                   for a in started)

    def test_a_provider_we_cannot_read_fails_closed(self, ctx, monkeypatch):
        """The question 'has this tenant already paid?' left unanswered must never resolve to
        'no' -- that is the answer that charges them twice."""
        from app.services import payments as payment_svc
        db, ids = ctx
        provider = _GuardProvider(
            raise_on_retrieve=payment_svc.ProviderUnavailable("stripe is unreachable"))
        with pytest.raises(HTTPException) as exc:
            self._invoke(ids, db, provider, monkeypatch)
        assert exc.value.status_code == 503
        assert provider.created == [], "an unknown payment state must not create a session"

    def test_a_state_unknown_provider_error_also_fails_closed(self, ctx, monkeypatch):
        """ProviderStateUnknown is the most dangerous case: the outcome may have applied."""
        from app.services import payments as payment_svc
        db, ids = ctx
        provider = _GuardProvider(
            raise_on_retrieve=payment_svc.ProviderStateUnknown("timeout, outcome unknown"))
        with pytest.raises(HTTPException) as exc:
            self._invoke(ids, db, provider, monkeypatch)
        assert exc.value.status_code == 503
        assert provider.created == []

    def test_an_unrecognised_session_status_is_not_treated_as_replaceable(self, ctx, monkeypatch):
        """Neither paid nor terminal, and no URL to hand back: refusal is the only safe answer.
        A future Stripe status must not silently become permission to create a second session."""
        db, ids = ctx
        provider = _GuardProvider(_SessionState(paid=False, terminal=False, url=None,
                                                provider_status="something_new"))
        with pytest.raises(HTTPException) as exc:
            self._invoke(ids, db, provider, monkeypatch)
        assert exc.value.status_code == 409
        assert provider.created == []

    def test_a_tenant_with_no_outstanding_session_is_unaffected(self, ctx, monkeypatch):
        """The guard must cost nothing on the normal first-purchase path."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.checkout_session_ref = None
        db.commit()
        provider = _GuardProvider()
        out = self._invoke(ids, db, provider, monkeypatch)
        assert provider.retrieved == [], "there is nothing to ask about"
        assert len(provider.created) == 1
        assert out["checkout_session_ref"].startswith("cs_new_")

    def test_an_already_linked_subscription_is_still_refused(self, ctx, monkeypatch):
        """The original guard must keep working: a tenant with a live Stripe subscription uses
        the scheduled plan-change path, never a second checkout."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.status = "active"
        sub.stripe_subscription_id = "sub_live_already"
        db.commit()
        provider = _GuardProvider()
        with pytest.raises(HTTPException) as exc:
            self._invoke(ids, db, provider, monkeypatch)
        assert exc.value.status_code == 409
        assert provider.created == []
        assert provider.retrieved == [], "the linked guard short-circuits before the session ask"

    def test_repeated_checkouts_do_not_accumulate_sessions(self, ctx, monkeypatch):
        """Idempotency at OUR layer rather than Stripe's: once a session is recorded and still
        open, every subsequent attempt returns that same one."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.checkout_session_ref = None
        db.commit()
        provider = _GuardProvider()
        first = self._invoke(ids, db, provider, monkeypatch)
        provider.state = _SessionState(paid=False, terminal=False,
                                       ref=first["checkout_session_ref"],
                                       url=first["checkout_url"])
        for _ in range(3):
            again = self._invoke(ids, db, provider, monkeypatch)
            assert again["checkout_session_ref"] == first["checkout_session_ref"]
        assert len(provider.created) == 1, "one session per purchase, however many clicks"


@needs_db
class TestCheckoutConcurrency:
    """Concurrency against real Postgres. The guard is only as good as the lock beneath it:
    two simultaneous requests both read `checkout_session_ref = NULL`, both passed the guard,
    and both created a session."""

    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            dev = Plan(name="CQDev", slug=f"cqdev-{tag}", is_active=True)
            org = Organization(name=f"cq-{tag}")
            db.add_all([dev, org])
            db.flush()
            sub = Subscription(org_id=org.id, plan_id=dev.id, status="trialing", seats=1)
            db.add(sub)
            db.commit()
            db.refresh(sub)
            yield db, SimpleNamespace(org=org, dev=dev, sub_id=sub.id)
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM plans WHERE id=:p"), {"p": dev.id})
            db.commit()

    def test_the_lock_returns_the_same_row_the_unlocked_read_would_pick(self, ctx):
        """The guard must lock the row the rest of the endpoint reads, not a different one."""
        db, ids = ctx
        with Session(engine) as a:
            locked = admin_crud.current_subscription_for_update(a, ids.org.id)
            unlocked = admin_crud._current_subs(a, [ids.org.id]).get(ids.org.id)
            assert locked is not None and unlocked is not None
            assert locked.id == unlocked.id
            a.rollback()

    def test_a_second_request_waits_rather_than_racing_past(self, ctx):
        """FOR UPDATE, not SKIP LOCKED: a competing checkout must not proceed on stale state.
        Proven by giving the second session a short lock_timeout -- it can only error on the
        wait if it was genuinely blocked."""
        from sqlalchemy.exc import OperationalError
        db, ids = ctx
        with Session(engine) as a:
            assert admin_crud.current_subscription_for_update(a, ids.org.id) is not None
            with Session(engine) as b:
                b.execute(text("SET LOCAL lock_timeout = '400ms'"))
                with pytest.raises(OperationalError):
                    admin_crud.current_subscription_for_update(b, ids.org.id)
                b.rollback()
            a.rollback()

    def test_the_row_is_available_again_once_the_first_request_finishes(self, ctx):
        """A held lock must not become a permanent block on ever checking out again."""
        db, ids = ctx
        with Session(engine) as a:
            assert admin_crud.current_subscription_for_update(a, ids.org.id) is not None
            a.rollback()
        with Session(engine) as b:
            assert admin_crud.current_subscription_for_update(b, ids.org.id) is not None
            b.rollback()

    def test_the_second_request_sees_the_first_requests_committed_ref(self, ctx):
        """What serialisation actually buys: the loser of the race reads the winner's session
        ref and therefore takes the 'one already exists' branch instead of minting a second."""
        db, ids = ctx
        with Session(engine) as a:
            sub = admin_crud.current_subscription_for_update(a, ids.org.id)
            sub.checkout_session_ref = "cs_winner"
            a.commit()
        with Session(engine) as b:
            sub = admin_crud.current_subscription_for_update(b, ids.org.id)
            assert sub.checkout_session_ref == "cs_winner"
            b.rollback()


# ══════════════════════════════════════════════════════════════════════════════════════
# Structural — the defect was a guard with the wrong input, so pin the inputs
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_guard_does_not_rely_on_the_webhook_populated_column_alone():
    """The whole defect was a guard whose only input was written by the webhook. Whatever else
    this endpoint grows, it must also consult the reference it recorded itself, and it must run
    against a locked row."""
    src = code_only(organization.create_subscription_checkout)
    assert "checkout_session_ref" in src, \
        "the guard must consider the checkout reference this endpoint recorded"
    assert "retrieve_subscription_checkout_session" in src, \
        "the guard must ask the provider about an outstanding session, not wait for a webhook"
    assert "current_subscription_for_update" in src, \
        "the guard must run against a locked row or concurrent checkouts race straight past it"


def test_the_guard_fails_closed_before_creating_a_session():
    """A provider error must not fall through into session creation."""
    src = code_only(organization.create_subscription_checkout)
    after_ask = src.split("retrieve_subscription_checkout_session", 1)[1]
    before_create = after_ask.split("create_subscription_checkout_session", 1)[0]
    assert "503" in before_create or "SERVICE_UNAVAILABLE" in before_create, \
        "an unreadable outstanding session must refuse, never proceed to a new checkout"


def test_the_locking_read_waits_instead_of_skipping():
    """SKIP LOCKED here would reintroduce the race it exists to prevent: a contended row would
    be silently treated as 'no subscription', not as 'someone else is checking out'."""
    src = code_only(admin_crud.current_subscription_for_update)
    assert "with_for_update()" in src, "the read must take a write lock"
    assert "skip_locked" not in src, \
        "a competing checkout must WAIT for the committed truth, not skip the row"
