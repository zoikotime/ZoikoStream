"""Command Center aggregation checks.

Two halves:
  * services.ops._selfcheck() — the pure logic (interval union, bucketing, verdicts, p95).
  * the tests below — the SQL, against the real database inside a transaction that is
    ALWAYS rolled back. Faking the Session here would test nothing: the whole point of
    these functions is the queries.

Run with `python test_ops.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Event,
    EventAssignment,
    GovernanceRecord,
    Incident,
    Organization,
    User,
)
from app.services import ops

NOW = datetime.now(timezone.utc)


def _admin(db):
    return db.scalar(select(User).where(User.role == "super_admin"))


def _org(db, name=None, **kw):
    org = Organization(name=name or f"ops-test-{uuid.uuid4().hex[:8]}", status="active", **kw)
    db.add(org)
    db.flush()
    return org


def _event(db, org, admin, **kw):
    ev = Event(org_id=org.id, created_by=admin.id, title="Ops Test Event",
               status="scheduled", start_time=NOW + timedelta(hours=4), **kw)
    db.add(ev)
    db.flush()
    return ev


def test_availability_reflects_recorded_incidents():
    """A 6h incident inside a 24h window must show ~75% availability for that stage+region,
    and must not touch any other stage."""
    db = SessionLocal()
    try:
        db.add(Incident(ref=f"INC-TEST-{uuid.uuid4().hex[:6]}", title="Route degraded",
                        severity="sev2", kind="operational", stage="deliver", region="eu",
                        status="resolved", started_at=NOW - timedelta(hours=8),
                        resolved_at=NOW - timedelta(hours=2)))
        db.flush()

        avail = ops.availability(db, NOW - timedelta(hours=24), NOW)
        assert 74.0 < avail[("deliver", "eu")] < 76.0, avail[("deliver", "eu")]
        # Other regions of the same stage are untouched by a region-scoped incident.
        assert avail[("deliver", "na")] == 100.0
        # Other stages are untouched entirely.
        assert avail[("ingest", "eu")] == 100.0
    finally:
        db.rollback()
        db.close()


def test_global_incident_hits_every_region():
    """region=NULL means platform-wide impact, so every region must degrade."""
    db = SessionLocal()
    try:
        db.add(Incident(ref=f"INC-TEST-{uuid.uuid4().hex[:6]}", title="Global auth outage",
                        severity="sev1", kind="security", stage="secure", region=None,
                        status="resolved", started_at=NOW - timedelta(hours=12),
                        resolved_at=NOW - timedelta(hours=6)))
        db.flush()
        avail = ops.availability(db, NOW - timedelta(hours=24), NOW)
        for region in ("na", "eu", "apac", "sa"):
            assert 74.0 < avail[("secure", region)] < 76.0, (region, avail[("secure", region)])
    finally:
        db.rollback()
        db.close()


def test_stage_health_escalates_on_open_incident():
    """An open sev1 must drive its stage to `down` even when the probes say ok."""
    db = SessionLocal()
    try:
        health = {"overall": "ok", "services": [
            {"id": "api", "name": "API", "status": "ok"},
            {"id": "database", "name": "Database", "status": "ok"},
        ]}
        before = ops.stage_health(db, health, NOW - timedelta(hours=1), ("platform",))
        assert before[0]["status"] == "ok", before[0]

        db.add(Incident(ref=f"INC-TEST-{uuid.uuid4().hex[:6]}", title="API down",
                        severity="sev1", kind="operational", stage="platform",
                        status="open", started_at=NOW - timedelta(minutes=5)))
        db.flush()
        after = ops.stage_health(db, health, NOW - timedelta(hours=1), ("platform",))
        assert after[0]["status"] == "down", after[0]
        assert after[0]["open_incidents"] == 1
    finally:
        db.rollback()
        db.close()


def test_unintegrated_service_is_not_healthy():
    """A stage backed only by an unconfigured dependency must not read as green — and must
    not read as an outage either."""
    db = SessionLocal()
    try:
        health = {"overall": "ok", "services": [
            {"id": "storage", "name": "Storage", "status": "not_configured"},
        ]}
        out = ops.stage_health(db, health, NOW - timedelta(hours=1), ("preserve",))
        assert out[0]["status"] == "not_configured", out[0]
    finally:
        db.rollback()
        db.close()


def test_readiness_gates_scale_with_impact():
    """The same missing host is a BLOCK on an unrepeatable event and only a CONDITIONAL
    pass on a standard one."""
    db = SessionLocal()
    try:
        admin = _admin(db)
        org = _org(db)

        unrepeatable = _event(db, org, admin, impact="unrepeatable", recording_enabled=True)
        readiness = ops.event_readiness(db, include_test=True, limit=50)
        mine = next(e for e in readiness if e["id"] == str(unrepeatable.id))
        assert mine["verdict"] == "blocked", mine
        assert "Host assigned" in mine["failing"]

        # Assign a host -> every mandatory gate passes. There is no longer a separate
        # "Moderator assigned" gate: the role was retired, so that gate could never pass again
        # and would have permanently blocked every unrepeatable event (see services/ops._GATES).
        db.add(EventAssignment(event_id=unrepeatable.id, user_id=admin.id, role="host"))
        db.flush()
        mine = next(e for e in ops.event_readiness(db, include_test=True, limit=50)
                    if e["id"] == str(unrepeatable.id))
        assert mine["verdict"] == "passed", mine

        # A single-path override on the same event blocks it again (redundancy is mandatory
        # for unrepeatable events).
        db.add(GovernanceRecord(kind="single_path_override", org_id=org.id,
                               event_id=unrepeatable.id, status="open"))
        db.flush()
        mine = next(e for e in ops.event_readiness(db, include_test=True, limit=50)
                    if e["id"] == str(unrepeatable.id))
        assert mine["verdict"] == "blocked", mine
    finally:
        db.rollback()
        db.close()


def test_high_impact_only_and_suspended_account():
    db = SessionLocal()
    try:
        admin = _admin(db)
        org = _org(db)
        standard = _event(db, org, admin, impact="standard")
        high = _event(db, org, admin, impact="high")
        db.flush()

        ids = {e["id"] for e in ops.event_readiness(db, include_test=True, limit=50)}
        assert str(high.id) in ids
        assert str(standard.id) not in ids, "standard events must not reach the high-impact list"

        # A suspended account fails the account gate, which is mandatory at `high`.
        org.status = "suspended"
        db.flush()
        mine = next(e for e in ops.event_readiness(db, include_test=True, limit=50)
                    if e["id"] == str(high.id))
        assert mine["verdict"] == "blocked", mine
        assert "Account in good standing" in mine["failing"]
    finally:
        db.rollback()
        db.close()


def test_test_orgs_are_excluded_unless_included():
    """This is what the console's "Include test mode" toggle actually switches."""
    db = SessionLocal()
    try:
        admin = _admin(db)
        org = _org(db, is_test=True)
        ev = _event(db, org, admin, impact="unrepeatable")
        db.flush()

        excluded = {e["id"] for e in ops.event_readiness(db, include_test=False, limit=50)}
        included = {e["id"] for e in ops.event_readiness(db, include_test=True, limit=50)}
        assert str(ev.id) not in excluded
        assert str(ev.id) in included
    finally:
        db.rollback()
        db.close()


def test_action_queues_and_governance_count_real_rows():
    db = SessionLocal()
    try:
        admin = _admin(db)
        org = _org(db)

        db.add_all([
            Incident(ref=f"INC-TEST-{uuid.uuid4().hex[:6]}", title="Playback degraded",
                     severity="sev2", kind="operational", stage="deliver", status="open",
                     started_at=NOW - timedelta(minutes=20)),
            Incident(ref=f"INC-TEST-{uuid.uuid4().hex[:6]}", title="Access-grant spike",
                     severity="sev3", kind="security", stage="secure", status="monitoring",
                     started_at=NOW - timedelta(minutes=41)),
            GovernanceRecord(kind="break_glass", org_id=org.id, status="open",
                            due_at=NOW + timedelta(hours=40), detail="Emergency elevation"),
            GovernanceRecord(kind="legal_hold", org_id=org.id, status="open"),
            GovernanceRecord(kind="entitlement_override", org_id=org.id, status="pending",
                            detail="Seat limit lifted"),
            GovernanceRecord(kind="single_path_override", org_id=org.id, status="open",
                            opened_at=NOW),
            GovernanceRecord(kind="usage_export", org_id=org.id, status="delivered"),
            GovernanceRecord(kind="usage_export", org_id=org.id, status="delivered"),
            GovernanceRecord(kind="usage_export", org_id=org.id, status="failed"),
        ])
        db.flush()

        queues = {q["key"]: q for q in ops.action_queues(db, readiness=[])}
        assert queues["operational"]["count"] >= 1
        assert queues["security"]["count"] >= 2, queues["security"]  # incident + break-glass
        assert queues["commercial"]["count"] >= 1
        # Items carry a route so the console's links resolve.
        assert all(i["to"].startswith("/admin/") for q in queues.values() for i in q["items"])

        gov = ops.governance_exposure(db)
        assert gov["legal_holds"] >= 1
        assert gov["entitlement_overrides_pending"] >= 1
        assert gov["break_glass_under_review"] >= 1
        assert gov["single_path_overrides_quarter"] >= 1
        # 2 delivered of 3 -> 66.7% on time.
        assert gov["usage_export_total"] >= 3
        assert gov["usage_export_on_time_pct"] is not None
    finally:
        db.rollback()
        db.close()


def test_blocked_event_reaches_the_operational_queue():
    db = SessionLocal()
    try:
        readiness = [{"id": "e1", "title": "Global Memorial Broadcast", "impact": "unrepeatable",
                      "verdict": "blocked", "failing": ["Host assigned"], "start_time": NOW}]
        queues = {q["key"]: q for q in ops.action_queues(db, readiness)}
        titles = [i["title"] for i in queues["operational"]["items"]]
        assert any("blocked" in t.lower() for t in titles), titles
    finally:
        db.rollback()
        db.close()


def test_command_center_payload_is_complete():
    """Every region the page renders must be present, on every filter combination."""
    db = SessionLocal()
    try:
        admin = _admin(db)
        required = {"generated_at", "window", "lifecycle", "regions", "kpis", "attention",
                    "incidents", "action_queues", "governance", "privileged_activity",
                    "upcoming_events", "elevation"}
        required_kpis = {"platform_health", "live_sessions", "at_risk_sessions",
                         "concurrent_audience", "playback_quality", "api_health"}
        for range_ in ("live", "1h", "24h", "7d"):
            for region in (None, "na", "eu", "apac", "sa"):
                for scope in ("core_live", "core", "live"):
                    out = ops.command_center(db, admin, range_=range_, region=region,
                                            scope=scope, include_test=True)
                    assert required <= set(out), required - set(out)
                    assert required_kpis <= set(out["kpis"])
                    assert len(out["lifecycle"]) == len(ops.SCOPES[scope])
                    assert out["window"]["range"] == range_
    finally:
        db.rollback()
        db.close()


def test_console_state_shape():
    db = SessionLocal()
    try:
        out = ops.console_state(db, _admin(db))
        assert set(out) == {"health", "badges", "elevation", "user"}
        assert set(out["badges"]) == {"live_operations", "event_readiness", "system_status"}
        assert out["health"]["overall"] in ("ok", "warn", "down")
        assert all(isinstance(v, int) for v in out["badges"].values())
    finally:
        db.rollback()
        db.close()


def test_search_finds_real_entities_only():
    db = SessionLocal()
    try:
        org = _org(db, name="Zzq Unlikely Search Target")
        db.flush()
        hits = ops.search(db, "Zzq Unlikely")
        assert any(h["label"] == org.name and h["kind"] == "Organization" for h in hits), hits
        assert ops.search(db, "qqqzzzxxx-no-such-entity") == []
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    ops._selfcheck()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(tests)} db tests passed (all rolled back)")
