"""Contracted MRR: the arithmetic behind the Usage & Entitlements KPI row.

A report said the KPI read $596 where a hand-total of the subscriptions table gave $347, and
proposed the cause: trials being summed at list price. Traced end to end, that cause is not
present — it was found and fixed earlier, and test_platform_pages_honesty.py already pins it:

    SUBSCRIPTION_REVENUE_STATES   ("active", "trial", "trialing")   <- drives ENTITLEMENT
    _TRIAL_STATUSES               ("trial", "trialing")
    _MRR_STATUSES                 ("active",)                       <- drives MONEY

The two totals differ because they count different numbers of rows. $347 is 249 + 49 + 49 —
three subscriptions. The KPI row on the same screenshot reads Active 4, and 2x249 + 2x49 is
exactly 596. One eligible row sat below the visible portion of the table.

That is the failure mode worth pinning: not a wrong formula, but a reader totalling the rows
they can see against a figure computed over the whole table. /subscriptions/summary derives
`active` and `contracted_mrr` from the same Subscription rows in the same request, so the two
cannot drift — and the tests below hold them to that, in the shape the report described.

Prices are the seeded approved ones (seed.py): Developer $49, Business $249.
"""
import uuid
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import Organization, Plan, Subscription, User
from app.security import create_access_token, hash_password
from app.services import admin as admin_svc

UTC = timezone.utc
DEVELOPER, BUSINESS = 49, 249


@pytest.fixture
def world():
    db = SessionLocal()
    made = {"u": [], "o": [], "s": [], "p": []}
    try:
        org = Organization(name=f"MrrCo {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        made["o"] = [org.id]
        admin = User(org_id=org.id, full_name="Platform Admin", role="super_admin",
                     is_active=True, email=f"mrr-{uuid.uuid4().hex[:10]}@example.com",
                     username=f"mrr{uuid.uuid4().hex[:10]}",
                     password_hash=hash_password("x"), email_verified=True)
        db.add(admin)
        db.flush()
        made["u"] = [admin.id]
        db.commit()
        yield {"db": db, "org": org, "admin": admin, "made": made}
    finally:
        try:
            for sid in made["s"]:
                db.execute(Subscription.__table__.delete().where(Subscription.id == sid))
            for pid in made["p"]:
                db.execute(Plan.__table__.delete().where(Plan.id == pid))
            for uid in made["u"]:
                db.execute(User.__table__.delete().where(User.id == uid))
            for oid in made["o"]:
                db.execute(Organization.__table__.delete().where(Organization.id == oid))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def _sub(world, status, price=100):
    """One subscription on its own plan. `price=None` is a plan with no approved price."""
    db = world["db"]
    plan = Plan(name=f"P{uuid.uuid4().hex[:6]}", slug=f"p{uuid.uuid4().hex[:10]}",
                price_monthly=price)
    db.add(plan)
    db.flush()
    world["made"]["p"].append(plan.id)
    sub = Subscription(org_id=world["org"].id, plan_id=plan.id, status=status,
                       started_at=datetime.now(UTC))
    db.add(sub)
    db.commit()
    world["made"]["s"].append(sub.id)
    return sub


def client_for(user):
    c = TestClient(m.app)
    c.headers.update({"Authorization": f"Bearer {create_access_token(user, remember=False)}"})
    return c


def mrr(world):
    return admin_svc._monthly_revenue(world["db"])


def summary(world):
    return client_for(world["admin"]).get("/api/admin/subscriptions/summary").json()


# ── which statuses are money ────────────────────────────────────────────────────────────

def test_only_active_counts_toward_mrr():
    # The whole dispute in one line: entitlement is a wider set than revenue, on purpose.
    assert admin_svc._MRR_STATUSES == ("active",)


def test_the_entitlement_set_is_deliberately_wider():
    from app.models.subscription import SUBSCRIPTION_REVENUE_STATES
    assert set(SUBSCRIPTION_REVENUE_STATES) > set(admin_svc._MRR_STATUSES)


@pytest.mark.parametrize("status", ["trial", "trialing"])
def test_neither_spelling_of_a_trial_contributes(world, status):
    # Production rows use the legacy `trial`; a check written only against `trialing` would
    # miss exactly the rows that matter.
    before = mrr(world)
    _sub(world, status, price=BUSINESS)
    assert mrr(world) == before


@pytest.mark.parametrize("status", ["past_due", "canceled", "closed"])
def test_non_paying_states_contribute_nothing(world, status):
    before = mrr(world)
    _sub(world, status, price=BUSINESS)
    assert mrr(world) == before


def test_an_active_subscription_contributes_its_list_price(world):
    before = mrr(world)
    _sub(world, "active", price=BUSINESS)
    assert mrr(world) == pytest.approx(before + BUSINESS)


# ── the reported dataset ────────────────────────────────────────────────────────────────

@pytest.fixture
def reported(world):
    """The screenshot: Total 14, Active 4, Trial 10, Past Due 0."""
    base_mrr, base = mrr(world), summary(world)
    for price in (BUSINESS, BUSINESS, DEVELOPER, DEVELOPER):
        _sub(world, "active", price=price)
    for _ in range(10):
        _sub(world, "trial", price=BUSINESS)
    return {"world": world, "base_mrr": base_mrr, "base": base}


def test_four_active_subscriptions_total_596(reported):
    assert mrr(reported["world"]) - reported["base_mrr"] == pytest.approx(596)


def test_it_is_not_the_three_row_hand_total(reported):
    # 249 + 49 + 49. The figure the report expected, and one row short.
    assert mrr(reported["world"]) - reported["base_mrr"] != pytest.approx(347)


def test_ten_trials_add_nothing_even_at_business_list_price(reported):
    # Had they been summed, the ten would have carried 2490 of imaginary revenue.
    assert mrr(reported["world"]) - reported["base_mrr"] == pytest.approx(596)


def test_the_active_count_matches_the_rows_the_money_came_from(reported):
    now = summary(reported["world"])
    assert now["active"] - reported["base"]["active"] == 4
    assert now["trial"] - reported["base"]["trial"] == 10
    assert now["past_due"] == reported["base"]["past_due"]


def test_the_kpi_row_is_internally_consistent(reported):
    """Active and Contracted MRR come from one request over one table, so a reader comparing
    them is comparing like with like — which is what made 4-vs-3 rows look like a bad sum."""
    now = summary(reported["world"])
    assert now["contracted_mrr"] - reported["base"]["contracted_mrr"] == pytest.approx(596)
    assert now["mrr_basis"] == "contracted_list_price"


# ── the invariant that keeps the two figures together ───────────────────────────────────

@pytest.mark.parametrize("price", [DEVELOPER, BUSINESS])
def test_one_more_active_row_moves_both_figures_by_exactly_one_subscription(world, price):
    before = summary(world)
    _sub(world, "active", price=price)
    after = summary(world)
    assert after["active"] - before["active"] == 1
    assert after["contracted_mrr"] - before["contracted_mrr"] == pytest.approx(price)


def test_a_trial_moves_the_trial_count_and_not_the_money(world):
    before = summary(world)
    _sub(world, "trial", price=BUSINESS)
    after = summary(world)
    assert after["trial"] - before["trial"] == 1
    assert after["contracted_mrr"] == before["contracted_mrr"]


# ── what an unpriced plan does ──────────────────────────────────────────────────────────

def test_a_plan_with_no_approved_price_is_skipped_not_zeroed(world):
    # SUM ignores NULL. A 0 in this KPI means "no priced plans", not "no subscriptions" —
    # so an unpriced active plan must not drag a total down or read as free.
    before = summary(world)
    _sub(world, "active", price=None)
    after = summary(world)
    assert after["active"] - before["active"] == 1, "it is still an active subscription"
    assert after["contracted_mrr"] == before["contracted_mrr"], "but it contributes no revenue"


def test_mrr_is_never_none(world):
    assert isinstance(mrr(world), float)
