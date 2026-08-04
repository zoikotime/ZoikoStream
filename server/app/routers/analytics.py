"""Analytics & Business Intelligence API (/analytics/*).

Permission model, which follows the role table the dashboards were built against rather than
inventing a new one:

  host and above      the organization's analytics — every endpoint here
  moderator           /analytics/live only. "View live analytics" is on a moderator's list; the
                      organization's historical business figures are not.
  speaker             /analytics/me only — their own speaking time, questions and audience.
  attendee (viewer)   nothing. "Cannot access analytics" is explicit in the brief.

Every response carries the window it was computed over and, where relevant, an `unavailable` block
naming what this platform cannot measure and why (services/analytics.UNAVAILABLE). A dashboard that
silently omits a metric teaches its reader that the metric is zero.

Caching: the expensive roll-ups go through services.analytics.cached (60s). /live is never cached —
it is the real-time screen.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from ..crud import analytics as crud
from ..crud import media as crud_media
from ..crud.admin import create_audit_log
from ..db import get_db
from ..models import Organization, User
from ..security import get_current_user, require_min_role
from ..services import analytics as svc
from ..services import media as media_svc

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ── shared request shape ──────────────────────────────────────────────────────

class Scope:
    """The window and filters one request asked for, resolved once.

    A class rather than a dict so the cache key is built in ONE place — a hand-assembled key at each
    call site is how two endpoints end up sharing a cache entry for different filters.
    """

    __slots__ = ("org", "period", "start", "end", "bucket", "tz", "filters")

    def __init__(self, org, period, start, end, bucket, tz, filters):
        self.org, self.period = org, period
        self.start, self.end, self.bucket = start, end, bucket
        self.tz, self.filters = tz, filters

    def key(self, name: str) -> tuple:
        """Cache key. org_id FIRST so `invalidate(org_id)` can find every entry for one tenant."""
        return (str(self.org.id), name, self.period, self.start.isoformat(), self.end.isoformat(),
                self.bucket, self.tz, tuple(sorted(
                    (k, tuple(v) if isinstance(v, list) else str(v))
                    for k, v in self.filters.items() if v is not None)))

    def window(self) -> dict:
        return {"period": self.period, "label": svc.label_for(self.period, self.start, self.end),
                "start": self.start.isoformat(), "end": self.end.isoformat(),
                "bucket": self.bucket, "timezone": self.tz}


def _scope(
    period: str = Query("custom", description=f"one of {svc.PERIODS}"),
    start: datetime | None = Query(None, description="custom range start (ISO)"),
    end: datetime | None = Query(None, description="custom range end (ISO)"),
    tz: str = Query("UTC", description="IANA timezone for bucketing, e.g. Asia/Kolkata"),
    event_id: uuid.UUID | None = Query(None),
    host_id: uuid.UUID | None = Query(None),
    speaker_id: uuid.UUID | None = Query(None),
    department: str | None = Query(None, max_length=80),
    category: str | None = Query(None, max_length=40),
    location: str | None = Query(None, max_length=200),
    visibility: str | None = Query(None, max_length=20),
    event_status: str | None = Query(None, max_length=20),
    tag: list[str] | None = Query(None),
    user: User = Depends(require_min_role("host")),
    db: Session = Depends(get_db),
) -> Scope:
    """Resolve the window, the timezone and the filters for any org-level analytics request."""
    if period not in svc.PERIODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unknown period. Expected one of {svc.PERIODS}")
    if not user.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your account is not in an organization")
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your organization no longer exists")

    # Validated against the real tz database before it reaches SQL. An unknown name would otherwise
    # surface as a Postgres error on every chart, and the org's own timezone is the sensible default
    # rather than UTC for an organizer who set one.
    zone = (tz or "").strip() or org.timezone or "UTC"
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown timezone: {zone}")

    start = _aware(start)
    end = _aware(end)
    win_start, win_end, bucket = svc.period_window(period, start=start, end=end)
    return Scope(org, period, win_start, win_end, bucket, zone, {
        "event_id": event_id, "host_id": host_id, "speaker_id": speaker_id,
        "department": department, "category": category, "location": location,
        "visibility": visibility, "status": event_status, "tags": tag or [],
    })


def _aware(value: datetime | None) -> datetime | None:
    """Treat a naive timestamp as UTC. Comparing naive to aware raises in Python and would 500."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── organization ──────────────────────────────────────────────────────────────

@router.get("/overview")
def overview(scope: Scope = Depends(_scope), db: Session = Depends(get_db)):
    """Every headline figure for the window, plus growth against the comparable previous period."""
    data = svc.cached(scope.key("overview"), lambda: crud.organization_overview(
        db, scope.org.id, scope.start, scope.end, scope.filters))
    return {"window": scope.window(), **data, "unavailable": svc.UNAVAILABLE}


@router.get("/trends")
def trends(scope: Scope = Depends(_scope), db: Session = Depends(get_db)):
    """Time series for the charts, bucketed in the requested timezone."""
    series = svc.cached(scope.key("trends"), lambda: crud.trend_series(
        db, scope.org.id, scope.start, scope.end,
        bucket=scope.bucket, timezone_name=scope.tz, filters=scope.filters))
    return {"window": scope.window(), "series": series}


@router.get("/events")
def events(sort: str = Query("attended"), limit: int = Query(crud.MAX_EVENT_ROWS, ge=1,
                                                             le=crud.MAX_EVENT_ROWS),
           scope: Scope = Depends(_scope), db: Session = Depends(get_db)):
    """Per-event performance — the reports table and the Top Events ranking."""
    rows = svc.cached(scope.key(f"events:{sort}:{limit}"), lambda: crud.event_table(
        db, scope.org.id, scope.start, scope.end, scope.filters, sort=sort, limit=limit))
    return {"window": scope.window(), "events": rows, "count": len(rows)}


@router.get("/events/{event_id}")
def event_detail(event_id: uuid.UUID, user: User = Depends(require_min_role("host")),
                 db: Session = Depends(get_db)):
    """One event's deep dive: retention curve, engagement timeline, funnel and hour-of-day heatmap.

    Not cached and not windowed — it is one event's whole history, and it is read after clicking a
    row rather than on a dashboard that refreshes.
    """
    if not user.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your account is not in an organization")
    data = crud.event_detail(db, user.org_id, event_id)
    if data is None:
        # 404 for a foreign event, never 403 — a 403 confirms another tenant's event exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return {**data, "unavailable": svc.UNAVAILABLE}


@router.get("/engagement")
def engagement(scope: Scope = Depends(_scope), db: Session = Depends(get_db)):
    """Chat, Q&A, polls, reactions, hands and participation, with the score's components exposed
    so a reader can form their own view rather than trusting one composite number."""
    def build():
        ids = crud._event_ids(db, scope.org.id, scope.start, scope.end, scope.filters)
        totals = crud.engagement_totals(db, scope.org.id, ids)
        live = crud._live_aggregates(db, scope.org.id, ids)
        return {
            **totals,
            "peak_viewers": live["peak_viewers"],
            "engagement_score": crud.engagement_score(totals, live["peak_viewers"]),
            "score_formula": ("100 * (messages + 2*questions + 3*poll_votes + reactions) "
                              "/ (5 * peak_viewers), capped at 100 — a heuristic, not a "
                              "measurement."),
        }

    return {"window": scope.window(), **svc.cached(scope.key("engagement"), build)}


@router.get("/speakers")
def speakers(scope: Scope = Depends(_scope), db: Session = Depends(get_db)):
    """Per-speaker figures. `speaking_seconds` is null where no broadcast ever ended — see
    crud/analytics.speaker_stats for why that is not zero."""
    data = svc.cached(scope.key("speakers"), lambda: crud.speaker_stats(
        db, scope.org.id, scope.start, scope.end, scope.filters))
    return {"window": scope.window(), **data}


@router.get("/attendees")
def attendees(limit: int = Query(crud.MAX_PEOPLE_ROWS, ge=1, le=crud.MAX_PEOPLE_ROWS),
              scope: Scope = Depends(_scope), db: Session = Depends(get_db)):
    """Per-attendee figures, ranked by watch time."""
    data = svc.cached(scope.key(f"attendees:{limit}"), lambda: crud.attendee_stats(
        db, scope.org.id, scope.start, scope.end, scope.filters, limit=limit))
    return {"window": scope.window(), **data}


@router.get("/heatmap")
def heatmap(scope: Scope = Depends(_scope), db: Session = Depends(get_db)):
    """Audience by weekday × hour — when people actually watched, which is a different question
    from when events were scheduled. Weekday 0 is Monday."""
    cells = svc.cached(scope.key("heatmap"), lambda: crud.org_heatmap(
        db, scope.org.id, scope.start, scope.end, scope.filters, timezone_name=scope.tz))
    return {"window": scope.window(), "cells": cells,
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}


@router.get("/filters")
def filters(user: User = Depends(require_min_role("host")), db: Session = Depends(get_db)):
    """The filter values that actually occur, so no filter can produce an empty result."""
    if not user.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your account is not in an organization")
    return crud.filter_options(db, user.org_id)


# ── live ──────────────────────────────────────────────────────────────────────

@router.get("/live")
def live(user: User = Depends(require_min_role("moderator")), db: Session = Depends(get_db)):
    """Real-time dashboard across every on-air event.

    Moderator and above: watching the room is a moderator's job. Never cached, and the payload
    states its own staleness — these are the sampler's figures, so they are up to one sample
    interval old. Live encoder telemetry (bitrate, packet loss, RTT) travels on the event socket to
    the host console and is not persisted; `unavailable` says so rather than showing zeros.
    """
    if not user.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your account is not in an organization")
    return crud.live_now(db, user.org_id)


# ── the speaker's own view ────────────────────────────────────────────────────

@router.get("/me")
def my_analytics(period: str = Query("yearly"), user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """One person's own figures, for the speaker console.

    Any staff role reaches this, because it discloses nothing about anybody else: the speaker
    filter is pinned to the CALLER, so a speaker cannot widen it to a colleague by editing a query
    parameter. Attendees (viewer role) get a 403 — their own participation is on their dashboard,
    not here.
    """
    if user.role == "viewer":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Attendee participation is on your own dashboard")
    if not user.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your account is not in an organization")
    if period not in svc.PERIODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Expected one of {svc.PERIODS}")
    start, end, _bucket = svc.period_window(period)
    data = crud.speaker_stats(db, user.org_id, start, end, {"speaker_id": user.id})
    mine = next((s for s in data["speakers"] if s["id"] == str(user.id)), None)
    if mine is None:
        # Also try the host role — a host presenting their own event is a speaker for this purpose.
        data = crud.speaker_stats(db, user.org_id, start, end, {"host_id": user.id})
        mine = next((s for s in data["speakers"] if s["id"] == str(user.id)), None)
    return {
        "window": {"period": period, "start": start.isoformat(), "end": end.isoformat()},
        "me": mine or {"id": str(user.id), "name": user.full_name, "events": 0,
                       "speaking_seconds": None, "questions_answered": 0},
        "unavailable": data["unavailable"],
    }


# ── alerts ────────────────────────────────────────────────────────────────────

@router.get("/alerts")
def alerts(user: User = Depends(require_min_role("host")), db: Session = Depends(get_db)):
    """The organization's operational alert feed: storage, failed recordings, live events.

    Derived on read. There is no scheduler or notification table in this deployment, so a stored
    "report ready" notification would be one nobody sent — the feed reports what is true now, and
    says as much.
    """
    if not user.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your account is not in an organization")
    org = db.get(Organization, user.org_id)
    policy = media_svc.policy_for(org)
    storage_stats = crud_media.storage_stats(db, org.id, org.storage_quota_gb)
    totals = crud_media.library_totals(db, org.id)
    live = crud.live_now(db, org.id)
    return {
        "alerts": svc.alerts(storage_stats=storage_stats, policy=policy,
                             failed_recordings=totals["failed"],
                             live_events=live["totals"]["live_events"]),
        "storage": storage_stats,
    }


# ── reports ───────────────────────────────────────────────────────────────────

@router.get("/report")
def report(scope: Scope = Depends(_scope), db: Session = Depends(get_db)):
    """A whole report in one request: headline figures, trends, the event table and the comparison.

    One request rather than four because a report is a single artefact — it is printed, exported and
    read as a unit, and four round trips could each land in a different cache generation and produce
    a document whose totals do not add up.
    """
    prev_start, prev_end = svc.previous_window(scope.start, scope.end, scope.period)

    def build():
        return {
            "overview": crud.organization_overview(db, scope.org.id, scope.start, scope.end,
                                                   scope.filters),
            "trends": crud.trend_series(db, scope.org.id, scope.start, scope.end,
                                        bucket=scope.bucket, timezone_name=scope.tz,
                                        filters=scope.filters),
            "events": crud.event_table(db, scope.org.id, scope.start, scope.end, scope.filters,
                                       sort="attended", limit=100),
            "comparison": crud.organization_overview(db, scope.org.id, prev_start, prev_end,
                                                     scope.filters)["events"],
        }

    data = svc.cached(scope.key("report"), build)
    return {
        "window": scope.window(),
        "previous_window": {"start": prev_start.isoformat(), "end": prev_end.isoformat(),
                            "label": svc.label_for(scope.period, prev_start, prev_end)},
        "organization": {"id": str(scope.org.id), "name": scope.org.name},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **data,
        "unavailable": svc.UNAVAILABLE,
        "pdf": svc.PDF_NOTE,
    }


# ── export ────────────────────────────────────────────────────────────────────

_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}


@router.get("/export")
def export(
    dataset: str = Query("events", description=f"one of {tuple(svc.EXPORT_COLUMNS)}"),
    fmt: str = Query("csv", alias="format", description=f"one of {svc.EXPORT_FORMATS}"),
    request: Request = None,
    scope: Scope = Depends(_scope),
    db: Session = Depends(get_db),
):
    """Download one dataset as CSV, Excel or JSON.

    Audited: an export is a bulk read of the organization's attendee and speaker records leaving the
    platform, which is exactly the event a compliance reviewer asks about later.

    PDF is not offered here — see services/analytics.PDF_NOTE. The report view prints to PDF through
    the browser, which lays out the charts the page already renders.
    """
    if dataset not in svc.EXPORT_COLUMNS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unknown dataset. Expected one of {tuple(svc.EXPORT_COLUMNS)}")
    if fmt not in svc.EXPORT_FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unknown format. Expected one of {svc.EXPORT_FORMATS}. {svc.PDF_NOTE}")

    rows = _dataset_rows(db, scope, dataset)
    columns = svc.EXPORT_COLUMNS[dataset]
    stem = f"zoikostream-{dataset}-{svc.label_for(scope.period, scope.start, scope.end)}"

    create_audit_log(
        db, actor=None, action=f"analytics.export.{dataset}", target_type="organization",
        target_id=scope.org.id, org_id=scope.org.id,
        ip=(request.client.host if request and request.client else None),
        meta={"format": fmt, "rows": len(rows), "window": scope.window()},
    )
    db.commit()

    if fmt == "json":
        return {"window": scope.window(), "dataset": dataset, "rows": rows,
                "unavailable": svc.UNAVAILABLE}
    body = (svc.to_csv(columns, rows) if fmt == "csv"
            else svc.to_xlsx(columns, rows, sheet=dataset.title()))
    return Response(
        content=body,
        media_type=_MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{svc.safe_filename(stem, fmt)}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _dataset_rows(db: Session, scope: Scope, dataset: str) -> list[dict]:
    """The rows behind each export scope. Reuses the same functions the screens read, so an export
    can never disagree with the dashboard it was taken from."""
    if dataset == "events":
        return crud.event_table(db, scope.org.id, scope.start, scope.end, scope.filters,
                                sort="attended", limit=crud.MAX_EVENT_ROWS)
    if dataset == "speakers":
        return crud.speaker_stats(db, scope.org.id, scope.start, scope.end,
                                  scope.filters)["speakers"]
    if dataset == "attendees":
        return crud.attendee_stats(db, scope.org.id, scope.start, scope.end,
                                   scope.filters)["attendees"]
    if dataset == "trends":
        return crud.trend_series(db, scope.org.id, scope.start, scope.end, bucket=scope.bucket,
                                 timezone_name=scope.tz, filters=scope.filters)
    if dataset == "recordings":
        return crud_media.library(db, scope.org.id, scope="all", sort="newest",
                                  page_size=crud_media.MAX_PAGE_SIZE)["items"]
    return []
