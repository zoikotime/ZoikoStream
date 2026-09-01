"""Webhook endpoint verification and delivery health (ZST-EC-001 DEV-006 / DEV-007).

DEV-006 closes a real integrity hole. Before this, `enqueue` gated only on `enabled`, so any
URL an administrator saved began receiving signed production payloads at once. Verification
is a purpose-bound challenge the endpoint has to echo back from the URL itself, and
`webhook_security.is_deliverable` is the single boundary every sender consults.

DEV-007 turns the delivery log that already existed into health the customer hears about.
The rules are deterministic and threshold-based, never "one email per retry":

    consecutive terminal failures >= WEBHOOK_FAILURE_THRESHOLD  -> DEGRADED  (notify once)
    consecutive terminal failures >= WEBHOOK_DISABLE_THRESHOLD  -> DISABLED  (notify once)
    a real successful delivery while DEGRADED/DISABLED-by-failure -> RECOVERED (notify once)

Each transition clears the opposite marker, so an endpoint that fails, recovers and fails
again is announced each time — one notice per transition, not one per lifetime.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    WEBHOOK_DEGRADED,
    WEBHOOK_DISABLE_THRESHOLD,
    WEBHOOK_DISABLED,
    WEBHOOK_FAILURE_THRESHOLD,
    WEBHOOK_HEALTHY,
    WEBHOOK_PENDING_VERIFICATION,
    WEBHOOK_VERIFIED,
    Organization,
    User,
    WebhookDelivery,
    WebhookEndpoint,
)
from . import org_comms, webhook_security

log = logging.getLogger(__name__)

# Outcomes from verify(), so routers map policy to status codes without re-deriving it.
OK = "ok"
NOT_PENDING = "not_pending"
NO_CHALLENGE = "no_challenge"
EXPIRED = "expired"
MISMATCH = "mismatch"
TOO_MANY_ATTEMPTS = "too_many_attempts"

DISABLE_REASON_FAILURES = "repeated_delivery_failures"
REASON_LABELS = {
    DISABLE_REASON_FAILURES: "Repeated delivery failures",
    "verification_reset": "Verification reset",
    "url_changed": "Endpoint URL changed",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _queue(background, send, addresses, **kwargs) -> None:
    try:
        for address in addresses:
            background.add_task(send, address, **kwargs)
    except UnsafeLinkError:
        log.exception("Webhook notice not queued: APP_URL unsafe for this environment")


def recipients(db: Session, endpoint: WebhookEndpoint) -> list[str]:
    """Endpoint owner plus the Organization's administrators, deduplicated."""
    owner = db.get(User, endpoint.created_by) if endpoint.created_by else None
    org = db.get(Organization, endpoint.org_id)
    admins = org_comms.org_admins(db, endpoint.org_id)
    extra = []
    if org is not None and org.owner_user_id:
        holder = db.get(User, org.owner_user_id)
        if holder is not None:
            extra.append(holder.email)
    return org_comms.recipients(owner, *admins, extra=extra)


def _claim(db: Session, endpoint: WebhookEndpoint, column: str) -> bool:
    """One notification per transition. Set-and-commit, guarded by the current value."""
    if getattr(endpoint, column) is not None:
        return False
    setattr(endpoint, column, _now())
    db.commit()
    return True


# ── DEV-006 verification ────────────────────────────────────────────────────────────────

def issue_challenge(db: Session, endpoint: WebhookEndpoint) -> str:
    """Mint a fresh challenge and put the endpoint into PENDING_VERIFICATION.

    Returns the raw challenge, which is shown once in the console so the operator can
    configure their endpoint to echo it. Only the hash is stored.
    """
    raw, hashed, expires = webhook_security.new_challenge()
    endpoint.status = WEBHOOK_PENDING_VERIFICATION
    endpoint.verification_token_hash = hashed
    endpoint.verification_expires_at = expires
    endpoint.verification_attempts = 0
    endpoint.verified_at = None
    # A re-issued challenge is a new obligation, so the notice may fire again.
    endpoint.verification_notified_at = None
    db.commit()
    db.refresh(endpoint)
    return raw


def verify(db: Session, endpoint: WebhookEndpoint, presented: str) -> str:
    """Redeem a challenge. Single-use, expiring, attempt-limited, constant-time compared."""
    import hmac

    if endpoint.status == WEBHOOK_VERIFIED:
        return NOT_PENDING
    if not endpoint.verification_token_hash:
        return NO_CHALLENGE
    if endpoint.verification_attempts >= webhook_security.MAX_VERIFICATION_ATTEMPTS:
        return TOO_MANY_ATTEMPTS

    endpoint.verification_attempts += 1
    endpoint.verification_attempted_at = _now()
    if endpoint.verification_expires_at and endpoint.verification_expires_at <= _now():
        db.commit()
        return EXPIRED
    if not hmac.compare_digest(webhook_security.hash_challenge(presented or ""),
                               endpoint.verification_token_hash):
        db.commit()
        return MISMATCH

    endpoint.status = WEBHOOK_VERIFIED
    endpoint.verified_at = _now()
    # Single use: the challenge cannot be redeemed again.
    endpoint.verification_token_hash = None
    endpoint.verification_expires_at = None
    endpoint.health = WEBHOOK_HEALTHY
    endpoint.consecutive_failures = 0
    db.commit()
    db.refresh(endpoint)
    return OK


def reset_verification(db: Session, endpoint: WebhookEndpoint, *,
                       reason: str = "verification_reset") -> str:
    """Return a verified endpoint to PENDING_VERIFICATION and withhold production events."""
    endpoint.verification_reset_at = _now()
    endpoint.reset_notified_at = None
    endpoint.disabled_at = None
    endpoint.disabled_reason = None
    raw = issue_challenge(db, endpoint)
    endpoint.verification_reset_at = _now()
    db.commit()
    db.refresh(endpoint)
    return raw


def notify_verification_required(db: Session, background, endpoint: WebhookEndpoint) -> bool:
    if not _claim(db, endpoint, "verification_notified_at"):
        return False
    org = db.get(Organization, endpoint.org_id)
    addresses = recipients(db, endpoint)
    if not addresses:
        return False
    _queue(background, email_mod.send_webhook_verification_email, addresses,
           org_name=org.name if org else "your Organization",
           endpoint_url=endpoint.url,
           expires_at=org_comms.org_timestamp(org, endpoint.verification_expires_at),
           challenge_header=webhook_security.CHALLENGE_HEADER)
    return True


def notify_verification_reset(db: Session, background, endpoint: WebhookEndpoint,
                              reason: str = "verification_reset") -> bool:
    if not _claim(db, endpoint, "reset_notified_at"):
        return False
    org = db.get(Organization, endpoint.org_id)
    addresses = recipients(db, endpoint)
    if not addresses:
        return False
    _queue(background, email_mod.send_webhook_verification_reset_email, addresses,
           org_name=org.name if org else "your Organization",
           endpoint_url=endpoint.url,
           reset_at=org_comms.org_timestamp(org, endpoint.verification_reset_at or _now()),
           reason=REASON_LABELS.get(reason, "Verification reset"))
    return True


# ── DEV-007 health ──────────────────────────────────────────────────────────────────────

def dead_lettered_count(db: Session, endpoint: WebhookEndpoint) -> int:
    return db.scalar(
        select(func.count(WebhookDelivery.id)).where(
            WebhookDelivery.endpoint_id == endpoint.id,
            WebhookDelivery.status == "dead_lettered")
    ) or 0


def record_success(db: Session, endpoint: WebhookEndpoint) -> str | None:
    """Fold one successful delivery into endpoint health. Returns 'recovered' on transition."""
    was_unhealthy = (endpoint.health == WEBHOOK_DEGRADED
                     or (endpoint.status == WEBHOOK_DISABLED
                         and endpoint.disabled_reason == DISABLE_REASON_FAILURES))
    endpoint.consecutive_failures = 0
    endpoint.last_delivery_at = _now()
    endpoint.last_success_at = _now()
    transition = None
    if was_unhealthy:
        endpoint.health = WEBHOOK_HEALTHY
        # Re-arm the degraded/disabled markers so a future outage is announced again.
        endpoint.degraded_notified_at = None
        endpoint.disabled_notified_at = None
        endpoint.recovered_notified_at = None
        transition = "recovered"
    db.commit()
    db.refresh(endpoint)
    return transition


def record_failure(db: Session, endpoint: WebhookEndpoint) -> str | None:
    """Fold one TERMINAL delivery failure into endpoint health.

    Called only when a delivery has exhausted its retries — a single failed attempt that
    will be retried is not a health signal, which is what keeps one transient 500 from
    alarming anybody.
    """
    endpoint.consecutive_failures += 1
    endpoint.last_delivery_at = _now()
    transition = None

    if (endpoint.consecutive_failures >= WEBHOOK_DISABLE_THRESHOLD
            and endpoint.status != WEBHOOK_DISABLED):
        endpoint.status = WEBHOOK_DISABLED
        endpoint.disabled_at = _now()
        endpoint.disabled_reason = DISABLE_REASON_FAILURES
        endpoint.health = WEBHOOK_DEGRADED
        endpoint.recovered_notified_at = None
        transition = "disabled"
    elif (endpoint.consecutive_failures >= WEBHOOK_FAILURE_THRESHOLD
            and endpoint.health != WEBHOOK_DEGRADED):
        endpoint.health = WEBHOOK_DEGRADED
        endpoint.degraded_since = _now()
        endpoint.recovered_notified_at = None
        transition = "degraded"
    db.commit()
    db.refresh(endpoint)
    return transition


def notify_health(db: Session, background, endpoint: WebhookEndpoint,
                  transition: str) -> bool:
    """One notice per health transition."""
    org = db.get(Organization, endpoint.org_id)
    addresses = recipients(db, endpoint)
    if not addresses:
        return False
    org_name = org.name if org else "your Organization"
    last_success = (org_comms.org_timestamp(org, endpoint.last_success_at)
                    if endpoint.last_success_at else "No successful delivery recorded")

    if transition == "degraded":
        if not _claim(db, endpoint, "degraded_notified_at"):
            return False
        _queue(background, email_mod.send_webhook_degraded_email, addresses,
               org_name=org_name, endpoint_url=endpoint.url,
               consecutive_failures=endpoint.consecutive_failures,
               failure_window=(org_comms.org_timestamp(org, endpoint.degraded_since)
                               if endpoint.degraded_since else "Recent"),
               last_success=last_success,
               next_retry="Retries continue automatically",
               dead_lettered=dead_lettered_count(db, endpoint))
        return True

    if transition == "disabled":
        if not _claim(db, endpoint, "disabled_notified_at"):
            return False
        _queue(background, email_mod.send_webhook_disabled_email, addresses,
               org_name=org_name, endpoint_url=endpoint.url,
               disabled_at=org_comms.org_timestamp(org, endpoint.disabled_at),
               reason_label=REASON_LABELS[DISABLE_REASON_FAILURES],
               future_events=("New events are not delivered to this endpoint and are not "
                              "queued for it"),
               reenable_route=("Re-verify the endpoint from the Developer console to resume "
                               "delivery."))
        return True

    if transition == "recovered":
        if not _claim(db, endpoint, "recovered_notified_at"):
            return False
        _queue(background, email_mod.send_webhook_recovered_email, addresses,
               org_name=org_name, endpoint_url=endpoint.url,
               recovered_at=org_comms.org_timestamp(org, endpoint.last_success_at or _now()),
               prior_failure_window=(org_comms.org_timestamp(org, endpoint.degraded_since)
                                     if endpoint.degraded_since else "Recent"),
               dead_lettered=dead_lettered_count(db, endpoint))
        return True
    return False


def replay(db: Session, delivery: WebhookDelivery, actor_id=None) -> bool:
    """Re-queue one dead-lettered delivery.

    Idempotent on the thing that matters: a delivery that already succeeded is never
    re-queued, and the ORIGINAL row is reused, so the subscriber sees the same
    `X-Zoiko-Delivery` id rather than a duplicate event.
    """
    if delivery.status == "delivered":
        return False
    if delivery.status != "dead_lettered":
        return False
    delivery.status = "pending"
    delivery.next_attempt_at = _now()
    delivery.attempt_count = 0
    delivery.replayed_at = _now()
    delivery.replayed_by = actor_id
    delivery.replay_count = (delivery.replay_count or 0) + 1
    db.commit()
    db.refresh(delivery)
    return True
