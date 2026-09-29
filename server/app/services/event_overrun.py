"""Authoritative scheduled duration & overrun calculation for live events.

Calculates remaining time, overrun seconds, and operational schedule states.
Used across backend snapshot, live inspection, and shared contract for Producer Console.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Standard pre-end reminder thresholds in seconds before scheduled end
PRE_END_REMINDER_THRESHOLDS = (
    600,  # 10 minutes
    300,  # 5 minutes
    60,   # 1 minute
    0,    # Scheduled end
)

STATE_ON_TIME = "on_time"
STATE_ENDING_SOON = "ending_soon"
STATE_ENDED_SCHEDULE = "ended_schedule"
STATE_OVERTIME = "overtime"
STATE_NO_SCHEDULE = "no_schedule"


def parse_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            cleaned = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def format_duration(seconds: int) -> str:
    """Format seconds into MM:SS or H:MM:SS."""
    s = max(0, int(seconds))
    hrs = s // 3600
    mins = (s % 3600) // 60
    secs = s % 60
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def evaluate_event_overrun(
    scheduled_end: datetime | str | None,
    now: datetime | str | None = None,
    warning_threshold_seconds: int = 600,
) -> dict[str, Any]:
    """Returns authoritative schedule overrun calculation.

    Keys returned:
    - scheduled_end: ISO 8601 string or None
    - now: ISO 8601 string
    - remaining_seconds: int | None (positive countdown seconds remaining until end, or 0)
    - overrun_seconds: int | None (positive seconds past scheduled end, or 0)
    - state: "on_time" | "ending_soon" | "ended_schedule" | "overtime" | "no_schedule"
    - formatted_remaining: "MM:SS" or None
    - formatted_overrun: "+MM:SS" or None
    - display_message: human-readable status for UI / alerts
    """
    now_dt = parse_datetime(now) or datetime.now(timezone.utc)
    end_dt = parse_datetime(scheduled_end)

    if end_dt is None:
        return {
            "scheduled_end": None,
            "now": now_dt.isoformat(),
            "remaining_seconds": None,
            "overrun_seconds": None,
            "state": STATE_NO_SCHEDULE,
            "formatted_remaining": None,
            "formatted_overrun": None,
            "display_message": None,
        }

    diff_seconds = int((end_dt - now_dt).total_seconds())

    if diff_seconds > warning_threshold_seconds:
        state = STATE_ON_TIME
        rem = diff_seconds
        ovr = 0
        msg = f"{format_duration(rem)} remaining"
    elif diff_seconds > 0:
        state = STATE_ENDING_SOON
        rem = diff_seconds
        ovr = 0
        msg = f"{format_duration(rem)} remaining"
    elif diff_seconds == 0:
        state = STATE_ENDED_SCHEDULE
        rem = 0
        ovr = 0
        msg = "Scheduled event time has ended"
    else:
        state = STATE_OVERTIME
        rem = 0
        ovr = abs(diff_seconds)
        mins = ovr // 60
        secs = ovr % 60
        if mins > 0 and secs > 0:
            msg = f"Event is running {mins}m {secs}s past scheduled end"
        elif mins > 0:
            msg = f"Event is running {mins} minute{'s' if mins != 1 else ''} past scheduled end"
        else:
            msg = f"Event is running {secs} seconds past scheduled end"

    return {
        "scheduled_end": end_dt.isoformat(),
        "now": now_dt.isoformat(),
        "remaining_seconds": rem,
        "overrun_seconds": ovr,
        "state": state,
        "formatted_remaining": format_duration(rem) if rem is not None else None,
        "formatted_overrun": f"+{format_duration(ovr)}" if ovr > 0 else "+00:00",
        "display_message": msg,
    }
