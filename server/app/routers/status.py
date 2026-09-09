"""Public status page API (ZST-EC-001 STS-001 -> STS-006).

Deliberately UNAUTHENTICATED, like routers/contact.py: a status page that requires a login
is useless during an outage that prevents logging in. Everything served here is a PUBLISHED
public record - the internal `platform_ops.Incident` is never reachable through this router,
and neither is any admin object.

Publication itself is NOT here: only authorized platform operators publish, so those routes
live on the super-admin `/admin` router. This module reads published state and manages a
subscriber's own subscription.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    COMPONENT_LABELS,
    PUBLIC_IMPACT_LABELS,
    REGION_LABELS,
    PublicStatusIncident,
    ScheduledMaintenance,
    StatusComponent,
    StatusSubscriber,
)
from ..schemas.status import StatusSubscribeIn, StatusTokenIn, StatusPreferencesIn
from ..services import status_publication as sp

router = APIRouter(prefix="/status", tags=["status"])


def _incident_out(incident: PublicStatusIncident, db: Session) -> dict:
    """The PUBLISHED projection. Nothing internal crosses this boundary.

    `internal_incident_id` is deliberately absent: it is a correlation key for operators,
    not something a public consumer needs or should have.
    """
    return {
        "reference": incident.public_reference,
        "title": incident.title,
        "status": incident.status,
        "impact": incident.impact,
        "impact_label": PUBLIC_IMPACT_LABELS.get(incident.impact, "Under assessment"),
        "components": [COMPONENT_LABELS.get(c, c)
                       for c in (incident.affected_components or [])],
        "regions": [REGION_LABELS.get(r, r) for r in (incident.affected_regions or [])],
        "current_update": incident.current_update,
        "customer_action": incident.customer_action,
        # Every timestamp is rendered in UTC: the driver would otherwise hand back the
        # connection's local offset, which reads as a different clock time to consumers.
        "started_at": sp.as_utc(incident.started_at),
        "identified_at": sp.as_utc(incident.identified_at),
        "monitoring_at": sp.as_utc(incident.monitoring_at),
        "resolved_at": sp.as_utc(incident.resolved_at),
        "reopened_at": sp.as_utc(incident.reopened_at),
        "residual_work": incident.residual_work,
        "residual_summary": incident.residual_summary,
        "review_published": incident.review_published,
        "next_update_at": sp.as_utc(incident.next_update_at),
        "version": incident.version,
        # The full append-only history, so a correction and the statement it corrects are
        # both visible to anybody reading the page.
        "history": [
            {"version": u.version, "status": u.status, "type": u.update_type,
             "body": u.body, "published_at": sp.as_utc(u.published_at),
             "corrects_version": u.correction_of_version}
            for u in sp.published_history(db, incident)
        ],
    }


def _maintenance_out(row: ScheduledMaintenance) -> dict:
    return {
        "reference": row.public_reference,
        "title": row.title,
        "kind": row.kind,
        "status": row.status,
        "components": [COMPONENT_LABELS.get(c, c) for c in (row.affected_components or [])],
        "regions": [REGION_LABELS.get(r, r) for r in (row.affected_regions or [])],
        # UTC is the canonical record and is named as such in the field itself.
        "starts_at_utc": sp.as_utc(row.starts_at_utc),
        "ends_at_utc": sp.as_utc(row.ends_at_utc),
        "previous_starts_at_utc": sp.as_utc(row.previous_starts_at_utc),
        "previous_ends_at_utc": sp.as_utc(row.previous_ends_at_utc),
        "impact_summary": row.impact_summary,
        "emergency_reason": row.emergency_reason,
        "started_at": sp.as_utc(row.started_at),
        "completed_at": sp.as_utc(row.completed_at),
        "canceled_at": sp.as_utc(row.canceled_at),
        "remaining_work": row.remaining_work,
        "version": row.version,
    }


@router.get("")
def public_status(db: Session = Depends(get_db)):
    """Everything the public status page renders, from published records only."""
    components = db.scalars(
        select(StatusComponent).order_by(StatusComponent.display_order)).all()
    active = db.scalars(
        select(PublicStatusIncident)
        .where(PublicStatusIncident.status != "resolved",
               PublicStatusIncident.published_at.isnot(None))
        .order_by(PublicStatusIncident.started_at.desc())).all()
    history = db.scalars(
        select(PublicStatusIncident)
        .where(PublicStatusIncident.status == "resolved",
               PublicStatusIncident.published_at.isnot(None))
        .order_by(PublicStatusIncident.resolved_at.desc()).limit(20)).all()
    upcoming = db.scalars(
        select(ScheduledMaintenance)
        .where(ScheduledMaintenance.status.in_(("scheduled", "in_progress", "extended")),
               ScheduledMaintenance.published_at.isnot(None))
        .order_by(ScheduledMaintenance.starts_at_utc)).all()
    recent = db.scalars(
        select(ScheduledMaintenance)
        .where(ScheduledMaintenance.status.in_(("completed", "canceled")),
               ScheduledMaintenance.published_at.isnot(None))
        .order_by(ScheduledMaintenance.updated_at.desc()).limit(10)).all()

    # Overall posture is derived from the components' own recorded impact, not asserted.
    worst = "none"
    for order in ("major_outage", "partial_outage", "degraded"):
        if any(c.current_impact == order for c in components):
            worst = order
            break
    return {
        "overall": worst,
        "overall_label": PUBLIC_IMPACT_LABELS.get(worst, "Operational"),
        "components": [{"key": c.key, "label": c.label, "impact": c.current_impact,
                        "impact_label": PUBLIC_IMPACT_LABELS.get(c.current_impact,
                                                                 "Operational")}
                       for c in components],
        "regions": [{"key": k, "label": v} for k, v in REGION_LABELS.items()],
        "active_incidents": [_incident_out(i, db) for i in active],
        "incident_history": [_incident_out(i, db) for i in history],
        "scheduled_maintenance": [_maintenance_out(m) for m in upcoming],
        "recent_maintenance": [_maintenance_out(m) for m in recent],
    }


@router.post("/subscribe", status_code=status.HTTP_202_ACCEPTED)
def subscribe_to_status(data: StatusSubscribeIn, background: BackgroundTasks,
                        db: Session = Depends(get_db)):
    """Subscribe an address to status updates.

    Returns 202 whatever happens for an otherwise-valid address: confirming or denying that
    a given address is already subscribed would make this endpoint an enumeration oracle.
    Nothing is sent until the address proves control of the inbox.
    """
    subscriber, raw = sp.subscribe(db, email=data.email, components=data.components,
                                  regions=data.regions, notify_kinds=data.notify_kinds)
    if subscriber is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A valid email address is required")
    sp.notify_verify(db, background, subscriber, raw)
    return {"status": "pending_verification"}


@router.post("/subscribe/confirm")
def confirm_status_subscription(data: StatusTokenIn, background: BackgroundTasks,
                               db: Session = Depends(get_db)):
    """Redeem a subscription verification token. Single-use, purpose-bound, expiring."""
    subscriber, outcome, manage = sp.confirm(db, token=data.token)
    if subscriber is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"invalid": "This confirmation link is not valid.",
                             "already_used": "This link has already been used.",
                             "superseded": "A newer confirmation link was issued.",
                             "expired": "This confirmation link has expired."}[outcome])
    sp.notify_confirmed(db, background, subscriber, manage)
    return {"status": subscriber.status, "manage_token": manage}


def _by_manage_token(db: Session, token: str) -> StatusSubscriber:
    """Resolve a subscriber from their opaque manage handle.

    The handle is hashed at rest, so the stored value is not usable as a link, and the
    address never appears in a URL.
    """
    subscriber = db.scalar(
        select(StatusSubscriber).where(
            StatusSubscriber.manage_token_hash == sp._hash(token)))  # noqa: SLF001
    if subscriber is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This preferences link is not valid")
    return subscriber


@router.get("/preferences")
def read_status_preferences(t: str, db: Session = Depends(get_db)):
    subscriber = _by_manage_token(db, t)
    return {"email": subscriber.email, "status": subscriber.status,
            "components": subscriber.components or [],
            "regions": subscriber.regions or [],
            "notify_kinds": subscriber.notify_kinds or []}


@router.patch("/preferences")
def update_status_preferences(t: str, data: StatusPreferencesIn,
                              background: BackgroundTasks,
                              db: Session = Depends(get_db)):
    """Change a subscription. A no-op change sends nothing."""
    subscriber = _by_manage_token(db, t)
    changed, previous = sp.update_preferences(
        db, subscriber, components=data.components, regions=data.regions,
        notify_kinds=data.notify_kinds)
    if changed:
        sp.notify_preferences_changed(db, background, subscriber, previous=previous,
                                      manage_token=t)
    return {"changed": changed, "components": subscriber.components or [],
            "regions": subscriber.regions or []}


@router.post("/unsubscribe")
def unsubscribe_from_status(t: str, background: BackgroundTasks,
                            db: Session = Depends(get_db)):
    """Stop STATUS mail only.

    Account, security, billing and privacy channels are untouched - status is a separate
    communication domain, and the confirmation says so explicitly.
    """
    subscriber = _by_manage_token(db, t)
    if not sp.unsubscribe(db, subscriber):
        return {"status": subscriber.status}
    sp.notify_unsubscribed(db, background, subscriber)
    return {"status": subscriber.status}
