"""Support case lifecycle and communications (ZST-EC-001 SUP-001 -> SUP-004).

**The authorization fix.** Support cases were Super Admin-only: every route sat on the
`/admin` router, gated at router level. `open_case()` here is called from the ORGANIZATION
router instead, so a customer opens a case for their OWN organization through the same
`get_my_org` / `require_org_admin` pattern every other tenant route uses. The admin routes
are untouched - Super Admin capability is unchanged, not widened, and tenant isolation is
unchanged because the org comes from the caller's own token rather than from the request body.

**Internal notes can never be mailed.** `SupportTicket.internal_notes` exists so staff notes
have a home, and no sender in `app/email.py` accepts it. `customer_update` is the only free
text SUP-002 renders. The same applies to incidents: `Incident.detail` and
`Incident.commander` are read by nothing here.

**Next-update commitments are never generated.** `next_update_note()` returns the stored
`next_update_at` or the honest fallback sentence. There is no default, no derived SLA and no
"within 2 hours" anywhere in this module.

**Feedback eligibility is authoritative.** `is_feedback_eligible()` reads the stored
`sensitivity` classification and the `feedback_eligible` flag - never the subject line. A
security, privacy, safety or bereavement case is excluded by classification, so a
neutrally-worded bereavement case is still protected and an alarmingly-worded billing case
is still surveyed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    CASE_CATEGORIES,
    CASE_CATEGORY_LABELS,
    CASE_SENSITIVITIES,
    ESCALATION_REASON_LABELS,
    ESCALATION_REASONS,
    FEEDBACK_EXCLUDED_SENSITIVITIES,
    Incident,
    Organization,
    SupportCaseParticipant,
    SupportEscalation,
    SupportNotice,
    SupportTicket,
    User,
)

log = logging.getLogger(__name__)

# How long after an action request the single governed reminder goes out, when no explicit
# due date was given. Named and in one place rather than guessed per call site.
ACTION_REMINDER_HOURS = 72
# The customer-facing owning team a case starts with. A team name, never a person.
DEFAULT_OWNER_TEAM = "Zoiko Steam Support"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _may_send(family: str, org) -> bool:
    from . import notifications

    return notifications.should_send_operational_notification(family=family, org=org)


def case_url(ticket: SupportTicket) -> str:
    """The customer-facing support view.

    Never `/admin` and never a Super Admin path: a customer following this link lands in
    their own Tenant Console, scoped to their own case.
    """
    return f"{email_mod.public_base_url()}/organization/support/{ticket.id}"


# ══ case reference ══════════════════════════════════════════════════════════════════════

def next_case_reference(db: Session, *, now: datetime | None = None) -> str:
    """An immutable, customer-safe reference like SUP-2026-001284.

    Sequential within the calendar year and derived from a COUNT of existing references for
    that year, so it is stable, quotable and contains no database identifier. The uniqueness
    index on the column is the real guard: a concurrent duplicate loses the insert and the
    caller retries rather than two cases sharing a reference.
    """
    moment = now or _now()
    year = moment.year
    prefix = f"SUP-{year}-"
    used = db.scalar(
        select(func.count(SupportTicket.id)).where(
            SupportTicket.case_reference.like(f"{prefix}%"))) or 0
    return f"{prefix}{used + 1:06d}"


# ══ participants ════════════════════════════════════════════════════════════════════════

def participants(db: Session, ticket: SupportTicket) -> list[SupportCaseParticipant]:
    """Only people explicitly on the case.

    There is no path here that expands to organization members, admins or event
    participants - which is exactly what SUP-002 requires.
    """
    return db.scalars(
        select(SupportCaseParticipant).where(
            SupportCaseParticipant.ticket_id == ticket.id,
            SupportCaseParticipant.removed_at.is_(None))
        .order_by(SupportCaseParticipant.created_at)).all()


def add_participant(db: Session, ticket: SupportTicket, *, email: str,
                    display_name: str | None = None, role: str = "participant",
                    user_id=None, added_by=None) -> SupportCaseParticipant | None:
    address = (email or "").strip().lower()
    if not address:
        return None
    existing = db.scalar(
        select(SupportCaseParticipant).where(
            SupportCaseParticipant.ticket_id == ticket.id,
            SupportCaseParticipant.email == address))
    if existing is not None:
        existing.removed_at = None
        existing.role = role
        db.commit()
        return existing
    row = SupportCaseParticipant(ticket_id=ticket.id, org_id=ticket.org_id, email=address,
                                 display_name=display_name, role=role, user_id=user_id,
                                 added_by=added_by)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def recipients(db: Session, ticket: SupportTicket) -> list[tuple[str, str]]:
    """(email, name) for every active case participant, deduplicated."""
    seen, out = set(), []
    for row in participants(db, ticket):
        key = row.email.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append((row.email, row.display_name or "there"))
    return out


# ══ shared plumbing ═════════════════════════════════════════════════════════════════════

def _claim(db: Session, ticket: SupportTicket, kind: str, *, sequence: int = 0,
           detail: str | None = None) -> bool:
    """Durable, single-shot claim, keyed on (kind, ticket, CYCLE, sequence).

    The cycle is what makes RESOLVED -> REOPENED -> RESOLVED produce two resolution notices
    while a repeated resolve inside one cycle produces one.
    """
    cycle = ticket.lifecycle_cycle or 0
    existing = db.scalar(
        select(SupportNotice).where(SupportNotice.kind == kind,
                                    SupportNotice.ticket_id == ticket.id,
                                    SupportNotice.cycle == cycle,
                                    SupportNotice.sequence == sequence))
    if existing is not None:
        return False
    try:
        db.add(SupportNotice(ticket_id=ticket.id, org_id=ticket.org_id, kind=kind,
                             cycle=cycle, sequence=sequence, detail=detail))
        db.commit()
        return True
    except Exception:  # noqa: BLE001 - a lost uniqueness race IS a successful dedup
        db.rollback()
        log.info("Support notice %s already claimed for case %s cycle %s",
                 kind, ticket.id, cycle)
        return False


def _queue(background, send, people, **kwargs) -> None:
    """Queue one message per case participant.

    Wrapped so a provider failure can never propagate into the caller's transaction: case
    creation, updates, escalation, ownership, resolution, close and reopen must all survive
    Resend being unavailable.
    """
    try:
        for address, name in people:
            background.add_task(send, address, name=name, **kwargs)
    except UnsafeLinkError:
        log.exception("Support notice not queued: APP_URL unsafe for this environment")


class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a notice must never break committed case state
            log.exception("Support notice failed")


def next_update_note(ticket: SupportTicket, org: Organization | None = None) -> str:
    """The ONLY source of a next-update sentence.

    Returns the stored commitment or the honest fallback. Nothing in this module derives a
    time, and no template composes one - which is what makes an invented SLA impossible
    rather than merely discouraged.
    """
    if ticket.next_update_at is None:
        return "We will update the case when new information is available."
    return f"We will update you by {email_mod.billing_date(ticket.next_update_at)}."


def _shared(db: Session, ticket: SupportTicket, org: Organization | None) -> dict:
    return {
        "case_reference": ticket.case_reference or "Not assigned",
        "subject": ticket.subject,
        "category": CASE_CATEGORY_LABELS.get(ticket.category or "other", "Other"),
        "priority": (ticket.priority or "normal").title(),
        "status": (ticket.status or "open").replace("_", " ").title(),
        "owner": ticket.assigned_owner or DEFAULT_OWNER_TEAM,
        "next_update": next_update_note(ticket, org),
        "case_url": case_url(ticket),
        "org_name": org.name if org else "your Organization",
    }


def _org(db: Session, ticket: SupportTicket) -> Organization | None:
    return db.get(Organization, ticket.org_id) if ticket.org_id else None


# ══ SUP-001 — case opened ═══════════════════════════════════════════════════════════════

def open_case(db: Session, *, org: Organization, requester: User, subject: str,
              description: str, category: str, priority: str = "normal",
              sensitivity: str = "standard") -> SupportTicket | None:
    """Create a support case for the requester's OWN organization.

    `org` comes from the caller's token (routers/organization.get_my_org), never from the
    request body, so a customer cannot open a case against another tenant. `priority` is
    taken as supplied by the authorized caller and defaults to normal - it is never inferred
    from how urgent the wording sounds.
    """
    if category not in CASE_CATEGORIES or sensitivity not in CASE_SENSITIVITIES:
        return None
    from ..models import TICKET_PRIORITIES

    if priority not in TICKET_PRIORITIES:
        return None

    ticket = SupportTicket(
        org_id=org.id, subject=subject, message=description, status="open",
        priority=priority, requester_email=requester.email,
        requester_id=requester.id, category=category, sensitivity=sensitivity,
        # Derived from the authoritative classification at creation time; an operator may
        # still revoke it later, but it is never granted to an excluded sensitivity.
        feedback_eligible=sensitivity not in FEEDBACK_EXCLUDED_SENSITIVITIES,
        assigned_owner=DEFAULT_OWNER_TEAM, lifecycle_cycle=0)
    ticket.case_reference = next_case_reference(db)
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    add_participant(db, ticket, email=requester.email,
                    display_name=requester.full_name, role="requester",
                    user_id=requester.id)
    return ticket


def notify_case_opened(db: Session, background, ticket: SupportTicket) -> bool:
    """Acknowledge a committed case. Called only after the row exists."""
    if not _claim(db, ticket, "case_opened"):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-001", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_case_opened_email, people,
           created_at=email_mod.billing_date(ticket.created_at),
           requester=ticket.requester_email or "Not recorded",
           next_action=("Our support team is reviewing your case. "
                        + next_update_note(ticket, org)),
           **_shared(db, ticket, org))
    return True


# ══ SUP-002 — update and customer action ════════════════════════════════════════════════

# Fields whose change is INTERNAL only. A PATCH touching nothing outside this set is a
# staff-side edit and must not mail the customer.
INTERNAL_ONLY_FIELDS = frozenset({"internal_notes", "assigned_owner_internal", "tags",
                                  "sensitivity", "feedback_eligible", "incident_id"})
# Fields whose change IS customer-visible.
CUSTOMER_VISIBLE_FIELDS = frozenset({"status", "priority", "customer_update",
                                     "pending_action", "next_update_at",
                                     "resolution_summary", "assigned_owner"})


def is_customer_visible(changed: dict) -> bool:
    """Whether a committed change is worth telling the customer about.

    An internal note edit, a tag change or a sensitivity reclassification produces nothing.
    """
    return any(field in CUSTOMER_VISIBLE_FIELDS for field in (changed or {}))


def post_update(db: Session, ticket: SupportTicket, *, customer_update: str,
                status: str | None = None, next_update_at=None) -> bool:
    """Record a customer-visible update."""
    if not (customer_update or "").strip():
        return False
    ticket.customer_update = customer_update
    if status is not None:
        ticket.status = status
    if next_update_at is not None:
        ticket.next_update_at = next_update_at
    db.commit()
    return True


def notify_case_update(db: Session, background, ticket: SupportTicket,
                       *, sequence: int | None = None) -> bool:
    """Announce a customer-visible update.

    `sequence` lets several genuine updates go out within one lifecycle cycle while still
    being individually single-shot.
    """
    seq = sequence if sequence is not None else _update_sequence(db, ticket)
    if not _claim(db, ticket, "case_update", sequence=seq):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-002", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_case_update_email, people,
           updated_at=email_mod.billing_date(ticket.updated_at),
           # The ONLY free text mailed. internal_notes is not read here at all.
           update_text=ticket.customer_update or "The case was updated.",
           action_required=ticket.status == "waiting_for_customer",
           **_shared(db, ticket, org))
    return True


def _update_sequence(db: Session, ticket: SupportTicket) -> int:
    """The next update number inside this lifecycle cycle."""
    used = db.scalar(
        select(func.count(SupportNotice.id)).where(
            SupportNotice.ticket_id == ticket.id,
            SupportNotice.kind == "case_update",
            SupportNotice.cycle == (ticket.lifecycle_cycle or 0))) or 0
    return used + 1


def request_customer_action(db: Session, ticket: SupportTicket, *, action: str,
                            due_at=None) -> bool:
    """Move the case to WAITING_FOR_CUSTOMER with a specific requested action."""
    if not (action or "").strip():
        return False
    ticket.status = "waiting_for_customer"
    ticket.pending_action = action
    ticket.pending_action_due_at = due_at
    # A NEW request opens a new version, so one reminder can be owed per request rather
    # than per case.
    ticket.action_version = (ticket.action_version or 0) + 1
    db.commit()
    return True


def complete_customer_action(db: Session, ticket: SupportTicket,
                             *, status: str = "in_progress") -> bool:
    """Clear the pending action. A satisfied request must stop reminding."""
    if ticket.status != "waiting_for_customer":
        return False
    ticket.status = status
    ticket.pending_action = None
    ticket.pending_action_due_at = None
    db.commit()
    return True


def notify_action_required(db: Session, background, ticket: SupportTicket) -> bool:
    if ticket.status != "waiting_for_customer" or not ticket.pending_action:
        return False
    if not _claim(db, ticket, "action_required", sequence=ticket.action_version or 0):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-002", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_action_required_email, people,
           requested_action=ticket.pending_action,
           due_at=(email_mod.billing_date(ticket.pending_action_due_at)
                   if ticket.pending_action_due_at else None),
           # Sensitive material is entered in the portal, not replied into an inbox.
           secure_note=("Please add any account details, logs or documents in the support "
                        "case rather than by email."),
           **_shared(db, ticket, org))
    return True


def reminder_due(ticket: SupportTicket, now: datetime | None = None) -> tuple[bool, str]:
    """Whether an action reminder is genuinely owed."""
    moment = now or _now()
    if ticket.status != "waiting_for_customer":
        return False, f"the case is {ticket.status}"
    if not ticket.pending_action:
        return False, "no action is outstanding"
    if ticket.pending_action_due_at is not None:
        if ticket.pending_action_due_at < moment:
            return False, "the requested action is already overdue"
        if ticket.pending_action_due_at - timedelta(hours=24) > moment:
            return False, "outside the reminder threshold"
        return True, "due soon"
    # No explicit due date: remind once, a governed interval after the request.
    if ticket.updated_at is None:
        return False, "no request timestamp"
    if ticket.updated_at + timedelta(hours=ACTION_REMINDER_HOURS) > moment:
        return False, "outside the reminder threshold"
    return True, "outstanding"


def notify_action_reminder(db: Session, background, ticket: SupportTicket) -> bool:
    """One reminder per action VERSION - so a satisfied and re-requested action reminds
    again, while an unchanged outstanding request does not repeat."""
    due, _why = reminder_due(ticket)
    if not due:
        return False
    if not _claim(db, ticket, "action_reminder", sequence=ticket.action_version or 0):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-002", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_action_reminder_email, people,
           requested_action=ticket.pending_action,
           due_at=(email_mod.billing_date(ticket.pending_action_due_at)
                   if ticket.pending_action_due_at else None),
           **_shared(db, ticket, org))
    return True


# ══ SUP-003 — escalation ════════════════════════════════════════════════════════════════

def escalate(db: Session, ticket: SupportTicket, *, level: int, reason_category: str,
             owner_after: str | None = None, next_update_at=None,
             escalated_by=None) -> SupportEscalation | None:
    """Record a real escalation."""
    if reason_category not in ESCALATION_REASONS or level not in (1, 2, 3):
        return None
    row = SupportEscalation(
        ticket_id=ticket.id, org_id=ticket.org_id, level=level,
        reason_category=reason_category, escalated_at=_now(), escalated_by=escalated_by,
        owner_before=ticket.assigned_owner, owner_after=owner_after or ticket.assigned_owner,
        next_update_at=next_update_at, incident_id=ticket.incident_id)
    db.add(row)
    if owner_after:
        ticket.assigned_owner = owner_after
    if next_update_at is not None:
        ticket.next_update_at = next_update_at
    db.commit()
    db.refresh(row)
    return row


def notify_escalated(db: Session, background, ticket: SupportTicket,
                     escalation: SupportEscalation) -> bool:
    if escalation.escalated_at is None:
        return False
    if escalation.escalated_notified_at is not None:
        return False
    if not _claim(db, ticket, "escalated", sequence=escalation.level,
                  detail=escalation.reason_category):
        return False
    escalation.escalated_notified_at = _now()
    db.commit()
    org = _org(db, ticket)
    if not _may_send("SUP-003", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_escalated_email, people,
           escalated_at=email_mod.billing_date(escalation.escalated_at),
           # A coarse, approved category. Nothing names a subsystem or a person.
           reason=ESCALATION_REASON_LABELS[escalation.reason_category],
           **_shared(db, ticket, org))
    return True


def change_owner(db: Session, ticket: SupportTicket, *, owner_after: str) -> tuple[bool, str]:
    """Change the customer-facing owning team. Returns (changed, previous)."""
    previous = ticket.assigned_owner or DEFAULT_OWNER_TEAM
    if not owner_after or owner_after == previous:
        # An internal reassignment inside the same team is not an owner change.
        return False, previous
    ticket.assigned_owner = owner_after
    db.commit()
    return True, previous


def notify_owner_changed(db: Session, background, ticket: SupportTicket, *,
                         previous: str) -> bool:
    if not ticket.assigned_owner or ticket.assigned_owner == previous:
        return False
    used = db.scalar(
        select(func.count(SupportNotice.id)).where(
            SupportNotice.ticket_id == ticket.id,
            SupportNotice.kind == "owner_changed",
            SupportNotice.cycle == (ticket.lifecycle_cycle or 0))) or 0
    if not _claim(db, ticket, "owner_changed", sequence=used + 1,
                  detail=f"{previous} -> {ticket.assigned_owner}"):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-003", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_owner_changed_email, people,
           previous_owner=previous, current_owner=ticket.assigned_owner,
           effective_at=email_mod.billing_date(_now()),
           **_shared(db, ticket, org))
    return True


def link_incident(db: Session, ticket: SupportTicket, incident: Incident) -> bool:
    """Link a case to a REAL platform incident (models/platform_ops.Incident)."""
    if incident is None or ticket.incident_id == incident.id:
        return False
    ticket.incident_id = incident.id
    db.commit()
    return True


# What a customer may be told about an incident's state. `Incident.detail` and
# `Incident.commander` are deliberately absent from this mapping.
INCIDENT_STATUS_LABELS = {
    "open": "Being worked on",
    "monitoring": "Fix applied, being monitored",
    "resolved": "Resolved",
}
# A security incident's very nature can be sensitive, so the impact line stays generic for
# that kind rather than describing what was exploited.
INCIDENT_IMPACT = {
    "operational": "Service performance or availability is affected",
    "security": "A security matter is under review",
    "governance": "A compliance or governance matter is under review",
}


def notify_incident_linked(db: Session, background, ticket: SupportTicket) -> bool:
    if ticket.incident_id is None:
        return False
    incident = db.get(Incident, ticket.incident_id)
    if incident is None:
        return False
    if not _claim(db, ticket, "incident_linked", detail=incident.ref):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-003", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_incident_linked_email, people,
           # `ref` is the approved customer-facing incident reference. `detail`,
           # `commander` and the severity are never included.
           incident_reference=incident.ref,
           incident_status=INCIDENT_STATUS_LABELS.get(incident.status, "Under review"),
           impact=INCIDENT_IMPACT.get(incident.kind, "Under review"),
           **_shared(db, ticket, org))
    return True


# ══ SUP-004 — resolution, close, reopen, feedback ═══════════════════════════════════════

def resolve(db: Session, ticket: SupportTicket, *, summary: str,
            customer_action_remains: bool = False) -> bool:
    if ticket.status in ("resolved", "closed"):
        return False
    ticket.status = "resolved"
    ticket.resolved_at = _now()
    ticket.resolution_summary = summary
    ticket.pending_action = None if not customer_action_remains else ticket.pending_action
    # A resolution retires any outstanding next-update commitment rather than leaving a
    # promise standing against a closed question.
    ticket.next_update_at = None
    db.commit()
    return True


def close(db: Session, ticket: SupportTicket) -> bool:
    """Close a case. Distinct from resolve: the domain differentiates them, so the two
    notices are separate and a close does not re-announce the resolution."""
    if ticket.status == "closed":
        return False
    ticket.status = "closed"
    ticket.closed_at = _now()
    db.commit()
    return True


def reopen(db: Session, ticket: SupportTicket, *, reason: str | None = None) -> bool:
    """Reopen a resolved or closed case, starting a NEW lifecycle cycle."""
    if ticket.status not in ("resolved", "closed"):
        return False
    ticket.status = "reopened"
    ticket.reopened_at = _now()
    ticket.closed_at = None
    ticket.resolved_at = None
    ticket.customer_update = reason or ticket.customer_update
    # The new cycle is what allows a second, legitimate resolution notice later.
    ticket.lifecycle_cycle = (ticket.lifecycle_cycle or 0) + 1
    db.commit()
    return True


def notify_resolved(db: Session, background, ticket: SupportTicket) -> bool:
    if ticket.status != "resolved":
        return False
    if not _claim(db, ticket, "resolved"):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-004", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_resolved_email, people,
           resolved_at=email_mod.billing_date(ticket.resolved_at),
           # A customer-facing summary an operator wrote. internal_notes is never read.
           summary=ticket.resolution_summary or "Your case has been resolved.",
           action_remains=bool(ticket.pending_action),
           reopen_note=("If this is not resolved, reopen the case from your support view "
                        "and we will pick it up again."),
           **_shared(db, ticket, org))
    return True


def notify_closed(db: Session, background, ticket: SupportTicket) -> bool:
    if ticket.status != "closed":
        return False
    if not _claim(db, ticket, "closed"):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-004", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_closed_email, people,
           closed_at=email_mod.billing_date(ticket.closed_at),
           summary=ticket.resolution_summary or "This case is now closed.",
           reopen_note=("You can still reopen this case from your support view if you need "
                        "to."),
           **_shared(db, ticket, org))
    return True


def notify_reopened(db: Session, background, ticket: SupportTicket) -> bool:
    if ticket.status != "reopened":
        return False
    if not _claim(db, ticket, "reopened"):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-004", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_reopened_email, people,
           reopened_at=email_mod.billing_date(ticket.reopened_at),
           next_step=("Our support team is picking the case back up. "
                      + next_update_note(ticket, org)),
           **_shared(db, ticket, org))
    return True


def is_feedback_eligible(ticket: SupportTicket) -> tuple[bool, str]:
    """Whether a satisfaction survey may be sent. Returns (eligible, reason).

    Decided from the stored classification, NOT from the wording of the case. A bereavement
    case phrased neutrally is still protected; a billing case phrased dramatically is still
    surveyed. That is the whole reason `sensitivity` is a column rather than a heuristic.
    """
    if ticket.status not in ("resolved", "closed"):
        return False, f"the case is {ticket.status}, not resolved or closed"
    if not ticket.feedback_eligible:
        return False, "feedback was explicitly disabled for this case"
    sensitivity = ticket.sensitivity or "standard"
    if sensitivity in FEEDBACK_EXCLUDED_SENSITIVITIES:
        return False, f"{sensitivity} cases are excluded from feedback requests"
    return True, "eligible"


def notify_feedback_request(db: Session, background, ticket: SupportTicket) -> bool:
    """Ask for feedback, but only on an eligible case."""
    eligible, _why = is_feedback_eligible(ticket)
    if not eligible:
        return False
    if not _claim(db, ticket, "feedback_request"):
        return False
    org = _org(db, ticket)
    if not _may_send("SUP-004", org):
        return False
    people = recipients(db, ticket)
    if not people:
        return False

    _queue(background, email_mod.send_support_feedback_email, people,
           resolved_at=email_mod.billing_date(ticket.resolved_at or ticket.closed_at),
           # Support feedback is operational processing, not marketing. Stated so the
           # recipient knows answering does not sign them up to anything.
           consent_note=("This is a one-off question about this support case. It does not "
                         "subscribe you to marketing or any mailing list."),
           **_shared(db, ticket, org))
    return True


# ══ sweeper ═════════════════════════════════════════════════════════════════════════════

def sweep(db: Session, background=None) -> dict:
    """One durable pass over waiting-for-customer cases.

    Runs on the existing leader-elected planning ticker rather than a SUP-specific queue, so
    a reminder survives a process restart without new infrastructure.
    """
    background = background or _Bg()
    counts = {"action_reminders": 0}
    try:
        for ticket in db.scalars(
            select(SupportTicket).where(
                SupportTicket.status == "waiting_for_customer",
                SupportTicket.pending_action.isnot(None))).all():
            if notify_action_reminder(db, background, ticket):
                counts["action_reminders"] += 1
    except Exception:  # noqa: BLE001 - a bad sweep must not kill the ticker
        log.exception("SUP-002 action reminder sweep failed")
    return counts
