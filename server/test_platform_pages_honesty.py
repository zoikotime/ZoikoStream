"""Super Admin -> PLATFORM: every number and status traceable to a real source.

Each test below pins a specific defect found in the audit, so a regression fails loudly
rather than quietly reintroducing a fabricated value:

  System Status        LiveKit / storage / email reported "ok" because an env var was SET;
                       Redis was not checked at all; "All systems operational" therefore meant
                       "the database answered SELECT 1".
  Commerce             "Stripe runs in TEST mode" was fixed copy on every deployment.
  Analytics            MRR summed TRIALING subscriptions at full list price as "Revenue".
  Usage & Entitlements KPIs counted the first 100 rows; "Edit subscription" 500'd on HEAD
                       because Subscription was never imported into the router.
  Support Operations   KPIs counted the first 100 tickets.
  Platform Config      PATCH accepted any key — including media_retention_policy, which
                       decides how long recordings are protected — and audited no values.
"""
import uuid
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, Organization, Plan, Subscription, User
from app.models.platform_ops import ElevationSession
from app.security import create_access_token, hash_password
from app.services import admin as admin_svc

UTC = timezone.utc


@pytest.fixture
def world():
    db = SessionLocal()
    made = {"u": [], "o": [], "e": [], "s": [], "p": []}
    try:
        org = Organization(name=f"PlatCo {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        made["o"] = [org.id]
        admin = User(org_id=org.id, full_name="Platform Admin", role="super_admin", is_active=True,
                     email=f"plat-{uuid.uuid4().hex[:10]}@example.com",
                     username=f"plat{uuid.uuid4().hex[:10]}",
                     password_hash=hash_password("x"), email_verified=True)
        viewer = User(org_id=org.id, full_name="Viewer", role="viewer", is_active=True,
                      email=f"view-{uuid.uuid4().hex[:10]}@example.com",
                      username=f"view{uuid.uuid4().hex[:10]}",
                      password_hash=hash_password("x"), email_verified=True)
        db.add_all([admin, viewer])
        db.flush()
        made["u"] = [admin.id, viewer.id]
        db.commit()
        yield {"db": db, "org": org, "admin": admin, "viewer": viewer, "made": made}
    finally:
        try:
            db.execute(ElevationSession.__table__.delete().where(
                ElevationSession.user_id.in_(made["u"])))
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


def client_for(user):
    c = TestClient(m.app)
    c.headers.update({"Authorization": f"Bearer {create_access_token(user, remember=False)}"})
    return c


def elevate(world, scope="platform"):
    now = datetime.now(UTC)
    from datetime import timedelta
    row = ElevationSession(user_id=world["admin"].id, scope=scope, scopes=[], reason="test",
                           granted_at=now, expires_at=now + timedelta(minutes=15))
    world["db"].add(row)
    world["db"].commit()


# ── System Status ──────────────────────────────────────────────────────────────────────

def test_a_configured_but_unprobed_service_is_never_reported_ok(monkeypatch):
    """THE FAKE GREEN. An env var being set is evidence of intent, not of health."""
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://example.invalid")
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    db = SessionLocal()
    try:
        h = admin_svc.platform_health(db)
    finally:
        db.close()
    by_id = {s["id"]: s for s in h["services"]}
    assert by_id["streaming"]["status"] == "unmonitored"
    assert by_id["email"]["status"] == "unmonitored"
    assert by_id["streaming"]["status"] != "ok"


def test_overall_is_computed_only_from_probed_services(monkeypatch):
    """Unmonitored services cannot make `overall` green, and are counted so the header can
    say what "operational" covers."""
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://example.invalid")
    db = SessionLocal()
    try:
        h = admin_svc.platform_health(db)
    finally:
        db.close()
    assert h["unmonitored"] >= 1
    probed = [s for s in h["services"] if s["status"] in admin_svc.PROBED_STATUSES]
    assert all(s["id"] != "streaming" for s in probed)


def test_redis_is_actually_reported():
    """It was absent entirely, though the realtime bus depends on it."""
    db = SessionLocal()
    try:
        ids = {s["id"] for s in admin_svc.platform_health(db)["services"]}
    finally:
        db.close()
    assert "redis" in ids


def test_an_unreachable_redis_is_degraded_not_ok(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")   # nothing listens
    admin_svc._redis_probe_cache.update(at=0.0, result=None)
    try:
        r = admin_svc._probe_redis()
    finally:
        admin_svc._redis_probe_cache.update(at=0.0, result=None)
    assert r["status"] == "warn"
    assert "127.0.0.1" not in r["note"], "the probe must never echo the URL"


def test_no_redis_url_is_not_configured_not_an_outage(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    assert admin_svc._probe_redis()["status"] == "not_configured"


# ── Commerce: the payment provider notice ──────────────────────────────────────────────

@pytest.mark.parametrize("key,mode", [
    ("", "not_configured"),
    ("sk_test_abc123", "test"),
    ("rk_test_abc123", "test"),
    ("sk_live_abc123", "live"),
    ("garbage", "unrecognised"),
])
def test_stripe_mode_is_detected_not_assumed(world, monkeypatch, key, mode):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", key)
    r = client_for(world["admin"]).get("/api/admin/payment-provider")
    assert r.status_code == 200
    assert r.json()["mode"] == mode


def test_the_stripe_key_never_leaves_the_server(world, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_live_SECRETVALUE123")
    body = client_for(world["admin"]).get("/api/admin/payment-provider").text
    assert "SECRETVALUE123" not in body
    assert "sk_live_" not in body, "not even the prefix"


# ── Analytics / Usage: MRR excludes trials ─────────────────────────────────────────────

def _plan_and_sub(world, status, price=100):
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


def test_a_trial_contributes_nothing_to_contracted_mrr(world):
    """A trial collects nothing, yet MRR summed it at full list price and Analytics charted
    it as "Revenue"."""
    before = admin_svc._monthly_revenue(world["db"])
    _plan_and_sub(world, "trialing", price=500)
    assert admin_svc._monthly_revenue(world["db"]) == before


def test_an_active_subscription_does_contribute(world):
    before = admin_svc._monthly_revenue(world["db"])
    _plan_and_sub(world, "active", price=250)
    assert admin_svc._monthly_revenue(world["db"]) == pytest.approx(before + 250)


def test_trials_are_reported_separately_and_labelled(world):
    _plan_and_sub(world, "trialing", price=40)
    body = client_for(world["admin"]).get("/api/admin/analytics").json()
    assert body["mrr_basis"] == "contracted_list_price"
    assert body["trials"]["count"] >= 1


def test_usage_and_analytics_report_the_same_mrr(world):
    """Two pages, one number. They used to compute MRR differently."""
    _plan_and_sub(world, "active", price=75)
    c = client_for(world["admin"])
    assert (c.get("/api/admin/subscriptions/summary").json()["contracted_mrr"]
            == c.get("/api/admin/analytics").json()["mrr"])


# ── Usage & Entitlements ───────────────────────────────────────────────────────────────

def test_the_subscription_summary_is_dataset_wide(world):
    _plan_and_sub(world, "active")
    s = client_for(world["admin"]).get("/api/admin/subscriptions/summary").json()
    real = world["db"].query(Subscription).count()
    assert s["total"] == real, "counted over every subscription, not a fetched page"


def test_editing_a_subscription_no_longer_500s(world):
    """Pre-existing: PATCH /admin/subscriptions/{id} raised NameError on HEAD because
    Subscription was never imported. The Edit button had never worked."""
    r = client_for(world["admin"]).patch(f"/api/admin/subscriptions/{uuid.uuid4()}",
                                         json={"status": "active"})
    assert r.status_code == 404, r.text


def test_subscription_search_happens_on_the_server(world):
    _plan_and_sub(world, "active")
    items = client_for(world["admin"]).get(
        "/api/admin/subscriptions", params={"q": world["org"].name}).json()["items"]
    assert items and all(i["organization_name"] == world["org"].name for i in items)


# ── Support Operations ─────────────────────────────────────────────────────────────────

def test_the_support_summary_is_dataset_wide(world):
    from app.models import SupportTicket
    s = client_for(world["admin"]).get("/api/admin/support-tickets/summary").json()
    assert s["total"] == world["db"].query(SupportTicket).count()


# ── Platform Configuration ─────────────────────────────────────────────────────────────

def test_the_retention_policy_cannot_be_written_through_generic_settings(world):
    """The governance bypass. media_retention_policy decides how long a recording is
    protected; through this endpoint it could be set to zero days."""
    elevate(world)
    r = client_for(world["admin"]).patch(
        "/api/admin/settings", json={"values": {"media_retention_policy": {"retention_days": 0}}})
    assert r.status_code == 422, r.text
    from app.models import PlatformSetting
    world["db"].expire_all()
    row = world["db"].get(PlatformSetting, "media_retention_policy")
    assert row is None or row.value.get("retention_days") != 0


def test_an_arbitrary_key_cannot_be_created(world):
    elevate(world)
    r = client_for(world["admin"]).patch(
        "/api/admin/settings", json={"values": {"made_up_key": {"x": 1}}})
    assert r.status_code == 422


def test_a_managed_key_is_still_writable_and_audited_with_before_and_after(world):
    elevate(world)
    c = client_for(world["admin"])
    marker = f"Brand {uuid.uuid4().hex[:6]}"
    r = c.patch("/api/admin/settings", json={"values": {"brand": {"name": marker}}})
    assert r.status_code == 200, r.text

    world["db"].expire_all()
    row = (world["db"].query(AuditLog)
           .filter(AuditLog.action == "settings.update")
           .order_by(AuditLog.created_at.desc()).first())
    assert row is not None
    changes = (row.meta or {}).get("changes", {})
    assert changes.get("brand", {}).get("after") == {"name": marker}
    assert "before" in changes["brand"], "the audit row must say what it changed FROM"


def test_settings_writes_still_require_elevation(world):
    r = client_for(world["admin"]).patch("/api/admin/settings",
                                         json={"values": {"brand": {"name": "x"}}})
    assert r.status_code == 403


def test_no_secret_is_ever_returned_by_settings(world, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_live_TOPSECRET")
    monkeypatch.setattr(settings, "SECRET_KEY", "jwt-TOPSECRET")
    body = client_for(world["admin"]).get("/api/admin/settings").text
    assert "TOPSECRET" not in body


# ── authorization ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/admin/payment-provider", "/api/admin/subscriptions/summary",
    "/api/admin/support-tickets/summary", "/api/admin/platform-health", "/api/admin/analytics",
])
def test_a_non_super_admin_is_refused(world, path):
    assert client_for(world["viewer"]).get(path).status_code == 403
