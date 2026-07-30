"""Organization Overview aggregation checks.

Real database, inside a transaction that is ALWAYS rolled back — these functions are
almost entirely queries, so faking the Session would test nothing.

The isolation tests are the important ones: the whole point of this surface is that an org
admin sees their own tenant and nothing else.

Run with `python test_org_overview.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    AnalyticsSnapshot,
    BroadcastSession,
    Event,
    EventAssignment,
    Invitation,
    LiveRecording,
    Organization,
    Plan,
    Subscription,
    SupportTicket,
    User,
)
from app.services import org as org_svc

NOW = datetime.now(timezone.utc)


def _org(db, name=None, **kw):
    o = Organization(name=name or f"org-test-{uuid.uuid4().hex[:8]}", status="active", **kw)
    db.add(o)
    db.flush()
    return o


def _user(db, org, role="org_admin"):
    u = User(
        org_id=org.id, full_name="Test Owner", role=role, is_active=True,
        email=f"t{uuid.uuid4().hex[:10]}@example.com",
        username=f"u{uuid.uuid4().hex[:10]}", password_hash="x",
    )
    db.add(u)
    db.flush()
    return u


def _plan(db, **kw):
    p = Plan(name="Test Plan", slug=f"test-{uuid.uuid4().hex[:8]}", price_monthly=99,
             currency="USD", is_active=True, **kw)
    db.add(p)
    db.flush()
    return p


def _event(db, org, creator, **kw):
    e = Event(org_id=org.id, created_by=creator.id, title="Org Test Event",
              status="scheduled", start_time=NOW + timedelta(hours=6), **kw)
    db.add(e)
    db.flush()
    return e


def _session(db, org, event, **kw):
    s = BroadcastSession(org_id=org.id, event_id=event.id, **kw)
    db.add(s)
    db.flush()
    return s


# ── isolation ─────────────────────────────────────────────────────────────────

def test_overview_only_reports_the_callers_org():
    """Two orgs, each with their own session/recording/ticket. Neither overview may show
    the other's rows."""
    db = SessionLocal()
    try:
        a, b = _org(db, name="Tenant A"), _org(db, name="Tenant B")
        ua, ub = _user(db, a), _user(db, b)
        ea, eb = _event(db, a, ua), _event(db, b, ub)
        _session(db, a, ea, status="live", started_at=NOW - timedelta(hours=1))
        for _ in range(3):
            _session(db, b, eb, status="live", started_at=NOW - timedelta(hours=1))
        db.add_all([
            LiveRecording(event_id=ea.id, org_id=a.id, status="stopped", enforced=True),
            LiveRecording(event_id=eb.id, org_id=b.id, status="stopped", enforced=True),
            LiveRecording(event_id=eb.id, org_id=b.id, status="stopped", enforced=True),
            SupportTicket(org_id=b.id, subject="B only", message="m", status="open"),
        ])
        db.flush()

        oa = org_svc.overview(db, a, ua)
        ob = org_svc.overview(db, b, ub)

        assert oa["sessions"]["live"] == 1, oa["sessions"]
        assert ob["sessions"]["live"] == 3, ob["sessions"]
        assert oa["media_assets"]["ready"] == 1
        assert ob["media_assets"]["ready"] == 2
        # B's support ticket must not leak into A.
        assert oa["security_support"]["open_cases"] == 0
        assert ob["security_support"]["open_cases"] == 1
        # Neither payload carries a platform-wide rollup.
        for payload in (oa, ob):
            assert "total_organizations" not in str(payload.keys())
            assert payload["organization"]["id"] in (str(a.id), str(b.id))
    finally:
        db.rollback()
        db.close()


def test_upcoming_events_are_org_scoped():
    db = SessionLocal()
    try:
        a, b = _org(db), _org(db)
        ua, ub = _user(db, a), _user(db, b)
        ea = _event(db, a, ua, impact="high")
        eb = _event(db, b, ub, impact="high")
        db.flush()
        ids_a = {e["id"] for e in org_svc.upcoming_events(db, a.id)}
        ids_b = {e["id"] for e in org_svc.upcoming_events(db, b.id)}
        assert str(ea.id) in ids_a and str(eb.id) not in ids_a
        assert str(eb.id) in ids_b and str(ea.id) not in ids_b
    finally:
        db.rollback()
        db.close()


# ── entitlements ──────────────────────────────────────────────────────────────

def test_entitlement_percentages_use_real_plan_limits():
    db = SessionLocal()
    try:
        org = _org(db, storage_used_gb=75.0)
        _user(db, org)
        plan = _plan(db, max_storage_gb=100, max_users=10, max_streaming_hours=50)
        db.add(Subscription(org_id=org.id, plan_id=plan.id, status="active", seats=5))
        db.flush()

        ent = org_svc.entitlements(db, org)
        by = {i["label"]: i for i in ent["items"]}
        assert by["Storage"]["used"] == 75.0 and by["Storage"]["limit"] == 100
        assert by["Storage"]["percent"] == 75.0, by["Storage"]
        assert by["Members"]["limit"] == 10 and by["Members"]["used"] == 1
        assert ent["plan"] == "Test Plan" and ent["status"] == "active"
        assert ent["highest_percent"] == 75.0
    finally:
        db.rollback()
        db.close()


def test_no_plan_means_no_invented_ceiling():
    """An org without a subscription must report usage with a null limit — never a
    made-up denominator, which would render a meaningless progress bar."""
    db = SessionLocal()
    try:
        org = _org(db, storage_used_gb=12.0)
        _user(db, org)
        ent = org_svc.entitlements(db, org)
        for item in ent["items"]:
            assert item["limit"] is None and item["percent"] is None, item
        assert ent["highest_percent"] is None
    finally:
        db.rollback()
        db.close()


def test_usage_threshold_raises_attention_item():
    db = SessionLocal()
    try:
        org = _org(db, storage_used_gb=95.0)
        u = _user(db, org)
        plan = _plan(db, max_storage_gb=100, max_users=10, max_streaming_hours=50)
        db.add(Subscription(org_id=org.id, plan_id=plan.id, status="active", seats=1))
        db.flush()
        out = org_svc.overview(db, org, u)
        titles = [a["title"] for a in out["attention"]]
        assert any("Storage usage approaching limit" in t for t in titles), titles
        # 95% is a warning, not yet critical.
        item = next(a for a in out["attention"] if "Storage" in a["title"])
        assert item["severity"] == "warning" and item["action"] == "Review"
    finally:
        db.rollback()
        db.close()


def test_usage_over_limit_is_critical():
    db = SessionLocal()
    try:
        org = _org(db, storage_used_gb=120.0)
        u = _user(db, org)
        plan = _plan(db, max_storage_gb=100)
        db.add(Subscription(org_id=org.id, plan_id=plan.id, status="active", seats=1))
        db.flush()
        out = org_svc.overview(db, org, u)
        item = next(a for a in out["attention"] if "Storage" in a["title"])
        assert item["severity"] == "critical" and "limit reached" in item["title"], item
    finally:
        db.rollback()
        db.close()


# ── attention items ───────────────────────────────────────────────────────────

def test_expiring_credential_is_surfaced_and_non_expiring_is_not():
    db = SessionLocal()
    try:
        org = _org(db)
        u = _user(db, org)
        org.api_keys = [
            {"id": "k1", "label": "Northwind Web Player", "prefix": "zk_live_aaa",
             "expires_at": (NOW + timedelta(days=6)).isoformat(), "revoked": False},
            {"id": "k2", "label": "Legacy key", "prefix": "zk_live_bbb",
             "expires_at": None, "revoked": False},
            {"id": "k3", "label": "Far future", "prefix": "zk_live_ccc",
             "expires_at": (NOW + timedelta(days=200)).isoformat(), "revoked": False},
        ]
        db.flush()
        out = org_svc.overview(db, org, u)
        cred = [a for a in out["attention"] if a["id"].startswith("cred:")]
        assert len(cred) == 1, cred                       # only the 6-day key
        assert "expires in 6 days" in cred[0]["title"], cred[0]
        assert cred[0]["action"] == "Rotate"
        assert org_svc.developer_ops(db, org)["credentials_active"] == 3
    finally:
        db.rollback()
        db.close()


def test_blocked_event_and_pending_invites_reach_attention():
    db = SessionLocal()
    try:
        org = _org(db)
        u = _user(db, org)
        _event(db, org, u, impact="unrepeatable", recording_enabled=False)
        db.add(Invitation(org_id=org.id, email="new@example.com", role="host",
                         token_hash=uuid.uuid4().hex, status="pending",
                         expires_at=NOW + timedelta(days=7)))
        db.flush()
        out = org_svc.overview(db, org, u)
        ids = [a["id"] for a in out["attention"]]
        assert any(i.startswith("event:") for i in ids), ids
        assert "invites" in ids, ids
        # Critical items sort above informational ones.
        assert out["attention"][0]["severity"] == "critical"
    finally:
        db.rollback()
        db.close()


# ── stage usage ───────────────────────────────────────────────────────────────

def test_stage_usage_reflects_what_the_org_actually_does():
    """A brand-new org uses only Secure; adding sessions/recordings/analytics/keys lights
    the corresponding stages. Dimming the rest is the point — a green Preserve tick means
    nothing to an org that has never recorded."""
    db = SessionLocal()
    try:
        org = _org(db)
        u = _user(db, org)
        fresh = {s["stage"]: s["in_use"] for s in org_svc.stage_health(db, org.id, NOW - timedelta(hours=24))}
        assert fresh["secure"] is True
        assert not any(v for k, v in fresh.items() if k != "secure"), fresh

        ev = _event(db, org, u)
        _session(db, org, ev, status="live", started_at=NOW - timedelta(hours=1))
        db.add_all([
            LiveRecording(event_id=ev.id, org_id=org.id, status="stopped", enforced=True,
                         file_url="path/to.mp4"),
            AnalyticsSnapshot(event_id=ev.id, org_id=org.id, viewers=10, participants=12),
        ])
        org.api_keys = [{"id": "k", "label": "l", "prefix": "p", "revoked": False}]
        db.flush()

        used = {s["stage"]: s["in_use"] for s in org_svc.stage_health(db, org.id, NOW - timedelta(hours=24))}
        for stage in ("contribute", "ingest", "deliver", "produce", "preserve", "understand", "platform"):
            assert used[stage] is True, (stage, used)
    finally:
        db.rollback()
        db.close()


def test_service_health_ignores_stages_the_org_does_not_use():
    """Storage is `not_configured` platform-wide, but an org that never records must not be
    reported as degraded because of it."""
    db = SessionLocal()
    try:
        org = _org(db)
        _user(db, org)
        stages = org_svc.stage_health(db, org.id, NOW - timedelta(hours=24))
        preserve = next(s for s in stages if s["stage"] == "preserve")
        assert preserve["in_use"] is False
        assert org_svc.service_health(stages)["status"] == "ok"
    finally:
        db.rollback()
        db.close()


# ── sessions & media ──────────────────────────────────────────────────────────

def test_session_modes_and_states():
    db = SessionLocal()
    try:
        org = _org(db)
        u = _user(db, org)
        ev = _event(db, org, u)
        _session(db, org, ev, status="live", started_at=NOW - timedelta(hours=2))
        _session(db, org, ev, status="paused", started_at=NOW - timedelta(minutes=18))
        _session(db, org, ev, status="ended", started_at=NOW - timedelta(days=1),
                ended_at=NOW - timedelta(days=1) + timedelta(hours=1))
        db.flush()
        out = org_svc.sessions(db, org.id, NOW - timedelta(days=7))
        assert out["active"] == 2 and out["live"] == 1 and out["paused"] == 1
        modes = {i["mode"] for i in out["items"]}
        assert modes == {"live", "paused", "ended"}, modes
        assert any(i["state"] == "Archived" for i in out["items"])
    finally:
        db.rollback()
        db.close()


def test_test_org_sessions_report_test_mode():
    db = SessionLocal()
    try:
        org = _org(db, is_test=True)
        u = _user(db, org)
        ev = _event(db, org, u)
        _session(db, org, ev, status="live", started_at=NOW - timedelta(hours=1))
        db.flush()
        out = org_svc.sessions(db, org.id, NOW - timedelta(days=1))
        assert out["items"][0]["mode"] == "test", out["items"][0]
    finally:
        db.rollback()
        db.close()


def test_media_assets_separate_ready_processing_and_uncaptured():
    db = SessionLocal()
    try:
        org = _org(db)
        u = _user(db, org)
        ev = _event(db, org, u)
        db.add_all([
            LiveRecording(event_id=ev.id, org_id=org.id, status="stopped", enforced=True),
            LiveRecording(event_id=ev.id, org_id=org.id, status="stopped", enforced=True),
            LiveRecording(event_id=ev.id, org_id=org.id, status="recording", enforced=True),
            # Row exists but LiveKit never captured it — must not count as ready.
            LiveRecording(event_id=ev.id, org_id=org.id, status="stopped", enforced=False),
        ])
        db.flush()
        m = org_svc.media_assets(db, org.id)
        assert m == {"ready": 2, "processing": 1, "not_captured": 1, "total": 4}, m
    finally:
        db.rollback()
        db.close()


# ── readiness states ──────────────────────────────────────────────────────────

def test_readiness_state_distinguishes_not_started_from_in_progress():
    db = SessionLocal()
    try:
        org = _org(db)
        u = _user(db, org)
        bare = _event(db, org, u, impact="high", recording_enabled=False)
        started = _event(db, org, u, impact="high", recording_enabled=True)
        db.add(EventAssignment(event_id=started.id, user_id=u.id, role="host"))
        db.flush()

        out = {e["id"]: e for e in org_svc.upcoming_events(db, org.id)}
        assert out[str(bare.id)]["readiness_state"] == "not_started", out[str(bare.id)]
        assert out[str(started.id)]["readiness_state"] in ("in_progress", "passed"), out[str(started.id)]
    finally:
        db.rollback()
        db.close()


# ── workspace + posture ───────────────────────────────────────────────────────

def test_single_implicit_workspace():
    db = SessionLocal()
    try:
        org = _org(db, name="Northwind Media")
        ws = org_svc.workspaces(org)
        assert len(ws) == 1 and ws[0]["slug"] == "production"
        assert ws[0]["name"] == "Northwind Media" and ws[0]["is_default"] is True
    finally:
        db.rollback()
        db.close()


def test_security_posture_reads_real_org_settings():
    db = SessionLocal()
    try:
        org = _org(db)
        org.security = {"enforce_sso": True, "require_2fa": True, "allowed_domains": "acme.com"}
        org.domain_verified = True
        db.add(Invitation(org_id=org.id, email="p@example.com", role="viewer",
                         token_hash=uuid.uuid4().hex, status="pending",
                         expires_at=NOW + timedelta(days=3)))
        db.add(SupportTicket(org_id=org.id, subject="urgent", message="m",
                            status="open", priority="urgent"))
        db.flush()
        s = org_svc.security_support(db, org)
        assert s["sso_enforced"] and s["two_factor_required"]
        assert s["allowed_domains"] == "acme.com" and s["domain_verified"]
        assert s["pending_members"] == 1 and s["open_cases"] == 1 and s["urgent_cases"] == 1
        # Unmodelled facts stay None rather than defaulting to a reassuring zero.
        assert s["open_findings"] is None and s["maintenance_window"] is None
    finally:
        db.rollback()
        db.close()


def test_overview_payload_is_complete_on_every_range():
    db = SessionLocal()
    try:
        org = _org(db)
        u = _user(db, org)
        required = {"generated_at", "window", "organization", "workspace", "workspaces",
                    "lifecycle", "service_health", "sessions", "media_assets",
                    "entitlements", "api", "attention", "upcoming_events",
                    "developer_ops", "security_support", "gaps"}
        for r in ("1h", "24h", "7d", "30d"):
            out = org_svc.overview(db, org, u, range_=r)
            assert required <= set(out), required - set(out)
            assert len(out["lifecycle"]) == 8
            assert out["window"]["range"] == r
            assert out["window"]["since"] < out["window"]["until"]
        assert len(org_svc.ORG_GAPS) == 11
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(tests)} org tests passed (all rolled back)")
