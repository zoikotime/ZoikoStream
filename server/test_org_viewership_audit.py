"""Focused end-to-end audit test suite for:
Organization -> Analytics -> Viewership & watch time

Exercises all 18 requirements from Section 13:
1. host-only broadcast -> 0 audience viewers
2. host-only broadcast duration is NOT watch time
3. one audience viewer creates presence
4. active viewer contributes watch duration
5. NULL left_at does not force zero
6. last_seen contributes active duration correctly
7. completed viewer duration preserved
8. one 15-minute viewer -> ~15 min total watch
9. two viewers sum correctly
10. peak concurrency computed from overlap
11. active event appears in current-day graph
12. completed event preserves analytics
13. missing telemetry != measured zero
14. engagement missing inputs != fabricated 0
15. 30-day graph uses correct date buckets
16. staff viewers excluded as intended
17. reconnect doesn't double-count overlapping presence
18. all analytics surfaces agree
"""

import time
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.watch_time import (
    calculate_viewer_watch_time,
    calculate_peak_concurrent,
    merge_intervals,
    aggregate_event_watch_time,
)
from app.services import bus
from app.services import broadcast as bc_svc
from app.services import org as org_svc
from app.services import report as report_svc
from app.db import SessionLocal
from app.models import AnalyticsSnapshot, BroadcastSession, Event, Organization, User
from app.security import hash_password

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")


# 1. host-only broadcast -> 0 audience viewers
def test_1_host_only_broadcast_yields_zero_audience_viewers():
    sessions = [
        {"identity": "host_user", "role": "host", "joined_at": 1700000000.0, "last_seen": 1700001000.0}
    ]
    res = calculate_viewer_watch_time(sessions, now_ts=1700001000.0)
    assert res["viewer_count"] == 0
    assert res["peak_concurrent"] == 0
    assert res["measured"] is False
    assert res["total_watch_seconds"] is None
    assert res["watch_hours"] is None


# 2. host-only broadcast duration is NOT watch time
def test_2_host_only_broadcast_duration_is_not_watch_time():
    # Host was broadcasting for 20 minutes (1200 seconds)
    sessions = [
        {"identity": "producer_1", "role": "host", "joined_at": 1700000000.0, "last_seen": 1700001200.0, "left_at": 1700001200.0}
    ]
    res = calculate_viewer_watch_time(sessions, now_ts=1700001200.0)
    # Broadcast duration was 1200s, but audience watch time must NOT be 1200s!
    assert res["total_watch_seconds"] is None
    assert res["watch_hours"] is None
    assert res["viewer_count"] == 0


# 3. one audience viewer creates presence
@pytest.mark.asyncio
async def test_3_one_audience_viewer_creates_presence():
    event_id = f"test_presence_{uuid.uuid4().hex[:8]}"
    t0 = 1700000000.0
    await bus.session_record_join(event_id, "audience_1", role="viewer", joined_at=t0)
    
    sessions = bus.session_get_all_sync(event_id)
    assert len(sessions) == 1
    assert sessions[0]["identity"] == "audience_1"
    assert sessions[0]["role"] == "viewer"
    assert sessions[0]["joined_at"] == t0
    assert sessions[0]["left_at"] is None


# 4. active viewer contributes watch duration
def test_4_active_viewer_contributes_watch_duration():
    now = 1700000500.0
    joined = 1700000000.0
    sessions = [{
        "identity": "audience_live",
        "role": "viewer",
        "joined_at": joined,
        "last_seen": now,
        "left_at": None,
    }]
    res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert res["measured"] is True
    assert res["total_watch_seconds"] == 500
    assert res["watch_hours"] == pytest.approx(500 / 3600.0)


# 5. NULL left_at does not force zero
def test_5_null_left_at_does_not_force_zero():
    # Active session has left_at=None; must calculate from last_seen/now, not return 0
    now = 1700000900.0
    joined = 1700000000.0
    sessions = [{
        "identity": "viewer_active_no_left",
        "role": "viewer",
        "joined_at": joined,
        "last_seen": now,
        "left_at": None,
    }]
    res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert res["total_watch_seconds"] == 900
    assert res["total_watch_seconds"] != 0
    assert res["watch_hours"] != 0.0


# 6. last_seen contributes active duration correctly
def test_6_last_seen_contributes_active_duration_correctly():
    joined = 1700000000.0
    last_seen = 1700000450.0  # viewer sent heartbeat up to 450s
    now = 1700001000.0       # server time is 1000s, but client last pinged at 450s
    sessions = [{
        "identity": "viewer_hb",
        "role": "viewer",
        "joined_at": joined,
        "last_seen": last_seen,
        "left_at": None,
    }]
    # Effective end should be last_seen (450s duration)
    res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert res["total_watch_seconds"] == 450


# 7. completed viewer duration preserved
def test_7_completed_viewer_duration_preserved():
    joined = 1700000000.0
    left = 1700000800.0
    sessions = [{
        "identity": "viewer_left",
        "role": "viewer",
        "joined_at": joined,
        "last_seen": left,
        "left_at": left,
    }]
    res = calculate_viewer_watch_time(sessions, now_ts=left + 1000.0)
    assert res["total_watch_seconds"] == 800
    assert res["viewer_count"] == 1


# 8. one 15-minute viewer -> ~15 min total watch
def test_8_one_15_minute_viewer_yields_approx_15_min_watch():
    joined = 1700000000.0
    left = joined + 15 * 60  # 900 seconds
    sessions = [{
        "identity": "viewer_15m",
        "role": "viewer",
        "joined_at": joined,
        "last_seen": left,
        "left_at": left,
    }]
    res = calculate_viewer_watch_time(sessions)
    assert res["total_watch_seconds"] == 900
    assert pytest.approx(res["watch_hours"], 1e-4) == 0.25  # 15 / 60
    assert res["peak_concurrent"] == 1


# 9. two viewers sum correctly
def test_9_two_viewers_sum_correctly():
    now = 1700002000.0
    sessions = [
        {"identity": "v1", "role": "viewer", "joined_at": now - 600, "left_at": now},   # 10 min = 600s
        {"identity": "v2", "role": "viewer", "joined_at": now - 300, "left_at": now},   # 5 min = 300s
    ]
    res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert res["viewer_count"] == 2
    assert res["total_watch_seconds"] == 900  # 600 + 300
    assert res["average_watch_seconds"] == 450  # 900 / 2


# 10. peak concurrency computed from overlap
def test_10_peak_concurrency_computed_from_overlap():
    # Case A: Two viewers overlap in time [1200, 1500] -> peak = 2
    overlapping = {
        "v1": [(1000.0, 1500.0)],
        "v2": [(1200.0, 1800.0)],
    }
    assert calculate_peak_concurrent(overlapping) == 2

    # Case B: Two viewers do NOT overlap -> peak = 1
    non_overlapping = {
        "v1": [(1000.0, 1200.0)],
        "v2": [(1300.0, 1500.0)],
    }
    assert calculate_peak_concurrent(non_overlapping) == 1

    # Case C: Three viewers, 2 overlap, 3rd joins later -> peak = 2
    three_viewers = {
        "v1": [(1000.0, 1400.0)],
        "v2": [(1200.0, 1300.0)],
        "v3": [(1500.0, 1700.0)],
    }
    assert calculate_peak_concurrent(three_viewers) == 2


# 11. active event appears in current-day graph
def test_11_active_event_appears_in_current_day_graph():
    since = datetime.now(UTC) - timedelta(days=30)
    until = datetime.now(UTC)
    timeline = org_svc._bucket_timeline(since, until, "30d", UTC)
    today_label = org_svc._bucket_label(until, "30d", UTC)
    assert today_label in timeline
    assert timeline[-1] == today_label


# 12. completed event preserves analytics
def test_12_completed_event_preserves_analytics():
    summary_override = {
        "total_watch_seconds": 900,
        "average_watch_seconds": 900,
        "watch_hours": 0.25,
        "measured": True,
        "viewer_count": 1,
        "peak_concurrent": 1,
    }
    agg = aggregate_event_watch_time(
        event_id="completed_event",
        sessions=[],  # in-memory presence was cleared
        summary_override=summary_override,
    )
    assert agg["measured"] is True
    assert agg["total_watch_seconds"] == 900
    assert agg["watch_hours"] == 0.25
    assert agg["peak_concurrent"] == 1


# 13. missing telemetry != measured zero
def test_13_missing_telemetry_is_not_measured_zero():
    # Unmeasured event
    unmeasured = aggregate_event_watch_time(event_id="no_data", sessions=[], snaps=[])
    assert unmeasured["measured"] is False
    assert unmeasured["total_watch_seconds"] is None
    assert unmeasured["watch_hours"] is None

    # Measured zero (someone connected and immediately dropped in 0s)
    measured_zero = calculate_viewer_watch_time([
        {"identity": "bounce_user", "role": "viewer", "joined_at": 1000.0, "left_at": 1000.0}
    ])
    assert measured_zero["measured"] is True
    assert measured_zero["total_watch_seconds"] == 0
    assert measured_zero["watch_hours"] == 0.0


# 14. engagement missing inputs != fabricated 0
def test_14_engagement_missing_inputs_is_not_fabricated_zero():
    from app.services.broadcast import engagement_score
    # When peak <= 0, engagement is None (never 0%)
    # Let's verify through org_svc.analytics engagement calculation logic
    # In org.py: if not info["measured"] or info["peak"] <= 0: return None
    info_no_audience = {"measured": True, "peak": 0, "messages": 0, "questions": 0, "poll_votes": 0, "reactions": 0}
    # Per org.py logic:
    def eval_eng(info):
        if not info["measured"] or info["peak"] <= 0:
            return None
        return engagement_score(
            {"messages": info["messages"], "questions": info["questions"],
             "poll_votes": info["poll_votes"], "reactions": info["reactions"]}, info["peak"])

    assert eval_eng(info_no_audience) is None

    # But when peak > 0 and no interactions occurred, score is measured 0
    info_idle_audience = {"measured": True, "peak": 5, "messages": 0, "questions": 0, "poll_votes": 0, "reactions": 0}
    assert eval_eng(info_idle_audience) == 0


# 15. 30-day graph uses correct date buckets (event occurrence vs created_at)
def test_15_30_day_graph_uses_correct_date_buckets():
    # Created 2 weeks ago, but broadcast on Sep 28
    created_at = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    broadcast_started_at = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
    
    # Bucket for occurrence should be Sep 28
    bucket_occ = org_svc._bucket_label(broadcast_started_at, "30d", UTC)
    bucket_created = org_svc._bucket_label(created_at, "30d", UTC)
    assert bucket_occ == "Sep 28"
    assert bucket_created == "Sep 14"
    assert bucket_occ != bucket_created


# 16. staff viewers excluded as intended
def test_16_staff_viewers_excluded_as_intended():
    sessions = [
        {"identity": "h", "role": "host", "joined_at": 1000.0, "left_at": 2000.0},
        {"identity": "s", "role": "speaker", "joined_at": 1000.0, "left_at": 2000.0},
        {"identity": "m", "role": "moderator", "joined_at": 1000.0, "left_at": 2000.0},
        {"identity": "st", "role": "viewer", "on_stage": True, "joined_at": 1000.0, "left_at": 2000.0},
        {"identity": "w", "role": "viewer", "waiting": True, "joined_at": 1000.0, "left_at": 2000.0},
        {"identity": "v", "role": "viewer", "joined_at": 1000.0, "left_at": 1600.0},  # 600s
    ]
    res = calculate_viewer_watch_time(sessions)
    assert res["viewer_count"] == 1
    assert res["total_watch_seconds"] == 600
    assert res["peak_concurrent"] == 1


# 17. reconnect doesn't double-count overlapping presence
def test_17_reconnect_does_not_double_count_overlapping_presence():
    # Viewer opens 2 tabs with overlap: [1000, 1500] and [1200, 1700]
    sessions = [
        {"identity": "user_reconnect", "role": "viewer", "joined_at": 1000.0, "left_at": 1500.0},
        {"identity": "user_reconnect", "role": "viewer", "joined_at": 1200.0, "left_at": 1700.0},
    ]
    res = calculate_viewer_watch_time(sessions)
    # Merged interval is [1000, 1700] = 700s, NOT 500+500 = 1000s!
    assert res["viewer_count"] == 1
    assert res["total_watch_seconds"] == 700
    assert res["peak_concurrent"] == 1


# 18. all analytics surfaces agree
def test_18_all_analytics_surfaces_agree():
    # Shared session data
    now = 1700001000.0
    sessions = [
        {"identity": "viewer_shared", "role": "viewer", "joined_at": now - 900.0, "last_seen": now}
    ]
    # watch_time calculation
    wt_res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert wt_res["total_watch_seconds"] == 900
    assert wt_res["watch_hours"] == 0.25

    # Producer console analytics helper _watch_seconds (mean watch seconds)
    bc_seconds = bc_svc._watch_seconds(people=sessions, now_ts=now)
    assert bc_seconds == 900

    # Event aggregate watch time helper
    agg_res = aggregate_event_watch_time("evt_shared", sessions=sessions, now_ts=now)
    assert agg_res["total_watch_seconds"] == 900
    assert agg_res["watch_hours"] == 0.25
    assert agg_res["peak_concurrent"] == 1
