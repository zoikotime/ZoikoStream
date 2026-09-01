"""Webhook signing-secret rotation (ZST-EC-001 DEV-008).

The overlap window is real, not announced. `webhooks.active_secrets` returns both secrets
while `rotation_ends_at` is in the future, and `webhooks.signature_header` emits an HMAC for
each — as two `v1=` entries in the existing header, so a subscriber's current verifier keeps
working while they migrate. When the window closes the previous secret is cleared and stops
signing anything.

On storage: a webhook signing secret cannot be hash-only. The server has to produce the
HMAC, so it needs recoverable material — the model has always stored it in the clear and
says so. Rotation does not change that posture; what it changes is how long any one secret
stays in play, and it gives the customer a bounded, announced window to move.

Neither secret ever appears in an email. The notices carry fingerprints, which are derived
from the secret by one-way hash and disclose nothing about it.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    ROTATION_ENDING_WARNING_HOURS,
    ROTATION_OVERLAP_HOURS,
    Organization,
    WebhookEndpoint,
)
from . import org_comms, webhook_lifecycle

log = logging.getLogger(__name__)

TICKER_INTERVAL_SECONDS = 900.0

REQUIRED_ACTION = (
    "Update your endpoint to verify with the new signing secret before the overlap ends. "
    "Both signatures are sent until then."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(secret: str | None) -> str:
    """Non-secret identifier for a signing secret.

    Derived by one-way hash, never a slice of the secret itself: publishing part of a
    signing key would hand an attacker a head start on forging signatures.
    """
    if not secret:
        return "None"
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8].upper()
    return f"{digest[:4]}-{digest[4:]}"


def rotation_active(endpoint: WebhookEndpoint) -> bool:
    return bool(endpoint.previous_secret and endpoint.rotation_ends_at
                and endpoint.rotation_ends_at > _now())


def start_rotation(db: Session, endpoint: WebhookEndpoint, actor_id=None,
                   overlap_hours: int = ROTATION_OVERLAP_HOURS) -> tuple[str, str]:
    """Issue a replacement signing secret and open the overlap window.

    Returns (raw_new_secret, new_fingerprint). The raw secret is returned once, to the
    authorized caller, and is otherwise only re-viewable through the existing reveal route.
    """
    if rotation_active(endpoint):
        # A second rotation inside an open window would strand whichever secret it displaced
        # before the customer had finished migrating onto it.
        return endpoint.secret, fingerprint(endpoint.secret)

    now = _now()
    endpoint.previous_secret = endpoint.secret
    endpoint.previous_secret_version = endpoint.secret_version or 1
    endpoint.secret = secrets.token_hex(32)
    endpoint.secret_version = (endpoint.secret_version or 1) + 1
    endpoint.rotation_started_at = now
    endpoint.rotation_ends_at = now + timedelta(hours=overlap_hours)
    endpoint.rotation_completed_at = None
    endpoint.rotated_by = actor_id
    # A new rotation is a new obligation, so both notices may fire again.
    endpoint.rotation_started_notified_at = None
    endpoint.rotation_ending_notified_at = None
    db.commit()
    db.refresh(endpoint)
    return endpoint.secret, fingerprint(endpoint.secret)


def complete_rotation(db: Session, endpoint: WebhookEndpoint) -> bool:
    """Retire the previous secret. After this it signs nothing."""
    if not endpoint.previous_secret:
        return False
    endpoint.previous_secret = None
    endpoint.previous_secret_version = None
    endpoint.rotation_completed_at = _now()
    endpoint.rotation_ends_at = None
    db.commit()
    db.refresh(endpoint)
    return True


def _queue(background, send, addresses, **kwargs) -> None:
    try:
        for address in addresses:
            background.add_task(send, address, **kwargs)
    except UnsafeLinkError:
        log.exception("DEV-008 notice not queued: APP_URL unsafe for this environment")


def _claim(db: Session, endpoint: WebhookEndpoint, column: str) -> bool:
    if getattr(endpoint, column) is not None:
        return False
    setattr(endpoint, column, _now())
    db.commit()
    return True


def notify_started(db: Session, background, endpoint: WebhookEndpoint) -> bool:
    if not _claim(db, endpoint, "rotation_started_notified_at"):
        return False
    org = db.get(Organization, endpoint.org_id)
    addresses = webhook_lifecycle.recipients(db, endpoint)
    if not addresses:
        return False
    _queue(background, email_mod.send_signing_rotation_started_email, addresses,
           org_name=org.name if org else "your Organization",
           endpoint_url=endpoint.url,
           old_fingerprint=fingerprint(endpoint.previous_secret),
           new_fingerprint=fingerprint(endpoint.secret),
           started_at=org_comms.org_timestamp(org, endpoint.rotation_started_at),
           overlap_ends_at=org_comms.org_timestamp(org, endpoint.rotation_ends_at),
           required_action=REQUIRED_ACTION)
    return True


def notify_ending(db: Session, background, endpoint: WebhookEndpoint) -> bool:
    if not _claim(db, endpoint, "rotation_ending_notified_at"):
        return False
    org = db.get(Organization, endpoint.org_id)
    addresses = webhook_lifecycle.recipients(db, endpoint)
    if not addresses:
        return False
    hours_left = max(0, int((endpoint.rotation_ends_at - _now()).total_seconds() // 3600)) \
        if endpoint.rotation_ends_at else 0
    _queue(background, email_mod.send_signing_rotation_ending_email, addresses,
           org_name=org.name if org else "your Organization",
           endpoint_url=endpoint.url,
           new_fingerprint=fingerprint(endpoint.secret),
           overlap_ends_at=org_comms.org_timestamp(org, endpoint.rotation_ends_at),
           hours_left=hours_left)
    return True


class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("DEV-008 notice failed")


def sweep(db: Session, background=None) -> dict:
    """One pass: warn windows about to close, then close the ones that have."""
    background = background or _Bg()
    now = _now()
    warned = closed = 0

    warn_horizon = now + timedelta(hours=ROTATION_ENDING_WARNING_HOURS)
    for endpoint in db.scalars(
        select(WebhookEndpoint).where(
            WebhookEndpoint.previous_secret.isnot(None),
            WebhookEndpoint.rotation_ends_at.isnot(None))
    ).all():
        if endpoint.rotation_ends_at <= now:
            if complete_rotation(db, endpoint):
                closed += 1
        elif endpoint.rotation_ends_at <= warn_horizon:
            if notify_ending(db, background, endpoint):
                warned += 1
    return {"warned": warned, "closed": closed}


async def run_signing_rotation_sweeper(interval: float = TICKER_INTERVAL_SECONDS) -> None:
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
            log.exception("Signing-rotation sweep failed")
