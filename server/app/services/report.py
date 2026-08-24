"""Post-event report — BRD §18.2's "generated post-event audience and operations report,"
explicitly NOT a customer analytics console or custom-query builder (table 66). A report
is a point-in-time JSON snapshot (models.live.EventReport), not a live query, so a
released report reads the same to its recipient forever after.

Same honesty convention as services/org.py::analytics()/audience_attendance(): a figure
with no real source is None plus a note, never invented. Delivery reuses
services/delivery.py's CustomerDelivery mechanism (kind="report")."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from ..crud import admin as admin_crud
from ..crud import delivery as delivery_crud
from ..email import send_event_report_email
from ..models import (
    AnalyticsSnapshot,
    BroadcastSession,
    Event,
    EventAssignment,
    EventRegistration,
    LiveActivity,
    LiveRecording,
    User,
)

SAMPLE_HOURS = 15 / 3600  # AnalyticsSnapshot sampler interval — matches org.py::analytics()


def _assigned(db, event_id, role: str) -> list[str]:
    return list(db.scalars(
        select(User.full_name).join(EventAssignment, EventAssignment.user_id == User.id)
        .where(EventAssignment.event_id == event_id, EventAssignment.role == role)
    ).all())


def _audience(db, event: Event) -> dict:
    sessions = db.scalars(select(BroadcastSession).where(BroadcastSession.event_id == event.id)).all()
    snapshots = db.scalars(select(AnalyticsSnapshot).where(AnalyticsSnapshot.event_id == event.id)).all()
    peak = max((s.peak_viewers for s in sessions), default=0)
    watch_hours = round(sum(s.viewers for s in snapshots) * SAMPLE_HOURS, 1)

    total_registrations = db.scalar(
        select(func.count()).select_from(EventRegistration).where(EventRegistration.event_id == event.id)
    ) or 0
    show_rate = None
    if event.visibility == "private" and total_registrations:
        claimed = db.scalar(
            select(func.count()).select_from(EventRegistration).where(
                EventRegistration.event_id == event.id, EventRegistration.claimed_at.isnot(None),
            )
        ) or 0
        show_rate = round(100 * claimed / total_registrations)

    return {
        "peak_viewers": peak,
        "watch_hours": watch_hours,
        "total_registrations": total_registrations,
        "show_rate": show_rate,
        "show_rate_basis": "private_invited_events_only" if event.visibility == "private" else None,
        # Same limitation as org.py::audience_attendance — no table records a specific
        # person's watch duration once a room ends, so a true unique-attendee count isn't
        # available at the single-event level either.
        "unique_attendees": None,
        "unique_attendees_note": "No per-person watch-duration record exists once a room ends.",
    }


def _recordings(db, event_id) -> list[dict]:
    rows = db.scalars(select(LiveRecording).where(LiveRecording.event_id == event_id)).all()
    out = []
    for r in rows:
        duration = None
        if r.started_at and r.stopped_at:
            duration = int((r.stopped_at - r.started_at).total_seconds() - r.paused_ms / 1000)
        out.append({
            "role": r.role, "status": r.status, "validation_status": r.validation_status,
            "duration_seconds": duration, "size_bytes": r.size_bytes, "legal_hold": r.legal_hold,
        })
    return out


def _operations(db, event: Event) -> dict:
    activity_counts = dict(db.execute(
        select(LiveActivity.kind, func.count()).where(LiveActivity.event_id == event.id)
        .group_by(LiveActivity.kind)
    ).all())
    return {
        "hosts": _assigned(db, event.id, "host"),
        "moderators": _assigned(db, event.id, "moderator"),
        "activity_counts": activity_counts,
        # models.platform_ops.Incident has no event_id column — incidents aren't
        # correlated to a specific event anywhere in this stack, so this is honestly
        # omitted rather than guessed at from org-wide/time-window incidents.
        "incidents_note": "Incidents are not tracked per-event in this stack.",
    }


def generate_event_report(db, event: Event, actor=None) -> tuple:
    """Assembles and persists one EventReport snapshot. Returns the new row; the caller
    (router) decides whether/when to release it."""
    data = {
        "event": {
            "id": str(event.id), "title": event.title, "category": event.category,
            "start_time": event.start_time.isoformat() if event.start_time else None,
            "end_time": event.end_time.isoformat() if event.end_time else None,
        },
        "audience": _audience(db, event),
        "recordings": _recordings(db, event.id),
        "operations": _operations(db, event),
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_sources": [
                "BroadcastSession (peak_viewers)", "AnalyticsSnapshot (watch_hours, 15s samples)",
                "EventRegistration (registrations, show_rate)", "LiveRecording (recordings)",
                "EventAssignment (hosts, moderators)", "LiveActivity (activity_counts)",
            ],
        },
    }
    report = delivery_crud.create_report(db, event.id, event.org_id, data, generated_by=actor.id if actor else None)
    admin_crud.create_audit_log(
        db, actor=actor, action="report.generate", target_type="event_report",
        target_id=report.id, org_id=event.org_id,
        meta={"event_id": str(event.id), "version": report.version},
    )
    return report


def release_report(db, report, event: Event, *, recipient_name, recipient_email, expires_in_days, actor) -> tuple:
    """Returns (CustomerDelivery, raw_token) — same shape as services.delivery.create_export."""
    delivery, raw = delivery_crud.create_delivery(
        db, event.id, event.org_id, "report",
        report_id=report.id, recipient_name=recipient_name, recipient_email=recipient_email,
        expires_in_days=expires_in_days, created_by=actor.id if actor else None,
    )
    delivery_crud.release_report(db, report)
    admin_crud.create_audit_log(
        db, actor=actor, action="report.release", target_type="customer_delivery",
        target_id=delivery.id, org_id=event.org_id,
        meta={"event_id": str(event.id), "report_id": str(report.id), "recipient_email": recipient_email},
    )
    return delivery, raw


def deliver_report_email(db, delivery, event_title: str, report_url: str) -> None:
    send_event_report_email(delivery.recipient_email, delivery.recipient_name, event_title, report_url, delivery.expires_at)
    delivery.delivered_at = datetime.now(timezone.utc)
    db.commit()
