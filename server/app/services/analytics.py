"""Real org analytics derived from data already recorded (StreamView, Registration,
Stream) -- used by GET /dashboard/org/analytics.

Some metrics from the Analytics page's previous mock (client/src/data/analytics.js) --
watch-time hours, peak concurrent viewers, an audience-retention curve, and
device/location/traffic-source breakdowns -- aren't produced here. Those need
instrumentation this app doesn't capture yet (per-viewer session duration, live
concurrent-viewer sampling, and user-agent/referrer/geo-IP on view/registration), which
is a tracking-design decision, not just a matter of querying existing tables. Everything
below is a real count.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.registration import Registration
from app.models.stream import Stream
from app.models.view import StreamView

# range key -> (lookback window, date_trunc bucket unit)
RANGES: dict[str, tuple[int, str]] = {
    "7d": (7, "day"),
    "30d": (30, "day"),
    "90d": (90, "week"),
    "12m": (365, "month"),
}
_LABEL_FORMAT = {"day": "%b %d", "week": "%b %d", "month": "%b %Y"}


def _range_start(range_key: str) -> tuple[datetime, str]:
    days, unit = RANGES.get(range_key, RANGES["30d"])
    return datetime.now(timezone.utc) - timedelta(days=days), unit


def _trend(db: Session, model, date_col, org_id, start: datetime, unit: str) -> list[dict]:
    rows = db.execute(
        select(func.date_trunc(unit, date_col).label("bucket"), func.count(model.id))
        .join(Stream, Stream.id == model.stream_id)
        .where(Stream.org_id == org_id, date_col >= start)
        .group_by("bucket")
        .order_by("bucket")
    ).all()
    fmt = _LABEL_FORMAT[unit]
    return [{"label": bucket.strftime(fmt), "value": count} for bucket, count in rows]


def org_analytics(db: Session, org_id, range_key: str) -> dict:
    start, unit = _range_start(range_key)

    viewers = db.scalar(
        select(func.count(StreamView.id))
        .join(Stream, Stream.id == StreamView.stream_id)
        .where(Stream.org_id == org_id, StreamView.created_at >= start)
    ) or 0

    registrations = db.scalar(
        select(func.count(Registration.id))
        .join(Stream, Stream.id == Registration.stream_id)
        .where(Stream.org_id == org_id, Registration.created_at >= start)
    ) or 0

    events = db.scalar(
        select(func.count(Stream.id)).where(Stream.org_id == org_id, Stream.created_at >= start)
    ) or 0

    engagement_rate = round(min(100.0, (viewers / registrations) * 100), 1) if registrations else 0.0

    # Per-stream viewer/registration counts within the window -- outer-joined (not
    # filtered in WHERE) so an event with zero views/registrations in range still shows
    # up with 0s instead of being dropped. distinct-counting each child id avoids the
    # row-count fan-out from joining two one-to-many tables at once.
    per_stream = db.execute(
        select(
            Stream.id,
            Stream.title,
            Stream.status,
            Stream.scheduled_date,
            func.count(func.distinct(StreamView.id)).label("viewers"),
            func.count(func.distinct(Registration.id)).label("registrations"),
        )
        .outerjoin(StreamView, and_(StreamView.stream_id == Stream.id, StreamView.created_at >= start))
        .outerjoin(Registration, and_(Registration.stream_id == Stream.id, Registration.created_at >= start))
        .where(Stream.org_id == org_id, Stream.created_at >= start)
        .group_by(Stream.id)
        .order_by(Stream.created_at.desc())
    ).all()

    reports = [
        {
            "id": str(row.id),
            "title": row.title,
            "status": row.status,
            "date": row.scheduled_date.isoformat() if row.scheduled_date else None,
            "viewers": row.viewers,
            "registrations": row.registrations,
        }
        for row in per_stream
    ]
    top_events = sorted(reports, key=lambda r: r["viewers"], reverse=True)[:5]

    return {
        "range": range_key,
        "summary": {
            "viewers": viewers,
            "registrations": registrations,
            "events": events,
            "engagement_rate": engagement_rate,
        },
        "trends": {
            "viewers": _trend(db, StreamView, StreamView.created_at, org_id, start, unit),
            "registrations": _trend(db, Registration, Registration.created_at, org_id, start, unit),
        },
        "top_events": top_events,
        "reports": reports,
    }
