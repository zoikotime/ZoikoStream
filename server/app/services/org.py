"""Organization Overview aggregation — the org-admin counterpart to services/ops.py.

Every query here is scoped to ONE organization, and that org_id always comes from the
caller's JWT (never the request), so an org admin cannot read another tenant's data and
cannot see the platform-wide rollups the Super Admin console shows.

Same honesty rule as the rest of the codebase: a value with no source is None plus a note
explaining what would feed it, never a plausible constant.

What is genuinely measured here:
  * sessions, audience         <- BroadcastSession + AnalyticsSnapshot (real presence)
  * media assets              <- LiveRecording rows and their status
  * entitlement usage         <- Organization counters vs the subscribed Plan's limits
  * stage usage + health      <- which lifecycle stages this org actually touches, crossed
                                 with the platform probes and any Incident scoped to it
  * attention items           <- expiring credentials, usage thresholds, pending invites,
                                 unresolved governance records, blocked upcoming events
  * event readiness           <- services.ops gates, evaluated per event
  * security / support posture <- org.security flags, Invitation and SupportTicket rows

Documented gaps (no ingest in this stack — see ORG_GAPS at the bottom):
  per-org API success/p95, windowed delivery metering,
  per-application error rates, SDK-version exposure, rate-limit event history,
  ingest protocol/region per session, self-service vs managed classification,
  security findings, access-review schedule, maintenance windows.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AnalyticsSnapshot,
    BroadcastSession,
    Event,
    EventRegistration,
    GovernanceRecord,
    Incident,
    Invitation,
    LiveRecording,
    Organization,
    Plan,
    Subscription,
    SupportTicket,
    User,
    WebhookDelivery,
    WebhookEndpoint,
)
from . import admin as admin_svc
from . import ops as ops_svc
from .broadcast import engagement_score

# Ranges the overview toolbar offers. Mirrors the console's vocabulary.
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24),
          "7d": timedelta(days=7), "30d": timedelta(days=30)}

# Ranges the Analytics page offers. A separate vocabulary from RANGES above (that one
# drives the Overview toolbar's operational window; this one drives historical reporting).
ANALYTICS_RANGES = {"7d": timedelta(days=7), "30d": timedelta(days=30),
                     "90d": timedelta(days=90), "12m": timedelta(days=365)}

# Threshold at which an entitlement becomes an "attention required" item.
USAGE_WARN_RATIO = 0.8
# A credential inside this window is surfaced for rotation.
CREDENTIAL_WARN_DAYS = 14


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _days_until(when: datetime, now: datetime) -> int:
    """Whole days remaining, rounded UP. timedelta.days truncates, so a deadline 5.99 days
    away would read "5 days" the instant it was set to 6 — a deadline must never appear
    closer than it is."""
    return math.ceil((when - now).total_seconds() / 86400)


# ── implicit workspace ────────────────────────────────────────────────────────

def workspace(org: Organization) -> dict:
    """This platform has no Workspace entity: an organization IS its production
    workspace. Returned in workspace shape so the switcher and the "all workspaces"
    filter are honest about what exists today, and so introducing real workspaces later
    is a data change rather than a UI change."""
    return {
        "id": "production",
        "slug": "production",
        "label": "production",
        "name": org.name,
        "is_default": True,
    }


def workspaces(org: Organization) -> list[dict]:
    return [workspace(org)]


# ── which lifecycle stages this org actually uses ─────────────────────────────

def _stage_usage(db: Session, org_id) -> dict:
    """A stage is "in use" when this org has real rows that exercise it. The overview dims
    the stages an org never touches instead of reporting health for something it doesn't
    use — a green Preserve tick means nothing to an org that has never recorded."""
    has_sessions = bool(db.scalar(
        select(func.count(BroadcastSession.id)).where(BroadcastSession.org_id == org_id)
    ))
    has_recordings = bool(db.scalar(
        select(func.count(LiveRecording.id)).where(LiveRecording.org_id == org_id)
    ))
    has_stored = bool(db.scalar(
        select(func.count(LiveRecording.id)).where(
            LiveRecording.org_id == org_id, LiveRecording.file_url.isnot(None))
    ))
    has_analytics = bool(db.scalar(
        select(func.count(AnalyticsSnapshot.id)).where(AnalyticsSnapshot.org_id == org_id)
    ))
    org = db.get(Organization, org_id)
    has_api = bool(org and org.api_keys)

    return {
        "contribute": has_sessions,
        "ingest": has_sessions,
        "produce": has_recordings,
        "secure": True,          # every org authenticates
        "deliver": has_sessions,
        "understand": has_analytics,
        "preserve": has_stored,
        "platform": has_api,     # the developer platform surface
    }


def stage_health(db: Session, org_id, since: datetime) -> list[dict]:
    """Per-stage status for this org: the platform probe result, escalated by any Incident
    scoped to this organization (or global), and flagged with whether the org uses it."""
    health = admin_svc.platform_health(db)
    usage = _stage_usage(db, org_id)
    stages = ops_svc.stage_health(db, health, since, tuple(s[0] for s in ops_svc.STAGES))

    # Incidents that affect this org: its own, plus platform-wide ones (org_id NULL).
    mine = db.scalars(
        select(Incident).where(
            Incident.status != "resolved",
            or_(Incident.org_id == org_id, Incident.org_id.is_(None)),
        )
    ).all()
    by_stage: dict[str, list[Incident]] = {}
    for inc in mine:
        if inc.stage:
            by_stage.setdefault(inc.stage, []).append(inc)

    out = []
    for s in stages:
        incidents = by_stage.get(s["stage"], [])
        status = s["status"]
        detail = None
        for inc in incidents:
            status = ops_svc._WORST[max(
                ops_svc._RANK[status], 3 if inc.severity in ("sev1", "sev2") else 2)]
            detail = inc.title
        out.append({
            "stage": s["stage"],
            "label": s["label"],
            "status": status,
            "in_use": usage.get(s["stage"], False),
            "availability": s["availability"],
            "detail": detail,
            "open_incidents": len(incidents),
        })
    return out


def service_health(stages: list[dict]) -> dict:
    """One headline verdict, computed only from the stages this org actually uses —
    excluding stages that are entirely informational (see ops.INFORMATIONAL_STAGES), so a
    roadmap feature nobody has built yet (e.g. Produce <- background workers) can't pin an
    otherwise-healthy org at "Not configured" forever. Those stages still show up honestly
    in the per-stage breakdown; they just don't get to speak for the whole org."""
    used = [s for s in stages if s["in_use"] and s["stage"] not in ops_svc.INFORMATIONAL_STAGES]
    if not used:
        return {"status": "ok", "label": "No services in use", "cause": None}
    worst = max(used, key=lambda s: ops_svc._RANK.get(s["status"], 1))
    label = {"ok": "Healthy", "warn": "Degraded", "down": "Disrupted",
             "not_configured": "Not configured"}.get(worst["status"], "Unknown")
    cause = None
    if worst["status"] in ("warn", "down"):
        cause = f"{worst['label']} · {worst['detail'] or 'degraded'}"
    return {"status": worst["status"], "label": label, "cause": cause}


# ── entitlements ──────────────────────────────────────────────────────────────

def _plan(db: Session, org_id) -> tuple[Subscription | None, Plan | None]:
    sub = db.scalar(
        select(Subscription).where(
            Subscription.org_id == org_id,
            Subscription.status.in_(("active", "trial", "past_due")),
        ).order_by(Subscription.started_at.desc())
    )
    return sub, (sub.plan if sub else None)


def _streaming_hours(db: Session, org_id) -> float:
    """Broadcast hours this org has run, from real session timestamps (live sessions count
    up to now)."""
    now = _now()
    rows = db.scalars(
        select(BroadcastSession).where(
            BroadcastSession.org_id == org_id, BroadcastSession.started_at.isnot(None))
    ).all()
    secs = 0.0
    for s in rows:
        start = _aware(s.started_at)
        end = _aware(s.ended_at) or now
        if start and end > start:
            secs += (end - start).total_seconds() - (s.paused_ms or 0) / 1000
    return round(max(secs, 0) / 3600, 1)


def entitlements(db: Session, org: Organization) -> dict:
    """Usage against the subscribed plan's real limits. Only the three limits the Plan
    model actually carries are reported — a bar with an invented ceiling would be worse
    than no bar."""
    sub, plan = _plan(db, org.id)
    members = db.scalar(
        select(func.count(User.id)).where(User.org_id == org.id, User.deleted_at.is_(None))
    ) or 0
    hours = _streaming_hours(db, org.id)

    def bar(label, used, limit, unit):
        pct = round(100 * used / limit, 1) if limit else None
        return {"label": label, "used": used, "limit": limit, "unit": unit, "percent": pct}

    items = [
        bar("Storage", round(float(org.storage_used_gb or 0), 1),
            plan.max_storage_gb if plan else None, "GB"),
        bar("Members", members, plan.max_users if plan else None, "seats"),
        bar("Streaming hours", hours,
            plan.max_streaming_hours if plan else None, "hrs"),
    ]
    highest = max((i["percent"] for i in items if i["percent"] is not None), default=None)
    return {
        "plan": plan.name if plan else None,
        "plan_slug": plan.slug if plan else None,
        "status": sub.status if sub else None,
        "trial_ends_at": _aware(sub.trial_ends_at) if sub else None,
        "current_period_end": _aware(sub.current_period_end) if sub else None,
        "items": items,
        "highest_percent": highest,
        # Delivery volume is a lifetime counter on the org row; there is no metering
        # pipeline writing windowed bandwidth, so it is reported as a total, not "last 24h".
        "delivery_gb_total": round(float(org.bandwidth_gb or 0), 1),
        "delivery_windowed": None,
        "delivery_note": "Windowed delivery volume needs a metering pipeline (not integrated).",
    }


def _bucket_label(dt: datetime, range_key: str) -> str:
    return dt.strftime("%b %Y") if range_key == "12m" else dt.strftime("%b %d")


def analytics(db: Session, org: Organization, range_key: str = "30d") -> dict:
    """Real per-org historical analytics for /organization/analytics, from Event +
    BroadcastSession + AnalyticsSnapshot — the same tables the live console's own
    analytics_now() trusts, just rolled up across events instead of live-only.

    Device/location/traffic-source breakdowns are NOT included: those come from the
    WebSocket handshake's User-Agent and are only ever held in-memory presence for a
    currently-live event (see broadcast.classify_ua / _distribution) — nothing persists
    them historically, so a cross-event breakdown would have to be invented. Honest None
    + note, same convention as GeoIP elsewhere in this stack.
    """
    since = _now() - ANALYTICS_RANGES.get(range_key, ANALYTICS_RANGES["30d"])

    events = db.scalars(
        select(Event).where(Event.org_id == org.id, Event.deleted_at.is_(None),
                             Event.start_time.isnot(None), Event.start_time >= since)
        .order_by(Event.start_time)
    ).all()
    event_ids = [e.id for e in events]

    sessions_by_event: dict = {}
    if event_ids:
        for s in db.scalars(select(BroadcastSession).where(BroadcastSession.event_id.in_(event_ids))):
            sessions_by_event.setdefault(s.event_id, []).append(s)

    snapshots_by_event: dict = {}
    if event_ids:
        for snap in db.scalars(select(AnalyticsSnapshot).where(AnalyticsSnapshot.event_id.in_(event_ids))):
            snapshots_by_event.setdefault(snap.event_id, []).append(snap)

    # Concurrent viewers integrated over the sampler's 15s interval = a real watch-hours
    # measurement (area under the viewer-count curve), not a per-user estimate.
    SAMPLE_HOURS = 15 / 3600
    per_event: dict = {}
    for e in events:
        peak = max((s.peak_viewers for s in sessions_by_event.get(e.id, [])), default=0)
        snaps = snapshots_by_event.get(e.id, [])
        per_event[e.id] = {
            "title": e.title,
            "start": _aware(e.start_time),
            "peak": peak,
            "watch_hours": round(sum(s.viewers for s in snaps) * SAMPLE_HOURS, 1),
            "messages": sum(s.messages for s in snaps),
            "questions": sum(s.questions for s in snaps),
            "reactions": sum(s.reactions for s in snaps),
        }

    buckets: dict[str, dict] = {}
    bucket_order: list[str] = []
    for e in events:
        label = _bucket_label(per_event[e.id]["start"], range_key)
        if label not in buckets:
            buckets[label] = {"viewers": 0, "watch_hours": 0.0}
            bucket_order.append(label)
        buckets[label]["viewers"] += per_event[e.id]["peak"]
        buckets[label]["watch_hours"] += per_event[e.id]["watch_hours"]

    def event_engagement(info: dict) -> int:
        return engagement_score(
            {"messages": info["messages"], "questions": info["questions"],
             "poll_votes": 0, "reactions": info["reactions"]}, info["peak"])

    engaged = [event_engagement(i) for i in per_event.values() if i["peak"] > 0]
    ranked = sorted(per_event.items(), key=lambda kv: -kv[1]["peak"])

    return {
        "range": range_key,
        "since": since,
        "summary": {
            "viewers": sum(i["peak"] for i in per_event.values()),
            "watch_hours": round(sum(i["watch_hours"] for i in per_event.values()), 1),
            "peak": max((i["peak"] for i in per_event.values()), default=0),
            "engagement": round(sum(engaged) / len(engaged)) if engaged else 0,
        },
        "trends": {
            "viewership": [{"label": l, "value": buckets[l]["viewers"]} for l in bucket_order],
            "watch_time": [{"label": l, "value": round(buckets[l]["watch_hours"], 1)} for l in bucket_order],
        },
        "top_events": [{"label": i["title"], "value": i["peak"]} for _, i in ranked[:5] if i["peak"] > 0],
        "reports": [
            {"id": str(eid), "event": i["title"],
             "date": i["start"].date().isoformat() if i["start"] else None,
             "viewers": i["peak"], "watch_hours": i["watch_hours"],
             "engagement": event_engagement(i)}
            for eid, i in ranked
        ],
        "devices": None, "locations": None, "traffic_sources": None,
        "breakdowns_note": "Device, location and traffic-source breakdowns need a metering/"
                            "GeoIP pipeline that isn't integrated yet.",
    }


def audience_attendance(db: Session, org: Organization, range_key: str = "30d") -> dict:
    """Real cross-event attendance aggregate for /organization/audience-attendance,
    reusing analytics()'s own summary rather than re-querying BroadcastSession/
    AnalyticsSnapshot a second time — same range handling, same numbers underneath.

    Honesty rule, same as analytics()'s own docstring and broadcast.engagement_score/
    _watch_seconds: no table anywhere records a specific person's watch duration once a
    room ends (moderation.feed_activity deliberately never persists joins/leaves), so
    `unique_attendees`/`avg_watch_minutes` cannot be true per-person measurements — every
    caller-facing figure that isn't a straight count is marked `_estimated: True` and
    documents the real signal it's derived from, never presented as measured.

    `show_rate` IS real, but only for PRIVATE events: `EventRegistration.claimed_at` is
    only ever set when an invited viewer's browser first opens a private event's watch
    page (routers/events.py::watch_event) — a public or registration-required-public
    viewer never claims anything, so there is no "registered but didn't show" signal for
    them at all. `show_rate_basis` says so explicitly rather than implying an org-wide rate.
    """
    since = _now() - ANALYTICS_RANGES.get(range_key, ANALYTICS_RANGES["30d"])
    event_ids = list(db.scalars(
        select(Event.id).where(Event.org_id == org.id, Event.deleted_at.is_(None),
                               Event.start_time.isnot(None), Event.start_time >= since)
    ).all())

    claimed_by_email: dict[str, int] = {}
    private_registered = private_claimed = 0
    if event_ids:
        rows = db.execute(
            select(EventRegistration.email, EventRegistration.claimed_at, Event.visibility)
            .join(Event, Event.id == EventRegistration.event_id)
            .where(EventRegistration.event_id.in_(event_ids))
        ).all()
        for email, claimed_at, visibility in rows:
            if visibility == "private":
                private_registered += 1
                if claimed_at is not None:
                    private_claimed += 1
            if claimed_at is not None:
                claimed_by_email[email] = claimed_by_email.get(email, 0) + 1

    # Reuse, don't re-derive: analytics()'s summary.viewers is the same real peak-
    # concurrency figure /organization/analytics already shows. It's the only audience
    # signal available at all for an open public event (no EventRegistration row exists),
    # so it's added on top of the real claimed-registration count rather than blended
    # into it — a private event's real count is never diluted by a rough one.
    base = analytics(db, org, range_key)
    peak_viewers_sum = base["summary"]["viewers"]
    watch_hours = base["summary"]["watch_hours"]

    unique_attendees = len(claimed_by_email) + peak_viewers_sum
    returning = sum(1 for n in claimed_by_email.values() if n > 1)
    avg_watch_minutes = round((watch_hours * 60) / unique_attendees, 1) if unique_attendees else None
    show_rate = round(100 * private_claimed / private_registered) if private_registered else None

    return {
        "range": range_key, "since": since,
        "unique_attendees": unique_attendees, "unique_attendees_estimated": True,
        "returning": returning,
        "avg_watch_minutes": avg_watch_minutes, "avg_watch_minutes_estimated": True,
        "show_rate": show_rate, "show_rate_basis": "private_invited_events_only",
    }


# ── sessions and media ────────────────────────────────────────────────────────

def sessions(db: Session, org_id, since: datetime, limit: int = 6) -> dict:
    """Active counts plus the live/recent session list."""
    rows = db.scalars(
        select(BroadcastSession)
        .where(BroadcastSession.org_id == org_id)
        .order_by(BroadcastSession.created_at.desc())
        .limit(50)
    ).all()

    live = [s for s in rows if s.status == "live"]
    paused = [s for s in rows if s.status == "paused"]
    event_ids = [s.event_id for s in rows]
    events = {}
    if event_ids:
        events = {e.id: e for e in db.scalars(select(Event).where(Event.id.in_(event_ids))).all()}

    org = db.get(Organization, org_id)
    is_test_org = bool(org and org.is_test)

    def mode(s):
        # The Figma's MODE column is the session's operating mode, which we do have.
        # (Ingest PROTOCOL and region are not stored — see ORG_GAPS.)
        if s.status == "live":
            return "test" if is_test_org else "live"
        if s.status == "paused":
            return "paused"
        return "ended"

    def state(s):
        if s.ended_at:
            return "Archived" if s.status == "ended" else "Ended"
        if s.status == "paused":
            return "Paused"
        return "Healthy"

    listed = []
    for s in rows[:limit]:
        ev = events.get(s.event_id)
        listed.append({
            "id": str(s.id),
            "event_id": str(s.event_id),
            "title": (ev.title if ev else None) or "Untitled session",
            "room": None,  # ingest endpoint/region is not recorded on the session
            "mode": mode(s),
            "state": state(s),
            "started_at": _aware(s.started_at),
            "ended_at": _aware(s.ended_at),
            "peak_viewers": s.peak_viewers or 0,
        })

    # Current audience across this org's live sessions, from the sampler's real snapshots.
    fresh = _now() - timedelta(seconds=60)
    latest = ops_svc._latest_snapshot_per_event(db, [s.event_id for s in live + paused], fresh)
    current_audience = sum(sn.viewers for sn in latest.values())

    starting = db.scalar(
        select(func.count(Event.id)).where(
            Event.org_id == org_id, Event.deleted_at.is_(None),
            Event.status.in_(("scheduled", "published")),
            Event.start_time.isnot(None),
            Event.start_time > _now(),
            Event.start_time <= _now() + timedelta(minutes=30),
        )
    ) or 0

    return {
        "active": len(live) + len(paused),
        "live": len(live),
        "paused": len(paused),
        "starting_soon": starting,
        "current_audience": current_audience or None,
        "peak_audience": db.scalar(
            select(func.coalesce(func.max(BroadcastSession.peak_viewers), 0))
            .where(BroadcastSession.org_id == org_id)
        ) or None,
        "items": listed,
        # There is no self-service vs managed-event distinction in the schema.
        "breakdown_note": "Self-service vs managed classification is not modelled.",
    }


def media_assets(db: Session, org_id) -> dict:
    """Recording rows by state. `ready` means the capture finished; `processing` means it is
    still rolling. `not_captured` counts recordings LiveKit never actually enforced —
    honest, because those rows exist but no file does."""
    rows = db.execute(
        select(LiveRecording.status, LiveRecording.enforced, func.count(LiveRecording.id))
        .where(LiveRecording.org_id == org_id)
        .group_by(LiveRecording.status, LiveRecording.enforced)
    ).all()
    ready = processing = not_captured = 0
    for status, enforced, n in rows:
        if status in ("recording", "paused"):
            processing += n
        elif enforced:
            ready += n
        else:
            not_captured += n
    return {"ready": ready, "processing": processing, "not_captured": not_captured,
            "total": ready + processing + not_captured}


# ── attention required ────────────────────────────────────────────────────────

def attention(db: Session, org: Organization, ent: dict, readiness: list[dict]) -> list[dict]:
    """Things an org admin has to act on, each derived from a real row. Ordered by
    severity so the top of the list is the next thing to do."""
    now = _now()
    items: list[dict] = []

    # Credentials nearing expiry. api_keys is a JSON list; entries created without an
    # expiry simply have none and are not surfaced.
    for key in org.api_keys or []:
        expires = key.get("expires_at")
        if not expires:
            continue
        try:
            when = _aware(datetime.fromisoformat(str(expires).replace("Z", "+00:00")))
        except ValueError:
            continue
        days = _days_until(when, now)
        if days <= CREDENTIAL_WARN_DAYS:
            items.append({
                "id": f"cred:{key.get('id') or key.get('prefix')}",
                "severity": "critical" if days <= 3 else "warning",
                "title": f"Production credential expires in {max(days, 0)} days"
                         if days >= 0 else "Production credential has expired",
                "detail": f"Credentials · {key.get('label') or key.get('prefix') or 'API key'}",
                "action": "Rotate",
                "to": "/organization/settings",
            })

    # Entitlement thresholds.
    for item in ent["items"]:
        pct = item["percent"]
        if pct is not None and pct >= USAGE_WARN_RATIO * 100:
            items.append({
                "id": f"usage:{item['label']}",
                "severity": "critical" if pct >= 100 else "warning",
                "title": f"{item['label']} usage approaching limit"
                         if pct < 100 else f"{item['label']} limit reached",
                "detail": f"Usage & Entitlements · {pct}% of allotment",
                "action": "Review",
                "to": "/organization/billing",
            })

    # Trial ending.
    if ent.get("status") == "trial" and ent.get("trial_ends_at"):
        days = _days_until(ent["trial_ends_at"], now)
        if days <= 14:
            items.append({
                "id": "trial",
                "severity": "warning" if days > 3 else "critical",
                "title": f"Trial ends in {max(days, 0)} days",
                "detail": f"Usage & Entitlements · {ent.get('plan') or 'current plan'}",
                "action": "Review",
                "to": "/organization/billing",
            })

    # Upcoming events that cannot go ahead as configured.
    for ev in readiness:
        if ev["verdict"] != "blocked":
            continue
        items.append({
            "id": f"event:{ev['id']}",
            "severity": "critical",
            "title": f"{ev['title']} is blocked",
            "detail": "Live Events · " + ", ".join(ev["failing"][:2]),
            "action": "Resolve",
            "to": f"/organization/events/{ev['id']}",
        })

    # Governance obligations against this org.
    for g in db.scalars(
        select(GovernanceRecord).where(
            GovernanceRecord.org_id == org.id, GovernanceRecord.resolved_at.is_(None))
    ).all():
        items.append({
            "id": f"gov:{g.id}",
            "severity": "warning",
            "title": g.detail or g.kind.replace("_", " ").capitalize(),
            "detail": f"Security & Governance · {g.kind.replace('_', ' ')}",
            "action": "Review",
            "to": "/organization/settings",
        })

    # Members waiting on an invitation decision.
    pending = db.scalar(
        select(func.count(Invitation.id)).where(
            Invitation.org_id == org.id, Invitation.status == "pending")
    ) or 0
    if pending:
        items.append({
            "id": "invites",
            "severity": "info",
            "title": f"{pending} invitation{'s' if pending != 1 else ''} awaiting acceptance",
            "detail": "Members & Access · pending invitations",
            "action": "Manage",
            "to": "/organization/users",
        })

    order = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda i: order.get(i["severity"], 3))
    return items


# ── security, support, developer posture ──────────────────────────────────────

def security_support(db: Session, org: Organization) -> dict:
    """Posture from what the org row and its tickets/invitations actually record."""
    security = org.security or {}
    open_cases = db.scalar(
        select(func.count(SupportTicket.id)).where(
            SupportTicket.org_id == org.id,
            SupportTicket.status.in_(("open", "in_progress")))
    ) or 0
    urgent = db.scalar(
        select(func.count(SupportTicket.id)).where(
            SupportTicket.org_id == org.id,
            SupportTicket.status.in_(("open", "in_progress")),
            SupportTicket.priority == "urgent")
    ) or 0
    pending_members = db.scalar(
        select(func.count(Invitation.id)).where(
            Invitation.org_id == org.id, Invitation.status == "pending")
    ) or 0

    return {
        "sso_enforced": bool(security.get("enforce_sso")),
        "two_factor_required": bool(security.get("require_2fa")),
        "allowed_domains": (security.get("allowed_domains") or "").strip() or None,
        "domain_verified": bool(org.domain_verified),
        "pending_members": pending_members,
        "open_cases": open_cases,
        "urgent_cases": urgent,
        # No findings scanner, review scheduler or maintenance calendar in this stack.
        "open_findings": None,
        "next_review_days": None,
        "active_support_session": None,
        "maintenance_window": None,
    }


def _webhook_failure_streak(deliveries: list[WebhookDelivery]) -> int:
    """Consecutive failures at the head of `deliveries` (must be newest-first). Stops at
    the first delivered attempt, so a since-recovered endpoint reads 0."""
    streak = 0
    for d in deliveries:
        if d.status != "delivered":
            streak += 1
        else:
            break
    return streak


def developer_ops(db: Session, org: Organization) -> dict:
    """Developer-platform posture. Credentials and webhook delivery health are both real
    now (services/webhooks.py logs every attempt); app-level error/SDK/rate-limit
    telemetry still has no producer in this stack — see ORG_GAPS."""
    keys = org.api_keys or []
    active = [k for k in keys if not k.get("revoked")]

    endpoints = db.scalars(
        select(WebhookEndpoint).where(WebhookEndpoint.org_id == org.id)
    ).all()
    worst_streak = 0
    for ep in endpoints:
        recent = db.scalars(
            select(WebhookDelivery).where(WebhookDelivery.endpoint_id == ep.id)
            .order_by(WebhookDelivery.created_at.desc()).limit(10)
        ).all()
        worst_streak = max(worst_streak, _webhook_failure_streak(recent))

    return {
        "credentials_total": len(keys),
        "credentials_active": len(active),
        "webhooks_configured": len(endpoints),
        "webhook_failure_streaks": worst_streak,
        # Each still needs a producer that does not exist yet — see ORG_GAPS.
        "apps_elevated_error_rate": None,
        "deprecated_sdk_exposure": None,
        "rate_limit_events_24h": None,
        "note": "Application error rates, SDK exposure and rate-limit history need "
                "request telemetry (not integrated).",
    }


def api_posture() -> dict:
    """API success/latency is measured per PROCESS, not per organization: requests are not
    attributed to a tenant. Reporting the platform figure here would tell an org admin
    something about other tenants' traffic, so it is withheld."""
    return {
        "success_rate": None,
        "p95_ms": None,
        "note": "Per-organization API success and latency need request attribution by "
                "credential (not integrated).",
    }


# ── event readiness, org-scoped ───────────────────────────────────────────────

def upcoming_events(db: Session, org_id, include_test: bool = True, limit: int = 6) -> list[dict]:
    """This org's upcoming events with a readiness verdict. Reuses the same gates the
    Super Admin console evaluates, so the two views can never disagree about whether an
    event is ready — then adds `readiness_state`, which distinguishes "nothing has been
    set up yet" from "set up and failing a gate"."""
    all_upcoming = ops_svc.event_readiness(db, include_test=include_test, limit=200,
                                          high_impact_only=False)
    ids = {str(e_id) for e_id in db.scalars(
        select(Event.id).where(Event.org_id == org_id, Event.deleted_at.is_(None))
    ).all()}
    mine = [e for e in all_upcoming if e["id"] in ids][:limit]

    for ev in mine:
        ev["readiness_state"] = _readiness_state(ev)
    return mine


# The gates that represent readiness WORK — somebody has to roster a host, roster a
# moderator, turn recording on. The others (title, schedule, account standing, redundancy)
# pass on their own, so they say nothing about whether readiness has been started.
_WORK_GATES = ("host", "moderator", "recording")


def _readiness_state(ev: dict) -> str:
    """Readiness as an operator reads it, derived from which work gates are done.

    Keyed on the work gates rather than a count of all passed gates: four gates pass
    automatically, so any threshold on the total is a magic number that breaks the moment a
    gate is added.
    """
    work = [g for g in ev["gates"] if g["key"] in _WORK_GATES]
    done = [g for g in work if g["passed"]]
    if not done:
        return "not_started"          # nobody has begun, whatever the verdict would be
    if ev["verdict"] == "passed":
        return "passed"
    if len(done) < len(work):
        return "in_progress"          # actively being set up
    return ev["verdict"]              # all work done; conditional/blocked comes from elsewhere


# ── payloads ──────────────────────────────────────────────────────────────────

def console_state(db: Session, org: Organization, user: User) -> dict:
    """Small payload the org shell needs on every page: identity, workspace, nav badges."""
    active_sessions = db.scalar(
        select(func.count(BroadcastSession.id)).where(
            BroadcastSession.org_id == org.id,
            BroadcastSession.status.in_(("live", "paused")))
    ) or 0
    stages = stage_health(db, org.id, _now() - RANGES["24h"])
    health = service_health(stages)

    return {
        "organization": {"id": str(org.id), "name": org.name, "status": org.status,
                         "plan": (_plan(db, org.id)[1].name if _plan(db, org.id)[1] else None)},
        "workspace": workspace(org),
        "workspaces": workspaces(org),
        "health": {"status": health["status"], "label": health["label"]},
        "badges": {
            "streaming_sessions": active_sessions,
            "webhooks": len(org.webhook_urls or []),
            "live_events": db.scalar(
                select(func.count(Event.id)).where(
                    Event.org_id == org.id, Event.deleted_at.is_(None),
                    Event.status == "live")
            ) or 0,
        },
        "user": {"name": user.full_name, "email": user.email,
                 "role_label": _role_label(user.role)},
    }


def _role_label(role: str | None) -> str:
    return {"org_admin": "Organization Owner", "host": "Host", "moderator": "Moderator",
            "speaker": "Speaker", "viewer": "Viewer",
            "super_admin": "Super Admin"}.get(role or "", "Member")


def overview(db: Session, org: Organization, user: User, range_: str = "24h",
             include_test: bool = True) -> dict:
    """One call for the whole Organization Overview page."""
    generated_at = _now()
    since = generated_at - RANGES.get(range_, RANGES["24h"])

    stages = stage_health(db, org.id, since)
    ent = entitlements(db, org)
    sess = sessions(db, org.id, since)
    media = media_assets(db, org.id)
    readiness = upcoming_events(db, org.id, include_test=include_test)

    return {
        "generated_at": generated_at,
        "window": {"range": range_, "since": since, "until": generated_at},
        "organization": {
            "id": str(org.id), "name": org.name, "status": org.status,
            "domain": org.domain, "region": org.region,
        },
        "workspace": workspace(org),
        "workspaces": workspaces(org),
        "lifecycle": stages,
        "service_health": service_health(stages),
        "sessions": sess,
        "media_assets": media,
        "entitlements": ent,
        "api": api_posture(),
        "attention": attention(db, org, ent, readiness),
        "upcoming_events": readiness,
        "developer_ops": developer_ops(db, org),
        "security_support": security_support(db, org),
        "gaps": ORG_GAPS,
    }


# Documented missing producers. Returned in the payload so the UI can label an empty
# region with the reason rather than looking broken, and so the list can't rot silently.
ORG_GAPS = [
    {"field": "api.success_rate / api.p95_ms",
     "needs": "per-credential request attribution"},
    {"field": "entitlements.delivery_windowed",
     "needs": "bandwidth metering pipeline"},
    {"field": "developer_ops.apps_elevated_error_rate",
     "needs": "per-application request telemetry"},
    {"field": "developer_ops.deprecated_sdk_exposure",
     "needs": "SDK version reporting from clients"},
    {"field": "developer_ops.rate_limit_events_24h",
     "needs": "persisted rate-limit rejections (ratelimit.py is in-process, unlogged)"},
    {"field": "sessions.items[].room",
     "needs": "ingest protocol + region recorded on the session"},
    {"field": "sessions.breakdown_note",
     "needs": "self-service vs managed event classification"},
    {"field": "security_support.open_findings",
     "needs": "a security findings scanner"},
    {"field": "security_support.next_review_days",
     "needs": "an access-review schedule"},
    {"field": "security_support.maintenance_window",
     "needs": "a maintenance calendar"},
]
