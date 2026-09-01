"""Organization governance lifecycles (ZST-EC-001 ORG-007, ORG-008, ORG-010).

One module, three lifecycles, one shared discipline: the domain transition commits first,
then a per-transition notification claim, then the template, then Resend. A provider failure
never rolls back a committed governance or security transition.

Design notes worth keeping in view:

  * ORG-007 has no implicit approval. `complete()` refuses while any assignment is still
    PENDING, so a review cannot be closed by ignoring it. Overdue marks a policy state and
    notifies; it does not claim an escalation workflow ran, because none exists.
  * ORG-008 completes only from READY, which is only reachable once BOTH durable
    confirmations exist and the request has not expired. `expire()` is checked before any
    completion so a retry cannot resurrect a dead request.
  * ORG-010 reports a coarse reason category and a truthful capability statement. It does
    not claim capabilities were withdrawn that this platform does not actually withdraw.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    DECISION_APPROVED,
    DECISION_CHANGE_REQUIRED,
    DECISION_EXCEPTION,
    DECISION_PENDING,
    DECISION_REMOVE,
    ORG_REASON_CATEGORIES,
    ORG_STATE_ACTIVE,
    ORG_STATE_DELETED,
    ORG_STATE_RESTRICTED,
    ORG_STATE_SUSPENDED,
    REVIEW_COMPLETED,
    REVIEW_OPEN,
    REVIEW_OVERDUE,
    REVIEW_REMINDER_BEFORE_DUE_HOURS,
    TRANSFER_CANCELED,
    TRANSFER_COMPLETED,
    TRANSFER_CURRENT_CONFIRMED,
    TRANSFER_EXPIRED,
    TRANSFER_INITIATED,
    TRANSFER_PROPOSED_CONFIRMED,
    TRANSFER_READY,
    TRANSFER_TTL_HOURS,
    REVIEWER_FALLBACK_CREATOR,
    REVIEWER_OWNER,
    REVIEWER_SECURITY_ADMIN,
    REVIEWER_UNASSIGNED,
    AccessReview,
    AccessReviewAssignment,
    AccessReviewEscalation,
    Organization,
    OrgOperationalEvent,
    OwnershipTransfer,
    User,
)
from . import org_comms
from . import org_state as org_state_svc

log = logging.getLogger(__name__)

TICKER_INTERVAL_SECONDS = 900.0

# ORG-007: what the customer is told about escalation. Truthful - the platform records the
# overdue state and notifies, and there is no automated escalation workflow behind it.
ESCALATION_NOTE = (
    "Each item still pending has been escalated to the Organization Owner and remains open "
    "until it is decided."
)

# ORG-008: truthful step-up status. There is no re-authentication, MFA or step-up token
# mechanism in this codebase, so the copy says what actually protects the transfer.
STEP_UP_NOTE = (
    "Both parties must confirm from a signed-in Zoiko Steam session and re-enter their "
    "password at the moment of confirmation. This email carries no confirmation link."
)

# ORG-010 capability statements, derived from what services/org_state.py actually enforces rather than written here.
# The previous strings were the reason ORG-010 audited INCORRECT: they claimed sign-in was
# blocked while nothing checked organization state. Sourcing the sentence from the enforcing
# module means the claim and the control cannot drift apart.
def _capabilities(state: str) -> str:
    return org_state_svc.capability_summary(state)


def _preserved(state: str) -> str:
    return org_state_svc.preserved_summary(state)


RECOVERY = {
    "billing_commercial_requirement": "Resolve the outstanding billing requirement.",
    "security_requirement": (
        "Contact Zoiko Steam Support to complete the required security review."
    ),
    "policy_compliance_requirement": (
        "Contact Zoiko Steam Support to complete the required policy review."
    ),
    "administrative_restriction": (
        "Contact Zoiko Steam Support to review this administrative restriction."
    ),
}
STATE_LABELS = {
    ORG_STATE_ACTIVE: "Active",
    ORG_STATE_RESTRICTED: "Restricted",
    ORG_STATE_SUSPENDED: "Suspended",
    ORG_STATE_DELETED: "Deleted",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _queue(background, send, addresses, **kwargs) -> None:
    try:
        for address in addresses:
            background.add_task(send, address, **kwargs)
    except UnsafeLinkError:
        log.exception("Governance notice not queued: APP_URL unsafe for this environment")


def _claim(db: Session, model, row_id, column: str) -> bool:
    col = getattr(model, column)
    updated = db.execute(
        update(model).where(model.id == row_id, col.is_(None)).values(**{column: _now()})
    ).rowcount
    db.commit()
    return bool(updated)


def owner_and_admins(db: Session, org_id) -> list[str]:
    org = db.get(Organization, org_id)
    owner = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    return org_comms.recipients(owner, *org_comms.org_admins(db, org_id))


# ══ ORG-007 access review ═══════════════════════════════════════════════════════════════

def open_review(db: Session, *, org_id, due_at: datetime, created_by: User | None = None,
                review_period: str | None = None) -> AccessReview:
    """Open a review over every active member, one assignment each, all PENDING."""
    org = db.get(Organization, org_id)
    review = AccessReview(org_id=org_id, due_at=due_at, status=REVIEW_OPEN,
                          review_period=review_period,
                          created_by_id=created_by.id if created_by else None)
    db.add(review)
    db.flush()

    members = db.scalars(
        select(User).where(User.org_id == org_id, User.is_active.is_(True),
                           User.deleted_at.is_(None))
    ).all()
    for member in members:
        reviewer, source = designate_reviewer(db, org=org, member=member,
                                              created_by=created_by)
        db.add(AccessReviewAssignment(
            review_id=review.id,
            reviewer_id=reviewer.id if reviewer else None,
            reviewer_email=reviewer.email if reviewer else None,
            reviewer_source=source,
            member_id=member.id, member_email=member.email, member_name=member.full_name,
            access_snapshot=org_comms.describe_access(role=member.role, org=org,
                                                      active=member.is_active),
            decision=DECISION_PENDING,
        ))
    db.commit()
    db.refresh(review)
    return review


def designate_reviewer(db: Session, *, org: Organization, member: User,
                       created_by: User | None = None) -> tuple[User | None, str]:
    """Choose who reviews one member's access, deterministically and explainably.

    The rule, in order:

      1. An ADMINISTRATOR's access is reviewed by the Organization Owner. Letting one
         administrator rubber-stamp another's administrative grant is the weakest link in a
         review, so the owner — the one accountable party the model actually records — takes
         those.
      2. Everyone else is reviewed by an administrator, chosen by a stable ordering rather
         than whatever the query returned first, so re-opening a review does not silently
         reassign every item.
      3. If the owner is the subject, or no administrator exists, it falls to whoever opened
         the review.

    Every branch records WHY through `reviewer_source`, so an auditor can see who was asked
    and on what basis. Returning UNASSIGNED is allowed and is deliberately visible — an
    unassignable item must show up as unassigned rather than being quietly given to someone
    inappropriate.
    """
    owner = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    admins = sorted(org_comms.org_admins(db, org.id if org else None),
                    key=lambda u: (u.email or ""))

    is_admin_subject = (member.role or "").lower() in org_comms.ADMINISTRATIVE_ROLES

    if is_admin_subject and owner is not None and owner.id != member.id:
        return owner, REVIEWER_OWNER

    candidates = [a for a in admins if a.id != member.id]
    if candidates:
        return candidates[0], REVIEWER_SECURITY_ADMIN

    if owner is not None and owner.id != member.id:
        return owner, REVIEWER_OWNER
    if created_by is not None and created_by.id != member.id:
        return created_by, REVIEWER_FALLBACK_CREATOR
    return None, REVIEWER_UNASSIGNED


def escalate_overdue(db: Session, review: AccessReview) -> list[AccessReviewEscalation]:
    """Create one durable escalation per still-pending item on an overdue review.

    Escalation goes UP: an item whose reviewer never answered is handed to the Organization
    Owner, unless the owner was already the reviewer, in which case it stays with them and
    is recorded as such rather than being sent nowhere.

    Idempotent — an item already carrying an unresolved escalation is skipped, so repeated
    ticker passes do not pile up duplicates.
    """
    org = db.get(Organization, review.org_id)
    owner = db.get(User, org.owner_user_id) if org and org.owner_user_id else None

    pending = db.scalars(
        select(AccessReviewAssignment).where(
            AccessReviewAssignment.review_id == review.id,
            AccessReviewAssignment.decision == DECISION_PENDING)
    ).all()
    existing = {
        e.assignment_id for e in db.scalars(
            select(AccessReviewEscalation).where(
                AccessReviewEscalation.review_id == review.id,
                AccessReviewEscalation.resolved_at.is_(None))
        ).all()
    }

    created = []
    for item in pending:
        if item.id in existing:
            continue
        snapshot = (item.access_snapshot or "").lower()
        high_risk = "administrator" in snapshot
        escalation = AccessReviewEscalation(
            review_id=review.id, assignment_id=item.id,
            escalated_to_id=owner.id if owner else None,
            escalated_to_email=owner.email if owner else item.reviewer_email,
            reason=("Administrative access left unreviewed past the due date"
                    if high_risk else "Access left unreviewed past the due date"),
            high_risk=high_risk,
        )
        db.add(escalation)
        created.append(escalation)
    if created:
        db.commit()
        for e in created:
            db.refresh(e)
    return created


def resolve_escalations(db: Session, assignment: AccessReviewAssignment) -> int:
    """Close any open escalation once the underlying item is actually decided."""
    rows = db.scalars(
        select(AccessReviewEscalation).where(
            AccessReviewEscalation.assignment_id == assignment.id,
            AccessReviewEscalation.resolved_at.is_(None))
    ).all()
    for row in rows:
        row.resolved_at = _now()
    if rows:
        db.commit()
    return len(rows)


def open_escalations(db: Session, review: AccessReview) -> list[AccessReviewEscalation]:
    return list(db.scalars(
        select(AccessReviewEscalation).where(
            AccessReviewEscalation.review_id == review.id,
            AccessReviewEscalation.resolved_at.is_(None))
    ).all())


def outstanding(db: Session, review: AccessReview) -> int:
    return db.scalar(
        select(func.count(AccessReviewAssignment.id)).where(
            AccessReviewAssignment.review_id == review.id,
            AccessReviewAssignment.decision == DECISION_PENDING)
    ) or 0


def tally(db: Session, review: AccessReview) -> dict:
    rows = db.scalars(
        select(AccessReviewAssignment).where(AccessReviewAssignment.review_id == review.id)
    ).all()
    return {
        "approved": sum(1 for r in rows if r.decision == DECISION_APPROVED),
        "changed": sum(1 for r in rows if r.decision == DECISION_CHANGE_REQUIRED),
        "removed": sum(1 for r in rows if r.decision == DECISION_REMOVE),
        "exceptions": sum(1 for r in rows if r.decision == DECISION_EXCEPTION),
        "high_risk_exceptions": sum(1 for r in rows
                                    if r.decision == DECISION_EXCEPTION and r.high_risk),
        "pending": sum(1 for r in rows if r.decision == DECISION_PENDING),
    }


def record_decision(db: Session, assignment: AccessReviewAssignment, *, decision: str,
                    decided_by: User, reason: str | None = None,
                    exception_owner_email: str | None = None) -> AccessReviewAssignment:
    """Record one reviewer decision.

    An EXCEPTION requires a reason and an accountable owner; high_risk is derived from the
    snapshot rather than supplied, so a reviewer cannot quietly declare an administrator
    exception low-risk.
    """
    assignment.decision = decision
    assignment.decision_reason = (reason or "").strip() or None
    assignment.decided_at = _now()
    assignment.decided_by_id = decided_by.id if decided_by else None
    if decision == DECISION_EXCEPTION:
        assignment.exception_owner_email = (exception_owner_email
                                            or (decided_by.email if decided_by else None))
        snapshot = (assignment.access_snapshot or "").lower()
        assignment.high_risk = "administrator" in snapshot
    db.commit()
    db.refresh(assignment)
    # A decided item is no longer an outstanding obligation.
    resolve_escalations(db, assignment)
    return assignment


def complete(db: Session, review: AccessReview) -> bool:
    """Close a review. Refuses while anything is still PENDING.

    This is where "inaction must not count as approval" is actually enforced: there is no
    branch that converts a pending assignment into an approval in order to finish.
    """
    if outstanding(db, review):
        return False
    review.status = REVIEW_COMPLETED
    review.completed_at = _now()
    db.commit()
    db.refresh(review)
    return True


def mark_overdue(db: Session, review: AccessReview) -> bool:
    if review.status != REVIEW_OPEN or review.due_at > _now():
        return False
    review.status = REVIEW_OVERDUE
    review.escalated_at = review.escalated_at or _now()
    db.commit()
    db.refresh(review)
    return True


def _review_recipients(db: Session, review: AccessReview) -> list[str]:
    """Assigned reviewers plus the Organization owner."""
    rows = db.scalars(
        select(AccessReviewAssignment).where(AccessReviewAssignment.review_id == review.id)
    ).all()
    extra = [r.reviewer_email for r in rows if r.reviewer_email]
    return org_comms.recipients(*[], extra=owner_and_admins(db, review.org_id) + extra)


def notify_review_opened(db: Session, background, review: AccessReview) -> None:
    if not _claim(db, AccessReview, review.id, "opened_notified_at"):
        return
    org = db.get(Organization, review.org_id)
    _queue(background, email_mod.send_access_review_assigned_email,
           _review_recipients(db, review),
           org_name=org.name if org else "your Organization",
           due_display=org_comms.org_timestamp(org, review.due_at),
           outstanding=outstanding(db, review), review_period=review.review_period)


def notify_review_reminder(db: Session, background, review: AccessReview) -> None:
    if not _claim(db, AccessReview, review.id, "reminder_notified_at"):
        return
    org = db.get(Organization, review.org_id)
    _queue(background, email_mod.send_access_review_reminder_email,
           _review_recipients(db, review),
           org_name=org.name if org else "your Organization",
           due_display=org_comms.org_timestamp(org, review.due_at),
           outstanding=outstanding(db, review))


def notify_review_overdue(db: Session, background, review: AccessReview) -> None:
    if not _claim(db, AccessReview, review.id, "overdue_notified_at"):
        return
    org = db.get(Organization, review.org_id)
    _queue(background, email_mod.send_access_review_overdue_email,
           _review_recipients(db, review),
           org_name=org.name if org else "your Organization",
           due_display=org_comms.org_timestamp(org, review.due_at),
           outstanding=outstanding(db, review), escalation_note=ESCALATION_NOTE)


def notify_review_completed(db: Session, background, review: AccessReview) -> None:
    if not _claim(db, AccessReview, review.id, "completed_notified_at"):
        return
    org = db.get(Organization, review.org_id)
    counts = tally(db, review)
    _queue(background, email_mod.send_access_review_completed_email,
           _review_recipients(db, review),
           org_name=org.name if org else "your Organization",
           completed_display=org_comms.org_timestamp(org, review.completed_at),
           approved=counts["approved"], changed=counts["changed"],
           removed=counts["removed"], exceptions=counts["exceptions"],
           high_risk_exceptions=counts["high_risk_exceptions"])


# ══ ORG-008 ownership transfer ══════════════════════════════════════════════════════════

def initiate_transfer(db: Session, *, org: Organization, current_owner: User,
                      proposed_owner: User, initiated_by: User) -> OwnershipTransfer:
    """Record a proposed transfer. Ownership does NOT move here."""
    transfer = OwnershipTransfer(
        org_id=org.id,
        current_owner_id=current_owner.id if current_owner else None,
        current_owner_email=(current_owner.email if current_owner else "unassigned"),
        proposed_owner_id=proposed_owner.id,
        proposed_owner_email=proposed_owner.email,
        initiated_by_id=initiated_by.id if initiated_by else None,
        expires_at=_now() + timedelta(hours=TRANSFER_TTL_HOURS),
        status=TRANSFER_INITIATED,
        previous_owner_retained_role=(current_owner.role if current_owner else None),
    )
    db.add(transfer)
    db.commit()
    db.refresh(transfer)
    return transfer


def transfer_is_expired(transfer: OwnershipTransfer) -> bool:
    return transfer.expires_at is not None and transfer.expires_at <= _now()


def confirm_transfer(db: Session, transfer: OwnershipTransfer, actor: User,
                     session_reference: str | None = None) -> str:
    """Record one side's durable confirmation.

    Only the two named parties can confirm, and only their own side. A repeated confirmation
    is a no-op rather than an error, so a double-click cannot corrupt the state machine.
    """
    if transfer.status in (TRANSFER_COMPLETED, TRANSFER_CANCELED, TRANSFER_EXPIRED):
        return transfer.status
    if transfer_is_expired(transfer):
        return TRANSFER_EXPIRED

    address = (actor.email or "").lower()
    if address == (transfer.current_owner_email or "").lower():
        if transfer.current_owner_confirmed_at is None:
            transfer.current_owner_confirmed_at = _now()
            transfer.current_owner_confirmed_session = session_reference
    elif address == (transfer.proposed_owner_email or "").lower():
        if transfer.proposed_owner_confirmed_at is None:
            transfer.proposed_owner_confirmed_at = _now()
            transfer.proposed_owner_confirmed_session = session_reference
    else:
        return "not_a_party"

    both = (transfer.current_owner_confirmed_at is not None
            and transfer.proposed_owner_confirmed_at is not None)
    if both:
        transfer.status = TRANSFER_READY
    elif transfer.current_owner_confirmed_at is not None:
        transfer.status = TRANSFER_CURRENT_CONFIRMED
    else:
        transfer.status = TRANSFER_PROPOSED_CONFIRMED
    db.commit()
    db.refresh(transfer)
    return transfer.status


def complete_transfer(db: Session, transfer: OwnershipTransfer) -> bool:
    """Move ownership. Only from READY, only before expiry."""
    if transfer.status != TRANSFER_READY or transfer_is_expired(transfer):
        return False
    org = db.get(Organization, transfer.org_id)
    if org is None:
        return False
    new_owner = db.get(User, transfer.proposed_owner_id)
    if new_owner is None or not new_owner.is_active or new_owner.deleted_at is not None:
        return False

    org.owner_user_id = new_owner.id
    # The incoming owner needs administrator rights to exercise ownership; the outgoing owner
    # keeps the role recorded when the transfer was raised rather than being silently demoted.
    if new_owner.role not in ("org_admin", "super_admin"):
        new_owner.role = "org_admin"
    transfer.status = TRANSFER_COMPLETED
    transfer.completed_at = _now()
    db.commit()
    db.refresh(transfer)
    return True


def expire_transfer(db: Session, transfer: OwnershipTransfer) -> bool:
    if transfer.status in (TRANSFER_COMPLETED, TRANSFER_CANCELED, TRANSFER_EXPIRED):
        return False
    transfer.status = TRANSFER_EXPIRED
    db.commit()
    db.refresh(transfer)
    return True


def cancel_transfer(db: Session, transfer: OwnershipTransfer, actor: User) -> bool:
    if transfer.status in (TRANSFER_COMPLETED, TRANSFER_CANCELED, TRANSFER_EXPIRED):
        return False
    transfer.status = TRANSFER_CANCELED
    transfer.canceled_at = _now()
    transfer.canceled_by_email = actor.email if actor else None
    db.commit()
    db.refresh(transfer)
    return True


def _transfer_recipients(db: Session, transfer: OwnershipTransfer) -> list[str]:
    """Both parties plus the organization's security administrators."""
    admins = org_comms.org_admins(db, transfer.org_id)
    return org_comms.recipients(*admins, extra=[transfer.current_owner_email,
                                                transfer.proposed_owner_email])


def notify_transfer_initiated(db: Session, background, transfer: OwnershipTransfer) -> None:
    if not _claim(db, OwnershipTransfer, transfer.id, "initiated_notified_at"):
        return
    org = db.get(Organization, transfer.org_id)
    _queue(background, email_mod.send_ownership_transfer_email,
           _transfer_recipients(db, transfer),
           org_name=org.name if org else "your Organization",
           current_owner=transfer.current_owner_email,
           proposed_owner=transfer.proposed_owner_email,
           expires_display=org_comms.org_timestamp(org, transfer.expires_at),
           step_up_note=STEP_UP_NOTE)


def notify_transfer_completed(db: Session, background, transfer: OwnershipTransfer) -> None:
    if not _claim(db, OwnershipTransfer, transfer.id, "completed_notified_at"):
        return
    org = db.get(Organization, transfer.org_id)
    retained = org_comms.role_label(transfer.previous_owner_retained_role)
    _queue(background, email_mod.send_ownership_transferred_email,
           _transfer_recipients(db, transfer),
           org_name=org.name if org else "your Organization",
           previous_owner=transfer.current_owner_email,
           new_owner=transfer.proposed_owner_email,
           effective_display=org_comms.org_timestamp(org, transfer.completed_at),
           resulting_permissions=(
               f"New owner holds Administrator rights; previous owner retains {retained}"))


def notify_transfer_expired(db: Session, background, transfer: OwnershipTransfer) -> None:
    if not _claim(db, OwnershipTransfer, transfer.id, "expired_notified_at"):
        return
    org = db.get(Organization, transfer.org_id)
    _queue(background, email_mod.send_ownership_transfer_expired_email,
           _transfer_recipients(db, transfer),
           org_name=org.name if org else "your Organization",
           expired_display=org_comms.org_timestamp(org, transfer.expires_at),
           current_owner=transfer.current_owner_email)


def notify_transfer_canceled(db: Session, background, transfer: OwnershipTransfer) -> None:
    if not _claim(db, OwnershipTransfer, transfer.id, "canceled_notified_at"):
        return
    org = db.get(Organization, transfer.org_id)
    _queue(background, email_mod.send_ownership_transfer_canceled_email,
           _transfer_recipients(db, transfer),
           org_name=org.name if org else "your Organization",
           canceled_display=org_comms.org_timestamp(org, transfer.canceled_at),
           canceled_by=transfer.canceled_by_email or "an authorized administrator",
           current_owner=transfer.current_owner_email)


# ══ ORG-010 organization operational state ══════════════════════════════════════════════

def record_org_state(db: Session, *, org_id, org_name: str, previous_state: str | None,
                     state: str, reason_category: str | None) -> OrgOperationalEvent:
    event = OrgOperationalEvent(
        org_id=org_id, org_name=org_name, previous_state=previous_state, state=state,
        effective_at=_now(),
        reason_category=reason_category if reason_category in ORG_REASON_CATEGORIES else None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def notify_org_state(db: Session, background, event: OrgOperationalEvent) -> None:
    if not _claim(db, OrgOperationalEvent, event.id, "notified_at"):
        return
    org = db.get(Organization, event.org_id) if event.org_id else None
    addresses = owner_and_admins(db, event.org_id) if event.org_id else []
    if not addresses:
        return
    effective = org_comms.org_timestamp(org, event.effective_at)

    if event.state == ORG_STATE_ACTIVE:
        _queue(background, email_mod.send_organization_reactivated_email, addresses,
               org_name=event.org_name, effective_display=effective,
               restored_capabilities=org_state_svc.capability_summary(ORG_STATE_ACTIVE),
               remaining_restrictions="None recorded")
        return

    reason_label = email_mod.ORG_010_REASON_LABELS.get(
        event.reason_category or "", "Administrative restriction")
    _queue(background, email_mod.send_organization_restricted_email, addresses,
           org_name=event.org_name, state_label=STATE_LABELS.get(event.state, "Restricted"),
           effective_display=effective, reason_label=reason_label,
           affected_capabilities=_capabilities(event.state),
           recovery_criteria=RECOVERY.get(event.reason_category or "",
                                          "Contact Zoiko Steam Support to review this."),
           preserved_access=_preserved(event.state))


def announce_org_state(db: Session, background, *, org_id, org_name: str,
                       previous_state: str | None, state: str,
                       reason_category: str | None = None) -> OrgOperationalEvent | None:
    """The one call routers make. No-ops when the state did not actually change."""
    if previous_state == state:
        return None
    event = record_org_state(db, org_id=org_id, org_name=org_name,
                             previous_state=previous_state, state=state,
                             reason_category=reason_category)
    notify_org_state(db, background, event)
    return event


# ══ governance ticker ═══════════════════════════════════════════════════════════════════

class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("Governance notification failed")


def sweep(db: Session, background=None) -> dict:
    """One pass: review reminders, review overdue, transfer expiry."""
    background = background or _Bg()
    now = _now()
    reminded = overdue = expired = 0

    soon = now + timedelta(hours=REVIEW_REMINDER_BEFORE_DUE_HOURS)
    for review in db.scalars(
        select(AccessReview).where(AccessReview.status == REVIEW_OPEN,
                                   AccessReview.due_at > now,
                                   AccessReview.due_at <= soon,
                                   AccessReview.reminder_notified_at.is_(None))
    ).all():
        notify_review_reminder(db, background, review)
        reminded += 1

    for review in db.scalars(
        select(AccessReview).where(AccessReview.status == REVIEW_OPEN,
                                   AccessReview.due_at <= now)
    ).all():
        if mark_overdue(db, review):
            # Durable escalation rows first, then the notice — so the message reports an
            # obligation that already exists rather than announcing an intention.
            escalate_overdue(db, review)
            notify_review_overdue(db, background, review)
            overdue += 1

    for transfer in db.scalars(
        select(OwnershipTransfer).where(
            OwnershipTransfer.status.in_((TRANSFER_INITIATED, TRANSFER_CURRENT_CONFIRMED,
                                          TRANSFER_PROPOSED_CONFIRMED, TRANSFER_READY)),
            OwnershipTransfer.expires_at <= now)
    ).all():
        if expire_transfer(db, transfer):
            notify_transfer_expired(db, background, transfer)
            expired += 1

    return {"reminded": reminded, "overdue": overdue, "expired": expired}


async def run_governance_sweeper(interval: float = TICKER_INTERVAL_SECONDS) -> None:
    """Background ticker started from the app lifespan."""
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
        except Exception:  # noqa: BLE001
            log.exception("Governance sweep failed")
