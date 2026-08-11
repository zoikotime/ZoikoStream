"""Super Admin Command Center aggregation.

Same rule as services/admin.py and services/broadcast.py: every number here comes from a
real row, a real timestamp or a real measurement. Where this stack has no source for
something the console shows, the value is None with a `*_note` explaining what would feed
it — never a plausible-looking constant.

What is genuinely measured:
  * lifecycle stage status      <- services/admin.platform_health() probes
  * stage/region availability   <- recorded Incident impact windows (union, not sum)
  * concurrent audience + peak  <- AnalyticsSnapshot rows written every 15s by the
                                   broadcast sampler (real presence, not an estimate)
  * live sessions              <- BroadcastSession rows
  * API error rate + p95        <- RequestStats, fed by the ASGI timing middleware
  * event readiness verdicts    <- the event's own stored configuration and assignments
  * action queues / governance  <- Incident, SupportTicket, Subscription, GovernanceRecord

What has no source yet (reported as None + note):
  * playback QoE (startup, rebuffer, fatal error rate) — needs a player beacon writing
    PlatformMetric rows; the read path below is already wired for it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AnalyticsSnapshot,
    BroadcastSession,
    ElevationSession,
    Event,
    EventAssignment,
    GovernanceRecord,
    Incident,
    LiveRecording,
    Organization,
    PlatformMetric,
    SessionAlert,
    Subscription,
    SupportTicket,
    User,
)
from . import admin as admin_svc

log = logging.getLogger(__name__)

# ── vocabulary ────────────────────────────────────────────────────────────────

# The lifecycle rail. Each stage maps to the platform_health service ids that actually
# carry it, so a stage's status is a real probe result rather than a label. A stage whose
# services are all unintegrated reports "not_configured" instead of a green tick.
STAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("contribute", "Contribute", ("streaming",)),
    ("ingest", "Ingest", ("streaming",)),
    ("produce", "Produce", ("workers",)),
    ("secure", "Secure", ("auth",)),
    ("deliver", "Deliver", ("cdn", "streaming")),
    ("understand", "Understand", ("database",)),
    ("preserve", "Preserve", ("storage",)),
    ("platform", "Platform", ("api", "database")),
)

# Services that are permanently "not_configured" by design — roadmap features nobody has
# built yet (see services/admin.platform_health), not something an org's own configuration
# could ever turn green. Kept out of stage severity so a real, fixable gap (e.g. storage)
# doesn't get diluted by a stage that also happens to include one of these, and kept out of
# the org headline (service_health, below) entirely so a permanently-unbuilt feature can't
# pin every active org at "Not configured" forever regardless of actual health.
INFORMATIONAL_SERVICES = frozenset({"cdn", "workers"})

# Stages carried ENTIRELY by informational services (currently just "produce" <- "workers")
# — there is no real member left to report on, so the stage keeps reading "not_configured"
# itself (honest, matches the per-service list), but must never gate the one-line verdict.
INFORMATIONAL_STAGES = frozenset(
    code for code, _label, service_ids in STAGES
    if set(service_ids) <= INFORMATIONAL_SERVICES
)

REGIONS = (("na", "NA"), ("eu", "EU"), ("apac", "APAC"), ("sa", "SA"))

# Organization.region is free text ("US East", "EU West (Ireland)", "AP South (Mumbai)").
# Map it onto the delivery regions the console reports by. Unmatched -> None (counted as
# global, never silently bucketed into NA).
_REGION_PREFIXES = (
    ("na", ("us", "ca", "north america", "america")),
    ("eu", ("eu", "europe", "uk", "gb")),
    ("apac", ("ap", "asia", "au", "jp", "in", "sg")),
    ("sa", ("sa", "south america", "br", "latam")),
)

RANGES = {"live": timedelta(minutes=15), "1h": timedelta(hours=1),
          "24h": timedelta(hours=24), "7d": timedelta(days=7)}

# Which lifecycle stages each console scope covers. "Core + Live Events" is everything.
SCOPES = {
    "core_live": tuple(s[0] for s in STAGES),
    "core": ("secure", "understand", "preserve", "platform"),
    "live": ("contribute", "ingest", "produce", "deliver"),
}


def region_of(org_region: str | None) -> str | None:
    if not org_region:
        return None
    low = org_region.strip().lower()
    for code, prefixes in _REGION_PREFIXES:
        if any(low.startswith(p) for p in prefixes):
            return code
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Postgres returns tz-aware datetimes, but a SQLite/naive row must not blow up the
    arithmetic below."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── live API measurement ──────────────────────────────────────────────────────

class RequestStats:
    """Rolling per-minute request buckets. Fed by the ASGI middleware in main.py, so the
    console's API-health tile reports this process's real traffic instead of a guess.

    In-memory and per-process on purpose: it is a health signal, not billing data, and a
    table write per request would cost more than the signal is worth. With multiple
    workers each reports its own slice — noted in the payload.
    """

    def __init__(self, minutes: int = 60):
        self.minutes = minutes
        # minute-epoch -> [requests, errors, [latency_ms, ...]]
        self._buckets: deque[tuple[int, list]] = deque(maxlen=minutes)

    def record(self, latency_ms: float, status_code: int) -> None:
        minute = int(time.time() // 60)
        if not self._buckets or self._buckets[-1][0] != minute:
            self._buckets.append((minute, [0, 0, []]))
        _, cell = self._buckets[-1]
        cell[0] += 1
        if status_code >= 500:
            cell[1] += 1
        # Cap the sample list so a hot minute can't grow without bound; p95 over 2k
        # samples is as good as p95 over 200k.
        if len(cell[2]) < 2000:
            cell[2].append(latency_ms)

    def snapshot(self, window_minutes: int = 15) -> dict:
        cutoff = int(time.time() // 60) - window_minutes
        cells = [(m, c) for m, c in self._buckets if m >= cutoff]
        requests = sum(c[0] for _, c in cells)
        if not requests:
            return {"error_ratio": None, "p95_ms": None, "requests": 0, "series": []}
        errors = sum(c[1] for _, c in cells)
        samples = sorted(v for _, c in cells for v in c[2])
        p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))] if samples else None
        return {
            "error_ratio": round(100 * errors / requests, 3),
            "p95_ms": round(p95) if p95 is not None else None,
            "requests": requests,
            # One point per minute: the tile's sparkline is real traffic over the window.
            "series": [{"label": str(m), "value": c[0]} for m, c in cells],
        }


request_stats = RequestStats()


# ── availability from recorded incidents ──────────────────────────────────────

def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Union of overlapping intervals. Summing raw durations would double-count two
    concurrent incidents and can report >100% impact, so the overlaps are merged first."""
    if not intervals:
        return []
    out = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def _impact_seconds(intervals: list[tuple[datetime, datetime]], since: datetime, until: datetime) -> float:
    total = 0.0
    for start, end in _merge(intervals):
        lo, hi = max(start, since), min(end, until)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


def availability(db: Session, since: datetime, until: datetime | None = None) -> dict:
    """{(stage, region): percent} over the window, from real incident impact windows.

    A region-less incident (region NULL) counts against every region — that is what
    "global" impact means. Availability with no recorded impact is exactly 100.0, which is
    a measurement of "nothing was recorded", not an assumption of perfection.
    """
    until = until or _now()
    span = max((until - since).total_seconds(), 1.0)

    rows = db.scalars(
        select(Incident).where(
            Incident.stage.isnot(None),
            or_(Incident.resolved_at.is_(None), Incident.resolved_at >= since),
            Incident.started_at <= until,
        )
    ).all()

    per_key: dict[tuple[str, str], list[tuple[datetime, datetime]]] = {}
    for inc in rows:
        start = _aware(inc.started_at) or since
        end = _aware(inc.resolved_at) or until
        targets = [inc.region] if inc.region else [code for code, _ in REGIONS]
        for region in targets:
            per_key.setdefault((inc.stage, region), []).append((start, end))

    out = {}
    for (stage, _label, _svcs) in STAGES:
        for region, _rlabel in REGIONS:
            impact = _impact_seconds(per_key.get((stage, region), []), since, until)
            out[(stage, region)] = round(100 * (1 - min(impact / span, 1.0)), 2)
    return out


# ── metric samples ────────────────────────────────────────────────────────────

def metric_latest(db: Session, name: str) -> float | None:
    return db.scalar(
        select(PlatformMetric.value).where(PlatformMetric.name == name)
        .order_by(PlatformMetric.recorded_at.desc()).limit(1)
    )


def metric_series(db: Session, name: str, since: datetime, points: int = 30) -> list[dict]:
    """Down-sampled series for a sparkline. Buckets by time so the tile gets a fixed
    number of points regardless of sampling cadence."""
    rows = db.execute(
        select(PlatformMetric.recorded_at, PlatformMetric.value)
        .where(PlatformMetric.name == name, PlatformMetric.recorded_at >= since)
        .order_by(PlatformMetric.recorded_at)
    ).all()
    return _bucket([( _aware(t), v) for t, v in rows], since, _now(), points)


def _bucket(rows: list[tuple[datetime, float]], since: datetime, until: datetime,
            points: int, agg: str = "avg") -> list[dict]:
    """Average (or max) the samples into `points` equal time buckets. Empty buckets are
    dropped rather than zero-filled — a gap in sampling is not a value of zero."""
    if not rows or points < 1:
        return []
    span = max((until - since).total_seconds(), 1.0)
    width = span / points
    sums: dict[int, list[float]] = {}
    for stamp, value in rows:
        if stamp is None:
            continue
        idx = min(points - 1, max(0, int((stamp - since).total_seconds() / width)))
        sums.setdefault(idx, []).append(float(value))
    out = []
    for idx in sorted(sums):
        vals = sums[idx]
        value = max(vals) if agg == "max" else sum(vals) / len(vals)
        out.append({"label": str(idx), "value": round(value, 3)})
    return out


# ── audience, from the broadcast sampler's real snapshots ─────────────────────

def _live_session_rows(db: Session, include_test: bool) -> list[BroadcastSession]:
    stmt = select(BroadcastSession).where(BroadcastSession.ended_at.is_(None))
    if not include_test:
        test_ids = _test_org_ids(db)
        if test_ids:
            stmt = stmt.where(BroadcastSession.org_id.notin_(test_ids))
    return list(db.scalars(stmt).all())


def _test_org_ids(db: Session) -> list:
    return list(db.scalars(select(Organization.id).where(Organization.is_test.is_(True))).all())


def _latest_snapshot_per_event(db: Session, event_ids: list, since: datetime) -> dict:
    """Newest AnalyticsSnapshot per event within the window. One grouped query + one
    fetch, not one query per session."""
    if not event_ids:
        return {}
    newest = (
        select(AnalyticsSnapshot.event_id, func.max(AnalyticsSnapshot.created_at).label("t"))
        .where(AnalyticsSnapshot.event_id.in_(event_ids), AnalyticsSnapshot.created_at >= since)
        .group_by(AnalyticsSnapshot.event_id)
        .subquery()
    )
    rows = db.scalars(
        select(AnalyticsSnapshot).join(
            newest,
            (AnalyticsSnapshot.event_id == newest.c.event_id)
            & (AnalyticsSnapshot.created_at == newest.c.t),
        )
    ).all()
    return {s.event_id: s for s in rows}


def audience(db: Session, since: datetime, include_test: bool) -> dict:
    """Concurrent audience now, peak over the window, and the largest single session —
    all from AnalyticsSnapshot rows the broadcast sampler writes from real presence."""
    sessions = _live_session_rows(db, include_test)
    event_ids = [s.event_id for s in sessions]
    # "Now" means the most recent sample; snapshots older than two sampler intervals are
    # stale (worker down, event idle) and are not counted as current audience.
    fresh_since = _now() - timedelta(seconds=60)
    latest = _latest_snapshot_per_event(db, event_ids, fresh_since)
    current = sum(s.viewers for s in latest.values())

    largest_event_id, largest = None, 0
    for event_id, snap in latest.items():
        if snap.viewers > largest:
            largest_event_id, largest = event_id, snap.viewers
    largest_title = None
    if largest_event_id is not None:
        largest_title = db.scalar(select(Event.title).where(Event.id == largest_event_id))

    # Peak = highest platform-wide total, so per-tick sums are needed rather than a plain
    # MAX over rows (two sessions of 500 is a peak of 1000, not 500).
    ticks = db.execute(
        select(AnalyticsSnapshot.created_at, func.sum(AnalyticsSnapshot.viewers))
        .where(AnalyticsSnapshot.created_at >= since)
        .group_by(AnalyticsSnapshot.created_at)
        .order_by(AnalyticsSnapshot.created_at)
    ).all()
    peak = max((int(v or 0) for _, v in ticks), default=0)

    return {
        "current": current,
        "peak": peak,
        "largest_session": largest or None,
        "largest_session_title": largest_title,
        "series": _bucket([(_aware(t), float(v or 0)) for t, v in ticks], since, _now(), 30, agg="max"),
    }


# ── live sessions + attention ─────────────────────────────────────────────────

# Weighting for derived attention items. Written down rather than buried: an unrepeatable
# event losing media is the platform's worst case, a missing recording is a monitoring item.
_ISSUE_RULES = (
    # (predicate key, stage, issue text, severity for unrepeatable, severity otherwise)
    ("no_media", "contribute", "Nothing is publishing to the room", "critical", "high"),
    ("paused", "produce", "Broadcast is paused mid-session", "high", "monitoring"),
    ("recording_gap", "preserve", "Recording enabled but not captured", "high", "monitoring"),
    ("single_path", "contribute", "Running on a single contribution path", "critical", "high"),
)


def live_sessions(db: Session, since: datetime, include_test: bool) -> dict:
    """The live-sessions tile plus the attention table.

    Attention items are derived from rows that already exist (session status, the latest
    analytics snapshot, recording rows, single-path overrides) and merged with anything an
    operator raised by hand in session_alerts. Deriving rather than duplicating is what
    keeps the console from disagreeing with the host console about the same session.
    """
    sessions = _live_session_rows(db, include_test)
    live = [s for s in sessions if s.status == "live"]
    paused = [s for s in sessions if s.status == "paused"]
    event_ids = [s.event_id for s in sessions]

    events = {e.id: e for e in db.scalars(select(Event).where(Event.id.in_(event_ids))).all()} if event_ids else {}
    orgs = {o.id: o for o in db.scalars(select(Organization).where(
        Organization.id.in_({s.org_id for s in sessions}))).all()} if sessions else {}
    snaps = _latest_snapshot_per_event(db, event_ids, _now() - timedelta(seconds=60))

    # Recording rows that are actually capturing, per event.
    capturing = set(db.scalars(
        select(LiveRecording.event_id).where(
            LiveRecording.event_id.in_(event_ids),
            LiveRecording.status.in_(("recording", "paused")),
            LiveRecording.enforced.is_(True),
        )
    ).all()) if event_ids else set()

    single_path = set(db.scalars(
        select(GovernanceRecord.event_id).where(
            GovernanceRecord.kind == "single_path_override",
            GovernanceRecord.resolved_at.is_(None),
            GovernanceRecord.event_id.isnot(None),
        )
    ).all())

    hosts = _event_hosts(db, event_ids)

    items = []
    for s in sessions:
        ev = events.get(s.event_id)
        if ev is None:
            continue
        org = orgs.get(s.org_id)
        snap = snaps.get(s.event_id)
        unrepeatable = (ev.impact or "standard") == "unrepeatable"
        flags = {
            # health_of() in services/broadcast.py uses the same signal: live with nothing
            # on stage means no media is reaching the room.
            "no_media": s.status == "live" and snap is not None and snap.on_stage == 0,
            "paused": s.status == "paused",
            "recording_gap": bool(ev.recording_enabled) and s.status == "live"
                             and s.event_id not in capturing,
            "single_path": s.event_id in single_path,
        }
        for key, stage, issue, sev_unrepeatable, sev_normal in _ISSUE_RULES:
            if not flags[key]:
                continue
            items.append({
                "id": f"{s.event_id}:{key}",
                "event_id": str(s.event_id),
                "event": ev.title or "Untitled event",
                "organization": org.name if org else None,
                "impact": ev.impact or "standard",
                "severity": sev_unrepeatable if unrepeatable else sev_normal,
                "stage": stage,
                "issue": issue,
                "started_at": _aware(s.started_at),
                "owner": hosts.get(s.event_id),
                "source": "derived",
            })

    # Operator-raised items on top. The test-org set is resolved once, not per alert.
    raised = db.scalars(
        select(SessionAlert).where(SessionAlert.resolved_at.is_(None))
    ).all()
    test_orgs = set() if include_test else set(_test_org_ids(db))
    for a in raised:
        if a.org_id in test_orgs:
            continue
        ev = events.get(a.event_id) or db.get(Event, a.event_id)
        items.append({
            "id": str(a.id),
            "event_id": str(a.event_id),
            "event": (ev.title if ev else None) or "Untitled event",
            "organization": ev.organization.name if ev and ev.organization else None,
            "impact": (ev.impact if ev else None) or "standard",
            "severity": a.severity,
            "stage": a.stage,
            "issue": a.issue,
            "started_at": _aware(a.opened_at),
            "owner": a.owner.full_name if a.owner else None,
            "source": "raised",
        })

    order = {"critical": 0, "high": 1, "monitoring": 2}
    items.sort(key=lambda i: (order.get(i["severity"], 3), i["started_at"] or _now()))

    # Sessions starting soon, and how many of those have nobody rostered to host them.
    soon_cutoff = _now() + timedelta(minutes=30)
    soon = db.scalars(
        select(Event).where(
            Event.deleted_at.is_(None),
            Event.status.in_(("scheduled", "published")),
            Event.start_time.isnot(None),
            Event.start_time > _now(),
            Event.start_time <= soon_cutoff,
        )
    ).all()
    soon = [e for e in soon if include_test or not (e.organization and e.organization.is_test)]
    soon_hosts = _event_hosts(db, [e.id for e in soon])
    unattended = sum(1 for e in soon if not soon_hosts.get(e.id))

    counts = {sev: sum(1 for i in items if i["severity"] == sev) for sev in ("critical", "high", "monitoring")}
    stages = [i["stage"] for i in items if i["stage"]]
    dominant = max(set(stages), key=stages.count) if stages else None

    return {
        "live": len(live),
        "paused": len(paused),
        "starting_soon": len(soon),
        "unattended": unattended,
        "series": _bucket(
            [(_aware(t), float(v or 0)) for t, v in db.execute(
                select(AnalyticsSnapshot.created_at, func.count(func.distinct(AnalyticsSnapshot.event_id)))
                .where(AnalyticsSnapshot.created_at >= since)
                .group_by(AnalyticsSnapshot.created_at)
                .order_by(AnalyticsSnapshot.created_at)
            ).all()],
            since, _now(), 30, agg="max",
        ),
        "attention": items,
        "at_risk": {
            "total": len(items),
            **counts,
            "dominant_stage": dominant,
        },
    }


def _event_hosts(db: Session, event_ids: list) -> dict:
    """event_id -> assigned host name. One join instead of a query per event."""
    if not event_ids:
        return {}
    rows = db.execute(
        select(EventAssignment.event_id, User.full_name)
        .join(User, User.id == EventAssignment.user_id)
        .where(EventAssignment.event_id.in_(event_ids), EventAssignment.role == "host")
    ).all()
    out = {}
    for event_id, name in rows:
        out.setdefault(event_id, name)
    return out


# ── lifecycle stage health ────────────────────────────────────────────────────

_RANK = {"ok": 0, "not_configured": 1, "warn": 2, "down": 3}
_WORST = {v: k for k, v in _RANK.items()}


def stage_health(db: Session, health: dict, since: datetime, stages: tuple[str, ...]) -> list[dict]:
    """Per-stage status (from real service probes + open incidents) and availability
    (from recorded incident windows), plus the per-region matrix the console renders."""
    services = {s["id"]: s for s in health["services"]}
    avail = availability(db, since)

    open_by_stage: dict[str, list[Incident]] = {}
    for inc in db.scalars(select(Incident).where(Incident.status != "resolved")).all():
        if inc.stage:
            open_by_stage.setdefault(inc.stage, []).append(inc)

    out = []
    for code, label, service_ids in STAGES:
        if code not in stages:
            continue
        members = [services[i] for i in service_ids if i in services]
        # A stage with at least one real (non-informational) member is judged only by that
        # member — e.g. Deliver reads by Streaming's actual status, not dragged down by CDN
        # simply never having been built. A stage carried entirely by informational members
        # (Produce <- Workers) has nothing else to report, so it falls back to them.
        gating = [m for m in members if m["id"] not in INFORMATIONAL_SERVICES]
        statuses = [m["status"] for m in (gating or members)] or ["not_configured"]
        # An unintegrated dependency must not read as healthy, and must not read as an
        # outage either — it ranks between ok and warn.
        status = _WORST[max(_RANK.get(s, 1) for s in statuses)]
        for inc in open_by_stage.get(code, []):
            status = _WORST[max(_RANK[status], 3 if inc.severity in ("sev1", "sev2") else 2)]

        regions = {}
        for region, _rlabel in REGIONS:
            regions[region] = avail[(code, region)]
        # Stage headline availability = the worst region, because the console's job is to
        # surface the users who are having a bad time, not the average.
        overall = min(regions.values()) if regions else None
        out.append({
            "stage": code,
            "label": label,
            "status": status,
            "availability": overall,
            "regions": regions,
            "services": [{"id": m["id"], "name": m["name"], "status": m["status"],
                          "note": m.get("note")} for m in members],
            "open_incidents": len(open_by_stage.get(code, [])),
        })
    return out


# ── event readiness ───────────────────────────────────────────────────────────

# Readiness gates, evaluated against the event's own stored configuration. `required_for`
# lists the impact classes where failing the gate BLOCKS the event; for lower classes the
# same failure is a conditional pass. Nothing here is a guess — each gate reads a column.
_GATES = (
    ("title", "Title set", ("high", "unrepeatable")),
    ("schedule", "Start time scheduled", ("high", "unrepeatable")),
    ("host", "Host assigned", ("high", "unrepeatable")),
    ("moderator", "Moderator assigned", ("unrepeatable",)),
    ("recording", "Recording enabled", ("unrepeatable",)),
    ("account", "Account in good standing", ("high", "unrepeatable")),
    ("redundancy", "No unresolved single-path override", ("unrepeatable",)),
)


def _gate_results(ev: Event, hosts: dict, moderators: dict, single_path: set) -> list[dict]:
    checks = {
        "title": bool(ev.title),
        "schedule": ev.start_time is not None,
        "host": bool(hosts.get(ev.id)),
        "moderator": bool(moderators.get(ev.id)),
        "recording": bool(ev.recording_enabled),
        "account": bool(ev.organization and ev.organization.status != "suspended"),
        "redundancy": ev.id not in single_path,
    }
    return [{"key": key, "label": label, "passed": checks[key],
             "required": (ev.impact or "standard") in required_for}
            for key, label, required_for in _GATES]


def _verdict(gates: list[dict]) -> str:
    if any(g["required"] and not g["passed"] for g in gates):
        return "blocked"
    if any(not g["passed"] for g in gates):
        return "conditional"
    return "passed"


def event_readiness(db: Session, include_test: bool, limit: int = 8,
                    high_impact_only: bool = True) -> list[dict]:
    """Upcoming events with a readiness verdict computed from their real configuration."""
    stmt = (
        select(Event).where(
            Event.deleted_at.is_(None),
            Event.status.in_(("scheduled", "published")),
            Event.start_time.isnot(None),
            Event.start_time >= _now(),
        ).order_by(Event.start_time).limit(limit * 3)
    )
    events = list(db.scalars(stmt).all())
    if high_impact_only:
        events = [e for e in events if (e.impact or "standard") in ("high", "unrepeatable")]
    if not include_test:
        events = [e for e in events if not (e.organization and e.organization.is_test)]
    events = events[:limit]
    if not events:
        return []

    ids = [e.id for e in events]
    hosts = _event_hosts(db, ids)
    moderators = {}
    for event_id, name in db.execute(
        select(EventAssignment.event_id, User.full_name)
        .join(User, User.id == EventAssignment.user_id)
        .where(EventAssignment.event_id.in_(ids), EventAssignment.role == "moderator")
    ).all():
        moderators.setdefault(event_id, name)
    single_path = set(db.scalars(
        select(GovernanceRecord.event_id).where(
            GovernanceRecord.kind == "single_path_override",
            GovernanceRecord.resolved_at.is_(None),
            GovernanceRecord.event_id.isnot(None),
        )
    ).all())

    out = []
    for ev in events:
        gates = _gate_results(ev, hosts, moderators, single_path)
        out.append({
            "id": str(ev.id),
            "title": ev.title or "Untitled event",
            "organization": ev.organization.name if ev.organization else None,
            "impact": ev.impact or "standard",
            "start_time": _aware(ev.start_time),
            "timezone": ev.timezone,
            "verdict": _verdict(gates),
            "gates": gates,
            "failing": [g["label"] for g in gates if not g["passed"]],
        })
    return out


# ── incidents, queues, governance ─────────────────────────────────────────────

def incidents(db: Session, since: datetime, limit: int = 6) -> list[dict]:
    rows = db.scalars(
        select(Incident)
        .where(or_(Incident.status != "resolved", Incident.started_at >= since))
        .order_by(Incident.started_at.desc()).limit(limit)
    ).all()
    return [{
        "id": str(i.id), "ref": i.ref, "title": i.title, "detail": i.detail,
        "severity": i.severity, "kind": i.kind, "stage": i.stage, "region": i.region,
        "status": i.status, "commander": i.commander,
        "organization": i.organization.name if i.organization else None,
        "started_at": _aware(i.started_at), "resolved_at": _aware(i.resolved_at),
    } for i in rows]


def action_queues(db: Session, readiness: list[dict]) -> list[dict]:
    """Four queues, each built from the rows that actually represent outstanding work.
    Items carry a `to` route so the console's links go somewhere real."""
    now = _now()

    open_incidents = db.scalars(
        select(Incident).where(Incident.status != "resolved").order_by(Incident.started_at.desc())
    ).all()
    blocked = [e for e in readiness if e["verdict"] == "blocked"]
    tickets = db.scalars(
        select(SupportTicket).where(SupportTicket.status.in_(("open", "in_progress")))
        .order_by(SupportTicket.created_at.desc())
    ).all()
    governance = db.scalars(
        select(GovernanceRecord).where(GovernanceRecord.resolved_at.is_(None))
        .order_by(GovernanceRecord.opened_at.desc())
    ).all()
    expiring = db.scalars(
        select(Subscription).where(
            Subscription.status.in_(("trial", "past_due")),
        ).order_by(Subscription.current_period_end)
    ).all()

    def when(dt):
        return _aware(dt)

    operational = [
        *[{"id": str(i.id), "title": i.title, "detail": f"{i.ref} · {i.severity.upper()}",
           "tone": "danger" if i.severity in ("sev1", "sev2") else "warning",
           "at": when(i.started_at), "to": "/admin/status"}
          for i in open_incidents if i.kind == "operational"],
        *[{"id": e["id"], "title": f"{e['impact'].replace('_', ' ').title()} event blocked",
           "detail": f"{e['title']} · {', '.join(e['failing'][:2])}",
           "tone": "danger", "at": e["start_time"], "to": "/admin/live-events"}
          for e in blocked],
    ]
    security = [
        *[{"id": str(i.id), "title": i.title, "detail": f"{i.ref} · {i.status}",
           "tone": "warning", "at": when(i.started_at), "to": "/admin/audit"}
          for i in open_incidents if i.kind == "security"],
        *[{"id": str(g.id), "title": "Break-glass grant under review",
           "detail": (g.detail or "Emergency elevation") + (
               f" · due {_fmt_due(g.due_at, now)}" if g.due_at else ""),
           "tone": "warning", "at": when(g.opened_at), "to": "/admin/audit"}
          for g in governance if g.kind == "break_glass"],
    ]
    customer = [
        {"id": str(t.id), "title": t.subject,
         "detail": f"{t.organization.name if t.organization else '—'} · {t.priority}",
         "tone": "danger" if t.priority == "urgent" else "info",
         "at": when(t.created_at), "to": "/admin/support"}
        for t in tickets
    ]
    commercial = [
        *[{"id": str(g.id), "title": "Entitlement override pending approval",
           "detail": g.detail or (g.organization.name if g.organization else "—"),
           "tone": "warning", "at": when(g.opened_at), "to": "/admin/subscriptions"}
          for g in governance if g.kind == "entitlement_override"],
        *[{"id": str(s.id), "title": f"Subscription {s.status.replace('_', ' ')}",
           "detail": f"{s.organization.name if s.organization else '—'}"
                     + (f" · ends {_fmt_due(s.current_period_end, now)}" if s.current_period_end else ""),
           "tone": "warning" if s.status == "trial" else "danger",
           "at": when(s.current_period_end), "to": "/admin/subscriptions"}
          for s in expiring],
    ]

    return [
        {"key": "operational", "label": "Operational", "count": len(operational), "items": operational[:6]},
        {"key": "security", "label": "Security", "count": len(security), "items": security[:6]},
        {"key": "customer", "label": "Customer", "count": len(customer), "items": customer[:6]},
        {"key": "commercial", "label": "Commercial", "count": len(commercial), "items": commercial[:6]},
    ]


def _fmt_due(dt: datetime | None, now: datetime) -> str:
    dt = _aware(dt)
    if dt is None:
        return "—"
    delta = dt - now
    mins = int(delta.total_seconds() // 60)
    if mins < 0:
        return "overdue"
    if mins < 60:
        return f"in {mins} min"
    hours = mins // 60
    if hours < 48:
        return f"in {hours}h"
    return f"in {hours // 24}d"


def governance_exposure(db: Session) -> dict:
    """The five governance rows, each a real aggregate over governance_records."""
    def count(kind: str, *extra):
        return db.scalar(
            select(func.count(GovernanceRecord.id)).where(GovernanceRecord.kind == kind, *extra)
        ) or 0

    quarter_start = _now().replace(month=((_now().month - 1) // 3) * 3 + 1, day=1,
                                  hour=0, minute=0, second=0, microsecond=0)

    exports_total = count("usage_export")
    exports_late = count("usage_export", GovernanceRecord.status == "failed")
    return {
        "usage_export_on_time_pct": (
            round(100 * (exports_total - exports_late) / exports_total, 1) if exports_total else None
        ),
        "usage_export_total": exports_total,
        "legal_holds": count("legal_hold", GovernanceRecord.resolved_at.is_(None)),
        "entitlement_overrides_pending": count(
            "entitlement_override", GovernanceRecord.status == "pending"),
        "break_glass_under_review": count(
            "break_glass", GovernanceRecord.resolved_at.is_(None)),
        "single_path_overrides_quarter": count(
            "single_path_override", GovernanceRecord.opened_at >= quarter_start),
    }


def privileged_activity(db: Session, limit: int = 6) -> list[dict]:
    """Recent audit entries, labelled with the actor's operating team. The department comes
    from the user row when the actor still exists; the audit log itself only keeps the
    email (by design — it must outlive the account)."""
    from ..crud import admin as crud

    logs, _total = crud.list_audit_logs(db, page=1, page_size=limit)
    emails = [l.actor_email for l in logs if l.actor_email]
    people = {}
    if emails:
        for full_name, email, department, role in db.execute(
            select(User.full_name, User.email, User.department, User.role)
            .where(User.email.in_(emails))
        ).all():
            people[email] = {"name": full_name, "department": department, "role": role}

    out = []
    for l in logs:
        person = people.get(l.actor_email or "", {})
        target = None
        if l.meta:
            target = l.meta.get("name") or l.meta.get("version") or l.meta.get("subject") or l.meta.get("email")
        out.append({
            "id": str(l.id),
            "actor": person.get("name") or l.actor_email or "system",
            "department": person.get("department") or _role_label(person.get("role")),
            "action": l.action,
            "target_type": l.target_type,
            "target": target,
            "at": _aware(l.created_at),
        })
    return out


def _role_label(role: str | None) -> str | None:
    if not role:
        return None
    return admin_svc.ROLE_META.get(role, (role.replace("_", " ").title(),))[0]


# ── elevation ─────────────────────────────────────────────────────────────────

def current_elevation(db: Session, user: User) -> dict | None:
    row = db.scalar(
        select(ElevationSession).where(
            ElevationSession.user_id == user.id,
            ElevationSession.ended_at.is_(None),
            ElevationSession.expires_at > _now(),
        ).order_by(ElevationSession.granted_at.desc())
    )
    if row is None:
        return None
    return {
        "id": str(row.id),
        "scope": row.scope,
        "scopes": row.scopes or [],
        "reason": row.reason,
        "granted_at": _aware(row.granted_at),
        "expires_at": _aware(row.expires_at),
        "seconds_remaining": max(0, int((_aware(row.expires_at) - _now()).total_seconds())),
    }


# ── global search ─────────────────────────────────────────────────────────────

def search(db: Session, q: str, limit: int = 8) -> list[dict]:
    """Real entity search across the objects a platform operator looks for."""
    term = f"%{q.strip()}%"
    out: list[dict] = []

    for org in db.scalars(
        select(Organization).where(Organization.name.ilike(term)).limit(limit)
    ).all():
        out.append({"kind": "Organization", "label": org.name,
                    "detail": org.status, "to": "/admin/organizations"})

    for ev in db.scalars(
        select(Event).where(Event.deleted_at.is_(None), Event.title.ilike(term))
        .order_by(Event.start_time.desc().nullslast()).limit(limit)
    ).all():
        out.append({"kind": "Event", "label": ev.title or "Untitled event",
                    "detail": ev.status, "to": "/admin/live-events"})

    for u in db.scalars(
        select(User).where(
            User.deleted_at.is_(None),
            or_(User.full_name.ilike(term), User.email.ilike(term)),
        ).limit(limit)
    ).all():
        out.append({"kind": "User", "label": u.full_name, "detail": u.email, "to": "/admin/users"})

    return out[: limit * 2]


# ── console state (chrome) ────────────────────────────────────────────────────

def console_state(db: Session, user: User) -> dict:
    """Everything the console frame needs on every admin page: overall health, the three
    sidebar badge counts and the caller's elevation session. Deliberately small — it is
    fetched by the shell, not by the dashboard."""
    health = admin_svc.platform_health(db)
    sessions = live_sessions(db, _now() - RANGES["24h"], include_test=False)
    readiness = event_readiness(db, include_test=False, limit=50)
    degraded = sum(1 for s in health["services"] if s["status"] in ("warn", "down"))

    return {
        "health": {"overall": health["overall"], "degraded": degraded,
                   "total": len(health["services"])},
        "badges": {
            "live_operations": sessions["at_risk"]["total"],
            "event_readiness": sum(1 for e in readiness if e["verdict"] != "passed"),
            "system_status": degraded,
        },
        "elevation": current_elevation(db, user),
        "user": {
            "name": user.full_name,
            "email": user.email,
            "department": user.department or _role_label(user.role),
        },
    }


# ── the page payload ──────────────────────────────────────────────────────────

def command_center(db: Session, user: User, range_: str = "live", region: str | None = None,
                   scope: str = "core_live", include_test: bool = False,
                   since: datetime | None = None, until: datetime | None = None) -> dict:
    """One call for the whole Command Center. The page has eleven interdependent regions
    over the same window; eleven round trips would only give eleven chances to disagree
    about what "now" is.

    `since`/`until` are supplied only for range="custom"; every named range derives its
    window from RANGES so the two paths can't drift.
    """
    # `generated_at` is always the real clock — it is when this payload was built, which is
    # what the page's freshness line reports. `window_end` is what the DATA covers, and for
    # a custom historical range those are deliberately different.
    generated_at = _now()
    window_end = until or generated_at
    since = since or (window_end - RANGES.get(range_, RANGES["live"]))
    stages = SCOPES.get(scope, SCOPES["core_live"])

    health = admin_svc.platform_health(db)
    sessions = live_sessions(db, since, include_test)
    aud = audience(db, since, include_test)
    api = request_stats.snapshot()
    readiness = event_readiness(db, include_test)
    lifecycle = stage_health(db, health, since, stages)

    if region:
        # Region narrows the session/attention view to orgs delivering from that region
        # and the incidents scoped to it. Availability is already per-region.
        org_region = {o.id: region_of(o.region) for o in db.scalars(select(Organization)).all()}
        keep_events = {
            str(e_id) for e_id, org_id in db.execute(
                select(Event.id, Event.org_id).where(Event.deleted_at.is_(None))
            ).all() if org_region.get(org_id) == region
        }
        sessions["attention"] = [a for a in sessions["attention"] if a["event_id"] in keep_events]
        readiness = [e for e in readiness if e["id"] in keep_events]

    ok_services = sum(1 for s in health["services"] if s["status"] == "ok")
    live_services = [s for s in health["services"] if s["status"] in ("ok", "warn", "down")]

    return {
        "generated_at": generated_at,
        "window": {"range": range_, "since": since, "until": window_end,
                   "region": region, "scope": scope, "include_test": include_test},
        "lifecycle": lifecycle,
        "regions": [{"code": c, "label": l} for c, l in REGIONS],
        "kpis": {
            "platform_health": {
                "status": health["overall"],
                "ok": ok_services,
                "total": len(health["services"]),
                "unavailable": sum(1 for s in live_services if s["status"] == "down"),
                "series": metric_series(db, "platform_health_ok", since),
            },
            "live_sessions": {
                "value": sessions["live"],
                "starting_soon": sessions["starting_soon"],
                "unattended": sessions["unattended"],
                "series": sessions["series"],
            },
            "at_risk_sessions": sessions["at_risk"],
            "concurrent_audience": {
                "value": aud["current"] or None,
                "peak": aud["peak"] or None,
                "largest_session": aud["largest_session"],
                "largest_session_title": aud["largest_session_title"],
                "series": aud["series"],
            },
            "playback_quality": {
                "value": metric_latest(db, "playback_quality_pct"),
                "startup_ms": metric_latest(db, "playback_startup_ms"),
                "rebuffer_ratio": metric_latest(db, "playback_rebuffer_ratio"),
                "fatal_ratio": metric_latest(db, "playback_fatal_ratio"),
                "series": metric_series(db, "playback_quality_pct", since),
                "note": "Playback QoE needs a player beacon writing platform_metrics "
                        "(no ingest yet).",
            },
            "api_health": {
                "value": api["error_ratio"],
                "p95_ms": api["p95_ms"],
                "requests": api["requests"],
                "series": api["series"],
                "note": "Measured in-process; with multiple workers each reports its own share.",
            },
        },
        "attention": sessions["attention"],
        "incidents": incidents(db, since),
        "action_queues": action_queues(db, readiness),
        "governance": governance_exposure(db),
        "privileged_activity": privileged_activity(db),
        "upcoming_events": readiness,
        "elevation": current_elevation(db, user),
    }


# ── metric sampler ────────────────────────────────────────────────────────────

SAMPLE_SECONDS = 60
RETENTION_DAYS = 30


def record_samples(db: Session) -> None:
    """One tick of platform metrics. Only values with a real source are written — the QoE
    names stay absent until something measures them."""
    health = admin_svc.platform_health(db)
    ok = sum(1 for s in health["services"] if s["status"] == "ok")
    rows = [PlatformMetric(name="platform_health_ok", value=float(ok))]

    api = request_stats.snapshot(window_minutes=5)
    if api["requests"]:
        rows.append(PlatformMetric(name="api_error_ratio", value=float(api["error_ratio"])))
        if api["p95_ms"] is not None:
            rows.append(PlatformMetric(name="api_p95_ms", value=float(api["p95_ms"])))

    db.add_all(rows)
    # Bounded growth: one delete per tick beats a cron nobody sets up.
    db.query(PlatformMetric).filter(
        PlatformMetric.recorded_at < _now() - timedelta(days=RETENTION_DAYS)
    ).delete(synchronize_session=False)
    db.commit()


async def run_metric_sampler(interval: float = SAMPLE_SECONDS) -> None:
    """Own ticker, like the broadcast sampler. Runs per process — see main.lifespan."""
    from ..db import SessionLocal

    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_sample_tick, SessionLocal)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad sample must not kill the sampler
            log.exception("platform metric sampler tick failed")


def _sample_tick(session_factory) -> None:
    db = session_factory()
    try:
        record_samples(db)
    finally:
        db.close()


# ── self-check ────────────────────────────────────────────────────────────────

def _selfcheck() -> None:
    """The interval union and bucketing are the only non-obvious logic in this module;
    everything else is a query. Run with `python -m app.services.ops`."""
    base = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    h = timedelta(hours=1)

    # Overlapping incidents must not double-count: two 1h incidents overlapping by 30m
    # is 1.5h of impact, not 2h.
    merged = _merge([(base, base + h), (base + h / 2, base + 2 * h)])
    assert merged == [(base, base + 2 * h)], merged
    assert _impact_seconds([(base, base + h), (base + h / 2, base + 2 * h)],
                           base, base + 4 * h) == 2 * 3600

    # Impact is clipped to the window, not counted outside it.
    assert _impact_seconds([(base - 10 * h, base + h)], base, base + 2 * h) == 3600

    # Disjoint intervals sum.
    assert _impact_seconds([(base, base + h), (base + 2 * h, base + 3 * h)],
                           base, base + 4 * h) == 2 * 3600

    # Empty history = no recorded impact.
    assert _impact_seconds([], base, base + h) == 0

    # Bucketing: 4 samples over a 4-bucket window land one per bucket, averaged.
    rows = [(base + timedelta(minutes=m), float(v)) for m, v in ((0, 10), (15, 20), (30, 30), (45, 40))]
    out = _bucket(rows, base, base + h, 4)
    assert [p["value"] for p in out] == [10.0, 20.0, 30.0, 40.0], out
    # Two samples in one bucket average; max aggregation takes the peak.
    rows2 = [(base, 10.0), (base + timedelta(minutes=1), 30.0)]
    assert _bucket(rows2, base, base + h, 4)[0]["value"] == 20.0
    assert _bucket(rows2, base, base + h, 4, agg="max")[0]["value"] == 30.0
    # A gap is dropped, not zero-filled.
    assert len(_bucket([(base, 5.0)], base, base + h, 4)) == 1

    # Region mapping is prefix-based and refuses to guess.
    assert region_of("US East") == "na"
    assert region_of("EU West (Ireland)") == "eu"
    assert region_of("AP South (Mumbai)") == "apac"
    assert region_of("SA East (São Paulo)") == "sa"
    assert region_of("Mars") is None and region_of(None) is None

    # Readiness: a required gate failing blocks; an optional one is conditional.
    gates = [{"key": "host", "label": "Host assigned", "passed": False, "required": True}]
    assert _verdict(gates) == "blocked"
    assert _verdict([{**gates[0], "required": False}]) == "conditional"
    assert _verdict([{**gates[0], "passed": True, "required": True}]) == "passed"

    # RequestStats: error ratio and p95 over real samples.
    stats = RequestStats()
    for _ in range(99):
        stats.record(100.0, 200)
    stats.record(900.0, 500)
    snap = stats.snapshot()
    assert snap["requests"] == 100 and snap["error_ratio"] == 1.0, snap
    assert snap["p95_ms"] == 100, snap
    assert RequestStats().snapshot()["error_ratio"] is None  # no traffic -> no number

    print("ops selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
