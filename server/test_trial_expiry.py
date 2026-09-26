"""C2 — trial expiry: Section 12's TRIALING -> TRIAL_EXPIRED edge.

THE DEFECT THIS CLOSES. `trial_expired` was a legal, terminal node in the §12 graph that
NOTHING in the codebase ever wrote, while `trialing` is in SUBSCRIPTION_ENTITLED_STATES. So
every 14-day trial granted its plan permanently, and provisioning 2 055 organizations would have
created 2 055 plans that never end.

WHAT THIS IMPLEMENTATION DELIBERATELY DOES NOT DECIDE. Post-trial entitlement policy is Product
decision **P2** and is UNDECIDED. The job moves one column. It removes no capability, changes no
limit, cancels nothing and deletes nothing. The class TestProductDecisionBoundaryP2 at the
bottom pins that boundary as tests, so a future change that quietly encodes a policy here has to
break one of them first.

NO STRIPE, EVER. Reaching the end of a trial charges nobody — §12 gives `trialing` no edge to
`active`, so the only route to a paid subscription is a deliberate checkout through
CONVERSION_PENDING. Tests below assert the path imports and calls no provider at all.
"""

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
from app.models.subscription import (
    SUBSCRIPTION_ENTITLED_STATES,
    SUBSCRIPTION_TRANSITIONS,
    SUBSCRIPTION_TRIALING_STATES,
    normalize_subscription_state,
)
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

NOW = datetime.now(timezone.utc)
PAST = NOW - timedelta(days=1)
FUTURE = NOW + timedelta(days=7)


# ══════════════════════════════════════════════════════════════════════════════════════
# The state machine itself — no database needed
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_edge_this_job_takes_is_the_one_section_12_declares():
    assert "trial_expired" in SUBSCRIPTION_TRANSITIONS["trialing"]


def test_trial_expired_is_terminal():
    """Nothing may follow it. A tenant does not drift out of an expired trial on a clock; they
    convert through a deliberate checkout, which starts from a different state entirely."""
    assert SUBSCRIPTION_TRANSITIONS["trial_expired"] == ()


def test_trial_expired_grants_no_entitlement():
    assert "trial_expired" not in SUBSCRIPTION_ENTITLED_STATES


def test_the_trialing_state_set_covers_the_legacy_spelling():
    """The production row that matters is spelled `trial`, not `trialing`. A query that spelled
    the state inline would silently skip exactly that row."""
    assert set(SUBSCRIPTION_TRIALING_STATES) == {"trial", "trialing"}
    assert normalize_subscription_state("trial") == "trialing"


def test_the_expiry_path_reaches_no_provider_and_moves_no_money():
    """Structural. Reaching the end of a trial must never charge anyone."""
    for fn in (maintenance.expire_due_trials, admin_crud.expire_trial):
        src = code_only(fn)
        for forbidden in ("stripe", "payment", "provider", "charge", "invoice", "price",
                          "authorize", "capture", "checkout"):
            assert forbidden not in src.lower(), \
                f"{fn.__name__} must not touch {forbidden!r} — a trial ending collects nothing"


def test_the_job_encodes_no_post_expiry_policy():
    """P2 is undecided, so the job must not write anything except the lifecycle state."""
    src = code_only(admin_crud.expire_trial)
    for forbidden in ("plan_id =", "max_storage_gb", "max_users", "max_streaming_hours",
                      "cancelled_at =", "trial_ends_at =", "db.delete", "seats ="):
        assert forbidden not in src, \
            f"expire_trial must not write {forbidden!r} — that would be deciding P2"


def test_the_transition_is_asked_for_not_asserted():
    """The state machine stays the single authority on what may follow what."""
    src = code_only(admin_crud.expire_trial)
    assert "subscription_transition_error" in src, \
        "the edge must be validated by the graph, not assumed by this function"


# ══════════════════════════════════════════════════════════════════════════════════════
# Behaviour against real Postgres
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestTrialExpirySweep:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            plan = Plan(name="TeDev", slug=f"tedev-{tag}", is_active=True, max_users=5,
                        max_storage_gb=50, max_streaming_hours=20)
            org = Organization(name=f"te-{tag}")
            db.add_all([plan, org])
            db.flush()
            db.commit()
            db.refresh(plan)
            db.refresh(org)
            made: list = []

            def make(status="trialing", ends=PAST, **kw):
                """One subscription on its OWN organization, so a sweep touching several rows
                cannot be mistaken for one row being touched twice."""
                o = Organization(name=f"te-{tag}-{len(made)}")
                db.add(o)
                db.flush()
                sub = Subscription(org_id=o.id, plan_id=plan.id, status=status, seats=1,
                                   trial_ends_at=ends,
                                   started_at=kw.pop("started_at", NOW - timedelta(days=14)),
                                   **kw)
                db.add(sub)
                db.commit()
                db.refresh(sub)
                made.append((o, sub))
                return sub

            yield db, SimpleNamespace(tag=tag, plan=plan, org=org, make=make, made=made)

            for o, _ in made:
                db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": o.id})
                db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": o.id})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o.id})
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM plans WHERE id=:p"), {"p": plan.id})
            db.commit()

    def _sweep(self, db):
        return maintenance.expire_due_trials(db)

    def _actions(self, db, org_id):
        return [a.action for a in db.scalars(
            select(AuditLog).where(AuditLog.org_id == org_id)).all()]

    # ── 1 / 2 / 3 / 4 — the date predicate ──────────────────────────────────────────────
    def test_an_expired_trial_transitions(self, ctx):
        db, ids = ctx
        sub = ids.make(status="trialing", ends=PAST)
        out = self._sweep(db)
        db.refresh(sub)
        assert sub.status == "trial_expired"
        assert out["expired"] == 1 and out["refused"] == 0

    def test_an_active_trial_is_left_alone(self, ctx):
        db, ids = ctx
        sub = ids.make(status="trialing", ends=FUTURE)
        out = self._sweep(db)
        db.refresh(sub)
        assert sub.status == "trialing"
        assert out["expired"] == 0 and out["candidates"] == 0

    def test_a_trial_ending_exactly_now_expires(self, ctx):
        """`<=`, not `<`: a trial whose end date has arrived is over."""
        db, ids = ctx
        moment = datetime.now(timezone.utc)
        sub = ids.make(status="trialing", ends=moment)
        claimed = admin_crud.claim_expired_trial(db, sub.id, now=moment)
        assert claimed is not None, "at-the-boundary must be claimable"
        db.rollback()

    def test_a_trial_ending_a_moment_later_does_not(self, ctx):
        db, ids = ctx
        moment = datetime.now(timezone.utc)
        sub = ids.make(status="trialing", ends=moment + timedelta(seconds=1))
        assert admin_crud.claim_expired_trial(db, sub.id, now=moment) is None
        db.rollback()

    # ── 5 — NULL end date ───────────────────────────────────────────────────────────────
    def test_a_null_trial_end_date_is_never_treated_as_expired(self, ctx):
        """An absence is not a deadline in the past. Treating NULL as overdue would end the
        trial of every row predating the column being populated, on no evidence at all."""
        db, ids = ctx
        sub = ids.make(status="trialing", ends=None)
        out = self._sweep(db)
        db.refresh(sub)
        assert sub.status == "trialing"
        assert out["expired"] == 0
        assert out["manual_review_no_trial_end_date"] >= 1
        assert any(d["subscription_id"] == str(sub.id)
                   for d in out["detail"]["no_trial_end_date"])

    def test_a_null_end_date_produces_no_transition_audit(self, ctx):
        db, ids = ctx
        sub = ids.make(status="trialing", ends=None)
        self._sweep(db)
        assert "subscription.transition" not in self._actions(db, sub.org_id)

    # ── 6 / 7 — legacy spelling ─────────────────────────────────────────────────────────
    def test_a_legacy_trial_row_with_an_expired_date_transitions(self, ctx):
        """The production row is spelled `trial`. It must be swept like any other."""
        db, ids = ctx
        sub = ids.make(status="trial", ends=PAST)
        out = self._sweep(db)
        db.refresh(sub)
        assert sub.status == "trial_expired"
        assert out["expired"] == 1

    def test_a_legacy_trial_row_with_a_future_date_stays_trialing(self, ctx):
        db, ids = ctx
        sub = ids.make(status="trial", ends=FUTURE)
        self._sweep(db)
        db.refresh(sub)
        assert sub.status == "trial", "the stored spelling is preserved, not rewritten"

    def test_the_legacy_row_with_a_null_date_is_untouched_and_reported(self, ctx):
        """This is the exact shape of the one production row (decision 8: do not modify)."""
        db, ids = ctx
        sub = ids.make(status="trial", ends=None)
        out = self._sweep(db)
        db.refresh(sub)
        assert sub.status == "trial"
        assert any(d["subscription_id"] == str(sub.id) and d["status"] == "trial"
                   for d in out["detail"]["no_trial_end_date"])

    # ── 7 — already expired ─────────────────────────────────────────────────────────────
    def test_an_already_expired_subscription_is_not_processed_again(self, ctx):
        db, ids = ctx
        sub = ids.make(status="trial_expired", ends=PAST)
        out = self._sweep(db)
        db.refresh(sub)
        assert sub.status == "trial_expired"
        assert out["candidates"] == 0, "it is not in the trialing set, so never a candidate"
        assert "subscription.transition" not in self._actions(db, sub.org_id)

    def test_calling_expire_trial_directly_on_an_expired_row_is_a_clean_no_op(self, ctx):
        """No error, and no second audit row — §12 treats re-asserting a state as a no-op."""
        db, ids = ctx
        sub = ids.make(status="trial_expired", ends=PAST)
        applied, error = admin_crud.expire_trial(db, sub)
        db.commit()
        assert applied is False and error is None
        assert "subscription.transition" not in self._actions(db, sub.org_id)

    # ── 8 — several at once ─────────────────────────────────────────────────────────────
    def test_many_expired_trials_are_all_processed(self, ctx):
        db, ids = ctx
        subs = [ids.make(status="trialing", ends=PAST) for _ in range(4)]
        subs.append(ids.make(status="trial", ends=PAST))          # legacy spelling too
        untouched = ids.make(status="trialing", ends=FUTURE)
        out = self._sweep(db)
        assert out["expired"] == 5
        for s in subs:
            db.refresh(s)
            assert s.status == "trial_expired"
        db.refresh(untouched)
        assert untouched.status == "trialing", "an unrelated organization must not be swept"

    # ── 9 — concurrency ─────────────────────────────────────────────────────────────────
    def test_a_second_worker_cannot_claim_a_locked_row(self, ctx):
        db, ids = ctx
        sub = ids.make(status="trialing", ends=PAST)
        with Session(engine) as a, Session(engine) as b:
            first = admin_crud.claim_expired_trial(a, sub.id)
            assert first is not None, "the first worker must get the row"
            assert admin_crud.claim_expired_trial(b, sub.id) is None, \
                "SKIP LOCKED: a concurrent worker must skip, never block or duplicate"
            a.rollback()

    def test_an_already_expired_row_is_not_claimable(self, ctx):
        """The case the batch lock cannot cover, because create_audit_log commits and so
        releases every other lock in the batch."""
        db, ids = ctx
        sub = ids.make(status="trialing", ends=PAST)
        with Session(engine) as a:
            claimed = admin_crud.claim_expired_trial(a, sub.id)
            admin_crud.expire_trial(a, claimed)
            a.commit()
        with Session(engine) as b:
            assert admin_crud.claim_expired_trial(b, sub.id) is None

    def test_two_concurrent_sweeps_expire_each_trial_exactly_once(self, ctx):
        """The real race: two threads through the real job against real Postgres."""
        import threading
        db, ids = ctx
        subs = [ids.make(status="trialing", ends=PAST) for _ in range(6)]
        org_ids = [s.org_id for s in subs]
        results, errors = [], []

        def run():
            try:
                with Session(engine) as s:
                    results.append(maintenance.expire_due_trials(s))
            except Exception as exc:                              # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"a concurrent sweep raised: {errors}"
        assert sum(r["expired"] for r in results) == 6, \
            "every trial expired exactly once across both workers"
        for s in subs:
            db.refresh(s)
            assert s.status == "trial_expired"
        # And exactly one transition row per subscription — the requirement the batch lock
        # alone could not hold.
        for org_id in org_ids:
            rows = db.scalars(select(AuditLog).where(
                AuditLog.org_id == org_id,
                AuditLog.action == "subscription.transition")).all()
            assert len(rows) == 1, f"expected one transition row, got {len(rows)}"

    def test_a_trial_that_converts_mid_sweep_is_left_to_the_checkout_path(self, ctx):
        """A tenant who starts paying between the candidate query and the claim is
        `conversion_pending` by the time we re-read, so the sweep must miss them rather than
        expire a subscription someone is in the middle of buying."""
        db, ids = ctx
        sub = ids.make(status="trialing", ends=PAST)
        candidates = [s.id for s in admin_crud.due_trial_expiries(db)]
        assert sub.id in candidates
        db.rollback()
        with Session(engine) as other:                    # the checkout webhook lands
            row = other.get(Subscription, sub.id)
            row.status = "conversion_pending"
            other.commit()
        assert admin_crud.claim_expired_trial(db, sub.id) is None
        db.rollback()
        db.refresh(sub)
        assert sub.status == "conversion_pending", "the purchase must survive the sweep"

    # ── 12 — audit evidence ─────────────────────────────────────────────────────────────
    def test_the_transition_is_audited_with_the_house_shape(self, ctx):
        db, ids = ctx
        sub = ids.make(status="trialing", ends=PAST)
        self._sweep(db)
        rows = db.scalars(select(AuditLog).where(
            AuditLog.org_id == sub.org_id,
            AuditLog.action == "subscription.transition")).all()
        assert len(rows) == 1
        meta = rows[0].meta or {}
        assert meta["from"] == "trialing" and meta["to"] == "trial_expired"
        assert meta["reason"] == "maintenance:trial_expiry"
        assert meta["trial_ends_at"], "the fact the decision rested on must be recorded"
        assert meta["expired_at"]
        assert meta["plan_id"] == str(ids.plan.id)
        assert "P2" in meta["entitlement_policy"], \
            "the audit row must say the entitlement question is still open"
        assert rows[0].target_id == str(sub.id)

    def test_the_legacy_spelling_is_recorded_as_it_was_stored(self, ctx):
        """`from` must say what the row actually held, not a normalised rewrite of it."""
        db, ids = ctx
        sub = ids.make(status="trial", ends=PAST)
        self._sweep(db)
        row = db.scalars(select(AuditLog).where(
            AuditLog.org_id == sub.org_id,
            AuditLog.action == "subscription.transition")).all()[0]
        assert (row.meta or {})["from"] == "trial"
        assert (row.meta or {})["to"] == "trial_expired"

    def test_trial_ends_at_survives_the_transition(self, ctx):
        """It is the evidence for why the row expired, not state to be tidied away."""
        db, ids = ctx
        sub = ids.make(status="trialing", ends=PAST)
        self._sweep(db)
        db.refresh(sub)
        assert sub.trial_ends_at is not None

    # ── 13 — idempotency ────────────────────────────────────────────────────────────────
    def test_re_running_the_sweep_produces_no_further_transitions(self, ctx):
        db, ids = ctx
        subs = [ids.make(status="trialing", ends=PAST) for _ in range(3)]
        first = self._sweep(db)
        second = self._sweep(db)
        third = self._sweep(db)
        assert first["expired"] == 3
        assert second["expired"] == 0 and third["expired"] == 0
        assert second["candidates"] == 0
        for s in subs:
            rows = db.scalars(select(AuditLog).where(
                AuditLog.org_id == s.org_id,
                AuditLog.action == "subscription.transition")).all()
            assert len(rows) == 1, "a re-run must not add a second transition row"

    # ── 14 — failing safely ─────────────────────────────────────────────────────────────
    def test_an_illegal_source_state_is_refused_and_audited_never_forced(self, ctx):
        db, ids = ctx
        sub = ids.make(status="active", ends=PAST)
        applied, error = admin_crud.expire_trial(db, sub)
        db.commit()
        db.refresh(sub)
        assert applied is False and error
        assert sub.status == "active", "a refused transition must not be applied"
        assert "subscription.transition_rejected" in self._actions(db, sub.org_id)
        assert "subscription.transition" not in self._actions(db, sub.org_id)

    def test_a_failure_in_the_expiry_job_is_reported_and_the_sweep_continues(self, ctx, monkeypatch):
        """A database failure mid-expiry must be named, not swallowed, and must not stop the
        other jobs — a scheduler needs to know "8 of 9 ran" and which one didn't."""
        db, ids = ctx

        def expire_due_trials(db, *, actor=None):          # same name, so `failed` names it
            raise RuntimeError("database went away")

        def still_runs(db, *, actor=None):
            return {"job": "still_runs", "ok": True}

        monkeypatch.setattr(maintenance, "JOBS", (expire_due_trials, still_runs))
        out = maintenance.run_all(db)

        assert out["failed"] == ["expire_due_trials"], \
            f"the failing job must be named in `failed`, got {out['failed']}"
        failing = [j for j in out["jobs"] if j.get("job") == "expire_due_trials"
                   or "error" in j]
        assert failing and "database went away" in failing[0]["error"], \
            "the cause must survive into the result, not be reduced to a bare 500"
        assert {"job": "still_runs", "ok": True} in out["jobs"], \
            "one job failing must not skip the rest of the sweep"

    def test_a_failed_expiry_leaves_the_subscription_untouched(self, ctx, monkeypatch):
        """Fail-safe direction: if the transition raises, the trial must still be a trial."""
        db, ids = ctx
        sub = ids.make(status="trialing", ends=PAST)

        def boom(db, s, *, actor=None, reason="x"):
            raise RuntimeError("commit failed")

        monkeypatch.setattr(admin_crud, "expire_trial", boom)
        with pytest.raises(RuntimeError):
            maintenance.expire_due_trials(db)
        db.rollback()
        db.refresh(sub)
        assert sub.status == "trialing", "a failed sweep must not half-apply a transition"

    def test_a_sweep_with_nothing_due_writes_nothing(self, ctx):
        db, ids = ctx
        ids.make(status="trialing", ends=FUTURE)
        before = db.scalar(select(text("count(*)")).select_from(AuditLog))
        out = self._sweep(db)
        assert out["expired"] == 0 and out["candidates"] == 0
        assert db.scalar(select(text("count(*)")).select_from(AuditLog)) == before


# ══════════════════════════════════════════════════════════════════════════════════════
# The P2 boundary — what expiry MEANS is still a Product decision
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestProductDecisionBoundaryP2:
    """These tests document the entitlement consequences of `trial_expired` AS THEY STAND.

    They are deliberately written as a record of an open question, not as an endorsement of the
    current behaviour. If Product answers P2 differently, these tests are the ones that must be
    changed — which is the point: the policy cannot drift in silently.
    """

    @pytest.fixture
    def expired(self):
        from app.services import org as org_svc
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            plan = Plan(name="P2Dev", slug=f"p2dev-{tag}", is_active=True, max_users=5,
                        max_storage_gb=50, max_streaming_hours=20)
            org = Organization(name=f"p2-{tag}")
            db.add_all([plan, org])
            db.flush()
            sub = Subscription(org_id=org.id, plan_id=plan.id, status="trialing", seats=1,
                               trial_ends_at=PAST, started_at=NOW - timedelta(days=15))
            db.add(sub)
            db.commit()
            db.refresh(sub)
            maintenance.expire_due_trials(db)
            db.refresh(sub)
            assert sub.status == "trial_expired", "fixture precondition"
            yield db, SimpleNamespace(org=org, plan=plan, sub=sub, org_svc=org_svc)
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM plans WHERE id=:p"), {"p": plan.id})
            db.commit()

    def test_expiry_does_not_make_the_tenant_unmetered(self, expired):
        """THE HAZARD THE TASK ASKS ABOUT, and it does NOT occur.

        `enforcement_plan` returns the subscription's existing plan for a non-entitled state, so
        the quantitative ceiling survives expiry. That is the already-approved F1 Part A
        decision, not a policy chosen here. Were this to regress to None, `seat_limit = None`
        would read as "no ceiling" and losing a trial would INCREASE capacity."""
        db, ids = expired
        plan = ids.org_svc.enforcement_plan(db, ids.org.id)
        assert plan is not None, \
            "REGRESSION: an expired trial resolved to no enforcement plan, which reads as " \
            "unlimited at both enforcement points"
        assert plan.id == ids.plan.id
        assert plan.max_storage_gb == 50 and plan.max_users == 5

    def test_expiry_removes_entitlement_and_that_is_all_it_does(self, expired):
        """The one behavioural consequence that IS decided: `trial_expired` is not in
        SUBSCRIPTION_ENTITLED_STATES, so the tenant is not entitled. Nothing beyond that."""
        db, ids = expired
        assert normalize_subscription_state(ids.sub.status) not in SUBSCRIPTION_ENTITLED_STATES

    def test_the_display_resolver_reports_no_plan_while_enforcement_keeps_one(self, expired):
        """The display/enforcement asymmetry is intentional and pinned elsewhere; recorded here
        because it is the behaviour a P2 answer will most likely want to revisit — the console
        shows an expired tenant no plan while their old ceiling still governs them."""
        db, ids = expired
        sub_row, plan_row = ids.org_svc._plan(db, ids.org.id)
        assert (sub_row, plan_row) == (None, None), "display: no entitled plan"
        assert ids.org_svc.enforcement_plan(db, ids.org.id) is not None, "enforcement: ceiling"

    def test_nothing_about_the_plan_or_the_row_changed(self, expired):
        """P2 is undecided, so expiry must not have pre-empted it by editing limits or the row."""
        db, ids = expired
        db.refresh(ids.sub)
        assert ids.sub.plan_id == ids.plan.id
        assert ids.sub.seats == 1
        assert ids.sub.cancelled_at is None, "expiry is not cancellation"
        assert ids.sub.trial_ends_at is not None
        assert ids.sub.stripe_subscription_id is None
        db.refresh(ids.plan)
        assert ids.plan.max_storage_gb == 50 and ids.plan.max_users == 5, \
            "a plan is shared by many tenants; expiry must never edit one"

    def test_the_open_question_is_named_in_the_code(self):
        """So the next person to read `expire_trial` learns the boundary from the source."""
        src = code_only(admin_crud.expire_trial)
        assert "P2" in src, "the undecided policy must be named where the transition is written"
