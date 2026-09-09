"""Status Publication Service (ZST-EC-001 STS-001 -> STS-006).

**The invariant this module exists to enforce.** A public status email is only ever sent
from a COMMITTED public record. Every publish function follows the same shape:

    1. build the customer-safe public row/version
    2. db.commit()                      <- the public record is now durable
    3. append the append-only update row, and commit
    4. resolve subscriber eligibility from the committed row
    5. hand the fan-out to background tasks

`_publish()` is the single chokepoint for steps 2-5, so there is no path in which an email
precedes publication. If Resend fails, the record stays published - the fan-out is wrapped
and its failure is logged, never propagated. Tests assert both directions: an internal
`Incident` alone mails nobody, and a provider outage leaves the public row intact.

**Internal and public are different vocabularies, not different views.**
`platform_ops.Incident` keeps `detail`, `commander`, `severity` and `kind`; none of them has
a parameter in any sender here. `PublicStatusIncident` carries only an approved title, an
impact level, components, regions and operator-authored body text. Nothing serializes one
into the other.

**Published history is append-only.** `publish_update()` and `publish_correction()` INSERT;
nothing in this module updates a `PublicStatusIncidentUpdate` after insert. A correction
carries `correction_of_version`, so the statement it corrects stays readable.

**Subscriber filtering is real.** `eligible_subscribers()` intersects the subscriber's
selected components and regions with the incident's. An empty selection means "all" - which
is the only way somebody receives everything, so a component-scoped subscriber is never
mailed about an unrelated outage.

**Nothing is invented.** `MAINTENANCE_REMINDER_HOURS` is empty, so no reminder fires.
`started_at` is what makes a Started notice truthful - the clock reaching `starts_at_utc`
is a different fact from work beginning. Emergency work is a distinct `kind`, never a
short-notice "scheduled maintenance".
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    COMPONENT_KEYS,
    COMPONENT_LABELS,
    EMERGENCY_REASON_LABELS,
    EMERGENCY_REASONS,
    MAINTENANCE_KINDS,
    MAINTENANCE_REMINDER_HOURS,
    NOTIFY_KINDS,
    PUBLIC_IMPACT_LABELS,
    PUBLIC_IMPACT_LEVELS,
    PUBLIC_INCIDENT_STATUSES,
    PURPOSE_STATUS_SUBSCRIPTION,
    REGION_KEYS,
    REGION_LABELS,
    STATUS_COMPONENTS,
    SUBSCRIPTION_TOKEN_TTL_HOURS,
    PublicStatusIncident,
    PublicStatusIncidentUpdate,
    ScheduledMaintenance,
    StatusComponent,
    StatusNotice,
    StatusSubscriber,
)

log = logging.getLogger(__name__)

_TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(moment: datetime | None) -> datetime | None:
    """Render a stored timestamp in UTC.

    The columns are `timestamptz`, so the instant is never ambiguous - but psycopg renders
    it in the connection's timezone, which here is not UTC. A field named `starts_at_utc`
    that serializes with a +05:30 offset is a lie about the canonical record, so every read
    path normalizes instead of trusting the driver's rendering.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def status_url() -> str:
    """The PUBLIC status page. No authentication, and never an admin path."""
    return f"{email_mod.public_base_url()}/status"


def manage_url(token: str) -> str:
    return f"{status_url()}/preferences?t={token}"


def _reference(prefix: str, db: Session, model, column) -> str:
    stem = f"{prefix}-{_now().year}-"
    used = db.scalar(select(func.count(model.id)).where(column.like(f"{stem}%"))) or 0
    return f"{stem}{used + 1:06d}"


def seed_components(db: Session) -> int:
    """Register the real customer-facing components. Invents none."""
    added = 0
    for order, (key, label) in enumerate(STATUS_COMPONENTS):
        if db.scalar(select(StatusComponent).where(StatusComponent.key == key)) is not None:
            continue
        db.add(StatusComponent(key=key, label=label, display_order=order,
                               current_impact="none"))
        added += 1
    if added:
        db.commit()
    return added


# ══ the publication chokepoint ══════════════════════════════════════════════════════════

def _claim(db: Session, *, kind: str, subject_type: str, subject_id, version: int,
           recipients: int = 0, detail: str | None = None) -> bool:
    existing = db.scalar(
        select(StatusNotice).where(StatusNotice.kind == kind,
                                   StatusNotice.subject_type == subject_type,
                                   StatusNotice.subject_id == subject_id,
                                   StatusNotice.version == version))
    if existing is not None:
        return False
    try:
        db.add(StatusNotice(kind=kind, subject_type=subject_type, subject_id=subject_id,
                            version=version, recipients=recipients, detail=detail))
        db.commit()
        return True
    except Exception:  # noqa: BLE001 - a lost uniqueness race IS a successful dedup
        db.rollback()
        log.info("Status notice %s/%s v%s already claimed", kind, subject_id, version)
        return False


def _fan_out(background, send, people, **kwargs) -> int:
    """Hand the fan-out to background tasks. Returns how many were queued.

    Wrapped so a provider or link failure can never propagate back into the caller - by the
    time this runs the public record is already committed, and it must stay that way.
    """
    queued = 0
    try:
        for address, token in people:
            background.add_task(send, address, manage_url=manage_url(token), **kwargs)
            queued += 1
    except UnsafeLinkError:
        log.exception("Status notice not queued: APP_URL unsafe for this environment")
    except Exception:  # noqa: BLE001 - publication stands regardless of delivery
        log.exception("Status fan-out failed after publication")
    return queued


class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a notice must never unpublish a status record
            log.exception("Status notice failed")


# ══ STS-001 — subscription lifecycle ═══════════════════════════════════════════════════

def _clean_selection(values, allowed) -> list[str]:
    """Keep only real components/regions. An unknown key is dropped rather than stored,
    so a subscriber's preferences can never reference a taxonomy that does not exist."""
    return [v for v in dict.fromkeys(values or []) if v in allowed]


def subscribe(db: Session, *, email: str, components=None, regions=None,
              notify_kinds=None) -> tuple[StatusSubscriber | None, str | None]:
    """Create or re-open a subscription. Returns (subscriber, raw_verification_token).

    Never auto-subscribes anybody: this is only reached from an explicit request carrying an
    address. Re-subscribing supersedes any outstanding token.
    """
    address = (email or "").strip().lower()
    if not address or "@" not in address:
        return None, None
    subscriber = db.scalar(
        select(StatusSubscriber).where(StatusSubscriber.email == address))
    if subscriber is None:
        subscriber = StatusSubscriber(email=address)
        db.add(subscriber)

    subscriber.components = _clean_selection(components, COMPONENT_KEYS)
    subscriber.regions = _clean_selection(regions, REGION_KEYS)
    subscriber.notify_kinds = _clean_selection(notify_kinds, NOTIFY_KINDS) or list(
        NOTIFY_KINDS)
    subscriber.status = "pending_verification"
    subscriber.unsubscribed_at = None

    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    subscriber.verification_superseded_at = None
    subscriber.verification_purpose = PURPOSE_STATUS_SUBSCRIPTION
    subscriber.verification_token_hash = _hash(raw)
    subscriber.verification_expires_at = _now() + timedelta(
        hours=SUBSCRIPTION_TOKEN_TTL_HOURS)
    subscriber.verification_consumed_at = None
    subscriber.verification_version = (subscriber.verification_version or 0) + 1
    db.commit()
    db.refresh(subscriber)
    return subscriber, raw


def confirm(db: Session, *, token: str,
            purpose: str = PURPOSE_STATUS_SUBSCRIPTION
            ) -> tuple[StatusSubscriber | None, str, str | None]:
    """Redeem a verification token. Returns (subscriber, outcome, raw_manage_token)."""
    if not token:
        return None, "invalid", None
    subscriber = db.scalar(
        select(StatusSubscriber).where(
            StatusSubscriber.verification_token_hash == _hash(token)))
    if subscriber is None or subscriber.verification_purpose != purpose:
        return None, "invalid", None
    if subscriber.verification_consumed_at is not None:
        return None, "already_used", None
    if subscriber.verification_superseded_at is not None:
        return None, "superseded", None
    if (subscriber.verification_expires_at is None
            or subscriber.verification_expires_at < _now()):
        return None, "expired", None

    subscriber.verification_consumed_at = _now()
    subscriber.verified_at = _now()
    subscriber.status = "active"
    manage = secrets.token_urlsafe(_TOKEN_BYTES)
    subscriber.manage_token_hash = _hash(manage)
    db.commit()
    db.refresh(subscriber)
    return subscriber, "active", manage


def update_preferences(db: Session, subscriber: StatusSubscriber, *, components=None,
                       regions=None, notify_kinds=None) -> tuple[bool, dict]:
    """Commit a preference change. Returns (changed, previous).

    A no-op returns False, so re-saving the same selection announces nothing.
    """
    previous = {"components": list(subscriber.components or []),
                "regions": list(subscriber.regions or []),
                "notify_kinds": list(subscriber.notify_kinds or [])}
    proposed = {
        "components": _clean_selection(
            components if components is not None else previous["components"],
            COMPONENT_KEYS),
        "regions": _clean_selection(
            regions if regions is not None else previous["regions"], REGION_KEYS),
        "notify_kinds": _clean_selection(
            notify_kinds if notify_kinds is not None else previous["notify_kinds"],
            NOTIFY_KINDS),
    }
    if all(set(proposed[k]) == set(previous[k]) for k in proposed):
        return False, previous
    subscriber.components = proposed["components"]
    subscriber.regions = proposed["regions"]
    subscriber.notify_kinds = proposed["notify_kinds"]
    subscriber.preference_version = (subscriber.preference_version or 0) + 1
    db.commit()
    return True, previous


def unsubscribe(db: Session, subscriber: StatusSubscriber) -> bool:
    """Stop STATUS mail only.

    This touches nothing in `Organization.notifications` and no account, security, billing
    or privacy channel - status is its own communication domain, and a status preference
    must never be able to silence a security alert.
    """
    if subscriber.status == "unsubscribed":
        return False
    subscriber.status = "unsubscribed"
    subscriber.unsubscribed_at = _now()
    db.commit()
    return True


def _labels(keys, mapping, empty="All") -> list[str]:
    if not keys:
        return [empty]
    return [mapping.get(k, k) for k in keys]


def notify_verify(db: Session, background, subscriber: StatusSubscriber,
                  raw_token: str) -> bool:
    if subscriber.status != "pending_verification" or not raw_token:
        return False
    if not _claim(db, kind="subscription_verify", subject_type="subscriber",
                  subject_id=subscriber.id, version=subscriber.verification_version):
        return False
    try:
        background.add_task(
            email_mod.send_status_verify_email, subscriber.email,
            components=_labels(subscriber.components, COMPONENT_LABELS, "All components"),
            regions=_labels(subscriber.regions, REGION_LABELS, "All regions"),
            expires_at=email_mod.billing_date(subscriber.verification_expires_at),
            confirm_url=f"{status_url()}/subscribe/confirm?t={raw_token}",
            status_url=status_url())
    except Exception:  # noqa: BLE001
        log.exception("STS-001 verification notice failed")
    return True


def notify_confirmed(db: Session, background, subscriber: StatusSubscriber,
                     manage_token: str) -> bool:
    if subscriber.status != "active":
        return False
    if not _claim(db, kind="subscription_confirmed", subject_type="subscriber",
                  subject_id=subscriber.id, version=subscriber.verification_version):
        return False
    _fan_out(background, email_mod.send_status_confirmed_email,
             [(subscriber.email, manage_token)],
             components=_labels(subscriber.components, COMPONENT_LABELS, "All components"),
             regions=_labels(subscriber.regions, REGION_LABELS, "All regions"),
             notify_kinds=[k.title() for k in (subscriber.notify_kinds or [])],
             status_url=status_url())
    return True


def notify_preferences_changed(db: Session, background, subscriber: StatusSubscriber, *,
                               previous: dict, manage_token: str) -> bool:
    if not _claim(db, kind="preferences_changed", subject_type="subscriber",
                  subject_id=subscriber.id, version=subscriber.preference_version):
        return False
    _fan_out(background, email_mod.send_status_preferences_email,
             [(subscriber.email, manage_token)],
             previous_components=_labels(previous.get("components"), COMPONENT_LABELS,
                                          "All components"),
             previous_regions=_labels(previous.get("regions"), REGION_LABELS,
                                       "All regions"),
             current_components=_labels(subscriber.components, COMPONENT_LABELS,
                                         "All components"),
             current_regions=_labels(subscriber.regions, REGION_LABELS, "All regions"),
             status_url=status_url())
    return True


def notify_unsubscribed(db: Session, background, subscriber: StatusSubscriber) -> bool:
    if subscriber.status != "unsubscribed":
        return False
    if not _claim(db, kind="unsubscribed", subject_type="subscriber",
                  subject_id=subscriber.id, version=subscriber.preference_version):
        return False
    try:
        background.add_task(
            email_mod.send_status_unsubscribed_email, subscriber.email,
            # Stated explicitly so nobody believes they have opted out of security or
            # billing mail by leaving the status list.
            scope_note=("This only stops status page updates. Account, security, billing "
                        "and privacy emails are separate and are not affected."),
            status_url=status_url())
    except Exception:  # noqa: BLE001
        log.exception("STS-001 unsubscribe notice failed")
    return True


# ══ subscriber eligibility ══════════════════════════════════════════════════════════════

def eligible_subscribers(db: Session, *, components: list[str], regions: list[str],
                         kind: str) -> list[tuple[str, str]]:
    """ACTIVE subscribers whose preferences intersect the affected scope.

    An empty component or region selection means "all" - the only way somebody receives
    everything. A subscriber scoped to one component is therefore never mailed about an
    unrelated outage, which is the filtering STS-002 requires.

    Returns (email, manage_token_placeholder). The manage token hash is stored, not the raw
    value, so the link carries a per-send opaque handle rather than the address.
    """
    rows = db.scalars(
        select(StatusSubscriber).where(StatusSubscriber.status == "active",
                                       StatusSubscriber.unsubscribed_at.is_(None))).all()
    out = []
    affected_components = set(components or [])
    affected_regions = set(regions or [])
    for row in rows:
        wanted = set(row.notify_kinds or [])
        if kind not in wanted:
            continue
        chosen_components = set(row.components or [])
        if chosen_components and affected_components:
            if not (chosen_components & affected_components):
                continue
        chosen_regions = set(row.regions or [])
        if chosen_regions and affected_regions:
            # "global" impact reaches every region subscriber.
            if "global" not in affected_regions and not (chosen_regions & affected_regions):
                continue
        out.append((row.email, row.manage_token_hash or ""))
    return out


# ══ STS-002 / STS-003 — public incidents ═══════════════════════════════════════════════

def open_public_incident(db: Session, *, title: str, impact: str,
                         components: list[str], regions: list[str], body: str,
                         internal_incident_id=None, customer_action: str | None = None,
                         next_update_at: datetime | None = None,
                         published_by=None) -> PublicStatusIncident | None:
    """Publish an INVESTIGATING incident.

    Step 1 and 2 of the invariant: the public row and its first append-only update are
    committed here. No email is sent by this function - `notify_incident()` is a separate
    call, which is what makes the ordering testable.
    """
    if impact not in PUBLIC_IMPACT_LEVELS:
        return None
    clean_components = _clean_selection(components, COMPONENT_KEYS)
    clean_regions = _clean_selection(regions, REGION_KEYS)
    if not clean_components:
        # Component impact is never guessed: a publication has to name what it affects.
        return None

    incident = PublicStatusIncident(
        title=title, status="investigating", impact=impact,
        affected_components=clean_components, affected_regions=clean_regions,
        current_update=body, customer_action=customer_action,
        next_update_at=next_update_at, internal_incident_id=internal_incident_id,
        started_at=_now(), published_at=_now(), version=1)
    incident.public_reference = _reference("STS", db, PublicStatusIncident,
                                           PublicStatusIncident.public_reference)
    db.add(incident)
    db.commit()
    db.refresh(incident)
    _append_update(db, incident, status="investigating", body=body,
                   update_type="status_update", published_by=published_by)
    return incident


def _append_update(db: Session, incident: PublicStatusIncident, *, status: str, body: str,
                   update_type: str = "status_update", correction_of: int | None = None,
                   published_by=None) -> PublicStatusIncidentUpdate:
    """INSERT one published update. Never an UPDATE of an existing row."""
    row = PublicStatusIncidentUpdate(
        public_incident_id=incident.id, version=incident.version, status=status, body=body,
        update_type=update_type, correction_of_version=correction_of,
        published_by=published_by)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def publish_update(db: Session, incident: PublicStatusIncident, *, status: str, body: str,
                   impact: str | None = None, next_update_at: datetime | None = None,
                   residual_work: bool | None = None,
                   residual_summary: str | None = None,
                   published_by=None) -> bool:
    """Publish the next version of an incident.

    A RESOLVED incident moving back to an active state is a REOPEN: a new version with its
    own timestamp, leaving the resolution visible in history rather than editing it away.
    """
    if status not in PUBLIC_INCIDENT_STATUSES:
        return False
    reopening = incident.status == "resolved" and status != "resolved"

    incident.version = (incident.version or 0) + 1
    incident.status = status
    incident.current_update = body
    if impact is not None and impact in PUBLIC_IMPACT_LEVELS:
        incident.impact = impact
    incident.next_update_at = next_update_at
    if residual_work is not None:
        incident.residual_work = residual_work
        incident.residual_summary = residual_summary
    if status == "identified" and incident.identified_at is None:
        incident.identified_at = _now()
    if status == "monitoring" and incident.monitoring_at is None:
        incident.monitoring_at = _now()
    if status == "resolved":
        incident.resolved_at = _now()
    if reopening:
        incident.reopened_at = _now()
        # `resolved_at` is deliberately left in place: the earlier resolution is a published
        # fact, and the reopen notice quotes it.
    incident.published_at = _now()
    db.commit()
    _append_update(db, incident, status=status, body=body, update_type="status_update",
                   published_by=published_by)
    return True


def publish_correction(db: Session, incident: PublicStatusIncident, *,
                       corrects_version: int, body: str,
                       published_by=None) -> PublicStatusIncidentUpdate | None:
    """Append a correction. The corrected statement is left exactly as published."""
    original = db.scalar(
        select(PublicStatusIncidentUpdate).where(
            PublicStatusIncidentUpdate.public_incident_id == incident.id,
            PublicStatusIncidentUpdate.version == corrects_version))
    if original is None:
        return None
    incident.version = (incident.version or 0) + 1
    incident.published_at = _now()
    db.commit()
    return _append_update(db, incident, status=incident.status, body=body,
                          update_type="correction", correction_of=corrects_version,
                          published_by=published_by)


def publish_review(db: Session, incident: PublicStatusIncident, *, body: str,
                   published_by=None) -> PublicStatusIncidentUpdate | None:
    """Publish an APPROVED customer-facing post-incident review.

    Only reachable for a resolved incident, and it is this call - not a draft anywhere -
    that sets `review_published`, so the resolved notice never advertises a review that does
    not exist.
    """
    if incident.status != "resolved":
        return None
    incident.version = (incident.version or 0) + 1
    incident.review_published = True
    incident.published_at = _now()
    db.commit()
    return _append_update(db, incident, status=incident.status, body=body,
                          update_type="post_incident_review", published_by=published_by)


def published_history(db: Session, incident: PublicStatusIncident
                      ) -> list[PublicStatusIncidentUpdate]:
    """Every published update, oldest first. Nothing is filtered or rewritten."""
    return db.scalars(
        select(PublicStatusIncidentUpdate).where(
            PublicStatusIncidentUpdate.public_incident_id == incident.id)
        .order_by(PublicStatusIncidentUpdate.version)).all()


def next_update_note(row) -> str:
    """Stored commitment or the honest fallback. Nothing derives a time."""
    moment = getattr(row, "next_update_at", None)
    if moment is None:
        return "We will post another update when we have more information."
    return f"Next update by {email_mod.billing_date(moment)}."


def _incident_shared(incident: PublicStatusIncident) -> dict:
    return {
        "reference": incident.public_reference or "Not assigned",
        "title": incident.title,
        "impact": PUBLIC_IMPACT_LABELS.get(incident.impact, "Under assessment"),
        "components": _labels(incident.affected_components, COMPONENT_LABELS),
        "regions": _labels(incident.affected_regions, REGION_LABELS, "All regions"),
        "started_at": email_mod.billing_date(incident.started_at),
        "next_update": next_update_note(incident),
        "status_url": status_url(),
    }


_INCIDENT_KIND = {
    "investigating": "incident_investigating",
    "identified": "incident_identified",
    "monitoring": "incident_monitoring",
    "resolved": "incident_resolved",
}


def notify_incident(db: Session, background, incident: PublicStatusIncident,
                    *, reopened: bool = False) -> str | None:
    """Fan out ONE published incident version to eligible subscribers.

    Steps 4 and 5 of the invariant. By the time this runs the public row and its update are
    already committed, so a delivery failure cannot unpublish anything.
    """
    kind = "incident_reopened" if reopened else _INCIDENT_KIND.get(incident.status)
    if kind is None:
        return None
    if incident.published_at is None or not incident.public_reference:
        # Not published: nothing to announce. This is the guard that makes an internal-only
        # incident silent.
        return None

    people = eligible_subscribers(db, components=incident.affected_components or [],
                                  regions=incident.affected_regions or [],
                                  kind="incidents")
    if not _claim(db, kind=kind, subject_type="public_incident", subject_id=incident.id,
                  version=incident.version, recipients=len(people),
                  detail=incident.status):
        return None
    if not people:
        return kind

    _fan_out(background, email_mod.send_status_incident_email, people,
             variant=kind,
             body=incident.current_update or "We are looking into it.",
             customer_action=incident.customer_action,
             identified_at=(email_mod.billing_date(incident.identified_at)
                            if incident.identified_at else None),
             monitoring_at=(email_mod.billing_date(incident.monitoring_at)
                            if incident.monitoring_at else None),
             resolved_at=(email_mod.billing_date(incident.resolved_at)
                          if incident.resolved_at else None),
             reopened_at=(email_mod.billing_date(incident.reopened_at)
                          if incident.reopened_at else None),
             residual=(incident.residual_summary if incident.residual_work else None),
             review_available=bool(incident.review_published),
             **_incident_shared(incident))
    return kind


def notify_residual(db: Session, background, incident: PublicStatusIncident) -> bool:
    """Service restored, follow-up work continuing. Only from the real flag."""
    if not incident.residual_work or not incident.residual_summary:
        return False
    if incident.status not in ("monitoring", "resolved"):
        return False
    people = eligible_subscribers(db, components=incident.affected_components or [],
                                  regions=incident.affected_regions or [],
                                  kind="incidents")
    if not _claim(db, kind="incident_residual", subject_type="public_incident",
                  subject_id=incident.id, version=incident.version,
                  recipients=len(people)):
        return False
    if not people:
        return True
    _fan_out(background, email_mod.send_status_residual_email, people,
             body=incident.residual_summary,
             # Stated plainly: follow-up engineering work is not continued impact.
             impact_note=("Your service is restored. The remaining work is on our side and "
                          "should not affect you."),
             **_incident_shared(incident))
    return True


def notify_correction(db: Session, background, incident: PublicStatusIncident,
                      correction: PublicStatusIncidentUpdate) -> bool:
    if correction.update_type != "correction":
        return False
    original = db.scalar(
        select(PublicStatusIncidentUpdate).where(
            PublicStatusIncidentUpdate.public_incident_id == incident.id,
            PublicStatusIncidentUpdate.version == correction.correction_of_version))
    if original is None:
        return False
    people = eligible_subscribers(db, components=incident.affected_components or [],
                                  regions=incident.affected_regions or [],
                                  kind="incidents")
    if not _claim(db, kind="incident_correction", subject_type="public_incident",
                  subject_id=incident.id, version=correction.version,
                  recipients=len(people)):
        return False
    if not people:
        return True
    _fan_out(background, email_mod.send_status_correction_email, people,
             # Both statements are shown. The original is quoted from the preserved row.
             previously_reported=original.body,
             corrected=correction.body,
             corrected_at=email_mod.billing_date(correction.published_at),
             **_incident_shared(incident))
    return True


def notify_review(db: Session, background, incident: PublicStatusIncident,
                  review: PublicStatusIncidentUpdate) -> bool:
    """Only for a published, approved review - never a draft."""
    if review.update_type != "post_incident_review" or not incident.review_published:
        return False
    people = eligible_subscribers(db, components=incident.affected_components or [],
                                  regions=incident.affected_regions or [],
                                  kind="incidents")
    if not _claim(db, kind="incident_review", subject_type="public_incident",
                  subject_id=incident.id, version=review.version,
                  recipients=len(people)):
        return False
    if not people:
        return True
    _fan_out(background, email_mod.send_status_review_email, people,
             summary=review.body,
             impact_period=(f"{email_mod.billing_date(incident.started_at)} to "
                            f"{email_mod.billing_date(incident.resolved_at)}"),
             published_at=email_mod.billing_date(review.published_at),
             **_incident_shared(incident))
    return True


# ══ STS-005 / STS-006 — maintenance ════════════════════════════════════════════════════

def schedule_maintenance(db: Session, *, title: str, components: list[str],
                         regions: list[str], starts_at_utc: datetime,
                         ends_at_utc: datetime, impact_summary: str,
                         kind: str = "scheduled", emergency_reason: str | None = None,
                         published_by=None) -> ScheduledMaintenance | None:
    """Publish a maintenance window.

    Timestamps are stored timezone-aware in UTC as the canonical record. Emergency work must
    declare `kind="emergency"` and a reason category - it is never presented as scheduled.
    """
    if kind not in MAINTENANCE_KINDS:
        return None
    if kind == "emergency" and emergency_reason not in EMERGENCY_REASONS:
        return None
    if not impact_summary or not impact_summary.strip():
        # Impact is never assumed - and never silently "no impact".
        return None
    clean_components = _clean_selection(components, COMPONENT_KEYS)
    if not clean_components:
        return None
    if starts_at_utc is None or ends_at_utc is None or ends_at_utc <= starts_at_utc:
        return None

    row = ScheduledMaintenance(
        title=title, kind=kind, status="scheduled",
        affected_components=clean_components,
        affected_regions=_clean_selection(regions, REGION_KEYS),
        # Normalized to UTC so the stored record is unambiguous regardless of the caller.
        starts_at_utc=starts_at_utc.astimezone(timezone.utc),
        ends_at_utc=ends_at_utc.astimezone(timezone.utc),
        impact_summary=impact_summary, emergency_reason=emergency_reason,
        published_at=_now(), version=1, reminders_sent=[])
    row.public_reference = _reference("MNT", db, ScheduledMaintenance,
                                      ScheduledMaintenance.public_reference)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def revise_maintenance(db: Session, row: ScheduledMaintenance, *,
                       starts_at_utc: datetime | None = None,
                       ends_at_utc: datetime | None = None,
                       impact_summary: str | None = None) -> tuple[bool, dict]:
    """Version a published schedule rather than mutating it.

    Any revision clears `reminders_sent`, so reminders for the old window cannot fire
    against the new one.
    """
    previous = {"starts_at_utc": row.starts_at_utc, "ends_at_utc": row.ends_at_utc,
                "impact_summary": row.impact_summary}
    changed = False
    if starts_at_utc is not None and starts_at_utc != row.starts_at_utc:
        row.previous_starts_at_utc = row.starts_at_utc
        row.starts_at_utc = starts_at_utc.astimezone(timezone.utc)
        changed = True
    if ends_at_utc is not None and ends_at_utc != row.ends_at_utc:
        row.previous_ends_at_utc = row.ends_at_utc
        row.ends_at_utc = ends_at_utc.astimezone(timezone.utc)
        changed = True
    if impact_summary is not None and impact_summary != row.impact_summary:
        row.impact_summary = impact_summary
        changed = True
    if not changed:
        return False, previous
    row.version = (row.version or 0) + 1
    row.published_at = _now()
    row.reminders_sent = []
    db.commit()
    return True, previous


def cancel_maintenance(db: Session, row: ScheduledMaintenance) -> bool:
    """Cancel a window and invalidate its outstanding reminders."""
    if row.status in ("canceled", "completed"):
        return False
    row.status = "canceled"
    row.canceled_at = _now()
    row.version = (row.version or 0) + 1
    # Any reminder threshold not yet fired must never fire for a cancelled window.
    row.reminders_sent = list(MAINTENANCE_REMINDER_HOURS)
    db.commit()
    return True


def start_maintenance(db: Session, row: ScheduledMaintenance) -> bool:
    """Record that work ACTUALLY began.

    Not the clock reaching `starts_at_utc`: that is a different fact, and a Started notice
    sent because a time passed would be a claim nobody verified.
    """
    if row.status not in ("scheduled",):
        return False
    row.status = "in_progress"
    row.started_at = _now()
    row.version = (row.version or 0) + 1
    db.commit()
    return True


def extend_maintenance(db: Session, row: ScheduledMaintenance, *,
                       new_end_utc: datetime) -> tuple[bool, datetime | None]:
    """Extend an in-progress window. Requires a real, later end time."""
    if row.status not in ("in_progress", "extended"):
        return False, None
    if new_end_utc is None or row.ends_at_utc is None:
        return False, None
    if new_end_utc <= row.ends_at_utc:
        return False, None
    previous = row.ends_at_utc
    row.previous_ends_at_utc = previous
    row.ends_at_utc = new_end_utc.astimezone(timezone.utc)
    row.status = "extended"
    row.version = (row.version or 0) + 1
    db.commit()
    return True, previous


def complete_maintenance(db: Session, row: ScheduledMaintenance, *,
                         remaining_work: str | None = None) -> bool:
    if row.status not in ("in_progress", "extended"):
        return False
    row.status = "completed"
    row.completed_at = _now()
    row.remaining_work = remaining_work
    row.version = (row.version or 0) + 1
    db.commit()
    return True


def reminder_due(row: ScheduledMaintenance,
                 now: datetime | None = None) -> int | None:
    """Which configured reminder offset is due, or None.

    MAINTENANCE_REMINDER_HOURS is empty in this product, so this always returns None and no
    reminder is sent. There is no fallback 24h/1h policy anywhere.
    """
    if not MAINTENANCE_REMINDER_HOURS:
        return None
    if row.status != "scheduled" or row.starts_at_utc is None:
        return None
    moment = now or _now()
    already = set(row.reminders_sent or [])
    for hours in sorted(MAINTENANCE_REMINDER_HOURS):
        if hours in already:
            continue
        threshold = row.starts_at_utc - timedelta(hours=hours)
        if threshold <= moment < row.starts_at_utc:
            return hours
    return None


def health_statement(db: Session, row: ScheduledMaintenance) -> str:
    """What may be said about service health after maintenance.

    Reads the components' own recorded impact rather than asserting everything is fine: a
    completed window does not by itself prove the platform is healthy.
    """
    keys = list(row.affected_components or [])
    if not keys:
        return "Affected services are back in service."
    rows = db.scalars(
        select(StatusComponent).where(StatusComponent.key.in_(keys))).all()
    degraded = [r.label for r in rows if r.current_impact != "none"]
    if degraded:
        return ("These services are back in service, but our status page still shows "
                f"reduced service for: {', '.join(degraded)}.")
    return "The affected services are operating normally on our status page."


def _maintenance_shared(row: ScheduledMaintenance) -> dict:
    return {
        "reference": row.public_reference or "Not assigned",
        "title": row.title,
        "components": _labels(row.affected_components, COMPONENT_LABELS),
        "regions": _labels(row.affected_regions, REGION_LABELS, "All regions"),
        # UTC is the canonical record and is labelled as such.
        "starts_at": email_mod.billing_date(row.starts_at_utc),
        "ends_at": email_mod.billing_date(row.ends_at_utc),
        "impact": row.impact_summary or "Under assessment",
        "emergency": row.kind == "emergency",
        "next_update": next_update_note(row),
        "status_url": status_url(),
        "local_note": ("All times are shown in UTC, which is how we record maintenance "
                       "windows."),
    }


def notify_maintenance(db: Session, background, row: ScheduledMaintenance, *,
                       variant: str, previous: dict | None = None,
                       previous_end: datetime | None = None) -> str | None:
    """Fan out one maintenance transition to eligible subscribers."""
    if row.published_at is None or not row.public_reference:
        return None
    people = eligible_subscribers(db, components=row.affected_components or [],
                                  regions=row.affected_regions or [],
                                  kind="maintenance")
    if not _claim(db, kind=variant, subject_type="maintenance", subject_id=row.id,
                  version=row.version, recipients=len(people), detail=row.status):
        return None
    if not people:
        return variant

    _fan_out(background, email_mod.send_status_maintenance_email, people,
             variant=variant,
             state=(row.status or "").replace("_", " ").title(),
             started_at=(email_mod.billing_date(row.started_at)
                         if row.started_at else None),
             completed_at=(email_mod.billing_date(row.completed_at)
                           if row.completed_at else None),
             canceled_at=(email_mod.billing_date(row.canceled_at)
                          if row.canceled_at else None),
             previous_start=(email_mod.billing_date(previous.get("starts_at_utc"))
                             if previous and previous.get("starts_at_utc") else None),
             previous_end=(email_mod.billing_date(previous_end or (
                 previous.get("ends_at_utc") if previous else None))
                 if (previous_end or (previous and previous.get("ends_at_utc"))) else None),
             previous_impact=(previous.get("impact_summary") if previous else None),
             emergency_reason=(EMERGENCY_REASON_LABELS.get(row.emergency_reason or "other")
                               if row.kind == "emergency" else None),
             # Read from component state, never asserted.
             health=health_statement(db, row),
             remaining_work=row.remaining_work,
             **_maintenance_shared(row))
    return variant


# ══ sweeper ════════════════════════════════════════════════════════════════════════════

def sweep(db: Session, background=None) -> dict:
    """Deadline-driven status obligations, on the existing leader-elected ticker.

    With MAINTENANCE_REMINDER_HOURS empty this is a no-op by design; the loop exists so
    configuring a real policy switches reminders on without new infrastructure.
    """
    background = background or _Bg()
    counts = {"maintenance_reminders": 0}
    if not MAINTENANCE_REMINDER_HOURS:
        return counts
    try:
        for row in db.scalars(
            select(ScheduledMaintenance).where(
                ScheduledMaintenance.status == "scheduled")).all():
            hours = reminder_due(row)
            if hours is None:
                continue
            row.reminders_sent = list(row.reminders_sent or []) + [hours]
            db.commit()
            if notify_maintenance(db, background, row, variant="maintenance_reminder"):
                counts["maintenance_reminders"] += 1
    except Exception:  # noqa: BLE001 - a bad sweep must not kill the ticker
        log.exception("STS-005 maintenance reminder sweep failed")
    return counts
