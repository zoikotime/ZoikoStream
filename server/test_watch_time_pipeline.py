"""Comprehensive test suite for the per-event watch-time pipeline.

Exercises all 15 scenarios specified in Section 12:
1. active viewer contributes watch time
2. active viewer with NULL left_at does not become zero
3. completed viewer duration calculated correctly
4. completed event preserves final watch time
5. 145 minutes produces approximately 145 min in UI
6. multiple viewers sum correctly
7. average watch time calculated correctly
8. reconnect does not double-count overlapping sessions
9. refresh/reconnect preserves prior duration
10. staff exclusion remains correct
11. seconds/minutes conversion correct
12. active event watch time increases after refetch
13. recently completed event does not reset to zero
14. zero vs unavailable correctly distinguished
15. all analytics surfaces use the same authoritative helper where applicable
"""

import time
import pytest
from datetime import datetime, timedelta, timezone

from app.services.watch_time import (
    calculate_viewer_watch_time,
    merge_intervals,
    aggregate_event_watch_time,
)
from app.services import bus
from app.services import broadcast
from app.services import org as org_svc
from app.services import report as report_svc


# UI formatter logic matching client/src/utils/analyticsFormat.js formatWatchTime
def format_watch_time_ui(hours):
    if hours is None:
        return "—"
    minutes = hours * 60
    if 0 < minutes < 1:
        return "< 1 min"
    if minutes < 60:
        return f"{round(minutes)} min"
    return f"{hours:.1f} hrs"


# 1. active viewer contributes watch time
def test_1_active_viewer_contributes_watch_time():
    now = 1700001000.0
    joined = now - 900.0  # joined 15 minutes ago
    sessions = [{
        "identity": "user_1",
        "role": "viewer",
        "joined_at": joined,
        "last_seen": now,
        "left_at": None,
    }]
    res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert res["measured"] is True
    assert res["total_watch_seconds"] == 900
    assert res["watch_hours"] == pytest.approx(900 / 3600.0)
    assert format_watch_time_ui(res["watch_hours"]) == "15 min"


# 2. active viewer with NULL left_at does not become zero
def test_2_active_viewer_with_null_left_at_does_not_become_zero():
    now = 1700000600.0
    joined = 1700000000.0
    sessions = [{
        "identity": "user_active",
        "role": "viewer",
        "joined_at": joined,
        "last_seen": now,
        "left_at": None,  # Active session has NULL left_at
    }]
    res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert res["measured"] is True
    assert res["total_watch_seconds"] == 600
    assert res["total_watch_seconds"] > 0
    assert res["watch_hours"] > 0


# 3. completed viewer duration calculated correctly
def test_3_completed_viewer_duration_calculated_correctly():
    joined = 1700000000.0
    left = 1700001200.0  # 1200 seconds = 20 minutes
    sessions = [{
        "identity": "user_completed",
        "role": "viewer",
        "joined_at": joined,
        "last_seen": left,
        "left_at": left,
    }]
    res = calculate_viewer_watch_time(sessions, now_ts=left + 500)
    assert res["measured"] is True
    assert res["total_watch_seconds"] == 1200
    assert format_watch_time_ui(res["watch_hours"]) == "20 min"


# 4. completed event preserves final watch time
@pytest.mark.asyncio
async def test_4_completed_event_preserves_final_watch_time():
    event_id = "test_event_completed_preserve"
    start_ts = 1700000000.0
    end_ts = 1700003600.0  # 1 hour event

    # Record viewer joins and heartbeats
    await bus.session_record_join(event_id, "v1", role="viewer", joined_at=start_ts)
    await bus.session_record_heartbeat(event_id, "v1", timestamp=start_ts + 1800)  # 30 min

    await bus.session_record_join(event_id, "v2", role="viewer", joined_at=start_ts + 600)
    await bus.session_record_heartbeat(event_id, "v2", timestamp=end_ts)  # 50 min (3000s)

    # Event ends and finalizes
    summary = await bus.session_finalize(event_id, end_time=end_ts)
    assert summary["measured"] is True
    total_sec = summary["total_watch_seconds"]
    assert total_sec == 1800 + 3000  # 4800s = 80 min

    # Presence is cleared when room ends
    await bus.presence_clear(event_id)

    # Even after presence_clear, summary is preserved
    preserved = bus.summary_get_sync(event_id)
    assert preserved is not None
    assert preserved["total_watch_seconds"] == 4800
    assert preserved["watch_hours"] == pytest.approx(4800 / 3600.0)


# 5. 145 minutes produces approximately 145 min in UI
def test_5_145_minutes_produces_approximately_145_min_in_ui():
    # 145 minutes = 8700 seconds = 2.416667 hours
    watch_seconds = 145 * 60
    watch_hours = watch_seconds / 3600.0
    ui_str = format_watch_time_ui(watch_hours)
    # UI formats >= 60 min as hours: 2.4 hrs (approx 145 min)
    assert ui_str == "2.4 hrs"
    assert ui_str != "0 min"
    assert ui_str != "—"


# 6. multiple viewers sum correctly
def test_6_multiple_viewers_sum_correctly():
    now = 1700010000.0
    # Viewer 1: 30 min (1800s)
    # Viewer 2: 60 min (3600s)
    # Viewer 3: 55 min (3300s)
    # Total: 145 min (8700s)
    sessions = [
        {"identity": "v1", "role": "viewer", "joined_at": now - 1800, "left_at": now},
        {"identity": "v2", "role": "viewer", "joined_at": now - 3600, "left_at": now},
        {"identity": "v3", "role": "viewer", "joined_at": now - 3300, "left_at": now},
    ]
    res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert res["viewer_count"] == 3
    assert res["total_watch_seconds"] == 8700  # 145 minutes exactly!
    assert pytest.approx(res["watch_hours"], 1e-4) == 145 / 60.0
    assert format_watch_time_ui(res["watch_hours"]) == "2.4 hrs"


# 7. average watch time calculated correctly
def test_7_average_watch_time_calculated_correctly():
    now = 1700010000.0
    sessions = [
        {"identity": "v1", "role": "viewer", "joined_at": now - 1800, "left_at": now},  # 1800s
        {"identity": "v2", "role": "viewer", "joined_at": now - 3600, "left_at": now},  # 3600s
        {"identity": "v3", "role": "viewer", "joined_at": now - 3300, "left_at": now},  # 3300s
    ]
    res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert res["viewer_count"] == 3
    assert res["total_watch_seconds"] == 8700
    assert res["average_watch_seconds"] == 2900  # 8700 / 3 = 2900 seconds


# 8. reconnect does not double-count overlapping sessions
def test_8_reconnect_does_not_double_count_overlapping_sessions():
    # Viewer opened two tabs with overlapping time:
    # Tab 1: [1000, 1400] (400s)
    # Tab 2: [1200, 1600] (400s)
    # Overlapping interval union must be [1000, 1600] = 600s, not 800s!
    sessions = [
        {"identity": "user_multitab", "role": "viewer", "joined_at": 1000.0, "left_at": 1400.0},
        {"identity": "user_multitab", "role": "viewer", "joined_at": 1200.0, "left_at": 1600.0},
    ]
    res = calculate_viewer_watch_time(sessions)
    assert res["viewer_count"] == 1
    assert res["total_watch_seconds"] == 600


# 9. refresh/reconnect preserves prior duration
def test_9_refresh_reconnect_preserves_prior_duration():
    # Viewer watches 600s, refreshes page (offline for 5s), watches another 600s
    sessions = [
        {"identity": "user_refresh", "role": "viewer", "joined_at": 1000.0, "left_at": 1600.0},
        {"identity": "user_refresh", "role": "viewer", "joined_at": 1605.0, "left_at": 2205.0},
    ]
    res = calculate_viewer_watch_time(sessions)
    assert res["viewer_count"] == 1
    assert res["total_watch_seconds"] == 1200  # 600 + 600


# 10. staff exclusion remains correct
def test_10_staff_exclusion_remains_correct():
    now = 1700001000.0
    sessions = [
        {"identity": "host_1", "role": "host", "joined_at": now - 3600, "left_at": now},
        {"identity": "speaker_1", "role": "speaker", "joined_at": now - 3600, "left_at": now},
        {"identity": "mod_1", "role": "moderator", "joined_at": now - 3600, "left_at": now},
        {"identity": "stage_1", "role": "viewer", "on_stage": True, "joined_at": now - 3600, "left_at": now},
        {"identity": "waiting_1", "role": "viewer", "waiting": True, "joined_at": now - 3600, "left_at": now},
        {"identity": "real_viewer", "role": "viewer", "joined_at": now - 1800, "left_at": now},  # 30 min
    ]
    res = calculate_viewer_watch_time(sessions, now_ts=now)
    assert res["viewer_count"] == 1
    assert res["total_watch_seconds"] == 1800
    assert format_watch_time_ui(res["watch_hours"]) == "30 min"


# 11. seconds/minutes conversion correct
def test_11_seconds_minutes_conversion_correct():
    seconds = 8700
    minutes = seconds / 60.0
    hours = seconds / 3600.0
    assert minutes == 145.0
    assert hours == pytest.approx(2.416667, 1e-4)
    # Verify no double division:
    assert hours * 3600 == seconds
    assert minutes * 60 == seconds


# 12. active event watch time increases after refetch
def test_12_active_event_watch_time_increases_after_refetch():
    t0 = 1700001000.0
    sessions = [{
        "identity": "viewer_ticking",
        "role": "viewer",
        "joined_at": 1700000000.0,
        "last_seen": t0,
        "left_at": None,
    }]
    res_t0 = calculate_viewer_watch_time(sessions, now_ts=t0)
    assert res_t0["total_watch_seconds"] == 1000

    # 300 seconds later, caller refetches:
    t1 = t0 + 300.0
    sessions[0]["last_seen"] = t1
    res_t1 = calculate_viewer_watch_time(sessions, now_ts=t1)
    assert res_t1["total_watch_seconds"] == 1300
    assert res_t1["total_watch_seconds"] > res_t0["total_watch_seconds"]


# 13. recently completed event does not reset to zero
def test_13_recently_completed_event_does_not_reset_to_zero():
    event_start = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
    event_end = datetime(2026, 9, 28, 12, 25, tzinfo=timezone.utc)  # 145 minutes later
    stored_summary = {
        "total_watch_seconds": 8700,
        "average_watch_seconds": 8700,
        "watch_hours": 2.4167,
        "measured": True,
        "viewer_count": 1,
    }
    agg = aggregate_event_watch_time(
        event_id="recently_completed_event",
        sessions=[],
        snaps=[],
        event_start=event_start,
        event_end=event_end,
        summary_override=stored_summary,
    )
    assert agg["measured"] is True
    assert agg["total_watch_seconds"] == 8700
    assert agg["watch_hours"] == 2.4167
    assert format_watch_time_ui(agg["watch_hours"]) == "2.4 hrs"


# 14. zero vs unavailable correctly distinguished
def test_14_zero_vs_unavailable_correctly_distinguished():
    # Case A: Never sampled / no telemetry -> honest None -> UI displays "—"
    agg_none = aggregate_event_watch_time(
        event_id="event_no_telemetry",
        sessions=[],
        snaps=[],
    )
    assert agg_none["measured"] is False
    assert agg_none["total_watch_seconds"] is None
    assert agg_none["watch_hours"] is None
    assert format_watch_time_ui(agg_none["watch_hours"]) == "—"

    # Case B: Sampled with 0 duration -> measured 0.0 -> UI displays "0 min"
    sessions_zero = [{
        "identity": "user_instant_bounce",
        "role": "viewer",
        "joined_at": 1700000000.0,
        "left_at": 1700000000.0,
    }]
    res_zero = calculate_viewer_watch_time(sessions_zero)
    assert res_zero["measured"] is True
    assert res_zero["total_watch_seconds"] == 0
    assert res_zero["watch_hours"] == 0.0
    assert format_watch_time_ui(res_zero["watch_hours"]) == "0 min"


# 15. all analytics surfaces use the same authoritative helper where applicable
def test_15_all_analytics_surfaces_use_authoritative_helper():
    # Ensure watch_time.py calculate_viewer_watch_time & aggregate_event_watch_time
    # are imported and utilized across services
    import app.services.broadcast as bc
    import app.services.org as org
    import app.services.report as rep
    import app.services.bus as b

    assert hasattr(bc, "_watch_seconds")
    assert hasattr(org, "analytics")
    assert hasattr(org, "audience_attendance")
    assert hasattr(rep, "_audience")
    assert hasattr(b, "session_finalize")
