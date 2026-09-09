"""Intake, planning and rehearsal lifecycles (ZST-EC-001 LVE-002, LVE-004, LVE-005).

Every message here is derived from a committed row in models/event_planning.py. The three
lifecycles are new domain, not new templates - none of them existed in this codebase before,
which is why the audit reported all three MISSING rather than PARTIAL.

**What is evaluated versus what is attested.** LVE-004 names four planning categories. Three
of them can be judged from state the platform already holds, so `evaluate()` computes them and
a human cannot mark them complete while the underlying facts say otherwise:

    contributors     - EventAssignment(role="speaker") and their ContributorSession consent
    audience_access  - Event.visibility / registration_required / registration_limit
    recording_replay - Event.recording_enabled and the audience ReplayEntitlement

The fourth, accessibility, has NO backing domain. There is no captions engine (MED-010 is
unsupported), no interpretation booking, no accommodation record. It is therefore
operator-attested only: `evaluate()` never completes it, and its message says explicitly that
Zoiko Steam does not itself produce captions or translation, so the customer arranges them.
Auto-completing it, or describing caption delivery, would be the exact fabrication this family
is supposed to prevent.

**Reminders** are derived from `due_at` / `scheduled_at` plus a durable marker column, not
from a REMINDER_DUE state. A reminder is a communication event; it is not a state the event
is in.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..models import (
    INTAKE_REMINDER_HOURS,
    INTAKE_SECTION_LABELS,
    INTAKE_SECTIONS,
    PLANNING_CATEGORIES,
    PLANNING_CATEGORY_LABELS,
    PLANNING_SETTLED,
    REHEARSAL_REMINDER_HOURS,
    ContributorSession,
    Event,
    EventAssignment,
    EventIntake,
    EventPlanningRequirement,
    EventRehearsal,
    Organization,
    User,
)
from . import event_comms
from .event_comms import _Bg, _claim, _ledger, _queue, event_timestamp, is_test_org, resolve

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _org_and_event(db: Session, row) -> tuple[Organization | None, Event | None]:
    event = db.get(Event, row.event_id)
    org = db.get(Organization, event.org_id) if event else None
    return org, event


def _may_send(family: str, org) -> bool:
    from . import notifications

    return notifications.should_send_operational_notification(family=family, org=org)


# ══ LVE-002 — event intake ══════════════════════════════════════════════════════════════

def validate(db: Session, event: Event) -> list[str]:
    """Which intake sections are still outstanding, judged against real Event columns.

    This is the ONLY authority on intake completeness. `complete()` refuses while it returns
    anything, so an intake cannot be marked complete by asserting it - which is what makes
    the Completed message trustworthy.
    """
    outstanding = []
    if not (event.start_time and event.timezone):
        outstanding.append("schedule")
    if not (event.expected_audience or event.registration_limit):
        outstanding.append("audience")
    speakers = db.scalars(
        select(EventAssignment).where(EventAssignment.event_id == event.id,
                                      EventAssignment.role == "speaker")).all()
    if not speakers:
        outstanding.append("contributors")
    # A delivery decision must have been made deliberately. recording_enabled defaults False,
    # so "False" alone is not evidence of a decision - a titled, scheduled event with a host
    # is. This checks the host assignment, which is the recorded delivery owner.
    host = db.scalar(
        select(EventAssignment).where(EventAssignment.event_id == event.id,
                                      EventAssignment.role == "host"))
    if host is None:
        outstanding.append("delivery")
    return outstanding


def open_intake(db: Session, event: Event, *, owner_id=None, due_at=None) -> EventIntake:
    """Create (or return) the intake for one event."""
    existing = db.scalar(select(EventIntake).where(EventIntake.event_id == event.id))
    if existing is not None:
        return existing
    intake = EventIntake(event_id=event.id, org_id=event.org_id,
                         owner_id=owner_id or event.created_by, status="open",
                         opened_at=_now(), due_at=due_at,
                         outstanding_sections=validate(db, event))
    db.add(intake)
    db.commit()
    db.refresh(intake)
    return intake


def notify_opened(db: Session, background, intake: EventIntake) -> bool:
    if intake.status != "open":
        return False
    if not _claim(db, intake, "opened_notified_at"):
        return False
    org, event = _org_and_event(db, intake)
    if event is None or not _may_send("LVE-002", org):
        return False
    people = resolve(db, event, None)
    if not people.addresses():
        return False
    _ledger(db, org_id=intake.org_id, family="LVE-002", transition="opened",
            event_id=event.id)
    _queue(background, email_mod.send_intake_opened_email, people,
           event_title=event.title or "your event",
           opened_at=event_timestamp(event, intake.opened_at, org),
           due_at=event_timestamp(event, intake.due_at, org),
           sections=[INTAKE_SECTION_LABELS[s] for s in INTAKE_SECTIONS],
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def notify_reminder(db: Session, background, intake: EventIntake) -> bool:
    """One reminder, at one governed threshold before `due_at`."""
    if intake.status in ("completed",) or intake.due_at is None:
        return False
    if intake.due_at - timedelta(hours=INTAKE_REMINDER_HOURS) > _now():
        return False
    if intake.due_at < _now():
        return False
    if not _claim(db, intake, "reminder_notified_at"):
        return False
    org, event = _org_and_event(db, intake)
    if event is None or not _may_send("LVE-002", org):
        return False
    people = resolve(db, event, None)
    if not people.addresses():
        return False
    outstanding = validate(db, event)
    _ledger(db, org_id=intake.org_id, family="LVE-002", transition="reminder",
            event_id=event.id)
    _queue(background, email_mod.send_intake_reminder_email, people,
           event_title=event.title or "your event",
           due_at=event_timestamp(event, intake.due_at, org),
           sections=[INTAKE_SECTION_LABELS[s] for s in outstanding] or ["Final review"],
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def mark_incomplete(db: Session, intake: EventIntake, event: Event) -> list[str]:
    """Record that validation found required information missing."""
    outstanding = validate(db, event)
    intake.outstanding_sections = outstanding
    intake.status = "incomplete" if outstanding else "in_progress"
    db.commit()
    return outstanding


def notify_incomplete(db: Session, background, intake: EventIntake) -> bool:
    if intake.status != "incomplete":
        return False
    if not _claim(db, intake, "incomplete_notified_at"):
        return False
    org, event = _org_and_event(db, intake)
    if event is None or not _may_send("LVE-002", org):
        return False
    people = resolve(db, event, None)
    if not people.addresses():
        return False
    # Safe CATEGORY labels, never the validator's raw internal keys.
    sections = [INTAKE_SECTION_LABELS[s] for s in (intake.outstanding_sections or [])]
    _ledger(db, org_id=intake.org_id, family="LVE-002", transition="incomplete",
            event_id=event.id)
    _queue(background, email_mod.send_intake_incomplete_email, people,
           event_title=event.title or "your event",
           due_at=event_timestamp(event, intake.due_at, org),
           sections=sections or ["Required information"],
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def complete(db: Session, intake: EventIntake, event: Event) -> bool:
    """Complete an intake ONLY if validation actually passes."""
    outstanding = validate(db, event)
    if outstanding:
        intake.outstanding_sections = outstanding
        intake.status = "incomplete"
        db.commit()
        return False
    intake.outstanding_sections = []
    intake.status = "completed"
    intake.completed_at = _now()
    # Re-arm so a later reopen/complete cycle can announce again.
    intake.reopened_notified_at = None
    db.commit()
    return True


def notify_completed(db: Session, background, intake: EventIntake) -> bool:
    if intake.status != "completed":
        return False
    if not _claim(db, intake, "completed_notified_at"):
        return False
    org, event = _org_and_event(db, intake)
    if event is None or not _may_send("LVE-002", org):
        return False
    people = resolve(db, event, None)
    if not people.addresses():
        return False
    _ledger(db, org_id=intake.org_id, family="LVE-002", transition="completed",
            event_id=event.id)
    _queue(background, email_mod.send_intake_completed_email, people,
           event_title=event.title or "your event",
           completed_at=event_timestamp(event, intake.completed_at, org),
           sections=[INTAKE_SECTION_LABELS[s] for s in INTAKE_SECTIONS],
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def reopen(db: Session, intake: EventIntake, *, sections: list[str], reason: str | None,
           actor_id=None, due_at=None) -> bool:
    """Reopen a completed intake for specific sections."""
    wanted = [s for s in sections if s in INTAKE_SECTIONS]
    if not wanted:
        return False
    intake.status = "reopened"
    intake.reopened_at = _now()
    intake.reopened_by = actor_id
    intake.reopen_reason = (reason or "")[:300] or None
    intake.outstanding_sections = wanted
    if due_at is not None:
        intake.due_at = due_at
    intake.completed_at = None
    # Re-arm the transition markers this cycle may legitimately repeat.
    intake.reopened_notified_at = None
    intake.completed_notified_at = None
    intake.reminder_notified_at = None
    db.commit()
    return True


def notify_reopened(db: Session, background, intake: EventIntake) -> bool:
    if intake.status != "reopened":
        return False
    if not _claim(db, intake, "reopened_notified_at"):
        return False
    org, event = _org_and_event(db, intake)
    if event is None or not _may_send("LVE-002", org):
        return False
    people = resolve(db, event, None)
    if not people.addresses():
        return False
    _ledger(db, org_id=intake.org_id, family="LVE-002", transition="reopened",
            event_id=event.id, detail=intake.reopen_reason)
    _queue(background, email_mod.send_intake_reopened_email, people,
           event_title=event.title or "your event",
           reopened_at=event_timestamp(event, intake.reopened_at, org),
           due_at=event_timestamp(event, intake.due_at, org),
           sections=[INTAKE_SECTION_LABELS[s]
                     for s in (intake.outstanding_sections or [])] or ["Required information"],
           reason=intake.reopen_reason or "Additional information is needed",
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


# ══ LVE-004 — planning actions required ═════════════════════════════════════════════════

# Accessibility has no backing domain in this platform, so it can never be auto-completed and
# its message must not describe a capability that does not exist. See the module docstring.
ATTESTED_ONLY = ("accessibility",)

ACCESSIBILITY_NOTE = (
    "Zoiko Steam does not generate captions, subtitles, translation or interpretation. If your "
    "event needs them, arrange them with a provider and record the arrangement here."
)


def requirements(db: Session, event: Event) -> list[EventPlanningRequirement]:
    """Ensure one row per category, then return them in canonical order."""
    existing = {r.category: r for r in db.scalars(
        select(EventPlanningRequirement).where(
            EventPlanningRequirement.event_id == event.id)).all()}
    created = False
    for category in PLANNING_CATEGORIES:
        if category not in existing:
            row = EventPlanningRequirement(
                event_id=event.id, org_id=event.org_id, category=category,
                status="incomplete", assigned_to=event.created_by)
            db.add(row)
            existing[category] = row
            created = True
    if created:
        db.commit()
    return [existing[c] for c in PLANNING_CATEGORIES]


def _contributor_outstanding(db: Session, event: Event) -> list[str]:
    speakers = db.scalars(
        select(EventAssignment).where(EventAssignment.event_id == event.id,
                                      EventAssignment.role == "speaker")).all()
    if not speakers:
        return ["No contributors have been assigned"]
    out = []
    for assignment in speakers:
        session = db.scalar(
            select(ContributorSession).where(
                ContributorSession.event_id == event.id,
                ContributorSession.user_id == assignment.user_id))
        if session is None:
            out.append("A contributor has not been invited to the backstage session")
        elif not session.consent_given:
            out.append("A contributor has not given consent")
    return out


def _audience_outstanding(event: Event) -> list[str]:
    out = []
    if not event.visibility:
        out.append("No audience visibility has been chosen")
    if event.registration_required and not event.registration_limit:
        out.append("Registration is required but no capacity limit is set")
    if event.expected_audience is None:
        out.append("No expected audience size has been given")
    return out


def _recording_outstanding(db: Session, event: Event) -> list[str]:
    from ..crud import commercial as commercial_crud

    out = []
    if not event.recording_enabled:
        out.append("Recording has not been enabled, so no replay can be produced")
        return out
    entitlement = commercial_crud.get_replay_entitlement(db, event.id, scope="audience")
    if entitlement is None:
        out.append("No replay decision has been recorded")
    return out


def evaluate(db: Session, event: Event) -> dict:
    """Recompute every evaluable category from committed state.

    Returns {category: status}. Accessibility is skipped entirely - it has no evidence to
    read, so its stored, operator-attested status stands untouched.
    """
    result = {}
    for row in requirements(db, event):
        if row.category in ATTESTED_ONLY:
            result[row.category] = row.status
            continue
        if row.status == "not_applicable":
            result[row.category] = row.status
            continue
        if row.category == "contributors":
            outstanding = _contributor_outstanding(db, event)
        elif row.category == "audience_access":
            outstanding = _audience_outstanding(event)
        else:
            outstanding = _recording_outstanding(db, event)

        previous = row.status
        row.outstanding = outstanding
        if outstanding:
            # A category that had settled and has regressed opens a new notify cycle, so the
            # owner can be told once more without the marker being nulled and losing history.
            if previous in PLANNING_SETTLED:
                row.notify_cycle += 1
                row.reopened_at = _now()
                row.completed_at = None
            row.status = "blocked" if row.blocking else "incomplete"
        else:
            row.status = "complete"
            row.completed_at = row.completed_at or _now()
        result[row.category] = row.status
    db.commit()
    return result


def attest(db: Session, row: EventPlanningRequirement, *, status: str,
           outstanding: list[str] | None = None) -> bool:
    """Record an operator decision on an attested-only category."""
    from ..models import PLANNING_STATES

    if status not in PLANNING_STATES:
        return False
    previous = row.status
    row.status = status
    row.outstanding = outstanding or []
    if status in PLANNING_SETTLED:
        row.completed_at = _now()
    elif previous in PLANNING_SETTLED:
        row.notify_cycle += 1
        row.reopened_at = _now()
        row.completed_at = None
    db.commit()
    return True


def notify_action_required(db: Session, background, row: EventPlanningRequirement) -> bool:
    """One action-required message per requirement per notify cycle.

    Routed to the ASSIGNED owner, not to every stakeholder - LVE-004 is explicit that a
    planning action goes to the person who owes it.
    """
    if row.status in PLANNING_SETTLED:
        return False
    if row.action_notified_cycle == row.notify_cycle:
        return False
    org, event = _org_and_event(db, row)
    if event is None or not _may_send("LVE-004", org):
        return False

    owner = db.get(User, row.assigned_to) if row.assigned_to else None
    owner = owner or db.get(User, event.created_by)
    if owner is None or not owner.email:
        return False

    row.action_notified_cycle = row.notify_cycle
    db.commit()

    _ledger(db, org_id=row.org_id, family="LVE-004",
            transition="action_" + row.category, event_id=event.id)
    people = event_comms.Recipients(event_owner=owner)
    _queue(background, email_mod.send_planning_action_email, people,
           category=row.category,
           category_label=PLANNING_CATEGORY_LABELS[row.category],
           event_title=event.title or "your event",
           outstanding=list(row.outstanding or []) or ["Details required"],
           assigned_owner=owner.email,
           due_at=event_timestamp(event, row.due_at, org),
           blocking=bool(row.blocking),
           extra_note=(ACCESSIBILITY_NOTE if row.category == "accessibility" else None),
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def sweep_planning(db: Session, event: Event, background=None) -> int:
    """Re-evaluate and notify every outstanding requirement for one event."""
    background = background or _Bg()
    evaluate(db, event)
    sent = 0
    for row in requirements(db, event):
        if notify_action_required(db, background, row):
            sent += 1
    return sent


# ══ LVE-005 — rehearsal lifecycle ═══════════════════════════════════════════════════════

# Rehearsal joining uses the SAME backstage route a contributor already uses to reach the
# event. No separate rehearsal link, and therefore no new token to leak: access is decided by
# the signed-in contributor's own ContributorSession, which is per-person, revocable and
# already validated server-side (services/contributor.py). See JOINING_NOTE.
JOINING_NOTE = (
    "Join from your Backstage page while signed in. Access is checked against your own "
    "contributor session, so this message carries no joining token and forwarding it gives "
    "nobody access."
)


def schedule(db: Session, event: Event, *, scheduled_at: datetime, purpose: str | None = None,
             actor_id=None) -> EventRehearsal:
    rehearsal = EventRehearsal(
        event_id=event.id, org_id=event.org_id, scheduled_at=scheduled_at,
        timezone_name=event.timezone, status="scheduled",
        purpose=(purpose or "")[:300] or None, created_by=actor_id)
    db.add(rehearsal)
    db.commit()
    db.refresh(rehearsal)
    return rehearsal


def participants(db: Session, event: Event) -> list[User]:
    """Event owner, assigned team and contributors - everyone expected at a rehearsal."""
    users = []
    owner = db.get(User, event.created_by) if event.created_by else None
    if owner is not None:
        users.append(owner)
    for assignment in db.scalars(
            select(EventAssignment).where(EventAssignment.event_id == event.id)).all():
        user = db.get(User, assignment.user_id)
        if user is not None:
            users.append(user)
    return users


def _rehearsal_people(db: Session, event: Event) -> event_comms.Recipients:
    people = resolve(db, event, None)
    # Contributors are rehearsal participants but are not commercial contacts, so they are
    # added on top of the standard resolution rather than replacing it.
    people.commercial = list(people.commercial) + participants(db, event)
    return people


def _shared(event: Event, org, rehearsal: EventRehearsal) -> dict:
    return {"event_title": event.title or "your event",
            "scheduled_at": event_timestamp(event, rehearsal.scheduled_at, org),
            "zone": rehearsal.timezone_name or event_comms.event_zone(event, org)[1],
            "local_note": event_comms.LOCAL_TIME_NOTE,
            "event_id": str(event.id),
            "org_name": org.name if org else "your Organization",
            "test_mode": is_test_org(org)}


def notify_scheduled(db: Session, background, rehearsal: EventRehearsal) -> bool:
    if rehearsal.status != "scheduled":
        return False
    if not _claim(db, rehearsal, "scheduled_notified_at"):
        return False
    org, event = _org_and_event(db, rehearsal)
    if event is None or not _may_send("LVE-005", org):
        return False
    people = _rehearsal_people(db, event)
    if not people.addresses():
        return False
    _ledger(db, org_id=rehearsal.org_id, family="LVE-005", transition="scheduled",
            event_id=event.id)
    _queue(background, email_mod.send_rehearsal_scheduled_email, people,
           purpose=rehearsal.purpose or "Confirm the delivery setup before the event",
           expected=[f"{u.full_name or u.email}" for u in participants(db, event)]
                    or ["To be confirmed"],
           joining=JOINING_NOTE,
           **_shared(event, org, rehearsal))
    return True


def notify_rehearsal_reminder(db: Session, background, rehearsal: EventRehearsal) -> bool:
    """One reminder, at one governed threshold before the rehearsal."""
    if rehearsal.status != "scheduled" or rehearsal.scheduled_at is None:
        return False
    now = _now()
    if rehearsal.scheduled_at - timedelta(hours=REHEARSAL_REMINDER_HOURS) > now:
        return False
    if rehearsal.scheduled_at < now:
        return False
    if not _claim(db, rehearsal, "reminder_notified_at"):
        return False
    org, event = _org_and_event(db, rehearsal)
    if event is None or not _may_send("LVE-005", org):
        return False
    people = _rehearsal_people(db, event)
    if not people.addresses():
        return False
    _ledger(db, org_id=rehearsal.org_id, family="LVE-005", transition="reminder",
            event_id=event.id)
    _queue(background, email_mod.send_rehearsal_reminder_email, people,
           joining=JOINING_NOTE, **_shared(event, org, rehearsal))
    return True


def complete_rehearsal(db: Session, rehearsal: EventRehearsal, *, validated: list[str],
                       outstanding: list[str], repeat_required: bool = False,
                       repeat_reason: str | None = None, next_at: datetime | None = None,
                       outcome: str | None = None) -> str:
    """Record a real rehearsal outcome. Returns the resulting status."""
    rehearsal.completed_at = _now()
    rehearsal.validated_capabilities = list(validated or [])
    rehearsal.outstanding_issues = list(outstanding or [])
    rehearsal.outcome = (outcome or "")[:2000] or None
    rehearsal.repeat_required = bool(repeat_required)
    rehearsal.repeat_reason = (repeat_reason or "")[:300] or None
    rehearsal.next_rehearsal_at = next_at
    rehearsal.status = "needs_repeat" if repeat_required else "completed"
    db.commit()
    return rehearsal.status


def notify_completed_rehearsal(db: Session, background, rehearsal: EventRehearsal) -> bool:
    if rehearsal.status != "completed":
        return False
    if not _claim(db, rehearsal, "completed_notified_at"):
        return False
    org, event = _org_and_event(db, rehearsal)
    if event is None or not _may_send("LVE-005", org):
        return False
    people = _rehearsal_people(db, event)
    if not people.addresses():
        return False
    _ledger(db, org_id=rehearsal.org_id, family="LVE-005", transition="completed",
            event_id=event.id)
    _queue(background, email_mod.send_rehearsal_completed_email, people,
           completed_at=event_timestamp(event, rehearsal.completed_at, org),
           validated=list(rehearsal.validated_capabilities or []) or ["None recorded"],
           outstanding=list(rehearsal.outstanding_issues or []) or ["None"],
           next_steps=("Proceed to event readiness"
                       if not rehearsal.outstanding_issues
                       else "Resolve the outstanding issues before the event"),
           **_shared(event, org, rehearsal))
    return True


def notify_needs_repeat(db: Session, background, rehearsal: EventRehearsal) -> bool:
    """Only ever reachable from a recorded Needs Repeat outcome."""
    if rehearsal.status != "needs_repeat":
        return False
    if not _claim(db, rehearsal, "repeat_notified_at"):
        return False
    org, event = _org_and_event(db, rehearsal)
    if event is None or not _may_send("LVE-005", org):
        return False
    people = _rehearsal_people(db, event)
    if not people.addresses():
        return False
    _ledger(db, org_id=rehearsal.org_id, family="LVE-005", transition="needs_repeat",
            event_id=event.id, detail=rehearsal.repeat_reason)
    _queue(background, email_mod.send_rehearsal_repeat_email, people,
           reason=rehearsal.repeat_reason or "The rehearsal did not validate the setup",
           outstanding=list(rehearsal.outstanding_issues or []) or ["Under review"],
           next_action="Schedule and attend another rehearsal",
           next_at=(event_timestamp(event, rehearsal.next_rehearsal_at, org)
                    if rehearsal.next_rehearsal_at else "Not yet scheduled"),
           joining=JOINING_NOTE,
           **_shared(event, org, rehearsal))
    return True


# ══ sweeper ═════════════════════════════════════════════════════════════════════════════

def sweep(db: Session, background=None) -> dict:
    """One pass over every deadline-driven LVE obligation.

    Proposal expiry, intake reminders and rehearsal reminders are all deadline-driven, so
    none of them has a request or a webhook that could carry them.
    """
    background = background or _Bg()
    counts = {"proposals_expired": 0, "intake_reminders": 0, "rehearsal_reminders": 0}

    try:
        counts["proposals_expired"] = event_comms.expire_due(db, background)
    except Exception:  # noqa: BLE001 - one family must not stop the others
        log.exception("LVE-001 proposal expiry sweep failed")

    now = _now()
    try:
        for intake in db.scalars(
            select(EventIntake).where(
                EventIntake.status != "completed",
                EventIntake.due_at.isnot(None),
                EventIntake.reminder_notified_at.is_(None),
                EventIntake.due_at > now,
                EventIntake.due_at <= now + timedelta(hours=INTAKE_REMINDER_HOURS))
        ).all():
            if notify_reminder(db, background, intake):
                counts["intake_reminders"] += 1
    except Exception:  # noqa: BLE001
        log.exception("LVE-002 intake reminder sweep failed")

    try:
        for rehearsal in db.scalars(
            select(EventRehearsal).where(
                EventRehearsal.status == "scheduled",
                EventRehearsal.scheduled_at.isnot(None),
                EventRehearsal.reminder_notified_at.is_(None),
                EventRehearsal.scheduled_at > now,
                EventRehearsal.scheduled_at <= now + timedelta(hours=REHEARSAL_REMINDER_HOURS))
        ).all():
            if notify_rehearsal_reminder(db, background, rehearsal):
                counts["rehearsal_reminders"] += 1
    except Exception:  # noqa: BLE001
        log.exception("LVE-005 rehearsal reminder sweep failed")

    # ZST-EC-001 CON-001 / CON-003 / CON-004. Contributor invitation expiry, rehearsal
    # reminders and access-link issuance are deadline-driven too, and they run on THIS
    # leader-elected ticker rather than a CON-specific queue - so a reminder survives a
    # process restart without new infrastructure.
    try:
        from . import contributor_access

        counts["contributors"] = contributor_access.sweep(db, background)
    except Exception:  # noqa: BLE001
        log.exception("CON contributor sweep failed")

    # ZST-EC-001 SUP-002. A case waiting on the customer must not stall silently, and the
    # reminder is deadline-driven, so it runs on THIS leader-elected ticker rather than a
    # SUP-specific queue - surviving a restart without new infrastructure.
    try:
        from . import support_comms

        counts["support"] = support_comms.sweep(db, background)
    except Exception:  # noqa: BLE001
        log.exception("SUP support sweep failed")

    # ZST-EC-001 SEC-002. An overdue break-glass review is deadline-driven, so it rides the
    # same leader-elected ticker rather than a SEC-specific queue.
    try:
        from . import security_comms

        counts["security"] = security_comms.sweep(db, background)
    except Exception:  # noqa: BLE001
        log.exception("SEC security sweep failed")

    # ZST-EC-001 STS-005. Maintenance reminders are deadline-driven, so they ride the same
    # leader-elected ticker. With MAINTENANCE_REMINDER_HOURS empty this is a no-op by
    # design - configuring a real policy switches it on without new infrastructure.
    try:
        from . import status_publication

        counts["status"] = status_publication.sweep(db, background)
    except Exception:  # noqa: BLE001
        log.exception("STS status sweep failed")

    # ZST-EC-001 TRU-002. Evidence access is time-bound, so something has to notice when a
    # window closes - and expired access must stop working whether or not anybody visits.
    # Same leader-elected ticker, no TRU-specific queue.
    try:
        from . import trust_center

        counts["trust"] = trust_center.sweep(db, background)
    except Exception:  # noqa: BLE001
        log.exception("TRU trust sweep failed")

    # ZST-EC-001 MKT-003. A developer onboarding step unlocks when its milestone becomes
    # observable, which is not an event anything pushes - so the sequence advances here.
    # Consent is re-checked at send time, so an unsubscribe between ticks wins.
    try:
        from . import marketing

        counts["marketing"] = marketing.sweep(db, background)
    except Exception:  # noqa: BLE001
        log.exception("MKT marketing sweep failed")

    return counts


async def run_event_planning_sweeper(interval: float = 300.0) -> None:
    """Background ticker started from the app lifespan, under leader election."""
    import asyncio

    from ..db import SessionLocal

    while True:
        try:
            await asyncio.sleep(interval)
            db = SessionLocal()
            try:
                await asyncio.to_thread(sweep, db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a bad tick must not kill the ticker
            log.exception("Event planning sweep failed")
