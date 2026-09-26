"""E5 — the Ledger 1 webhook response contract: `applied` must report the transition.

THE DEFECT. `crud.record_provider_event_evidence` returns its own top-level `"applied": False`
— correctly, because it records evidence and applies nothing. Every Ledger 1 branch spread that
claim LAST, so it overwrote the branch's own outcome and the response reported `applied: false`
even when a Section 12 transition had just been applied and committed.

The database was never wrong. Only the reported field was. That still matters: this is the value
an operator, a monitor or an alert would read to decide whether an event took effect, and it was
a constant.

THE FIX. `**claim` is spread FIRST in every branch that carries an explicit outcome, so the
explicit keys win. The duplicate branch is deliberately excluded — it has no explicit keys, and
its contract is to return the STORED answer, whose real outcome lives in `result`.

This file changes no policy: no state, no plan, no entitlement, no period, no tenancy, no Stripe
behaviour. Tests below assert exactly that.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.crud import admin as admin_crud
from app.db import engine
from app.models import AuditLog, Organization, Plan, Subscription
from app.routers import commercial as commercial_router


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


def _sub_event(event_id, sub_ref, status, price_id, *,
               etype="customer.subscription.updated"):
    return {
        "id": event_id, "object": "event", "type": etype, "livemode": False,
        "created": int(datetime.now(timezone.utc).timestamp()),
        "data": {"object": {
            "id": sub_ref, "object": "subscription", "status": status,
            "customer": "cus_e5", "metadata": {},
            "items": {"object": "list", "data": [{
                "id": "si_e5",
                "current_period_end": int((datetime.now(timezone.utc)
                                           + timedelta(days=30)).timestamp()),
                "price": {"id": price_id, "object": "price",
                          "recurring": {"interval": "month"}},
            }]},
        }},
    }


def _checkout_event(event_id, session_ref, sub_ref):
    return {
        "id": event_id, "object": "event", "type": "checkout.session.completed",
        "livemode": False, "created": int(datetime.now(timezone.utc).timestamp()),
        "data": {"object": {
            "id": session_ref, "object": "checkout_session", "mode": "subscription",
            "subscription": sub_ref, "customer": "cus_e5",
            "payment_status": "paid", "status": "complete", "metadata": {},
        }},
    }


# ══════════════════════════════════════════════════════════════════════════════════════
# Structural — every branch carrying an outcome spreads the claim first
# ══════════════════════════════════════════════════════════════════════════════════════

def test_no_ledger_1_branch_spreads_the_claim_after_an_explicit_outcome():
    """The regression guard. A branch that spreads `**claim` last silently reverts `applied`
    to the claim's hard-coded False, which is exactly the defect this phase fixed."""
    src = code_only(commercial_router._handle_subscription_event)
    offenders = []
    for stmt in src.split("return {")[1:]:
        body = stmt.split("}", 1)[0]
        if "**claim" not in body:
            continue
        has_explicit = any(k in body for k in ("'applied'", '"applied"',
                                               "'reason'", '"reason"'))
        claim_last = body.rstrip().rstrip(",").endswith("**claim")
        if has_explicit and claim_last:
            offenders.append(body.strip()[:90])
    assert not offenders, (
        "these Ledger 1 returns spread **claim after their own outcome keys, so `applied` "
        f"is overwritten: {offenders}")


def test_the_duplicate_branch_is_deliberately_left_alone():
    """It carries no explicit outcome, and its contract is to return the STORED answer — the
    real result of a replay lives in `result`, not in the top-level `applied`."""
    src = code_only(commercial_router._handle_subscription_event)
    dup = src.split("claim.get('duplicate')", 1)[1].split("return", 1)[1].split("}", 1)[0]
    assert "**claim" in dup
    assert "applied" not in dup, \
        "the duplicate branch must not assert an outcome of its own"


def test_the_evidence_claim_still_reports_applied_false():
    """Confirms the premise rather than assuming it: the claim's own `applied` is a constant,
    which is WHY ordering matters. If this ever became meaningful, the fix would need review."""
    import inspect
    from app.crud import commercial as crud
    src = inspect.getsource(crud.record_provider_event_evidence)
    assert '"applied": False' in src


# ══════════════════════════════════════════════════════════════════════════════════════
# Behaviour against real Postgres
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestReportedApplied:
    @pytest.fixture
    def ctx(self, monkeypatch):
        from app.config import settings
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            dev = Plan(name=f"E5Dev-{tag}", slug=f"e5dev-{tag}", is_active=True,
                       max_users=5, max_storage_gb=50)
            biz = Plan(name=f"E5Biz-{tag}", slug=f"e5biz-{tag}", is_active=True,
                       max_users=30, max_storage_gb=500)
            db.add_all([dev, biz]); db.flush()
            dev_price, biz_price = f"price_e5dev_{tag}", f"price_e5biz_{tag}"
            monkeypatch.setattr(settings, "STRIPE_SUBSCRIPTION_PRICES",
                                f"{dev.slug}:monthly={dev_price},{biz.slug}:monthly={biz_price}")
            made = []

            def org(status="trialing", **kw):
                o = Organization(name=f"e5-{tag}-{len(made)}")
                db.add(o); db.flush()
                s = Subscription(org_id=o.id, plan_id=dev.id, status=status, seats=1, **kw)
                db.add(s); db.flush()
                made.append((o, s))
                return o, s

            db.commit()
            yield db, SimpleNamespace(tag=tag, dev=dev, biz=biz, org=org, made=made,
                                      dev_price=dev_price, biz_price=biz_price)
            for o, _ in made:
                db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": o.id})
                db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": o.id})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o.id})
            db.execute(text("DELETE FROM plans WHERE id IN (:a,:b)"),
                       {"a": dev.id, "b": biz.id})
            db.execute(text("DELETE FROM provider_events WHERE correlation_id='e5-test'"))
            db.commit()

    def _fire(self, db, event):
        return commercial_router._handle_subscription_event(
            db, event, correlation_id="e5-test", raw_body=json.dumps(event).encode())

    # ── 1. subscription-status branch ───────────────────────────────────────────────────
    def test_a_legal_status_transition_reports_applied_true(self, ctx):
        """THE fix. conversion_pending -> active is legal and was applied; before E5 this
        returned applied: false."""
        db, ids = ctx
        ref = f"sub_e5a_{ids.tag}"
        org, sub = ids.org("conversion_pending", stripe_subscription_id=ref)
        db.commit()
        res = self._fire(db, _sub_event(f"evt_a_{ids.tag}", ref, "active", ids.biz_price))
        db.refresh(sub)
        assert sub.status == "active", "precondition: the transition really happened"
        assert res["applied"] is True, "the response must report the transition, not the claim"
        assert res["subscription_state"] == "active"
        assert res["error"] is None

    def test_a_refused_status_transition_still_reports_applied_false(self, ctx):
        """The fix must not flip refusals to true."""
        db, ids = ctx
        ref = f"sub_e5b_{ids.tag}"
        org, sub = ids.org("trialing", stripe_subscription_id=ref)
        db.commit()
        res = self._fire(db, _sub_event(f"evt_b_{ids.tag}", ref, "active", ids.biz_price))
        db.refresh(sub)
        assert sub.status == "trialing", "Section 12 has no trialing -> active edge"
        assert res["applied"] is False and res["error"]

    def test_the_plan_fields_survive_the_reordering(self, ctx):
        """`plan_slug` and `plan_error` sit in the same return; reordering must not drop them."""
        db, ids = ctx
        ref = f"sub_e5c_{ids.tag}"
        org, sub = ids.org("conversion_pending", stripe_subscription_id=ref)
        db.commit()
        res = self._fire(db, _sub_event(f"evt_c_{ids.tag}", ref, "active", ids.biz_price))
        assert res["plan_slug"] == ids.biz.slug
        assert res["plan_error"] is None
        assert "provider_event_id" in res and "processing_status" in res, \
            "the claim's own keys must still be present"

    def test_an_unresolvable_price_still_reports_its_plan_error(self, ctx):
        db, ids = ctx
        ref = f"sub_e5d_{ids.tag}"
        org, sub = ids.org("conversion_pending", stripe_subscription_id=ref)
        db.commit()
        res = self._fire(db, _sub_event(f"evt_d_{ids.tag}", ref, "active", "price_not_ours"))
        assert res["applied"] is True, "the lifecycle fact is real"
        assert res["plan_slug"] is None and res["plan_error"]

    # ── 2. checkout-correlation branch ──────────────────────────────────────────────────
    def test_a_checkout_correlation_that_advances_reports_applied_true(self, ctx):
        db, ids = ctx
        ref, session = f"sub_e5e_{ids.tag}", f"cs_e5e_{ids.tag}"
        org, sub = ids.org("trialing", checkout_session_ref=session)
        db.commit()
        res = self._fire(db, _checkout_event(f"evt_e_{ids.tag}", session, ref))
        db.refresh(sub)
        assert sub.status == "conversion_pending"
        assert res["applied"] is True, "advanced to conversion_pending"
        assert res["reason"] == "checkout_correlated"
        assert res["subscription_state"] == "conversion_pending"

    def test_a_checkout_correlation_that_cannot_advance_reports_applied_false(self, ctx):
        """Correlation still happens; the Section 12 move does not, because `active` has no
        edge to conversion_pending. `applied` must distinguish the two."""
        db, ids = ctx
        ref, session = f"sub_e5f_{ids.tag}", f"cs_e5f_{ids.tag}"
        org, sub = ids.org("active", checkout_session_ref=session)
        db.commit()
        res = self._fire(db, _checkout_event(f"evt_f_{ids.tag}", session, ref))
        db.refresh(sub)
        assert sub.status == "active"
        assert res["applied"] is False
        assert res["reason"] == "checkout_correlated"
        assert sub.stripe_subscription_id == ref, "correlation still bound the ids"

    # ── 3. idempotency unchanged ────────────────────────────────────────────────────────
    def test_a_replayed_event_keeps_the_existing_duplicate_contract(self, ctx):
        db, ids = ctx
        ref = f"sub_e5g_{ids.tag}"
        org, sub = ids.org("conversion_pending", stripe_subscription_id=ref)
        db.commit()
        ev = _sub_event(f"evt_g_{ids.tag}", ref, "active", ids.biz_price)
        first = self._fire(db, ev)
        second = self._fire(db, ev)
        db.refresh(sub)
        assert first["applied"] is True
        assert second["duplicate"] is True
        assert second["applied"] is False, \
            "the duplicate contract is unchanged — the stored outcome lives in `result`"
        assert second["result"], "the original outcome must still be returned"
        assert sub.status == "active"
        transitions = [a for a in db.scalars(select(AuditLog).where(
            AuditLog.org_id == org.id,
            AuditLog.action == "subscription.transition")).all()]
        assert len(transitions) == 1, "a replay must not re-apply"

    def test_an_unmatched_subscription_reports_the_same_shape_as_before(self, ctx):
        db, ids = ctx
        res = self._fire(db, _sub_event(f"evt_h_{ids.tag}", f"sub_absent_{ids.tag}",
                                         "active", ids.biz_price))
        assert res["applied"] is False
        assert res["reason"] == "no_matching_subscription"
        assert "provider_event_id" in res

    def test_an_unmapped_provider_status_reports_the_same_shape_as_before(self, ctx):
        db, ids = ctx
        ref = f"sub_e5i_{ids.tag}"
        org, sub = ids.org("active", stripe_subscription_id=ref)
        db.commit()
        res = self._fire(db, _sub_event(f"evt_i_{ids.tag}", ref, "incomplete", ids.biz_price))
        db.refresh(sub)
        assert res["applied"] is False
        assert res["reason"].startswith("unmapped_provider_status:")
        assert sub.status == "active", "an unmapped status must change nothing"

    # ── 5. nothing else changed ─────────────────────────────────────────────────────────
    def test_no_state_plan_period_or_tenancy_behaviour_changed(self, ctx):
        """A response-shape fix must be observably invisible to the ledger."""
        db, ids = ctx
        ref = f"sub_e5j_{ids.tag}"
        boundary = datetime.now(timezone.utc) + timedelta(days=11)
        org, sub = ids.org("conversion_pending", stripe_subscription_id=ref,
                           current_period_end=boundary)
        other_org, other_sub = ids.org("trialing")
        db.commit()
        before_other = (other_sub.status, other_sub.plan_id)

        res = self._fire(db, _sub_event(f"evt_j_{ids.tag}", ref, "active", ids.biz_price))
        db.refresh(sub); db.refresh(other_sub)

        assert sub.status == "active" and sub.plan_id == ids.biz.id
        assert sub.org_id == org.id, "tenancy unchanged"
        assert sub.current_period_end is not None, "period synced from the event, as before"
        assert (other_sub.status, other_sub.plan_id) == before_other, \
            "another tenant must be untouched"
        assert res["applied"] is True

    def test_entitlement_resolution_is_unaffected(self, ctx):
        from app.services import org as org_svc
        db, ids = ctx
        ref = f"sub_e5k_{ids.tag}"
        org, sub = ids.org("conversion_pending", stripe_subscription_id=ref)
        db.commit()
        self._fire(db, _sub_event(f"evt_k_{ids.tag}", ref, "active", ids.biz_price))
        assert org_svc.enforcement_plan(db, org.id).id == ids.biz.id


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. Ledger 2 is untouched
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_ledger_2_path_was_not_modified():
    """E5 touched only `_handle_subscription_event`. The Ledger 2 ingest builds its own
    response and its `applied` semantics are asserted by test_checkout_reconciliation.py."""
    src = code_only(commercial_router.stripe_webhook)
    assert "ingest_provider_event" in src or "translate" in src, \
        "the Ledger 2 dispatch must still be present"
    l1 = code_only(commercial_router._handle_subscription_event)
    for ledger2_only in ("ingest_provider_event", "ingest_dispute_event",
                         "reconcile_checkout_session"):
        assert ledger2_only not in l1, \
            "Ledger 1 must not have acquired a Ledger 2 call"
