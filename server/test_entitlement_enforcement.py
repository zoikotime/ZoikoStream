"""Plan resolution separated from entitlement state (audit F1 Part A).

The defect: both enforcement points resolved the plan through an ENTITLED-ONLY query, so a
lapsed subscription resolved to plan=None and `None` was then read as "no ceiling". Losing
entitlement therefore INCREASED usable capacity — a tenant over quota became compliant by
having their trial expire, their subscription cancelled, or their organization suspended.

The two concepts are now distinct:

    plan        = the subscription's stored plan          -> org.enforcement_plan()
    entitlement = status in SUBSCRIPTION_ENTITLED_STATES  -> unchanged

Approved decision: an existing subscription's plan remains the QUANTITATIVE ceiling for storage
and seats even when its state is not entitled. That is not entitlement — no state was added to
SUBSCRIPTION_ENTITLED_STATES, and paid-feature access is a separate question these tests do not
touch.
"""
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.db import engine
from app.models import (SUBSCRIPTION_ENTITLED_STATES, Organization, Plan, Subscription, User)
# `org` FIRST, deliberately. services/org.py imports engagement_score from broadcast, while
# broadcast reaches org again through webhooks -> org_comms. That pre-existing cycle resolves
# only when org is the module that starts the import, which is the order the app itself uses.
from app.services import org as org_svc
from app.services import broadcast as bc

NON_ENTITLED = ("trial_expired", "canceled", "closed", "suspended")
ENTITLED = ("trialing", "active", "plan_change_scheduled")


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


# ── the separation, as invariants ─────────────────────────────────────────────────────────

def test_no_non_entitled_state_was_added_to_the_entitled_set():
    """The decision explicitly forbids this. Keeping the plan as a CEILING must not be
    implemented by making a lapsed subscription entitled."""
    for state in NON_ENTITLED:
        assert state not in SUBSCRIPTION_ENTITLED_STATES
    assert set(SUBSCRIPTION_ENTITLED_STATES) == {
        "active", "past_due", "plan_change_scheduled", "trial", "trialing"}


def test_the_display_resolver_still_filters_on_entitlement():
    """`_plan` is the REPORTING resolver and must keep returning nothing for a lapsed
    subscription — that null is deliberate (no invented denominator)."""
    src = code_only(org_svc._plan)
    assert "SUBSCRIPTION_ENTITLED_STATES" in src


def test_the_enforcement_resolver_does_not_filter_on_entitlement_alone():
    """It may PREFER an entitled row, but must fall back to the subscription that exists."""
    src = code_only(org_svc.enforcement_plan)
    assert "SUBSCRIPTION_ENTITLED_STATES" in src, "it prefers an entitled row when there is one"
    # ...and still resolves when there is not.
    assert src.count("select(Subscription)") >= 2, "a lapsed subscription must still resolve"


def test_enforcement_never_reads_the_reporting_payload():
    """Seat enforcement used to pull the 'Members' bar out of entitlements(). That is how the
    display convention `limit: None` became a permission grant."""
    from app.routers import organization
    src = code_only(organization.invite_member) if hasattr(organization, "invite_member") else ""
    if src:
        assert "entitlements(" not in src, "enforcement must not consume the reporting API"


# ── behaviour against real Postgres ───────────────────────────────────────────────────────

@needs_db
class TestEnforcementSeparation:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            dev = db.scalar(select(Plan).where(Plan.slug == "developer"))
            biz = db.scalar(select(Plan).where(Plan.slug == "business"))
            assert dev is not None and biz is not None
            made = []

            def org(name, *, storage=0.0, members=1, plan=None, state="trialing", sub=True):
                o = Organization(name=f"{name}-{tag}", storage_used_gb=storage)
                db.add(o); db.flush()
                for i in range(members):
                    db.add(User(org_id=o.id, full_name="U", email=f"{name}{i}-{tag}@t.test",
                                username=f"{name}{i}{tag}"[:40], password_hash="x",
                                role="org_admin", email_verified=True))
                if sub:
                    db.add(Subscription(org_id=o.id, plan_id=(plan or dev).id,
                                        status=state, seats=1))
                db.flush(); made.append(o.id)
                return o

            db.commit()
            yield db, dev, biz, org
            for oid in made:
                db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})
            db.commit()

    # 1/2/3 — entitled states are unchanged
    @pytest.mark.parametrize("state", ENTITLED)
    def test_an_entitled_subscription_resolves_and_enforces_its_plan(self, ctx, state):
        db, dev, _, mk = ctx
        o = mk(f"ent{state[:4]}", storage=60.0, state=state)
        db.commit()
        assert org_svc.enforcement_plan(db, o.id).id == dev.id
        assert org_svc._plan(db, o.id)[1].id == dev.id, "display unchanged for entitled states"
        assert bc._storage_over_limit(db, o.id) is True

    def test_a_scheduled_plan_change_keeps_the_CURRENT_plan_as_the_ceiling(self, ctx):
        """The pending plan must not become the ceiling early — Business's 500 GB must not
        apply while the tenant is still on Developer."""
        db, dev, biz, mk = ctx
        o = mk("sched", storage=60.0, state="plan_change_scheduled")
        sub = db.scalar(select(Subscription).where(Subscription.org_id == o.id))
        sub.pending_plan_id = biz.id
        db.commit()
        assert org_svc.enforcement_plan(db, o.id).id == dev.id, "still Developer's ceiling"
        assert bc._storage_over_limit(db, o.id) is True

    # 4/5/6/7 — THE FIX
    @pytest.mark.parametrize("state", NON_ENTITLED)
    def test_a_lapsed_subscription_keeps_its_plan_as_the_storage_ceiling(self, ctx, state):
        """Before F1 every one of these returned False — losing entitlement UNBLOCKED a tenant
        that was over quota."""
        db, dev, _, mk = ctx
        o = mk(f"lap{state[:4]}", storage=60.0, state=state)
        db.commit()
        assert org_svc.enforcement_plan(db, o.id).id == dev.id
        assert bc._storage_over_limit(db, o.id) is True, \
            f"{state} must not raise the storage ceiling"

    @pytest.mark.parametrize("state", NON_ENTITLED)
    def test_a_lapsed_subscription_remains_non_entitled(self, ctx, state):
        """Keeping the ceiling must NOT have granted entitlement."""
        db, _, _, mk = ctx
        o = mk(f"nent{state[:4]}", state=state)
        db.commit()
        assert state not in SUBSCRIPTION_ENTITLED_STATES
        assert org_svc._plan(db, o.id)[1] is None, "display still reports no entitled plan"
        ent = org_svc.entitlements(db, db.get(Organization, o.id))
        assert ent["plan_slug"] is None

    @pytest.mark.parametrize("state", NON_ENTITLED)
    def test_a_lapsed_subscription_keeps_its_seat_ceiling(self, ctx, state):
        """Developer allows 5 seats. A lapsed organization at 5 must not be able to invite a
        sixth — it previously could, without limit."""
        db, dev, _, mk = ctx
        o = mk(f"seat{state[:4]}", members=5, state=state)
        db.commit()
        plan = org_svc.enforcement_plan(db, o.id)
        used = db.scalar(select(func.count(User.id)).where(
            User.org_id == o.id, User.deleted_at.is_(None)))
        assert plan.max_users == 5 and used >= plan.max_users, \
            "the seat ceiling must still be reached, so the invite guard fires"

    def test_the_business_plan_keeps_its_own_larger_ceiling(self, ctx):
        """The ceiling is the subscription's OWN plan, not a fixed one."""
        db, _, biz, mk = ctx
        o = mk("bizlapse", storage=60.0, plan=biz, state="canceled")
        db.commit()
        assert org_svc.enforcement_plan(db, o.id).id == biz.id
        assert bc._storage_over_limit(db, o.id) is False, "60 GB is under Business's 500 GB"

    # 8 — no subscription row: unchanged, nothing fabricated
    def test_an_organization_with_no_subscription_gets_no_fabricated_plan(self, ctx):
        """DOCUMENTS AN OPEN GAP rather than closing it. What governs an organization that has
        never had a subscription is an unanswered Product question, so behaviour here is
        deliberately unchanged and no default plan is invented."""
        db, _, _, mk = ctx
        o = mk("nosub", storage=60.0, sub=False)
        db.commit()
        assert org_svc.enforcement_plan(db, o.id) is None, "no plan may be fabricated"
        assert bc._storage_over_limit(db, o.id) is False, \
            "unchanged pre-existing behaviour — post-subscription policy is undefined"

    # 9 — display/reporting semantics preserved
    def test_the_overview_payload_still_reports_no_invented_ceiling(self, ctx):
        """The reporting contract is untouched: a non-entitled org reports null limits, so no
        meaningless progress bar is rendered. This is what test_no_plan_means_no_invented_
        ceiling pins, and enforcement no longer depends on it."""
        db, _, _, mk = ctx
        o = mk("display", storage=60.0, state="canceled")
        db.commit()
        ent = org_svc.entitlements(db, db.get(Organization, o.id))
        assert ent["plan_slug"] is None and ent["plan"] is None
        for item in ent["items"]:
            assert item["limit"] is None and item["percent"] is None
        # ...while enforcement, asked separately, still has a ceiling.
        assert org_svc.enforcement_plan(db, o.id) is not None

    # 10 — the trial warning
    def test_the_trial_warning_fires_for_the_canonical_trialing_state(self, ctx):
        """It tested `== "trial"`, the pre-Section-12 spelling, while provisioning writes
        `trialing` — so the warning never fired for any subscription current code creates, and
        tenants got no notice before their trial ended."""
        from datetime import datetime, timedelta, timezone
        db, _, _, mk = ctx
        o = mk("trialwarn", state="trialing")
        sub = db.scalar(select(Subscription).where(Subscription.org_id == o.id))
        sub.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=3)
        db.commit()
        org_row = db.get(Organization, o.id)
        ent = org_svc.entitlements(db, org_row)
        items = org_svc.attention(db, org_row, ent, [])
        trial = [i for i in items if i.get("id") == "trial"]
        assert trial, "a trialing subscription must produce the trial-ending warning"
        assert trial[0]["severity"] == "critical", "3 days out is critical under the existing rule"

    def test_the_trial_warning_does_not_fire_without_an_end_date(self, ctx):
        """Unchanged condition: no date, no warning. The legacy NULL row stays silent."""
        db, _, _, mk = ctx
        o = mk("nowarn", state="trialing")
        db.commit()
        org_row = db.get(Organization, o.id)
        items = org_svc.attention(db, org_row, org_svc.entitlements(db, org_row), [])
        assert not [i for i in items if i.get("id") == "trial"]

    def test_the_trial_warning_still_accepts_the_legacy_spelling(self):
        """Legacy rows written as `trial` must keep working — normalizing matches both."""
        from app.models.subscription import normalize_subscription_state
        assert normalize_subscription_state("trial") == "trialing"
        assert normalize_subscription_state("trialing") == "trialing"
        src = code_only(org_svc)
        assert 'ent.get("status") == "trial"' not in src, "the literal comparison must be gone"

    # 11 — cross-organization isolation
    def test_one_organizations_plan_never_governs_another(self, ctx):
        db, dev, biz, mk = ctx
        a = mk("isoA", storage=60.0, plan=dev, state="canceled")
        b = mk("isoB", storage=60.0, plan=biz, state="canceled")
        db.commit()
        assert org_svc.enforcement_plan(db, a.id).id == dev.id
        assert org_svc.enforcement_plan(db, b.id).id == biz.id
        # Developer's 50 GB blocks A; Business's 500 GB does not block B.
        assert bc._storage_over_limit(db, a.id) is True
        assert bc._storage_over_limit(db, b.id) is False

    def test_an_entitled_subscription_wins_over_an_older_lapsed_one(self, ctx):
        """Row selection only decides WHICH subscription to read: a stale cancelled row must
        never override a current live one."""
        from datetime import datetime, timedelta, timezone
        db, dev, biz, mk = ctx
        o = mk("twosubs", storage=60.0, plan=dev, state="canceled")
        old = db.scalar(select(Subscription).where(Subscription.org_id == o.id))
        old.started_at = datetime.now(timezone.utc) - timedelta(days=90)
        db.add(Subscription(org_id=o.id, plan_id=biz.id, status="active", seats=1,
                            started_at=datetime.now(timezone.utc)))
        db.commit()
        assert org_svc.enforcement_plan(db, o.id).id == biz.id, \
            "the live Business subscription governs, not the old cancelled Developer one"
