"""Read models for the Event Workspace sections that had no single source (ZST-ENG-DASH-001
EVT-02 Preparation, EVT-04 Live Control, EVT-06 Insights).

Each one only READS rows other services own and decides nothing those services have not
already decided: readiness verdicts come from services/ops' gates, broadcast health from
BroadcastSession.health_level (written by services/broadcast), signal state from
LiveIngressEndpoint.signal_state (services/media_comms), audience figures from the same
tables the Audience and Playback pages aggregate.

Null is not zero. A figure that was never measured — peak viewers of an event that never
went on air, an average watch time over no viewers, a rating nobody gave — is None, and the
console shows it as unavailable rather than as a real 0. A 0 here means "measured, and none".
"""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    AnalyticsSnapshot,
    AudienceSession,
    BroadcastSession,
    EventFeedback,
    EventIncident,
    LiveIngressEndpoint,
    LiveRecording,
)
from ..models.event import ON_AIR_STATUSES
from . import audience as audience_svc
from . import bus
from . import ops as ops_svc
from . import playback_qoe as playback_qoe_svc
from .broadcast import _split as presence_split
from .org import _readiness_state

# An incident is open while it is delayed or held and nobody has resumed or cancelled it.
_OPEN_INCIDENT_STATES = ("delayed", "temporary_hold")
_SESSION_HISTORY = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _on_air_seconds(session: BroadcastSession, now: datetime) -> int | None:
    if session.started_at is None:
        return None
    started = session.started_at if session.started_at.tzinfo else session.started_at.replace(tzinfo=timezone.utc)
    end = session.ended_at or now
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, int((end - started).total_seconds() - (session.paused_ms or 0) / 1000))


# ── Preparation ──────────────────────────────────────────────────────────────────────────

def readiness(db: Session, ev) -> dict:
    out = ops_svc.readiness_for(db, ev)
    out["readiness_state"] = _readiness_state(out)
    out["evaluated_at"] = _iso(_now())
    return out


# ── Live Control ─────────────────────────────────────────────────────────────────────────

def _session_out(s: BroadcastSession, now: datetime) -> dict:
    return {
        "id": str(s.id),
        "status": s.status,
        "started_at": _iso(s.started_at),
        "ended_at": _iso(s.ended_at),
        "ended_reason": s.ended_reason,
        "on_air_seconds": _on_air_seconds(s, now),
        "peak_viewers": s.peak_viewers if s.started_at else None,
        "health_level": s.health_level,
        "health_changed_at": _iso(s.health_changed_at),
    }


def _input_out(i: LiveIngressEndpoint) -> dict:
    # Never the stream key (not a column) and never the provider's error text, which can
    # carry endpoint URLs; only whether provisioning failed.
    return {
        "id": str(i.id),
        "title": i.title,
        "input_type": i.input_type,
        "provisioned": bool(i.enforced and i.ingress_id),
        "provisioning_failed": bool(i.error) or not i.enforced,
        "state": i.state,
        "signal_state": i.signal_state,
        "signal_changed_at": _iso(i.signal_changed_at),
        "interruption_count": i.interruption_count,
    }


def _recording_out(r: LiveRecording | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": str(r.id),
        "status": r.status,
        "enforced": r.enforced,
        "health_state": r.health_state,
        "started_at": _iso(r.started_at),
        "stopped_at": _iso(r.stopped_at),
        "role": r.role,
    }


def _incident_out(i: EventIncident) -> dict:
    return {
        "id": str(i.id),
        "operational_state": i.operational_state,
        "reason_category": i.reason_category,
        "started_at": _iso(i.hold_started_at or i.delay_started_at or i.opened_at),
        "next_update_at": _iso(i.next_update_at),
        "customer_summary": i.customer_summary,
    }


async def live_status(db: Session, ev) -> dict:
    """What the Live Control section shows, from committed rows plus the live presence set.

    Presence is read only while the event is on air; an event that is not live reports no
    audience-now figure at all (None), never a 0 read from an empty room that was never open."""
    now = _now()
    sessions = db.scalars(
        select(BroadcastSession).where(BroadcastSession.event_id == ev.id)
        .order_by(BroadcastSession.created_at.desc()).limit(_SESSION_HISTORY)
    ).all()
    session_count = db.scalar(
        select(func.count()).select_from(BroadcastSession).where(BroadcastSession.event_id == ev.id)) or 0
    current = next((s for s in sessions if s.status in ("live", "paused", "preview") and s.ended_at is None), None)
    recordings = db.scalars(
        select(LiveRecording).where(LiveRecording.event_id == ev.id, LiveRecording.deleted_at.is_(None))
        .order_by(LiveRecording.created_at.desc()).limit(2)
    ).all()
    inputs = db.scalars(
        select(LiveIngressEndpoint).where(LiveIngressEndpoint.event_id == ev.id)
        .order_by(LiveIngressEndpoint.created_at)
    ).all()
    incidents = db.scalars(
        select(EventIncident).where(
            EventIncident.event_id == ev.id,
            EventIncident.operational_state.in_(_OPEN_INCIDENT_STATES),
            EventIncident.resumed_at.is_(None), EventIncident.canceled_at.is_(None),
        ).order_by(EventIncident.opened_at.desc())
    ).all()

    on_air = ev.status in ON_AIR_STATUSES
    audience = None
    presence_error = False
    if on_air:
        try:
            split = presence_split(await bus.presence_all(ev.id))
            audience = {"viewers": split["viewers"], "speakers": split["speakers"],
                        "hosts": split["hosts"], "waiting": split["waiting"],
                        "poor_connections": split["poor_connections"]}
        except Exception:  # noqa: BLE001 - a presence outage is reported, not guessed around
            presence_error = True

    return {
        "event_id": str(ev.id),
        "status": ev.status,
        "on_air": on_air,
        "as_of": _iso(now),
        "current_session": _session_out(current, now) if current else None,
        "sessions": [_session_out(s, now) for s in sessions],
        "session_count": session_count,
        "recording": _recording_out(recordings[0] if recordings else None),
        "recording_enabled": bool(ev.recording_enabled),
        "inputs": [_input_out(i) for i in inputs],
        "open_incidents": [_incident_out(i) for i in incidents],
        "audience_now": audience,
        "audience_unavailable": presence_error,
    }


# ── Insights ─────────────────────────────────────────────────────────────────────────────

_TIMELINE_POINTS = 120


def _timeline(db: Session, org, ev) -> list[dict]:
    """Concurrent viewers over the broadcast, from the live sampler's snapshots
    (services/broadcast writes one AnalyticsSnapshot per sample). At most _TIMELINE_POINTS
    points: a long event is bucketed and each bucket keeps its PEAK, so the chart can never
    understate the busiest moment. Empty when nothing was sampled — never interpolated."""
    rows = db.execute(
        select(AnalyticsSnapshot.created_at, AnalyticsSnapshot.viewers)
        .where(AnalyticsSnapshot.event_id == ev.id, AnalyticsSnapshot.org_id == org.id)
        .order_by(AnalyticsSnapshot.created_at)
    ).all()
    if len(rows) <= _TIMELINE_POINTS:
        return [{"t": _iso(t), "viewers": int(v or 0)} for t, v in rows]
    size = -(-len(rows) // _TIMELINE_POINTS)
    out = []
    for i in range(0, len(rows), size):
        bucket = rows[i:i + size]
        peak_t, peak_v = max(bucket, key=lambda r: r[1] or 0)
        out.append({"t": _iso(peak_t), "viewers": int(peak_v or 0)})
    return out


def _outcomes(db: Session, org, ev, programme_seconds: int | None, viewers: int) -> dict | None:
    """How far identified viewers got: at least 10 minutes, half and nine-tenths of the
    programme (the time on air). Counted from each viewer's recorded watch time. None when
    there is nobody to count; the programme thresholds are None when nothing went on air."""
    if not viewers:
        return None
    base = [AudienceSession.event_id == ev.id, AudienceSession.org_id == org.id]

    def at_least(seconds):
        return int(db.scalar(select(func.count()).select_from(AudienceSession)
                             .where(*base, AudienceSession.watch_seconds >= seconds)) or 0)

    return {
        "viewers": viewers,
        "watched_10_min": at_least(600),
        "reached_half": at_least(programme_seconds * 0.5) if programme_seconds else None,
        "reached_ninety": at_least(programme_seconds * 0.9) if programme_seconds else None,
        "programme_seconds": programme_seconds,
    }


def insights(db: Session, org, ev) -> dict:
    now = _now()
    sessions = db.scalars(select(BroadcastSession).where(BroadcastSession.event_id == ev.id)).all()
    broadcast = [s for s in sessions if s.started_at is not None]
    measured = bool(broadcast)

    viewers, total_watch, avg_watch = db.execute(
        select(func.count(), func.coalesce(func.sum(AudienceSession.watch_seconds), 0),
               func.avg(AudienceSession.watch_seconds))
        .where(AudienceSession.event_id == ev.id, AudienceSession.org_id == org.id)
    ).one()
    rejoined = db.scalar(select(func.count()).select_from(AudienceSession).where(
        AudienceSession.event_id == ev.id, AudienceSession.org_id == org.id,
        AudienceSession.join_count > 1)) or 0

    rated, rating_avg, feedback_total = db.execute(
        select(func.count(EventFeedback.rating), func.avg(EventFeedback.rating), func.count())
        .where(EventFeedback.event_id == ev.id, EventFeedback.org_id == org.id,
               EventFeedback.role == "viewer")
    ).one()

    audience = audience_svc.insights(db, org, event_id=ev.id, whole_event=True)
    qoe = playback_qoe_svc.summarize(db, org.id, event_id=ev.id, window="all")

    on_air = [_on_air_seconds(s, now) or 0 for s in broadcast]
    programme = sum(on_air) if measured else None
    return {
        "event_id": str(ev.id),
        "as_of": _iso(now),
        "measured": measured,
        "broadcast": {
            "sessions": len(broadcast),
            "on_air_seconds": sum(on_air) if measured else None,
            "first_started_at": _iso(min(s.started_at for s in broadcast)) if measured else None,
            "last_ended_at": _iso(max((s.ended_at for s in broadcast if s.ended_at), default=None)),
        },
        "audience": {
            "unique_viewers": int(viewers or 0),
            "peak_viewers": max(s.peak_viewers or 0 for s in broadcast) if measured else None,
            "avg_watch_seconds": int(round(avg_watch)) if viewers else None,
            "total_watch_seconds": int(total_watch or 0),
            "returning_viewers": int(rejoined) if viewers else None,
        },
        "registration": audience["registration_attendance"],
        "geography": audience["geography"],
        "geography_unknown": audience["geography_unknown"],
        "device_mix": audience["device_mix"],
        "blocked_join_attempts": audience["blocked_join_attempts"],
        "feedback": {
            "responses": int(feedback_total or 0),
            "ratings": int(rated or 0),
            "average_rating": round(float(rating_avg), 2) if rated else None,
        },
        "playback": qoe,
        "timeline": _timeline(db, org, ev),
        "outcomes": _outcomes(db, org, ev, programme, int(viewers or 0)),
    }
