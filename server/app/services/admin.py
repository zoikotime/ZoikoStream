"""Derived/aggregated logic for the Super Admin dashboard, analytics, health and live
monitoring. Everything here is computed from real tables. Where no data source exists yet
(concurrent viewers, per-stream bitrate, infra latency for un-integrated services), the
value is null/not_configured with a note — never a fabricated number."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import event as event_crud
from ..models import (
    BroadcastSession, Event, LiveRecording, Organization, Plan, Subscription, User,
    SUBSCRIPTION_REVENUE_STATES, SUBSCRIPTION_TERMINATED_STATES,
)
from ..security import _ROLE_RANK
from . import livekit

log = logging.getLogger(__name__)

# Excluded from customer-facing counts — it only holds the super admin (matches dashboard.py).
PLATFORM_ORG_NAME = "ZoikoStream Platform"
# PAYING states only. SUBSCRIPTION_REVENUE_STATES is ("active", "trial", "trialing") — it is
# the set that counts toward an organization's ENTITLEMENT, which is right for access control
# and wrong for revenue: a trial collects nothing, yet MRR summed every trialing subscription
# at full list price and the Analytics page charted it as "Revenue". The shared constant is
# deliberately left alone (it drives entitlement, not money); this alias narrows only the
# figure that claims to be recurring revenue. Trials are reported separately and labelled.
_TRIAL_STATUSES = ("trial", "trialing")
_MRR_STATUSES = tuple(st for st in SUBSCRIPTION_REVENUE_STATES if st not in _TRIAL_STATUSES)


def _customer_orgs():
    return Organization.name != PLATFORM_ORG_NAME


def _month_series(db: Session, col, id_col) -> list[dict]:
    """Rows-per-month for a timestamp column. Only months with data appear."""
    rows = db.execute(
        select(func.date_trunc("month", col).label("m"), func.count(id_col))
        .group_by("m").order_by("m")
    ).all()
    return [{"label": m.strftime("%b %Y"), "value": int(n)} for m, n in rows if m]


def _monthly_revenue(db: Session, org_id=None) -> float:
    """Contracted MRR = sum of plan LIST price over PAYING (active) subscriptions.

    Two things this is not, and the console now says so. It is not collected revenue:
    ZoikoStream owns plans and entitlements, not invoicing or payment collection, so this is
    the recurring value of what is contracted at list price, before discounts, credits or
    failed payments. And it excludes trials, which pay nothing.

    Plans with no approved price published (Plan.price_monthly IS NULL) contribute nothing:
    SQL SUM skips NULLs, so an unpriced plan is not counted as $0 revenue — it is simply not
    counted. A 0 here therefore means "no priced plans", not "no subscriptions".
    """
    total = db.scalar(
        select(func.coalesce(func.sum(Plan.price_monthly), 0))
        .select_from(Subscription).join(Plan, Subscription.plan_id == Plan.id)
        .where(Subscription.status.in_(_MRR_STATUSES),
               *([Subscription.org_id == org_id] if org_id else []))
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
    """New MRR added per month = sum of plan price over subscriptions started that month.

    Same NULL semantics as _monthly_revenue: unpriced plans are skipped, not zeroed.
    """
    rows = db.execute(
        select(func.date_trunc("month", Subscription.started_at).label("m"),
               func.coalesce(func.sum(Plan.price_monthly), 0))
        .join(Plan, Subscription.plan_id == Plan.id)
        # Paying only, for the same reason as _monthly_revenue: "not terminated" includes
        # every trial still trialing, which added list price for accounts that pay nothing.
        .where(Subscription.status.in_(_MRR_STATUSES))
        .group_by("m").order_by("m")
    ).all()
    return [{"label": m.strftime("%b %Y"), "value": round(float(v), 2)} for m, v in rows if m]


def _trial_list_value(db: Session, org_id=None) -> dict:
    """Trials, reported as themselves: how many, and what they would be worth at list price
    if they converted. Separate from MRR precisely so neither figure misrepresents the other."""
    count, value = db.execute(
        select(func.count(Subscription.id), func.coalesce(func.sum(Plan.price_monthly), 0))
        .select_from(Subscription).join(Plan, Subscription.plan_id == Plan.id)
        .where(Subscription.status.in_(_TRIAL_STATUSES),
               *([Subscription.org_id == org_id] if org_id else []))
    ).one()
    return {"count": int(count or 0), "list_value": round(float(value or 0), 2)}


# Delivery telemetry this platform does not collect. Listed, never charted: each would need
# a player beacon or a CDN log pipeline, neither of which exists, and a chart of zeros for
# them would read as "measured, nothing happened" - the opposite of the truth.
UNMEASURED_TELEMETRY = (
    "Playback quality (QoE)", "Buffering / rebuffer rate", "CDN latency",
    "Bitrate distribution", "Regional delivery latency", "Viewer abandonment",
)

ANALYTICS_RANGES = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _bucket_keys(since, until, daily):
    """Every bucket in the window, in order - so a chart shows empty days as measured zeros
    rather than skipping them."""
    keys = []
    if daily:
        cur = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
        while cur <= until:
            keys.append(cur.date())
            cur += timedelta(days=1)
    else:
        y, mo = since.year, since.month
        while (y, mo) <= (until.year, until.month):
            keys.append((y, mo))
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return keys


def _label(key, daily):
    if daily:
        return key.strftime("%b %d")
    return datetime(key[0], key[1], 1).strftime("%b %Y")


def _bucketed(db, bucket_expr, value_expr, where, since, until, daily, as_float=False):
    rows = db.execute(
        select(bucket_expr.label("b"), value_expr).where(*where).group_by("b")
    ).all()
    found = {}
    for b, v in rows:
        if b is None:
            continue
        key = b.date() if daily else (b.year, b.month)
        found[key] = float(v or 0) if as_float else int(v or 0)
    out = []
    for key in _bucket_keys(since, until, daily):
        val = found.get(key, 0.0 if as_float else 0)
        out.append({"label": _label(key, daily), "value": round(val, 2) if as_float else val})
    return out


def _count_series(db, col, id_col, since, until, daily, *where):
    unit = "day" if daily else "month"
    return _bucketed(db, func.date_trunc(unit, col), func.count(id_col),
                     [col >= since, col <= until, *where], since, until, daily)


def _window_streaming_hours(db, since, until, org_id=None) -> float:
    """Broadcast hours INSIDE the window: each session clipped to [since, until], so one that
    began before the window contributes only the part within it."""
    stmt = select(BroadcastSession).where(
        BroadcastSession.started_at.isnot(None),
        BroadcastSession.started_at <= until,
        or_(BroadcastSession.ended_at.is_(None), BroadcastSession.ended_at >= since),
    )
    if org_id:
        stmt = stmt.where(BroadcastSession.org_id == org_id)
    secs = 0.0
    for sess in db.scalars(stmt).all():
        start = max(_aware(sess.started_at), since)
        end = min(_aware(sess.ended_at) or until, until)
        if end > start:
            secs += (end - start).total_seconds()
    return round(secs / 3600, 1)


def analytics(db: Session, *, org_id=None, range_key: str = "30d",
              since=None, until=None) -> dict:
    """Platform analytics over an explicit window, optionally for one organization.

    Every filter is applied IN SQL. The endpoint used to take no parameters, so the page could
    not be narrowed at all and every chart was all-time and all-tenant.

    Each figure carries its `measurement`:
      measured - counted directly from rows (events, sessions, users, recordings)
      derived  - computed from measured rows under a stated rule (contracted MRR)
    and UNMEASURED_TELEMETRY is returned as a list, never as numbers.
    """
    until = _aware(until) or datetime.now(timezone.utc)
    if range_key == "custom":
        since = _aware(since) or (until - timedelta(days=30))
    else:
        since = until - timedelta(days=ANALYTICS_RANGES.get(range_key, 30))
    if since > until:
        since, until = until, since
    daily = (until - since) <= timedelta(days=92)
    unit = "day" if daily else "month"

    ev_where = [Event.deleted_at.is_(None)] + ([Event.org_id == org_id] if org_id else [])
    user_where = [User.org_id == org_id] if org_id else []
    sub_where = [Subscription.status.in_(_MRR_STATUSES)] + (
        [Subscription.org_id == org_id] if org_id else [])
    rec_where = [LiveRecording.org_id == org_id] if org_id else []
    bs_where = [BroadcastSession.org_id == org_id] if org_id else []

    def count(model, col, *where):
        return int(db.scalar(
            select(func.count(model.id)).where(col >= since, col <= until, *where)) or 0)

    revenue = _bucketed(
        db, func.date_trunc(unit, Subscription.started_at),
        func.coalesce(func.sum(Plan.price_monthly), 0),
        [Subscription.plan_id == Plan.id, Subscription.started_at >= since,
         Subscription.started_at <= until, *sub_where],
        since, until, daily, as_float=True)

    return {
        "window": {"range": range_key, "since": since, "until": until,
                   "org_id": str(org_id) if org_id else None, "bucket": unit},
        # Point-in-time, not windowed: MRR is "what is contracted now". Org-scoped when set.
        "mrr": _monthly_revenue(db, org_id),
        "mrr_basis": "contracted_list_price",
        "trials": _trial_list_value(db, org_id),
        "streaming_hours": _window_streaming_hours(db, since, until, org_id),
        "totals": {
            "events_created": {"value": count(Event, Event.created_at, *ev_where),
                               "measurement": "measured"},
            "broadcasts_completed": {"value": count(BroadcastSession, BroadcastSession.ended_at,
                                                    *bs_where),
                                     "measurement": "measured"},
            "recordings": {"value": count(LiveRecording, LiveRecording.created_at, *rec_where),
                           "measurement": "measured"},
            "new_users": {"value": count(User, User.created_at, *user_where),
                          "measurement": "measured"},
        },
        "revenue": revenue,
        # Organization growth is meaningless inside one organization, so it is omitted rather
        # than shown as a single flat point.
        "organizations": ([] if org_id else
                          _count_series(db, Organization.created_at, Organization.id,
                                        since, until, daily)),
        "users": _count_series(db, User.created_at, User.id, since, until, daily, *user_where),
        "events": _count_series(db, Event.created_at, Event.id, since, until, daily, *ev_where),
        "measurement": {"mrr": "derived", "revenue": "derived", "streaming_hours": "measured",
                        "users": "measured", "events": "measured", "organizations": "measured"},
        "unmeasured": list(UNMEASURED_TELEMETRY),
        "note": "Delivery telemetry (QoE, buffering, CDN, bitrate) is not collected on this platform.",
    }


def usage_overview(db: Session, *, q: str | None = None, plan_slug: str | None = None,
                   sub_status: str | None = None, page: int = 1, page_size: int = 25,
                   sort: str = "name") -> dict:
    """Per-organization entitlements and measured usage, for Usage & Entitlements.

    ONE engine: each organization's quota bars come from services/org.entitlements - the same
    function the organization's own console renders - so the Super Admin view and the tenant
    view cannot disagree about a tenant's usage. This function only pages, filters and adds
    the counts that engine does not carry.

    Filtering and paging happen in SQL over Organization, never over a fetched page.
    """
    from . import org as org_svc

    base = select(Organization).where(_customer_orgs())
    if q:
        base = base.where(func.lower(Organization.name).like(f"%{q.lower()}%"))
    if plan_slug or sub_status:
        sub_q = select(Subscription.org_id).join(Plan, Subscription.plan_id == Plan.id)
        if plan_slug:
            sub_q = sub_q.where(Plan.slug == plan_slug)
        if sub_status:
            from ..models.subscription import stored_spellings
            sub_q = sub_q.where(Subscription.status.in_(stored_spellings(sub_status)))
        base = base.where(Organization.id.in_(sub_q))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    order = {
        "name": (Organization.name.asc(),),
        "-name": (Organization.name.desc(),),
        # Heaviest storage first: the question an operator usually brings to this page.
        "storage": (Organization.storage_used_gb.desc().nulls_last(), Organization.name.asc()),
    }.get(sort, (Organization.name.asc(),))
    orgs = db.scalars(
        base.order_by(*order, Organization.id.asc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    ids = [o.id for o in orgs]

    def per_org(model, col_org, *where):
        if not ids:
            return {}
        return dict(db.execute(
            select(col_org, func.count(model.id)).where(col_org.in_(ids), *where).group_by(col_org)
        ).all())

    events = per_org(Event, Event.org_id, Event.deleted_at.is_(None))
    broadcasts = per_org(BroadcastSession, BroadcastSession.org_id,
                         BroadcastSession.ended_at.isnot(None))
    recordings = per_org(LiveRecording, LiveRecording.org_id)
    # Recording bytes: summed over KNOWN sizes only, with the count of unknown ones reported
    # beside it. The egress_ended webhook carries the size; a row it never reached has none,
    # and folding those in as 0 would understate storage while looking measured.
    rec_bytes = {}
    if ids:
        for org_id, known, unknown in db.execute(
            select(LiveRecording.org_id,
                   func.coalesce(func.sum(LiveRecording.size_bytes), 0),
                   func.count(LiveRecording.id).filter(LiveRecording.size_bytes.is_(None)))
            .where(LiveRecording.org_id.in_(ids)).group_by(LiveRecording.org_id)
        ).all():
            rec_bytes[org_id] = (int(known or 0), int(unknown or 0))

    items = []
    for o in orgs:
        ent = org_svc.entitlements(db, o)
        known, unknown = rec_bytes.get(o.id, (0, 0))
        items.append({
            "org_id": str(o.id),
            "organization": o.name,
            "is_test": bool(getattr(o, "is_test", False)),
            "plan": ent["plan"], "plan_slug": ent["plan_slug"],
            "subscription_status": ent["status"],
            "entitlement_source": "subscription" if ent["plan"] else "none",
            "current_period_end": ent["current_period_end"],
            "trial_ends_at": ent["trial_ends_at"],
            "quotas": ent["items"],
            "metrics": [
                {"key": "events_created", "label": "Events", "value": events.get(o.id, 0),
                 "unit": "events", "period": "lifetime", "state": "measured"},
                {"key": "broadcasts_completed", "label": "Broadcasts completed",
                 "value": broadcasts.get(o.id, 0), "unit": "broadcasts", "period": "lifetime",
                 "state": "measured"},
                {"key": "recordings", "label": "Recordings", "value": recordings.get(o.id, 0),
                 "unit": "recordings", "period": "lifetime", "state": "measured"},
                {"key": "recording_bytes", "label": "Recording storage (known sizes)",
                 "value": known, "unit": "bytes", "period": "lifetime",
                 "state": "measured", "unknown_count": unknown},
                # Stated, not zero-filled: no pipeline measures delivered bytes per window.
                {"key": "delivery_windowed", "label": "Delivery volume (windowed)",
                 "value": None, "unit": "GB", "period": None, "state": "unavailable"},
            ],
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size, "sort": sort}


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
        "Assign hosts and speakers",
        "Host and moderate any event in the organization",
    ]),
    "host": ("Host", "Runs the broadcast AND the audience for events they are assigned to.", [
        "Go live, pause, resume and end the broadcast",
        "Start, pause and stop recording",
        "Control stage, waiting room and live settings",
        "Edit events they own or are assigned to host",
        "Approve, pin, delete and annotate chat",
        "Manage Q&A, polls and announcements",
        "Mute, timeout, stage, ban and remove participants",
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


async def live_events(db: Session, state: str = "live") -> list[dict]:
    """Broadcast sessions for the platform monitor, joined up to their event and org.
    state="live"   -> currently broadcasting (including paused mid-broadcast), newest first
    state="recent" -> finished broadcasts, most recently ended first

    ── WHY THIS ASKS LIVEKIT ────────────────────────────────────────────────────────────
    The "live" list used to be `BroadcastSession.status IN ("live","paused")` and nothing
    else, and `health` was `"ok" if s.status in ("live","paused")` — the row vouching for
    itself. A session whose worker died never reaches "ended", so it sat in the monitor for
    weeks: the reported case showed two rows started 15 days earlier, 383 hours of
    "duration", both reading **Operational**. A status page that calls a fortnight-old
    corpse healthy is worse than no status page, because operators learn to discount it.

    So every supposedly-live session is now reconciled against the media server, which is
    the only thing that actually knows. Three answers, kept distinct:

      room present  -> genuinely live (or paused); real participant count attached
      room absent   -> stale. Retired through broadcast._retire_stale_session — the SAME
                       lifecycle the analytics sampler uses, so the row is closed with a
                       reason, bus presence/state is cleared, and Event.status is left
                       alone — then dropped from this list. It reappears under Recently
                       Ended, where it belongs.
      cannot ask    -> health "unknown". NOT retired and NOT called healthy: a LiveKit
                       outage must not make the console start closing live broadcasts, and
                       it must not let them keep claiming Operational either.
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
            "server": livekit.room_for_event(s.event_id),  # services.livekit.room_for_event == Ctx.room
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "status": s.status,
            # Filled in by the reconciliation pass below for "live"; an ended session is
            # history and has no current health to report.
            "health": None,
            "viewers": None,
            "bitrate_kbps": None,   # source: LiveKit track stats (not integrated)
        })
    if state == "recent":
        return out
    return await _reconcile_live(out)


# Health vocabulary for the Live Operations table. "unknown" is a first-class answer, not a
# fallback — see _reconcile_live.
LIVE_HEALTH_OK = "ok"
LIVE_HEALTH_UNKNOWN = "unknown"


async def _reconcile_live(rows: list[dict]) -> list[dict]:
    """Confirm each supposedly-live session against LiveKit; retire the ones that are gone.

    Probed concurrently — an operator opening the monitor should not wait on N sequential
    round trips, and these are independent reads.
    """
    # Imported here: services/broadcast imports this module's siblings, and a module-level
    # import would close the cycle.
    from . import broadcast as bc

    if not rows:
        return rows
    probes = await asyncio.gather(
        *(livekit.room_is_live(r["server"]) for r in rows), return_exceptions=True
    )

    live: list[dict] = []
    for row, probe in zip(rows, probes):
        # An exception that escaped the probe is still "we could not tell".
        alive = None if isinstance(probe, BaseException) else probe
        if alive is False:
            # The media server says this room does not exist. Close it through the real
            # lifecycle rather than editing the row here, so the reason, the bus cleanup and
            # the audit trail are the same ones every other stale session gets.
            retired = await bc._retire_stale_session(row["id"], row["event_id"], "livekit_room_absent")
            log.warning("live-operations: session %s (event %s) had no LiveKit room — retired=%s",
                        row["id"], row["event_id"], retired)
            continue
        row["health"] = LIVE_HEALTH_OK if alive else LIVE_HEALTH_UNKNOWN
        if alive:
            # Real participant count, or None when LiveKit cannot say. Never 0 by default.
            row["viewers"] = await livekit.room_participant_count(row["server"])
        live.append(row)
    return live


def _recording_search(q: str):
    """Recordings whose event title or organization name matches `q` - applied in SQL so a
    search reaches past the newest `limit` rows the list returns."""
    like = f"%{q.lower()}%"
    return LiveRecording.event_id.in_(
        select(Event.id).join(Organization, Event.org_id == Organization.id, isouter=True)
        .where(or_(func.lower(Event.title).like(like), func.lower(Organization.name).like(like)))
    )


RECORDING_STATES = ("ready", "unverified", "processing", "in_progress", "failed")


def recordings_summary(db: Session, org_id=None, q: str | None = None) -> dict:
    """Dataset-wide recording counts for the Media console's KPIs.

    The KPIs used to be counted off the list, which returns the newest 200 rows - so
    "Recordings" stopped at 200 and read like a total. This counts every row in SQL.

    The verdict per group comes from crud.event.recording_list_state itself, applied to the
    three columns it reads, so this cannot drift from the per-row badge.
    """
    from types import SimpleNamespace

    stmt = select(LiveRecording.status, LiveRecording.enforced,
                  LiveRecording.size_bytes.isnot(None), func.count(LiveRecording.id))
    if org_id:
        stmt = stmt.where(LiveRecording.org_id == org_id)
    if q:
        stmt = stmt.where(_recording_search(q))
    stmt = stmt.group_by(LiveRecording.status, LiveRecording.enforced,
                         LiveRecording.size_bytes.isnot(None))
    by_state = {k: 0 for k in RECORDING_STATES}
    by_status: dict[str, int] = {}
    total = 0
    for st, enforced, sized, n in db.execute(stmt).all():
        verdict = event_crud.recording_list_state(
            SimpleNamespace(status=st, enforced=enforced, size_bytes=0 if sized else None))
        by_state[verdict] = by_state.get(verdict, 0) + n
        by_status[st] = by_status.get(st, 0) + n
        total += n
    return {"total": total, "by_state": by_state, "by_status": by_status}


def list_recordings(db: Session, status: str | None = None, org_id=None, limit: int = 200,
                    q: str | None = None) -> list[dict]:
    """Cross-org recordings for the Media console (pages/admin/Media.jsx) — every real
    LiveRecording row, joined up to its event and org. No fabricated asset/job/processing
    pipeline: this is exactly what the platform actually captured, nothing more."""
    # Deliberately no per-row livekit.object_exists() probe here — that's a real GCS API
    # call, and this list can run to `limit` rows; recording_playback_url() below does the
    # existence-backed signed-URL lookup lazily, for one row, when an operator actually asks
    # to play it back (same one-row-at-a-time cost watch_event already pays per viewer).
    stmt = select(LiveRecording).order_by(LiveRecording.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(LiveRecording.status == status)
    if org_id:
        stmt = stmt.where(LiveRecording.org_id == org_id)
    if q:
        stmt = stmt.where(_recording_search(q))
    rows = db.scalars(stmt).all()
    out = []
    for r in rows:
        ev = db.get(Event, r.event_id)
        org = ev.organization if ev else None
        out.append({
            "id": str(r.id),
            "event_id": str(r.event_id),
            "event_title": ev.title if ev else None,
            "organization": org.name if org else None,
            "status": r.status,
            # The persisted-evidence verdict (crud.event.recording_list_state). "ready" only
            # when the provider reported a finished file; the real object check still happens
            # on playback. Never "ready" merely because file_url holds a string.
            "state": event_crud.recording_list_state(r),
            "quality": r.quality,
            "size_bytes": r.size_bytes,
            "enforced": r.enforced,
            "error": r.error,
            "started_at": r.started_at,
            "stopped_at": r.stopped_at,
            "has_file_reference": bool(r.file_url),
            # Dual-recording validation (services/validation.py) + the replay publish gate
            # (routers/events.py::watch_event) — surfaced here so pages/admin/Media.jsx can
            # show the evidence and the Publish action without a second endpoint.
            "role": r.role,
            "validation_status": r.validation_status,
            "validation_evidence": r.validation_evidence,
        })
    return out


def recording_org_id(db: Session, recording_id):
    """Owning Organization of a recording, via its event.

    ZST-EC-001 ORG-009: the support approval has to be checked against the tenant whose
    media this actually is, not against whatever organization the caller happened to name.
    """
    rec = db.get(LiveRecording, recording_id)
    if rec is None:
        return None
    event = db.get(Event, rec.event_id) if rec.event_id else None
    return event.org_id if event else None


def event_org_id(db: Session, event_id):
    """Owning Organization of an event, for the same reason as recording_org_id."""
    event = db.get(Event, event_id)
    return event.org_id if event else None


def recording_playback_url(db: Session, recording_id) -> str | None:
    r = db.get(LiveRecording, recording_id)
    if r is None or not r.file_url or not livekit.object_exists(r.file_url):
        return None
    return livekit.signed_url(r.file_url)


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
        # No "moderators" key: the role is retired and nothing assigns it. Grandfathered rows
        # are surfaced by retire_moderator_role.py, not by this console read.
        "speakers": [_member_out(u) for u in event_crud.list_assignees(db, event_id, "speaker")],
    }


# ── health vocabulary ───────────────────────────────────────────────────────────────────
#   ok             PROBED and healthy — something actually answered.
#   warn / down    PROBED and unhealthy.
#   unmonitored    configured, but nothing here checks it is working. Not an outage and not
#                  a success: an env var being set is evidence of intent, not of health.
#   not_configured not set up at all.
#
# `unmonitored` is the change that matters. LiveKit, email and storage used to report "ok"
# the moment their env var existed, and `overall` was computed over them — so "All systems
# operational" in the console header meant, in practice, "the database answered SELECT 1".
# A live-streaming platform reporting its streaming provider healthy because LIVEKIT_URL is
# a non-empty string is the fabricated green the console is meant never to show.
PROBED_STATUSES = ("ok", "warn", "down")

# The Redis probe runs on every admin page (console-state polls it) and on the org console, so
# it is bounded and cached. A timeout on an unreachable Redis would otherwise add its full
# duration to every page load.
_REDIS_PROBE_TTL = 15.0
_REDIS_PROBE_TIMEOUT = 0.5
_redis_probe_cache: dict = {"at": 0.0, "result": None}


def _probe_redis() -> dict:
    """PING the configured Redis, synchronously, bounded and cached. Never raises."""
    if not settings.REDIS_URL:
        return {"id": "redis", "name": "Realtime bus (Redis)", "status": "not_configured",
                "note": "REDIS_URL not set — realtime fan-out runs in-process on one worker"}
    now = time.monotonic()
    cached = _redis_probe_cache["result"]
    if cached is not None and now - _redis_probe_cache["at"] < _REDIS_PROBE_TTL:
        return cached
    t = time.perf_counter()
    try:
        import redis as _redis
        client = _redis.Redis.from_url(settings.REDIS_URL,
                                       socket_connect_timeout=_REDIS_PROBE_TIMEOUT,
                                       socket_timeout=_REDIS_PROBE_TIMEOUT)
        try:
            client.ping()
        finally:
            client.close()
        result = {"id": "redis", "name": "Realtime bus (Redis)", "status": "ok",
                  "latency_ms": round((time.perf_counter() - t) * 1000, 1),
                  "note": "PING answered"}
    except Exception as exc:  # noqa: BLE001 — a probe reports, it does not propagate
        # "warn", not "down": services/bus.py degrades to in-process delivery, so the platform
        # keeps working on a single worker. What stops is fan-out across workers, which is a
        # real degradation and is reported as one. The exception class only — never the URL,
        # which carries credentials.
        result = {"id": "redis", "name": "Realtime bus (Redis)", "status": "warn",
                  "latency_ms": None,
                  "note": f"Configured but unreachable ({type(exc).__name__}) — realtime "
                          "fan-out degraded to in-process"}
    _redis_probe_cache.update(at=now, result=result)
    return result


def platform_health(db: Session) -> dict:
    """Health of each dependency, saying for each whether it was PROBED or only configured.

    `overall` is computed from probed services only. `unmonitored` counts the services that are
    configured but that nothing here verifies — the console states that number next to the
    verdict so "operational" is never read as covering something that was not checked.
    """
    services = []

    t = time.perf_counter()
    try:
        db.execute(select(1))
        services.append({"id": "database", "name": "Database", "status": "ok",
                         "latency_ms": round((time.perf_counter() - t) * 1000, 1),
                         "note": "PostgreSQL answered SELECT 1"})
    except Exception:
        services.append({"id": "database", "name": "Database", "status": "down",
                         "latency_ms": None, "note": "Unreachable"})

    # Evidenced by the request itself: this response could not exist if the API were not
    # serving, and every caller of this function has already passed JWT verification to get
    # here. Stated as exactly that, rather than as an independent check.
    services.append({"id": "api", "name": "API", "status": "ok",
                     "note": "Serving this request"})
    services.append({"id": "auth", "name": "Authentication", "status": "ok",
                     "note": "Verified this request's token"})

    services.append(_probe_redis())

    from . import provider_health

    def configured_only(sid, name, present, missing_note):
        # Never probed inline: a network round trip to LiveKit, GCS or Resend would land on
        # every admin page load, because this runs inside console-state. The BACKGROUND probe
        # (services/provider_health, on the leader's metric sampler) records a verdict; this
        # reads it. Absent or stale -> "unmonitored", because an old success is not evidence
        # of current health.
        if not present:
            return {"id": sid, "name": name, "status": "not_configured", "note": missing_note}
        probe = provider_health.latest(db, sid)
        if probe is None:
            return {"id": sid, "name": name, "status": "unmonitored",
                    "note": "Configured — no recent health probe result"}
        out = {"id": sid, "name": name, "status": probe["status"],
               "latency_ms": probe.get("latency_ms"), "checked_at": probe.get("checked_at")}
        if probe["status"] == "unknown":
            # "Could not ask" is not a failure and not a success. Reported as unmonitored so
            # it cannot pull `overall` either way, with the reason stated.
            out["status"] = "unmonitored"
            out["note"] = f"Probe could not reach the provider ({probe.get('error') or 'unknown'})"
        elif probe["status"] == "ok":
            out["note"] = "Background probe answered"
        else:
            out["note"] = f"Background probe failed ({probe.get('error') or probe['status']})"
        return out

    services.append(configured_only("streaming", "Streaming (LiveKit)", bool(settings.LIVEKIT_URL),
                                    "LIVEKIT_URL not set"))
    services.append(configured_only("email", "Email", bool(settings.RESEND_API_KEY),
                                    "RESEND_API_KEY not set"))
    services.append(configured_only("storage", "Storage", livekit.gcs_configured(),
                                    "GCS_BUCKET / GCS_CREDENTIALS_PATH not set"))
    services.append({"id": "cdn", "name": "CDN", "status": "not_configured", "note": "Not yet integrated"})
    services.append({"id": "workers", "name": "Background Workers", "status": "not_configured",
                     "note": "Not yet integrated"})

    probed = [s["status"] for s in services if s["status"] in PROBED_STATUSES]
    overall = "down" if "down" in probed else "warn" if "warn" in probed else "ok"
    unmonitored = sum(1 for s in services if s["status"] == "unmonitored")
    return {"overall": overall, "services": services, "unmonitored": unmonitored}
