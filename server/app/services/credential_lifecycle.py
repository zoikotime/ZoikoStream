"""API credential expiry, rotation and revocation notices (ZST-EC-001 DEV-003 / DEV-004).

Every notice here reports committed credential state, and every claim it makes is bounded by
what the platform actually enforces. Two boundaries matter enough to state up front:

  * **No Zoiko Steam endpoint authenticates an API key.** `key_hash` is written at creation
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
    "This credential is marked expired in Zoiko Steam. API-key authentication is not yet "
    "enforced by any Zoiko Steam endpoint, so no access was withdrawn"
)
ACCESS_NOTE_WARNING = (
    "No access change is scheduled: API-key authentication is not yet enforced by any "
    "Zoiko Steam endpoint"
)
NO_OVERLAP_NOTE = "Revoked immediately — there is no overlap window"

EMERGENCY_REASONS = {
    "suspected_exposure": "Suspected credential exposure",
    "security_review": "Security review",
    "policy": "Policy requirement",
}
EMERGENCY_NEXT_STEPS = (
    "Create a replacement credential from the Developer console and move your integrations "
    "onto it. If you believe this was unexpected, contact Zoiko Steam Support."
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

def notify_expiring(db: Session, background, *, org: Organization, record: dict) -> bool:
    key_id = record["id"]
    if not admin_crud.claim_key_notification(db, org, key_id,
                                             "dev_003_warning_notified_at"):
        return False
    addresses = developer_comms.recipients(db, org, None)
    if not addresses:
        return False
    expires = developer_comms._as_datetime(record.get("expires_at"))
    days_left = max(0, (expires - _now()).days) if expires else 0
    facts = _facts(org, record)
    _queue(background, email_mod.send_credential_expiring_email, addresses,
           org_name=facts["org_name"], credential_name=facts["credential_name"],
           fingerprint=facts["fingerprint"],
           expires_at=org_comms.org_timestamp(org, expires), days_left=days_left,
           access_note=ACCESS_NOTE_WARNING)
    return True


def notify_expired(db: Session, background, *, org: Organization, record: dict) -> bool:
    key_id = record["id"]
    if not admin_crud.claim_key_notification(db, org, key_id,
                                             "dev_003_expired_notified_at"):
        return False
    addresses = developer_comms.recipients(db, org, None)
    if not addresses:
        return False
    facts = _facts(org, record)
    _queue(background, email_mod.send_credential_expired_email, addresses,
           org_name=facts["org_name"], credential_name=facts["credential_name"],
           fingerprint=facts["fingerprint"],
           expired_at=org_comms.org_timestamp(
               org, developer_comms._as_datetime(record.get("expires_at"))),
           access_note=ACCESS_NOTE)
    return True


class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — a ticker must not die on one bad send
            log.exception("Credential notice failed")


def sweep(db: Session, background=None) -> dict:
    """One pass: warn credentials nearing expiry, mark and announce lapsed ones."""
    background = background or _Bg()
    now = _now()
    horizon = now + timedelta(days=admin_crud.KEY_EXPIRY_WARNING_DAYS)
    warned = expired = 0

    for org in db.scalars(select(Organization)).all():
        for record in list(org.api_keys or []):
            expires = developer_comms._as_datetime(record.get("expires_at"))
            if expires is None:
                continue
            state = admin_crud.key_status(record)
            if state == admin_crud.KEY_REVOKED:
                continue
            if expires <= now:
                if state != admin_crud.KEY_EXPIRED:
                    admin_crud.mark_key_expired(db, org, record["id"])
                fresh = admin_crud.get_api_key_record(org, record["id"]) or record
                if notify_expired(db, background, org=org, record=fresh):
                    expired += 1
            elif expires <= horizon:
                if notify_expiring(db, background, org=org, record=record):
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
