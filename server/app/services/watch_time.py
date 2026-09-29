"""Authoritative watch-time calculation and viewer session ledger.

Unified across:
- Producer Console analytics (services/broadcast.py::analytics_now)
- Organization Analytics / Performance Summary (services/org.py::analytics)
- Attendance summary (services/org.py::audience_attendance)
- Event lifecycle finalization (when event ends)

Telemetry Model:
1. Active viewer session:
   - joined_at: unix timestamp of initial connection
   - last_seen: unix timestamp of latest ping/heartbeat (defaults to joined_at)
   - left_at: None while active
   - effective_end: min(last_seen or now_ts, event_end_ts or now_ts)
   - duration: effective_end - joined_at (>= 0)
2. Completed viewer session:
   - left_at: unix timestamp of disconnection
   - effective_end: min(left_at, event_end_ts or left_at)
   - duration: effective_end - joined_at (>= 0)
3. Session deduplication / interval union:
   - Reconnects and multi-tab sessions for the same viewer identity are merged.
   - Overlapping intervals are unified so viewers are never double-counted.
4. Staff exclusion:
   - "host", "speaker", "moderator", and on_stage participants are excluded from audience metrics.
   - Only role == "viewer" (or default/unassigned viewer) are counted.
   - Participants with waiting == True (waiting room) are excluded.
5. Zero vs unavailable:
   - No telemetry -> watch_hours=None, total_watch_seconds=None (UI: "—")
   - Telemetry measured with 0 duration -> watch_hours=0.0, total_watch_seconds=0 (UI: "0 min")
   - Telemetry measured with >0 duration -> exact watch_hours / total_watch_seconds
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any


STAFF_ROLES = {"host", "speaker", "moderator"}


def _to_timestamp(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.timestamp()
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            try:
                dt = datetime.fromisoformat(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except (ValueError, TypeError):
                return None
    return None


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping or touching [start, end] intervals.
    
    Prevents double-counting of overlapping sessions (e.g. multiple browser tabs,
    reconnect overlaps, or duplicate presence rows).
    """
    cleaned: list[tuple[float, float]] = []
    for start, end in intervals:
        if end >= start:
            cleaned.append((start, end))
    if not cleaned:
        return []
    
    cleaned.sort(key=lambda iv: (iv[0], iv[1]))
    merged: list[tuple[float, float]] = [cleaned[0]]
    for cur_start, cur_end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if cur_start <= last_end:
            # Overlap or contiguous -> extend boundary
            merged[-1] = (last_start, max(last_end, cur_end))
        else:
            merged.append((cur_start, cur_end))
    return merged


def calculate_peak_concurrent(merged_intervals_by_identity: dict[str, list[tuple[float, float]]]) -> int:
    """Calculate the maximum concurrent viewers across all distinct viewer identities."""
    events: list[tuple[float, int]] = []
    for intervals in merged_intervals_by_identity.values():
        for start, end in intervals:
            if end >= start:
                events.append((start, 1))
                events.append((end, -1))
    if not events:
        return 0
    # Sort events: at same timestamp, joins (+1) before leaves (-1)
    events.sort(key=lambda x: (x[0], -x[1]))
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        if current > peak:
            peak = current
    return peak


def calculate_viewer_watch_time(
    sessions: list[dict],
    now_ts: float | None = None,
    event_end_ts: float | None = None,
    include_staff: bool = False,
) -> dict:
    """Authoritative watch time calculation over a collection of viewer sessions.
    
    Returns:
        total_watch_seconds: int | None (None if no telemetry available)
        average_watch_seconds: int | None (None if no telemetry available)
        watch_hours: float | None (None if no telemetry available)
        measured: bool
        viewer_count: int
        peak_concurrent: int
        valid_sessions_count: int
    """
    now = now_ts if now_ts is not None else time.time()
    
    # Filter sessions
    valid_sessions: list[dict] = []
    for s in sessions:
        role = (s.get("role") or "viewer").lower()
        if not include_staff and role in STAFF_ROLES:
            continue
        if not include_staff and s.get("on_stage"):
            continue
        if s.get("waiting"):
            continue
        
        joined_at = _to_timestamp(s.get("joined_at"))
        if joined_at is None:
            continue
        valid_sessions.append(s)
        
    if not valid_sessions:
        return {
            "total_watch_seconds": None,
            "average_watch_seconds": None,
            "watch_hours": None,
            "measured": False,
            "viewer_count": 0,
            "peak_concurrent": 0,
            "valid_sessions_count": 0,
        }

    # Group intervals by viewer identity
    intervals_by_identity: dict[str, list[tuple[float, float]]] = {}
    for idx, s in enumerate(valid_sessions):
        identity = str(s.get("identity") or f"anonymous_{idx}")
        joined_at = _to_timestamp(s.get("joined_at"))
        assert joined_at is not None  # filtered above
        
        left_at = _to_timestamp(s.get("left_at"))
        last_seen = _to_timestamp(s.get("last_seen"))
        
        if left_at is not None:
            effective_end = left_at
        else:
            # Active session: use last_seen or current now_ts
            effective_end = last_seen if last_seen is not None else now
            
        if event_end_ts is not None:
            effective_end = min(effective_end, event_end_ts)
            
        effective_end = max(joined_at, effective_end)
        intervals_by_identity.setdefault(identity, []).append((joined_at, effective_end))

    # Calculate merged duration per viewer identity
    merged_by_identity: dict[str, list[tuple[float, float]]] = {}
    total_seconds = 0.0
    for identity, ivs in intervals_by_identity.items():
        merged = merge_intervals(ivs)
        merged_by_identity[identity] = merged
        viewer_dur = sum(end - start for start, end in merged)
        total_seconds += max(0.0, viewer_dur)

    viewer_count = len(intervals_by_identity)
    peak_concurrent = calculate_peak_concurrent(merged_by_identity)
    total_int_seconds = int(round(total_seconds))
    avg_int_seconds = int(round(total_seconds / viewer_count)) if viewer_count > 0 else 0
    watch_hours = round(total_seconds / 3600.0, 6)

    return {
        "total_watch_seconds": total_int_seconds,
        "average_watch_seconds": avg_int_seconds,
        "watch_hours": watch_hours,
        "measured": True,
        "viewer_count": viewer_count,
        "peak_concurrent": peak_concurrent,
        "valid_sessions_count": len(valid_sessions),
    }


def aggregate_event_watch_time(
    event_id: Any,
    sessions: list[dict] | None = None,
    snaps: list[Any] | None = None,
    now_ts: float | None = None,
    event_start: Any = None,
    event_end: Any = None,
    summary_override: dict | None = None,
) -> dict:
    """Authoritatively resolves watch time for a single event by checking:
    1. Stored / persisted analytics summary (if available).
    2. Real-time active and completed viewer sessions.
    3. Snapshots integral (fallback for historical events).
    4. Unmeasured (returns None).
    """
    now = now_ts if now_ts is not None else time.time()
    end_ts = _to_timestamp(event_end)

    # 1. Stored summary override (e.g. from finalized event in DB or Redis)
    if summary_override and summary_override.get("measured"):
        total_sec = summary_override.get("total_watch_seconds")
        avg_sec = summary_override.get("average_watch_seconds")
        hours = summary_override.get("watch_hours")
        if hours is None and total_sec is not None:
            hours = round(total_sec / 3600.0, 6)
        peak_conc = summary_override.get("peak_concurrent", summary_override.get("viewer_count", 0))
        return {
            "total_watch_seconds": total_sec,
            "average_watch_seconds": avg_sec,
            "watch_hours": hours,
            "measured": True,
            "viewer_count": summary_override.get("viewer_count", 0),
            "peak_concurrent": peak_conc,
            "source": "stored_summary",
        }

    # 2. Real-time sessions
    if sessions:
        res = calculate_viewer_watch_time(sessions, now_ts=now, event_end_ts=end_ts)
        if res["measured"]:
            res["source"] = "viewer_sessions"
            return res

    # 3. Snapshot fallback
    if snaps:
        # Concurrent viewers integrated over the sampler's 15s interval = real watch-hours
        SAMPLE_HOURS = 15 / 3600.0
        total_viewers_sampled = sum(getattr(s, "viewers", 0) for s in snaps)
        watch_hours = round(total_viewers_sampled * SAMPLE_HOURS, 6)
        total_seconds = int(round(total_viewers_sampled * 15))
        peak = max((getattr(s, "viewers", 0) for s in snaps), default=0)
        avg_seconds = int(round(total_seconds / max(1, peak))) if peak > 0 else 0
        return {
            "total_watch_seconds": total_seconds,
            "average_watch_seconds": avg_seconds,
            "watch_hours": watch_hours,
            "measured": True,
            "viewer_count": peak,
            "peak_concurrent": peak,
            "source": "snapshots",
        }

    # 4. Nothing measured
    return {
        "total_watch_seconds": None,
        "average_watch_seconds": None,
        "watch_hours": None,
        "measured": False,
        "viewer_count": 0,
        "peak_concurrent": 0,
        "source": "none",
    }


def build_event_analytics_aggregate(
    event_id: Any,
    event_title: str | None = None,
    event_start: Any = None,
    event_end: Any = None,
    created_at: Any = None,
    sessions: list[dict] | None = None,
    snaps: list[Any] | None = None,
    b_sessions: list[Any] | None = None,
    summary_override: dict | None = None,
    chat_count: int = 0,
    question_count: int = 0,
    reaction_count: int = 0,
    poll_votes: int = 0,
    now_ts: float | None = None,
) -> dict:
    """Canonical, authoritative per-event analytics aggregate.

    Unified data source across:
    - Summary cards (KPIs)
    - 30-day historical trend graph
    - Top Performing Events (viewers, watch time, engagement)
    - Engagement mix
    - Audience retention scatter plot
    - Recent Reports / Performance Summary table
    - Single-event reports
    """
    now = now_ts if now_ts is not None else time.time()
    b_sessions = b_sessions or []
    snaps = snaps or []
    sessions = sessions or []

    # Calculate effective peak concurrency across sources
    bs_peak = max((getattr(s, "peak_viewers", 0) or 0 for s in b_sessions), default=0)
    snap_peak = max((getattr(s, "viewers", 0) or 0 for s in snaps), default=0)

    # Resolve authoritative watch time and peak concurrency
    agg = aggregate_event_watch_time(
        event_id=event_id,
        sessions=sessions,
        snaps=snaps,
        now_ts=now,
        event_start=event_start,
        event_end=event_end,
        summary_override=summary_override,
    )

    effective_peak = max(bs_peak, snap_peak, agg.get("peak_concurrent", 0))

    # Event occurrence datetime: prioritize broadcast session started_at, then event start_time, then created_at
    latest_bs = None
    if b_sessions:
        latest_bs = max(
            b_sessions,
            key=lambda s: getattr(s, "started_at", None) or datetime.min.replace(tzinfo=timezone.utc),
        )

    occurrence_dt = None
    if latest_bs and getattr(latest_bs, "started_at", None):
        occurrence_dt = latest_bs.started_at
    elif event_start:
        occurrence_dt = event_start
    elif created_at:
        occurrence_dt = created_at

    if isinstance(occurrence_dt, datetime) and occurrence_dt.tzinfo is None:
        occurrence_dt = occurrence_dt.replace(tzinfo=timezone.utc)

    # Engagement score:
    # None if telemetry is unmeasured OR no audience was present (peak <= 0)
    # Heuristic 0-100 score if audience was present to measure
    is_measured = agg.get("measured", False)
    if not is_measured or effective_peak <= 0:
        engagement = None
    else:
        weighted = chat_count + 2 * question_count + 3 * poll_votes + reaction_count
        engagement = max(0, min(100, round(100 * weighted / (5 * effective_peak))))

    # State classification:
    if not is_measured:
        measurement_state = "unmeasured"
    elif effective_peak == 0 and (agg.get("total_watch_seconds") == 0 or agg.get("total_watch_seconds") is None):
        measurement_state = "measured_zero"
    else:
        measurement_state = "measured"

    return {
        "event_id": str(event_id),
        "event_name": event_title or str(event_id),
        "event_start": event_start,
        "event_end": event_end,
        "occurrence_dt": occurrence_dt,
        "peak_viewers": effective_peak,
        "unique_viewers": agg.get("viewer_count", 0),
        "total_watch_seconds": agg.get("total_watch_seconds"),
        "average_watch_seconds": agg.get("average_watch_seconds"),
        "watch_hours": agg.get("watch_hours"),
        "chat_count": chat_count,
        "question_count": question_count,
        "reaction_count": reaction_count,
        "poll_votes": poll_votes,
        "engagement_score": engagement,
        "measurement_state": measurement_state,
        "source": agg.get("source", "none"),
    }

