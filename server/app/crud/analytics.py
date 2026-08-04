"""Analytics queries. Every number here comes from a row somebody's action wrote.

Sources, so the provenance of each figure is inspectable rather than folklore:

  events                 lifecycle counts, categories, tags, schedule
  event_registrations    registrations, attendance, watch seconds, join counts   (crud/attendee.py)
  analytics_snapshots    the 15-second timeseries: viewers, participants, messages, questions,
                         reactions, hands, lobby depth                            (broadcast sampler)
  broadcast_sessions     go-live windows, paused time, peak viewers
  live_messages/_questions/_polls/_announcements   engagement, Q&A outcomes, poll votes
  live_recordings        replay views, downloads, stored duration and size
  event_assignments      who filled which role, and durable speaking_ms
  users                  actor identity and department (the brief's Department filter)

Two rules, both load-bearing:

  * ORG-SCOPED, ALWAYS. Every statement filters `org_id`. Analytics is the easiest place in a
    multi-tenant system to leak, because an aggregate looks harmless — a COUNT across tenants is
    still a disclosure about another tenant's business.
  * SET-BASED, NEVER PER-ROW. A dashboard covering a year of events must not issue a query per
    event. Everything below is one statement per metric family, grouped in SQL. The audit already
    flagged this shape once (services/admin._streaming_hours); it is not repeated here.

What is NOT here, and why, is recorded in services/analytics.UNAVAILABLE — viewer geography, per
attendee reactions, bandwidth metering and historical encoder telemetry all have no source in this
stack, and are reported as unavailable with a reason instead of being estimated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, Integer, and_, case, cast, distinct, func, select

from ..models import (
    AnalyticsSnapshot,
    BroadcastSession,
    Event,
    EventAssignment,
    EventRegistration,
    LiveAnnouncement,
    LiveMessage,
    LivePoll,
    LiveQuestion,
    LiveRecording,
    MediaMark,
    User,
)

# Statuses that count as "an event that happened or will happen". A draft is not a business fact.
COUNTED_STATUSES = ("published", "scheduled", "live", "paused", "ended", "cancelled")
# Bucket sizes for trends, mapped to Postgres date_trunc units.
BUCKETS = {"hour": "hour", "day": "day", "week": "week", "month": "month", "quarter": "quarter",
           "year": "year"}
# Retention curve resolution: percentage of event progress. 21 points is every 5%.
RETENTION_STEPS = 20
# Table caps. A year of weekly events is ~52 rows; these are the ceilings that keep one request
# from serialising an entire tenant.
MAX_EVENT_ROWS = 500
MAX_PEOPLE_ROWS = 500


def _now():
    return datetime.now(timezone.utc)


def _window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """Default to the last 30 days, and never let end precede start."""
    end = end or _now()
    start = start or (end - timedelta(days=30))
    return (start, end) if start <= end else (end, start)


def _event_scope(org_id, start, end, filters: dict | None = None):
    """The WHERE clause every org-level metric shares.

    Events are placed in the window by `start_time`, falling back to `created_at` for an event
    that was never scheduled — otherwise an unscheduled event is invisible to every report.
    """
    filters = filters or {}
    when = func.coalesce(Event.start_time, Event.created_at)
    clauses = [
        Event.org_id == org_id,
        Event.deleted_at.is_(None),
        Event.status.in_(COUNTED_STATUSES),
        when >= start,
        when <= end,
    ]
    if filters.get("event_id"):
        clauses.append(Event.id == filters["event_id"])
    if filters.get("category"):
        clauses.append(Event.category == filters["category"])
    if filters.get("location"):
        clauses.append(func.lower(Event.location).like(f"%{str(filters['location']).lower()}%"))
    if filters.get("visibility"):
        clauses.append(Event.visibility == filters["visibility"])
    if filters.get("status"):
        clauses.append(Event.status == filters["status"])
    for tag in (filters.get("tags") or [])[:8]:
        clauses.append(Event.tags.contains([tag]))
    if filters.get("host_id") or filters.get("speaker_id") or filters.get("department"):
        # Host / speaker / department all narrow by WHO was on the event, which lives in
        # event_assignments. An EXISTS keeps it a single statement and cannot multiply rows the way
        # a join to a many-side would — an event with three speakers must still count once.
        sub = select(EventAssignment.id).where(EventAssignment.event_id == Event.id)
        if filters.get("host_id"):
            sub = sub.where(EventAssignment.user_id == filters["host_id"],
                            EventAssignment.role == "host")
        if filters.get("speaker_id"):
            sub = sub.where(EventAssignment.user_id == filters["speaker_id"],
                            EventAssignment.role == "speaker")
        if filters.get("department"):
            sub = sub.join(User, User.id == EventAssignment.user_id).where(
                User.department == filters["department"])
        clauses.append(sub.exists())
    return clauses


def _event_ids(db, org_id, start, end, filters=None, *, limit: int = MAX_EVENT_ROWS) -> list[uuid.UUID]:
    """The events in scope. Resolved ONCE and reused, so every metric below aggregates over
    exactly the same population — a second evaluation of the filter could drift as rows change."""
    return list(db.scalars(
        select(Event.id).where(*_event_scope(org_id, start, end, filters))
        .order_by(func.coalesce(Event.start_time, Event.created_at).desc()).limit(limit)
    ).all())


# ── organization overview ─────────────────────────────────────────────────────

def organization_overview(db, org_id, start=None, end=None, filters=None) -> dict:
    """Every headline figure, with the previous period alongside it for growth.

    Five statements total for ~20 metrics. `growth` compares to the immediately preceding window
    of the same length, which is what "up 12%" has to mean to be checkable.
    """
    start, end = _window(start, end)
    ids = _event_ids(db, org_id, start, end, filters)

    lifecycle = _lifecycle_counts(db, org_id, start, end, filters)
    audience = _audience_counts(db, org_id, ids)
    live = _live_aggregates(db, org_id, ids)
    engagement = engagement_totals(db, org_id, ids)
    replay = _replay_totals(db, org_id, ids)

    registrations, attended = audience["registrations"], audience["attended"]
    peak = live["peak_viewers"]
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "days": max(1, (end - start).days)},
        "events": lifecycle,
        "audience": {
            **audience,
            # Rate is None, not 0, when nobody registered — 0% attendance of nobody is not a fact.
            "attendance_rate": round(attended * 100 / registrations, 1) if registrations else None,
            "peak_concurrent": peak,
        },
        "live": live,
        "engagement": {
            **engagement,
            "engagement_score": engagement_score(engagement, peak),
            "participation_rate": (
                round(engagement["participants"] * 100 / attended, 1) if attended else None
            ),
        },
        "replay": replay,
        "growth": _growth(db, org_id, start, end, filters),
    }


def _lifecycle_counts(db, org_id, start, end, filters) -> dict:
    """One pass over events for every status bucket."""
    row = db.execute(
        select(
            func.count(),
            func.count(case((Event.status.in_(("live", "paused")), 1))),
            func.count(case((Event.status == "ended", 1))),
            func.count(case((Event.status == "cancelled", 1))),
            func.count(case((Event.status.in_(("published", "scheduled")), 1))),
            func.count(case((Event.registration_required.is_(True), 1))),
        ).where(*_event_scope(org_id, start, end, filters))
    ).one()
    total, live, completed, cancelled, upcoming, gated = row
    return {"total": int(total), "live": int(live), "completed": int(completed),
            "cancelled": int(cancelled), "upcoming": int(upcoming),
            "registration_required": int(gated),
            "completion_rate": round(int(completed) * 100 / int(total), 1) if total else None}


def _audience_counts(db, org_id, ids) -> dict:
    """Registrations, attendance and watch time. Distinct PEOPLE as well as row counts, because
    "500 registrations" across 40 events may be 30 people."""
    if not ids:
        return {"registrations": 0, "attended": 0, "unique_attendees": 0, "unique_registrants": 0,
                "cancelled": 0, "total_watch_seconds": 0, "avg_watch_seconds": None,
                "avg_sessions_per_attendee": None, "bookmarks": 0}
    reg = EventRegistration
    row = db.execute(
        select(
            func.count(case((reg.status.in_(("registered", "attended")), 1))),
            func.count(case((reg.status == "attended", 1))),
            func.count(distinct(case((reg.status == "attended", reg.user_id)))),
            func.count(distinct(case((reg.status.in_(("registered", "attended")), reg.user_id)))),
            func.count(case((reg.status == "cancelled", 1))),
            func.coalesce(func.sum(reg.watch_seconds), 0),
            func.coalesce(func.sum(reg.join_count), 0),
            func.count(case((reg.bookmarked.is_(True), 1))),
        ).where(reg.org_id == org_id, reg.event_id.in_(ids))
    ).one()
    registrations, attended, uniq_att, uniq_reg, cancelled, watch, joins, bookmarks = row
    return {
        "registrations": int(registrations),
        "attended": int(attended),
        "unique_attendees": int(uniq_att),
        "unique_registrants": int(uniq_reg),
        "cancelled": int(cancelled),
        "total_watch_seconds": int(watch),
        "avg_watch_seconds": int(int(watch) / int(attended)) if attended else None,
        "avg_sessions_per_attendee": round(int(joins) / int(uniq_att), 1) if uniq_att else None,
        "bookmarks": int(bookmarks),
    }


def _live_aggregates(db, org_id, ids) -> dict:
    """Peak concurrency and broadcast duration, from the sampler and the session rows.

    Peak is MAX over samples AND over broadcast_sessions.peak_viewers: the sampler runs every 15
    seconds and a genuine spike between two samples is only recorded on the session row.
    """
    if not ids:
        return {"peak_viewers": 0, "avg_concurrent": None, "samples": 0,
                "broadcast_seconds": 0, "avg_broadcast_seconds": None, "broadcasts": 0,
                "paused_seconds": 0}
    sample_peak, avg_viewers, samples = db.execute(
        select(func.coalesce(func.max(AnalyticsSnapshot.viewers), 0),
               func.avg(cast(AnalyticsSnapshot.viewers, Float)),
               func.count())
        .where(AnalyticsSnapshot.org_id == org_id, AnalyticsSnapshot.event_id.in_(ids))
    ).one()
    session_peak, total_ms, paused_ms, broadcasts = db.execute(
        select(
            func.coalesce(func.max(BroadcastSession.peak_viewers), 0),
            func.coalesce(func.sum(
                func.extract("epoch", BroadcastSession.ended_at - BroadcastSession.started_at) * 1000
                - BroadcastSession.paused_ms), 0),
            func.coalesce(func.sum(BroadcastSession.paused_ms), 0),
            func.count(case((BroadcastSession.started_at.isnot(None), 1))),
        ).where(BroadcastSession.org_id == org_id, BroadcastSession.event_id.in_(ids),
                BroadcastSession.ended_at.isnot(None))
    ).one()
    seconds = max(0, int(float(total_ms or 0) / 1000))
    return {
        "peak_viewers": max(int(sample_peak), int(session_peak)),
        "avg_concurrent": round(float(avg_viewers), 1) if avg_viewers is not None else None,
        "samples": int(samples),
        "broadcast_seconds": seconds,
        "avg_broadcast_seconds": int(seconds / int(broadcasts)) if broadcasts else None,
        "broadcasts": int(broadcasts),
        "paused_seconds": int(int(paused_ms) / 1000),
    }


def engagement_totals(db, org_id, ids) -> dict:
    """Messages, questions, poll votes, reactions, hands and how many DISTINCT people interacted.

    Reactions and hands come from the analytics samples rather than a per-event table because
    reactions are deliberately not persisted per tap (see services/attendee.py) — the sampler's
    aggregate is the durable record. Hands is a MAX, not a SUM: it is a concurrent depth, and
    adding it across samples would count one raised hand once per 15 seconds.
    """
    if not ids:
        return {"messages": 0, "questions": 0, "questions_answered": 0, "polls": 0,
                "poll_votes": 0, "reactions": 0, "hands": 0, "announcements": 0,
                "participants": 0, "avg_interactions_per_attendee": None}
    messages, chat_people = db.execute(
        select(func.count(), func.count(distinct(LiveMessage.user_id)))
        .where(LiveMessage.org_id == org_id, LiveMessage.event_id.in_(ids),
               LiveMessage.deleted_at.is_(None))
    ).one()
    questions, answered, qa_people = db.execute(
        select(func.count(),
               func.count(case((LiveQuestion.status == "answered", 1))),
               func.count(distinct(LiveQuestion.user_id)))
        .where(LiveQuestion.org_id == org_id, LiveQuestion.event_id.in_(ids))
    ).one()
    polls = list(db.scalars(
        select(LivePoll.options).where(LivePoll.org_id == org_id, LivePoll.event_id.in_(ids))
    ).all())
    poll_votes = sum(int(o.get("votes") or 0) for opts in polls
                     for o in (opts or []) if isinstance(o, dict))
    reactions, hands = db.execute(
        select(func.coalesce(func.sum(AnalyticsSnapshot.reactions), 0),
               func.coalesce(func.max(AnalyticsSnapshot.hands), 0))
        .where(AnalyticsSnapshot.org_id == org_id, AnalyticsSnapshot.event_id.in_(ids))
    ).one()
    announcements = db.scalar(
        select(func.count()).select_from(LiveAnnouncement)
        .where(LiveAnnouncement.org_id == org_id, LiveAnnouncement.event_id.in_(ids),
               LiveAnnouncement.sent_at.isnot(None))) or 0

    # Distinct interacting people across chat and Q&A. A union of two DISTINCT sets, computed in
    # SQL so somebody who did both is counted once.
    participants = db.scalar(
        select(func.count()).select_from(
            select(LiveMessage.user_id.label("uid")).where(
                LiveMessage.org_id == org_id, LiveMessage.event_id.in_(ids),
                LiveMessage.user_id.isnot(None), LiveMessage.deleted_at.is_(None))
            .union(select(LiveQuestion.user_id.label("uid")).where(
                LiveQuestion.org_id == org_id, LiveQuestion.event_id.in_(ids),
                LiveQuestion.user_id.isnot(None)))
            .subquery())
    ) or 0

    total_interactions = int(messages) + int(questions) + poll_votes + int(reactions)
    return {
        "messages": int(messages),
        "questions": int(questions),
        "questions_answered": int(answered),
        "answer_rate": round(int(answered) * 100 / int(questions), 1) if questions else None,
        "polls": len(polls),
        "poll_votes": poll_votes,
        "reactions": int(reactions),
        "hands": int(hands),
        "announcements": int(announcements),
        "participants": int(participants),
        "chat_participants": int(chat_people),
        "qa_participants": int(qa_people),
        "total_interactions": total_interactions,
        "avg_interactions_per_attendee": (
            round(total_interactions / int(participants), 1) if participants else None
        ),
    }


def _replay_totals(db, org_id, ids) -> dict:
    """Recording and replay figures — the Media Library's contribution to the analytics picture."""
    if not ids:
        return {"recordings": 0, "views": 0, "downloads": 0, "duration_ms": 0, "size_bytes": 0,
                "with_transcript": 0, "failed": 0}
    row = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(LiveRecording.view_count), 0),
            func.coalesce(func.sum(LiveRecording.download_count), 0),
            func.coalesce(func.sum(LiveRecording.duration_ms), 0),
            func.coalesce(func.sum(LiveRecording.size_bytes), 0),
            func.count(case((LiveRecording.transcript.isnot(None), 1))),
            func.count(case((LiveRecording.status == "failed", 1))),
        ).where(LiveRecording.org_id == org_id, LiveRecording.event_id.in_(ids),
                LiveRecording.deleted_at.is_(None))
    ).one()
    count, views, downloads, duration, size, transcripts, failed = row
    return {"recordings": int(count), "views": int(views), "downloads": int(downloads),
            "duration_ms": int(duration), "size_bytes": int(size),
            "with_transcript": int(transcripts), "failed": int(failed)}


def engagement_score(engagement: dict, peak: int) -> int:
    """Weighted interactions per viewer, capped at 100.

    score = 100 * (messages + 2*questions + 3*poll_votes + reactions) / (5 * peak_viewers)

    Identical to services/broadcast.engagement_score so the live console and the historical report
    cannot disagree about the same event. It is a HEURISTIC, written down so nobody mistakes it for
    a measurement, and the components are all returned alongside it so a reader can form their own.
    """
    if peak <= 0:
        return 0
    weighted = (engagement.get("messages", 0) + 2 * engagement.get("questions", 0)
                + 3 * engagement.get("poll_votes", 0) + engagement.get("reactions", 0))
    return max(0, min(100, round(100 * weighted / (5 * peak))))


def _growth(db, org_id, start, end, filters) -> dict:
    """This window against the one immediately before it, same length.

    Percentages are None rather than 0 when the previous period was empty: "infinite growth from
    zero" is not a useful number, and 0% would be false.
    """
    span = end - start
    prev_start, prev_end = start - span, start
    prev_ids = _event_ids(db, org_id, prev_start, prev_end, filters)
    prev_lifecycle = _lifecycle_counts(db, org_id, prev_start, prev_end, filters)
    prev_audience = _audience_counts(db, org_id, prev_ids)
    prev_live = _live_aggregates(db, org_id, prev_ids)

    ids = _event_ids(db, org_id, start, end, filters)
    now_lifecycle = _lifecycle_counts(db, org_id, start, end, filters)
    now_audience = _audience_counts(db, org_id, ids)
    now_live = _live_aggregates(db, org_id, ids)

    def delta(current, previous):
        if not previous:
            return None
        return round((current - previous) * 100 / previous, 1)

    return {
        "previous_window": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "events": delta(now_lifecycle["total"], prev_lifecycle["total"]),
        "registrations": delta(now_audience["registrations"], prev_audience["registrations"]),
        "attended": delta(now_audience["attended"], prev_audience["attended"]),
        "watch_seconds": delta(now_audience["total_watch_seconds"],
                               prev_audience["total_watch_seconds"]),
        "peak_viewers": delta(now_live["peak_viewers"], prev_live["peak_viewers"]),
        "previous": {"events": prev_lifecycle["total"],
                     "registrations": prev_audience["registrations"],
                     "attended": prev_audience["attended"],
                     "watch_seconds": prev_audience["total_watch_seconds"],
                     "peak_viewers": prev_live["peak_viewers"]},
    }


# ── trends ────────────────────────────────────────────────────────────────────

def trend_series(db, org_id, start=None, end=None, *, bucket: str = "day",
                 timezone_name: str = "UTC", filters=None) -> list[dict]:
    """Time series for the charts, bucketed in SQL.

    `date_trunc` runs in the requested TIMEZONE, not UTC: an organizer in Kolkata expects
    "Tuesday" to mean their Tuesday, and bucketing in UTC silently moves every evening event to
    the following day. Postgres does this with `timezone(tz, ts)`; doing it in Python would mean
    fetching every row.

    Empty buckets are filled in by the caller-facing shape below, so a chart with a quiet week
    shows a gap at zero rather than joining two distant points into a false trend line.
    """
    start, end = _window(start, end)
    unit = BUCKETS.get(bucket, "day")
    tz = timezone_name or "UTC"
    ids = _event_ids(db, org_id, start, end, filters)

    when = func.coalesce(Event.start_time, Event.created_at)
    period = func.date_trunc(unit, func.timezone(tz, when)).label("period")
    events = db.execute(
        select(period, func.count(),
               func.count(case((Event.status == "ended", 1))),
               func.count(case((Event.status == "cancelled", 1))))
        .where(*_event_scope(org_id, start, end, filters)).group_by(period).order_by(period)
    ).all()

    reg = EventRegistration
    reg_period = func.date_trunc(unit, func.timezone(tz, reg.created_at)).label("period")
    registrations = db.execute(
        select(reg_period,
               func.count(case((reg.status.in_(("registered", "attended")), 1))),
               func.count(case((reg.status == "attended", 1))),
               func.coalesce(func.sum(reg.watch_seconds), 0))
        .where(reg.org_id == org_id, reg.event_id.in_(ids) if ids else False)
        .group_by(reg_period).order_by(reg_period)
    ).all() if ids else []

    snap = AnalyticsSnapshot
    snap_period = func.date_trunc(unit, func.timezone(tz, snap.created_at)).label("period")
    live = db.execute(
        select(snap_period, func.coalesce(func.max(snap.viewers), 0),
               func.coalesce(func.sum(snap.messages), 0),
               func.coalesce(func.sum(snap.reactions), 0))
        .where(snap.org_id == org_id, snap.event_id.in_(ids) if ids else False)
        .group_by(snap_period).order_by(snap_period)
    ).all() if ids else []

    merged: dict[str, dict] = {}

    def slot(period):
        key = period.isoformat() if hasattr(period, "isoformat") else str(period)
        return merged.setdefault(key, {
            "period": key, "events": 0, "completed": 0, "cancelled": 0,
            "registrations": 0, "attended": 0, "watch_seconds": 0,
            "peak_viewers": 0, "messages": 0, "reactions": 0,
        })

    for period, total, completed, cancelled in events:
        row = slot(period)
        row.update(events=int(total), completed=int(completed), cancelled=int(cancelled))
    for period, registered, attended, watch in registrations:
        row = slot(period)
        row.update(registrations=int(registered), attended=int(attended), watch_seconds=int(watch))
    for period, peak, messages, reactions in live:
        row = slot(period)
        row.update(peak_viewers=int(peak), messages=int(messages), reactions=int(reactions))

    return [merged[k] for k in sorted(merged)]


# ── per-event ─────────────────────────────────────────────────────────────────

def event_table(db, org_id, start=None, end=None, filters=None, *, sort: str = "attended",
                limit: int = MAX_EVENT_ROWS) -> list[dict]:
    """One row per event with its own figures — the reports table and the Top Events chart.

    Four grouped queries, then a join in Python on event id. That is deliberate: one SQL statement
    joining registrations AND snapshots AND messages to events would multiply rows across three
    many-sides and need DISTINCT gymnastics to un-multiply. Four GROUP BYs over an indexed
    `event_id IN (…)` is both faster and legible.
    """
    start, end = _window(start, end)
    rows = db.execute(
        select(Event).where(*_event_scope(org_id, start, end, filters))
        .order_by(func.coalesce(Event.start_time, Event.created_at).desc()).limit(limit)
    ).scalars().all()
    ids = [e.id for e in rows]
    if not ids:
        return []

    reg = EventRegistration
    by_event = {
        eid: {"registrations": int(r), "attended": int(a), "watch_seconds": int(w),
              "unique": int(u)}
        for eid, r, a, w, u in db.execute(
            select(reg.event_id,
                   func.count(case((reg.status.in_(("registered", "attended")), 1))),
                   func.count(case((reg.status == "attended", 1))),
                   func.coalesce(func.sum(reg.watch_seconds), 0),
                   func.count(distinct(reg.user_id)))
            .where(reg.org_id == org_id, reg.event_id.in_(ids)).group_by(reg.event_id)
        ).all()
    }
    snaps = {
        eid: {"peak": int(p), "avg": round(float(av or 0), 1),
              "messages": int(m), "reactions": int(rx), "hands": int(h)}
        for eid, p, av, m, rx, h in db.execute(
            select(AnalyticsSnapshot.event_id,
                   func.coalesce(func.max(AnalyticsSnapshot.viewers), 0),
                   func.avg(cast(AnalyticsSnapshot.viewers, Float)),
                   func.coalesce(func.sum(AnalyticsSnapshot.messages), 0),
                   func.coalesce(func.sum(AnalyticsSnapshot.reactions), 0),
                   func.coalesce(func.max(AnalyticsSnapshot.hands), 0))
            .where(AnalyticsSnapshot.org_id == org_id, AnalyticsSnapshot.event_id.in_(ids))
            .group_by(AnalyticsSnapshot.event_id)
        ).all()
    }
    chat = {
        eid: int(n) for eid, n in db.execute(
            select(LiveMessage.event_id, func.count())
            .where(LiveMessage.org_id == org_id, LiveMessage.event_id.in_(ids),
                   LiveMessage.deleted_at.is_(None)).group_by(LiveMessage.event_id)
        ).all()
    }
    qa = {
        eid: {"asked": int(n), "answered": int(a)} for eid, n, a in db.execute(
            select(LiveQuestion.event_id, func.count(),
                   func.count(case((LiveQuestion.status == "answered", 1))))
            .where(LiveQuestion.org_id == org_id, LiveQuestion.event_id.in_(ids))
            .group_by(LiveQuestion.event_id)
        ).all()
    }
    duration = {
        eid: {"seconds": max(0, int(float(ms or 0) / 1000)), "peak": int(pk)}
        for eid, ms, pk in db.execute(
            select(BroadcastSession.event_id,
                   func.coalesce(func.sum(
                       func.extract("epoch", BroadcastSession.ended_at
                                    - BroadcastSession.started_at) * 1000
                       - BroadcastSession.paused_ms), 0),
                   func.coalesce(func.max(BroadcastSession.peak_viewers), 0))
            .where(BroadcastSession.org_id == org_id, BroadcastSession.event_id.in_(ids),
                   BroadcastSession.ended_at.isnot(None))
            .group_by(BroadcastSession.event_id)
        ).all()
    }
    replays = {
        eid: {"recordings": int(n), "views": int(v), "downloads": int(d)}
        for eid, n, v, d in db.execute(
            select(LiveRecording.event_id, func.count(),
                   func.coalesce(func.sum(LiveRecording.view_count), 0),
                   func.coalesce(func.sum(LiveRecording.download_count), 0))
            .where(LiveRecording.org_id == org_id, LiveRecording.event_id.in_(ids),
                   LiveRecording.deleted_at.is_(None)).group_by(LiveRecording.event_id)
        ).all()
    }

    out = []
    for ev in rows:
        a = by_event.get(ev.id, {})
        s = snaps.get(ev.id, {})
        q = qa.get(ev.id, {})
        d = duration.get(ev.id, {})
        r = replays.get(ev.id, {})
        registered, attended = a.get("registrations", 0), a.get("attended", 0)
        peak = max(s.get("peak", 0), d.get("peak", 0))
        counts = {"messages": chat.get(ev.id, 0), "questions": q.get("asked", 0),
                  "poll_votes": 0, "reactions": s.get("reactions", 0)}
        out.append({
            "id": str(ev.id),
            "title": ev.title,
            "status": ev.status,
            "category": ev.category,
            "tags": list(ev.tags or []),
            "visibility": ev.visibility,
            "location": ev.location,
            "start_time": ev.start_time.isoformat() if ev.start_time else None,
            "registrations": registered,
            "attended": attended,
            "unique_attendees": a.get("unique", 0),
            "attendance_rate": round(attended * 100 / registered, 1) if registered else None,
            "peak_viewers": peak,
            "avg_concurrent": s.get("avg"),
            "watch_seconds": a.get("watch_seconds", 0),
            "avg_watch_seconds": (int(a.get("watch_seconds", 0) / attended) if attended else None),
            "messages": counts["messages"],
            "questions": q.get("asked", 0),
            "questions_answered": q.get("answered", 0),
            "reactions": s.get("reactions", 0),
            "hands": s.get("hands", 0),
            "broadcast_seconds": d.get("seconds", 0),
            "recordings": r.get("recordings", 0),
            "replay_views": r.get("views", 0),
            "replay_downloads": r.get("downloads", 0),
            "engagement_score": engagement_score(counts, peak),
        })

    sorters = {
        "attended": lambda r: (-r["attended"], r["title"]),
        "registrations": lambda r: (-r["registrations"], r["title"]),
        "peak": lambda r: (-r["peak_viewers"], r["title"]),
        "engagement": lambda r: (-r["engagement_score"], r["title"]),
        "watch": lambda r: (-r["watch_seconds"], r["title"]),
        "recent": lambda r: (r["start_time"] or "", r["title"]),
        "title": lambda r: r["title"].lower(),
    }
    out.sort(key=sorters.get(sort, sorters["attended"]))
    if sort == "recent":
        out.reverse()
    return out


def event_detail(db, org_id, event_id) -> dict | None:
    """One event's deep dive: the retention curve, the engagement timeline and the funnel."""
    ev = db.scalar(select(Event).where(Event.id == event_id, Event.org_id == org_id,
                                       Event.deleted_at.is_(None)))
    if ev is None:
        return None
    samples = list(db.scalars(
        select(AnalyticsSnapshot)
        .where(AnalyticsSnapshot.event_id == ev.id, AnalyticsSnapshot.org_id == org_id)
        .order_by(AnalyticsSnapshot.created_at)
    ).all())
    table = event_table(db, org_id, filters={"event_id": ev.id},
                        start=datetime(1970, 1, 1, tzinfo=timezone.utc), end=_now())
    return {
        "event": table[0] if table else {"id": str(ev.id), "title": ev.title},
        "retention": retention_curve(samples),
        "timeline": engagement_timeline(samples),
        "funnel": _funnel(db, org_id, ev),
        "heatmap": _heatmap(samples),
        "sample_count": len(samples),
    }


def retention_curve(samples: list) -> list[dict]:
    """Percentage of the peak audience still present, across event progress.

    THE key audience chart, and it is a real measurement — the sampler wrote one row every 15
    seconds. Normalised to event progress (0–100%) rather than wall-clock so events of different
    lengths are comparable on one axis.

    Returns [] for an event with too few samples. A two-point "curve" is a straight line that
    implies a trend nobody measured.
    """
    if len(samples) < 3:
        return []
    peak = max(int(s.viewers or 0) for s in samples) or 0
    if peak <= 0:
        return []
    total = len(samples) - 1
    curve = []
    for step in range(RETENTION_STEPS + 1):
        index = min(total, round(total * step / RETENTION_STEPS))
        viewers = int(samples[index].viewers or 0)
        curve.append({"progress": step * 100 // RETENTION_STEPS, "viewers": viewers,
                      "percent": round(viewers * 100 / peak, 1)})
    return curve


def engagement_timeline(samples: list, *, max_points: int = 240) -> list[dict]:
    """Interactions over time. Down-sampled by AVERAGING, never by dropping points — taking every
    nth sample would make a spike appear or vanish depending on where the stride landed."""
    if not samples:
        return []
    stride = max(1, -(-len(samples) // max_points))
    out = []
    for i in range(0, len(samples), stride):
        chunk = samples[i:i + stride]
        out.append({
            "t": chunk[0].created_at.isoformat() if chunk[0].created_at else None,
            "viewers": round(sum(int(s.viewers or 0) for s in chunk) / len(chunk)),
            "messages": sum(int(s.messages or 0) for s in chunk),
            "questions": sum(int(s.questions or 0) for s in chunk),
            "reactions": sum(int(s.reactions or 0) for s in chunk),
            "hands": max(int(s.hands or 0) for s in chunk),
            "waiting": max(int(s.waiting or 0) for s in chunk),
        })
    return out


def _funnel(db, org_id, ev: Event) -> list[dict]:
    """Registered → joined → watched past half → interacted. Each stage a real count.

    "Watched past half" uses the recorded broadcast length when there is one; without it the stage
    is reported as unavailable rather than measured against a guess.
    """
    reg = EventRegistration
    registered, joined = db.execute(
        select(func.count(case((reg.status.in_(("registered", "attended")), 1))),
               func.count(case((reg.join_count > 0, 1))))
        .where(reg.org_id == org_id, reg.event_id == ev.id)
    ).one()
    broadcast_ms = db.scalar(
        select(func.coalesce(func.sum(
            func.extract("epoch", BroadcastSession.ended_at - BroadcastSession.started_at) * 1000
            - BroadcastSession.paused_ms), 0))
        .where(BroadcastSession.org_id == org_id, BroadcastSession.event_id == ev.id,
               BroadcastSession.ended_at.isnot(None))) or 0
    half_seconds = int(float(broadcast_ms) / 2000) if broadcast_ms else None
    sustained = db.scalar(
        select(func.count()).select_from(reg)
        .where(reg.org_id == org_id, reg.event_id == ev.id,
               reg.watch_seconds >= half_seconds)) if half_seconds else None
    interacted = db.scalar(
        select(func.count()).select_from(
            select(LiveMessage.user_id.label("uid")).where(
                LiveMessage.event_id == ev.id, LiveMessage.org_id == org_id,
                LiveMessage.user_id.isnot(None), LiveMessage.deleted_at.is_(None))
            .union(select(LiveQuestion.user_id.label("uid")).where(
                LiveQuestion.event_id == ev.id, LiveQuestion.org_id == org_id,
                LiveQuestion.user_id.isnot(None)))
            .subquery())) or 0

    stages = [
        {"stage": "Registered", "count": int(registered)},
        {"stage": "Joined", "count": int(joined)},
        {"stage": "Watched half", "count": int(sustained) if sustained is not None else None,
         "note": None if half_seconds else "No completed broadcast to measure against"},
        {"stage": "Interacted", "count": int(interacted)},
    ]
    top = int(registered) or int(joined) or 1
    for stage in stages:
        stage["percent"] = (round(stage["count"] * 100 / top, 1)
                            if stage["count"] is not None and top else None)
    return stages


def _heatmap(samples: list) -> list[dict]:
    """Audience by weekday × hour, from the sample timestamps. Real observation of when people
    actually watched — which is a different question from when events were scheduled."""
    grid: dict[tuple[int, int], list[int]] = {}
    for s in samples:
        if not s.created_at:
            continue
        key = (s.created_at.weekday(), s.created_at.hour)
        grid.setdefault(key, []).append(int(s.viewers or 0))
    return [{"weekday": day, "hour": hour, "viewers": round(sum(v) / len(v))}
            for (day, hour), v in sorted(grid.items())]


def org_heatmap(db, org_id, start=None, end=None, filters=None, *,
                timezone_name: str = "UTC") -> list[dict]:
    """Weekday × hour audience across the whole organization, aggregated in SQL.

    Grouped in the requested timezone for the same reason as `trend_series`: "Tuesday 7pm" has to
    mean the organizer's Tuesday evening, or the heatmap points at the wrong cell.
    """
    start, end = _window(start, end)
    ids = _event_ids(db, org_id, start, end, filters)
    if not ids:
        return []
    local = func.timezone(timezone_name or "UTC", AnalyticsSnapshot.created_at)
    # Postgres dow: 0 = Sunday. Shifted to Python's 0 = Monday so the client has one convention.
    # EXTRACT returns numeric/double, and Postgres has no mod(double, int) — the cast to INTEGER is
    # required, not cosmetic.
    weekday = func.mod(cast(func.extract("dow", local), Integer) + 6, 7)
    hour = cast(func.extract("hour", local), Integer)
    rows = db.execute(
        select(weekday.label("weekday"), hour.label("hour"),
               func.avg(cast(AnalyticsSnapshot.viewers, Float)),
               func.count())
        .where(AnalyticsSnapshot.org_id == org_id, AnalyticsSnapshot.event_id.in_(ids))
        .group_by("weekday", "hour").order_by("weekday", "hour")
    ).all()
    return [{"weekday": int(d), "hour": int(h), "viewers": round(float(v or 0)), "samples": int(n)}
            for d, h, v, n in rows]


# ── people ────────────────────────────────────────────────────────────────────

def speaker_stats(db, org_id, start=None, end=None, filters=None) -> dict:
    """Per-speaker figures across the window.

    `speaking_seconds` comes from event_assignments.speaking_ms, flushed from live presence when
    each broadcast ended (services/broadcast._persist_speaking_time). NULL there means the event
    never went live, which is why the response distinguishes "no data" from zero.
    """
    start, end = _window(start, end)
    ids = _event_ids(db, org_id, start, end, filters)
    if not ids:
        return {"speakers": [], "unavailable": _SPEAKER_UNAVAILABLE}

    rows = db.execute(
        select(User.id, User.full_name, User.email, User.department,
               func.count(distinct(EventAssignment.event_id)),
               func.sum(EventAssignment.speaking_ms),
               func.count(distinct(case((EventAssignment.speaking_ms.isnot(None),
                                         EventAssignment.event_id)))))
        .join(EventAssignment, EventAssignment.user_id == User.id)
        .where(EventAssignment.event_id.in_(ids),
               EventAssignment.role.in_(("speaker", "host")),
               User.deleted_at.is_(None))
        .group_by(User.id, User.full_name, User.email, User.department)
        .limit(MAX_PEOPLE_ROWS)
    ).all()

    answered = {
        uid: int(n) for uid, n in db.execute(
            select(LiveQuestion.answered_by, func.count())
            .where(LiveQuestion.org_id == org_id, LiveQuestion.event_id.in_(ids),
                   LiveQuestion.answered_by.isnot(None)).group_by(LiveQuestion.answered_by)
        ).all()
    }
    assigned = {
        uid: int(n) for uid, n in db.execute(
            select(LiveQuestion.assigned_to, func.count())
            .where(LiveQuestion.org_id == org_id, LiveQuestion.event_id.in_(ids),
                   LiveQuestion.assigned_to.isnot(None)).group_by(LiveQuestion.assigned_to)
        ).all()
    }
    # Peak and closing audience PER EVENT, fetched once for every event in scope. Calling a
    # per-speaker helper inside the loop below would be one pair of queries per speaker — the exact
    # N+1 this module's docstring forbids — so the two maps are built here and indexed in Python.
    peak_by_event, final_by_event = _audience_edges(db, org_id, ids)
    events_by_speaker: dict[uuid.UUID, list[uuid.UUID]] = {}
    for uid, eid in db.execute(
        select(EventAssignment.user_id, EventAssignment.event_id)
        .where(EventAssignment.event_id.in_(ids),
               EventAssignment.role.in_(("speaker", "host")))
    ).all():
        events_by_speaker.setdefault(uid, []).append(eid)

    speakers = []
    for uid, name, mail, dept, event_count, speaking_ms, measured in rows:
        their = events_by_speaker.get(uid, [])
        peaks = [peak_by_event[e] for e in their if peak_by_event.get(e)]
        drops = [round((peak_by_event[e] - final_by_event.get(e, 0)) * 100 / peak_by_event[e], 1)
                 for e in their if peak_by_event.get(e)]
        drop = round(sum(drops) / len(drops), 1) if drops else None
        speakers.append({
            "id": str(uid),
            "name": name,
            "email": mail,
            "department": dept,
            "events": int(event_count),
            # None, not 0, when nothing was measured — see the docstring.
            "speaking_seconds": int(int(speaking_ms) / 1000) if speaking_ms else None,
            "events_measured": int(measured),
            "questions_answered": answered.get(uid, 0),
            "questions_assigned": assigned.get(uid, 0),
            "answer_rate": (round(answered.get(uid, 0) * 100 / assigned[uid], 1)
                            if assigned.get(uid) else None),
            "avg_attendance": round(sum(peaks) / len(peaks)) if peaks else None,
            "peak_attendance": max(peaks) if peaks else None,
            "drop_off_percent": drop,
        })
    speakers.sort(key=lambda s: (-(s["speaking_seconds"] or 0), -s["events"], s["name"] or ""))
    return {"speakers": speakers, "unavailable": _SPEAKER_UNAVAILABLE}


_SPEAKER_UNAVAILABLE = {
    "audience_rating": ("No rating or survey feature exists in this platform, so speakers cannot "
                        "be scored by their audience. Questions answered, speaking time and "
                        "retention are measured instead."),
}


def _audience_edges(db, org_id, event_ids) -> tuple[dict, dict]:
    """({event_id: peak_viewers}, {event_id: closing_viewers}) — two statements for ALL events.

    Drop-off is peak minus what was still there at the last sample, which is the real answer to
    "did people stay". Both maps are built once and shared by every caller that needs a per-person
    or per-event roll-up.
    """
    if not event_ids:
        return {}, {}
    peaks = {
        eid: int(p or 0) for eid, p in db.execute(
            select(AnalyticsSnapshot.event_id, func.max(AnalyticsSnapshot.viewers))
            .where(AnalyticsSnapshot.org_id == org_id, AnalyticsSnapshot.event_id.in_(event_ids))
            .group_by(AnalyticsSnapshot.event_id)
        ).all()
    }
    newest = (
        select(AnalyticsSnapshot.event_id,
               func.max(AnalyticsSnapshot.created_at).label("last_at"))
        .where(AnalyticsSnapshot.org_id == org_id, AnalyticsSnapshot.event_id.in_(event_ids))
        .group_by(AnalyticsSnapshot.event_id).subquery()
    )
    finals = {
        eid: int(v or 0) for eid, v in db.execute(
            select(AnalyticsSnapshot.event_id, AnalyticsSnapshot.viewers)
            .join(newest, and_(newest.c.event_id == AnalyticsSnapshot.event_id,
                               newest.c.last_at == AnalyticsSnapshot.created_at))
        ).all()
    }
    return peaks, finals


def attendee_stats(db, org_id, start=None, end=None, filters=None, *,
                   limit: int = MAX_PEOPLE_ROWS) -> dict:
    """Per-attendee figures. The registration row already holds most of this per event; here it is
    rolled up per PERSON across the window."""
    start, end = _window(start, end)
    ids = _event_ids(db, org_id, start, end, filters)
    if not ids:
        return {"attendees": [], "unavailable": _ATTENDEE_UNAVAILABLE}

    reg = EventRegistration
    rows = db.execute(
        select(reg.user_id,
               func.max(func.coalesce(reg.name, "")),
               func.max(func.coalesce(reg.email, "")),
               func.count(),
               func.count(case((reg.status == "attended", 1))),
               func.coalesce(func.sum(reg.watch_seconds), 0),
               func.coalesce(func.sum(reg.join_count), 0),
               func.count(case((reg.bookmarked.is_(True), 1))),
               func.max(reg.last_joined_at))
        .where(reg.org_id == org_id, reg.event_id.in_(ids))
        .group_by(reg.user_id)
        .order_by(func.coalesce(func.sum(reg.watch_seconds), 0).desc())
        .limit(limit)
    ).all()
    user_ids = [r[0] for r in rows]
    if not user_ids:
        return {"attendees": [], "unavailable": _ATTENDEE_UNAVAILABLE}

    names = {uid: (name, dept) for uid, name, dept in db.execute(
        select(User.id, User.full_name, User.department).where(User.id.in_(user_ids))
    ).all()}
    asked = {uid: int(n) for uid, n in db.execute(
        select(LiveQuestion.user_id, func.count())
        .where(LiveQuestion.org_id == org_id, LiveQuestion.event_id.in_(ids),
               LiveQuestion.user_id.in_(user_ids)).group_by(LiveQuestion.user_id)
    ).all()}
    chatted = {uid: int(n) for uid, n in db.execute(
        select(LiveMessage.user_id, func.count())
        .where(LiveMessage.org_id == org_id, LiveMessage.event_id.in_(ids),
               LiveMessage.user_id.in_(user_ids), LiveMessage.deleted_at.is_(None))
        .group_by(LiveMessage.user_id)
    ).all()}
    marked = {uid: int(n) for uid, n in db.execute(
        select(MediaMark.user_id, func.count())
        .where(MediaMark.org_id == org_id, MediaMark.user_id.in_(user_ids))
        .group_by(MediaMark.user_id)
    ).all()}

    out = []
    for uid, snap_name, snap_mail, registrations, attended, watch, joins, bookmarks, last in rows:
        name, dept = names.get(uid, (None, None))
        out.append({
            "id": str(uid),
            "name": name or snap_name or None,
            "email": snap_mail or None,
            "department": dept,
            "registrations": int(registrations),
            "attended": int(attended),
            "attendance_rate": (round(int(attended) * 100 / int(registrations), 1)
                                if registrations else None),
            "watch_seconds": int(watch),
            "sessions_joined": int(joins),
            "questions_asked": asked.get(uid, 0),
            "messages_sent": chatted.get(uid, 0),
            "bookmarks": int(bookmarks),
            "recording_marks": marked.get(uid, 0),
            "last_seen": last.isoformat() if last else None,
        })
    return {"attendees": out, "unavailable": _ATTENDEE_UNAVAILABLE}


_ATTENDEE_UNAVAILABLE = {
    "reactions": ("Reactions are counted per event, not per person — one audit row per tap at "
                  "10,000 attendees is a write storm, so the aggregate is what is stored."),
    "resources_downloaded": ("Presentation downloads are not metered per attendee. Recording "
                             "downloads are, and appear in the media library's audit trail."),
    "certificates": ("Attendance is recorded (join times and watch duration), but no certificate "
                     "template or issuing flow exists yet, so none have been earned."),
}


# ── live ──────────────────────────────────────────────────────────────────────

def live_now(db, org_id) -> dict:
    """Every on-air event with its newest sample. The real-time dashboard's backing query.

    Reads the SAMPLER's output rather than Redis presence: one indexed query answers for every
    live event at once, where a presence read is a round trip per event. The figures are therefore
    at most one sample interval old, which is stated in the payload so nobody reads it as instant.
    """
    events = list(db.scalars(
        select(Event).where(Event.org_id == org_id, Event.deleted_at.is_(None),
                            Event.status.in_(("live", "paused")))
        .order_by(Event.start_time)
    ).all())
    if not events:
        return {"events": [], "totals": {"live_events": 0, "viewers": 0, "participants": 0,
                                         "waiting": 0, "hands": 0},
                "sample_seconds": SAMPLE_INTERVAL_SECONDS, "unavailable": _LIVE_UNAVAILABLE}

    ids = [e.id for e in events]
    newest = (
        select(AnalyticsSnapshot.event_id,
               func.max(AnalyticsSnapshot.created_at).label("last_at"))
        .where(AnalyticsSnapshot.event_id.in_(ids)).group_by(AnalyticsSnapshot.event_id).subquery()
    )
    latest = {
        s.event_id: s for s in db.scalars(
            select(AnalyticsSnapshot).join(
                newest, and_(newest.c.event_id == AnalyticsSnapshot.event_id,
                             newest.c.last_at == AnalyticsSnapshot.created_at))
        ).all()
    }
    peaks = {
        eid: int(p) for eid, p in db.execute(
            select(BroadcastSession.event_id, func.max(BroadcastSession.peak_viewers))
            .where(BroadcastSession.event_id.in_(ids)).group_by(BroadcastSession.event_id)
        ).all()
    }
    rows, totals = [], {"live_events": len(events), "viewers": 0, "participants": 0,
                        "waiting": 0, "hands": 0}
    for ev in events:
        s = latest.get(ev.id)
        rows.append({
            "id": str(ev.id),
            "title": ev.title,
            "status": ev.status,
            "started_at": ev.start_time.isoformat() if ev.start_time else None,
            "viewers": int(s.viewers or 0) if s else None,
            "participants": int(s.participants or 0) if s else None,
            "on_stage": int(s.on_stage or 0) if s else None,
            "waiting": int(s.waiting or 0) if s else None,
            "hands": int(s.hands or 0) if s else None,
            "peak_viewers": peaks.get(ev.id, 0),
            "sampled_at": s.created_at.isoformat() if s and s.created_at else None,
            # No sample yet is a real state: the event just went live and the sampler has not
            # ticked. Nulls above say so instead of reporting an audience of zero.
            "has_sample": s is not None,
        })
        if s:
            totals["viewers"] += int(s.viewers or 0)
            totals["participants"] += int(s.participants or 0)
            totals["waiting"] += int(s.waiting or 0)
            totals["hands"] += int(s.hands or 0)
    return {"events": rows, "totals": totals, "sample_seconds": SAMPLE_INTERVAL_SECONDS,
            "unavailable": _LIVE_UNAVAILABLE}


# Mirrors services/broadcast.SAMPLE_SECONDS. Duplicated as a constant rather than imported so this
# module stays free of the broadcast service (which imports the bus, LiveKit and asyncio).
SAMPLE_INTERVAL_SECONDS = 15

_LIVE_UNAVAILABLE = {
    "bitrate": ("Encoder bitrate, packet loss, RTT and frame rate are reported by publishers over "
                "the live socket and shown in the host console. They are not persisted, so they "
                "exist live and not historically."),
    "bandwidth": "Egress bandwidth is not metered by this platform; the CDN or SFU bill is the source.",
    "cpu_memory": ("Server CPU and memory are on the platform status console (/admin). A "
                   "participant's own CPU is not readable from the browser."),
    "geography": "Viewer geography needs a GeoIP lookup, which is not integrated.",
}


# ── filter options ────────────────────────────────────────────────────────────

def filter_options(db, org_id) -> dict:
    """The values that actually occur, so no filter can produce an empty result by construction."""
    categories = [c for c in db.scalars(
        select(distinct(Event.category)).where(Event.org_id == org_id,
                                               Event.category.isnot(None))).all() if c]
    locations = [loc for loc in db.scalars(
        select(distinct(Event.location)).where(Event.org_id == org_id,
                                               Event.location.isnot(None)).limit(100)).all() if loc]
    departments = [d for d in db.scalars(
        select(distinct(User.department)).where(User.org_id == org_id,
                                                User.department.isnot(None))).all() if d]
    tag_lists = db.scalars(select(Event.tags).where(Event.org_id == org_id,
                                                    Event.tags.isnot(None))).all()
    tags = sorted({t for lst in tag_lists for t in (lst or []) if t})
    people = db.execute(
        select(User.id, User.full_name, EventAssignment.role)
        .join(EventAssignment, EventAssignment.user_id == User.id)
        .join(Event, Event.id == EventAssignment.event_id)
        .where(Event.org_id == org_id, User.deleted_at.is_(None),
               EventAssignment.role.in_(("host", "speaker")))
        .distinct()
    ).all()
    hosts = sorted({(str(i), n) for i, n, role in people if role == "host"}, key=lambda p: p[1] or "")
    speakers = sorted({(str(i), n) for i, n, role in people if role == "speaker"},
                      key=lambda p: p[1] or "")
    return {
        "categories": sorted(categories),
        "locations": sorted(locations),
        "departments": sorted(departments),
        "tags": tags[:100],
        "hosts": [{"id": i, "name": n} for i, n in hosts],
        "speakers": [{"id": i, "name": n} for i, n in speakers],
        "statuses": list(COUNTED_STATUSES),
    }
