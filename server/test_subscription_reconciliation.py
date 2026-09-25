"""C3 — Stripe <-> database subscription reconciliation.

WHY THIS JOB EXISTS. Every other part of Ledger 1 assumes the webhook arrived, and nothing
noticed when it did not. The measured production failure was three paid Stripe subscriptions
with no local row at all, and four settlements of $249 filed as unattributable money -- none of
which any job could see, because no job ever compared the two sides.

WHAT IT MUST NOT DO, which is most of what these tests assert. It reports; it never resolves.
Every divergence it can detect has at least two possible causes with opposite correct fixes -- a
local row missing its Stripe subscription might be an undelivered webhook (link it) or a
subscription cancelled out of band (do not) -- and Section 18 forbids unlocking anything because
a provider fact looked convincing. So the job writes audit evidence and returns findings, and a
human decides.

NO REAL STRIPE CALLS. The provider is replaced at the `get_provider` boundary.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.config import settings
from app.db import engine
from app.models import AuditLog, Organization, Plan, Subscription
from app.services import maintenance


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


class _RemoteSub:
    """Stands in for ProviderSubscriptionState."""

    def __init__(self, ref, *, status="active", price_ids=(), interval="month",
                 customer="cus_x", period_end=None):
        self.provider_subscription_ref = ref
        self.provider_status = status
        self.price_ids = tuple(price_ids)
        self.provider_interval = interval
        self.current_period_end = period_end or (datetime.now(timezone.utc) + timedelta(days=20))
        self.stripe_customer_id = customer
        self.cancel_at_period_end = False


class _RemoteSession:
    """Stands in for SubscriptionCheckoutState."""

    def __init__(self, *, paid=False, terminal=False, status="open", payment_status="unpaid",
                 subscription_id=None):
        self.paid = paid
        self.terminal = terminal
        self.provider_status = status
        self.provider_payment_status = payment_status
        self.stripe_subscription_id = subscription_id
        self.stripe_customer_id = None
        self.checkout_url = None
        self.checkout_session_ref = "cs_x"


class _ReconProvider:
    """Scripted provider. `subs` maps ref -> _RemoteSub or an exception to raise."""

    def __init__(self, subs=None, sessions=None, listing=None, list_error=None):
        self.subs = subs or {}
        self.sessions = sessions or {}
        self.listing = listing
        self.list_error = list_error
        self.retrieved = []
        self.listed = 0

    def retrieve_subscription(self, ref):
        self.retrieved.append(ref)
        found = self.subs.get(ref)
        if isinstance(found, Exception):
            raise found
        if found is None:
            from app.services.payments import ProviderInvalidRequest
            raise ProviderInvalidRequest(f"No such subscription: {ref}")
        return found

    def retrieve_subscription_checkout_session(self, ref):
        found = self.sessions.get(ref)
        if isinstance(found, Exception):
            raise found
        return found or _RemoteSession()

    def list_subscriptions(self, *, limit=100):
        self.listed += 1
        if self.list_error is not None:
            raise self.list_error
        return list(self.listing or [])


def _kinds(result):
    return result["findings"]


@needs_db
class TestReconciliation:
    @pytest.fixture
    def ctx(self, monkeypatch):
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            dev = Plan(name="RcDev", slug=f"rcdev-{tag}", is_active=True)
            biz = Plan(name="RcBiz", slug=f"rcbiz-{tag}", is_active=True)
            org = Organization(name=f"rc-{tag}")
            db.add_all([dev, biz, org])
            db.flush()
            sub = Subscription(org_id=org.id, plan_id=dev.id, status="active", seats=1,
                               billing_interval="monthly",
                               stripe_subscription_id=f"sub_rc_{tag}")
            db.add(sub)
            db.commit()
            db.refresh(sub)
            dev_price = f"price_rcdev_{tag}"
            biz_price = f"price_rcbiz_{tag}"
            monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_recon")
            monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                                f"{dev.slug}:monthly={dev_price},"
                                f"{dev.slug}:annual=price_rcdevyr_{tag},"
                                f"{biz.slug}:monthly={biz_price}")
            yield db, SimpleNamespace(tag=tag, dev=dev, biz=biz, org=org, sub_id=sub.id,
                                      ref=f"sub_rc_{tag}", dev_price=dev_price,
                                      biz_price=biz_price)
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM audit_logs WHERE org_id IS NULL "
                            "AND action='commercial.maintenance.subscription_divergence'"))
            db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM plans WHERE id IN (:a,:b)"), {"a": dev.id, "b": biz.id})
            db.commit()

    def _run(self, db, provider, monkeypatch, **kw):
        from app.services import payments as payment_svc
        monkeypatch.setattr(payment_svc, "get_provider", lambda name=None: provider)
        return maintenance.reconcile_stripe_subscriptions(db, **kw)

    # ── the happy path ────────────────────────────────────────────────────────────────────
    def test_a_matching_subscription_produces_no_findings(self, ctx, monkeypatch):
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, status="active",
                                      price_ids=[ids.dev_price], interval="month")},
            listing=[_RemoteSub(ids.ref, price_ids=[ids.dev_price])])
        out = self._run(db, provider, monkeypatch)
        assert out["findings"] == {}, out["detail"]
        assert out["verified_matching"] == 1
        assert out["local_linked_examined"] == 1

    # ── 1. paid Stripe subscription with no local row ─────────────────────────────────────
    def test_an_orphan_stripe_subscription_is_reported(self, ctx, monkeypatch):
        """The measured production failure: money being billed against no local record."""
        db, ids = ctx
        orphan = _RemoteSub("sub_orphan_xyz", status="active", price_ids=[ids.biz_price])
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.dev_price])},
            listing=[_RemoteSub(ids.ref, price_ids=[ids.dev_price]), orphan])
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("orphan_stripe_subscription") == 1
        entry = [f for f in out["detail"] if f["finding"] == "orphan_stripe_subscription"][0]
        assert entry["stripe_subscription_id"] == "sub_orphan_xyz"

    def test_a_cancelled_orphan_is_not_reported_as_live_revenue(self, ctx, monkeypatch):
        """History is not a divergence. Only a subscription the provider is actually billing
        counts as unattributed revenue."""
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.dev_price])},
            listing=[_RemoteSub(ids.ref, price_ids=[ids.dev_price]),
                     _RemoteSub("sub_dead", status="canceled", price_ids=[ids.biz_price])])
        out = self._run(db, provider, monkeypatch)
        assert "orphan_stripe_subscription" not in _kinds(out)

    # ── 2. local row whose Stripe subscription is gone ────────────────────────────────────
    def test_a_local_row_stripe_does_not_know_is_reported_not_deleted(self, ctx, monkeypatch):
        db, ids = ctx
        provider = _ReconProvider(subs={}, listing=[])      # retrieve raises "no such"
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("stripe_subscription_missing") == 1
        # The row is evidence. It must survive.
        sub = db.get(Subscription, ids.sub_id)
        db.refresh(sub)
        assert sub is not None and sub.stripe_subscription_id == ids.ref
        assert sub.status == "active", "reporting must not change commercial state"

    # ── 3. status divergence ──────────────────────────────────────────────────────────────
    def test_a_status_mismatch_is_reported(self, ctx, monkeypatch):
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, status="past_due",
                                      price_ids=[ids.dev_price])},
            listing=[])
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("status_mismatch") == 1
        entry = [f for f in out["detail"] if f["finding"] == "status_mismatch"][0]
        assert entry["provider_status"] == "past_due" and entry["local_status"] == "active"
        sub = db.get(Subscription, ids.sub_id)
        db.refresh(sub)
        assert sub.status == "active", "the job must never adopt the provider status"

    def test_a_provider_status_with_no_section_12_counterpart_is_not_forced(self, ctx, monkeypatch):
        """Section 12 has no state for a half-finished signup. Inventing one would be a
        commercial rule this job has no authority to add."""
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, status="incomplete",
                                      price_ids=[ids.dev_price])},
            listing=[])
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("provider_status_unmappable") == 1
        assert "status_mismatch" not in _kinds(out)

    # ── 4. plan / price divergence ────────────────────────────────────────────────────────
    def test_a_plan_mismatch_is_reported(self, ctx, monkeypatch):
        """Stripe bills Business; our record says Developer."""
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.biz_price])},
            listing=[])
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("plan_mismatch") == 1
        entry = [f for f in out["detail"] if f["finding"] == "plan_mismatch"][0]
        assert entry["provider_plan_slug"] == ids.biz.slug
        sub = db.get(Subscription, ids.sub_id)
        db.refresh(sub)
        assert sub.plan_id == ids.dev.id, "the job must never re-plan a subscription"

    def test_a_price_no_operator_approved_is_reported_as_unresolvable(self, ctx, monkeypatch):
        """A dashboard-created subscription. The lifecycle fact is real, the plan is not ours."""
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=["price_not_ours"])},
            listing=[])
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("price_unresolvable") == 1
        assert "plan_mismatch" not in _kinds(out)

    # ── 5. billing-interval divergence ────────────────────────────────────────────────────
    def test_an_interval_mismatch_is_reported(self, ctx, monkeypatch):
        """Stripe bills yearly; our record says monthly."""
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.dev_price],
                                      interval="year")},
            listing=[])
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("interval_mismatch") == 1
        entry = [f for f in out["detail"] if f["finding"] == "interval_mismatch"][0]
        assert entry["provider_interval_as_ours"] == "annual"
        assert entry["local_billing_interval"] == "monthly"

    def test_an_absent_local_interval_is_reported_separately_from_a_conflict(self, ctx, monkeypatch):
        """An absence is not a contradiction. This is the state a paid subscription created
        before cadence was tracked sits in -- bookkeeping, triaged apart from a real conflict."""
        db, ids = ctx
        sub = db.get(Subscription, ids.sub_id)
        sub.billing_interval = None
        db.commit()
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.dev_price],
                                      interval="month")},
            listing=[])
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("interval_unrecorded") == 1
        assert "interval_mismatch" not in _kinds(out)

    # ── 6. paid checkout that never linked ────────────────────────────────────────────────
    def test_a_paid_checkout_with_no_linkage_is_reported(self, ctx, monkeypatch):
        """The single most expensive divergence: money taken, no subscription bound to it."""
        db, ids = ctx
        pending = Subscription(org_id=ids.org.id, plan_id=ids.dev.id, status="trialing",
                               seats=1, checkout_session_ref=f"cs_paid_{ids.tag}",
                               started_at=datetime.now(timezone.utc) - timedelta(days=1))
        db.add(pending)
        db.commit()
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.dev_price])},
            sessions={f"cs_paid_{ids.tag}": _RemoteSession(paid=True, status="complete",
                                                            payment_status="paid",
                                                            subscription_id="sub_unlinked")},
            listing=[])
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("checkout_paid_without_linkage") == 1
        entry = [f for f in out["detail"]
                 if f["finding"] == "checkout_paid_without_linkage"][0]
        assert entry["stripe_subscription_id"] == "sub_unlinked"
        # Reported, NOT linked. Linking is a commercial decision for a human.
        db.refresh(pending)
        assert pending.stripe_subscription_id is None
        assert pending.status == "trialing"

    def test_an_unpaid_open_checkout_is_not_a_finding(self, ctx, monkeypatch):
        """A customer still deciding is not a divergence."""
        db, ids = ctx
        pending = Subscription(org_id=ids.org.id, plan_id=ids.dev.id, status="trialing",
                               seats=1, checkout_session_ref=f"cs_open_{ids.tag}",
                               started_at=datetime.now(timezone.utc) - timedelta(days=1))
        db.add(pending)
        db.commit()
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.dev_price])},
            sessions={f"cs_open_{ids.tag}": _RemoteSession(paid=False)},
            listing=[])
        out = self._run(db, provider, monkeypatch)
        assert "checkout_paid_without_linkage" not in _kinds(out)

    # ── Stripe API failure — fail closed ──────────────────────────────────────────────────
    def test_a_provider_error_is_reported_as_unverified_not_as_matching(self, ctx, monkeypatch):
        """'We could not check' and 'nothing is wrong' must never render identically."""
        from app.services.payments import ProviderUnavailable
        db, ids = ctx
        provider = _ReconProvider(subs={ids.ref: ProviderUnavailable("stripe is down")},
                                  listing=[])
        out = self._run(db, provider, monkeypatch)
        assert _kinds(out).get("provider_unreachable") == 1
        assert out["verified_matching"] == 0, "an unreadable subscription is not verified"
        assert out["unverified_due_to_provider_errors"] == 1

    def test_a_provider_error_changes_no_local_state(self, ctx, monkeypatch):
        """A Stripe outage must not be able to alter our ledger in any way."""
        from app.services.payments import ProviderStateUnknown
        db, ids = ctx
        before = db.get(Subscription, ids.sub_id)
        snapshot = (before.status, before.plan_id, before.billing_interval,
                    before.stripe_subscription_id)
        provider = _ReconProvider(subs={ids.ref: ProviderStateUnknown("timeout")},
                                  list_error=ProviderStateUnknown("timeout"))
        out = self._run(db, provider, monkeypatch)
        db.refresh(before)
        assert (before.status, before.plan_id, before.billing_interval,
                before.stripe_subscription_id) == snapshot
        assert _kinds(out).get("provider_unreachable") == 2, \
            "both the per-subscription read and the listing failure must be reported"

    def test_a_failed_listing_does_not_claim_there_are_no_orphans(self, ctx, monkeypatch):
        from app.services.payments import ProviderUnavailable
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.dev_price])},
            list_error=ProviderUnavailable("rate limited"))
        out = self._run(db, provider, monkeypatch)
        assert out["provider_subscriptions_examined"] == 0
        assert any(f["finding"] == "provider_unreachable"
                   and f.get("scope") == "list_subscriptions" for f in out["detail"])

    # ── configuration ─────────────────────────────────────────────────────────────────────
    def test_the_job_skips_rather_than_guesses_when_stripe_is_unconfigured(self, ctx, monkeypatch):
        """An environment with no Stripe has nothing to reconcile against. It must say so, and
        it must NOT report a clean bill of health -- there are no findings because nothing was
        checked, which is a different statement."""
        db, ids = ctx
        monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
        out = maintenance.reconcile_stripe_subscriptions(db)
        assert out.get("skipped"), f"expected a skip reason, got {out}"
        assert "findings" not in out, \
            "a skipped run must not present an empty findings set as 'nothing is wrong'"
        assert "verified_matching" not in out

    def test_a_truncated_provider_page_is_visible_in_the_result(self, ctx, monkeypatch):
        """'No orphans found' must not silently mean 'we only looked at the first page'."""
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.dev_price])},
            listing=[_RemoteSub(f"sub_pad_{i}", status="canceled") for i in range(3)])
        out = self._run(db, provider, monkeypatch, limit=3)
        assert out["provider_page_truncated"] is True

    # ── evidence + idempotency ────────────────────────────────────────────────────────────
    def test_every_finding_lands_in_the_audit_trail(self, ctx, monkeypatch):
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, status="past_due",
                                      price_ids=[ids.biz_price], interval="year")},
            listing=[])
        out = self._run(db, provider, monkeypatch)
        rows = db.scalars(select(AuditLog).where(
            AuditLog.org_id == ids.org.id,
            AuditLog.action == "commercial.maintenance.subscription_divergence")).all()
        assert len(rows) == sum(_kinds(out).values())
        reasons = {(r.meta or {}).get("reason") for r in rows}
        assert {"status_mismatch", "plan_mismatch", "interval_mismatch"} <= reasons

    def test_running_twice_changes_no_commercial_state(self, ctx, monkeypatch):
        """Idempotent in the sense that matters for money: the ledger after two runs is
        identical to the ledger after one."""
        db, ids = ctx
        provider = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, status="past_due",
                                      price_ids=[ids.biz_price])},
            listing=[])
        first = self._run(db, provider, monkeypatch)
        sub = db.get(Subscription, ids.sub_id)
        db.refresh(sub)
        snapshot = (sub.status, sub.plan_id, sub.billing_interval, sub.stripe_subscription_id)
        second = self._run(db, provider, monkeypatch)
        db.refresh(sub)
        assert (sub.status, sub.plan_id, sub.billing_interval,
                sub.stripe_subscription_id) == snapshot
        assert _kinds(first) == _kinds(second), "the same inputs must yield the same findings"

    def test_a_fixed_divergence_stops_being_reported(self, ctx, monkeypatch):
        """The job must be able to go quiet -- otherwise nobody will read it."""
        db, ids = ctx
        diverged = _ReconProvider(
            subs={ids.ref: _RemoteSub(ids.ref, price_ids=[ids.biz_price])}, listing=[])
        assert _kinds(self._run(db, diverged, monkeypatch)).get("plan_mismatch") == 1
        # A human resolves it by moving our record onto the plan Stripe actually bills.
        sub = db.get(Subscription, ids.sub_id)
        sub.plan_id = ids.biz.id
        db.commit()
        assert "plan_mismatch" not in _kinds(self._run(db, diverged, monkeypatch))

    def test_no_subscription_row_is_ever_deleted(self, ctx, monkeypatch):
        """Even when Stripe denies knowing any of them. The row is the evidence that we
        believed we had a subscription; a sweep that removed it would destroy exactly what a
        reconciliation needs."""
        from sqlalchemy import func
        db, ids = ctx
        before = db.scalar(select(func.count(Subscription.id)))
        provider = _ReconProvider(subs={}, listing=[])       # everything looks missing
        out = self._run(db, provider, monkeypatch)
        assert out["findings"].get("stripe_subscription_missing") == 1
        assert db.scalar(select(func.count(Subscription.id))) == before


# ══════════════════════════════════════════════════════════════════════════════════════
# Structural — the job is registered, and it holds no authority it should not have
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_job_is_registered_with_the_maintenance_framework():
    names = [j.__name__ for j in maintenance.JOBS]
    assert "reconcile_stripe_subscriptions" in names
    # Registered alongside the other jobs, so the one authenticated scheduler endpoint drives
    # it -- this module deliberately adds no runner of its own.
    assert maintenance.reconcile_stripe_subscriptions in maintenance.JOBS


def test_the_job_mutates_no_subscription_field():
    """The whole design: it reads both sides and writes evidence. Any assignment onto a
    subscription here would make a reporting sweep into a commercial actor."""
    src = code_only(maintenance.reconcile_stripe_subscriptions)
    for forbidden in ("sub.status =", "sub.plan_id =", "sub.billing_interval =",
                      "sub.stripe_subscription_id =", "sub.checkout_session_ref =",
                      "db.delete", "sub.trial_ends_at ="):
        assert forbidden not in src, f"reconciliation must not write {forbidden!r}"


def test_the_job_grants_no_entitlement_and_moves_no_money():
    src = code_only(maintenance.reconcile_stripe_subscriptions)
    for forbidden in ("apply_subscription_provider_event", "request_plan_change",
                      "apply_plan_change", "change_subscription_price",
                      "create_subscription_checkout_session", "authorize", "capture"):
        assert forbidden not in src, \
            f"a reconciliation sweep must not call {forbidden!r} -- it reports, it does not act"


def test_the_status_comparison_reuses_the_one_existing_mapping():
    """A second Stripe-status-to-Section-12 mapping would be a second source of truth."""
    src = code_only(maintenance.reconcile_stripe_subscriptions)
    assert "_SUBSCRIPTION_STATUS_STATE" in src, \
        "reuse the mapping in payments_stripe_events rather than coining another"
