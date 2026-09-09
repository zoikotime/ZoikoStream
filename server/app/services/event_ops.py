"""Readiness, schedule, activation and interruption communications
(ZST-EC-001 LVE-006, LVE-008, LVE-009, LVE-010).

**Readiness is not reimplemented here.** `crud.commercial.evaluate_readiness` is the engine -
the same one the go-live gate calls - and this module asks it for a verdict and compares that
verdict to the last one announced. There is no rule, threshold or check code in this file.
That separation is the point: a second implementation would eventually disagree with the gate,
and the customer would be told an event was ready that the platform would then refuse to run.

**Schedule changes can no longer be silent.** `record_change()` is the one helper every
mutation path funnels through, and `routers/events.update_event` now calls it instead of
writing `start_time` directly. It persists the previous and new window, re-evaluates the
dependent domains that actually exist, and governs each recipient class separately.

**Cancellation is never inferred.** A BroadcastSession ending, a producer disconnecting, a
recording stopping and a LiveKit room closing are all ordinary. `notify_canceled` refuses
unless `Event.status == "cancelled"` - an explicit, committed lifecycle state.

**Activation is operator communication only.** Nothing in this module can reach an audience
sender; it imports none, and the tests assert that going live mails no registrant.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..models import (
    INCIDENT_REASON_CATEGORIES,
    INCIDENT_REASON_LABELS,
    READINESS_SETTLED,
    VERDICT_TO_VARIANT,
    ContributorSession,
    Event,
    EventActivationState,
    EventAssignment,
    EventIncident,
    EventReadinessState,
    EventRegistration,
    EventRehearsal,
    EventScheduleChange,
    Organization,
    User,
)
from . import event_comms
from .event_comms import _Bg, _claim, _ledger, _queue, event_timestamp, is_test_org, resolve

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ctx(db: Session, event: Event):
    org = db.get(Organization, event.org_id) if event else None
    return org, resolve(db, event, None)


def _may_send(family: str, org) -> bool:
    from . import notifications

    return notifications.should_send_operational_notification(family=family, org=org)


def team(db: Session, event: Event) -> list[User]:
    """The assigned event team - host, moderator and speakers."""
    users = []
    for assignment in db.scalars(
            select(EventAssignment).where(EventAssignment.event_id == event.id)).all():
        user = db.get(User, assignment.user_id)
        if user is not None:
            users.append(user)
    return users


def owner_and_team(db: Session, event: Event) -> event_comms.Recipients:
    """Event owner + assigned team. NOT the billing contact, and never a purchaser.

    LVE-006/009/010 are operational messages for the people running the event. The commercial
    roles that event_comms.resolve() adds for a proposal are deliberately absent.
    """
    owner = db.get(User, event.created_by) if event.created_by else None
    people = event_comms.Recipients(event_owner=owner,
                                    host=event_comms.event_host(db, event))
    people.commercial = team(db, event)
    return people


# ══ LVE-006 — readiness gate lifecycle ══════════════════════════════════════════════════

def _state_row(db: Session, event: Event) -> EventReadinessState:
    row = db.scalar(
        select(EventReadinessState).where(EventReadinessState.event_id == event.id))
    if row is None:
        row = EventReadinessState(event_id=event.id, org_id=event.org_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def evaluate(db: Session, event: Event) -> tuple[str | None, dict]:
    """Ask the authoritative engine for a verdict and fold it into the announced state.

    Returns (variant_to_announce, evaluation). The variant is None when nothing changed -
    which is what makes repeated polling silent.
    """
    from ..crud import commercial as commercial_crud

    evaluation = commercial_crud.evaluate_readiness(
        db, event, commercial_crud.get_current_order(db, event.id))
    variant = VERDICT_TO_VARIANT.get(evaluation.get("verdict") or "")
    if variant is None:
        return None, evaluation

    row = _state_row(db, event)
    previous = row.variant
    if previous == variant:
        # Same verdict as last announced. The reasons may have been re-ordered by the engine,
        # but the customer's position has not changed, so nothing is owed.
        row.blocking_reasons = list(evaluation.get("blocking_reasons") or [])
        db.commit()
        return None, evaluation

    # A settled verdict falling back to an unsettled one is the transition LVE-006 cares most
    # about: it must never be left standing under an earlier "passed" message.
    regressed = previous in READINESS_SETTLED and variant not in READINESS_SETTLED
    row.regressed_from = previous if regressed else None
    row.variant = variant
    row.verdict = evaluation.get("verdict")
    row.blocking_reasons = list(evaluation.get("blocking_reasons") or [])
    row.exceptions_applied = list(evaluation.get("exceptions_applied") or [])
    row.changed_at = _now()
    row.notified_at = None      # a real transition re-arms the single-shot claim
    db.commit()
    return ("regressed" if regressed else variant), evaluation


def notify_readiness(db: Session, background, event: Event) -> str | None:
    """Announce a readiness transition, at most once per transition."""
    variant, evaluation = evaluate(db, event)
    if variant is None:
        return None
    row = _state_row(db, event)
    if not _claim(db, row, "notified_at"):
        return None
    org, _ = _ctx(db, event)
    if not _may_send("LVE-006", org):
        return None
    people = owner_and_team(db, event)
    if not people.addresses():
        return None

    reasons = list(row.blocking_reasons or [])
    # `ready` is the engine's own answer to "may this proceed". Never asserted independently:
    # saying an event may proceed when the gate would refuse it is the failure mode here.
    may_proceed = bool(evaluation.get("ready"))
    _ledger(db, org_id=event.org_id, family="LVE-006", transition=f"readiness_{variant}",
            event_id=event.id, detail="; ".join(reasons)[:2000] or None)
    _queue(background, email_mod.send_readiness_email, people,
           variant=variant,
           event_title=event.title or "your event",
           reference=str(event.id)[:8],
           current_state=variant.replace("_", " ").title(),
           previous_state=(row.regressed_from or "Not previously assessed").title(),
           regressed_at=event_timestamp(event, row.changed_at, org),
           conditions=reasons or ["None outstanding"],
           exceptions=list(row.exceptions_applied or []),
           may_proceed=may_proceed,
           impact=("This event cannot enter production until these are resolved."
                   if not may_proceed else
                   "The event may proceed while these conditions stand."),
           owners=[u.email for u in people.commercial] or ["The event owner"],
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return variant


# ══ LVE-008 — event schedule change ═════════════════════════════════════════════════════

# A move smaller than this is treated as a no-op correction rather than a schedule change.
# Zero would make a re-save that round-trips a timestamp through the client look like a move.
NOOP_TOLERANCE_SECONDS = 60


def _materially_different(previous, new) -> bool:
    if previous is None and new is None:
        return False
    if previous is None or new is None:
        return True
    return abs((new - previous).total_seconds()) > NOOP_TOLERANCE_SECONDS


def reevaluate_downstream(db: Session, event: Event, change: EventScheduleChange) -> dict:
    """Re-evaluate the dependent domains that actually exist, and report on those that do not.

    LVE-008 forbids mailing about a schedule change while leaving stale scheduled actions in
    place. Every entry below is either a real re-evaluation or an explicit statement that the
    subsystem is absent - nothing is claimed to have been updated that was not.
    """
    from . import event_planning

    out: dict = {}

    # Rehearsals scheduled AFTER the new start no longer make sense as rehearsals. They are
    # flagged, not silently moved: only an operator knows the right new time.
    stale = db.scalars(
        select(EventRehearsal).where(EventRehearsal.event_id == event.id,
                                     EventRehearsal.status == "scheduled")).all()
    conflicting = [r for r in stale
                   if r.scheduled_at and change.new_start_time
                   and r.scheduled_at >= change.new_start_time]
    for rehearsal in conflicting:
        # Re-arm the reminder so the new timing is announced against the corrected schedule
        # rather than suppressed by a marker set for the old one.
        rehearsal.reminder_notified_at = None
    if conflicting:
        db.commit()
    out["rehearsals"] = ("no rehearsal scheduled" if not stale else
                         f"{len(conflicting)} rehearsal(s) now fall after the new start and "
                         f"need rescheduling" if conflicting else
                         "rehearsal still precedes the event")

    # Readiness is genuinely re-derivable, so it is genuinely re-derived.
    try:
        _, evaluation = evaluate(db, event)
        out["readiness"] = ("re-evaluated: ready" if evaluation.get("ready")
                            else f"re-evaluated: {len(evaluation.get('blocking_reasons') or [])} "
                                 f"blocking condition(s)")
    except Exception:  # noqa: BLE001 - a re-evaluation failure must not lose the change
        log.exception("LVE-008 readiness re-evaluation failed for event %s", event.id)
        out["readiness"] = "could not be re-evaluated"

    # Planning requirements read the event's own columns, so they are recomputed too.
    try:
        event_planning.evaluate(db, event)
        out["planning"] = "re-evaluated"
    except Exception:  # noqa: BLE001
        log.exception("LVE-008 planning re-evaluation failed for event %s", event.id)
        out["planning"] = "could not be re-evaluated"

    # An approved brief describes a day that has now moved, so it is superseded rather than
    # left standing as an approved description of the wrong schedule.
    try:
        from . import event_closeout

        out["brief"] = event_closeout.supersede_for_schedule_change(db, event)
    except Exception:  # noqa: BLE001
        log.exception("LVE-008 brief supersede failed for event %s", event.id)
        out["brief"] = "could not be updated"

    # ZST-EC-001 CON-003 / CON-004. Contributor access windows are DERIVED from the event
    # schedule, so a move re-derives them, revokes every backstage token minted against the
    # old window, and re-arms the rehearsal reminder by version - which is what stops a
    # reminder carrying the old time and stops a stale link outliving the schedule.
    try:
        from . import contributor_access

        moved = contributor_access.reissue_for_schedule_change(db, event)
        out["contributors"] = (f"{moved} contributor access window(s) re-derived and stale "
                               f"backstage links revoked" if moved
                               else "no active contributor grants")
    except Exception:  # noqa: BLE001 - a re-issue failure must not lose the change
        log.exception("CON access re-issue failed for event %s", event.id)
        out["contributors"] = "could not be updated"

    # Intake due dates are set by an operator against a delivery plan, not derived from
    # start_time, so there is nothing to recompute - stated rather than implied.
    out["intake"] = "due date is operator-set and unchanged"
    # Absent subsystems, reported rather than fabricated.
    out["audience_reminders"] = "not supported - no audience reminder scheduler exists"
    out["access_windows"] = "not supported - no audience access window is stored"
    out["capacity"] = ("capacity reservations are window-specific; a commercial reschedule "
                       "releases them, an ordinary edit does not hold any")
    return out


def record_change(db: Session, event: Event, *, previous_start, previous_end,
                  previous_timezone, actor_id=None, reason_category=None
                  ) -> EventScheduleChange | None:
    """Persist one committed schedule change. Returns None for a no-op.

    Called AFTER the new values are committed to the event, so the record always describes a
    change that actually happened.
    """
    moved = (_materially_different(previous_start, event.start_time)
             or _materially_different(previous_end, event.end_time)
             or (previous_timezone or None) != (event.timezone or None))
    if not moved:
        return None

    date_changed = bool(
        previous_start and event.start_time
        and previous_start.date() != event.start_time.date())
    change = EventScheduleChange(
        event_id=event.id, org_id=event.org_id, changed_by=actor_id,
        previous_start_time=previous_start, new_start_time=event.start_time,
        previous_end_time=previous_end, new_end_time=event.end_time,
        previous_timezone=previous_timezone, new_timezone=event.timezone,
        reason_category=reason_category, changed_at=_now(), date_changed=date_changed)
    db.add(change)
    db.commit()
    db.refresh(change)

    change.downstream = reevaluate_downstream(db, event, change)
    db.commit()
    return change


def contributors(db: Session, event: Event) -> list[User]:
    """Speakers who have actually been brought into the event.

    An EventAssignment alone is eligibility; a ContributorSession is an actual commitment of
    that person's time, which is what a schedule move disrupts. Mailing every eligible
    speaker about a move they were never booked for is noise.
    """
    out = []
    for assignment in db.scalars(
            select(EventAssignment).where(EventAssignment.event_id == event.id,
                                          EventAssignment.role == "speaker")).all():
        session = db.scalar(
            select(ContributorSession).where(
                ContributorSession.event_id == event.id,
                ContributorSession.user_id == assignment.user_id))
        if session is None or session.state in ("removed",):
            continue
        user = db.get(User, assignment.user_id)
        if user is not None:
            out.append(user)
    return out


def audience_decision(db: Session, event: Event, change: EventScheduleChange) -> tuple[bool, str]:
    """Whether the audience may be told, and why.

    Three conditions, all required. An internal planning tweak stays internal, which is what
    stops a schedule tidy-up from mailing every registrant.
    """
    registrations = db.scalars(
        select(EventRegistration).where(EventRegistration.event_id == event.id)).all()
    if not registrations:
        return False, "no audience registrations exist"
    if event.status not in ("published", "scheduled"):
        return False, f"event is '{event.status}', not published to an audience"
    if not change.date_changed and not _materially_different(
            change.previous_start_time, change.new_start_time):
        return False, "the change does not materially affect attendance"
    return True, f"{len(registrations)} registered attendee(s) materially affected"


def notify_schedule_change(db: Session, background, event: Event,
                           change: EventScheduleChange) -> dict:
    """Announce one committed schedule change to each recipient class, independently."""
    org, _ = _ctx(db, event)
    sent = {"owner": False, "contributors": False, "audience": False}
    if change is None or not _may_send("LVE-008", org):
        return sent

    shared = {
        "event_title": event.title or "your event",
        "reference": str(event.id)[:8],
        "previous_time": event_timestamp(event, change.previous_start_time, org)
                         if change.previous_start_time else "Not previously scheduled",
        "new_time": event_timestamp(event, change.new_start_time, org),
        "previous_zone": change.previous_timezone or "Not set",
        "new_zone": change.new_timezone or event_comms.event_zone(event, org)[1],
        "date_changed": bool(change.date_changed),
        "local_note": event_comms.LOCAL_TIME_NOTE,
        "event_id": str(event.id),
        "org_name": org.name if org else "your Organization",
        "test_mode": is_test_org(org),
    }
    downstream = change.downstream or {}

    if _claim(db, change, "owner_notified_at"):
        people = owner_and_team(db, event)
        if people.addresses():
            _ledger(db, org_id=event.org_id, family="LVE-008", transition="schedule_changed",
                    event_id=event.id,
                    detail=f"{shared['previous_time']} -> {shared['new_time']}")
            _queue(background, email_mod.send_schedule_change_email, people,
                   audience="team",
                   rehearsal_note=downstream.get("rehearsals", "Not evaluated"),
                   readiness_note=downstream.get("readiness", "Not evaluated"),
                   brief_note=downstream.get("brief", "Not evaluated"),
                   contributor_note="Contributors have been notified separately",
                   **shared)
            sent["owner"] = True

    people = contributors(db, event)
    if people and _claim(db, change, "contributors_notified_at"):
        bundle = event_comms.Recipients(commercial=people)
        _ledger(db, org_id=event.org_id, family="LVE-008",
                transition="schedule_changed_contributors", event_id=event.id)
        _queue(background, email_mod.send_schedule_change_email, bundle,
               audience="contributor",
               rehearsal_note=downstream.get("rehearsals", "Not evaluated"),
               readiness_note="Your event team is reviewing readiness",
               brief_note=downstream.get("brief", "Not evaluated"),
               contributor_note="Your contributor access moves with the event",
               **shared)
        sent["contributors"] = True

    allowed, why = audience_decision(db, event, change)
    change.audience_decision = why[:60]
    db.commit()
    if allowed and _claim(db, change, "audience_notified_at"):
        registrations = db.scalars(
            select(EventRegistration).where(EventRegistration.event_id == event.id)).all()
        bundle = event_comms.Recipients()
        bundle.billing = None
        # Registrations are audience contacts, not platform users, so they are queued
        # directly rather than resolved through the operator roles.
        _ledger(db, org_id=event.org_id, family="LVE-008",
                transition="schedule_changed_audience", event_id=event.id, detail=why)
        for registration in registrations:
            if not registration.email:
                continue
            background.add_task(
                email_mod.send_schedule_change_email, registration.email,
                name=registration.name or "there", audience="audience",
                rehearsal_note="Not applicable",
                readiness_note="Not applicable",
                brief_note="Not applicable",
                contributor_note="Not applicable", **shared)
        sent["audience"] = True
    return sent


# ══ LVE-009 — event-day activation ══════════════════════════════════════════════════════
#
# Two variants only. "Audience access opens soon" has no authoritative trigger - the product
# stores no audience-access opening time anywhere - and MONITORING is not a member of
# EVENT_STATUSES. Both are reported unsupported rather than fired off a guess.

AUDIENCE_ACCESS_SCHEDULE_SUPPORTED = False
MONITORING_STATE_SUPPORTED = False


def _activation_row(db: Session, event: Event) -> EventActivationState:
    row = db.scalar(
        select(EventActivationState).where(EventActivationState.event_id == event.id))
    if row is None:
        row = EventActivationState(event_id=event.id, org_id=event.org_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def notify_activation(db: Session, background, event: Event) -> str | None:
    """Announce an armed/live transition from the event's own committed status.

    Called after the status is committed, so a go-live that the backend ultimately refused
    never produces a "live" message - the status simply never became "live".
    """
    variant = event.status if event.status in ("armed", "live") else None
    if variant is None:
        return None
    row = _activation_row(db, event)
    marker = {"armed": "armed_notified_at", "live": "live_notified_at"}[variant]
    if not _claim(db, row, marker):
        return None
    row.variant, row.changed_at = variant, _now()
    db.commit()

    org, _ = _ctx(db, event)
    if not _may_send("LVE-009", org):
        return None
    people = owner_and_team(db, event)
    if not people.addresses():
        return None

    _ledger(db, org_id=event.org_id, family="LVE-009", transition=f"activation_{variant}",
            event_id=event.id)
    _queue(background, email_mod.send_activation_email, people,
           variant=variant,
           event_title=event.title or "your event",
           reference=str(event.id)[:8],
           changed_at=event_timestamp(event, row.changed_at, org),
           start_at=event_timestamp(event, event.start_time, org),
           zone=event_comms.event_zone(event, org)[1],
           local_note=event_comms.LOCAL_TIME_NOTE,
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return variant


# ══ LVE-010 — interruption and cancellation ═════════════════════════════════════════════

def open_incident(db: Session, event: Event, *, state: str, reason_category: str,
                  summary: str | None = None, next_update_at=None, actor_id=None,
                  severity: str = "sev3") -> EventIncident | None:
    """Open or advance an operational interruption on the event's incident record."""
    if state not in ("delayed", "temporary_hold"):
        return None
    if reason_category not in INCIDENT_REASON_CATEGORIES:
        return None
    incident = db.scalar(
        select(EventIncident).where(EventIncident.event_id == event.id,
                                    EventIncident.closed_at.is_(None))
        .order_by(EventIncident.opened_at.desc()))
    if incident is None:
        incident = EventIncident(event_id=event.id, severity=severity,
                                 cause_domain="mixed_unknown", created_by=actor_id)
        db.add(incident)
    incident.operational_state = state
    incident.reason_category = reason_category
    incident.customer_summary = (summary or "")[:300] or None
    incident.next_update_at = next_update_at
    if state == "delayed":
        incident.delay_started_at = incident.delay_started_at or _now()
    else:
        incident.hold_started_at = incident.hold_started_at or _now()
    # A new interruption re-arms resume, so a later resume is announced again.
    incident.resumed_notified_at = None
    incident.resumed_at = None
    db.commit()
    db.refresh(incident)
    return incident


def resume_incident(db: Session, incident: EventIncident) -> bool:
    if incident.operational_state not in ("delayed", "temporary_hold"):
        return False
    incident.operational_state = "resumed"
    incident.resumed_at = _now()
    incident.next_update_at = None
    # Re-arm the interruption markers so a later interruption can be announced again.
    incident.delayed_notified_at = None
    incident.hold_notified_at = None
    db.commit()
    return True


def cancel_incident(db: Session, event: Event, incident: EventIncident, *,
                    reason_category: str, summary: str | None = None) -> bool:
    """Record a cancellation. Refuses unless the EVENT is authoritatively cancelled.

    This is the guard that stops a broadcast ending, a producer disconnecting or a room
    closing from ever being reported to a customer as a cancellation.
    """
    if event.status != "cancelled":
        return False
    if reason_category not in INCIDENT_REASON_CATEGORIES:
        return False
    incident.operational_state = "canceled"
    incident.reason_category = reason_category
    incident.customer_summary = (summary or "")[:300] or None
    incident.canceled_at = _now()
    incident.closed_at = incident.closed_at or _now()
    db.commit()
    return True


def _incident_shared(event: Event, org, incident: EventIncident) -> dict:
    return {
        "event_title": event.title or "your event",
        "reference": str(event.id)[:8],
        "reason": INCIDENT_REASON_LABELS.get(incident.reason_category or "other", "Other"),
        "summary": incident.customer_summary or "Our team is working on it.",
        # LVE-010: a next-update promise is made ONLY when an operator committed to one.
        # There is no default, no derived time and no "shortly".
        "next_update": (event_timestamp(event, incident.next_update_at, org)
                        if incident.next_update_at else None),
        "current_state": (event.status or "unknown").replace("_", " ").title(),
        "event_id": str(event.id),
        "org_name": org.name if org else "your Organization",
        "test_mode": is_test_org(org),
    }


def notify_incident(db: Session, background, event: Event,
                    incident: EventIncident) -> str | None:
    """Announce one committed interruption transition."""
    state = incident.operational_state
    marker = {"delayed": "delayed_notified_at", "temporary_hold": "hold_notified_at",
              "resumed": "resumed_notified_at", "canceled": "canceled_notified_at"}.get(state)
    if marker is None:
        return None
    if not _claim(db, incident, marker):
        return None
    org, _ = _ctx(db, event)
    if not _may_send("LVE-010", org):
        return None
    people = owner_and_team(db, event)
    if not people.addresses():
        return None

    shared = _incident_shared(event, org, incident)
    started = incident.delay_started_at if state == "delayed" else incident.hold_started_at
    _ledger(db, org_id=event.org_id, family="LVE-010", transition=f"incident_{state}",
            event_id=event.id, detail=incident.reason_category)
    _queue(background, email_mod.send_event_incident_email, people,
           variant=state,
           started_at=event_timestamp(event, started, org),
           resumed_at=event_timestamp(event, incident.resumed_at, org),
           canceled_at=event_timestamp(event, incident.canceled_at, org),
           interruption_duration=event_comms._duration_between(started, incident.resumed_at),
           **shared)
    return state
