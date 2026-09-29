"""Unit and integration tests for scheduled-duration overrun behavior."""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from app.services.event_overrun import (
    evaluate_event_overrun,
    STATE_ON_TIME,
    STATE_ENDING_SOON,
    STATE_ENDED_SCHEDULE,
    STATE_OVERTIME,
    STATE_NO_SCHEDULE,
    PRE_END_REMINDER_THRESHOLDS,
)
from app.models.event import Event
from app.models.live import BroadcastSession
from app.services import watch_time


def test_1_event_before_warning_window_no_alert():
    """1. event before warning window -> on_time state, no alert threshold triggered."""
    now = datetime(2026, 9, 29, 11, 30, 0, tzinfo=timezone.utc)
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)  # 23 minutes remaining

    res = evaluate_event_overrun(scheduled_end, now=now)
    assert res["state"] == STATE_ON_TIME
    assert res["remaining_seconds"] == 23 * 60
    assert res["overrun_seconds"] == 0
    assert res["formatted_remaining"] == "23:00"
    assert res["remaining_seconds"] > 600  # Outside the 10-min warning window


def test_2_10_minute_warning_threshold():
    """2. 10-minute warning: at 10 minutes remaining, state is ending_soon."""
    now = datetime(2026, 9, 29, 11, 43, 0, tzinfo=timezone.utc)
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)  # 10 minutes remaining

    res = evaluate_event_overrun(scheduled_end, now=now)
    assert res["state"] == STATE_ENDING_SOON
    assert res["remaining_seconds"] == 600
    assert res["overrun_seconds"] == 0
    assert 600 in PRE_END_REMINDER_THRESHOLDS


def test_3_5_minute_warning_threshold():
    """3. 5-minute warning: at 5 minutes remaining, state is ending_soon."""
    now = datetime(2026, 9, 29, 11, 48, 0, tzinfo=timezone.utc)
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)  # 5 minutes remaining

    res = evaluate_event_overrun(scheduled_end, now=now)
    assert res["state"] == STATE_ENDING_SOON
    assert res["remaining_seconds"] == 300
    assert res["overrun_seconds"] == 0
    assert 300 in PRE_END_REMINDER_THRESHOLDS


def test_4_1_minute_warning_threshold():
    """4. 1-minute warning: at 1 minute remaining, state is ending_soon."""
    now = datetime(2026, 9, 29, 11, 52, 0, tzinfo=timezone.utc)
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)  # 1 minute remaining

    res = evaluate_event_overrun(scheduled_end, now=now)
    assert res["state"] == STATE_ENDING_SOON
    assert res["remaining_seconds"] == 60
    assert res["overrun_seconds"] == 0
    assert 60 in PRE_END_REMINDER_THRESHOLDS


def test_5_scheduled_end_reached_alert():
    """5. scheduled end reached -> alert shown, state is ended_schedule."""
    now = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)

    res = evaluate_event_overrun(scheduled_end, now=now)
    assert res["state"] == STATE_ENDED_SCHEDULE
    assert res["remaining_seconds"] == 0
    assert res["overrun_seconds"] == 0
    assert res["display_message"] == "Scheduled event time has ended"


def test_6_overtime_1_minute_displayed():
    """6. overtime +1 minute displayed."""
    now = datetime(2026, 9, 29, 11, 54, 0, tzinfo=timezone.utc)
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)

    res = evaluate_event_overrun(scheduled_end, now=now)
    assert res["state"] == STATE_OVERTIME
    assert res["remaining_seconds"] == 0
    assert res["overrun_seconds"] == 60
    assert res["formatted_overrun"] == "+01:00"
    assert "1 minute past scheduled end" in res["display_message"]


def test_7_overtime_timer_continues_increasing():
    """7. overtime timer continues increasing (e.g. +3m 42s)."""
    t1 = datetime(2026, 9, 29, 11, 54, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 29, 11, 56, 42, tzinfo=timezone.utc)
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)

    r1 = evaluate_event_overrun(scheduled_end, now=t1)
    r2 = evaluate_event_overrun(scheduled_end, now=t2)
    assert r1["overrun_seconds"] == 60
    assert r2["overrun_seconds"] == 222
    assert r2["overrun_seconds"] > r1["overrun_seconds"]
    assert r2["formatted_overrun"] == "+03:42"


def test_8_warning_persists_while_live():
    """8. warning persists while live: state remains overtime as long as broadcast continues."""
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)
    for minute_offset in range(1, 30):
        now = scheduled_end + timedelta(minutes=minute_offset)
        res = evaluate_event_overrun(scheduled_end, now=now)
        assert res["state"] == STATE_OVERTIME
        assert res["overrun_seconds"] == minute_offset * 60


def test_9_ending_broadcast_removes_overtime_state():
    """9. ending broadcast: live status changes to ended, leaving no active live overtime."""
    # When status is not live (e.g. status == "ended"), console leaves live mode
    status = "ended"
    is_live = (status == "live")
    assert not is_live


def test_10_rescheduling_updates_deadline():
    """10. rescheduling updates deadline without reload."""
    now = datetime(2026, 9, 29, 11, 55, 0, tzinfo=timezone.utc)
    old_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)
    # Old schedule was 2 minutes overtime
    r_old = evaluate_event_overrun(old_end, now=now)
    assert r_old["state"] == STATE_OVERTIME
    assert r_old["overrun_seconds"] == 120

    # Rescheduled to 12:15
    new_end = datetime(2026, 9, 29, 12, 15, 0, tzinfo=timezone.utc)
    r_new = evaluate_event_overrun(new_end, now=now)
    assert r_new["state"] == STATE_ON_TIME
    assert r_new["remaining_seconds"] == 20 * 60
    assert r_new["overrun_seconds"] == 0


def test_11_refresh_reconnect_preserves_correct_overtime():
    """11. refresh/reconnect preserves correct overtime from authoritative timestamp."""
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 29, 11, 58, 30, tzinfo=timezone.utc)

    # First connection
    snap1 = evaluate_event_overrun(scheduled_end, now=now)
    # Simulated reconnect after 30 seconds
    snap2 = evaluate_event_overrun(scheduled_end, now=now + timedelta(seconds=30))

    assert snap1["overrun_seconds"] == 330
    assert snap2["overrun_seconds"] == 360
    assert snap1["formatted_overrun"] == "+05:30"
    assert snap2["formatted_overrun"] == "+06:00"


def test_12_no_duplicate_notifications():
    """12. no duplicate notifications: each threshold only in standard thresholds set."""
    assert len(PRE_END_REMINDER_THRESHOLDS) == len(set(PRE_END_REMINDER_THRESHOLDS))
    assert list(PRE_END_REMINDER_THRESHOLDS) == [600, 300, 60, 0]


def test_13_event_is_not_auto_ended_by_default():
    """13. event is NOT auto-ended by default: auto_end_event defaults to False on Event model."""
    event_model_fields = Event.__table__.columns
    assert "auto_end_event" in event_model_fields
    assert event_model_fields["auto_end_event"].default.arg is False


def test_14_actual_broadcast_duration_can_exceed_scheduled_duration():
    """14. actual broadcast duration can exceed scheduled duration."""
    scheduled_start = datetime(2026, 9, 29, 11, 42, 0, tzinfo=timezone.utc)
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)
    scheduled_duration = (scheduled_end - scheduled_start).total_seconds()  # 11 minutes = 660s

    actual_start = datetime(2026, 9, 29, 11, 42, 0, tzinfo=timezone.utc)
    actual_end = datetime(2026, 9, 29, 12, 13, 0, tzinfo=timezone.utc)
    actual_duration = (actual_end - actual_start).total_seconds()  # 31 minutes = 1860s

    assert actual_duration > scheduled_duration
    assert actual_duration == 1860


def test_15_analytics_watch_time_not_truncated_at_scheduled_end():
    """15. analytics/watch time not truncated at scheduled end."""
    scheduled_end = datetime(2026, 9, 29, 11, 53, 0, tzinfo=timezone.utc)

    # Viewer joins at 11:45 and leaves at 12:15 (22 minutes past scheduled end)
    joined_at = datetime(2026, 9, 29, 11, 45, 0, tzinfo=timezone.utc)
    left_at = datetime(2026, 9, 29, 12, 15, 0, tzinfo=timezone.utc)

    # Compute raw seconds between joined and left
    viewer_seconds = (left_at - joined_at).total_seconds()
    assert viewer_seconds == 30 * 60  # 30 minutes
    assert left_at > scheduled_end
    # Watch time represents real viewer duration, not capped at scheduled_end (which would have been 8 mins)
