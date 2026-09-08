"""Initial subscription provisioning — approved Product decisions.

New organizations receive Developer / `trialing` / 14 days, with no card and no Stripe call.
Existing subscription-less organizations are backfilled on the same terms, subject to the
approved eligibility rules.

Nothing here asserts a price: the amount lives on the Stripe Price. What IS asserted is that
provisioning never touches Stripe at all.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.config import TRIAL_DAYS
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


# ── the approved rules, as pure assertions ────────────────────────────────────────────────

def test_the_default_plan_is_developer():
    assert admin_crud.INITIAL_PLAN_SLUG == "developer"


def test_the_trial_is_fourteen_days():
    assert TRIAL_DAYS == 14


def test_provisioning_never_touches_stripe():
    """No card is required and no charge may be raised, so this path must not reach Stripe at
    all — not to create a customer, not a subscription, not a checkout."""
    src = code_only(admin_crud.provision_initial_subscription)
    # Real USAGE surfaces, not the bare word: the audit meta this function writes deliberately
    # records "no card required, no charge, no Stripe subscription", and a word-scan would trip
    # on the very string that documents the guarantee.
    for token in ("StripeClient", "stripe.", "payments_stripe", "get_provider",
                  "checkout.sessions", "price_id=", "resolve_subscription_price_id",
                  "create_subscription_checkout_session"):
        assert token not in src, f"provisioning must not use {token!r}"


def test_provisioning_does_not_commit():
    """It must join the caller's transaction so an organization can never be committed with a
    half-written subscription beside it."""
    src = code_only(admin_crud.provision_initial_subscription)
    assert "db.commit()" not in src
    assert "create_audit_log" not in src, \
        "create_audit_log commits internally, which would break the atomicity guarantee"


def test_registration_provisions_inside_the_same_transaction():
    """Structural: the provisioning call must precede registration's commit, or the org and
    its subscription could be persisted separately."""
    from app.routers import auth
    src = code_only(auth.register)
    assert "provision_initial_subscription" in src
    assert src.index("provision_initial_subscription") < src.rindex("db.commit()")


def test_a_trial_cannot_auto_convert_to_paid():
    """Decision 6: reaching the trial end must never charge anyone. Section 12 enforces it
    structurally — `trialing` has no edge to `active`."""
    from app.models.subscription import SUBSCRIPTION_TRANSITIONS
    assert "active" not in SUBSCRIPTION_TRANSITIONS["trialing"]
    assert set(SUBSCRIPTION_TRANSITIONS["trialing"]) == {"conversion_pending", "trial_expired"}


# ── provisioning against real Postgres ────────────────────────────────────────────────────

@needs_db
class TestProvisioning:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            dev = db.scalar(select(Plan).where(Plan.slug == "developer"))
            assert dev is not None, "the developer plan must exist in the test catalog"
            made = []

            def org(name, *, status="active", verified=True, with_user=True):
                o = Organization(name=f"{name}-{tag}", status=status)
                db.add(o); db.flush()
                if with_user:
                    u = User(org_id=o.id, full_name="U", email=f"{name}-{tag}@t.test",
                             username=f"{name}{tag}"[:40], password_hash="x", role="org_admin",
                             email_verified=verified)
                    db.add(u)
                db.flush()
                made.append(o.id)
                return o

            db.commit()
            yield db, dev, org, made
            for oid in made:
                db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})
            db.commit()

    def test_an_organization_is_provisioned_on_developer_trialing(self, ctx):
        db, dev, mk, _ = ctx
        o = mk("prov")
        sub = admin_crud.provision_initial_subscription(db, o)
        db.commit()
        assert sub is not None
        assert sub.org_id == o.id
        assert sub.plan_id == dev.id
        assert sub.status == "trialing"

    def test_the_trial_window_is_exactly_fourteen_days(self, ctx):
        db, _, mk, _ = ctx
        o = mk("window")
        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        sub = admin_crud.provision_initial_subscription(db, o, now=now)
        db.commit()
        assert sub.started_at == now
        assert sub.trial_ends_at == now + timedelta(days=14)
        assert (sub.trial_ends_at - sub.started_at).days == TRIAL_DAYS

    def test_the_trial_end_is_never_left_null(self, ctx):
        """The legacy defect: a NULL end means a trial nothing can ever expire."""
        db, _, mk, _ = ctx
        o = mk("notnull")
        sub = admin_crud.provision_initial_subscription(db, o)
        db.commit()
        assert sub.trial_ends_at is not None

    def test_no_stripe_reference_is_written(self, ctx):
        db, _, mk, _ = ctx
        o = mk("nostripe")
        sub = admin_crud.provision_initial_subscription(db, o)
        db.commit()
        assert sub.stripe_customer_id is None
        assert sub.stripe_subscription_id is None
        assert sub.checkout_session_ref is None
        assert sub.billing_interval is None, "no cadence is purchased by a trial"

    def test_provisioning_is_audited(self, ctx):
        db, _, mk, _ = ctx
        o = mk("audited")
        admin_crud.provision_initial_subscription(db, o)
        db.commit()
        rows = [a for a in db.scalars(select(AuditLog).where(AuditLog.org_id == o.id)).all()
                if a.action == "subscription.provisioned"]
        assert len(rows) == 1
        assert rows[0].meta["plan_slug"] == "developer"
        assert rows[0].meta["trial_days"] == TRIAL_DAYS

    def test_provisioning_twice_does_not_duplicate(self, ctx):
        db, _, mk, _ = ctx
        o = mk("dupe")
        first = admin_crud.provision_initial_subscription(db, o)
        db.commit()
        second = admin_crud.provision_initial_subscription(db, o)
        db.commit()
        assert first is not None and second is None
        assert db.scalar(select(func.count(Subscription.id))
                         .where(Subscription.org_id == o.id)) == 1

    def test_an_existing_subscription_is_never_replaced(self, ctx):
        """Whatever its state — a paid, canceled or scheduled subscription must survive."""
        db, dev, mk, _ = ctx
        biz = db.scalar(select(Plan).where(Plan.slug == "business"))
        for state in ("active", "canceled", "past_due", "plan_change_scheduled"):
            o = mk(f"keep{state[:4]}")
            db.add(Subscription(org_id=o.id, plan_id=biz.id, status=state, seats=1))
            db.commit()
            assert admin_crud.provision_initial_subscription(db, o) is None
            db.commit()
            kept = db.scalar(select(Subscription).where(Subscription.org_id == o.id))
            assert kept.plan_id == biz.id and kept.status == state

    def test_the_subscription_belongs_to_the_right_organization(self, ctx):
        """Multi-tenancy: org_id comes from the row just created, never from any input."""
        db, _, mk, _ = ctx
        a, b = mk("tenanta"), mk("tenantb")
        sub_a = admin_crud.provision_initial_subscription(db, a)
        db.commit()
        assert sub_a.org_id == a.id != b.id
        assert db.scalar(select(func.count(Subscription.id))
                         .where(Subscription.org_id == b.id)) == 0
        src = code_only(admin_crud.provision_initial_subscription)
        assert "org.id" in src and "data.org_id" not in src

    def test_a_missing_plan_fails_soft_rather_than_breaking_signup(self, ctx):
        """Deliberate: raising here would make registration depend on the catalog already
        carrying the Section 03 slugs, so deploying before migrate_plan_names.py would break
        every signup. It degrades to the old behaviour and logs instead."""
        db, _, mk, _ = ctx
        o = mk("noplan")
        assert admin_crud.provision_initial_subscription(db, o, plan_slug="no-such-plan") is None
        db.commit()
        assert db.scalar(select(func.count(Subscription.id))
                         .where(Subscription.org_id == o.id)) == 0

    # ── migration eligibility ────────────────────────────────────────────────────────────

    def test_an_eligible_organization_is_selected(self, ctx):
        from migrate_provision_subscriptions import eligible_org_ids
        db, _, mk, _ = ctx
        o = mk("eligible")
        db.commit()
        assert o.id in eligible_org_ids(db)

    def test_a_user_less_organization_is_excluded(self, ctx):
        """Provisioning a trial for an abandoned registration starts a clock nobody asked for."""
        from migrate_provision_subscriptions import eligible_org_ids
        db, _, mk, _ = ctx
        o = mk("nouser", with_user=False)
        db.commit()
        assert o.id not in eligible_org_ids(db)

    def test_an_unverified_organization_is_excluded(self, ctx):
        from migrate_provision_subscriptions import eligible_org_ids
        db, _, mk, _ = ctx
        o = mk("unverif", verified=False)
        db.commit()
        assert o.id not in eligible_org_ids(db)

    def test_a_suspended_but_verified_organization_is_included(self, ctx):
        """Approved rule: billing state and operational state are orthogonal."""
        from migrate_provision_subscriptions import eligible_org_ids
        db, _, mk, _ = ctx
        o = mk("susp", status="suspended")
        db.commit()
        assert o.id in eligible_org_ids(db)

    def test_an_organization_that_already_has_a_subscription_is_excluded(self, ctx):
        from migrate_provision_subscriptions import eligible_org_ids
        db, dev, mk, _ = ctx
        o = mk("hassub")
        db.add(Subscription(org_id=o.id, plan_id=dev.id, status="trialing", seats=1))
        db.commit()
        assert o.id not in eligible_org_ids(db)

    def test_the_legacy_null_trial_end_is_reported_and_never_modified(self, ctx):
        """Decision 8: no deterministic rule exists for what its trial end should have been, so
        it is surfaced for manual review and left exactly as it is."""
        from migrate_provision_subscriptions import (migrate_provision_subscriptions,
                                                      report_legacy_subscriptions)
        db, dev, mk, _ = ctx
        o = mk("legacy")
        db.add(Subscription(org_id=o.id, plan_id=dev.id, status="trialing", seats=1,
                            trial_ends_at=None))
        db.commit()
        assert any(r[1] == o.id for r in report_legacy_subscriptions(db))
        migrate_provision_subscriptions(dry_run=False, batch_size=50)
        db.expire_all()
        kept = db.scalar(select(Subscription).where(Subscription.org_id == o.id))
        assert kept.trial_ends_at is None, "the legacy row must not be silently backfilled"
        assert kept.status == "trialing"

    def test_the_migration_is_idempotent(self, ctx):
        from migrate_provision_subscriptions import migrate_provision_subscriptions
        db, _, mk, _ = ctx
        mk("idem1"); mk("idem2")
        db.commit()
        first = migrate_provision_subscriptions(dry_run=False, batch_size=50)
        second = migrate_provision_subscriptions(dry_run=False, batch_size=50)
        assert first["provisioned"] >= 2
        assert second["provisioned"] == 0, "a second run must provision nothing"

    def test_a_dry_run_changes_nothing(self, ctx):
        from migrate_provision_subscriptions import migrate_provision_subscriptions
        db, _, mk, _ = ctx
        o = mk("dry")
        db.commit()
        before = db.scalar(select(func.count(Subscription.id)))
        result = migrate_provision_subscriptions(dry_run=True, batch_size=50)
        db.expire_all()
        assert result["provisioned"] >= 1, "it must still report what it would do"
        assert db.scalar(select(func.count(Subscription.id))) == before
        assert db.scalar(select(func.count(Subscription.id))
                         .where(Subscription.org_id == o.id)) == 0

    def test_batching_covers_every_eligible_organization(self, ctx):
        from migrate_provision_subscriptions import eligible_org_ids, migrate_provision_subscriptions
        db, _, mk, _ = ctx
        for i in range(5):
            mk(f"batch{i}")
        db.commit()
        migrate_provision_subscriptions(dry_run=False, batch_size=2)
        db.expire_all()
        assert eligible_org_ids(db) == [], "nothing eligible may remain after a full run"

    def test_the_migration_refuses_without_the_developer_plan(self, ctx, monkeypatch):
        """Fails closed rather than provisioning onto some other plan."""
        import migrate_provision_subscriptions as mig
        db, _, mk, _ = ctx
        monkeypatch.setattr(mig, "INITIAL_PLAN_SLUG", "definitely-not-a-plan")
        result = mig.migrate_provision_subscriptions(dry_run=True)
        assert result["refused"] is True and result["provisioned"] == 0

    def test_the_migration_makes_no_stripe_call(self):
        import migrate_provision_subscriptions as mig
        src = code_only(mig)
        # Usage surfaces only — the script's own progress line says "No card, no charge, no
        # Stripe", which is the guarantee, not a violation of it.
        for token in ("StripeClient", "stripe.", "payments_stripe", "get_provider",
                      "checkout.sessions", "import stripe"):
            assert token not in src, f"the migration must not use {token!r}"
