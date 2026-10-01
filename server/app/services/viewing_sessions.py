"""Session-aware viewer analytics: additional operational metrics, never a replacement.

ZST-SPEC-VAP-001 §9 defines audience reporting around viewing SESSIONS: sessions admitted,
peak concurrent sessions, median watch duration, join-time distribution, rejoin rate and
playback QoE. This module derives exactly those from the session ledger that already exists
(services/bus.py `live:{event}:sessions`). One interval there is one connect-to-disconnect
cycle of one credential-backed identity, which is one viewing session.

It changes nothing about Peak Viewers, Avg Watch, Watch Time or any existing card. Those keep
coming from services/watch_time.py. These numbers sit beside them.

Honesty rules, all enforced here:
  * No data is None, never 0. A broadcast with no recorded viewer sessions reports
    measured=False, and every metric is None.
  * Nothing is de-duplicated across identities, and no person is inferred. Rejoin rate is
    computed only over identities that genuinely belong to one credential (a signed-in user,
    a registration, a per-browser access-link pass). The legacy shared `guest-link-<id>`
    identity, which every holder of an old ?link= URL shares, and disposable `viewer-<uuid>`
    identities are excluded from it and counted in `rejoin_basis`.
  * Playback QoE reports only what viewers' own players sent (moderation._playback_report):
    time to first frame and playback failures, by coarse browser and device class. There is no
    rebuffer telemetry for WebRTC playback, so rebuffer_ratio is None with a note, not an
    estimate.
"""
from __future__ import annotations

import re
import statistics

from .watch_time import STAFF_ROLES, _to_timestamp

# Join time relative to the broadcast start, in seconds: [low, high).
JOIN_BUCKETS = (
    ("before_start", None, 0),
    ("0-5m", 0, 300),
    ("5-15m", 300, 900),
    ("15-30m", 900, 1800),
    ("30-60m", 1800, 3600),
    ("60m+", 3600, None),
)

# Identities that do NOT stand for one person: the legacy shared access-link identity (every
# holder of the same old ?link= URL) and the disposable anonymous one.
_SHARED_IDENTITY = re.compile(r"^(guest-link-[0-9a-f-]{36}|viewer-.+)$")

REBUFFER_NOTE = ("Rebuffering is not measured: live playback is WebRTC, which exposes no "
                 "buffering events to the player.")


def _viewer_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows or []:
        role = row.get("role") or "viewer"
        if role in STAFF_ROLES or row.get("on_stage") or row.get("waiting"):
            continue
        if _to_timestamp(row.get("joined_at")) is None:
            continue
        out.append(row)
    return out


def _span(row: dict, now_ts: float | None, event_end_ts: float | None) -> tuple[float, float]:
    """[start, end] of one session. Same NULL left_at rule as watch_time: an open session ends
    at its last heartbeat, else now. Clamped to the event end, and never negative."""
    start = _to_timestamp(row.get("joined_at"))
    end = _to_timestamp(row.get("left_at"))
    if end is None:
        end = _to_timestamp(row.get("last_seen"))
    if end is None:
        end = now_ts if now_ts is not None else start
    if event_end_ts is not None:
        end = min(end, event_end_ts)
    return start, max(start, end)


def _peak_concurrent(spans: list[tuple[float, float]]) -> int:
    # Sessions, not people: two tabs of one identity are already one interval in the ledger,
    # so this counts simultaneous viewing sessions. A join sorts before a leave at the same
    # instant, matching watch_time.calculate_peak_concurrent.
    points = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans], key=lambda p: (p[0], -p[1]))
    peak = current = 0
    for _, delta in points:
        current += delta
        peak = max(peak, current)
    return peak


def _join_distribution(spans, broadcast_start_ts):
    if broadcast_start_ts is None:
        return None
    counts = {name: 0 for name, _, _ in JOIN_BUCKETS}
    for start, _ in spans:
        offset = start - broadcast_start_ts
        for name, low, high in JOIN_BUCKETS:
            if (low is None or offset >= low) and (high is None or offset < high):
                counts[name] += 1
                break
    return [{"bucket": name, "sessions": counts[name]} for name, _, _ in JOIN_BUCKETS]


def _qoe(rows: list[dict]) -> dict:
    startups = [int(r["startup_ms"]) for r in rows if isinstance(r.get("startup_ms"), (int, float))]
    failed = [r for r in rows if (r.get("failures") or 0) > 0]

    def by(field):
        out: dict[str, int] = {}
        for r in failed:
            key = r.get(field) or "Unknown"
            out[key] = out.get(key, 0) + 1
        return out

    reporting = len(startups) + len([r for r in failed if not isinstance(r.get("startup_ms"), (int, float))])
    return {
        "sessions_reporting": reporting,
        "startup_ms_median": int(statistics.median(startups)) if startups else None,
        "failed_sessions": len(failed) if reporting else None,
        "failures_by_browser": by("browser") if failed else {},
        "failures_by_device": by("device") if failed else {},
        "rebuffer_ratio": None,
        "rebuffer_note": REBUFFER_NOTE,
    }


def summarize_sessions(rows: list[dict], *, broadcast_start_ts=None, now_ts: float | None = None,
                       event_end_ts=None) -> dict:
    """The session metrics for one broadcast, from ledger rows (bus.session_get_all)."""
    viewers = _viewer_rows(rows)
    start_ts = _to_timestamp(broadcast_start_ts)
    end_ts = _to_timestamp(event_end_ts)
    if not viewers:
        return {"measured": False, "sessions_admitted": None, "peak_concurrent_sessions": None,
                "median_watch_seconds": None, "join_time_distribution": None,
                "rejoin_rate": None, "rejoins": None,
                "rejoin_basis": {"sessions": 0, "excluded_shared_sessions": 0}, "qoe": None}

    spans = [_span(r, now_ts, end_ts) for r in viewers]
    durations = [e - s for s, e in spans]

    per_identity: dict[str, int] = {}
    excluded = 0
    for r in viewers:
        identity = r.get("identity") or ""
        if _SHARED_IDENTITY.match(identity):
            excluded += 1
            continue
        per_identity[identity] = per_identity.get(identity, 0) + 1
    considered = sum(per_identity.values())
    rejoins = sum(n - 1 for n in per_identity.values())

    return {
        "measured": True,
        "sessions_admitted": len(viewers),
        "peak_concurrent_sessions": _peak_concurrent(spans),
        "median_watch_seconds": round(statistics.median(durations), 1),
        "join_time_distribution": _join_distribution(spans, start_ts),
        "rejoin_rate": round(rejoins / considered, 4) if considered else None,
        "rejoins": rejoins if considered else None,
        "rejoin_basis": {"sessions": considered, "excluded_shared_sessions": excluded},
        "qoe": _qoe(viewers),
    }


def combine(summaries: list[dict]) -> dict:
    """Range-level view over several broadcasts' summaries (org analytics).

    Additive metrics add, peaks take the max, the rejoin rate is re-derived from its own
    numerator and denominator, and the join-time buckets sum. The median is NOT combined:
    a median of medians is not a median, so it stays per event (reports[].sessions)."""
    measured = [s for s in summaries if s and s.get("measured")]
    if not measured:
        return {"measured": False, "events_measured": 0, "sessions_admitted": None,
                "peak_concurrent_sessions": None, "rejoin_rate": None,
                "join_time_distribution": None, "qoe_sessions_reporting": None}
    considered = sum((s.get("rejoin_basis") or {}).get("sessions", 0) for s in measured)
    rejoins = sum(s.get("rejoins") or 0 for s in measured)
    buckets: dict[str, int] = {}
    has_buckets = False
    for s in measured:
        for b in s.get("join_time_distribution") or []:
            has_buckets = True
            buckets[b["bucket"]] = buckets.get(b["bucket"], 0) + int(b.get("sessions") or 0)
    return {
        "measured": True,
        "events_measured": len(measured),
        "sessions_admitted": sum(int(s.get("sessions_admitted") or 0) for s in measured),
        "peak_concurrent_sessions": max(int(s.get("peak_concurrent_sessions") or 0) for s in measured),
        "rejoin_rate": round(rejoins / considered, 4) if considered else None,
        "join_time_distribution": (
            [{"bucket": name, "sessions": buckets.get(name, 0)} for name, _, _ in JOIN_BUCKETS]
            if has_buckets else None),
        "qoe_sessions_reporting": sum(int((s.get("qoe") or {}).get("sessions_reporting") or 0) for s in measured),
    }
