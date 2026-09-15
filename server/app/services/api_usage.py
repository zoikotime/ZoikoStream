"""Durable rate-limit accounting and governed usage events (ZST-EC-001 DEV-010).

`ratelimit.py` counts in memory, per process. That is fine for turning credential stuffing
from "unbounded" into "slow", but it cannot drive a notification: the counters are one
worker's opinion and they vanish on restart. An email sent from that would describe
something the platform cannot evidence.

This module adds the durable half:

    refusal -> shared counter (Redis, atomic INCR+EXPIRE)
            -> sustained breach of a governed threshold
            -> committed RateLimitEvent
            -> one notification

The counter is Redis-backed because Redis is already configured for the event bus. When
REDIS_URL is blank the counter degrades to a process-local dictionary and `backend()`
reports "memory" — the degradation is visible rather than silent, and the DEV-010 status is
reported accordingly rather than claimed as distributed.

Detection is deterministic and explainable: a count of refusals inside a fixed window. There
is no scoring, no model, and one 429 can never produce an event.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..config import settings
from ..email import UnsafeLinkError
from ..models import (
    RATE_LIMIT_CLEARED,
    RATE_LIMIT_EVENT_THRESHOLD,
    RATE_LIMIT_EVENT_WINDOW_SECONDS,
    RATE_LIMIT_OPEN,
    Organization,
    RateLimitEvent,
    User,
)
from . import org_comms

log = logging.getLogger(__name__)

_KEY_PREFIX = "zoiko:ratelimit:"

# Process-local fallback, used only when REDIS_URL is unset. Guarded because the FastAPI
# dependency can run on more than one thread.
_local: dict[str, tuple[int, float]] = {}
_local_lock = threading.Lock()

_redis_client = None
# When the last connection attempt failed, as a monotonic timestamp. NOT a permanent latch:
# a boolean "it failed once" meant a single blip at first use — a cold start during a
# provider hiccup, say — dropped that Cloud Run instance to per-process counters for the
# whole life of the instance, silently un-sharing the shared counter long after Redis came
# back. A cooldown is also what keeps the retry from becoming a storm: one attempt per
# window per process, not one per refused request.
_redis_failed_at: float | None = None
_REDIS_RETRY_COOLDOWN_SECONDS = 60.0


def backend() -> str:
    """Which counter backend is actually in use: 'redis' or 'memory'."""
    return "redis" if _client() is not None else "memory"


def _client():
    """Lazy synchronous Redis client. None when unconfigured, or while unreachable.

    Synchronous on purpose: the rate limiter runs inside a FastAPI dependency, and the
    existing bus client is async. A failure disables the shared backend rather than raising
    into a request — a counter outage must never take the API down with it.

    ONE client per process, like services/bus.py's, and bounded for the same reason: this is
    the SECOND pool on every Cloud Run instance, so its ceiling counts against the provider's
    concurrent-connection cap alongside the bus's. It needs far fewer connections than the
    bus — one INCR per refused request, and refusals are rare by definition.
    """
    global _redis_client, _redis_failed_at
    if not settings.REDIS_URL:
        return None
    if _redis_client is not None:
        return _redis_client
    if (_redis_failed_at is not None
            and time.monotonic() - _redis_failed_at < _REDIS_RETRY_COOLDOWN_SECONDS):
        return None
    try:
        import redis

        _redis_client = redis.Redis.from_url(
            settings.REDIS_URL,
            max_connections=max(2, settings.REDIS_MAX_CONNECTIONS // 4),
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            socket_keepalive=True,
            health_check_interval=30,
            decode_responses=True)
        _redis_client.ping()
    except Exception as exc:  # noqa: BLE001 — this degrades the counter, never the request
        # Exception TYPE only, with no traceback and no URL. This can fire on a per-request
        # path during a provider outage, so the line has to stay cheap and repeatable — and
        # REDIS_URL carries the password inline, so it must never reach the log.
        log.warning("Rate-limit Redis unavailable (%s); using per-process counters, "
                    "retrying in %ss", type(exc).__name__, int(_REDIS_RETRY_COOLDOWN_SECONDS))
        _redis_client, _redis_failed_at = None, time.monotonic()
        return None
    _redis_failed_at = None
    return _redis_client


def record_refusal(principal: str, rule: str,
                   window_seconds: int = RATE_LIMIT_EVENT_WINDOW_SECONDS) -> int:
    """Count one refused request. Returns the running total inside the window.

    Atomic where it matters: INCR then EXPIRE only on first increment, so concurrent workers
    cannot each reset the window and hide a sustained breach.
    """
    key = f"{_KEY_PREFIX}{rule}:{principal}"
    client = _client()
    if client is not None:
        try:
            count = client.incr(key)
            if count == 1:
                client.expire(key, window_seconds)
            return int(count)
        except Exception:  # noqa: BLE001
            log.warning("Rate-limit counter write failed; using local count", exc_info=True)

    now = time.monotonic()
    with _local_lock:
        count, started = _local.get(key, (0, now))
        if now - started > window_seconds:
            count, started = 0, now
        count += 1
        _local[key] = (count, started)
        return count


def window_reset_at(principal: str, rule: str,
                    window_seconds: int = RATE_LIMIT_EVENT_WINDOW_SECONDS) -> datetime:
    key = f"{_KEY_PREFIX}{rule}:{principal}"
    client = _client()
    if client is not None:
        try:
            ttl = client.ttl(key)
            if ttl and ttl > 0:
                return datetime.now(timezone.utc) + timedelta(seconds=int(ttl))
        except Exception:  # noqa: BLE001
            pass
    return datetime.now(timezone.utc) + timedelta(seconds=window_seconds)


def clear(principal: str, rule: str) -> None:
    key = f"{_KEY_PREFIX}{rule}:{principal}"
    client = _client()
    if client is not None:
        try:
            client.delete(key)
        except Exception:  # noqa: BLE001
            pass
    with _local_lock:
        _local.pop(key, None)


# ── governed events ─────────────────────────────────────────────────────────────────────

def open_event(db: Session, *, principal: str, rule: str, observed: int, org_id=None,
               threshold: int = RATE_LIMIT_EVENT_THRESHOLD,
               window_seconds: int = RATE_LIMIT_EVENT_WINDOW_SECONDS
               ) -> RateLimitEvent | None:
    """Record a governed threshold crossing, once per open condition.

    An already-open event for the same principal and rule is reused rather than duplicated,
    so a continuing breach is one incident rather than one incident per request.
    """
    if observed < threshold:
        return None
    existing = db.scalar(
        select(RateLimitEvent).where(
            RateLimitEvent.principal == principal, RateLimitEvent.rule == rule,
            RateLimitEvent.status == RATE_LIMIT_OPEN)
    )
    if existing is not None:
        existing.observed_count = observed
        db.commit()
        return existing

    event = RateLimitEvent(
        org_id=org_id, principal=principal, rule=rule, observed_count=observed,
        threshold=threshold, window_seconds=window_seconds,
        triggered_at=datetime.now(timezone.utc),
        resets_at=window_reset_at(principal, rule, window_seconds),
        status=RATE_LIMIT_OPEN,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def clear_event(db: Session, event: RateLimitEvent) -> bool:
    """Close a governed condition. Only ever called from an observed clear window."""
    if event.status != RATE_LIMIT_OPEN:
        return False
    event.status = RATE_LIMIT_CLEARED
    event.cleared_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(event)
    return True


RULE_LABELS = {
    "login": "Sign-in endpoint",
    "otp_request": "Account recovery request endpoint",
    "otp_verify": "Account recovery verification endpoint",
    "verify_email": "Email verification endpoint",
    "resend_verification": "Verification resend endpoint",
}
THRESHOLD_LABEL = "Sustained rate-limited requests"
RECOMMENDED_ACTION = (
    "Reduce request volume, add backoff between retries, and check for a retry loop in your "
    "integration."
)


def notify(db: Session, background, event: RateLimitEvent) -> bool:
    """One notice per governed event. Recipients are the organization's administrators.

    Only sent when the event is attributable to a tenant. An unauthenticated flood from a
    stray IP is a platform concern, not something to email a customer about.
    """
    if event.org_id is None:
        return False
    updated = db.execute(
        update(RateLimitEvent)
        .where(RateLimitEvent.id == event.id, RateLimitEvent.notified_at.is_(None))
        .values(notified_at=datetime.now(timezone.utc))
    ).rowcount
    db.commit()
    if not updated:
        return False

    org = db.get(Organization, event.org_id)
    owner = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    addresses = org_comms.recipients(owner, *org_comms.org_admins(db, event.org_id))
    if not addresses:
        return False

    try:
        for address in addresses:
            background.add_task(
                email_mod.send_api_usage_email, address,
                org_name=org.name if org else "your Organization",
                rule_label=RULE_LABELS.get(event.rule, event.rule),
                observed_window=f"{event.window_seconds // 60} minutes",
                observed_count=event.observed_count,
                # Deliberately a category, not the number: exact anti-abuse thresholds are
                # not something a customer is told.
                threshold=THRESHOLD_LABEL,
                current_state="Rate limited",
                resets_at=org_comms.org_timestamp(org, event.resets_at),
                recommended_action=RECOMMENDED_ACTION)
    except UnsafeLinkError:
        log.exception("DEV-010 not queued: APP_URL unsafe for this environment")
        return False
    return True
