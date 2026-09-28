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
  * API requests / errors / p95 <- RequestStats (ASGI timing middleware), flushed per
                                   process per minute to platform_metrics, so any window
                                   up to the retention limit can be answered
  * event readiness verdicts    <- the event's own stored configuration and assignments
  * action queues / governance  <- Incident, SupportTicket, Subscription, GovernanceRecord

What has no source yet (reported as None + note):
  * playback QoE (startup, rebuffer, fatal error rate) — needs a player beacon writing
    PlatformMetric rows; the read path below is already wired for it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import false, func, literal_column, or_, select
from sqlalchemy.orm import Session

from ..models import (
    LEGACY_SUBSCRIPTION_STATES,
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
from ..crud import commercial as commercial_crud
from . import admin as admin_svc
from . import ops_window

log = logging.getLogger(__name__)

# Subscriptions whose commercial phase is an evaluation, in both the §12 spelling and the
# pre-§12 one, so the expiring-trial banner still matches rows written before the state
# machine existed. Local to this module because the grouping is a UI concern (which banner
# tone to show), not a commercial state class.
_TRIALING_STATES = ("trialing", *(k for k, v in LEGACY_SUBSCRIPTION_STATES.items() if v == "trialing"))
_TRIAL_OR_PAST_DUE = (*_TRIALING_STATES, "past_due")

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

# Latency histogram bounds (ms). A percentile over a WINDOW cannot be merged from per-minute
# percentiles, but it can be read from summed histograms: p95 is reported as the upper bound
# of the bucket that holds the 95th percentile ("p95 <= 250 ms"), never interpolated into a
# more precise-looking number than the data supports.
LATENCY_BOUNDS_MS = (10, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500,
                     2000, 3000, 5000, 10000)
API_REQUESTS, API_ERRORS, API_LATENCY = "api_requests", "api_errors", "api_lat_le_"


def _lat_bucket(latency_ms: float) -> int:
    for i, bound in enumerate(LATENCY_BOUNDS_MS):
        if latency_ms <= bound:
            return i
    return len(LATENCY_BOUNDS_MS)


def _lat_name(i: int) -> str:
    return f"{API_LATENCY}{LATENCY_BOUNDS_MS[i]}" if i < len(LATENCY_BOUNDS_MS) else f"{API_LATENCY}inf"


class RequestStats:
    """Per-minute request buckets, fed by the ASGI middleware in main.py.

    In memory per process, and FLUSHED once a minute (run_request_stats_flusher, which runs in
    every process - not only the ticker leader - because every process serves requests) to
    platform_metrics as: api_requests (written for every minute the process was alive, zero
    included, so a missing minute means "not collected", not "no traffic"), api_errors, and a
    latency histogram. The Command Center reads those rows for whatever window is selected;
    before this, the tile always showed the last 15 minutes of one process whatever range was
    picked.
    """

    def __init__(self, minutes: int = 60):
        self.minutes = minutes
        # minute-epoch -> [requests, errors, [latency_ms, ...], histogram]
        self._buckets: deque[tuple[int, list]] = deque(maxlen=minutes)
        self._lock = threading.Lock()
        # Minutes before this process started were not observed by it.
        self._flushed_through = int(time.time() // 60) - 1

    def record(self, latency_ms: float, status_code: int) -> None:
        minute = int(time.time() // 60)
        with self._lock:
            if not self._buckets or self._buckets[-1][0] != minute:
                self._buckets.append((minute, [0, 0, [], [0] * (len(LATENCY_BOUNDS_MS) + 1)]))
            _, cell = self._buckets[-1]
            cell[0] += 1
            if status_code >= 500:
                cell[1] += 1
            # Cap the sample list so a hot minute can't grow without bound.
            if len(cell[2]) < 2000:
                cell[2].append(latency_ms)
            cell[3][_lat_bucket(latency_ms)] += 1

    def drain(self, now_minute: int | None = None) -> list[tuple[int, int, int, list[int]]]:
        """Completed minutes not yet flushed, zero-traffic minutes included, oldest first.
        The current minute is never drained - it is still being counted."""
        now_minute = int(time.time() // 60) if now_minute is None else now_minute
        with self._lock:
            by_minute = {m: c for m, c in self._buckets}
            start = max(self._flushed_through + 1, now_minute - self.minutes)
            out = []
            for m in range(start, now_minute):
                c = by_minute.get(m)
                if c is None:
                    out.append((m, 0, 0, [0] * (len(LATENCY_BOUNDS_MS) + 1)))
                else:
                    out.append((m, c[0], c[1], list(c[3])))
            if out:
                self._flushed_through = now_minute - 1
            return out

    def snapshot(self, window_minutes: int = 15) -> dict:
        """This process's in-memory view (exact p95). Kept for diagnostics; the console reads
        the flushed, cross-process rows instead (api_window)."""
        cutoff = int(time.time() // 60) - window_minutes
        with self._lock:
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


def _risk_summary(items: list[dict]) -> dict:
    counts = {sev: sum(1 for i in items if i["severity"] == sev)
              for sev in ("critical", "high", "monitoring")}
    stages = [i["stage"] for i in items if i["stage"]]
    dominant = max(set(stages), key=stages.count) if stages else None
    return {"total": len(items), **counts, "dominant_stage": dominant}


def live_sessions(db: Session, since: datetime, include_test: bool, org_ok=None) -> dict:
    """The live-sessions tile plus the attention table.

    Attention items are derived from rows that already exist (session status, the latest
    analytics snapshot, recording rows, single-path overrides) and merged with anything an
    operator raised by hand in session_alerts. Deriving rather than duplicating is what
    keeps the console from disagreeing with the host console about the same session.
    """
    sessions = _live_session_rows(db, include_test)
    if org_ok is not None:
        sessions = [s for s in sessions if org_ok(s.org_id)]
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
                "org_id": str(s.org_id),
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
        if org_ok is not None and not org_ok(a.org_id):
            continue
        ev = events.get(a.event_id) or db.get(Event, a.event_id)
        items.append({
            "id": str(a.id),
            "event_id": str(a.event_id),
            "org_id": str(a.org_id) if a.org_id else None,
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
    if org_ok is not None:
        soon = [e for e in soon if org_ok(e.org_id)]
    soon_hosts = _event_hosts(db, [e.id for e in soon])
    unattended = sum(1 for e in soon if not soon_hosts.get(e.id))

    return {
        "live": len(live),
        "paused": len(paused),
        "starting_soon": len(soon),
        "unattended": unattended,
        # The live rows themselves, for the caller's windowed metrics. Not serialized.
        "rows": sessions,
        "attention": items,
        # The attention list is CURRENT operational state: open alerts and conditions on
        # sessions live now. No created_at filter - a session failing now is shown however
        # long ago it started.
        "at_risk": _risk_summary(items),
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

# "unmonitored" ranks with "not_configured": neither is an outage, and neither is evidence of
# health, so a stage carried only by one must not show a confident green node.
#
# _WORST is spelled out rather than built by inverting _RANK. Inversion silently keeps only
# the LAST key per rank, so the moment two statuses share a rank the mapping quietly drops
# one of them — exactly what adding "unmonitored" beside "not_configured" would have done.
# It is only used for incident escalation now (ranks 2 and 3), which is unambiguous.
_RANK = {"ok": 0, "unmonitored": 1, "not_configured": 1, "warn": 2, "down": 3}
_WORST = {2: "warn", 3: "down"}


def stage_health(db: Session, health: dict, since: datetime, stages: tuple[str, ...],
                 until: datetime | None = None) -> list[dict]:
    """Per-stage status (from real service probes + open incidents) and availability
    (from recorded incident windows), plus the per-region matrix the console renders."""
    services = {s["id"]: s for s in health["services"]}
    avail = availability(db, since, until)

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
        # An unintegrated or unmonitored dependency must not read as healthy, and must not
        # read as an outage either — it ranks between ok and warn. The worst MEMBER'S OWN
        # status is kept, so a stage reports "unmonitored" or "not_configured" as whichever
        # it actually is, instead of both collapsing to one label.
        status = max(statuses, key=lambda st: _RANK.get(st, 1))
        for inc in open_by_stage.get(code, []):
            floor = 3 if inc.severity in ("sev1", "sev2") else 2
            if _RANK.get(status, 1) < floor:
                status = _WORST[floor]

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
# The "moderator" gate ("Moderator assigned", mandatory for unrepeatable events) was
# REMOVED here, not renamed. The moderator role is retired, so nobody can be assigned as one
# any more and the gate could never pass again — leaving it would have permanently blocked
# readiness for every unrepeatable-impact event. A second-operator requirement is a real
# operational idea, but expressing it needs a rostering concept that does not exist yet
# (an event has one host list), so inventing one here would be a guessed gate. The "host"
# gate below already covers "somebody is rostered to run this".
_GATES = (
    ("title", "Title set", ("high", "unrepeatable")),
    ("schedule", "Start time scheduled", ("high", "unrepeatable")),
    ("host", "Host assigned", ("high", "unrepeatable")),
    ("recording", "Recording enabled", ("unrepeatable",)),
    ("account", "Account in good standing", ("high", "unrepeatable")),
    ("redundancy", "No unresolved single-path override", ("unrepeatable",)),
)


def _gate_results(ev: Event, hosts: dict, single_path: set) -> list[dict]:
    checks = {
        "title": bool(ev.title),
        "schedule": ev.start_time is not None,
        "host": bool(hosts.get(ev.id)),
        "recording": bool(ev.recording_enabled),
        "account": bool(ev.organization and ev.organization.status != "suspended"),
        "redundancy": ev.id not in single_path,
    }
    return [{"key": key, "label": label, "passed": checks[key],
             "required": (ev.impact or "standard") in required_for}
            for key, label, required_for in _GATES]


def _commercial_gate(db: Session, ev: Event) -> dict | None:
    """Folds the commercial/risk-tier readiness system (crud/commercial.py:evaluate_readiness
    — the same non-waivable check the "armed" transition itself calls) into this gate list,
    so an admin sees both readiness systems in one place instead of needing to know a second
    endpoint exists. Only appears for events that actually carry commercial/risk-tier
    obligations (a service profile, a non-default risk tier, or an audience estimate over the
    default envelope) — an ordinary self-service event's gate list, verdict and query cost are
    all unchanged from before this existed."""
    if ev.service_profile_id is None and (ev.risk_tier or "r0") == "r0" and not ev.expected_audience:
        return None
    order = commercial_crud.get_current_order(db, ev.id)
    evaluation = commercial_crud.evaluate_readiness(db, ev, order)
    label = ("Commercial/risk-tier readiness" if evaluation["ready"]
             else "Commercial/risk-tier readiness: " + "; ".join(evaluation["blocking_reasons"]))
    return {"key": "commercial_readiness", "label": label, "passed": evaluation["ready"], "required": True}


def _verdict(gates: list[dict]) -> str:
    if any(g["required"] and not g["passed"] for g in gates):
        return "blocked"
    if any(not g["passed"] for g in gates):
        return "conditional"
    return "passed"


def _upcoming_stmt(include_test: bool, high_impact_only: bool, horizon: timedelta | None,
                   org_filter: "OrgFilter | None", test_ids):
    now = _now()
    stmt = select(Event).where(
        Event.deleted_at.is_(None),
        Event.status.in_(("scheduled", "published")),
        Event.start_time.isnot(None),
        Event.start_time >= now,
    )
    # Every narrowing happens in SQL, BEFORE the limit. It used to fetch the next limit*3
    # events and filter those in Python, so a run of standard or test-org events ahead of
    # a high-impact one pushed it out of the list entirely.
    if horizon is not None:
        stmt = stmt.where(Event.start_time < now + horizon)
    if high_impact_only:
        stmt = stmt.where(Event.impact.in_(("high", "unrepeatable")))
    if not include_test and test_ids:
        stmt = stmt.where(Event.org_id.notin_(test_ids))
    if org_filter is not None:
        cond = org_filter.cond(Event.org_id)
        if cond is not None:
            stmt = stmt.where(cond)
    return stmt


def event_readiness(db: Session, include_test: bool, limit: int = 8,
                    high_impact_only: bool = True, *, horizon: timedelta | None = None,
                    org_filter: "OrgFilter | None" = None) -> list[dict]:
    """Upcoming events with a readiness verdict computed from their real configuration."""
    test_ids = [] if include_test else _test_org_ids(db)
    stmt = _upcoming_stmt(include_test, high_impact_only, horizon, org_filter, test_ids)
    events = list(db.scalars(stmt.order_by(Event.start_time).limit(limit)).all())
    if not events:
        return []

    ids = [e.id for e in events]
    hosts = _event_hosts(db, ids)
    # The per-event moderator lookup that used to sit here went with the "moderator" gate
    # above — one fewer query per readiness page, since nothing reads it any more.
    single_path = set(db.scalars(
        select(GovernanceRecord.event_id).where(
            GovernanceRecord.kind == "single_path_override",
            GovernanceRecord.resolved_at.is_(None),
            GovernanceRecord.event_id.isnot(None),
        )
    ).all())

    out = []
    for ev in events:
        gates = _gate_results(ev, hosts, single_path)
        commercial_gate = _commercial_gate(db, ev)
        if commercial_gate is not None:
            gates = gates + [commercial_gate]
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

def _incident_out(i: Incident) -> dict:
    return {
        "id": str(i.id), "ref": i.ref, "title": i.title, "detail": i.detail,
        "severity": i.severity, "kind": i.kind, "stage": i.stage, "region": i.region,
        "status": i.status, "commander": i.commander,
        "organization": i.organization.name if i.organization else None,
        "started_at": _aware(i.started_at), "resolved_at": _aware(i.resolved_at),
    }


def incident_summary(db: Session, window: "ops_window.Window", org_filter: "OrgFilter",
                     stages: tuple[str, ...], limit: int = 6) -> dict:
    """Active incidents NOW, plus incidents resolved inside the window.

    `active` is every open incident whatever its start date: an incident that began before
    the selected window is still burning. It used to share one newest-first list of six with
    the window's resolved incidents, so a busy week could push an older, still-open incident
    off the page. The window only decides `resolved_in_window`.

    Filters: region -> the incident's own region, and a region-less (global) incident counts
    in every region; scope -> the incident's lifecycle stage, stage-less incidents in every
    scope; test mode -> incidents attached to a test organization are hidden unless included.
    """
    def narrowed(stmt):
        if org_filter.region:
            stmt = stmt.where(or_(Incident.region == org_filter.region, Incident.region.is_(None)))
        stmt = stmt.where(or_(Incident.stage.in_(stages), Incident.stage.is_(None)))
        if not org_filter.include_test and org_filter.test_ids:
            stmt = stmt.where(or_(Incident.org_id.is_(None),
                                  Incident.org_id.notin_(org_filter.test_ids)))
        return stmt

    active = db.scalars(narrowed(select(Incident).where(Incident.status != "resolved"))
                        .order_by(Incident.started_at.desc())).all()
    resolved_q = narrowed(select(Incident).where(
        Incident.status == "resolved",
        Incident.resolved_at >= window.since, Incident.resolved_at < window.until))
    resolved_count = db.scalar(select(func.count()).select_from(resolved_q.subquery())) or 0
    resolved = db.scalars(resolved_q.order_by(Incident.resolved_at.desc()).limit(limit)).all()
    return {
        "semantics": "current",
        "active": [_incident_out(i) for i in active],
        "active_count": len(active),
        "resolved_in_window": [_incident_out(i) for i in resolved],
        "resolved_count": int(resolved_count),
    }


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
            Subscription.status.in_(_TRIAL_OR_PAST_DUE),
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
           "tone": "warning" if s.status in _TRIALING_STATES else "danger",
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

def active_elevation_scopes(db: Session, user: User) -> set[str]:
    """Every scope this user is currently elevated for, across ALL live sessions.

    Plural on purpose. `current_elevation` below returns only the NEWEST session because the
    console badge can show one thing — but a super admin legitimately holds more than one at
    a time: services/support_access opens its own elevation when a tenant support session
    starts, scoped to that case. Authorizing from the newest alone meant starting a support
    session silently revoked an unrelated platform elevation, because the support one shadowed
    it. Whether an action is permitted is a question about the SET, not about whichever row
    happens to be most recent.

    Expiry is applied here, so a lapsed session contributes nothing.
    """
    rows = db.scalars(
        select(ElevationSession).where(
            ElevationSession.user_id == user.id,
            ElevationSession.ended_at.is_(None),
            ElevationSession.expires_at > _now(),
        )
    ).all()
    out: set[str] = set()
    for row in rows:
        if row.scope:
            out.add(row.scope)
        out.update(row.scopes or [])
    return out


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
        # What the SERVER will actually authorize, unioned across every live session —
        # the same set require_elevation() checks against. `scope`/`scopes` above describe
        # this one row and are what the badge prints; a console that disabled a control
        # from those alone would block an operator who legitimately holds a second grant
        # (see active_elevation_scopes). The UI gate must read the same truth the gate does.
        "granted_scopes": sorted(active_elevation_scopes(db, user)),
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
        # `unmonitored` travels with the verdict so the header can say what "operational"
        # actually covers. Without it, "ok" read as "everything is fine" while three of the
        # dependencies in that count had never been checked.
        "health": {"overall": health["overall"], "degraded": degraded,
                   "total": len(health["services"]),
                   "unmonitored": health.get("unmonitored", 0)},
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


# ── windowed metrics (window rules: services/ops_window) ─────────────────────────

def _as_uuid(value):
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class OrgFilter:
    """Region + test-mode narrowing over organizations, resolved once per request.

    Region comes from Organization.region (free text, mapped by region_of). An organization
    whose region maps to nothing belongs to NO region, so a region filter excludes it rather
    than guessing one. Test organizations are excluded unless include_test.
    """

    def __init__(self, db: Session, region: str | None, include_test: bool):
        self.region = region
        self.include_test = include_test
        self.active = bool(region) or not include_test
        self.allowed: set = set()
        self.test_ids: set = set()
        for oid, org_region, is_test in db.execute(
                select(Organization.id, Organization.region, Organization.is_test)).all():
            if is_test:
                self.test_ids.add(oid)
                if not include_test:
                    continue
            if region and region_of(org_region) != region:
                continue
            self.allowed.add(oid)

    def ok(self, org_id) -> bool:
        if not self.active:
            return True
        return org_id is not None and _as_uuid(org_id) in self.allowed

    def cond(self, col):
        """SQL condition on an org_id column, or None when nothing is narrowed."""
        if self.region:
            return col.in_(self.allowed) if self.allowed else false()
        if not self.include_test and self.test_ids:
            return col.notin_(self.test_ids)
        return None


def _narrow(stmt, cond):
    return stmt if cond is None else stmt.where(cond)


def _session_intervals(db: Session, win: "ops_window.Window", org_filter: OrgFilter):
    """Sessions that were live at any point inside the window: started before it ended and
    not ended before it began. BroadcastSession is authoritative for this, so an interval
    with no session is a MEASURED zero."""
    stmt = select(BroadcastSession.started_at, BroadcastSession.ended_at).where(
        BroadcastSession.started_at.isnot(None),
        BroadcastSession.started_at < win.until,
        or_(BroadcastSession.ended_at.is_(None), BroadcastSession.ended_at > win.since),
    )
    stmt = _narrow(stmt, org_filter.cond(BroadcastSession.org_id))
    return [(_aware(a), _aware(b)) for a, b in db.execute(stmt).all()]


def _overlaps(intervals, start: datetime, end: datetime) -> int:
    return sum(1 for a, b in intervals if a < end and (b is None or b > start))


def sessions_in_window(db: Session, win: "ops_window.Window", org_filter: OrgFilter) -> dict:
    current = _session_intervals(db, win, org_filter)
    previous = _session_intervals(db, win.previous, org_filter)
    step = timedelta(seconds=win.bucket_seconds)
    counts = {i: _overlaps(current, b0, min(b0 + step, win.until))
              for i, b0 in enumerate(win.bucket_starts())}
    return {
        "in_period": len(current),
        # Sessions live at any point in each bucket.
        "series": ops_window.series(win, counts, fill="zero"),
        "comparison": ops_window.compare(len(current), len(previous)),
        "intervals": current,
    }


AUDIENCE_SLOT_SECONDS = 30
_SLOT = literal_column(str(AUDIENCE_SLOT_SECONDS))


def _audience_slots(db: Session, win: "ops_window.Window", org_filter: OrgFilter):
    """Platform-wide concurrent audience per 30-second slot inside the window.

    The broadcast sampler writes each event's snapshot in its OWN transaction, so rows from
    one tick carry slightly different created_at values. The old peak grouped by exact
    created_at, which never summed two events together - it reported the single largest
    event as the platform peak. Here each event contributes its highest sample per slot
    (it is sampled every 15s, so at least once per 30s slot while live) and those are summed.
    """
    slot = func.floor(func.extract("epoch", AnalyticsSnapshot.created_at) / _SLOT)
    per_event = _narrow(
        select(AnalyticsSnapshot.event_id.label("event_id"), slot.label("slot"),
               func.max(AnalyticsSnapshot.viewers).label("viewers"))
        .where(AnalyticsSnapshot.created_at >= win.since, AnalyticsSnapshot.created_at < win.until),
        org_filter.cond(AnalyticsSnapshot.org_id),
    ).group_by(AnalyticsSnapshot.event_id, slot).subquery()
    rows = db.execute(select(per_event.c.slot, func.sum(per_event.c.viewers),
                             func.count())
                      .group_by(per_event.c.slot)).all()
    slots = sorted((datetime.fromtimestamp(float(s) * AUDIENCE_SLOT_SECONDS, tz=timezone.utc),
                    int(v or 0)) for s, v, _n in rows)
    # (event, slot) pairs actually sampled - the numerator of sampling coverage.
    return slots, sum(int(n) for _s, _v, n in rows)


def _expected_slots(intervals, win: "ops_window.Window") -> float:
    """(session, slot) pairs the sampler SHOULD have written: broadcast time inside the
    window, in slots. Sessions still open run to the window end."""
    secs = 0.0
    for a, b in intervals:
        start, end = max(a, win.since), min(b or win.until, win.until)
        if end > start:
            secs += (end - start).total_seconds()
    return secs / AUDIENCE_SLOT_SECONDS


def audience_window(db: Session, win: "ops_window.Window", org_filter: OrgFilter,
                    live_rows: list, intervals: list) -> dict:
    # NOW: the latest sample (<= 60s old) of every session live now.
    live_events = {s.event_id for s in live_rows}
    fresh = _latest_snapshot_per_event(db, list(live_events), _now() - timedelta(seconds=60))
    if not live_events:
        current, current_state = 0, "measured"          # nothing live: nobody can be watching
    elif not fresh:
        current, current_state = None, "not_sampled"    # live, but no recent sample
    else:
        current = sum(s.viewers for s in fresh.values())
        current_state = "measured" if len(fresh) == len(live_events) else "partial"
    largest_event_id, largest = None, 0
    for event_id, snap in fresh.items():
        if snap.viewers > largest:
            largest_event_id, largest = event_id, snap.viewers
    largest_title = (db.scalar(select(Event.title).where(Event.id == largest_event_id))
                     if largest_event_id else None)

    def summarize(w, ivals):
        slots, sampled = _audience_slots(db, w, org_filter)
        expected = _expected_slots(ivals, w)
        coverage = round(min(1.0, sampled / expected) * 100, 1) if expected else None
        if slots:
            vals = [v for _, v in slots]
            # A peak read from a fraction of the broadcast time is a floor, not the peak, so
            # thin sampling is reported as partial rather than as a measurement.
            state = "partial" if coverage is not None and coverage < 90 else "measured"
            return slots, max(vals), round(sum(vals) / len(vals), 1), state, coverage
        if not ivals:
            return slots, 0, 0.0, "measured", None      # no broadcast in the window at all
        return slots, None, None, "not_sampled", 0.0    # broadcasts ran, nothing was sampled

    slots, peak, average, window_state, coverage = summarize(win, intervals)
    _, prev_peak, _, prev_state, _ = summarize(
        win.previous, _session_intervals(db, win.previous, org_filter))
    # Each session's own recorded peak (BroadcastSession.peak_viewers, from live presence).
    # Per session and over the WHOLE session, so it is reported beside - never as - the
    # platform-wide concurrent peak.
    session_peak = db.scalar(_narrow(
        select(func.max(BroadcastSession.peak_viewers)).where(
            BroadcastSession.started_at.isnot(None),
            BroadcastSession.started_at < win.until,
            or_(BroadcastSession.ended_at.is_(None), BroadcastSession.ended_at > win.since)),
        org_filter.cond(BroadcastSession.org_id)))

    step = timedelta(seconds=win.bucket_seconds)
    values: dict[int, float] = {}
    for ts, v in slots:
        i = win.bucket_index(ts)
        if i is not None:
            values[i] = max(values.get(i, 0), v)
    for i, b0 in enumerate(win.bucket_starts()):
        # A bucket with no sample is a zero only when no session was live in it; otherwise
        # it is a sampling gap and stays out of the line.
        if i not in values and not _overlaps(intervals, b0, min(b0 + step, win.until)):
            values[i] = 0

    return {
        "current": current,
        "current_state": current_state,
        "unsampled_now": len(live_events) - len(fresh),
        "peak": peak,
        "average": average,
        "window_state": window_state,
        "sampling_coverage_pct": coverage,
        "highest_session_peak": session_peak,
        "largest_session": largest or None,
        "largest_session_title": largest_title,
        "series": ops_window.series(win, values, fill="gap"),
        # Only compared when both windows were fully measured: a partial peak is a floor.
        "comparison": ops_window.compare(
            peak if window_state == "measured" else None,
            prev_peak if prev_state == "measured" else None),
    }


# The last minutes are not flushed yet (flush interval + the minute still being counted), so
# they are not expected to be covered.
API_FLUSH_LAG = timedelta(minutes=2)


def _hist_percentile(totals: dict, q: float) -> tuple[int | None, bool]:
    ordered = [(b, float(totals.get(f"{API_LATENCY}{b}", 0) or 0)) for b in LATENCY_BOUNDS_MS]
    ordered.append((None, float(totals.get(f"{API_LATENCY}inf", 0) or 0)))
    total = sum(n for _, n in ordered)
    if total <= 0:
        return None, False
    target, cum = math.ceil(q * total), 0.0
    for bound, n in ordered:
        cum += n
        if cum >= target:
            return (bound, False) if bound is not None else (LATENCY_BOUNDS_MS[-1], True)
    return LATENCY_BOUNDS_MS[-1], True


def _api_totals(db: Session, win: "ops_window.Window") -> tuple[dict, dict]:
    in_window = (PlatformMetric.recorded_at >= win.since, PlatformMetric.recorded_at < win.until)
    totals = dict(db.execute(
        select(PlatformMetric.name, func.sum(PlatformMetric.value)).where(
            *in_window,
            or_(PlatformMetric.name.in_((API_REQUESTS, API_ERRORS)),
                PlatformMetric.name.like(f"{API_LATENCY}%")))
        .group_by(PlatformMetric.name)).all())
    minutes = dict(db.execute(
        select(PlatformMetric.recorded_at, func.sum(PlatformMetric.value))
        .where(*in_window, PlatformMetric.name == API_REQUESTS)
        .group_by(PlatformMetric.recorded_at)).all())
    return totals, minutes


def api_window(db: Session, win: "ops_window.Window") -> dict:
    """Requests, error rate, p95 and the sparkline, ALL from the same window's rows."""
    totals, minutes = _api_totals(db, win)
    expected_end = min(win.until, _now() - API_FLUSH_LAG)
    expected = max(0, int((expected_end - win.since).total_seconds() // 60))
    covered = len(minutes)
    if covered == 0:
        state = "not_measured"
    elif expected and covered < 0.9 * expected:
        state = "partial"
    else:
        state = "measured"

    requests = int(totals.get(API_REQUESTS, 0) or 0) if covered else None
    errors = int(totals.get(API_ERRORS, 0) or 0) if covered else None
    p95, overflow = _hist_percentile(totals, 0.95) if requests else (None, False)

    values: dict[int, float] = {}
    for ts, n in minutes.items():
        i = win.bucket_index(ts)
        if i is not None:
            values[i] = values.get(i, 0) + float(n or 0)

    prev_totals, prev_minutes = _api_totals(db, win.previous)
    prev_requests = int(prev_totals.get(API_REQUESTS, 0) or 0) if prev_minutes else None
    return {
        "measurement_state": state,
        "coverage_pct": round(100 * min(covered, expected) / expected, 1) if expected else None,
        "requests": requests,
        "errors": errors,
        # None when nothing was requested: an error RATE over zero requests does not exist.
        "value": round(100 * errors / requests, 3) if requests else None,
        "p95_ms": p95,
        "p95_basis": "histogram_upper_bound",
        "p95_overflow": overflow,
        # Buckets with collected minutes only; uncollected time is a gap, not zero traffic.
        "series": ops_window.series(win, values, fill="gap"),
        "comparison": ops_window.compare(requests, prev_requests),
    }


def health_window(db: Session, win: "ops_window.Window") -> dict:
    """Healthy-service count over the window, from the sampler's platform_health_ok rows."""
    def samples(w):
        return [(_aware(t), float(v)) for t, v in db.execute(
            select(PlatformMetric.recorded_at, PlatformMetric.value).where(
                PlatformMetric.name == "platform_health_ok",
                PlatformMetric.recorded_at >= w.since, PlatformMetric.recorded_at < w.until)
        ).all()]

    cur, prev = samples(win), samples(win.previous)
    grouped: dict[int, list[float]] = {}
    for ts, v in cur:
        i = win.bucket_index(ts)
        if i is not None:
            grouped.setdefault(i, []).append(v)
    avg = (lambda rows: round(sum(v for _, v in rows) / len(rows), 2) if rows else None)
    return {
        "series": ops_window.series(win, {i: sum(v) / len(v) for i, v in grouped.items()},
                                    fill="gap"),
        "average_ok": avg(cur),
        "samples": len(cur),
        "comparison": ops_window.compare(avg(cur), avg(prev)),
    }


UPCOMING_HORIZON = timedelta(days=7)


def upcoming_high_impact(db: Session, org_filter: OrgFilter, include_test: bool) -> dict:
    """FORWARD-looking: high-impact events starting in the next UPCOMING_HORIZON. The page's
    range selector looks backward and never applies here."""
    test_ids = [] if include_test else list(org_filter.test_ids)
    total = db.scalar(select(func.count()).select_from(
        _upcoming_stmt(include_test, True, UPCOMING_HORIZON, org_filter, test_ids).subquery())) or 0
    now = _now()
    return {
        "semantics": "upcoming",
        "from": now,
        "to": now + UPCOMING_HORIZON,
        "total": int(total),
        "events": event_readiness(db, include_test, horizon=UPCOMING_HORIZON,
                                  org_filter=org_filter),
    }


def _scoped_health(health: dict, stages: tuple[str, ...], scope: str) -> dict:
    ids = {sid for code, _label, sids in STAGES if code in stages for sid in sids}
    services = health["services"] if scope == "core_live" else [
        s for s in health["services"] if s["id"] in ids]
    by = {st: sum(1 for s in services if s["status"] == st)
          for st in ("ok", "warn", "down", "unmonitored", "not_configured")}
    if scope == "core_live":
        overall = health["overall"]
    else:
        probed = [s["status"] for s in services
                  if s["status"] in ("ok", "warn", "down") and s["id"] not in INFORMATIONAL_SERVICES]
        overall = ("down" if "down" in probed else "warn" if "warn" in probed
                   else "ok" if probed else "unknown")
    return {"status": overall, "total": len(services), **by}


# ── the page payload ──────────────────────────────────────────────────────────

def command_center(db: Session, user: User, range_: str = "live", region: str | None = None,
                   scope: str = "core_live", include_test: bool = False,
                   since: datetime | None = None, until: datetime | None = None) -> dict:
    """One call for the whole Command Center, over ONE resolved window.

    Every tile says what kind of number it is (`semantics`):
      current   state NOW, whatever range is selected: platform health, live sessions,
                at-risk sessions, active incidents. The range only drives their trend or
                their clearly-labelled windowed secondary figure.
      window    an aggregate over the selected window: concurrent-audience peak/average,
                API requests / error rate / p95, sessions in the period.
      upcoming  forward-looking over a fixed horizon: high-impact events.
    and which filters genuinely apply to it (`applies`), so the console can say "not
    region-specific" instead of implying a filter it cannot honour. `measurement_state`
    separates a measured zero from "nothing was measured".

    Raises ops_window.WindowError for an impossible window (the router answers 400).
    """
    generated_at = _now()
    win = ops_window.resolve(range_, since, until, now=generated_at)
    stages = SCOPES.get(scope, SCOPES["core_live"])
    orgs = OrgFilter(db, region, include_test)

    health = admin_svc.platform_health(db)
    scoped = _scoped_health(health, stages, scope)
    hist = health_window(db, win) if scope == "core_live" else None

    sessions = live_sessions(db, win.since, include_test, org_ok=orgs.ok if orgs.active else None)
    live_rows = sessions.pop("rows")
    attention = [i for i in sessions["attention"] if not i["stage"] or i["stage"] in stages]
    in_window = sessions_in_window(db, win, orgs)
    aud = audience_window(db, win, orgs, live_rows, in_window.pop("intervals"))
    api = api_window(db, win)
    upcoming = upcoming_high_impact(db, orgs, include_test)
    incident_view = incident_summary(db, win, orgs, stages)

    return {
        "generated_at": generated_at,
        "range": win.as_dict(),
        # Kept in its original shape for the other pages and for exports.
        "window": {"range": range_, "since": win.since, "until": win.until,
                   "region": region, "scope": scope, "include_test": include_test},
        "lifecycle": stage_health(db, health, win.since, stages, until=win.until),
        "regions": [{"code": c, "label": l} for c, l in REGIONS],
        "kpis": {
            "platform_health": {
                "semantics": "current",
                **scoped,
                "unavailable": scoped["down"],
                "series": hist["series"] if hist else [],
                "average_ok": hist["average_ok"] if hist else None,
                "comparison": hist["comparison"] if hist else None,
                "history_note": None if hist else (
                    "Health history is sampled platform-wide, so the trend is shown for "
                    "Core + Live Events only."),
                "applies": {"range": "trend", "region": False, "scope": True,
                            "include_test": False},
            },
            "live_sessions": {
                "semantics": "current",
                "measurement_state": "measured",
                "value": sessions["live"],
                "paused": sessions["paused"],
                "starting_soon": sessions["starting_soon"],
                "unattended": sessions["unattended"],
                "in_period": in_window["in_period"],
                "series": in_window["series"],
                "comparison": in_window["comparison"],
                "applies": {"range": "secondary", "region": True, "scope": False,
                            "include_test": True},
            },
            "at_risk_sessions": {
                "semantics": "current",
                **_risk_summary(attention),
                "applies": {"range": False, "region": True, "scope": True, "include_test": True},
            },
            "concurrent_audience": {
                "semantics": "current" if range_ == "live" else "window",
                # Live: the audience now. Any other range: the peak inside the window.
                "value": aud["current"] if range_ == "live" else aud["peak"],
                "measurement_state": aud["current_state"] if range_ == "live" else aud["window_state"],
                **aud,
                "applies": {"range": True, "region": True, "scope": False, "include_test": True},
            },
            "playback_quality": {
                "semantics": "window",
                "measurement_state": "not_measured",
                "value": metric_latest(db, "playback_quality_pct"),
                "series": [],
                "note": "Playback QoE needs a player beacon writing platform_metrics "
                        "(no ingest yet).",
            },
            "api_health": {
                "semantics": "window",
                **api,
                "note": "Every process's requests, flushed per minute. Error = HTTP 5xx.",
                "applies": {"range": True, "region": False, "scope": False,
                            "include_test": False},
            },
        },
        "attention": attention,
        # Active incidents first (all of them), then the window's resolved ones.
        "incidents": incident_view["active"] + incident_view["resolved_in_window"],
        "incident_summary": incident_view,
        "action_queues": action_queues(db, upcoming["events"]),
        "governance": governance_exposure(db),
        "privileged_activity": privileged_activity(db),
        "upcoming_events": upcoming["events"],
        "upcoming": {k: v for k, v in upcoming.items() if k != "events"},
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
    # API traffic is no longer sampled here: this ticker runs on the leader only, so it saw
    # one process's slice through overlapping 5-minute windows. Every process now flushes its
    # own per-minute counts (flush_request_stats).
    rows = [PlatformMetric(name="platform_health_ok", value=float(ok))]

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
        # Provider health rides this same ticker rather than a new scheduler. It throttles
        # itself to PROBE_INTERVAL, so most ticks return immediately.
        try:
            from . import provider_health
            await provider_health.probe_round(SessionLocal)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed probe round must not kill the sampler
            log.exception("provider health probe round failed")


def flush_request_stats(session_factory, stats: RequestStats | None = None) -> int:
    """Write this process's completed minutes to platform_metrics. Returns rows written.
    A failed write loses those minutes - reported later as a coverage gap, never as zero."""
    stats = stats or request_stats
    entries = stats.drain()
    if not entries:
        return 0
    rows = []
    for minute, requests, errors, hist in entries:
        at = datetime.fromtimestamp(minute * 60, tz=timezone.utc)
        rows.append(PlatformMetric(name=API_REQUESTS, value=float(requests), recorded_at=at))
        if errors:
            rows.append(PlatformMetric(name=API_ERRORS, value=float(errors), recorded_at=at))
        for i, n in enumerate(hist):
            if n:
                rows.append(PlatformMetric(name=_lat_name(i), value=float(n), recorded_at=at))
    db = session_factory()
    try:
        db.add_all(rows)
        db.commit()
        return len(rows)
    except Exception:  # noqa: BLE001 - a failed flush must not kill the flusher
        db.rollback()
        log.exception("request stats flush failed; %d minute(s) not recorded", len(entries))
        return 0
    finally:
        db.close()


async def run_request_stats_flusher(interval: float = 60.0) -> None:
    """Per PROCESS, not leader-elected: every process serves requests and holds its own
    counts. Started from main.lifespan."""
    from ..db import SessionLocal

    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(flush_request_stats, SessionLocal)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("request stats flusher tick failed")


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
