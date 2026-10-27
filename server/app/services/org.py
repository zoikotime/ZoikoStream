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

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    SUBSCRIPTION_ENTITLED_STATES,
    has_live_provider_subscription,
    normalize_subscription_state,
    AnalyticsSnapshot,
    BroadcastSession,
    Event,
    EventRegistration,
    GovernanceRecord,
    Incident,
    Invitation,
    LiveMessage,
    LivePoll,
    LiveQuestion,
    LiveRecording,
    Organization,
    Plan,
    Subscription,
    SupportTicket,
    User,
    WebhookDelivery,
    WebhookEndpoint,
)
from ..models.event import ON_AIR_STATUSES
from . import admin as admin_svc
from . import ops as ops_svc
from .quota import resolve_effective_limit
# `engagement_score` is imported lazily inside analytics() rather than here. There is a real
# module cycle - org -> broadcast -> webhooks -> webhook_lifecycle -> org_comms -> org - and
# a module-level `from .broadcast import <name>` is the one form of it that cannot survive:
# every other edge imports a module OBJECT, which tolerates partial initialization, whereas
# binding a NAME requires broadcast to be fully executed. Importing app.main happened to
# resolve the cycle in a working order, so this only ever surfaced when a caller imported
# app.routers.events (or app.services.org) first - which is exactly what test_broadcast.py
# and test_commercial_lifecycle.py do.

# Ranges the overview toolbar offers. Mirrors the console's vocabulary.
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24),
          "7d": timedelta(days=7), "30d": timedelta(days=30)}

# Ranges the Analytics page offers. A separate vocabulary from RANGES above (that one
# drives the Overview toolbar's operational window; this one drives historical reporting).
ANALYTICS_RANGES = {"7d": timedelta(days=7), "30d": timedelta(days=30),
                     "90d": timedelta(days=90), "12m": timedelta(days=365)}

log = logging.getLogger(__name__)

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


def stage_health(db: Session, org_id, since: datetime, health: dict | None = None) -> list[dict]:
    """Per-stage status for this org: the platform probe result, escalated by any Incident
    scoped to this organization (or global), and flagged with whether the org uses it.

    Only this organization's own and platform-wide incidents count — in the escalation and
    in the availability figure alike (ops.stage_health's org_id). Another tenant's incident
    used to degrade this one's stages."""
    health = health or admin_svc.platform_health(db)
    usage = _stage_usage(db, org_id)
    stages = ops_svc.stage_health(db, health, since, tuple(s[0] for s in ops_svc.STAGES),
                                  org_id=org_id)

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


# ── customer-facing service status (/organization/support) ────────────────────

# What the Support & Status page may tell a customer, and from what evidence. Each row is a
# product surface a customer recognises, carried by REAL checks (services/admin.platform_health):
#
#   Live streaming      <- "streaming": the background LiveKit probe, an authenticated API round
#                          trip every 5 minutes (services/provider_health). Going live and
#                          watching live both run through that one media server, so it is one
#                          row; separate "contribution", "ingest" and "viewer delivery" rows would
#                          have repeated the same signal under three names.
#   Recording & replay  <- "storage": the background bucket probe, PLUS this organization's own
#                          recent recording starts (_recording_start_evidence). LiveKit Egress
#                          cannot be probed without starting a job, so a start that Egress
#                          refused is the only evidence of its health there is.
#   Management API      <- "api" (this response was served) and "database" (SELECT 1).
#
# `stages` are the lifecycle stages whose recorded incidents a row answers for; every stage
# belongs to exactly one row, so no incident is lost.
#
# Deliberately absent: "Transcoding & packaging" (the `produce` stage). Its only member is
# "workers", which services/admin.platform_health hard-codes as "Not yet integrated" — there is
# no transcoding or packaging service in this platform to be healthy or not. Its incidents, if an
# operator ever records one, read against Live streaming. Likewise the CDN, which does not exist.
#
# A row whose checks have no current verdict (not configured, or a probe result that is absent or
# stale) is LEFT OUT unless an open incident speaks for it — never shown as "Not configured" or
# "Not measured". Those states stay on the Super Admin console, which is where they help.
CUSTOMER_SERVICES = (
    {"id": "live_streaming", "name": "Live streaming", "summary": "Going live and watching live",
     "checks": ("streaming",), "stages": ("contribute", "ingest", "produce", "deliver")},
    {"id": "recording", "name": "Recording & replay", "summary": "Recording broadcasts and playing replays",
     "checks": ("storage",), "stages": ("preserve",)},
    {"id": "platform_api", "name": "Management API", "summary": "This console and the ZoikoStream API",
     "checks": ("api", "database"), "stages": ("platform", "secure", "understand")},
)

# How far back a recording start still counts as evidence about the recording service.
RECORDING_EVIDENCE_WINDOW = timedelta(hours=24)

_STATUS_RANK = {"ok": 0, "warn": 1, "down": 2}


def _worse(a: str, b: str) -> str:
    return a if _STATUS_RANK[a] >= _STATUS_RANK[b] else b


def _incident_floor(inc: Incident) -> str:
    return "down" if inc.severity in ("sev1", "sev2") else "warn"


def _recording_start_evidence(db: Session, org_id, now: datetime) -> str | None:
    """What this organization's own recent recording STARTS say about the recording service.

    A start that LiveKit Egress refused is written as status "failed" with no egress_id
    (routers/events._recording_start — the production `503 no response from servers`). Reading
    the newest attempts in the window:

      the newest attempt failed to start, and so did the one before it   -> "down"
      the newest attempt failed to start                                  -> "warn"
      the newest attempt started (the recorder accepted it)               -> "ok"
      no attempt in the window                                            -> None (no evidence)

    An ATTEMPT is one start: a dual recording (R2/R3) writes both of its rows in one
    transaction, so they share created_at and are read as one attempt — one refused click used
    to read as two consecutive failures, i.e. a "Major outage". An attempt started if any of its
    rows got an egress id.

    Only this organization's attempts: they are its experience, and another tenant's cannot be
    used to tell it anything."""
    rows = db.execute(
        select(LiveRecording.status, LiveRecording.egress_id, LiveRecording.created_at)
        .where(LiveRecording.org_id == org_id,
               LiveRecording.created_at >= now - RECORDING_EVIDENCE_WINDOW)
        .order_by(LiveRecording.created_at.desc())
        .limit(10)
    ).all()
    attempts: list[list] = []
    for r in rows:
        if attempts and attempts[-1][0].created_at == r.created_at:
            attempts[-1].append(r)
        else:
            attempts.append([r])
    if not attempts:
        return None

    def refused(attempt) -> bool:
        return all(r.status == "failed" and not r.egress_id for r in attempt)

    if refused(attempts[0]):
        return "down" if len(attempts) > 1 and refused(attempts[1]) else "warn"
    return "ok"


# Published public-status components (models/status_page.STATUS_COMPONENTS) -> the customer row
# that describes the same capability. A fixed definition of what each row covers, not a guess
# made per incident: a published incident names its components explicitly. Billing and
# notifications have no row; a published incident on them reaches the page as a platform notice.
PUBLIC_COMPONENT_ROWS = {
    "live_streaming": "live_streaming",
    "recording": "recording",
    "replay": "recording",
    "authentication": "platform_api",
    "event_management": "platform_api",
    "developer_api": "platform_api",
    "webhooks": "platform_api",
}
_PUBLIC_IMPACT_STATUS = {"degraded": "warn", "partial_outage": "warn", "major_outage": "down"}

# What the page says about an INTERNAL incident. Operator incident titles are internal notes
# ("INC-… egress pool 3 OOM", a security investigation) and never reach customers; only the
# public status page's curated titles do.
_NEUTRAL_INCIDENT_TITLE = {
    "down": "Service disruption under investigation",
    "warn": "Degraded performance under investigation",
}


def _notice(status: str, title: str, scope: str) -> dict:
    return {"title": title, "severity": "major" if status == "down" else "minor", "scope": scope,
            "_rank": _STATUS_RANK[status]}


def _published_incidents(db: Session, since: datetime, now: datetime) -> list:
    """Public status incidents customers can already read on /status: published, and active at
    some point in the window. Platform-wide by nature."""
    from ..models import PublicStatusIncident

    return db.scalars(
        select(PublicStatusIncident).where(
            PublicStatusIncident.published_at.isnot(None),
            or_(PublicStatusIncident.started_at.is_(None), PublicStatusIncident.started_at <= now),
            or_(PublicStatusIncident.status != "resolved", PublicStatusIncident.resolved_at.is_(None),
                PublicStatusIncident.resolved_at >= since),
        )
    ).all()


def customer_status(db: Session, org_id, health: dict, since: datetime, now: datetime) -> dict:
    """The rows /organization/support shows, each with a status only real evidence supports.

    Per row: the worst of its checks (ok / warn / down), escalated by
      * open OPERATIONAL incidents recorded against its stages, for this organization or
        platform-wide (sev1-2 -> down, else warn);
      * open PUBLISHED public-status incidents on a component the row covers
        (PUBLIC_COMPONENT_ROWS; degraded / partial outage -> warn, major outage -> down);
      * for recordings, _recording_start_evidence.
    `incidents_24h` is a COUNT of those incidents active at any point in the window — not an
    uptime percentage, which nothing here measures.

    Recording & replay is "ok" only with POSITIVE evidence: storage healthy AND a recording this
    organization started in the window was accepted by the recorder. Storage alone proves replays
    can be served, not that recording works (the recorder, LiveKit Egress, has no probe; its
    production failure mode is refusing starts). With storage healthy and no recording attempt in
    the window, the row is "idle": shown, faulting nothing, claiming nothing about recording.

    An open operational incident that names NO stage, and a published incident on a component no
    row covers, cannot be attributed to a row without guessing, so neither is: each becomes the
    `platform_notice`, which still sets the verdict. Only OPERATIONAL incidents count: security
    and governance incidents are not availability statements and never change this page. Internal incident titles are never sent; a row or
    notice shows the published title when there is one, else neutral wording.

    `verdict`: "down" / "warn" from the worst row or notice; "ok" when every row was measured
    healthy (an idle row faults nothing); "partial" when the shown rows are healthy but some row
    could not be measured; "unavailable" when no row could be and nothing is known to be wrong."""
    services = {s["id"]: s for s in health.get("services", [])}
    incidents = db.scalars(
        select(Incident).where(
            Incident.kind == "operational",
            or_(Incident.org_id == org_id, Incident.org_id.is_(None)),
            Incident.started_at <= now,
            or_(Incident.status != "resolved", Incident.resolved_at.is_(None),
                Incident.resolved_at >= since),
        )
    ).all()
    published = _published_incidents(db, since, now)

    def public_rows(pub) -> set[str]:
        return {PUBLIC_COMPONENT_ROWS[c] for c in (pub.affected_components or []) if c in PUBLIC_COMPONENT_ROWS}

    rows, unmeasured = [], 0
    for svc in CUSTOMER_SERVICES:
        checks = [services.get(c) for c in svc["checks"]]
        measured = all(c is not None and c["status"] in admin_svc.PROBED_STATUSES for c in checks)
        mine = [i for i in incidents if i.stage in svc["stages"]]
        open_now = [i for i in mine if i.status != "resolved"]
        pub_mine = [p for p in published if svc["id"] in public_rows(p)]
        pub_open = [p for p in pub_mine if p.status != "resolved" and p.impact in _PUBLIC_IMPACT_STATUS]
        status = None
        if measured:
            status = "ok"
            for c in checks:
                status = _worse(status, c["status"])
        candidates = []
        for inc in open_now:
            floor = _incident_floor(inc)
            status = _worse(status or "ok", floor)
            candidates.append(_notice(floor, _NEUTRAL_INCIDENT_TITLE[floor], "platform"))
        for pub in pub_open:
            floor = _PUBLIC_IMPACT_STATUS[pub.impact]
            status = _worse(status or "ok", floor)
            # +0.5: on a tie the published, customer-written title wins over neutral wording.
            candidates.append({**_notice(floor, pub.title, "platform"), "_rank": _STATUS_RANK[floor] + 0.5})
        if svc["id"] == "recording":
            evidence = _recording_start_evidence(db, org_id, now)
            if evidence in ("warn", "down"):
                status = _worse(status or "ok", evidence)
            elif evidence is None and status == "ok":
                status = "idle"
        if status is None:
            unmeasured += 1
            continue
        worst = max(candidates, key=lambda n: n["_rank"], default=None)
        rows.append({
            "id": svc["id"], "name": svc["name"], "summary": svc["summary"],
            "status": status,
            "incidents_24h": len(mine) + len(pub_mine),
            "open_incidents": len(open_now) + len(pub_open),
            "incident": None if worst is None else {"title": worst["title"], "severity": worst["severity"]},
        })

    # What cannot be attributed to a row: an open operational incident with no stage (the only
    # kind the admin console creates), and a published incident on no row's component.
    notices = []
    for inc in incidents:
        if inc.status != "resolved" and inc.stage is None:
            floor = _incident_floor(inc)
            scope = "organization" if inc.org_id is not None else "platform"
            notices.append(_notice(floor, _NEUTRAL_INCIDENT_TITLE[floor], scope))
    for pub in published:
        if pub.status != "resolved" and pub.impact in _PUBLIC_IMPACT_STATUS and not public_rows(pub):
            floor = _PUBLIC_IMPACT_STATUS[pub.impact]
            notices.append({**_notice(floor, pub.title, "platform"), "_rank": _STATUS_RANK[floor] + 0.5})
    top = max(notices, key=lambda n: n["_rank"], default=None)
    platform_notice = None if top is None else {
        "title": top["title"], "severity": top["severity"], "scope": top["scope"], "count": len(notices)}

    verdict = None
    for r in rows:
        verdict = _worse(verdict or "ok", "ok" if r["status"] == "idle" else r["status"])
    if platform_notice:
        verdict = _worse(verdict or "ok", "down" if platform_notice["severity"] == "major" else "warn")
    if verdict is None:
        verdict = "unavailable"
    elif verdict == "ok" and unmeasured:
        verdict = "partial"
    return {"verdict": verdict, "services": rows, "platform_notice": platform_notice}


# ── scheduled maintenance (/organization/support) ─────────────────────────────

# Whether an EMPTY published schedule may be read as "nothing is planned".
#
# Maintenance windows are real, versioned records (scheduled_maintenances, written by
# status_publication.schedule_maintenance): the public status page announces them and status
# subscribers are emailed about them. A published window is therefore authoritative and is
# always shown. But nothing an operator can reach publishes one yet — no console screen and no
# API route calls schedule_maintenance — so an empty table proves nothing, and the Support page
# must not say "No scheduled maintenance". Set this to True when operators can publish windows,
# and the page's empty state becomes exactly that sentence.
MAINTENANCE_SCHEDULE_AUTHORITATIVE = False


def maintenance_schedule(db: Session) -> dict:
    """Published maintenance windows still ahead or under way (the same set the public status
    page lists), in customer terms: no ids, components and regions by their public labels,
    times as UTC instants for the browser to show in the viewer's own zone."""
    from ..models import COMPONENT_LABELS, REGION_LABELS
    from . import status_publication as status_pub

    return {
        "authoritative": MAINTENANCE_SCHEDULE_AUTHORITATIVE,
        "upcoming": [{
            "reference": row.public_reference,
            "title": row.title,
            "kind": row.kind,
            "status": row.status,
            "starts_at": status_pub.as_utc(row.starts_at_utc),
            "ends_at": status_pub.as_utc(row.ends_at_utc),
            "impact": row.impact_summary,
            "components": [COMPONENT_LABELS.get(c, c) for c in (row.affected_components or [])],
            "regions": [REGION_LABELS.get(r, r) for r in (row.affected_regions or [])],
        } for row in status_pub.upcoming_maintenance(db)],
    }


# ── entitlements ──────────────────────────────────────────────────────────────

def _plan(db: Session, org_id) -> tuple[Subscription | None, Plan | None]:
    """The org's ENTITLED subscription and plan, for DISPLAY. Unchanged, deliberately.

    This is the REPORTING resolver. Returning (None, None) for a non-entitled organization is
    intentional and is pinned by `test_no_plan_means_no_invented_ceiling`: the overview must
    report a null limit rather than a made-up denominator, which would render a meaningless
    progress bar.

    Do NOT use this for enforcement — see `enforcement_plan` below. The two questions ("what
    should I show?" and "what ceiling applies?") have different answers for a subscription whose
    entitlement has ended, and conflating them is what let a lapsed tenant become unmetered.
    """
    sub = db.scalar(
        select(Subscription).where(
            Subscription.org_id == org_id,
            Subscription.status.in_(SUBSCRIPTION_ENTITLED_STATES),
        ).order_by(Subscription.started_at.desc())
    )
    return sub, (sub.plan if sub else None)


def enforcement_plan(db: Session, org_id) -> Plan | None:
    """The plan whose QUANTITATIVE CEILING governs this organization, whatever its state.

    THE ENFORCEMENT resolver, and the counterpart to `_plan` above. Plan resolution and
    entitlement are separate concepts here:

        plan        = the subscription's stored plan            <- this function
        entitlement = status in SUBSCRIPTION_ENTITLED_STATES    <- unchanged, elsewhere

    A stored plan can remain relevant to other enforcement (such as seats) after lapse, but it
    does not grant storage quota. `_effective_quota` returns a zero storage limit unless the
    organization is entitled.

    Returns None ONLY when the organization has no subscription row at all; this does not
    fabricate a plan. Streaming fallback quotas continue to use explicitly configured platform
    defaults.

    Row selection prefers an ENTITLED subscription when one exists, so an old cancelled row can
    never override a current live one; otherwise it takes the most recent. That is the same
    intent as `crud.admin._current_subs`, and it decides only WHICH row to read.
    """
    entitled = db.scalar(
        select(Subscription).where(
            Subscription.org_id == org_id,
            Subscription.status.in_(SUBSCRIPTION_ENTITLED_STATES),
        ).order_by(Subscription.started_at.desc())
    )
    if entitled is not None:
        return entitled.plan
    lapsed = db.scalar(
        select(Subscription)
        .where(Subscription.org_id == org_id)
        .order_by(Subscription.started_at.desc())
    )
    return lapsed.plan if lapsed is not None else None


def _subscription_row(db: Session, org_id) -> Subscription | None:
    """The org's current subscription row WHATEVER its state, or None if it never had one.

    The REPORTING counterpart to the row the checkout guard locks. Selection is
    entitled-preferred, else most recent — the same intent as `enforcement_plan` and
    `crud.admin._current_subs`. Kept separate from `enforcement_plan` because that one answers
    a QUOTA question and is pinned by its own structural test.
    """
    entitled = db.scalar(
        select(Subscription).where(
            Subscription.org_id == org_id,
            Subscription.status.in_(SUBSCRIPTION_ENTITLED_STATES),
        ).order_by(Subscription.started_at.desc())
    )
    if entitled is not None:
        return entitled
    return db.scalar(
        select(Subscription)
        .where(Subscription.org_id == org_id)
        .order_by(Subscription.started_at.desc())
    )


def streaming_seconds(db: Session, org_id=None) -> float:
    """Exact streamed seconds, counting each concurrent session independently."""
    now = _now()
    stmt = select(BroadcastSession).where(BroadcastSession.started_at.isnot(None))
    if org_id is not None:
        stmt = stmt.where(BroadcastSession.org_id == org_id)
    rows = db.scalars(stmt).all()
    secs = 0.0
    for s in rows:
        start = _aware(s.started_at)
        end = _aware(s.ended_at) or now
        if start and end > start:
            paused_ms = s.paused_ms or 0
            if s.paused_at:
                paused_at = _aware(s.paused_at)
                if paused_at and paused_at < end:
                    paused_ms += int((end - paused_at).total_seconds() * 1000)
            secs += max(0.0, (end - start).total_seconds() - paused_ms / 1000)
    return max(secs, 0.0)


def _streaming_seconds(db: Session, org_id) -> float:
    return streaming_seconds(db, org_id)


def _streaming_hours(db: Session, org_id) -> float:
    return round(_streaming_seconds(db, org_id) / 3600, 1)


def active_stream_count(db: Session, org_id) -> int:
    """Number of open live or paused broadcasts owned by this organization."""
    return int(db.scalar(
        select(func.count(BroadcastSession.id)).where(
            BroadcastSession.org_id == org_id,
            BroadcastSession.ended_at.is_(None),
            BroadcastSession.started_at.isnot(None),
            BroadcastSession.status.in_(("live", "paused")),
        )
    ) or 0)


def _quota_plan(db: Session, org_id) -> tuple[Plan | None, bool, bool]:
    """Return (plan, entitled, has_subscription), keeping missing distinct from unlimited."""
    entitled = db.scalar(
        select(Subscription).where(
            Subscription.org_id == org_id,
            Subscription.status.in_(SUBSCRIPTION_ENTITLED_STATES),
        ).order_by(Subscription.started_at.desc())
    )
    if entitled is not None:
        return entitled.plan, True, True
    latest = db.scalar(
        select(Subscription).where(Subscription.org_id == org_id)
        .order_by(Subscription.started_at.desc())
    )
    return (latest.plan if latest else None), False, latest is not None


def _effective_quota(db: Session, org_id, dimension: str) -> dict:
    plan, entitled, has_subscription = _quota_plan(db, org_id)
    if dimension == "storage":
        plan_value = plan.max_storage_gb if plan else None
        from . import platform_settings as _ps
        default = None
        platform_ceiling = _ps.storage_ceiling_gb(db)
    elif dimension == "streaming_hours":
        plan_value = plan.max_streaming_hours if plan else None
        from . import platform_settings as _ps
        default = _ps.default_streaming_hours(db)
        platform_ceiling = None
    elif dimension == "concurrent_streams":
        plan_value = plan.max_concurrent_streams if plan else None
        from . import platform_settings as _ps
        default = _ps.default_concurrent_streams(db)
        platform_ceiling = None
    else:
        raise ValueError(f"Unknown quota dimension: {dimension}")

    if dimension == "storage" and not entitled:
        resolved = {
            "limit": 0,
            "configured": True,
            "source": "no_entitled_plan",
        }
    else:
        resolved = resolve_effective_limit(
            has_subscription=has_subscription,
            entitled=entitled,
            plan_limit=plan_value,
            fallback_limit=default,
            hard_ceiling=platform_ceiling,
        )
    return {
        **resolved,
        "plan_limit": plan_value,
        "entitled": entitled,
        "has_subscription": has_subscription,
    }


def effective_storage_quota(db: Session, org_id) -> dict:
    return _effective_quota(db, org_id, "storage")


def effective_streaming_hours_quota(db: Session, org_id) -> dict:
    return _effective_quota(db, org_id, "streaming_hours")


def effective_concurrent_streams_quota(db: Session, org_id) -> dict:
    return _effective_quota(db, org_id, "concurrent_streams")


def entitlements(db: Session, org: Organization) -> dict:
    """Usage and the authoritative server-enforced quota for this organization."""
    sub, plan = _plan(db, org.id)
    # The row the CHECKOUT GUARD will judge — not always the row above. A subscription that has
    # completed Stripe checkout but has not yet been activated sits in `conversion_pending`: it
    # has a live Stripe subscription (so a second checkout is refused) but is not entitled (so
    # `_plan` returns None). Reporting only the entitled view left the console believing the
    # tenant had no subscription at all.
    provider_sub = _subscription_row(db, org.id)
    members = db.scalar(
        select(func.count(User.id)).where(User.org_id == org.id, User.deleted_at.is_(None))
    ) or 0
    streaming_hours_exact = _streaming_seconds(db, org.id) / 3600
    hours = round(streaming_hours_exact, 1)
    active_streams = active_stream_count(db, org.id)

    effective = {
        "storage": effective_storage_quota(db, org.id),
        "streaming_hours": effective_streaming_hours_quota(db, org.id),
        "concurrent_streams": effective_concurrent_streams_quota(db, org.id),
    }

    def bar(key, label, used, plan_limit, unit, *, enforced: bool,
            evaluation_used=None):
        quota = effective.get(key)
        effective_limit = quota["limit"] if quota else plan_limit
        configured = quota["configured"] if quota else plan is not None
        evaluated_limit = effective_limit if enforced else plan_limit
        evaluated_used = used if evaluation_used is None else evaluation_used
        # Three different "no number" states, kept distinct because they mean different things:
        #   no applicable quota -> NOT APPLICABLE (new quota-consuming activity is blocked)
        #   configured null     -> UNLIMITED
        #   finite limit        -> WITHIN, AT_LIMIT, or EXCEEDED
        if not configured:
            quota_state, remaining, pct = "not_applicable", None, None
        elif evaluated_limit is None:
            quota_state, remaining, pct = "unlimited", None, None
        else:
            if enforced and evaluated_used == evaluated_limit:
                quota_state = "at_limit"
            elif evaluated_used > evaluated_limit:
                quota_state = "exceeded"
            else:
                quota_state = "within"
            remaining = (
                round(evaluated_limit - evaluated_used, 1)
                if isinstance(used, float) else evaluated_limit - evaluated_used
            )
            pct = round(100 * evaluated_used / evaluated_limit, 1) if evaluated_limit else None
        return {"key": key, "label": label, "used": used, "limit": plan_limit,
                "effective_limit": effective_limit if configured else None,
                "quota_source": quota["source"] if quota else ("plan" if plan else "unconfigured"),
                "unit": unit,
                "percent": pct, "remaining": remaining,
                "unlimited": configured and evaluated_limit is None,
                "quota_state": quota_state, "usage_state": "measured",
                "enforced": enforced,
                "enforced_limit": effective_limit if enforced and configured else None}

    items = [
        bar("storage", "Storage", round(float(org.storage_used_gb or 0), 1),
            plan.max_storage_gb if plan else None, "GB", enforced=True),
        bar("members", "Members", members, plan.max_users if plan else None, "seats",
            enforced=True),
        bar("streaming_hours", "Streaming hours", hours,
            plan.max_streaming_hours if plan else None, "hrs", enforced=True,
            evaluation_used=streaming_hours_exact),
        bar("concurrent_streams", "Concurrent streams", active_streams,
            plan.max_concurrent_streams if plan else None, "streams", enforced=True),
    ]
    highest = max((i["percent"] for i in items if i["percent"] is not None), default=None)
    return {
        "plan": plan.name if plan else None,
        "plan_slug": plan.slug if plan else None,
        "status": sub.status if sub else None,
        # The cadence this tenant is billed on, or None where it predates the column being
        # recorded. Section 16 requires the console to show the current interval; None renders
        # as "not recorded" rather than being guessed at as monthly.
        "billing_interval": sub.billing_interval if sub else None,
        # A scheduled plan change, so the console can show what is coming and when. Read from
        # OUR record — the effective date is the one the customer was promised, not a value the
        # page derives. All three are null together when nothing is pending.
        "pending_plan_slug": (
            sub.pending_plan.slug if sub and sub.pending_plan is not None else None),
        "pending_plan_name": (
            sub.pending_plan.name if sub and sub.pending_plan is not None else None),
        "pending_billing_interval": sub.pending_billing_interval if sub else None,
        "plan_change_effective_at": (
            _aware(sub.plan_change_effective_at) if sub else None),
        # What the checkout guard would say, so the console never offers a purchase the
        # backend will refuse. Asked through the SAME predicate the guard uses.
        "checkout_locked": has_live_provider_subscription(provider_sub),
        # That row's plan and state, for the case where it is NOT the entitled one. These
        # describe a subscription that exists; `plan_slug` above remains the only field that
        # means "this tenant is entitled to this plan".
        "subscription_plan_slug": (
            provider_sub.plan.slug if provider_sub is not None and provider_sub.plan else None),
        "subscription_status": provider_sub.status if provider_sub is not None else None,
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


def _bucket_label(dt: datetime, range_key: str, zone=timezone.utc) -> str:
    """Label the bucket in the ORGANIZATION's zone, not UTC.

    Timestamps are stored UTC, and this used to strftime them directly — so an event at
    02:00 in an IST organization (20:30 the previous day UTC) appeared on the chart a day
    before the Events list said it happened. Converting first is the whole fix.
    """
    local = dt.astimezone(zone)
    return local.strftime("%b %Y") if range_key == "12m" else local.strftime("%b %d")


def _bucket_timeline(since: datetime, until: datetime, range_key: str, zone) -> list[str]:
    """Every bucket label in the window, in order, whether or not anything happened in it.

    Only days/months that contained an event used to appear, so two events three days apart
    rendered as two isolated bars with nothing between them. A zero-filled bucket means "no
    measured activity on this date" — which is a fact, not missing telemetry.
    """
    labels: list[str] = []
    seen: set[str] = set()
    if range_key == "12m":
        cur = since.astimezone(zone).replace(day=1)
        end = until.astimezone(zone)
        while cur <= end:
            label = cur.strftime("%b %Y")
            if label not in seen:
                seen.add(label)
                labels.append(label)
            cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        return labels
    cur = since.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    end = until.astimezone(zone)
    while cur <= end:
        label = cur.strftime("%b %d")
        if label not in seen:
            seen.add(label)
            labels.append(label)
        cur += timedelta(days=1)
    return labels


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
        select(Event).where(
            Event.org_id == org.id,
            Event.deleted_at.is_(None),
            or_(
                Event.start_time >= since,
                Event.end_time >= since,
                Event.status.in_(("live", "degraded")),
                and_(Event.start_time.is_(None), Event.created_at >= since),
                Event.id.in_(select(BroadcastSession.event_id).where(BroadcastSession.started_at >= since)),
            ),
        )
        .order_by(Event.start_time.nulls_last(), Event.created_at.desc())
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

    # Real chat messages and reactions directly from LiveMessage
    messages_by_event: dict = {}
    reactions_by_event: dict = {}
    if event_ids:
        msg_rows = db.execute(
            select(LiveMessage.event_id, LiveMessage.reactions)
            .where(LiveMessage.event_id.in_(event_ids), LiveMessage.status != "deleted")
        ).all()
        for eid_val, reactions_json in msg_rows:
            messages_by_event[eid_val] = messages_by_event.get(eid_val, 0) + 1
            if isinstance(reactions_json, dict):
                reactions_by_event[eid_val] = reactions_by_event.get(eid_val, 0) + sum(
                    v if isinstance(v, (int, float)) else (v.get("count", 0) if isinstance(v, dict) else len(v) if isinstance(v, list) else 0)
                    for v in reactions_json.values()
                )

    # Real questions directly from LiveQuestion
    questions_by_event: dict = {}
    if event_ids:
        q_rows = db.execute(
            select(LiveQuestion.event_id, func.count())
            .where(LiveQuestion.event_id.in_(event_ids))
            .group_by(LiveQuestion.event_id)
        ).all()
        for eid_val, count_val in q_rows:
            questions_by_event[eid_val] = count_val

    # Poll votes, from the rows that actually hold them. LivePoll.options is
    # [{label, votes}] and is queryable per event, so the engagement formula can use a REAL
    # figure instead of the hardcoded 0 it used to pass — no snapshot column and no
    # migration needed.
    poll_votes_by_event: dict = {}
    if event_ids:
        for p in db.scalars(select(LivePoll).where(LivePoll.event_id.in_(event_ids))):
            poll_votes_by_event[p.event_id] = poll_votes_by_event.get(p.event_id, 0) + sum(
                (o or {}).get("votes", 0) for o in (p.options or []) if isinstance(o, dict))

    from . import bus
    from .viewing_sessions import combine as combine_sessions
    from .viewing_sessions import summarize_sessions
    from .watch_time import build_event_analytics_aggregate, pick_event_summary

    per_event: dict = {}
    session_metrics_by_event: dict = {}
    for e in events:
        b_sessions = sessions_by_event.get(e.id, [])
        snaps = snapshots_by_event.get(e.id, [])

        # The most complete finalized summary across this event's sessions — see
        # watch_time.pick_event_summary for why "the first row carrying one" was wrong.
        # The bus stays a FALLBACK rather than a competitor here, unlike the single-event
        # report: this runs once per event in the window, and consulting it unconditionally
        # would put a Redis round trip per event inside the loop. Nothing is lost by it —
        # `_end` writes the bus copy onto the session row, so an event with any persisted
        # summary has already had the bus's figure considered.
        stored_summary = pick_event_summary(b_sessions)
        if not stored_summary:
            stored_summary = bus.summary_get_sync(e.id)

        live_sessions = bus.session_get_all_sync(e.id)

        db_msgs = messages_by_event.get(e.id, 0)
        snap_msgs = max((s.messages for s in snaps), default=0)
        chat_count = max(db_msgs, snap_msgs)

        db_qs = questions_by_event.get(e.id, 0)
        snap_qs = max((s.questions for s in snaps), default=0)
        question_count = max(db_qs, snap_qs)

        db_rx = reactions_by_event.get(e.id, 0)
        snap_rx = max((s.reactions for s in snaps), default=0)
        reaction_count = max(db_rx, snap_rx)

        pv = poll_votes_by_event.get(e.id, 0)

        agg = build_event_analytics_aggregate(
            event_id=e.id,
            event_title=e.title,
            event_start=e.start_time,
            event_end=e.end_time,
            created_at=e.created_at,
            sessions=live_sessions,
            snaps=snaps,
            b_sessions=b_sessions,
            summary_override=stored_summary,
            chat_count=chat_count,
            question_count=question_count,
            reaction_count=reaction_count,
            poll_votes=pv,
        )
        per_event[e.id] = agg

        # Session-aware metrics, additional to everything above (services/viewing_sessions.py).
        # Same precedence as watch time: the finalized summary wins, else the live ledger.
        stored_sessions = stored_summary.get("sessions") if isinstance(stored_summary, dict) else None
        if isinstance(stored_sessions, dict) and stored_sessions.get("measured"):
            session_metrics_by_event[e.id] = stored_sessions
        else:
            starts = [bs.started_at for bs in b_sessions if bs.started_at]
            session_metrics_by_event[e.id] = summarize_sessions(
                live_sessions, broadcast_start_ts=min(starts) if starts else None,
                now_ts=_now().timestamp(), event_end_ts=e.end_time)

    # Bucket in the organization's own zone, and across the WHOLE window so the chart is a
    # continuous timeline rather than isolated points. event_zone is the existing resolver
    # (Event -> Organization -> UTC); passing None asks it for the organization's zone,
    # which is the right axis for a chart spanning many events.
    from .event_comms import event_zone
    zone, _zone_name = event_zone(None, org)

    bucket_order = _bucket_timeline(since, _now(), range_key, zone)
    buckets: dict[str, dict] = {l: {"viewers": 0, "watch_hours": 0.0} for l in bucket_order}
    for e in events:
        info = per_event[e.id]
        ev_start = info["occurrence_dt"]
        if ev_start is None:
            continue
        label = _bucket_label(ev_start, range_key, zone)
        if label not in buckets:
            # An event just outside the generated timeline (clock skew, or a start_time
            # exactly on the boundary) still gets a bucket rather than being dropped.
            buckets[label] = {"viewers": 0, "watch_hours": 0.0}
            bucket_order.append(label)
        buckets[label]["viewers"] += info["peak_viewers"]
        # `or 0.0`: watch_hours is None for an event with no snapshots, and a bucket total
        # is a sum of what WAS measured — `float += None` is a TypeError, not a metric.
        buckets[label]["watch_hours"] += info["watch_hours"] or 0.0

    engaged = [i["engagement_score"] for i in per_event.values() if i["engagement_score"] is not None]
    measured = [i for i in per_event.values() if i["measurement_state"] != "unmeasured"]
    ranked = sorted(per_event.items(), key=lambda kv: -kv[1]["peak_viewers"])

    return {
        "range": range_key,
        "since": since,
        "timezone": _zone_name,
        "summary": {
            # NOT unique viewers and not total joins: the sum of each event's PEAK
            # concurrency. Named to match, because the console used to label this
            # "Total Viewers" and the number cannot support that claim.
            "peak_viewers_summed": sum(i["peak_viewers"] for i in per_event.values()),
            # Unrounded hours so the client can render minutes for short sessions without
            # the backend inventing precision the 15s cadence does not support.
            "watch_hours": round(sum(i["watch_hours"] for i in measured if i["watch_hours"] is not None), 4) if any(i["watch_hours"] is not None for i in measured) else None,
            "total_watch_seconds": sum(i["total_watch_seconds"] for i in measured if i.get("total_watch_seconds") is not None) if any(i.get("total_watch_seconds") is not None for i in measured) else None,
            "peak": max((i["peak_viewers"] for i in per_event.values()), default=0),
            # None when no audience was present to measure; 0% when viewers were present but idle.
            "engagement": round(sum(engaged) / len(engaged)) if engaged else None,
        },
        "trends": {
            "viewership": [{"label": l, "value": buckets[l]["viewers"]} for l in bucket_order],
            "watch_time": [{"label": l, "value": round(buckets[l]["watch_hours"], 1)} for l in bucket_order],
        },
        "top_events": [{"label": i["event_name"], "value": i["peak_viewers"]} for _, i in ranked[:5] if i["peak_viewers"] > 0],
        "reports": [
            {"id": str(eid), "event": i["event_name"],
             # The organization-local date, matching the chart's buckets and the Events list.
             "date": i["occurrence_dt"].astimezone(zone).date().isoformat() if i["occurrence_dt"] else None,
             "viewers": i["peak_viewers"],
             "watch_hours": round(i["watch_hours"], 4) if i["watch_hours"] is not None else None,
             "total_watch_seconds": i.get("total_watch_seconds"),
             "average_watch_seconds": i.get("average_watch_seconds"),
             "engagement": i["engagement_score"],
             "measurement_state": i["measurement_state"],
             "chat_count": i["chat_count"],
             "question_count": i["question_count"],
             "reaction_count": i["reaction_count"],
             # Additional, per broadcast: sessions admitted, peak concurrent sessions, median
             # watch per session, join-time distribution, rejoin rate, playback QoE.
             "sessions": session_metrics_by_event.get(eid),
            }
            for eid, i in ranked
        ],
        # The same metrics over the whole range. The median is deliberately per event only
        # (see viewing_sessions.combine).
        "session_summary": combine_sessions(list(session_metrics_by_event.values())),
        "devices": None, "locations": None, "traffic_sources": None,
        "breakdowns_note": "Device, location and traffic-source breakdowns need a metering/"
                            "GeoIP pipeline that isn't integrated yet.",
    }


def audience_summary(db: Session, org: Organization, range_key: str = "30d") -> dict:
    """Dataset-wide audience counters for /organization/audience's KPI row.

    Separate from the paged event list on purpose. The page shows one page of events at a
    time, so deriving these four figures from the rows on screen would describe the page
    rather than the organization — "3 events" when the window holds thirty. Same reason
    /admin/users has its own summary endpoint.

    Every figure is counted in SQL over the org's own events inside the selected window:

      events        COUNT of non-deleted events started in the window
      gated         those with registration_required — the event's own flag, not a guess
      registrations COUNT of EventRegistration rows against those events
      at_capacity   events whose registration count has reached registration_limit

    at_capacity counts only events that HAVE a limit. An uncapped event can never be at
    capacity, so including it would be arithmetic on a number that does not exist.
    """
    since = _now() - ANALYTICS_RANGES.get(range_key, ANALYTICS_RANGES["30d"])

    scoped = (
        select(Event.id, Event.registration_required, Event.registration_limit)
        .where(
            Event.org_id == org.id,
            Event.deleted_at.is_(None),
            or_(
                Event.start_time >= since,
                Event.end_time >= since,
                Event.status.in_(("live", "degraded")),
                and_(Event.start_time.is_(None), Event.created_at >= since),
                Event.id.in_(select(BroadcastSession.event_id).where(BroadcastSession.started_at >= since)),
            ),
        )
    ).subquery()

    counts = (
        select(EventRegistration.event_id, func.count().label("n"))
        .where(EventRegistration.event_id.in_(select(scoped.c.id)))
        .group_by(EventRegistration.event_id)
    ).subquery()

    rows = db.execute(
        select(scoped.c.id, scoped.c.registration_required, scoped.c.registration_limit,
               func.coalesce(counts.c.n, 0))
        .select_from(scoped.outerjoin(counts, counts.c.event_id == scoped.c.id))
    ).all()

    registrations = sum(r[3] for r in rows)
    return {
        "range": range_key,
        "since": since,
        "events": len(rows),
        "registration_required": sum(1 for r in rows if r[1]),
        # A real count of rows, so 0 means nobody registered — never "not measured".
        "registrations": registrations,
        "at_capacity": sum(1 for r in rows if r[2] and r[3] >= r[2]),
        # Named so the client can say WHY at_capacity may be 0 rather than implying nothing
        # is full: an org that caps nothing has no event that can reach capacity.
        "events_with_capacity": sum(1 for r in rows if r[2]),
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
        select(Event.id).where(
            Event.org_id == org.id,
            Event.deleted_at.is_(None),
            or_(
                Event.start_time >= since,
                Event.end_time >= since,
                Event.status.in_(("live", "degraded")),
                and_(Event.start_time.is_(None), Event.created_at >= since),
                Event.id.in_(select(BroadcastSession.event_id).where(BroadcastSession.started_at >= since)),
            ),
        )
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

    # Reuse, don't re-derive: analytics()'s summary is the same real peak-concurrency figure
    # /organization/analytics already shows. It's the only audience signal available at all for
    # an open public event (no EventRegistration row exists), so it's added on top of the real
    # claimed-registration count rather than blended into it — a private event's real count is
    # never diluted by a rough one.
    #
    # The key is `peak_viewers_summed`, NOT `viewers`. analytics() renamed it precisely because
    # the number is the sum of each event's peak concurrency and cannot support a "viewers"
    # claim; this reader was missed in that rename and raised KeyError: 'viewers' on every
    # request, for every organization and role. Read it under its accurate name.
    base = analytics(db, org, range_key)
    peak_viewers_sum = base["summary"]["peak_viewers_summed"]
    watch_hours = base["summary"]["watch_hours"]

    attendees_estimated = len(claimed_by_email) + peak_viewers_sum
    returning = sum(1 for n in claimed_by_email.values() if n > 1)
    # watch_hours is None when no AnalyticsSnapshot row exists for the window — nothing was
    # measured, which is a different fact from "measured, and the answer was zero". A derived
    # average inherits that: unavailable stays unavailable rather than becoming 0.0, and
    # `None * 60` (a TypeError sitting directly behind the KeyError above) can no longer run.
    # A measured 0.0 still divides normally and reports 0.0.
    avg_watch_minutes = (
        round((watch_hours * 60) / attendees_estimated, 1)
        if watch_hours is not None and attendees_estimated
        else None
    )
    show_rate = round(100 * private_claimed / private_registered) if private_registered else None

    return {
        "range": range_key, "since": since,
        # Wire name kept stable (one consumer, AudienceAccess.jsx) but it is NOT a count of
        # distinct people: it is claimed private-registration emails plus the sum of each
        # event's peak concurrency, so anyone who attended two events is counted twice. The
        # `_estimated` flag beside it is what the UI keys its caveat off, and the UI label
        # reads "Estimated attendees" rather than "Unique attendees" for the same reason
        # analytics() renamed "Total Viewers" to peak_viewers_summed.
        "unique_attendees": attendees_estimated, "unique_attendees_estimated": True,
        # A real count: distinct claimed emails appearing at more than one event.
        "returning": returning,
        # None = never sampled (UI renders "—"); 0.0 = sampled and idle.
        "avg_watch_minutes": avg_watch_minutes, "avg_watch_minutes_estimated": True,
        "show_rate": show_rate, "show_rate_basis": "private_invited_events_only",
    }


# ── sessions and media ────────────────────────────────────────────────────────

def sessions(db: Session, org_id, since: datetime, limit: int = 6) -> dict:
    """Active counts plus the live/recent session list, scoped to the operational window.

    `since` used to be accepted and ignored — every figure here was all-time, so the console's
    range control refetched and returned identical numbers no matter what it was set to. It is
    now the window for everything that HAS a time: ended sessions, and peak audience.

    What is deliberately NOT windowed: live/paused/starting_soon. "On air now" is a question
    about now, and a broadcast that started before the window is still on air.
    """
    rows = db.scalars(
        select(BroadcastSession)
        .where(
            BroadcastSession.org_id == org_id,
            # Still running (no end yet) OR it ended inside the window.
            or_(BroadcastSession.ended_at.is_(None), BroadcastSession.ended_at >= since),
        )
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
        # Peak inside the window — this is what the range control now moves. `rows` is already
        # the windowed set (live/paused plus anything that ended since `since`).
        "peak_audience": max((s.peak_viewers or 0 for s in rows), default=0) or None,
        # All-time peak alongside it, so a UI can say "peak N all time" without a second call
        # and without mislabelling the windowed figure as a record.
        "peak_audience_all_time": db.scalar(
            select(func.coalesce(func.max(BroadcastSession.peak_viewers), 0))
            .where(BroadcastSession.org_id == org_id)
        ) or None,
        "items": listed,
        # There is no self-service vs managed-event distinction in the schema.
        "breakdown_note": "Self-service vs managed classification is not modelled.",
    }


# Bucket width + label format per operational window, chosen so a series lands at roughly
# 12–30 points: dense enough to read as a trend, sparse enough for a 34px sparkline.
_TREND_BUCKETS = {
    "1h": (timedelta(minutes=5), "%H:%M"),
    "24h": (timedelta(hours=1), "%H:%M"),
    "7d": (timedelta(hours=6), "%b %d %H:%M"),
    "30d": (timedelta(days=1), "%b %d"),
}


def audience_trend(db: Session, org_id, since: datetime, until: datetime,
                   range_key: str = "24h") -> list[dict]:
    """Concurrent audience across the org over the window, from real AnalyticsSnapshot rows.

    Two deliberate choices:
      * PEAK per bucket, not mean — an operator asks "how many were watching at once", and a
        mean across the bucket hides the spike that mattered.
      * Concurrency across events is the SUM of each event's peak in that bucket, which is the
        same rollup `current_audience` above already uses (sum of the latest snapshot per
        event). Consistent with the number shown beside it, rather than a second definition.

    Empty window -> [] (not zeros), so the UI can say "no history yet" instead of drawing a
    flat line that looks like a measured zero.
    """
    width, fmt = _TREND_BUCKETS.get(range_key, _TREND_BUCKETS["24h"])
    rows = db.execute(
        select(AnalyticsSnapshot.created_at, AnalyticsSnapshot.event_id, AnalyticsSnapshot.viewers)
        .where(AnalyticsSnapshot.org_id == org_id, AnalyticsSnapshot.created_at >= since)
        .order_by(AnalyticsSnapshot.created_at)
    ).all()
    if not rows:
        return []

    seconds = width.total_seconds()
    # (bucket index, event) -> that event's peak inside the bucket.
    per_event: dict[tuple[int, object], int] = {}
    for created_at, event_id, viewers in rows:
        when = _aware(created_at)
        if when is None:
            continue
        idx = int((when - since).total_seconds() // seconds)
        key = (idx, event_id)
        per_event[key] = max(per_event.get(key, 0), viewers or 0)

    totals: dict[int, int] = {}
    for (idx, _event_id), peak in per_event.items():
        totals[idx] = totals.get(idx, 0) + peak

    # Emit every bucket in the window, including the quiet ones — a gap in the middle of a
    # trend is information, and skipping it would compress the x-axis into a lie.
    buckets = max(1, int((until - since).total_seconds() // seconds))
    return [
        {"label": (since + width * i).strftime(fmt), "value": totals.get(i, 0)}
        for i in range(buckets + 1)
    ]


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
                # Deep-links to the panel that actually holds the keys; the bare path opens
                # Settings on General, which has no credential on it.
                "to": "/organization/settings?tab=developer",
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
    #
    # Compared through normalize_subscription_state, not against a literal. This tested only
    # `== "trial"`, the PRE-Section-12 spelling, while every subscription current code creates
    # is written as `trialing` — so the warning never fired for any of them and tenants got no
    # notice before their trial ended. Normalizing matches both spellings, so the legacy rows
    # this string was written for keep working too.
    #
    # The commercial meaning is unchanged: same threshold, same severities, same copy.
    if normalize_subscription_state(ent.get("status")) == "trialing" and ent.get("trial_ends_at"):
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
            "to": "/organization/settings?tab=security",
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

    # `enforce_sso` and `require_2fa` are stored preferences with NO enforcement behind
    # them: there is no IdP, no domain verification, no certificate lifecycle and no SSO
    # sign-in route, and nothing in the login path consults require_2fa. Reporting them as
    # enforced controls told operators their Organization was protected by measures that do
    # not exist, so the API now reports the request separately from the enforcement.
    #
    # ZST-EC-001 ORG-005 depends on this distinction: no SSO lifecycle mail may ever be
    # driven by a flag that enforces nothing.
    sso_requested = bool(security.get("enforce_sso"))
    mfa_requested = bool(security.get("require_2fa"))
    return {
        "sso_enforced": False,
        "sso_requested": sso_requested,
        "sso_available": False,
        "two_factor_required": False,
        "two_factor_requested": mfa_requested,
        "two_factor_available": False,
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


# The gates that represent readiness WORK — somebody has to roster a host and turn
# recording on. The others (title, schedule, account standing, redundancy) pass on their
# own, so they say nothing about whether readiness has been started.
#
# "moderator" was dropped alongside the gate itself (services/ops._GATES). Leaving it here
# would have been worse than cosmetic: _readiness_state below compares done-work against
# total-work, so a gate that can never pass would have pinned every fully-prepared event at
# "in_progress" and stopped it ever reporting "passed".
_WORK_GATES = ("host", "recording")


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
            # On air = live or degraded (a degraded broadcast is still on air) — the same set
            # the Events list's "Live now" filter shows, so the rail badge and the pages agree.
            "live_events": db.scalar(
                select(func.count(Event.id)).where(
                    Event.org_id == org.id, Event.deleted_at.is_(None),
                    Event.status.in_(ON_AIR_STATUSES))
            ) or 0,
        },
        "user": {"name": user.full_name, "email": user.email,
                 # The role the API authorizes, so the rail shows exactly what the routes allow
                 # (display only — every route and endpoint re-checks it).
                 "role": user.role,
                 "role_label": _role_label(user.role),
                 # The console chrome (sidebar, account menu) shows the photo when there is one.
                 "avatar_url": user.avatar_url},
        # Display metadata for the rail, never authority (ZST-ENG-DASH-001 §8.1). Developers is
        # the conditional eighth item, "off by default" (§3.2): no plan carries an API
        # entitlement, so it appears once the organization has actually set up developer
        # access — an API key or a webhook endpoint. The pages stay reachable from
        # Organization › Integrations either way.
        "features": {"developers": developer_access_in_use(db, org)},
    }


def developer_access_in_use(db: Session, org: Organization) -> bool:
    if org.api_keys or org.webhook_urls:
        return True
    return bool(db.scalar(select(func.count(WebhookEndpoint.id)).where(WebhookEndpoint.org_id == org.id)))


def _role_label(role: str | None) -> str:
    """Display label for a platform role. "moderator" is kept here deliberately: the role is
    retired and no new row can carry it, but a database written before the migration still
    can (models/user.LEGACY_USER_ROLES), and labelling such a person a generic "Member"
    would misreport who they are. Display only — grants nothing."""
    return {"org_admin": "Organization Owner", "host": "Host", "moderator": "Moderator",
            "speaker": "Speaker", "viewer": "Viewer",
            "super_admin": "Super Admin"}.get(role or "", "Member")


def _isolated(db: Session, label: str, fn, *args):
    """Run one NON-CRITICAL part of the overview so that its failure cannot take the rest down.

    /organization/overview feeds the Dashboard, Billing, Profile, Streaming Sessions, Developer
    Platform and the console shell, not only the Support page; a bug or a missing table in the
    status subsystem must not turn all of them into 500s. The part runs in a SAVEPOINT, so its
    failed SQL rolls back alone and the surrounding transaction stays usable. A failure is logged
    with its traceback for operators and returns None, which the Support page renders as
    "temporarily unavailable" — never as Operational, and never with the exception text."""
    try:
        with db.begin_nested():
            return fn(*args)
    except Exception:  # noqa: BLE001 — isolation is the point; the traceback goes to the log
        log.exception("organization overview: %s failed; returning it as unavailable", label)
        return None


def overview(db: Session, org: Organization, user: User, range_: str = "24h",
             include_test: bool = True) -> dict:
    """One call for the whole Organization Overview page."""
    generated_at = _now()
    since = generated_at - RANGES.get(range_, RANGES["24h"])

    # One probe snapshot for both views of it: the lifecycle stages (unchanged, for the pages
    # that read them) and the customer-facing service status (/organization/support).
    health = admin_svc.platform_health(db)
    stages = stage_health(db, org.id, since, health=health)
    ent = entitlements(db, org)
    sess = sessions(db, org.id, since)
    media = media_assets(db, org.id)
    readiness = upcoming_events(db, org.id, include_test=include_test)
    # Over a fixed 24 hours whatever the toolbar range, because the Support page's column says so.
    service_status = _isolated(db, "service status", customer_status, db, org.id, health,
                               generated_at - RANGES["24h"], generated_at)
    maintenance = _isolated(db, "maintenance schedule", maintenance_schedule, db)

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
        # None when that subsystem failed (_isolated): the Support page then says so.
        "service_status": service_status,
        "maintenance": maintenance,
        "sessions": sess,
        # Audience over the same window the toolbar selects. Feeds the KPI tiles' sparklines,
        # which until now had no series to draw and always fell back to a flat hairline.
        "trends": {"audience": audience_trend(db, org.id, since, generated_at, range_)},
        "media_assets": media,
        "entitlements": ent,
        "api": api_posture(),
        # attention() is "things an org admin has to act on", every item linking to an
        # admin-only page. Any member can read this overview, so below org_admin it is empty:
        # a member who had just accepted their invitation was shown the organization's
        # "N invitations awaiting acceptance · Manage", which was neither theirs nor theirs to act on.
        "attention": attention(db, org, ent, readiness) if user.role in ("org_admin", "super_admin") else [],
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
    # Superseded on the Support page by `maintenance` (maintenance_schedule), which reads the
    # published windows. What is still missing is a way for operators to publish one.
    {"field": "security_support.maintenance_window",
     "needs": "a way for operators to publish maintenance windows; until then an empty "
              "schedule is not proof that nothing is planned"},
]
