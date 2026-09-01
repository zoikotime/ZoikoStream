"""Retention, legal hold and deletion (ZST-EC-001 MED-011).

Three things existed before this module and three did not.

Existed: the `LiveRecording.retention_policy_version`, `retention_expires_at` and
`legal_hold` columns; a `GovernanceRecord(kind="legal_hold")` that only a super_admin can
open or resolve; and a refusal in `delete_recording` when either of those says the asset is
held. Legal hold was already enforced server-side, which is why this module extends that
check rather than inventing it.

Did not exist: anything that SET a retention date, any way to extend one, and any record of
what a deletion actually did. `delete_recording` called `livekit.delete_object()`, discarded
the boolean it returns, and deleted the row unconditionally — so a storage deletion that
failed left an orphaned object in the bucket with nothing anywhere recording that it was
supposed to be gone. `delete_asset` below is the governed replacement.

**Retention is a keep-guarantee, not a delete-timer.** `retention_expires_at` is the date the
asset becomes ELIGIBLE for deletion. Before that date it is retained, and deletion is refused
for an ordinary org admin. There is no automatic deletion scheduler in this platform, so
nothing here — and no template in the MED-011 family — says a recording "will be deleted" on
a date. It becomes deletable, and a human acts.

**Rollout safety.** A retention date is assigned only at finalization, going forward. Every
recording with `retention_expires_at IS NULL` — every legacy row, and every row still being
captured — keeps exactly today's deletion behaviour. A governance control that retroactively
locks assets nobody was told were locked would be a worse failure than the gap it closes.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..models import (
    DELETION_COMPLETED,
    DELETION_DELETING,
    DELETION_FAILED,
    LEGAL_HOLD_CATEGORIES,
    RETENTION_REASON_CATEGORIES,
    Event,
    LegalHoldContact,
    LiveRecording,
    MediaAssetEvent,
    Organization,
    PlatformSetting,
    RetentionExtension,
    User,
)
from . import livekit, media_comms, org_comms

log = logging.getLogger(__name__)

# ── policy ──────────────────────────────────────────────────────────────────────────────
# Stored in the existing platform_settings key/value table rather than as a constant here,
# so an operator can answer "under what policy was this kept?" with a version string. The
# defaults below are what an unconfigured platform uses, and they are reported by that same
# version string — never as a blank.

POLICY_KEY = "media_retention_policy"
DEFAULT_POLICY = {
    "version": "default-v1",
    "retention_days": 365,
    "warning_days": 14,
}

# Safe deletion-failure categories. A provider exception is never surfaced: cloud storage
# errors routinely embed bucket names, object keys and request signatures.
FAILURE_STORAGE_UNAVAILABLE = "Storage was unavailable or the object could not be removed"
FAILURE_NOT_CONFIGURED = "Object storage is not configured for this platform"

# What a completed deletion may truthfully claim. Zoiko Steam manages its own objects; it
# cannot speak for its cloud provider's backup retention, so it does not.
RESIDUAL_COPIES = (
    "Zoiko Steam does not retain its own additional copies. Backup retention inside the "
    "underlying cloud storage provider is outside Zoiko Steam's control and is not claimed "
    "to be erased."
)
RETAINED_METADATA = (
    "The deletion record, the asset's lifecycle history and the audit log are retained as "
    "governance evidence. These contain no media."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def policy(db: Session) -> dict:
    """The committed retention policy. Falls back to DEFAULT_POLICY, reported by version."""
    row = db.get(PlatformSetting, POLICY_KEY)
    stored = (row.value if row and isinstance(row.value, dict) else None) or {}
    merged = dict(DEFAULT_POLICY)
    for key in ("version", "retention_days", "warning_days"):
        if stored.get(key) not in (None, ""):
            merged[key] = stored[key]
    return merged


def _claim(db: Session, row, column: str) -> bool:
    if getattr(row, column) is not None:
        return False
    setattr(row, column, _now())
    db.commit()
    return True


def reference(recording: LiveRecording) -> str:
    """Safe asset reference — never a storage key or a signed URL."""
    return str(recording.id)[:8]


def _ledger(db: Session, recording: LiveRecording, transition: str,
            detail: str | None = None, actor_id=None) -> None:
    db.add(MediaAssetEvent(org_id=recording.org_id, event_id=recording.event_id,
                           recording_id=recording.id, family="MED-011",
                           transition=transition, detail=detail, actor_id=actor_id))
    db.commit()


# ── recipients ──────────────────────────────────────────────────────────────────────────

def contacts(db: Session, org_id, kind: str) -> list[str]:
    """Stored governance / legal-hold contacts for one Organization.

    This list exists because the person who asked for a legal hold is frequently not a
    Zoiko Steam user at all — counsel, or a compliance officer at the customer. Without it
    the only options were to mail org admins and call them "legal contacts", or to skip the
    notification entirely.
    """
    if not org_id:
        return []
    rows = db.scalars(
        select(LegalHoldContact).where(
            LegalHoldContact.org_id == org_id, LegalHoldContact.active.is_(True),
            LegalHoldContact.kind == kind)
    ).all()
    return [r.email for r in rows]


def governance_recipients(db: Session, recording: LiveRecording, *,
                          include_legal: bool = False) -> list[str]:
    """Asset owner + Organization owner + org admins + stored governance contacts.

    Org admins are the documented fallback, not a pretence at a routing feature — the same
    resolution services/media_comms.operators already uses and reports as a limitation.
    """
    owner = db.get(User, recording.created_by) if recording.created_by else None
    org = db.get(Organization, recording.org_id) if recording.org_id else None
    holder = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    addresses = org_comms.recipients(owner, holder,
                                     *org_comms.org_admins(db, recording.org_id))
    extra = contacts(db, recording.org_id, "governance")
    if include_legal:
        extra += contacts(db, recording.org_id, "legal")
    for address in extra:
        if address and address.lower() not in {a.lower() for a in addresses}:
            addresses.append(address)
    return addresses


def _context(db: Session, recording: LiveRecording):
    org = db.get(Organization, recording.org_id) if recording.org_id else None
    event = db.get(Event, recording.event_id) if recording.event_id else None
    return org, event


def _owner_label(db: Session, recording: LiveRecording) -> str:
    owner = db.get(User, recording.created_by) if recording.created_by else None
    return owner.email if owner else "An authorized operator"


# ── retention assignment ────────────────────────────────────────────────────────────────

def assign_retention(db: Session, recording: LiveRecording) -> LiveRecording:
    """Stamp the committed retention policy onto a finalized recording.

    Idempotent, and never shortens an existing date — a recording whose retention was
    extended by approval must not have that extension quietly reverted by a later
    finalization pass.
    """
    if recording.retention_expires_at is not None:
        return recording
    current = policy(db)
    base = recording.stopped_at or recording.created_at or _now()
    recording.retention_policy_version = str(current["version"])
    recording.retention_expires_at = base + timedelta(days=int(current["retention_days"]))
    db.commit()
    _ledger(db, recording, "retention_assigned",
            f"{current['version']} — until {recording.retention_expires_at.isoformat()}")
    return recording


def deletion_eligible(db: Session, recording: LiveRecording) -> tuple[bool, str | None]:
    """Whether this asset may be deleted right now, and why not.

    Order matters: legal hold is checked first because it overrides everything, including a
    retention window that has already expired.
    """
    from ..crud import admin as admin_crud

    if recording.legal_hold or admin_crud.event_under_legal_hold(db, recording.event_id):
        return False, ("This recording is under legal hold and cannot be deleted. Contact "
                       "platform support to release the hold.")
    pending = db.scalar(
        select(RetentionExtension).where(
            RetentionExtension.recording_id == recording.id,
            RetentionExtension.state == "pending")
    )
    if pending is not None:
        return False, ("A retention extension is pending review for this recording. It "
                       "cannot be deleted while that decision is open.")
    if (recording.retention_expires_at is not None
            and recording.retention_expires_at > _now()):
        return False, ("This recording is inside its retention period until "
                       f"{recording.retention_expires_at.date().isoformat()} and cannot be "
                       "deleted yet.")
    return True, None


# ── legal hold ──────────────────────────────────────────────────────────────────────────

def place_hold(db: Session, background, recording: LiveRecording, *, actor: User,
               category: str, hold_reference: str) -> LiveRecording:
    """Place a legal hold. Platform governance only — enforced by the calling route.

    Stores a CATEGORY and an opaque REFERENCE. The hold's actual subject matter is
    frequently privileged and has no column here, which is the structural reason a
    notification cannot leak it.
    """
    if category not in LEGAL_HOLD_CATEGORIES:
        raise ValueError(f"unknown legal hold category: {category!r}")
    recording.legal_hold = True
    recording.hold_category = category
    recording.hold_reference = hold_reference[:80]
    recording.hold_set_at = _now()
    recording.hold_set_by = actor.id
    recording.hold_released_notified_at = None
    db.commit()
    _ledger(db, recording, "legal_hold_placed", category, actor_id=actor.id)
    notify_hold(db, background, recording, released=False)
    return recording


def release_hold(db: Session, background, recording: LiveRecording, *,
                 actor: User) -> LiveRecording:
    if not recording.legal_hold:
        return recording
    recording.legal_hold = False
    released_at = _now()
    recording.hold_notified_at = None
    db.commit()
    _ledger(db, recording, "legal_hold_released", recording.hold_category,
            actor_id=actor.id)
    notify_hold(db, background, recording, released=True, released_at=released_at)
    return recording


def notify_hold(db: Session, background, recording: LiveRecording, *, released: bool,
                released_at: datetime | None = None) -> bool:
    marker = "hold_released_notified_at" if released else "hold_notified_at"
    if not _claim(db, recording, marker):
        return False
    org, event = _context(db, recording)
    addresses = governance_recipients(db, recording, include_legal=True)
    if not addresses:
        return False
    media_comms._queue(background, email_mod.send_legal_hold_email, addresses,
                       event_title=event.title if event else "an event",
                       asset_reference=reference(recording),
                       placed_at=org_comms.org_timestamp(org, recording.hold_set_at),
                       hold_reference=recording.hold_reference or "Not recorded",
                       hold_category=(recording.hold_category or "not_recorded")
                       .replace("_", " ").title(),
                       owner=_owner_label(db, recording),
                       org_name=org.name if org else "your Organization",
                       released=released,
                       released_at=org_comms.org_timestamp(org, released_at)
                       if released_at else None)
    return True


# ── retention extension (maker-checker) ─────────────────────────────────────────────────

def request_extension(db: Session, background, recording: LiveRecording, *, requester: User,
                      reason_category: str, requested_until: datetime) -> RetentionExtension:
    """Ask to keep a recording longer. Grants nothing by itself.

    Deliberately separate from approval: letting the asset owner extend their own retention
    unilaterally would make the retention policy advisory, which is the opposite of what it
    is for.
    """
    if reason_category not in RETENTION_REASON_CATEGORIES:
        raise ValueError(f"unknown reason category: {reason_category!r}")
    if recording.retention_expires_at is not None and requested_until <= recording.retention_expires_at:
        raise ValueError("The requested retention date must be later than the current one.")

    extension = RetentionExtension(
        org_id=recording.org_id, event_id=recording.event_id, recording_id=recording.id,
        state="pending", reason_category=reason_category, requested_until=requested_until,
        previous_until=recording.retention_expires_at, requested_by=requester.id)
    db.add(extension)
    db.commit()
    db.refresh(extension)
    _ledger(db, recording, "retention_extension_requested", reason_category,
            actor_id=requester.id)
    notify_extension_requested(db, background, recording, extension, requester)
    return extension


def decide_extension(db: Session, background, extension: RetentionExtension, *,
                     approver: User, approve: bool, granted_until: datetime | None = None,
                     note: str | None = None) -> RetentionExtension:
    """Approve or decline. Only an approval writes a new retention date, and it writes it in
    the same transaction as the decision — so the record and the effect cannot disagree."""
    if extension.state != "pending":
        raise ValueError("This retention extension has already been decided.")
    recording = db.get(LiveRecording, extension.recording_id)

    extension.state = "approved" if approve else "declined"
    extension.decided_by = approver.id
    extension.decided_at = _now()
    extension.decision_note = (note or "")[:2000] or None
    if approve:
        granted = granted_until or extension.requested_until
        extension.granted_until = granted
        if recording is not None:
            recording.retention_expires_at = granted
            # Re-arm the warning so the new deadline is announced in its own right.
            recording.retention_warned_at = None
    db.commit()
    db.refresh(extension)
    if recording is not None:
        _ledger(db, recording, f"retention_extension_{extension.state}",
                extension.reason_category, actor_id=approver.id)
        notify_extension_decided(db, background, recording, extension, approver)
    return extension


def notify_extension_requested(db: Session, background, recording: LiveRecording,
                               extension: RetentionExtension, requester: User) -> bool:
    if not _claim(db, extension, "requested_notified_at"):
        return False
    org, event = _context(db, recording)
    addresses = governance_recipients(db, recording)
    if not addresses:
        return False
    media_comms._queue(background, email_mod.send_retention_extension_requested_email,
                       addresses,
                       event_title=event.title if event else "an event",
                       asset_reference=reference(recording),
                       requester=requester.email,
                       reason_category=extension.reason_category.replace("_", " ").title(),
                       current_until=org_comms.org_timestamp(org, extension.previous_until)
                       if extension.previous_until else "No retention date set",
                       requested_until=org_comms.org_timestamp(org,
                                                               extension.requested_until),
                       org_name=org.name if org else "your Organization")
    return True


def notify_extension_decided(db: Session, background, recording: LiveRecording,
                             extension: RetentionExtension, approver: User) -> bool:
    if not _claim(db, extension, "decided_notified_at"):
        return False
    org, event = _context(db, recording)
    addresses = governance_recipients(db, recording)
    if not addresses:
        return False
    requester = db.get(User, extension.requested_by) if extension.requested_by else None
    media_comms._queue(background, email_mod.send_retention_extension_decided_email,
                       addresses,
                       event_title=event.title if event else "an event",
                       asset_reference=reference(recording),
                       decision=extension.state,
                       requester=requester.email if requester else "A former member",
                       approver=approver.email,
                       reason_category=extension.reason_category.replace("_", " ").title(),
                       previous_until=org_comms.org_timestamp(org, extension.previous_until)
                       if extension.previous_until else "No retention date set",
                       new_until=org_comms.org_timestamp(org, extension.granted_until)
                       if extension.granted_until else "Unchanged",
                       effective_at=org_comms.org_timestamp(org, extension.decided_at),
                       org_name=org.name if org else "your Organization")
    return True


# ── retention warning ───────────────────────────────────────────────────────────────────

def notify_retention_warning(db: Session, background, recording: LiveRecording) -> bool:
    """Sent once per committed retention deadline. Re-armed when an extension moves it."""
    if recording.retention_expires_at is None:
        return False
    if not _claim(db, recording, "retention_warned_at"):
        return False
    org, event = _context(db, recording)
    addresses = governance_recipients(db, recording)
    if not addresses:
        return False
    remaining = max(0, (recording.retention_expires_at - _now()).days)
    _ledger(db, recording, "retention_warning")
    media_comms._queue(background, email_mod.send_retention_warning_email, addresses,
                       event_title=event.title if event else "an event",
                       asset_reference=reference(recording),
                       retention_until=org_comms.org_timestamp(
                           org, recording.retention_expires_at),
                       policy_version=recording.retention_policy_version or "Not recorded",
                       owner=_owner_label(db, recording),
                       days_remaining=remaining,
                       # There is no automatic deletion scheduler. Saying the recording
                       # "will be deleted" on this date would be false.
                       deletion_behaviour=(
                           "The recording becomes eligible for deletion. Zoiko Steam does "
                           "not delete it automatically — an authorized administrator must "
                           "act. Until then it remains available."),
                       org_name=org.name if org else "your Organization")
    return True


def sweep_retention(db: Session, background=None) -> dict:
    """One pass over recordings approaching their committed retention deadline."""
    background = background or media_comms._Bg()
    current = policy(db)
    threshold = _now() + timedelta(days=int(current["warning_days"]))
    warned = 0
    for recording in db.scalars(
        select(LiveRecording).where(
            LiveRecording.retention_expires_at.isnot(None),
            LiveRecording.retention_expires_at <= threshold,
            LiveRecording.retention_warned_at.is_(None),
            LiveRecording.legal_hold.is_(False))
    ).all():
        try:
            if notify_retention_warning(db, background, recording):
                warned += 1
        except Exception:  # noqa: BLE001 — one bad row must not stop the sweep
            log.exception("retention warning failed for recording %s", recording.id)
            db.rollback()
    return {"warned": warned}


# ── deletion lifecycle ──────────────────────────────────────────────────────────────────

def delete_asset(db: Session, background, recording: LiveRecording, *,
                 actor: User) -> tuple[bool, str | None]:
    """The governed deletion. Returns (deleted, refusal_reason).

    Order is the whole point:

        1. eligibility (legal hold, pending extension, retention window)
        2. mark the intent, so a crash between the storage call and the row delete is
           visible afterwards rather than silent
        3. delete the storage object and READ THE RESULT
        4. commit the outcome
        5. notify

    Step 3 is the fix for the previous behaviour, which called `livekit.delete_object`,
    threw the boolean away, and deleted the row regardless — leaving an orphaned object that
    nothing recorded as needing removal.
    """
    eligible, reason = deletion_eligible(db, recording)
    if not eligible:
        return False, reason

    recording.deletion_status = DELETION_DELETING
    recording.deletion_requested_at = _now()
    recording.deletion_requested_by = actor.id
    db.commit()

    # Snapshot everything the notification needs BEFORE the row is gone.
    org, event = _context(db, recording)
    snapshot = {
        "event_title": event.title if event else "an event",
        "asset_reference": reference(recording),
        "org_name": org.name if org else "your Organization",
        "requester": actor.email,
        "org": org,
        "recording_id": recording.id,
        "event_id": recording.event_id,
        "org_id": recording.org_id,
        "size_bytes": recording.size_bytes,
    }
    addresses = governance_recipients(db, recording)

    had_object = bool(recording.file_url)
    removed = livekit.delete_object(recording.file_url) if had_object else False
    storage_ok = removed or not had_object
    if had_object and not removed:
        # Distinguish "storage not configured" from "storage rejected the delete": the
        # remediation is different, and neither is the provider's exception text.
        category = (FAILURE_NOT_CONFIGURED if not livekit.gcs_configured()
                    else FAILURE_STORAGE_UNAVAILABLE)
        recording.deletion_status = DELETION_FAILED
        recording.deletion_failure_category = category
        db.commit()
        _ledger(db, recording, "deletion_failed", category, actor_id=actor.id)
        notify_deletion_failed(db, background, recording, addresses, snapshot, category)
        return False, ("The recording could not be removed from storage. It has not been "
                       "deleted and remains subject to its retention policy.")

    # Storage is clear. Now the row, and only now.
    if recording.size_bytes and org is not None:
        org.storage_used_gb = round(
            max(0.0, float(org.storage_used_gb or 0)
                - recording.size_bytes / (1024 ** 3)), 3)

    db.add(MediaAssetEvent(org_id=snapshot["org_id"], event_id=snapshot["event_id"],
                           recording_id=snapshot["recording_id"], family="MED-011",
                           transition="deleted",
                           detail=f"Storage object removed: {had_object}",
                           actor_id=actor.id))
    db.delete(recording)
    db.commit()

    notify_deleted(db, background, addresses, snapshot, had_object=had_object)
    return True, None


def notify_deleted(db: Session, background, addresses: list[str], snapshot: dict,
                   *, had_object: bool) -> bool:
    """Sent only after the deletion actually succeeded.

    Takes a snapshot rather than a row: by the time this runs the LiveRecording no longer
    exists, which is precisely why the MediaAssetEvent ledger is written first.
    """
    if not addresses:
        return False
    org = snapshot.get("org")
    media_comms._queue(background, email_mod.send_recording_deleted_email, addresses,
                       event_title=snapshot["event_title"],
                       asset_reference=snapshot["asset_reference"],
                       deleted_at=org_comms.org_timestamp(org, _now()),
                       requester=snapshot["requester"],
                       primary_removed=("Removed from object storage" if had_object
                                        else "No stored object existed for this recording"),
                       derived_removed=("Watermarked and exported copies generated from "
                                        "this recording are removed with it"),
                       residual_copies=RESIDUAL_COPIES,
                       retained_metadata=RETAINED_METADATA,
                       org_name=snapshot["org_name"])
    return True


def notify_deletion_failed(db: Session, background, recording: LiveRecording,
                           addresses: list[str], snapshot: dict, category: str) -> bool:
    if not _claim(db, recording, "deletion_failed_notified_at"):
        return False
    if not addresses:
        return False
    org = snapshot.get("org")
    media_comms._queue(background, email_mod.send_recording_deletion_failed_email, addresses,
                       event_title=snapshot["event_title"],
                       asset_reference=snapshot["asset_reference"],
                       failed_at=org_comms.org_timestamp(org, _now()),
                       failure_category=category,
                       current_state=("The recording still exists and is still subject to "
                                      "its retention policy. Access is unchanged."),
                       remediation=("Retry the deletion once object storage is reachable. "
                                    "Contact platform support if it continues to fail."),
                       org_name=snapshot["org_name"])
    return True


# ── ticker ──────────────────────────────────────────────────────────────────────────────

SWEEP_INTERVAL_SECONDS = 3600.0


async def run_retention_sweeper(interval: float = SWEEP_INTERVAL_SECONDS) -> None:
    """Retention warnings and replay expiry, on one hourly pass.

    Both are deadline-driven rather than event-driven, so neither has a webhook or a request
    that could carry it. An hour is the right granularity for a date-based control.
    """
    import asyncio

    from ..db import SessionLocal
    from . import replay_comms

    def work():
        db = SessionLocal()
        try:
            sweep_retention(db)
            replay_comms.expire_due(db)
        finally:
            db.close()

    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.to_thread(work)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the ticker
            log.exception("retention sweep failed")
