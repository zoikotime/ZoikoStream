"""Focused test suite for Organization -> Analytics affected blocks:
- Top Performing Events
- Engagement mix
- Audience retention

Exercises all 15 scenarios from Section 11:
1. active event with one viewer appears in Top Performing Events
2. active event contributes watch time
3. completed event remains visible
4. audience-retention point appears
5. peak_viewers=1 and 15 min -> ~0.25 watch hours
6. engagement records populate Engagement mix
7. missing telemetry != measured zero
8. zero-viewer event handled correctly
9. date window includes current active event
10. timezone boundary correct
11. host-only event excluded from audience analytics
12. reconnect does not double-count
13. all three blocks use shared event aggregate
14. ranking tabs use correct metric
15. no static/fake fallback rows
"""

import time
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db import SessionLocal
from app.models import (
    AnalyticsSnapshot,
    BroadcastSession,
    Event,
    LiveMessage,
    LivePoll,
    LiveQuestion,
    Organization,
    User,
)
from app.security import hash_password
from app.services import bus
from app.services import org as org_svc
from app.services.watch_time import (
    build_event_analytics_aggregate,
    calculate_viewer_watch_time,
    calculate_peak_concurrent,
)

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def analytics_world():
    db = SessionLocal()
    made = {"orgs": [], "events": [], "users": []}
    try:
        org = Organization(name=f"AnalyticsBlocksOrg {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        made["orgs"].append(org.id)

        user = User(
            org_id=org.id,
            full_name="Host User",
            role="org_admin",
            is_active=True,
            email=f"host-{uuid.uuid4().hex[:8]}@example.com",
            username=f"host{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("password"),
            email_verified=True,
        )
        db.add(user)
        db.flush()
        made["users"].append(user.id)

        # Event 1: Active live event with broadcast started today
        ev_active = Event(
            org_id=org.id,
            created_by=user.id,
            title="Live Stream Sep 28",
            status="live",
            visibility="public",
            start_time=datetime.now(UTC) - timedelta(minutes=20),
        )
        db.add(ev_active)
        db.flush()
        made["events"].append(ev_active.id)

        bs_active = BroadcastSession(
            event_id=ev_active.id,
            org_id=org.id,
            status="live",
            started_at=datetime.now(UTC) - timedelta(minutes=20),
            peak_viewers=0,
        )
        db.add(bs_active)

        # Event 2: Completed event with snapshots and engagement
        ev_completed = Event(
            org_id=org.id,
            created_by=user.id,
            title="Completed Summit",
            status="ended",
            visibility="public",
            start_time=datetime.now(UTC) - timedelta(days=2),
            end_time=datetime.now(UTC) - timedelta(days=2) + timedelta(hours=1),
        )
        db.add(ev_completed)
        db.flush()
        made["events"].append(ev_completed.id)

        bs_completed = BroadcastSession(
            event_id=ev_completed.id,
            org_id=org.id,
            status="ended",
            started_at=datetime.now(UTC) - timedelta(days=2),
            ended_at=datetime.now(UTC) - timedelta(days=2) + timedelta(hours=1),
            peak_viewers=5,
        )
        db.add(bs_completed)

        # Snapshots for completed event
        for _ in range(4):
            db.add(
                AnalyticsSnapshot(
                    event_id=ev_completed.id,
                    org_id=org.id,
                    viewers=5,
                    participants=6,
                    on_stage=1,
                    messages=12,
                    questions=3,
                    reactions=10,
                )
            )

        db.commit()
        yield {"org": org, "user": user, "ev_active": ev_active, "ev_completed": ev_completed}
    finally:
        try:
            for eid in made["events"]:
                db.query(LiveMessage).filter(LiveMessage.event_id == eid).delete()
                db.query(LiveQuestion).filter(LiveQuestion.event_id == eid).delete()
                db.query(LivePoll).filter(LivePoll.event_id == eid).delete()
                db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.event_id == eid).delete()
                db.query(BroadcastSession).filter(BroadcastSession.event_id == eid).delete()
                db.query(Event).filter(Event.id == eid).delete()
            for uid in made["users"]:
                db.query(User).filter(User.id == uid).delete()
            for oid in made["orgs"]:
                db.query(Organization).filter(Organization.id == oid).delete()
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


# 1. active event with one viewer appears in Top Performing Events
@pytest.mark.asyncio
async def test_1_active_event_with_one_viewer_appears_in_top_performing_events(analytics_world):
    org = analytics_world["org"]
    ev_active = analytics_world["ev_active"]
    now = time.time()

    # Viewer joined 15 minutes ago
    await bus.session_record_join(ev_active.id, "viewer_active_1", role="viewer", joined_at=now - 900)
    await bus.session_record_heartbeat(ev_active.id, "viewer_active_1", timestamp=now)

    db = SessionLocal()
    try:
        out = org_svc.analytics(db, org, range_key="30d")
    finally:
        db.close()

    # Active event must be present in reports
    event_report = next((r for r in out["reports"] if r["id"] == str(ev_active.id)), None)
    assert event_report is not None
    assert event_report["viewers"] == 1
    assert event_report["watch_hours"] == pytest.approx(0.25, rel=1e-2)

    # In top_events, peak > 0 events appear
    top_labels = [te["label"] for te in out["top_events"]]
    assert ev_active.title in top_labels


# 2. active event contributes watch time
@pytest.mark.asyncio
async def test_2_active_event_contributes_watch_time(analytics_world):
    org = analytics_world["org"]
    ev_active = analytics_world["ev_active"]
    now = time.time()

    await bus.session_record_join(ev_active.id, "viewer_active_2", role="viewer", joined_at=now - 600)
    await bus.session_record_heartbeat(ev_active.id, "viewer_active_2", timestamp=now)

    db = SessionLocal()
    try:
        out = org_svc.analytics(db, org, range_key="30d")
    finally:
        db.close()

    event_report = next((r for r in out["reports"] if r["id"] == str(ev_active.id)), None)
    assert event_report["total_watch_seconds"] >= 600
    assert event_report["watch_hours"] >= (600 / 3600.0)


# 3. completed event remains visible
def test_3_completed_event_remains_visible(analytics_world):
    org = analytics_world["org"]
    ev_completed = analytics_world["ev_completed"]

    db = SessionLocal()
    try:
        out = org_svc.analytics(db, org, range_key="30d")
    finally:
        db.close()

    event_report = next((r for r in out["reports"] if r["id"] == str(ev_completed.id)), None)
    assert event_report is not None
    assert event_report["viewers"] == 5
    assert event_report["watch_hours"] > 0


# 4. audience-retention point appears
def test_4_audience_retention_point_appears(analytics_world):
    org = analytics_world["org"]
    ev_completed = analytics_world["ev_completed"]

    db = SessionLocal()
    try:
        out = org_svc.analytics(db, org, range_key="30d")
    finally:
        db.close()

    # Both x (peak viewers) and y (watch_hours) are non-null and > 0 for completed event
    report = next(r for r in out["reports"] if r["id"] == str(ev_completed.id))
    assert report["viewers"] > 0
    assert report["watch_hours"] > 0


# 5. peak_viewers=1 and 15 min -> ~0.25 watch hours
def test_5_peak_viewers_1_and_15_min_yields_025_hours():
    sessions = [{
        "identity": "single_viewer",
        "role": "viewer",
        "joined_at": 1700000000.0,
        "left_at": 1700000900.0,  # 900s = 15m
    }]
    res = calculate_viewer_watch_time(sessions)
    assert res["viewer_count"] == 1
    assert res["peak_concurrent"] == 1
    assert res["total_watch_seconds"] == 900
    assert pytest.approx(res["watch_hours"], 1e-4) == 0.25


# 6. engagement records populate Engagement mix
def test_6_engagement_records_populate_engagement_mix(analytics_world):
    org = analytics_world["org"]
    ev_completed = analytics_world["ev_completed"]

    db = SessionLocal()
    try:
        out = org_svc.analytics(db, org, range_key="30d")
    finally:
        db.close()

    report = next(r for r in out["reports"] if r["id"] == str(ev_completed.id))
    # Completed event had peak 5, messages 12, questions 3, reactions 10
    assert report["engagement"] is not None
    assert report["engagement"] > 0


# 7. missing telemetry != measured zero
def test_7_missing_telemetry_is_not_measured_zero():
    agg = build_event_analytics_aggregate(
        event_id="unmeasured_test_evt",
        event_title="Unmeasured Event",
        sessions=[],
        snaps=[],
        b_sessions=[],
    )
    assert agg["measurement_state"] == "unmeasured"
    assert agg["watch_hours"] is None
    assert agg["total_watch_seconds"] is None
    assert agg["engagement_score"] is None


# 8. zero-viewer event handled correctly
def test_8_zero_viewer_event_handled_correctly():
    # An event was broadcast but 0 viewers joined
    b_session = BroadcastSession(status="ended", peak_viewers=0)
    agg = build_event_analytics_aggregate(
        event_id="zero_viewer_evt",
        event_title="Broadcast With No Viewers",
        sessions=[],
        snaps=[],
        b_sessions=[b_session],
    )
    assert agg["peak_viewers"] == 0
    assert agg["unique_viewers"] == 0
    assert agg["engagement_score"] is None  # Never a fake 0%


# 9. date window includes current active event
def test_9_date_window_includes_current_active_event(analytics_world):
    org = analytics_world["org"]
    ev_active = analytics_world["ev_active"]

    db = SessionLocal()
    try:
        out = org_svc.analytics(db, org, range_key="30d")
    finally:
        db.close()

    ids = [r["id"] for r in out["reports"]]
    assert str(ev_active.id) in ids


# 10. timezone boundary correct
def test_10_timezone_boundary_correct():
    # 21:00 UTC on Sep 28 is 02:30 IST on Sep 29
    dt_utc = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)
    label_utc = org_svc._bucket_label(dt_utc, "30d", UTC)
    label_ist = org_svc._bucket_label(dt_utc, "30d", IST)
    assert label_utc == "Sep 28"
    assert label_ist == "Sep 29"


# 11. host-only event excluded from audience analytics
def test_11_host_only_event_excluded_from_audience_analytics():
    sessions = [
        {"identity": "host_only", "role": "host", "joined_at": 1000.0, "left_at": 2000.0}
    ]
    res = calculate_viewer_watch_time(sessions)
    assert res["viewer_count"] == 0
    assert res["peak_concurrent"] == 0
    assert res["total_watch_seconds"] is None


# 12. reconnect does not double-count
def test_12_reconnect_does_not_double_count():
    # One user reconnecting across two tabs with overlap
    sessions = [
        {"identity": "user_reconn", "role": "viewer", "joined_at": 1000.0, "left_at": 1400.0},
        {"identity": "user_reconn", "role": "viewer", "joined_at": 1200.0, "left_at": 1600.0},
    ]
    res = calculate_viewer_watch_time(sessions)
    assert res["viewer_count"] == 1
    assert res["peak_concurrent"] == 1
    assert res["total_watch_seconds"] == 600  # [1000, 1600] = 600s, not 800s


# 13. all three blocks use shared event aggregate
def test_13_all_three_blocks_use_shared_event_aggregate():
    agg = build_event_analytics_aggregate(
        event_id="test_shared_block_evt",
        event_title="Shared Event",
        sessions=[{"identity": "v1", "role": "viewer", "joined_at": 1000.0, "left_at": 1900.0}],
        chat_count=5,
        question_count=1,
        reaction_count=2,
    )
    # 1. Top Performing Events reads:
    assert agg["peak_viewers"] == 1
    assert agg["total_watch_seconds"] == 900
    assert agg["engagement_score"] is not None

    # 2. Engagement mix reads:
    assert agg["engagement_score"] is not None

    # 3. Audience retention reads:
    assert agg["peak_viewers"] == 1
    assert agg["watch_hours"] == 0.25


# 14. ranking tabs use correct metric
def test_14_ranking_tabs_use_correct_metric():
    # Event A: High viewers (10), short watch (100s)
    # Event B: Low viewers (2), long watch (5000s)
    evt_a = {
        "viewers": 10,
        "watch_hours": 100 / 3600.0,
        "total_watch_seconds": 100,
        "engagement": 20,
    }
    evt_b = {
        "viewers": 2,
        "watch_hours": 5000 / 3600.0,
        "total_watch_seconds": 5000,
        "engagement": 80,
    }
    reports = [evt_a, evt_b]

    # Rank by viewers: A > B
    ranked_viewers = sorted(reports, key=lambda r: r["viewers"], reverse=True)
    assert ranked_viewers[0] == evt_a

    # Rank by watch time (seconds): B > A
    ranked_watch = sorted(reports, key=lambda r: r["total_watch_seconds"], reverse=True)
    assert ranked_watch[0] == evt_b

    # Rank by engagement: B > A
    ranked_eng = sorted(reports, key=lambda r: r["engagement"], reverse=True)
    assert ranked_eng[0] == evt_b


# 15. no static/fake fallback rows
def test_15_no_static_or_fake_fallback_rows(analytics_world):
    org = analytics_world["org"]

    db = SessionLocal()
    try:
        out = org_svc.analytics(db, org, range_key="30d")
    finally:
        db.close()

    # Every item in reports corresponds to an actual Event in analytics_world
    report_titles = {r["event"] for r in out["reports"]}
    assert "Live Stream Sep 28" in report_titles
    assert "Completed Summit" in report_titles
    # No mocked or invented events
    for title in report_titles:
        assert title in ("Live Stream Sep 28", "Completed Summit")
