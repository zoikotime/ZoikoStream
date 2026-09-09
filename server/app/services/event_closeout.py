"""Event-day brief, completion/replay and post-event evidence
(ZST-EC-001 LVE-007, LVE-011, LVE-012).

**The recording/replay boundary is the whole point of LVE-011.** Five different facts get
collapsed into "the recording is ready" by careless copy, and this module keeps them apart:

    broadcast ended      -> Event.status == "ended"
    recording stopped    -> LiveRecording.status == "stopped"
    recording finalized  -> LiveRecording.validation_status in (valid, degraded)   [MED-008]
    replay prepared      -> ReplayEntitlement.publish_state == "ready_for_review"  [MED-009]
    replay published     -> ReplayEntitlement.publish_state == "published"

`recording_position()` reads those and returns a sentence that never runs ahead of them. An
event that has just ended says recording is still processing, because at that moment it is.

**Privacy.** No k-anonymity, cohort-minimum or suppression policy exists anywhere in this
repository - searched for, and absent. LVE-012 therefore reports only whole-event totals and
never a per-segment breakdown, `privacy_suppression_applied` is recorded FALSE rather than
asserted, and the family is reported PARTIAL. Inventing a "minimum 5 viewers" rule would be
exactly the fabricated policy the spec forbids.

**Retention is MED-011's.** This module reads `LiveRecording.retention_expires_at`,
`legal_hold` and `deletion_status`; it keeps no second retention state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..models import (
    Event,
    EventBrief,
    EventCompletionState,
    EventIncident,
    EventReport,
    LiveRecording,
    Organization,
    PostEventReport,
    User,
)
from . import event_comms
from .event_comms import _Bg, _claim, _ledger, _queue, event_timestamp, is_test_org

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _may_send(family: str, org) -> bool:
    from . import notifications

    return notifications.should_send_operational_notification(family=family, org=org)


def _org(db: Session, event: Event) -> Organization | None:
    return db.get(Organization, event.org_id) if event else None


def stakeholders(db: Session, event: Event) -> event_comms.Recipients:
    """Event owner + assigned team. Never the purchaser or billing contact.

    LVE-011 is explicit that a replay notice must not be addressed to whoever paid the
    invoice; the existing publish path did exactly that via `_order_contact`.
    """
    from . import event_ops

    return event_ops.owner_and_team(db, event)


# ══ LVE-007 — final event-day brief ═════════════════════════════════════════════════════

def build_content(db: Session, event: Event) -> dict:
    """Freeze the operational picture at approval time, from committed state only."""
    from . import event_ops, event_planning

    org = _org(db, event)
    members = event_comms.team(db, event)
    return {
        "event": event.title or "your event",
        "starts": event_timestamp(event, event.start_time, org),
        "ends": event_timestamp(event, event.end_time, org),
        "timezone": event_comms.event_zone(event, org)[1],
        "delivery": event_comms.DELIVERY_MODEL,
        "audience_access": event_comms._access_model(event),
        "contributors": event_comms._contributor_plan(db, event),
        "recording": "Recording enabled" if event.recording_enabled else "Recording not enabled",
        "replay": event_comms._replay_policy(db, event),
        # Accessibility has no backing domain (MED-010 unsupported), so the brief states the
        # arrangement position rather than describing a capability the platform lacks.
        "accessibility": event_planning.ACCESSIBILITY_NOTE,
        "team": [f"{role.title()}: {name}" for role, name, _ in members] or ["Not yet assigned"],
        "escalation": (event_ops.owner_and_team(db, event).addresses() or ["Not recorded"])[0],
    }


def create_brief(db: Session, event: Event, *, actor_id=None) -> EventBrief:
    """Open a new DRAFT brief at the next version."""
    latest = db.scalar(
        select(EventBrief).where(EventBrief.event_id == event.id)
        .order_by(EventBrief.version.desc()))
    brief = EventBrief(event_id=event.id, org_id=event.org_id,
                       version=(latest.version + 1) if latest else 1,
                       status="draft", created_by=actor_id)
    db.add(brief)
    db.commit()
    db.refresh(brief)
    return brief


def approve_brief(db: Session, event: Event, brief: EventBrief, *, actor_id=None) -> bool:
    """Approve one version, superseding any previously approved one."""
    if brief.status != "draft":
        return False
    for other in db.scalars(
        select(EventBrief).where(EventBrief.event_id == event.id,
                                 EventBrief.status == "approved")).all():
        other.status = "superseded"
        other.superseded_at = _now()
    brief.content = build_content(db, event)
    brief.status = "approved"
    brief.approved_by = actor_id
    brief.approved_at = _now()
    db.commit()
    return True


def supersede_for_schedule_change(db: Session, event: Event) -> str:
    """Called by LVE-008: an approved brief describes a day that has now moved."""
    approved = db.scalars(
        select(EventBrief).where(EventBrief.event_id == event.id,
                                 EventBrief.status == "approved")).all()
    if not approved:
        return "no approved brief to review"
    for brief in approved:
        brief.status = "superseded"
        brief.superseded_at = _now()
    db.commit()
    return (f"{len(approved)} approved brief(s) superseded and must be re-approved against "
            f"the new schedule")


def notify_brief_approved(db: Session, background, brief: EventBrief) -> bool:
    """One message per APPROVED version. A draft announces nothing."""
    if brief.status != "approved":
        return False
    if not _claim(db, brief, "approved_notified_at"):
        return False
    event = db.get(Event, brief.event_id)
    org = _org(db, event) if event else None
    if event is None or not _may_send("LVE-007", org):
        return False
    people = stakeholders(db, event)
    if not people.addresses():
        return False

    content = brief.content or {}
    _ledger(db, org_id=brief.org_id, family="LVE-007", transition="brief_approved",
            event_id=event.id, detail=f"v{brief.version}")
    _queue(background, email_mod.send_event_brief_email, people,
           event_title=event.title or "your event",
           version=brief.version,
           approved_at=event_timestamp(event, brief.approved_at, org),
           starts=content.get("starts", "Not set"),
           zone=content.get("timezone", "UTC"),
           local_note=event_comms.LOCAL_TIME_NOTE,
           delivery=content.get("delivery", "Not recorded"),
           audience_access=content.get("audience_access", "Not recorded"),
           contributors=content.get("contributors", "Not recorded"),
           recording=content.get("recording", "Not recorded"),
           replay=content.get("replay", "Not recorded"),
           accessibility=content.get("accessibility", "Not recorded"),
           team=content.get("team") or ["Not yet assigned"],
           escalation=content.get("escalation", "Not recorded"),
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


# ══ LVE-011 — completion and replay ═════════════════════════════════════════════════════

def _completion_row(db: Session, event: Event) -> EventCompletionState:
    row = db.scalar(
        select(EventCompletionState).where(EventCompletionState.event_id == event.id))
    if row is None:
        row = EventCompletionState(event_id=event.id, org_id=event.org_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def recording_position(db: Session, event: Event) -> str:
    """Exactly where the recording is, never further along than the evidence supports."""
    recordings = db.scalars(
        select(LiveRecording).where(LiveRecording.event_id == event.id)).all()
    if not recordings:
        return "No recording was captured for this event"
    if any(r.status == "recording" for r in recordings):
        return "Recording is still in progress"
    if any(r.status == "failed" for r in recordings):
        return "Recording failed; our team is reviewing it"
    finalized = [r for r in recordings if r.validation_status in ("valid", "degraded")]
    if not finalized:
        # Stopped is NOT finalized. MED-008's validation has not confirmed the file yet, so
        # nothing here may describe the recording as complete or available.
        return "Recording processing is still in progress"
    if any(r.validation_status == "degraded" for r in finalized):
        return "Recording finalized with some media missing"
    return "Recording finalized and validated"


def replay_position(db: Session, event: Event) -> tuple[str | None, str]:
    """(publish_state, human sentence) for the audience replay."""
    from ..crud import commercial as commercial_crud

    entitlement = commercial_crud.get_replay_entitlement(db, event.id, scope="audience")
    if entitlement is None:
        return None, "No replay has been prepared yet"
    state = entitlement.publish_state
    return state, {
        "not_available": "No replay is available yet",
        "validating": "The replay source is being validated",
        "ready_for_review": "The replay is prepared and awaiting your review",
        "published": "The replay is published",
        "withheld": "The replay has been withdrawn",
        "expired": "Replay availability has expired",
        "deleted_preserved": "The replay media was deleted; its record is preserved",
    }.get(state, f"Replay state: {state}")


def notify_event_ended(db: Session, background, event: Event) -> bool:
    """Announce authoritative event completion.

    Deliberately says where the RECORDING actually is rather than implying it is ready. At
    the moment an event ends, egress has usually not even finished uploading.
    """
    if event.status != "ended":
        return False
    row = _completion_row(db, event)
    if not _claim(db, row, "ended_notified_at"):
        return False
    org = _org(db, event)
    if not _may_send("LVE-011", org):
        return False
    people = stakeholders(db, event)
    if not people.addresses():
        return False

    # CON-005. The event ending IS an authoritative session close - unlike a websocket drop.
    try:
        from . import contributor_access

        contributor_access.end_sessions_for_event(db, event, background)
    except Exception:  # noqa: BLE001 - completion must not fail on a contributor notice
        log.exception("CON-005 session close failed for event %s", event.id)

    _ledger(db, org_id=event.org_id, family="LVE-011", transition="event_ended",
            event_id=event.id)
    _queue(background, email_mod.send_event_ended_email, people,
           event_title=event.title or "your event",
           reference=str(event.id)[:8],
           ended_at=event_timestamp(event, event.end_time, org),
           duration=event_comms._duration_between(event.start_time, event.end_time),
           final_state=(event.status or "ended").title(),
           recording_status=recording_position(db, event),
           replay_next="We will let you know when the replay is ready to review.",
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def notify_replay(db: Session, background, event: Event) -> str | None:
    """Announce a replay lifecycle transition, at most once per state."""
    state, sentence = replay_position(db, event)
    if state is None:
        return None
    row = _completion_row(db, event)
    if row.last_replay_state == state:
        return None

    variant, marker = {
        "validating": ("replay_processing", "processing_notified_at"),
        "ready_for_review": ("replay_approval_required", "approval_notified_at"),
        "published": ("replay_published", "published_notified_at"),
        "withheld": ("replay_unavailable", "unavailable_notified_at"),
        "expired": ("replay_unavailable", "unavailable_notified_at"),
    }.get(state, (None, None))
    if variant is None:
        row.last_replay_state = state
        db.commit()
        return None

    row.last_replay_state = state
    db.commit()
    if not _claim(db, row, marker):
        return None
    org = _org(db, event)
    if not _may_send("LVE-011", org):
        return None
    people = stakeholders(db, event)
    if not people.addresses():
        return None

    _ledger(db, org_id=event.org_id, family="LVE-011", transition=variant, event_id=event.id)
    _queue(background, email_mod.send_replay_lifecycle_email, people,
           variant=variant,
           event_title=event.title or "your event",
           reference=str(event.id)[:8],
           replay_status=sentence,
           recording_status=recording_position(db, event),
           reason=("The replay was withdrawn by an operator" if state == "withheld"
                   else "Replay availability reached its expiry date" if state == "expired"
                   else "Not applicable"),
           next_action=("Review and publish the replay" if state == "ready_for_review"
                        else "No action is needed" if state == "published"
                        else "Contact your Zoiko Steam team if you need it restored"),
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return variant


# ══ LVE-012 — post-event evidence and retention ═════════════════════════════════════════

# Searched for and absent: no k-anonymity constant, no cohort minimum, no suppression rule
# anywhere in this repository. Until a policy is published, per-segment audience figures are
# not disclosed at all and this stays False - the honest record of an absent control.
PRIVACY_POLICY_AVAILABLE = False
PRIVACY_NOTE = ("Only whole-event totals are shown. Zoiko Steam has no approved disclosure "
                "threshold for audience segments, so no segment breakdown is included here.")


def open_report(db: Session, event: Event, *, actor=None) -> PostEventReport:
    """Start a post-event report, reusing the existing EventReport generator."""
    latest = db.scalar(
        select(PostEventReport).where(PostEventReport.event_id == event.id)
        .order_by(PostEventReport.report_version.desc()))
    for prior in db.scalars(
        select(PostEventReport).where(PostEventReport.event_id == event.id,
                                      PostEventReport.status == "ready")).all():
        prior.status = "superseded"
        prior.superseded_at = _now()
    row = PostEventReport(event_id=event.id, org_id=event.org_id,
                          report_version=(latest.report_version + 1) if latest else 1,
                          status="processing", window_start=event.start_time,
                          window_end=event.end_time)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def generate(db: Session, event: Event, row: PostEventReport, *, actor=None) -> str:
    """Generate the underlying EventReport. Returns the resulting status."""
    from . import report as report_svc

    try:
        result = report_svc.generate_event_report(db, event, actor=actor)
        generated = result[0] if isinstance(result, tuple) else result
        row.report_id = getattr(generated, "id", None)
        row.generated_at = _now()
        row.status = "ready"
        row.privacy_suppression_applied = PRIVACY_POLICY_AVAILABLE
    except Exception:  # noqa: BLE001 - a generation failure is a reportable outcome
        log.exception("LVE-012 report generation failed for event %s", event.id)
        row.status = "failed"
        row.failure_category = "generation_failed"
    db.commit()
    return row.status


def _headline_metrics(db: Session, row: PostEventReport) -> list[str]:
    """Whole-event totals only. No segment, cohort or per-viewer breakdown.

    The underlying EventReport holds richer data; this deliberately reads only the aggregate
    figures, because there is no policy under which a segment could safely be disclosed.
    """
    report = db.get(EventReport, row.report_id) if row.report_id else None
    data = (report.data if report else None) or {}
    audience = data.get("audience") or {}
    out = []
    for label, key in (("Peak concurrent viewers", "peak_viewers"),
                       ("Unique attendees", "unique_viewers"),
                       ("Registrations", "registrations")):
        value = audience.get(key)
        if isinstance(value, int):
            out.append(f"{label}: {value}")
    return out or ["Totals are available in the full report"]


def notify_report(db: Session, background, event: Event, row: PostEventReport) -> str | None:
    """Announce a report becoming ready, or failing."""
    if row.status not in ("ready", "failed"):
        return None
    marker = "ready_notified_at" if row.status == "ready" else "failed_notified_at"
    if not _claim(db, row, marker):
        return None
    org = _org(db, event)
    if not _may_send("LVE-012", org):
        return None
    people = stakeholders(db, event)
    if not people.addresses():
        return None

    _ledger(db, org_id=row.org_id, family="LVE-012", transition=f"report_{row.status}",
            event_id=event.id, detail=f"v{row.report_version}")
    _queue(background, email_mod.send_post_event_report_email, people,
           variant=row.status,
           event_title=event.title or "your event",
           version=row.report_version,
           generated_at=event_timestamp(event, row.generated_at, org),
           window=(f"{event_timestamp(event, row.window_start, org)} to "
                   f"{event_timestamp(event, row.window_end, org)}"),
           metrics=_headline_metrics(db, row),
           privacy_note=PRIVACY_NOTE,
           failure_note=("We could not generate the report. Your Zoiko Steam team is "
                         "looking into it."),
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return row.status


def notify_incident_review(db: Session, background, event: Event,
                           incident: EventIncident) -> bool:
    """Announce an APPROVED incident review only.

    Gated on `review_state == "approved"`, the existing commercial review workflow. A draft
    or in-review record carries root-cause and investigation notes, and none of that is
    mailed: the message points at the approved artifact rather than quoting evidence.
    """
    if incident.review_state != "approved":
        return False
    if not _claim(db, incident, "review_ready_notified_at"):
        return False
    org = _org(db, event)
    if not _may_send("LVE-012", org):
        return False
    people = stakeholders(db, event)
    if not people.addresses():
        return False

    from ..models import INCIDENT_REASON_LABELS

    _ledger(db, org_id=event.org_id, family="LVE-012", transition="incident_review_ready",
            event_id=event.id)
    _queue(background, email_mod.send_incident_review_email, people,
           event_title=event.title or "your event",
           reference=str(incident.id)[:8],
           occurred_at=event_timestamp(event, incident.opened_at, org),
           category=INCIDENT_REASON_LABELS.get(incident.reason_category or "other", "Other"),
           summary=incident.customer_summary or "A summary is available in your console.",
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def retention_position(db: Session, event: Event) -> dict:
    """Read MED-011's retention state. Keeps no second copy of it."""
    recordings = db.scalars(
        select(LiveRecording).where(LiveRecording.event_id == event.id)).all()
    held = [r for r in recordings if r.legal_hold]
    expiring = [r for r in recordings if r.retention_expires_at]
    deleted = [r for r in recordings if (r.deletion_status or "") == "deleted"]
    return {
        "recordings": len(recordings),
        "legal_hold": len(held),
        "earliest_expiry": min((r.retention_expires_at for r in expiring), default=None),
        "deleted": len(deleted),
    }


def notify_record_closed(db: Session, background, event: Event,
                         row: PostEventReport) -> bool:
    """Announce that the event's operational record is closed.

    Never claims erasure. Security, audit and legal records outlive the media by design, and
    saying otherwise would be false - so the message states exactly what remains.
    """
    if row.status != "ready":
        return False
    if not _claim(db, row, "closed_notified_at"):
        return False
    org = _org(db, event)
    if not _may_send("LVE-012", org):
        return False
    people = stakeholders(db, event)
    if not people.addresses():
        return False

    position = retention_position(db, event)
    _ledger(db, org_id=row.org_id, family="LVE-012", transition="record_closed",
            event_id=event.id)
    _queue(background, email_mod.send_record_closed_email, people,
           event_title=event.title or "your event",
           reference=str(event.id)[:8],
           closed_at=event_timestamp(event, _now(), org),
           report_version=row.report_version,
           recordings=position["recordings"],
           legal_hold=position["legal_hold"],
           retention_until=(event_timestamp(event, position["earliest_expiry"], org)
                            if position["earliest_expiry"] else "No retention expiry set"),
           residual=("Audit, security and billing records for this event are retained under "
                     "their own policies and are not deleted with the media."),
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True
