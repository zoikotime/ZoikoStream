"""Contracted MRR on the Analytics page, and the time range it sits beside.

The report: the card read $596 whichever range was selected (24h/7d/30d/90d/custom). Traced
end to end, the range DOES reach the server and every windowed figure moves with it; MRR does
not, by design. services/admin.analytics computes it from `_monthly_revenue`, which reads the
subscriptions table as it stands now:

  * A Subscription is ONE mutable row. `status` and `plan_id` are overwritten in place on every
    transition and plan change, so the table cannot say what a tenant was paying last month.
  * Audit rows cover only some write paths (and none written before auditing existed), so
    replaying them would produce a historical figure that looks measured and is not.

So the defined metric is CURRENT contracted MRR — list price over `active` subscriptions at the
moment the request is served — and the fix is to say so: the response carries `mrr_as_of`, the
instant it was evaluated, which is NOT the window's `until` when a custom range ends in the past.
These tests pin that the figure is range-independent and that the response says when it holds.

The $596 itself is not a defect: 2 x $249 + 2 x $49 over four active subscriptions (see
test_contracted_mrr.py). The "$347" counter-example totalled three of the four rows.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import Organization, Plan, Subscription, User
from app.security import create_access_token, hash_password

UTC = timezone.utc
NOW = datetime.now(UTC)


@pytest.fixture
def world():
    db = SessionLocal()
    made = {"s": [], "p": [], "u": [], "o": []}
    try:
        org = Organization(name=f"MrrWindow {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        made["o"].append(org.id)
        admin = User(org_id=org.id, full_name="Platform Admin", role="super_admin",
                     is_active=True, email=f"mrrw-{uuid.uuid4().hex[:10]}@example.com",
                     username=f"mrrw{uuid.uuid4().hex[:10]}",
                     password_hash=hash_password("x"), email_verified=True)
        db.add(admin)
        db.flush()
        made["u"].append(admin.id)
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


def _sub(world, status, price, started_at, interval=None):
    db = world["db"]
    plan = Plan(name=f"P{uuid.uuid4().hex[:6]}", slug=f"p{uuid.uuid4().hex[:10]}",
                price_monthly=price)
    db.add(plan)
    db.flush()
    world["made"]["p"].append(plan.id)
    sub = Subscription(org_id=world["org"].id, plan_id=plan.id, status=status,
                       started_at=started_at, billing_interval=interval)
    db.add(sub)
    db.commit()
    world["made"]["s"].append(sub.id)
    return sub


def _get(world, **params):
    c = TestClient(m.app)
    c.headers.update({"Authorization": f"Bearer {create_access_token(world['admin'], remember=False)}"})
    r = c.get("/api/admin/analytics", params={"org_id": str(world["org"].id), **params})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def seeded(world):
    # The report's shape: 2 x Business + 2 x Developer, active, started at different ages so a
    # windowed sum WOULD differ by range if MRR were (wrongly) computed as one.
    _sub(world, "active", 249, NOW - timedelta(hours=2))
    _sub(world, "active", 249, NOW - timedelta(days=200))
    _sub(world, "active", 49, NOW - timedelta(days=20))
    _sub(world, "active", 49, NOW - timedelta(days=60), interval="annual")
    _sub(world, "trial", 249, NOW - timedelta(hours=1))      # pays nothing
    _sub(world, "trialing", 49, NOW - timedelta(days=3))     # pays nothing
    _sub(world, "canceled", 249, NOW - timedelta(days=10))   # no longer contracted
    _sub(world, "active", None, NOW - timedelta(days=5))     # unpriced: not counted, not $0
    return world


RANGES = [{"range": "24h"}, {"range": "7d"}, {"range": "30d"}, {"range": "90d"},
          {"range": "custom", "from": (NOW - timedelta(days=400)).isoformat(),
           "to": (NOW - timedelta(days=300)).isoformat()}]


@pytest.mark.parametrize("params", RANGES, ids=lambda p: p["range"])
def test_current_mrr_is_the_same_whatever_the_range(seeded, params):
    body = _get(seeded, **params)
    assert body["mrr"] == pytest.approx(596.0)
    # The trial note is the same kind of figure: who is in trial NOW, not trials in the window.
    assert body["trials"] == {"count": 2, "list_value": pytest.approx(298.0), "unpriced": 0}


def test_the_windowed_chart_does_move_with_the_range(seeded):
    # The contrast that makes the card's semantics visible: the "New contracted MRR" chart is
    # windowed (started inside the window, still active); the card is not.
    new = lambda p: sum(pt["value"] for pt in _get(seeded, **p)["revenue"])
    assert new({"range": "24h"}) == pytest.approx(249.0)
    assert new({"range": "30d"}) == pytest.approx(298.0)
    assert new({"range": "90d"}) == pytest.approx(347.0)


def test_response_states_when_mrr_holds(seeded):
    before = datetime.now(UTC)
    body = _get(seeded, range="7d")
    after = datetime.now(UTC)
    as_of = datetime.fromisoformat(body["mrr_as_of"])
    assert before - timedelta(seconds=1) <= as_of <= after + timedelta(seconds=1)


def test_a_past_custom_window_does_not_relabel_current_mrr_as_historical(seeded):
    # A window that ended 300 days ago: its `until` is in the past, but MRR is still evaluated
    # now — and the response says so rather than letting `until` stand in for it.
    body = _get(seeded, range="custom", **{"from": (NOW - timedelta(days=400)).isoformat(),
                                          "to": (NOW - timedelta(days=300)).isoformat()})
    until = datetime.fromisoformat(body["window"]["until"])
    as_of = datetime.fromisoformat(body["mrr_as_of"])
    assert as_of - until > timedelta(days=299)
    assert body["mrr"] == pytest.approx(596.0)


def test_one_subscription_is_counted_once(world):
    # A join that fanned out (one org, several users) must not multiply a subscription.
    for _ in range(3):
        u = User(org_id=world["org"].id, full_name="Member", role="viewer", is_active=True,
                 email=f"mrrw-m-{uuid.uuid4().hex[:10]}@example.com",
                 username=f"mrrwm{uuid.uuid4().hex[:10]}", password_hash=hash_password("x"))
        world["db"].add(u)
        world["db"].flush()
        world["made"]["u"].append(u.id)
    world["db"].commit()
    _sub(world, "active", 249, NOW - timedelta(days=1))
    assert _get(world, range="30d")["mrr"] == pytest.approx(249.0)


# ── the trial note ─────────────────────────────────────────────────────────────────────
#
# "10 subscriptions are in trial ($641/mo ...)" did not move with the range either, for the same
# reason: it is a CURRENT snapshot (status in trial/trialing now), the companion to current MRR.
# A windowed reading is not recoverable - a trial that converted or expired keeps no marker that
# it was ever one - so it stays a snapshot and says so. What WAS wrong: the count is COUNT(*) but
# the value is SUM(price), which skips unpriced plans, so the two described different rows.
# $641 is 1 x $249 + 8 x $49 - nine trials - beside a count of ten.

def test_trial_count_and_value_say_which_rows_they_cover(world):
    _sub(world, "trialing", 249, NOW - timedelta(days=2))
    _sub(world, "trial", 49, NOW - timedelta(days=20))        # legacy spelling still a trial
    _sub(world, "trialing", None, NOW - timedelta(days=1))    # unpriced: counted, not valued
    t = _get(world, range="7d")["trials"]
    assert t == {"count": 3, "list_value": pytest.approx(298.0), "unpriced": 1}


@pytest.mark.parametrize("status", ["active", "trial_expired", "canceled", "conversion_pending"])
def test_only_subscriptions_in_trial_now_are_in_the_note(world, status):
    _sub(world, status, 249, NOW - timedelta(days=1))
    assert _get(world, range="30d")["trials"]["count"] == 0


@pytest.mark.parametrize("params", RANGES, ids=lambda p: p["range"])
def test_trial_note_does_not_move_with_the_range(world, params):
    # Started 200 days ago and 1 hour ago: a windowed count would differ between 24h and 90d.
    _sub(world, "trialing", 249, NOW - timedelta(days=200))
    _sub(world, "trialing", 49, NOW - timedelta(hours=1))
    assert _get(world, **params)["trials"] == {"count": 2, "list_value": pytest.approx(298.0),
                                               "unpriced": 0}


def test_trial_note_is_org_scoped_and_never_reaches_mrr(world):
    _sub(world, "trialing", 249, NOW - timedelta(days=1))
    other = Organization(name=f"MrrWindow other {uuid.uuid4().hex[:6]}", status="active")
    world["db"].add(other)
    world["db"].flush()
    world["made"]["o"].append(other.id)
    plan = Plan(name=f"P{uuid.uuid4().hex[:6]}", slug=f"p{uuid.uuid4().hex[:10]}", price_monthly=49)
    world["db"].add(plan)
    world["db"].flush()
    world["made"]["p"].append(plan.id)
    s = Subscription(org_id=other.id, plan_id=plan.id, status="trialing", started_at=NOW)
    world["db"].add(s)
    world["db"].commit()
    world["made"]["s"].insert(0, s.id)
    body = _get(world, range="30d")
    assert body["trials"]["count"] == 1 and body["trials"]["list_value"] == pytest.approx(249.0)
    assert body["mrr"] == 0
