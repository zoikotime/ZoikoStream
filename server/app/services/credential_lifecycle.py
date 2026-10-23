"""API credential expiry, rotation and revocation notices (ZST-EC-001 DEV-003 / DEV-004).

Every notice here reports committed credential state, and every claim it makes is bounded by
what the platform actually enforces. Two boundaries matter enough to state up front:

  * **No Zoiko Stream endpoint authenticates an API key.** `key_hash` is written at creation
    and never read. So an expired or revoked credential is marked as such in the record, and
    the email says exactly that — it never claims access was blocked, because nothing blocks
    it. `ACCESS_NOTE` is the single place that wording lives.

  * **Dormancy is not implemented.** It can only be asserted from real `last_used_at`
    telemetry, and nothing records a use. Inferring dormancy from `created_at` would state a
    fact about usage nobody measured, so the variant does not exist. Reported as a gap.

Rotation has no overlap window for the same reason: an overlap is a promise about what an
authentication path will accept, and there is no authentication path to honour it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..crud import admin as admin_crud
from ..email import UnsafeLinkError
from ..models import Organization
from . import developer_comms, org_comms

log = logging.getLogger(__name__)

TICKER_INTERVAL_SECONDS = 3600.0

# The one place the truth about enforcement is worded. If API-key authentication is ever
# implemented, this constant changes and every DEV-003 message changes with it.
ACCESS_NOTE = (
    "This credential is marked expired in Zoiko Stream. API-key authentication is not yet "
    "enforced by any Zoiko Stream endpoint, so no access was withdrawn"
)
ACCESS_NOTE_WARNING = (
    "No access change is scheduled: API-key authentication is not yet enforced by any "
    "Zoiko Stream endpoint"
)
NO_OVERLAP_NOTE = "Revoked immediately — there is no overlap window"

EMERGENCY_REASONS = {
    "suspected_exposure": "Suspected credential exposure",
    "security_review": "Security review",
    "policy": "Policy requirement",
}
EMERGENCY_NEXT_STEPS = (
    "Create a replacement credential from the Developer console and move your integrations "
    "onto it. If you believe this was unexpected, contact Zoiko Stream Support."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _queue(background, send, addresses, **kwargs) -> None:
    try:
        for address in addresses:
            background.add_task(send, address, **kwargs)
    except UnsafeLinkError:
        log.exception("Credential notice not queued: APP_URL unsafe for this environment")


def _facts(org: Organization, record: dict) -> dict:
    return {
        "org_name": org.name,
        "credential_name": record.get("label") or "Unnamed credential",
        "fingerprint": admin_crud.key_fingerprint(record),
    }


# ── DEV-004 revocation / rotation ───────────────────────────────────────────────────────

def notify_revoked(db: Session, background, *, org: Organization, key_id: str,
                   actor, emergency: bool = False,
                   reason_code: str = "administrative") -> bool:
    """Announce a committed revocation. Called AFTER the record is written."""
    record = admin_crud.get_api_key_record(org, key_id)
    if record is None:
        return False
    marker = "dev_004_emergency_notified_at" if emergency else "dev_004_revoked_notified_at"
    if not admin_crud.claim_key_notification(db, org, key_id, marker):
        return False

    addresses = developer_comms.recipients(db, org, actor)
    if not addresses:
        return False
    facts = _facts(org, record)
    revoked_display = org_comms.org_timestamp(
        org, developer_comms._as_datetime(record.get("revoked_at")) or _now())

    if emergency:
        _queue(background, email_mod.send_credential_emergency_revoked_email, addresses,
               org_name=facts["org_name"], credential_name=facts["credential_name"],
               fingerprint=facts["fingerprint"], revoked_at=revoked_display,
               reason_label=EMERGENCY_REASONS.get(reason_code, "Security requirement"),
               next_steps=EMERGENCY_NEXT_STEPS)
    else:
        _queue(background, email_mod.send_credential_revoked_email, addresses,
               org_name=facts["org_name"], credential_name=facts["credential_name"],
               fingerprint=facts["fingerprint"], revoked_at=revoked_display,
               revoked_by=(actor.email if actor else "An authorized administrator"),
               scope_summary=developer_comms.NO_SCOPES)
    return True


def notify_rotated(db: Session, background, *, org: Organization, old_record: dict,
                   new_key_id: str, actor) -> bool:
    """Announce a committed rotation, naming both fingerprints and neither secret."""
    new_record = admin_crud.get_api_key_record(org, new_key_id)
    if new_record is None or old_record is None:
        return False
    if not admin_crud.claim_key_notification(db, org, new_key_id,
                                             "dev_004_rotated_notified_at"):
        return False

    addresses = developer_comms.recipients(db, org, actor)
    if not addresses:
        return False
    _queue(background, email_mod.send_credential_rotated_email, addresses,
           org_name=org.name,
           credential_name=new_record.get("label") or "Unnamed credential",
           old_fingerprint=admin_crud.key_fingerprint(old_record),
           new_fingerprint=admin_crud.key_fingerprint(new_record),
           rotated_at=org_comms.org_timestamp(
               org, developer_comms._as_datetime(new_record.get("rotated_at")) or _now()),
           rotated_by=(actor.email if actor else "An authorized administrator"),
           overlap_note=NO_OVERLAP_NOTE)
    return True


# ── DEV-003 expiry ──────────────────────────────────────────────────────────────────────
#
# Reminders go out 30, 14, 7, 3 and 1 day(s) before a key expires (admin_crud.
# KEY_REMINDER_DAYS), then one "expired" notice. Each threshold is sent at most once per key:
#
#   * the claim is a marker on the key record, written under the organization row lock
#     (admin_crud.claim_key_notification), so a second sweep, or a second process during a
#     ticker leadership hand-over, finds it set and sends nothing;
#   * a key first seen inside a later window (created with a 10-day lifetime, or a sweep that
#     was down for a week) gets that window's reminder once, and the earlier, larger windows
#     are recorded as superseded in the same write — never sent late, one after another;
#   * if NO recipient's message was accepted (a provider outage), the claim is released so the
#     next hourly sweep tries again, up to MAX_SEND_ATTEMPTS; a partial delivery keeps the
#     claim, because resending would duplicate the mail that did arrive.
#
# Nothing here ever changes a key's expiry or makes an expired key usable, and the reminder's
# outcome never affects the key: expiry is decided by admin_crud.key_status from the clock.

MAX_SEND_ATTEMPTS = 6
EXPIRED_NOTICE_WINDOW = timedelta(days=30)
_LEGACY_WARNING_MARKER = "dev_003_warning_notified_at"   # the old single 7-day warning
_EXPIRED_MARKER = "dev_003_expired_notified_at"


def reminder_marker(days: int) -> str:
    return f"dev_003_warning_{days}d_notified_at"


def due_reminder(record: dict, now: datetime) -> int | None:
    """The reminder threshold (in days) this key is due for now, or None.

    The most urgent window the key is inside, unless that window's reminder (or, for keys
    warned before the schedule existed, the old 7-day warning) has already been claimed."""
    if admin_crud.key_status(record, now) != admin_crud.KEY_ACTIVE:
        return None
    expires = admin_crud._parse_ts(record.get("expires_at"))
    if expires is None:
        return None
    seconds = (expires - now).total_seconds()
    inside = [d for d in admin_crud.KEY_REMINDER_DAYS if seconds <= d * 86400]
    if not inside:
        return None
    days = min(inside)
    if record.get(reminder_marker(days)):
        return None
    if record.get(_LEGACY_WARNING_MARKER) and days >= 7:
        return None
    return days


def _creator(db: Session, org: Organization, record: dict):
    """The member who minted the key, while they are still an active member of THIS
    organization — a platform operator who minted it under a support session is not."""
    from ..models import User

    raw = record.get("created_by")
    if not raw:
        return None
    try:
        user = db.get(User, uuid.UUID(str(raw)))
    except (ValueError, TypeError):
        return None
    return user if user is not None and user.org_id == org.id else None


def _deliver(send, addresses, **kwargs) -> int:
    """Send one message per address now; how many the provider accepted. Never raises."""
    accepted = 0
    for address in addresses:
        try:
            accepted += 1 if send(address, **kwargs) else 0
        except UnsafeLinkError:
            log.exception("Credential notice not sent: APP_URL unsafe for this environment")
        except Exception:  # noqa: BLE001 — one bad send must not stop the sweep
            log.exception("Credential notice failed")
    return accepted


def _after_send(db: Session, org: Organization, key_id: str, marker: str, accepted: int,
                recipients: int) -> bool:
    if accepted:
        if accepted < recipients:
            log.warning("Credential notice %s for key %s: %s of %s recipients accepted",
                        marker, key_id, accepted, recipients)
        return True
    failures = admin_crud.release_key_notification(db, org, key_id, marker)
    if failures >= MAX_SEND_ATTEMPTS:
        # Re-claim for good: an address the provider keeps refusing must not be retried hourly
        # for ever. The key itself is untouched either way.
        admin_crud.claim_key_notification(db, org, key_id, marker)
        log.error("Credential notice %s for key %s abandoned after %s failed attempts",
                  marker, key_id, failures)
    else:
        log.warning("Credential notice %s for key %s not delivered; will retry (attempt %s of %s)",
                    marker, key_id, failures, MAX_SEND_ATTEMPTS)
    return False


def notify_expiring(db: Session, background=None, *, org: Organization, record: dict,
                    days: int | None = None, now: datetime | None = None) -> bool:
    """Send this key's due reminder (threshold `days`, or whichever is due). True when sent."""
    now = now or _now()
    days = days or due_reminder(record, now)
    if days is None:
        return False
    key_id = record["id"]
    marker = reminder_marker(days)
    superseded = tuple(reminder_marker(d) for d in admin_crud.KEY_REMINDER_DAYS if d > days)
    if not admin_crud.claim_key_notification(db, org, key_id, marker, also=superseded):
        return False
    addresses = developer_comms.recipients(db, org, _creator(db, org, record))
    if not addresses:
        return False
    expires = admin_crud._parse_ts(record.get("expires_at"))
    facts = _facts(org, record)
    accepted = _deliver(email_mod.send_credential_expiring_email, addresses,
                        org_name=facts["org_name"], credential_name=facts["credential_name"],
                        fingerprint=facts["fingerprint"],
                        expires_at=org_comms.org_timestamp(org, expires),
                        seconds_left=int((expires - now).total_seconds()),
                        access_note=ACCESS_NOTE_WARNING)
    return _after_send(db, org, key_id, marker, accepted, len(addresses))


def notify_expired(db: Session, background=None, *, org: Organization, record: dict) -> bool:
    key_id = record["id"]
    if not admin_crud.claim_key_notification(db, org, key_id, _EXPIRED_MARKER):
        return False
    addresses = developer_comms.recipients(db, org, _creator(db, org, record))
    if not addresses:
        return False
    facts = _facts(org, record)
    accepted = _deliver(email_mod.send_credential_expired_email, addresses,
                        org_name=facts["org_name"], credential_name=facts["credential_name"],
                        fingerprint=facts["fingerprint"],
                        expired_at=org_comms.org_timestamp(
                            org, admin_crud._parse_ts(record.get("expires_at"))),
                        access_note=ACCESS_NOTE)
    return _after_send(db, org, key_id, _EXPIRED_MARKER, accepted, len(addresses))


class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — a ticker must not die on one bad send
            log.exception("Credential notice failed")


def sweep(db: Session, background=None, now: datetime | None = None) -> dict:
    """One pass: send due reminders, record lapsed keys as expired and announce them once.

    Reads organizations that hold any key at all; the keys live in a JSON column on the
    organization, so there is no per-key index to narrow by — the scan is one pass over the
    organizations table per hour, and everything per key is in memory. `background` is kept
    for callers' signatures: reminders are sent synchronously here (this already runs off the
    request path), because whether one was ACCEPTED decides whether it is retried."""
    now = now or _now()
    warned = expired = 0
    for org in db.scalars(select(Organization).where(Organization.api_keys.isnot(None))).all():
        for record in list(org.api_keys or []):
            if admin_crud._parse_ts(record.get("expires_at")) is None:
                continue
            state = admin_crud.key_status(record, now)
            if state == admin_crud.KEY_REVOKED:
                continue
            if state == admin_crud.KEY_EXPIRED:
                admin_crud.mark_key_expired(db, org, record["id"])
                fresh = admin_crud.get_api_key_record(org, record["id"]) or record
                # One informational notice, for a lapse that is news. A key that expired long
                # before anything could announce it is recorded, not mailed months late.
                lapsed = admin_crud._parse_ts(record.get("expires_at"))
                if now - lapsed <= EXPIRED_NOTICE_WINDOW and notify_expired(db, org=org, record=fresh):
                    expired += 1
            elif notify_expiring(db, org=org, record=record, now=now):
                warned += 1
    return {"warned": warned, "expired": expired}


async def run_credential_sweeper(interval: float = TICKER_INTERVAL_SECONDS) -> None:
    """Background ticker started from the app lifespan."""
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
        except Exception:  # noqa: BLE001
            log.exception("Credential sweep failed")
