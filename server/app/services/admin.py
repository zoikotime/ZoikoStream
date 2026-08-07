"""Derived/aggregated logic for the Super Admin dashboard, analytics, health and live
monitoring. Everything here is computed from real tables. Where no data source exists yet
(concurrent viewers, per-stream bitrate, infra latency for un-integrated services), the
value is null/not_configured with a note — never a fabricated number."""

import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import event as event_crud
from ..models import BroadcastSession, Event, LiveRecording, Organization, Plan, Subscription, User
from ..security import _ROLE_RANK
from . import livekit

# Excluded from customer-facing counts — it only holds the super admin (matches dashboard.py).
PLATFORM_ORG_NAME = "ZoikoStream Platform"
_MRR_STATUSES = ("active", "trial")


def _customer_orgs():
    return Organization.name != PLATFORM_ORG_NAME


def _month_series(db: Session, col, id_col) -> list[dict]:
    """Rows-per-month for a timestamp column. Only months with data appear."""
    rows = db.execute(
        select(func.date_trunc("month", col).label("m"), func.count(id_col))
        .group_by("m").order_by("m")
    ).all()
    return [{"label": m.strftime("%b %Y"), "value": int(n)} for m, n in rows if m]


def _monthly_revenue(db: Session) -> float:
    """Current MRR = sum of plan price over active + trial subscriptions."""
    total = db.scalar(
        select(func.coalesce(func.sum(Plan.price_monthly), 0))
        .select_from(Subscription).join(Plan, Subscription.plan_id == Plan.id)
        .where(Subscription.status.in_(_MRR_STATUSES))
    )
    return round(float(total or 0), 2)


def _streaming_hours(db: Session) -> float:
    """Real total broadcast hours from broadcast-session start/end timestamps (live = up
    to now). paused_ms is subtracted so time spent paused doesn't count as streamed."""
    now = datetime.now(timezone.utc)
    sessions = db.scalars(select(BroadcastSession).where(BroadcastSession.started_at.isnot(None))).all()
    # ponytail: loads all sessions; add a SQL age() sum if this table gets large.
    secs = sum(
        ((s.ended_at or now) - s.started_at).total_seconds() - (s.paused_ms or 0) / 1000
        for s in sessions if s.started_at
    )
    return round(max(secs, 0) / 3600, 1)


def dashboard_summary(db: Session) -> dict:
    total_orgs = db.scalar(select(func.count(Organization.id)).where(_customer_orgs())) or 0
    active_orgs = db.scalar(
        select(func.count(Organization.id)).where(_customer_orgs(), Organization.status == "active")
    ) or 0
    total_users = db.scalar(select(func.count(User.id))) or 0
    live_events = db.scalar(select(func.count(Event.id)).where(Event.status == "live")) or 0
    storage_used = db.scalar(
        select(func.coalesce(func.sum(Organization.storage_used_gb), 0)).where(_customer_orgs())
    ) or 0

    health = platform_health(db)

    latest_orgs = db.scalars(
        select(Organization).where(_customer_orgs()).order_by(Organization.created_at.desc()).limit(5)
    ).all()
    latest_users = db.scalars(select(User).order_by(User.created_at.desc()).limit(5)).all()
    alerts = [
        {"id": s["id"], "title": s["name"], "detail": s.get("note", ""), "severity": s["status"]}
        for s in health["services"] if s["status"] in ("warn", "down")
    ]

    return {
        "summary": {
            "total_organizations": total_orgs,
            "active_organizations": active_orgs,
            "total_users": total_users,
            "live_events": live_events,
            "concurrent_viewers": None,          # source: LiveKit room stats (not integrated)
            "monthly_revenue": _monthly_revenue(db),
            "streaming_hours": _streaming_hours(db),
            "storage_used_gb": round(float(storage_used), 1),
            "platform_health": health["overall"],
        },
        "organization_growth": _month_series(db, Organization.created_at, Organization.id),
        "user_growth": _month_series(db, User.created_at, User.id),
        "revenue_growth": _revenue_series(db),
        "latest_organizations": [
            {"id": str(o.id), "name": o.name, "status": o.status, "created_at": o.created_at}
            for o in latest_orgs
        ],
        "latest_signups": [
            {"id": str(u.id), "full_name": u.full_name, "email": u.email, "role": u.role,
             "organization_name": u.organization.name if u.organization else None,
             "created_at": u.created_at}
            for u in latest_users
        ],
        "recent_alerts": alerts,
        "support_tickets": [],                    # source: support ticketing (not integrated)
    }


def _revenue_series(db: Session) -> list[dict]:
    """New MRR added per month = sum of plan price over subscriptions started that month."""
    rows = db.execute(
        select(func.date_trunc("month", Subscription.started_at).label("m"),
               func.coalesce(func.sum(Plan.price_monthly), 0))
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(Subscription.status != "cancelled")
        .group_by("m").order_by("m")
    ).all()
    return [{"label": m.strftime("%b %Y"), "value": round(float(v), 2)} for m, v in rows if m]


def analytics(db: Session) -> dict:
    return {
        "mrr": _monthly_revenue(db),
        "streaming_hours": _streaming_hours(db),
        "revenue": _revenue_series(db),
        "organizations": _month_series(db, Organization.created_at, Organization.id),
        "users": _month_series(db, User.created_at, User.id),
        # traffic + bandwidth series require a request/metering pipeline that isn't integrated yet.
        "note": "Traffic and bandwidth time-series require a metering pipeline (not yet integrated).",
    }


ROLE_META = {
    "super_admin": ("Super Admin", "Platform owner. Clears every gate and sees every organization.", [
        "Manage all organizations, users and subscriptions",
        "Read the platform audit log",
        "Edit platform settings, feature flags and releases",
        "Bypasses organization isolation on every query",
    ]),
    "org_admin": ("Organization Admin", "Owns one organization and everything inside it.", [
        "Manage organization profile, branding, domain and settings",
        "Invite, edit and remove members",
        "Create, edit and delete events",
        "Assign hosts, moderators and speakers",
        "Host and moderate any event in the organization",
    ]),
    "host": ("Host", "Runs the broadcast for events they are assigned to.", [
        "Go live, pause, resume and end the broadcast",
        "Start, pause and stop recording",
        "Control stage, waiting room and live settings",
        "Edit events they own or are assigned to host",
        "Full moderation on their assigned events",
    ]),
    "moderator": ("Moderator", "Runs the audience for events they are assigned to.", [
        "Approve, pin, delete and annotate chat",
        "Manage Q&A, polls and announcements",
        "Mute, timeout, stage, ban and remove participants",
        "Cannot end the broadcast or stop the recording",
    ]),
    "speaker": ("Speaker", "Presents on stage when invited by a host.", [
        "Publish audio and video once granted the stage",
        "Take part in chat, Q&A and polls",
        "No moderation or broadcast controls",
    ]),
    "viewer": ("Viewer", "Attends events.", [
        "Watch the stream",
        "Send chat messages and reactions",
        "Ask questions and vote in polls",
        "Raise a hand",
    ]),
}


def roles() -> list[dict]:
    """The authorization ladder as reference data, derived from security._ROLE_RANK so this
    can never drift from what is actually enforced. Read-only: authorization is
    code-defined, so there is nothing here to edit."""
    return [
        {
            "role": role,
            "rank": rank,
            "label": ROLE_META[role][0],
            "description": ROLE_META[role][1],
            "capabilities": ROLE_META[role][2],
        }
        for role, rank in sorted(_ROLE_RANK.items(), key=lambda kv: -kv[1])
        if role in ROLE_META
    ]


def live_events(db: Session, state: str = "live") -> list[dict]:
    """Broadcast sessions for the platform monitor, joined up to their event and org.
    state="live"   -> currently broadcasting (including paused mid-broadcast), newest first
    state="recent" -> finished broadcasts, most recently ended first
    """
    if state == "recent":
        stmt = (
            select(BroadcastSession)
            .where(BroadcastSession.status == "ended", BroadcastSession.ended_at.isnot(None))
            .order_by(BroadcastSession.ended_at.desc())
            .limit(50)
        )
    else:
        stmt = (
            select(BroadcastSession)
            .where(BroadcastSession.status.in_(("live", "paused")))
            .order_by(BroadcastSession.started_at.desc())
        )
    sessions = db.scalars(stmt).all()
    out = []
    for s in sessions:
        ev = db.get(Event, s.event_id)
        # A "live" session whose event was deleted out from under it (old data from before
        # deletes force-ended the broadcast first) is unreachable and unmanageable — showing
        # it as live is actively misleading. "recent"/ended sessions keep their historical
        # row regardless, same as the audit log would.
        if state != "recent" and (ev is None or ev.deleted_at is not None):
            continue
        org = ev.organization if ev else None
        out.append({
            "id": str(s.id),
            "event_id": str(s.event_id),
            "title": ev.title if ev else None,
            "organization": org.name if org else None,
            "region": org.region if org else None,
            "server": f"event_{s.event_id}",  # matches services.moderation.Ctx.room
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "health": "ok" if s.status in ("live", "paused") else None,
            "viewers": None,        # source: LiveKit room stats (not integrated)
            "bitrate_kbps": None,   # source: LiveKit track stats (not integrated)
        })
    return out


def _member_out(u: User) -> dict:
    return {"id": str(u.id), "name": u.full_name, "email": u.email}


def event_detail(db: Session, event_id) -> dict | None:
    """Cross-org event detail for the Super Admin console. Unlike /events/{id} (org-scoped
    to the caller, which 404s a super admin on every event outside their own platform org —
    see routers/admin.py's event endpoints for the fix), this reads any event directly."""
    ev = db.get(Event, event_id)
    if ev is None or ev.deleted_at is not None:
        return None
    org = ev.organization
    session = db.scalar(
        select(BroadcastSession).where(BroadcastSession.event_id == event_id)
        .order_by(BroadcastSession.created_at.desc())
    )
    recording = db.scalar(
        select(LiveRecording).where(LiveRecording.event_id == event_id)
        .order_by(LiveRecording.created_at.desc())
    )
    return {
        "id": str(ev.id), "title": ev.title, "description": ev.description,
        "status": ev.status, "visibility": ev.visibility, "category": ev.category,
        "start_time": ev.start_time, "end_time": ev.end_time, "created_at": ev.created_at,
        "organization": {"id": str(org.id), "name": org.name} if org else None,
        "broadcast": None if session is None else {
            "id": str(session.id), "status": session.status,
            "started_at": session.started_at, "ended_at": session.ended_at,
            "peak_viewers": session.peak_viewers,
        },
        "recording": None if recording is None else {
            "id": str(recording.id), "status": recording.status,
            "enforced": recording.enforced, "size_bytes": recording.size_bytes,
        },
        "hosts": [_member_out(u) for u in event_crud.list_assignees(db, event_id, "host")],
        "moderators": [_member_out(u) for u in event_crud.list_assignees(db, event_id, "moderator")],
        "speakers": [_member_out(u) for u in event_crud.list_assignees(db, event_id, "speaker")],
    }


def platform_health(db: Session) -> dict:
    """Real signals: DB is pinged; integrated services report ok; un-integrated ones report
    not_configured (informational, not counted as an outage)."""
    services = []

    t = time.perf_counter()
    try:
        db.execute(select(1))
        services.append({"id": "database", "name": "Database", "status": "ok",
                         "latency_ms": round((time.perf_counter() - t) * 1000, 1),
                         "note": "PostgreSQL reachable"})
    except Exception:
        services.append({"id": "database", "name": "Database", "status": "down",
                         "latency_ms": None, "note": "Unreachable"})

    services.append({"id": "api", "name": "API", "status": "ok", "note": "Serving requests"})
    services.append({"id": "auth", "name": "Authentication", "status": "ok", "note": "JWT verification active"})

    def configured(sid, name, present, ready_note, missing_note):
        return {"id": sid, "name": name, "status": "ok" if present else "not_configured",
                "note": ready_note if present else missing_note}

    services.append(configured("streaming", "Streaming (LiveKit)", bool(settings.LIVEKIT_URL),
                               "Configured", "LIVEKIT_URL not set"))
    services.append(configured("email", "Email", bool(settings.RESEND_API_KEY),
                               "Resend configured", "RESEND_API_KEY not set"))
    services.append(configured("storage", "Storage", livekit.gcs_configured(),
                               "Google Cloud Storage configured", "GCS_BUCKET / GCS_CREDENTIALS_PATH not set"))
    services.append({"id": "cdn", "name": "CDN", "status": "not_configured", "note": "Not yet integrated"})
    services.append({"id": "workers", "name": "Background Workers", "status": "not_configured",
                     "note": "Not yet integrated"})

    live = [s["status"] for s in services if s["status"] in ("ok", "warn", "down")]
    overall = "down" if "down" in live else "warn" if "warn" in live else "ok"
    return {"overall": overall, "services": services}
