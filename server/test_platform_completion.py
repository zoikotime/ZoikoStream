"""Super Admin -> PLATFORM, completion pass: every page backed by an authoritative source.

  Usage & Entitlements   per-org quotas from the ONE entitlement engine the tenant console
                         uses; not_applicable / unlimited / within / exceeded kept distinct;
                         the ceiling actually enforced reported beside the plan limit; filters,
                         sort and paging in SQL; legacy subscription spellings still match.
  Analytics              window and organization filters reach SQL; totals and series agree;
                         trials never in contracted MRR; unmeasured telemetry listed, not zeroed.
  Support Operations     ORG-009 requests listed platform-wide with dataset-wide counts; only
                         the requesting engineer can start a session; approval is not offered.
  Platform Configuration retention policy changes only through propose -> DIFFERENT approver;
                         effective policy untouched until then; bounds refuse 0 / negative /
                         fractional / warning >= retention; every step audited.
  System Status          background probe results are stored for every process and go stale.
  Media                  "ready" needs provider evidence; a file_url string is not evidence;
                         totals are dataset-wide.
  Commerce               catalog / profile / policy writes audited; only drafts publish; an
                         empty catalog cannot publish; a published row cannot be written without
                         its publisher and time.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import StatementError
from starlette.testclient import TestClient

import app.main as m
from app.crud import event as event_crud
from app.db import SessionLocal
from app.models import (
    AuditLog, CancellationPolicy, CatalogLine, CatalogVersion, ElevationSession, Event,
    LiveRecording, Organization, PlatformSetting, Plan, ServiceProfile, Subscription,
    SupportAccessRequest, User,
)
from app.models.commercial import PublishInvariantError
from app.security import create_access_token, hash_password
from app.services import admin as admin_svc
from app.services import media_retention, provider_health
from app.services import org as org_svc

UTC = timezone.utc
NOW = datetime.now(UTC)
TAG = f"pc{uuid.uuid4().hex[:6]}"
GLOBAL_KEYS = (media_retention.POLICY_KEY, media_retention.PROPOSAL_KEY, provider_health.PROBE_KEY)


# ── fixture ────────────────────────────────────────────────────────────────────────────

class World:
    def __init__(self):
        self.db = SessionLocal()
        self.users, self.orgs, self.plans, self.subs, self.events = [], [], [], [], []
        self.catalogs, self.profiles, self.policies = [], [], []
        # Global rows these tests change. Snapshotted so the suite leaves the DB as it found it.
        self.snapshot = {}
        for key in GLOBAL_KEYS:
            row = self.db.get(PlatformSetting, key)
            self.snapshot[key] = None if row is None else (row.value, row.category, row.updated_by)
        self.staff = self.org(f"{TAG} Staff")
        self.admin = self.user(self.staff, "super_admin", "Admin One")
        self.admin2 = self.user(self.staff, "super_admin", "Admin Two")
        self.db.commit()

    def org(self, name):
        o = Organization(name=name, status="active")
        self.db.add(o)
        self.db.flush()
        self.orgs.append(o.id)
        return o

    def user(self, org, role, name="User"):
        u = User(org_id=org.id, full_name=name, role=role, is_active=True,
                 email=f"{TAG}-{uuid.uuid4().hex[:10]}@example.com",
                 username=f"u{uuid.uuid4().hex[:12]}", password_hash=hash_password("x"),
                 email_verified=True)
        self.db.add(u)
        self.db.flush()
        self.users.append(u.id)
        return u

    def plan(self, **limits):
        p = Plan(name=f"{TAG} plan", slug=f"{TAG}-{uuid.uuid4().hex[:8]}",
                 price_monthly=limits.pop("price", Decimal("50.00")), **limits)
        self.db.add(p)
        self.db.flush()
        self.plans.append(p.id)
        return p

    def sub(self, org, plan, status="active", started_at=None):
        s = Subscription(org_id=org.id, plan_id=plan.id, status=status, seats=1,
                         started_at=started_at or NOW)
        self.db.add(s)
        self.db.flush()
        self.subs.append(s.id)
        return s

    def event(self, org, creator, created_at=None, title=None):
        e = Event(org_id=org.id, created_by=creator.id, title=title or f"{TAG} event",
                  status="scheduled", start_time=NOW + timedelta(days=1), visibility="public")
        if created_at is not None:
            e.created_at = created_at
        self.db.add(e)
        self.db.flush()
        self.events.append(e.id)
        return e

    def elevate(self, user, scope="platform"):
        self.db.add(ElevationSession(user_id=user.id, scope=scope, scopes=[], reason="test",
                                     granted_at=datetime.now(UTC),
                                     expires_at=datetime.now(UTC) + timedelta(minutes=15)))
        self.db.commit()

    def cleanup(self):
        db = self.db
        db.rollback()
        try:
            db.query(LiveRecording).filter(LiveRecording.event_id.in_(self.events or [uuid.uuid4()])).delete(synchronize_session=False)
            db.query(Event).filter(Event.id.in_(self.events or [uuid.uuid4()])).delete(synchronize_session=False)
            db.query(SupportAccessRequest).filter(SupportAccessRequest.org_id.in_(self.orgs)).delete(synchronize_session=False)
            db.query(CatalogLine).filter(CatalogLine.catalog_version_id.in_(self.catalogs or [uuid.uuid4()])).delete(synchronize_session=False)
            db.query(CatalogVersion).filter(CatalogVersion.id.in_(self.catalogs or [uuid.uuid4()])).delete(synchronize_session=False)
            db.query(ServiceProfile).filter(ServiceProfile.id.in_(self.profiles or [uuid.uuid4()])).delete(synchronize_session=False)
            db.query(CancellationPolicy).filter(CancellationPolicy.id.in_(self.policies or [uuid.uuid4()])).delete(synchronize_session=False)
            db.query(Subscription).filter(Subscription.id.in_(self.subs or [uuid.uuid4()])).delete(synchronize_session=False)
            db.query(Plan).filter(Plan.id.in_(self.plans or [uuid.uuid4()])).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.actor_id.in_(self.users)).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.org_id.in_(self.orgs)).delete(synchronize_session=False)
            db.query(ElevationSession).filter(ElevationSession.user_id.in_(self.users)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(self.users)).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id.in_(self.orgs)).delete(synchronize_session=False)
            for key, snap in self.snapshot.items():
                row = db.get(PlatformSetting, key)
                if snap is None:
                    if row is not None:
                        db.delete(row)
                else:
                    if row is None:
                        row = PlatformSetting(key=key)
                        db.add(row)
                    row.value, row.category, row.updated_by = snap
            db.commit()
        finally:
            db.close()


@pytest.fixture
def w():
    world = World()
    try:
        yield world
    finally:
        world.cleanup()


def client_for(user):
    c = TestClient(m.app)
    c.headers.update({"Authorization": f"Bearer {create_access_token(user, remember=False)}"})
    return c


def audits(w, action):
    w.db.expire_all()
    return w.db.query(AuditLog).filter(AuditLog.action == action,
                                       AuditLog.actor_id.in_(w.users)).all()


# ── Usage & Entitlements ───────────────────────────────────────────────────────────────

def _usage_world(w, monkeypatch):
    from app.services import platform_settings
    monkeypatch.setattr(platform_settings, "storage_ceiling_gb", lambda db: 100)
    none = w.org(f"{TAG} A-none")                         # no subscription at all
    w.user(none, "org_admin")
    capped = w.org(f"{TAG} B-capped")                     # seats exceeded, storage uncapped
    plan = w.plan(max_users=2, max_storage_gb=None, max_streaming_hours=10)
    w.sub(capped, plan, "active")
    creator = w.user(capped, "org_admin")
    w.user(capped, "viewer")
    w.user(capped, "viewer")
    w.event(capped, creator)
    legacy = w.org(f"{TAG} C-legacy")                     # stored with the LEGACY spelling
    w.sub(legacy, w.plan(max_users=5, max_storage_gb=10, max_streaming_hours=None), "trial")
    w.db.commit()
    return none, capped, legacy, plan


def test_usage_quota_states_are_distinct_and_report_the_enforced_ceiling(w, monkeypatch):
    none, capped, legacy, plan = _usage_world(w, monkeypatch)
    r = client_for(w.admin).get("/api/admin/usage", params={"q": TAG, "page_size": 100})
    assert r.status_code == 200, r.text
    rows = {row["organization"]: row for row in r.json()["items"]}

    a = rows[none.name]
    assert a["entitlement_source"] == "none" and a["plan"] is None
    assert {q["quota_state"] for q in a["quotas"]} == {"not_applicable"}
    storage_a = next(q for q in a["quotas"] if q["key"] == "storage")
    # No plan, yet the platform ceiling still applies: said, not hidden behind "no plan".
    assert storage_a["enforced_limit"] == 100 and storage_a["limit"] is None

    b = {q["key"]: q for q in rows[capped.name]["quotas"]}
    assert b["members"]["quota_state"] == "exceeded"
    assert b["members"]["used"] == 3 and b["members"]["limit"] == 2 and b["members"]["remaining"] == -1
    assert b["storage"]["quota_state"] == "unlimited" and b["storage"]["unlimited"] is True
    assert b["storage"]["limit"] is None, "unlimited is a null limit, never a number"
    assert b["storage"]["enforced_limit"] == 100
    assert b["streaming_hours"]["quota_state"] == "within"
    assert b["streaming_hours"]["enforced"] is False and b["streaming_hours"]["enforced_limit"] is None

    metrics = {x["key"]: x for x in rows[capped.name]["metrics"]}
    assert metrics["events_created"]["value"] == 1 and metrics["events_created"]["state"] == "measured"
    assert metrics["delivery_windowed"]["state"] == "unavailable"
    assert metrics["delivery_windowed"]["value"] is None, "unavailable must not read as 0"


def test_usage_rows_come_from_the_same_engine_as_the_tenant_console(w, monkeypatch):
    _, capped, _, _ = _usage_world(w, monkeypatch)
    r = client_for(w.admin).get("/api/admin/usage", params={"q": capped.name})
    row = r.json()["items"][0]
    w.db.expire_all()
    assert row["quotas"] == org_svc.entitlements(w.db, w.db.get(Organization, capped.id))["items"]


def test_usage_filters_sort_and_pages_in_sql(w, monkeypatch):
    none, capped, legacy, plan = _usage_world(w, monkeypatch)
    c = client_for(w.admin)
    by_plan = c.get("/api/admin/usage", params={"q": TAG, "plan": plan.slug}).json()
    assert [x["organization"] for x in by_plan["items"]] == [capped.name]
    # Stored as "trial"; filtered by the canonical "trialing". Exact match used to miss it.
    by_status = c.get("/api/admin/usage", params={"q": TAG, "status": "trialing"}).json()
    assert [x["organization"] for x in by_status["items"]] == [legacy.name]

    first = c.get("/api/admin/usage", params={"q": f"{TAG} ", "page_size": 1, "page": 1}).json()
    second = c.get("/api/admin/usage", params={"q": f"{TAG} ", "page_size": 1, "page": 2}).json()
    assert first["total"] == second["total"] >= 3
    assert first["items"][0]["org_id"] != second["items"][0]["org_id"]
    asc = [x["organization"] for x in c.get("/api/admin/usage", params={"q": f"{TAG} ", "sort": "name", "page_size": 100}).json()["items"]]
    desc = [x["organization"] for x in c.get("/api/admin/usage", params={"q": f"{TAG} ", "sort": "-name", "page_size": 100}).json()["items"]]
    assert asc == list(reversed(desc)) and asc == sorted(asc)
    assert c.get("/api/admin/usage", params={"sort": "bogus"}).status_code == 422


def test_subscription_list_filter_matches_both_spellings(w, monkeypatch):
    _, _, legacy, _ = _usage_world(w, monkeypatch)
    items = client_for(w.admin).get("/api/admin/subscriptions",
                                    params={"status": "trialing", "q": legacy.name}).json()["items"]
    assert len(items) == 1


def test_usage_is_super_admin_only(w):
    tenant = w.org(f"{TAG} tenant")
    oa = w.user(tenant, "org_admin")
    w.db.commit()
    assert client_for(oa).get("/api/admin/usage").status_code == 403


# ── Analytics ──────────────────────────────────────────────────────────────────────────

def _analytics_world(w):
    org = w.org(f"{TAG} Analytics")
    creator = w.user(org, "org_admin")
    w.event(org, creator)
    w.event(org, creator)
    w.event(org, creator, created_at=NOW - timedelta(days=20))
    w.sub(org, w.plan(price=Decimal("50.00")), "active")
    w.sub(org, w.plan(price=Decimal("30.00")), "trialing")
    w.db.commit()
    return org


def test_analytics_window_and_org_filters_reach_the_queries(w):
    org = _analytics_world(w)
    c = client_for(w.admin)
    d7 = c.get("/api/admin/analytics", params={"range": "7d", "org_id": str(org.id)}).json()
    d30 = c.get("/api/admin/analytics", params={"range": "30d", "org_id": str(org.id)}).json()
    assert d7["totals"]["events_created"]["value"] == 2
    assert d30["totals"]["events_created"]["value"] == 3
    # The chart and the KPI are the same rows.
    assert sum(p["value"] for p in d30["events"]) == d30["totals"]["events_created"]["value"]
    assert d7["window"]["bucket"] == "day" and len(d7["events"]) in (7, 8)
    assert d7["organizations"] == [], "org growth inside one org is omitted, not a flat line"
    assert d7["totals"]["events_created"]["measurement"] == "measured"
    assert d7["measurement"]["mrr"] == "derived"


def test_analytics_contracted_mrr_excludes_trials_and_is_org_scoped(w):
    org = _analytics_world(w)
    body = client_for(w.admin).get("/api/admin/analytics", params={"org_id": str(org.id)}).json()
    assert body["mrr"] == 50.0
    assert body["trials"]["count"] == 1 and float(body["trials"]["list_value"]) == 30.0
    assert "Playback quality (QoE)" in body["unmeasured"]


def test_analytics_rejects_bad_windows_and_unknown_orgs(w):
    c = client_for(w.admin)
    assert c.get("/api/admin/analytics", params={"range": "custom"}).status_code == 422
    assert c.get("/api/admin/analytics", params={"range": "1y"}).status_code == 422
    assert c.get("/api/admin/analytics", params={"org_id": str(uuid.uuid4())}).status_code == 404
    since = (NOW - timedelta(days=3)).isoformat()
    body = c.get("/api/admin/analytics", params={"range": "custom", "from": since}).json()
    assert body["window"]["range"] == "custom"


# ── Support Operations (ORG-009) ───────────────────────────────────────────────────────

def _request(w, org, engineer, status="requested", **kw):
    req = SupportAccessRequest(
        org_id=org.id, case_reference=f"{TAG}-{uuid.uuid4().hex[:5]}",
        reason_category="customer_reported_issue", engineer_id=engineer.id,
        engineer_display=engineer.full_name, requested_scope="Investigate a report",
        allowed_actions="tenant.read", requested_minutes=30, status=status, **kw)
    w.db.add(req)
    w.db.commit()
    return req


def test_support_access_list_filters_and_counts_dataset_wide(w):
    org = w.org(f"{TAG} Tenant")
    w.db.commit()
    mine = _request(w, org, w.admin, "approved")
    _request(w, org, w.admin2, "requested")
    _request(w, org, w.admin2, "denied")
    c = client_for(w.admin)

    body = c.get("/api/admin/support-access", params={"org_id": str(org.id)}).json()
    assert body["total"] == 3
    row = next(i for i in body["items"] if i["id"] == str(mine.id))
    assert row["organization_name"] == org.name and row["is_mine"] is True
    assert row["countersigned"] is False
    only = c.get("/api/admin/support-access", params={"org_id": str(org.id), "status": "denied"}).json()
    assert [i["status"] for i in only["items"]] == ["denied"]
    # Counts are over the whole table, not the filtered page.
    assert only["summary"]["approved"] >= 1 and only["summary"]["requested"] >= 1
    assert c.get("/api/admin/support-access", params={"status": "approve"}).status_code == 422


def test_only_the_requesting_engineer_can_start_a_session(w):
    org = w.org(f"{TAG} Tenant")
    w.db.commit()
    req = _request(w, org, w.admin, "approved")
    r = client_for(w.admin2).post(f"/api/admin/support-access/{req.id}/start")
    assert r.status_code == 403 and "raised this request" in r.text
    w.db.expire_all()
    assert w.db.get(SupportAccessRequest, req.id).status == "approved", "refusal changed nothing"


def test_support_access_is_super_admin_only_and_vocabulary_is_server_owned(w):
    org = w.org(f"{TAG} Tenant")
    oa = w.user(org, "org_admin")
    w.db.commit()
    assert client_for(oa).get("/api/admin/support-access").status_code == 403
    vocab = client_for(w.admin).get("/api/admin/support-access/vocabulary").json()
    from app.models.support_access import SUPPORT_MAX_MINUTES, SUPPORT_REASONS
    from app.services import tenant_access
    assert vocab["reasons"] == list(SUPPORT_REASONS)
    assert {c["key"] for c in vocab["capabilities"]} == set(tenant_access.CAPABILITIES)
    assert vocab["max_minutes"] == SUPPORT_MAX_MINUTES


# ── Platform Configuration: governed retention ─────────────────────────────────────────

def _clear_retention(w):
    for key in (media_retention.POLICY_KEY, media_retention.PROPOSAL_KEY):
        row = w.db.get(PlatformSetting, key)
        if row is not None:
            w.db.delete(row)
    w.db.commit()


def test_retention_change_needs_a_different_approver_and_moves_nothing_until_then(w):
    _clear_retention(w)
    w.elevate(w.admin)
    w.elevate(w.admin2)
    maker, checker = client_for(w.admin), client_for(w.admin2)

    r = maker.post("/api/admin/retention-policy/proposals",
                   json={"retention_days": 400, "warning_days": 21, "reason": "Contract requires 400 days"})
    assert r.status_code == 201, r.text
    state = r.json()
    assert state["effective"]["retention_days"] == 365, "effective must not move on proposal"
    assert state["proposal"]["retention_days"] == 400
    assert state["proposal"]["requested_by_email"] == w.admin.email
    w.db.expire_all()
    assert media_retention.policy(w.db)["retention_days"] == 365

    again = maker.post("/api/admin/retention-policy/proposals",
                       json={"retention_days": 500, "warning_days": 21, "reason": "Second one at once"})
    assert again.status_code == 422, "one pending change at a time"

    self_ok = maker.post("/api/admin/retention-policy/proposals/approve")
    assert self_ok.status_code == 403
    assert audits(w, "retention_policy.approve_denied"), "a self-approval attempt is audited"
    w.db.expire_all()
    assert media_retention.policy(w.db)["retention_days"] == 365

    ok = checker.post("/api/admin/retention-policy/proposals/approve")
    assert ok.status_code == 200, ok.text
    eff = ok.json()["effective"]
    assert eff["retention_days"] == 400 and eff["warning_days"] == 21
    assert eff["approved_by_email"] == w.admin2.email and eff["requested_by_email"] == w.admin.email
    assert eff["effective_at"] is not None
    assert ok.json()["proposal"] is None
    (entry,) = audits(w, "retention_policy.approve")
    assert entry.meta["before"]["retention_days"] == 365 and entry.meta["after"]["retention_days"] == 400
    assert entry.meta["reason"] == "Contract requires 400 days"


def test_retention_bounds_refuse_zero_negative_fractional_and_inverted_values(w):
    _clear_retention(w)
    w.elevate(w.admin)
    c = client_for(w.admin)
    for rd, wd in [(0, 1), (-5, 1), (29, 1), (4000, 1), (60, 60), (60, 0), ("", 7)]:
        r = c.post("/api/admin/retention-policy/proposals",
                   json={"retention_days": rd, "warning_days": wd, "reason": "Bounds check reason"})
        assert r.status_code == 422, (rd, wd, r.text)
    for rd, wd in [(True, 7), (30.5, 7), (None, 7)]:
        with pytest.raises(ValueError):
            media_retention.validate_policy_values(rd, wd)
    short = c.post("/api/admin/retention-policy/proposals",
                   json={"retention_days": 90, "warning_days": 7, "reason": "short"})
    assert short.status_code == 422, "a reason is required"
    w.db.expire_all()
    assert media_retention.policy_state(w.db)["proposal"] is None


def test_retention_reject_discards_and_requires_elevation(w):
    _clear_retention(w)
    c = client_for(w.admin)
    denied = c.post("/api/admin/retention-policy/proposals",
                    json={"retention_days": 90, "warning_days": 7, "reason": "Without elevation"})
    assert denied.status_code == 403
    w.elevate(w.admin)
    assert c.post("/api/admin/retention-policy/proposals",
                  json={"retention_days": 90, "warning_days": 7, "reason": "Shorter for cost"}).status_code == 201
    r = c.post("/api/admin/retention-policy/proposals/reject", json={"reason": "Not agreed"})
    assert r.status_code == 200
    assert r.json()["proposal"] is None and r.json()["effective"]["retention_days"] == 365
    assert audits(w, "retention_policy.reject")
    assert c.post("/api/admin/retention-policy/proposals/approve").status_code == 404


def test_generic_settings_still_cannot_write_retention_or_its_proposal(w):
    w.elevate(w.admin)
    c = client_for(w.admin)
    for key in (media_retention.POLICY_KEY, media_retention.PROPOSAL_KEY, provider_health.PROBE_KEY):
        r = c.patch("/api/admin/settings", json={"values": {key: {"retention_days": 0}}})
        assert r.status_code == 422, key


# ── System Status: background probes ───────────────────────────────────────────────────

def test_probe_results_are_stored_for_every_process_and_go_stale(w, monkeypatch):
    async def ok():
        return {"status": "ok", "checked_at": datetime.now(UTC).isoformat(), "latency_ms": 12.0, "error": None}

    async def boom():
        raise RuntimeError("secret-bearing message")

    monkeypatch.setattr(provider_health, "configured_providers",
                        lambda: {"streaming": True, "storage": False, "email": True})
    monkeypatch.setattr(provider_health, "_probe_livekit", ok)
    monkeypatch.setattr(provider_health, "_probe_resend", boom)
    monkeypatch.setattr(provider_health, "_last_round", 0.0)
    import asyncio
    results = asyncio.run(provider_health.probe_round(SessionLocal))
    assert results["streaming"]["status"] == "ok"
    assert results["email"] == {**results["email"], "status": "unknown", "error": "RuntimeError"}
    assert "storage" not in results, "an unconfigured provider is not probed"
    assert asyncio.run(provider_health.probe_round(SessionLocal)) is None, "throttled"

    w.db.expire_all()
    assert provider_health.latest(w.db, "streaming")["latency_ms"] == 12.0
    row = w.db.get(PlatformSetting, provider_health.PROBE_KEY)
    assert "secret-bearing" not in str(row.value), "only the error category is stored"

    old = (datetime.now(UTC) - provider_health.STALE_AFTER - timedelta(seconds=5)).isoformat()
    row.value = {**row.value, "streaming": {**row.value["streaming"], "checked_at": old}}
    w.db.commit()
    assert provider_health.latest(w.db, "streaming") is None, "an old success is not current health"


def test_platform_health_reads_the_stored_probe(w, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://example.invalid")
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "k")
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "s")
    row = w.db.get(PlatformSetting, provider_health.PROBE_KEY) or PlatformSetting(key=provider_health.PROBE_KEY, category="health")
    row.value = {"streaming": {"status": "ok", "checked_at": datetime.now(UTC).isoformat(),
                               "latency_ms": 9.0, "error": None}}
    w.db.merge(row)
    w.db.commit()
    streaming = next(s for s in admin_svc.platform_health(w.db)["services"] if s["id"] == "streaming")
    assert streaming["status"] == "ok" and streaming["note"] == "Background probe answered"

    w.db.get(PlatformSetting, provider_health.PROBE_KEY).value = {
        "streaming": {"status": "unknown", "checked_at": datetime.now(UTC).isoformat(),
                      "latency_ms": None, "error": "timeout"}}
    w.db.commit()
    streaming = next(s for s in admin_svc.platform_health(w.db)["services"] if s["id"] == "streaming")
    assert streaming["status"] == "unmonitored", "could-not-ask is neither up nor down"


# ── Media ──────────────────────────────────────────────────────────────────────────────

def test_recording_verdict_needs_provider_evidence_not_a_file_url():
    rec = LiveRecording(status="stopped", enforced=True, file_url="gs://bucket/key.mp4", size_bytes=None)
    assert event_crud.recording_list_state(rec) == "unverified"
    rec.size_bytes = 1024
    assert event_crud.recording_list_state(rec) == "ready"
    assert event_crud.recording_list_state(LiveRecording(status="stopped", enforced=False, size_bytes=5)) == "failed"
    assert event_crud.recording_list_state(LiveRecording(status="recording", enforced=True)) == "in_progress"


def test_recording_totals_are_dataset_wide_and_search_is_server_side(w):
    org = w.org(f"{TAG} MediaOrg")
    creator = w.user(org, "org_admin")
    ev = w.event(org, creator, title=f"{TAG} unique-recital")
    w.db.add_all([
        LiveRecording(event_id=ev.id, org_id=org.id, status="stopped", enforced=True,
                      file_url="gs://b/1.mp4", size_bytes=2048),
        LiveRecording(event_id=ev.id, org_id=org.id, status="stopped", enforced=True,
                      file_url="gs://b/2.mp4", size_bytes=None),
        LiveRecording(event_id=ev.id, org_id=org.id, status="failed", enforced=True),
    ])
    w.db.commit()
    c = client_for(w.admin)
    s = c.get("/api/admin/recordings/summary", params={"q": "unique-recital"}).json()
    assert s["total"] == 3
    assert s["by_state"]["ready"] == 1 and s["by_state"]["unverified"] == 1 and s["by_state"]["failed"] == 1
    rows = c.get("/api/admin/recordings", params={"q": "unique-recital", "limit": 1}).json()
    assert len(rows) == 1 and rows[0]["state"] in {"ready", "unverified", "failed"}
    assert {r["state"] for r in c.get("/api/admin/recordings", params={"q": "unique-recital"}).json()} == {"ready", "unverified", "failed"}


# ── Commerce ───────────────────────────────────────────────────────────────────────────

def test_catalog_lifecycle_is_audited_and_only_a_priced_draft_publishes(w):
    c = client_for(w.admin)
    vertical = f"{TAG}-vert"
    cv = c.post("/api/commercial/catalog-versions", json={"vertical": vertical, "version_label": "v1"})
    assert cv.status_code == 201, cv.text
    cv_id = cv.json()["id"]
    w.catalogs.append(uuid.UUID(cv_id))
    assert cv.json()["status"] == "draft" and cv.json()["integrity_issue"] is None

    empty = c.post(f"/api/commercial/catalog-versions/{cv_id}/publish")
    assert empty.status_code == 400 and "no lines" in empty.text
    line = c.post(f"/api/commercial/catalog-versions/{cv_id}/lines",
                  json={"service_code": "BASE", "name": "Base", "unit_price": "100.00", "currency": "USD"})
    assert line.status_code == 201, line.text
    pub = c.post(f"/api/commercial/catalog-versions/{cv_id}/publish")
    assert pub.status_code == 200, pub.text
    body = pub.json()
    assert body["published_by"] == str(w.admin.id) and body["published_at"] and body["integrity_issue"] is None
    again = c.post(f"/api/commercial/catalog-versions/{cv_id}/publish")
    assert again.status_code == 400, "re-publishing would rewrite who published it and when"

    for action in ("commercial.catalog.create", "commercial.catalog.line_add", "commercial.catalog.publish"):
        assert audits(w, action), action


def test_a_published_catalog_row_cannot_be_written_without_its_publisher(w):
    w.db.add(CatalogVersion(vertical=f"{TAG}-direct", version_label="v1", status="published"))
    with pytest.raises((PublishInvariantError, StatementError)):
        w.db.commit()
    w.db.rollback()
    # The historical anomaly is REPORTED, not rewritten: a read of such a row flags it.
    from app.schemas.commercial import CatalogVersionOut
    out = CatalogVersionOut(id=uuid.uuid4(), vertical="events", version_label="v1", status="published")
    assert out.integrity_issue


def test_service_profile_and_cancellation_policy_writes_are_audited_and_draft_only(w):
    c = client_for(w.admin)
    sp = c.post("/api/commercial/service-profiles",
                json={"version_label": f"{TAG}", "risk_tier": "r1", "name": f"{TAG} profile"})
    assert sp.status_code == 201, sp.text
    w.profiles.append(uuid.UUID(sp.json()["id"]))
    assert c.post(f"/api/commercial/service-profiles/{sp.json()['id']}/publish").status_code == 200
    assert c.post(f"/api/commercial/service-profiles/{sp.json()['id']}/publish").status_code == 400

    cp = c.post("/api/commercial/cancellation-policies",
                json={"version_label": TAG, "vertical": f"{TAG}-vert", "lead_time_min_hours": 0,
                      "refund_percentage": "50"})
    assert cp.status_code == 201, cp.text
    w.policies.append(uuid.UUID(cp.json()["id"]))
    assert c.post(f"/api/commercial/cancellation-policies/{cp.json()['id']}/publish").status_code == 200
    assert c.post(f"/api/commercial/cancellation-policies/{cp.json()['id']}/publish").status_code == 400
    for action in ("commercial.service_profile.create", "commercial.service_profile.publish",
                   "commercial.cancellation_policy.create", "commercial.cancellation_policy.publish"):
        assert audits(w, action), action


def test_commerce_configuration_writes_refuse_non_staff(w):
    tenant = w.org(f"{TAG} tenant")
    viewer = w.user(tenant, "viewer")
    w.db.commit()
    r = client_for(viewer).post("/api/commercial/catalog-versions",
                                json={"vertical": f"{TAG}-x", "version_label": "v1"})
    assert r.status_code == 403
