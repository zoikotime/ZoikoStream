"""DB access for CustomerDelivery (controlled customer export + post-event report
release) and EventReport. Mirrors crud/event.py's access-link section exactly — same
sha256-of-a-urlsafe-token convention, same "raw value returned once" contract."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..models import CustomerDelivery, EventReport


def _hash_delivery_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_delivery(
    db, event_id, org_id, kind, *, recipient_name, recipient_email, expires_in_days,
    recording_id=None, report_id=None, created_by=None,
) -> tuple[CustomerDelivery, str]:
    raw = secrets.token_urlsafe(32)
    delivery = CustomerDelivery(
        event_id=event_id, org_id=org_id, kind=kind,
        recording_id=recording_id, report_id=report_id,
        recipient_name=recipient_name, recipient_email=recipient_email,
        token_hash=_hash_delivery_token(raw), created_by=created_by,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery, raw


def find_delivery_by_token(db, raw: str) -> CustomerDelivery | None:
    """The rules a delivery must pass to resolve: exists, not revoked, not expired.
    Recording an access is a side effect of a successful lookup — same posture as
    crud.event.find_access_link, since the raw token only ever reaches this check via a
    link the recipient actually opened."""
    if not raw:
        return None
    delivery = db.scalar(select(CustomerDelivery).where(CustomerDelivery.token_hash == _hash_delivery_token(raw)))
    if delivery is None or delivery.revoked_at is not None:
        return None
    if delivery.expires_at and delivery.expires_at < datetime.now(timezone.utc):
        return None
    now = datetime.now(timezone.utc)
    if delivery.first_accessed_at is None:
        delivery.first_accessed_at = now
    delivery.last_accessed_at = now
    delivery.access_count += 1
    db.commit()
    return delivery


def revoke_delivery(db, delivery: CustomerDelivery) -> CustomerDelivery:
    delivery.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(delivery)
    return delivery


def get_delivery(db, org_id, delivery_id) -> CustomerDelivery | None:
    return db.scalar(select(CustomerDelivery).where(CustomerDelivery.id == delivery_id, CustomerDelivery.org_id == org_id))


def list_deliveries(db, *, recording_id=None, report_id=None) -> list[CustomerDelivery]:
    stmt = select(CustomerDelivery).order_by(CustomerDelivery.created_at.desc())
    if recording_id is not None:
        stmt = stmt.where(CustomerDelivery.recording_id == recording_id)
    if report_id is not None:
        stmt = stmt.where(CustomerDelivery.report_id == report_id)
    return db.scalars(stmt).all()


# ── Event reports ──────────────────────────────────────────────────────────────

def next_report_version(db, event_id) -> int:
    latest = db.scalar(
        select(EventReport.version).where(EventReport.event_id == event_id)
        .order_by(EventReport.version.desc()).limit(1)
    )
    return (latest or 0) + 1


def create_report(db, event_id, org_id, data: dict, generated_by=None) -> EventReport:
    report = EventReport(
        event_id=event_id, org_id=org_id, version=next_report_version(db, event_id),
        data=data, generated_by=generated_by,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def get_report(db, org_id, report_id) -> EventReport | None:
    return db.scalar(select(EventReport).where(EventReport.id == report_id, EventReport.org_id == org_id))


def list_reports(db, event_id) -> list[EventReport]:
    return db.scalars(
        select(EventReport).where(EventReport.event_id == event_id).order_by(EventReport.version.desc())
    ).all()


def release_report(db, report: EventReport) -> EventReport:
    report.released_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(report)
    return report
