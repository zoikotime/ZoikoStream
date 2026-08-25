"""Account restriction and deletion notices (ZST-EC-001 IDN-008).

Every restriction or deletion path in the product funnels through here so the routers never
decide who to tell. Each committed transition writes one `AccountStateEvent`, and the
notification is claimed off that row — which is what makes the notice safely repeatable: an
account can be suspended, restored and suspended again, and each is its own event.

What this module will NOT do:
  * announce a scheduled deletion — no scheduling subsystem exists, so a cancellation
    deadline would be a promise nothing can keep
  * disclose a reason beyond a coarse category — internal detection detail and admin notes
    must never reach a recipient
  * claim data was erased — deletion here removes access; audit and legal records remain
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    STATE_DELETION_COMPLETED,
    STATE_REACTIVATED,
    STATE_RESTRICTED,
    AccountStateEvent,
    User,
)

log = logging.getLogger(__name__)

# Shown as the Organization-access line. Truthful for this product: membership is a column
# on the user row, so losing access to the account is losing access to the organization.
ORG_EFFECT_RESTRICTED = "Suspended for the duration of this restriction"
ORG_EFFECT_RESTORED = "Restored"
ORG_EFFECT_DELETED = "Ended"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def record_state_change(db: Session, *, user: User | None, email: str, org_id,
                        state: str, reason_category: str | None = None) -> AccountStateEvent:
    """Persist one committed transition. Called AFTER the state change is durable.

    `email` and `org_id` are passed explicitly rather than read from `user` because the
    deletion path calls this around a hard delete, where the row is about to disappear.
    """
    event = AccountStateEvent(
        user_id=user.id if user is not None else None,
        email=email.lower(),
        org_id=org_id,
        state=state,
        effective_at=_now(),
        reason_category=reason_category,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _claim(db: Session, event: AccountStateEvent) -> bool:
    """One notification per transition, arbitrated by the database."""
    updated = db.execute(
        update(AccountStateEvent)
        .where(AccountStateEvent.id == event.id, AccountStateEvent.notified_at.is_(None))
        .values(notified_at=_now())
    ).rowcount
    db.commit()
    return bool(updated)


def notify(db: Session, event: AccountStateEvent, background) -> None:
    """Queue the IDN-008 message for a recorded transition.

    Silent on every failure path: the account state is already committed and authoritative,
    and nothing about delivering a notice may undo it.
    """
    if not _claim(db, event):
        return

    effective = _stamp(event.effective_at)
    reference = str(event.id)[:8]
    try:
        if event.state == STATE_DELETION_COMPLETED:
            background.add_task(
                email_mod.send_account_deleted_email, event.email,
                effective_at=effective, org_effect=ORG_EFFECT_DELETED,
                security_reference=reference,
            )
        elif event.state == STATE_REACTIVATED:
            background.add_task(
                email_mod.send_account_restricted_email, event.email,
                account_state="Active", effective_at=effective,
                org_effect=ORG_EFFECT_RESTORED, security_reference=reference,
            )
        elif event.state == STATE_RESTRICTED:
            background.add_task(
                email_mod.send_account_restricted_email, event.email,
                account_state="Restricted", effective_at=effective,
                org_effect=ORG_EFFECT_RESTRICTED, security_reference=reference,
            )
    except UnsafeLinkError:
        log.exception("IDN-008 not sent for %s: APP_URL unsafe for this environment", event.id)


def announce(db: Session, background, *, user: User | None, email: str, org_id, state: str,
             reason_category: str | None = None) -> AccountStateEvent:
    """Record the transition and queue its notice. The one call routers make."""
    event = record_state_change(db, user=user, email=email, org_id=org_id, state=state,
                                reason_category=reason_category)
    notify(db, event, background)
    return event
