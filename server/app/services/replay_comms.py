"""Replay lifecycle communications (ZST-EC-001 MED-009).

**The audience distinction this family exists to enforce.** A replay becoming available is
two different events to two different populations. A purchaser learns their content is
watchable; an asset owner and the people authorized to publish learn that a decision was
taken on an asset they are accountable for. Zoiko Steam already had the first. This module
is the second, and it deliberately cannot reach the first: nothing here imports an
audience-facing sender, and `test_media_governance.py` asserts that publishing a replay
sends no purchaser mail from this path.

**Who is a publisher.** `security.commercial_can(user, "media_access")` — the same
permission that gates the publish route itself (`require_commercial("media_access")`). That
means the recipient list is derived from the actual authorization rule rather than from a
second, drifting list of "people who probably care". Customer-side that resolves to the
`host` role; staff-side to `live_ops`. Staff are NOT mailed about a tenant's replay: this
resolves publishers within the owning Organization only.

**States.** `ReplayEntitlement.publish_state` is the authoritative field and already existed
with a full vocabulary (not_available | validating | ready_for_review | published |
withheld | expired | deleted_preserved). Three of those had no writer at all before this
change — `withheld` and `expired` are now reachable through `withdraw()` and
`expire_due()`, and both are enforced at the access layer rather than being labels.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..models import Event, LiveRecording, MediaAssetEvent, Organization, User
from ..security import commercial_can
from . import media_comms, org_comms

log = logging.getLogger(__name__)

# How publish_state reads to a human. The vocabulary is the model's, not this module's.
ACCESS_LABELS = {
    "not_available": "Not available",
    "validating": "Validating",
    "ready_for_review": "Prepared — awaiting review, not published",
    "published": "Published to its audience",
    "withheld": "Withdrawn — not available",
    "expired": "Expired — no longer available",
    "deleted_preserved": "Deleted, metadata preserved",
}

# Zoiko Steam attaches no refund or entitlement reversal to a withdrawal. Stated rather than
# speculated about: a withdrawal notice that guesses at commercial consequences is worse
# than one that says exactly what the platform did.
ENTITLEMENT_EFFECT = (
    "Existing viewers can no longer watch this replay. Zoiko Steam does not change orders, "
    "entitlements or refunds as part of a withdrawal — any commercial consequence is a "
    "separate decision."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _claim(db: Session, row, column: str) -> bool:
    if getattr(row, column) is not None:
        return False
    setattr(row, column, _now())
    db.commit()
    return True


def reference(entitlement) -> str:
    return str(entitlement.id)[:8]


# ── recipients ──────────────────────────────────────────────────────────────────────────

def publishers(db: Session, org_id) -> list[User]:
    """Users in this Organization who may actually publish a replay.

    Resolved from the authorization rule, not from a role name written down twice. If the
    commercial matrix changes, this list changes with it.
    """
    if not org_id:
        return []
    members = db.scalars(
        select(User).where(User.org_id == org_id, User.is_active.is_(True),
                           User.role != "super_admin")
    ).all()
    return [u for u in members if _can_publish(u)]


def _can_publish(user: User) -> bool:
    try:
        return commercial_can(user, "media_access")
    except ValueError:  # pragma: no cover — the action name is a constant
        return False


def recipients(db: Session, org_id, owner_id=None) -> list[str]:
    """Asset owner + authorized publishers + the Organization owner.

    The Organization owner is included because they are accountable for the asset even when
    they hold no media permission — the same reasoning services/media_comms.operators uses.
    A purchaser address can never enter this list: no purchaser table is consulted here.
    """
    owner = db.get(User, owner_id) if owner_id else None
    org = db.get(Organization, org_id) if org_id else None
    holder = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    return org_comms.recipients(owner, holder, *publishers(db, org_id))


def _context(db: Session, entitlement):
    event = db.get(Event, entitlement.event_id)
    org = db.get(Organization, event.org_id) if event else None
    source = (db.get(LiveRecording, entitlement.source_recording_id)
              if entitlement.source_recording_id else None)
    owner_id = source.created_by if source else None
    return org, event, source, recipients(db, org.id if org else None, owner_id)


def _ledger(db: Session, entitlement, transition: str, detail: str | None = None,
            actor_id=None) -> None:
    event = db.get(Event, entitlement.event_id)
    db.add(MediaAssetEvent(org_id=event.org_id if event else entitlement.event_id,
                           event_id=entitlement.event_id,
                           recording_id=entitlement.source_recording_id,
                           replay_entitlement_id=entitlement.id, family="MED-009",
                           transition=transition, detail=detail, actor_id=actor_id))
    db.commit()


def _window(org, entitlement) -> str:
    """The availability window, stated only because expiry is now actually enforced."""
    if entitlement.expires_at is None:
        return "No expiry set — available until withdrawn"
    return f"Until {org_comms.org_timestamp(org, entitlement.expires_at)}"


def _owner_label(source: LiveRecording | None, db: Session) -> str:
    owner = db.get(User, source.created_by) if source and source.created_by else None
    return owner.email if owner else "An authorized operator"


# ── state transitions ───────────────────────────────────────────────────────────────────

def _transition(db: Session, entitlement, new_state: str) -> str | None:
    """Commit a publish_state change and remember what it replaced."""
    previous = entitlement.publish_state
    if previous == new_state:
        return None
    entitlement.previous_publish_state = previous
    entitlement.publish_state = new_state
    entitlement.state_changed_at = _now()
    db.commit()
    return previous


def withdraw(db: Session, entitlement, *, actor_id=None, reason: str | None = None):
    """Move a published (or prepared) replay to `withheld`.

    This is a real access change, not a label: routers/events.py::watch_event gates replay
    access on publish_state == "published", so a withheld entitlement stops being served
    immediately.
    """
    previous = _transition(db, entitlement, "withheld")
    if previous is None:
        return None
    entitlement.withdrawn_at = _now()
    entitlement.withdrawn_by = actor_id
    entitlement.withdraw_reason = (reason or "")[:200] or None
    # Re-arm so a later re-publish is announced again.
    entitlement.published_notified_at = None
    db.commit()
    return previous


def expire(db: Session, entitlement):
    """Move a replay whose committed expiry has passed to `expired`."""
    previous = _transition(db, entitlement, "expired")
    if previous is not None:
        entitlement.published_notified_at = None
        db.commit()
    return previous


def is_expired(entitlement) -> bool:
    return (entitlement.expires_at is not None
            and entitlement.expires_at <= datetime.now(timezone.utc))


# ── notifications ───────────────────────────────────────────────────────────────────────

def notify_prepared(db: Session, background, entitlement) -> bool:
    """MED-009 Prepared. Sent when the replay reaches ready_for_review — the state that
    means "a usable source exists", not "anyone may watch this"."""
    if entitlement.publish_state != "ready_for_review":
        return False
    if not _claim(db, entitlement, "prepared_notified_at"):
        return False
    org, event, source, addresses = _context(db, entitlement)
    if not addresses:
        return False

    validation = (source.validation_status if source and source.validation_status
                  else "Not validated")
    _ledger(db, entitlement, "prepared")
    media_comms._queue(background, email_mod.send_replay_prepared_email, addresses,
                       event_title=event.title if event else "an event",
                       asset_reference=reference(entitlement),
                       source_recording=(f"Recording {str(source.id)[:8]}" if source
                                         else "Selected at publication"),
                       prepared_at=org_comms.org_timestamp(org, _now()),
                       intended_access=f"{entitlement.scope.title()} replay",
                       validation_state=str(validation).title(),
                       org_name=org.name if org else "your Organization",
                       test_mode=media_comms.is_test_org(org))
    return True


def notify_published(db: Session, background, entitlement) -> bool:
    if entitlement.publish_state != "published":
        return False
    if not _claim(db, entitlement, "published_notified_at"):
        return False
    org, event, source, addresses = _context(db, entitlement)
    if not addresses:
        return False

    # "Published" and "watchable right now" are not the same instant: publish_replay queues
    # a watermark burn and routers/events.py withholds the URL until it is ready.
    delivery = {
        "ready": "Available to viewers now",
        "pending": "Publishing — the watermarked copy is still being prepared, so viewers "
                   "cannot watch it yet",
        "failed": "Not deliverable — the watermark step failed and viewers cannot watch it",
    }.get(entitlement.watermark_status, "Not applicable")

    _ledger(db, entitlement, "published")
    media_comms._queue(background, email_mod.send_replay_published_email, addresses,
                       event_title=event.title if event else "an event",
                       asset_reference=reference(entitlement),
                       published_at=org_comms.org_timestamp(org, _now()),
                       audience=f"{entitlement.scope.title()} — everyone entitled to this "
                                f"event's replay",
                       availability_window=_window(org, entitlement),
                       download_permission=("Permitted" if entitlement.download_permission
                                            else "Not permitted"),
                       delivery_state=delivery,
                       org_name=org.name if org else "your Organization",
                       test_mode=media_comms.is_test_org(org))
    return True


def notify_access_changed(db: Session, background, entitlement, previous: str) -> bool:
    """Any governed publish_state move that is not a publish, a withdrawal or an expiry."""
    org, event, source, addresses = _context(db, entitlement)
    if not addresses:
        return False
    entitlement.access_changed_notified_at = _now()
    db.commit()
    _ledger(db, entitlement, "access_changed", f"{previous} -> {entitlement.publish_state}")
    media_comms._queue(background, email_mod.send_replay_access_changed_email, addresses,
                       event_title=event.title if event else "an event",
                       asset_reference=reference(entitlement),
                       previous_access=ACCESS_LABELS.get(previous, previous.title()),
                       current_access=ACCESS_LABELS.get(entitlement.publish_state,
                                                        entitlement.publish_state.title()),
                       effective_at=org_comms.org_timestamp(org, _now()),
                       availability_window=_window(org, entitlement),
                       org_name=org.name if org else "your Organization",
                       test_mode=media_comms.is_test_org(org))
    return True


def notify_withdrawn(db: Session, background, entitlement) -> bool:
    if entitlement.publish_state != "withheld":
        return False
    if not _claim(db, entitlement, "withdrawn_notified_at"):
        return False
    org, event, source, addresses = _context(db, entitlement)
    if not addresses:
        return False
    _ledger(db, entitlement, "withdrawn", entitlement.withdraw_reason)
    media_comms._queue(background, email_mod.send_replay_withdrawn_email, addresses,
                       event_title=event.title if event else "an event",
                       asset_reference=reference(entitlement),
                       effective_at=org_comms.org_timestamp(org, entitlement.withdrawn_at),
                       owner=_owner_label(source, db),
                       reason=entitlement.withdraw_reason or "Not recorded",
                       entitlement_effect=ENTITLEMENT_EFFECT,
                       org_name=org.name if org else "your Organization",
                       test_mode=media_comms.is_test_org(org))
    return True


def notify_expired(db: Session, background, entitlement) -> bool:
    if entitlement.publish_state != "expired":
        return False
    if not _claim(db, entitlement, "expired_notified_at"):
        return False
    org, event, source, addresses = _context(db, entitlement)
    if not addresses:
        return False
    _ledger(db, entitlement, "expired")
    media_comms._queue(background, email_mod.send_replay_expired_email, addresses,
                       event_title=event.title if event else "an event",
                       asset_reference=reference(entitlement),
                       expired_at=org_comms.org_timestamp(org, entitlement.expires_at),
                       owner=_owner_label(source, db),
                       org_name=org.name if org else "your Organization",
                       test_mode=media_comms.is_test_org(org))
    return True


# ── expiry sweeper ──────────────────────────────────────────────────────────────────────

def expire_due(db: Session, background=None) -> dict:
    """Move every published replay past its committed expiry to `expired`.

    This is what makes the EXPIRED variant truthful. `expires_at` was a stored date nothing
    honoured until replay access started checking it; announcing an expiry while the replay
    stayed watchable would have been a false claim about an access control.
    """
    from ..models import ReplayEntitlement

    background = background or media_comms._Bg()
    now = _now()
    expired = 0
    for entitlement in db.scalars(
        select(ReplayEntitlement).where(
            ReplayEntitlement.publish_state == "published",
            ReplayEntitlement.expires_at.isnot(None),
            ReplayEntitlement.expires_at <= now)
    ).all():
        try:
            if expire(db, entitlement) is not None:
                notify_expired(db, background, entitlement)
                expired += 1
        except Exception:  # noqa: BLE001 — one bad row must not stop the sweep
            log.exception("replay expiry failed for entitlement %s", entitlement.id)
            db.rollback()
    return {"expired": expired}
