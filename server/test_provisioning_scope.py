"""P3 — provisioning scope: explicit, Product-supplied exclusions.

THE PROBLEM. `eligible_org_ids()` had three predicates and no exclusion mechanism, so the only
way to narrow a production run was to edit the query before executing it. The production audit
found 2 055 eligible organizations, 70 of which are super-admin-owned and look internal — and
`organizations.is_test` is `false` for all 2 157 rows, so it cannot separate them.

WHAT THIS DOES NOT DO, and these tests are how that stays true: it makes no Product decision.
It does not infer "staff" from a name, does not auto-exclude a super-admin-owned organization,
and does not consult `is_test`. Scope arrives as an explicit list of ids or it does not arrive
at all.

Every assertion here is written against DELTAS and against membership of this test's own
fixtures, never against absolute totals — the shared test database carries other suites' rows,
and a test that asserted `total_organizations == 2157` would be asserting the fixture data of
whatever ran before it.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import migrate_provision_subscriptions as mig
from _testsupport import code_only
from app.db import engine
from app.models import Organization, Plan, Subscription, User


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


# ══════════════════════════════════════════════════════════════════════════════════════
# Exclusion input parsing — no database
# ══════════════════════════════════════════════════════════════════════════════════════

class TestLoadExclusions:
    def test_no_input_is_an_empty_set_not_an_error(self):
        assert mig.load_exclusions() == set()
        assert mig.load_exclusions(paths=[], org_ids=[]) == set()

    def test_a_single_org_id_argument(self):
        one = uuid.uuid4()
        assert mig.load_exclusions(org_ids=[str(one)]) == {one}

    def test_repeated_org_id_arguments_are_unioned(self):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        assert mig.load_exclusions(org_ids=[str(a), str(b), str(c)]) == {a, b, c}

    def test_duplicate_ids_collapse_without_complaint(self):
        """A list assembled from two sources will repeat itself. That is not an error — the
        same organization excluded twice is excluded once."""
        one = uuid.uuid4()
        assert mig.load_exclusions(org_ids=[str(one), str(one), str(one).upper()]) == {one}

    def test_a_file_is_read_with_comments_and_blank_lines_ignored(self, tmp_path):
        a, b = uuid.uuid4(), uuid.uuid4()
        f = tmp_path / "p3.txt"
        f.write_text(
            "# P3 exclusions approved by Product, 2026-09-08\n"
            "\n"
            f"{a}   # internal: platform staff org\n"
            "   \n"
            f"  {b}\t# internal\n"
            "# trailing comment only\n",
            encoding="utf-8",
        )
        assert mig.load_exclusions(paths=[str(f)]) == {a, b}

    def test_a_file_and_arguments_are_unioned(self, tmp_path):
        a, b = uuid.uuid4(), uuid.uuid4()
        f = tmp_path / "p3.txt"
        f.write_text(f"{a}\n", encoding="utf-8")
        assert mig.load_exclusions(paths=[str(f)], org_ids=[str(b)]) == {a, b}

    def test_several_files_are_unioned(self, tmp_path):
        a, b = uuid.uuid4(), uuid.uuid4()
        (tmp_path / "one.txt").write_text(f"{a}\n", encoding="utf-8")
        (tmp_path / "two.txt").write_text(f"{b}\n", encoding="utf-8")
        got = mig.load_exclusions(
            paths=[str(tmp_path / "one.txt"), str(tmp_path / "two.txt")])
        assert got == {a, b}

    def test_an_invalid_uuid_refuses_the_whole_run(self):
        """Fail closed. Dropping a malformed line would provision an organization Product
        explicitly asked to exclude, which is the one outcome this must never produce."""
        with pytest.raises(mig.ExclusionInputError) as exc:
            mig.load_exclusions(org_ids=["not-a-uuid"])
        assert "not a valid organization UUID" in str(exc.value)

    def test_every_malformed_entry_is_named_in_one_pass(self, tmp_path):
        """One run tells the operator about all of them, rather than one per attempt."""
        good = uuid.uuid4()
        f = tmp_path / "p3.txt"
        f.write_text(f"{good}\nnope\n12345\n", encoding="utf-8")
        with pytest.raises(mig.ExclusionInputError) as exc:
            mig.load_exclusions(paths=[str(f)], org_ids=["also-bad"])
        message = str(exc.value)
        assert "2 malformed" in message or "3 malformed" in message
        assert "'nope'" in message and "'12345'" in message and "'also-bad'" in message
        # And the line numbers, so the operator can find them.
        assert f"{f}:2" in message and f"{f}:3" in message

    def test_an_unreadable_file_refuses_rather_than_running_with_no_exclusions(self, tmp_path):
        missing = tmp_path / "does-not-exist.txt"
        with pytest.raises(mig.ExclusionInputError) as exc:
            mig.load_exclusions(paths=[str(missing)])
        assert "cannot read exclusion file" in str(exc.value)


# ══════════════════════════════════════════════════════════════════════════════════════
# No inference — structural
# ══════════════════════════════════════════════════════════════════════════════════════

def test_scope_is_never_inferred_from_names_or_flags():
    """The mechanism must be explicit. Any of these appearing in the selection code would mean
    the script had started deciding P3 for itself."""
    src = "\n".join(code_only(fn) for fn in (
        mig.eligible_org_ids, mig.provisioning_scope, mig._eligibility_predicates,
        mig.migrate_provision_subscriptions, mig.load_exclusions))
    for forbidden in ("is_test", "super_admin", "platform_admin", "Zoiko", "staff",
                      "ilike", "startswith", "endswith", "Staff"):
        assert forbidden not in src, (
            f"{forbidden!r} appears in the selection path — scope must be supplied by Product, "
            "never inferred"
        )


def test_the_eligibility_rules_themselves_are_unchanged_by_p3():
    """P3 adds a filter on top of eligibility; it must not redefine eligibility."""
    src = code_only(mig._eligibility_predicates)
    assert "email_verified" in src, "the verified-user rule must remain"
    assert "Subscription.org_id" in src, "the no-subscription rule must remain"
    assert "status" not in src, \
        "eligibility must stay status-agnostic — whether suspended orgs are in scope is P3"


def test_exclusions_are_applied_before_the_limit():
    """Filtering after the limit would silently shrink a staged rollout: `--limit 100` would
    provision fewer than 100 and the operator would have no way to see why."""
    src = code_only(mig.eligible_org_ids)
    exclude_at = src.index("notin_")
    limit_at = src.index(".limit(")
    assert exclude_at < limit_at, "the exclusion filter must be applied before .limit()"


# ══════════════════════════════════════════════════════════════════════════════════════
# Behaviour against real Postgres
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestProvisioningScope:
    @pytest.fixture
    def ctx(self):
        """Five organizations covering every case, plus a handle on the rest of the database.

        `others` is every OTHER currently-eligible organization. Passing it as an exclusion is
        how the full-function tests below stay hermetic in a shared test database — and it
        exercises the mechanism at a realistic size while doing so.
        """
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            plan = db.scalar(select(Plan).where(Plan.slug == mig.INITIAL_PLAN_SLUG))
            created_plan = None
            if plan is None:
                plan = created_plan = Plan(name="Developer", slug=mig.INITIAL_PLAN_SLUG,
                                           is_active=True, max_users=5, max_storage_gb=50)
                db.add(plan)
                db.flush()

            made: list = []

            def org(name, *, status="active", verified=True, with_user=True,
                    role="org_admin", subscription=False):
                o = Organization(name=f"{name}-{tag}", status=status)
                db.add(o)
                db.flush()
                if with_user:
                    db.add(User(email=f"{name}-{tag}@example.com", full_name=name,
                                username=f"{name}-{tag}", password_hash="x", role=role,
                                org_id=o.id, email_verified=verified))
                if subscription:
                    db.add(Subscription(org_id=o.id, plan_id=plan.id, status="trialing",
                                        seats=1,
                                        trial_ends_at=datetime.now(timezone.utc)
                                        + timedelta(days=14)))
                db.flush()
                made.append(o)
                return o

            eligible_a = org("elga")
            eligible_b = org("elgb")
            suspended = org("susp", status="suspended")
            # Named and owned like an internal org. Eligible unless Product says otherwise.
            staff_like = org("Zoiko Staff", role="super_admin")
            already = org("done", subscription=True)
            unverified = org("unve", verified=False)
            userless = org("none", with_user=False)
            db.commit()

            mine = {eligible_a.id, eligible_b.id, suspended.id, staff_like.id}
            others = set(mig.eligible_org_ids(db)) - mine

            yield db, SimpleNamespace(
                tag=tag, plan=plan, mine=mine, others=others,
                eligible_a=eligible_a, eligible_b=eligible_b, suspended=suspended,
                staff_like=staff_like, already=already, unverified=unverified,
                userless=userless)

            for o in made:
                db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": o.id})
                db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": o.id})
                db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": o.id})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o.id})
            if created_plan is not None:
                db.execute(text("DELETE FROM plans WHERE id=:p"), {"p": created_plan.id})
            db.commit()

    # ── eligibility is unchanged, and nothing is inferred ───────────────────────────────
    def test_with_no_exclusions_every_qualifying_org_is_in_scope(self, ctx):
        db, ids = ctx
        eligible = set(mig.eligible_org_ids(db))
        assert ids.mine <= eligible, "all four qualifying fixtures must be eligible"
        assert ids.already.id not in eligible, "already provisioned"
        assert ids.unverified.id not in eligible, "no verified user"
        assert ids.userless.id not in eligible, "no users at all"

    def test_a_staff_looking_org_is_not_excluded_automatically(self, ctx):
        """The core P3 guarantee. This organization is named `Zoiko Staff …` and its only user
        is a super_admin — and it is still eligible, because nothing here infers scope."""
        db, ids = ctx
        assert ids.staff_like.id in set(mig.eligible_org_ids(db))

    def test_a_suspended_org_is_not_excluded_automatically(self, ctx):
        db, ids = ctx
        assert ids.suspended.id in set(mig.eligible_org_ids(db))

    # ── the exclusion filter ────────────────────────────────────────────────────────────
    def test_one_excluded_org_is_removed_and_the_rest_remain(self, ctx):
        db, ids = ctx
        eligible = set(mig.eligible_org_ids(db, exclude={ids.eligible_a.id}))
        assert ids.eligible_a.id not in eligible
        assert {ids.eligible_b.id, ids.suspended.id, ids.staff_like.id} <= eligible

    def test_multiple_excluded_orgs_are_all_removed(self, ctx):
        db, ids = ctx
        excluded = {ids.eligible_a.id, ids.staff_like.id, ids.suspended.id}
        eligible = set(mig.eligible_org_ids(db, exclude=excluded))
        assert not (excluded & eligible)
        assert ids.eligible_b.id in eligible

    def test_a_suspended_org_can_be_explicitly_excluded(self, ctx):
        db, ids = ctx
        assert ids.suspended.id not in set(
            mig.eligible_org_ids(db, exclude={ids.suspended.id}))

    def test_a_staff_looking_org_can_be_explicitly_excluded(self, ctx):
        db, ids = ctx
        assert ids.staff_like.id not in set(
            mig.eligible_org_ids(db, exclude={ids.staff_like.id}))

    def test_an_empty_exclusion_set_changes_nothing(self, ctx):
        db, ids = ctx
        assert set(mig.eligible_org_ids(db)) == set(mig.eligible_org_ids(db, exclude=set()))

    def test_excluding_an_already_provisioned_org_is_harmless(self, ctx):
        db, ids = ctx
        before = set(mig.eligible_org_ids(db))
        after = set(mig.eligible_org_ids(db, exclude={ids.already.id}))
        assert before == after, "it was never eligible, so excluding it removes nothing"

    def test_exclusions_are_applied_before_the_limit_in_practice(self, ctx):
        db, ids = ctx
        full = mig.eligible_org_ids(db, exclude=ids.others)
        assert len(full) == 4
        limited = mig.eligible_org_ids(db, exclude=ids.others | {full[0]}, limit=3)
        assert len(limited) == 3, "the limit must be filled AFTER exclusions, not before"
        assert full[0] not in limited

    # ── the scope report ────────────────────────────────────────────────────────────────
    def test_the_report_accounts_for_every_supplied_exclusion(self, ctx):
        """Four buckets, and every supplied id lands in exactly one. Nothing is dropped."""
        db, ids = ctx
        ghost = uuid.uuid4()
        supplied = {ids.eligible_a.id, ids.already.id, ids.unverified.id, ghost}
        scope = mig.provisioning_scope(db, exclude=supplied)

        assert scope["exclusions_supplied"] == 4
        assert str(ids.eligible_a.id) in scope["excluded_from_eligible"]
        assert str(ids.already.id) in scope["excluded_already_provisioned"]
        assert str(ids.unverified.id) in scope["excluded_not_eligible"]
        assert str(ghost) in scope["excluded_unknown_org_ids"]

        accounted = (len(scope["excluded_from_eligible"])
                     + len(scope["excluded_already_provisioned"])
                     + len(scope["excluded_not_eligible"])
                     + len(scope["excluded_unknown_org_ids"]))
        assert accounted == scope["exclusions_supplied"], \
            "every supplied exclusion must appear in exactly one bucket"

    def test_the_report_counts_the_final_population_and_its_statuses(self, ctx):
        db, ids = ctx
        scope = mig.provisioning_scope(db, exclude=ids.others)
        assert scope["eligible_before_exclusions"] >= 4
        assert scope["final_provisioning_count"] == 4
        assert scope["final_active"] == 3, "eligible_a, eligible_b, staff_like"
        assert scope["final_suspended"] == 1

    def test_excluding_the_suspended_org_moves_the_suspended_count(self, ctx):
        db, ids = ctx
        scope = mig.provisioning_scope(db, exclude=ids.others | {ids.suspended.id})
        assert scope["final_provisioning_count"] == 3
        assert scope["final_suspended"] == 0
        assert scope["final_active"] == 3

    def test_the_report_explains_why_the_ineligible_are_ineligible(self, ctx):
        db, ids = ctx
        baseline = mig.provisioning_scope(db)
        assert baseline["ineligible_no_users"] >= 1, "the user-less fixture must be counted"
        assert baseline["ineligible_users_none_verified"] >= 1, "the unverified fixture too"
        assert baseline["already_provisioned"] >= 1

    def test_the_report_reflects_the_limit(self, ctx):
        db, ids = ctx
        scope = mig.provisioning_scope(db, exclude=ids.others, limit=2)
        assert scope["limit_applied"] == 2
        assert scope["final_provisioning_count"] == 2

    def test_the_report_writes_nothing(self, ctx):
        db, ids = ctx
        from sqlalchemy import func
        before = db.scalar(select(func.count(Subscription.id)))
        mig.provisioning_scope(db, exclude={ids.eligible_a.id})
        assert db.scalar(select(func.count(Subscription.id))) == before

    def test_print_scope_names_every_excluded_id(self, ctx, capsys):
        """'Never silently exclude' means the ids are printed, not just counted."""
        db, ids = ctx
        scope = mig.provisioning_scope(db, exclude={ids.eligible_a.id, ids.staff_like.id})
        mig.print_scope(scope)
        out = capsys.readouterr().out
        assert str(ids.eligible_a.id) in out
        assert str(ids.staff_like.id) in out
        for label in ("Total organizations considered", "ELIGIBLE before exclusions",
                      "Exclusions supplied by Product", "FINAL provisioning count",
                      "active", "suspended"):
            assert label in out


# ══════════════════════════════════════════════════════════════════════════════════════
# The whole migration, dry-run and real, scoped to this test's own fixtures
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestMigrationWithExclusions:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            plan = db.scalar(select(Plan).where(Plan.slug == mig.INITIAL_PLAN_SLUG))
            created_plan = None
            if plan is None:
                plan = created_plan = Plan(name="Developer", slug=mig.INITIAL_PLAN_SLUG,
                                           is_active=True, max_users=5, max_storage_gb=50)
                db.add(plan)
                db.flush()
            made = []
            for n in range(3):
                o = Organization(name=f"mig{n}-{tag}", status="active")
                db.add(o)
                db.flush()
                db.add(User(email=f"mig{n}-{tag}@example.com", full_name=f"mig{n}",
                            username=f"mig{n}-{tag}", password_hash="x", role="org_admin",
                            org_id=o.id, email_verified=True))
                made.append(o)
            db.commit()
            mine = [o.id for o in made]
            others = set(mig.eligible_org_ids(db)) - set(mine)
            yield db, SimpleNamespace(tag=tag, mine=mine, others=others, plan=plan)
            for oid in mine:
                db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": oid})
            if created_plan is not None:
                db.execute(text("DELETE FROM plans WHERE id=:p"), {"p": created_plan.id})
            db.commit()

    def _subs(self, db, org_ids):
        return {s.org_id for s in db.scalars(
            select(Subscription).where(Subscription.org_id.in_(org_ids))).all()}

    def test_a_dry_run_reports_the_scope_and_writes_nothing(self, ctx, capsys):
        db, ids = ctx
        excluded = ids.mine[0]
        result = mig.migrate_provision_subscriptions(
            dry_run=True, exclude=ids.others | {excluded})
        out = capsys.readouterr().out

        assert result["refused"] is False
        assert result["eligible"] == 2, "3 fixtures minus 1 explicit exclusion"
        assert result["provisioned"] == 2
        assert "DRY RUN" in out and str(excluded) in out
        assert result["scope"]["final_provisioning_count"] == 2
        assert self._subs(db, ids.mine) == set(), "a dry run must write nothing at all"

    def test_an_excluded_org_is_never_provisioned_and_stays_untouched(self, ctx):
        db, ids = ctx
        excluded, kept = ids.mine[0], ids.mine[1:]
        before = db.get(Organization, excluded)
        name_before, status_before = before.name, before.status

        result = mig.migrate_provision_subscriptions(exclude=ids.others | {excluded})
        assert result["provisioned"] == 2

        got = self._subs(db, ids.mine)
        assert excluded not in got, "an excluded organization must not be provisioned"
        assert set(kept) == got
        after = db.get(Organization, excluded)
        db.refresh(after)
        assert (after.name, after.status) == (name_before, status_before), \
            "exclusion must not modify the organization in any way"

    def test_an_unknown_exclusion_id_refuses_and_writes_nothing(self, ctx):
        """A typo'd id would otherwise provision a tenant Product excluded."""
        db, ids = ctx
        ghost = uuid.uuid4()
        result = mig.migrate_provision_subscriptions(exclude=ids.others | {ghost})
        assert result["refused"] is True
        assert str(ghost) in result["scope"]["excluded_unknown_org_ids"]
        assert self._subs(db, ids.mine) == set(), "a refusal must provision nothing"

    def test_the_real_run_remains_idempotent(self, ctx):
        db, ids = ctx
        first = mig.migrate_provision_subscriptions(exclude=ids.others)
        assert first["provisioned"] == 3

        second = mig.migrate_provision_subscriptions(exclude=ids.others)
        assert second["provisioned"] == 0, "a second run must provision nothing"
        assert second["eligible"] == 0, "they are no longer eligible — they have subscriptions"

        rows = db.scalars(select(Subscription).where(
            Subscription.org_id.in_(ids.mine))).all()
        assert len(rows) == 3, "exactly one subscription per organization"

    def test_existing_subscriptions_are_not_modified_by_a_later_run(self, ctx):
        db, ids = ctx
        mig.migrate_provision_subscriptions(exclude=ids.others)
        rows = db.scalars(select(Subscription).where(
            Subscription.org_id.in_(ids.mine))).all()
        snapshot = {r.org_id: (r.status, r.plan_id, r.trial_ends_at, r.started_at)
                    for r in rows}

        mig.migrate_provision_subscriptions(exclude=ids.others)
        for r in db.scalars(select(Subscription).where(
                Subscription.org_id.in_(ids.mine))).all():
            db.refresh(r)
            assert (r.status, r.plan_id, r.trial_ends_at, r.started_at) == snapshot[r.org_id]

    def test_excluding_everything_provisions_nothing(self, ctx):
        db, ids = ctx
        result = mig.migrate_provision_subscriptions(
            exclude=ids.others | set(ids.mine))
        assert result["provisioned"] == 0 and result["eligible"] == 0
        assert result["refused"] is False, "an empty final population is not an error"
        assert self._subs(db, ids.mine) == set()

    def test_duplicate_exclusions_behave_as_one(self, ctx, tmp_path):
        """End to end through the parser, since that is where duplicates arrive."""
        db, ids = ctx
        excluded = ids.mine[0]
        f = tmp_path / "dupes.txt"
        f.write_text(f"{excluded}\n{excluded}\n", encoding="utf-8")
        parsed = mig.load_exclusions(paths=[str(f)], org_ids=[str(excluded)])
        assert parsed == {excluded}
        result = mig.migrate_provision_subscriptions(exclude=ids.others | parsed)
        assert result["provisioned"] == 2
        assert excluded not in self._subs(db, ids.mine)
