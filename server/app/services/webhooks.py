"""Outbound webhook delivery — signs, sends, and retries deliveries to org-registered
endpoints (models/webhook.py). Fills the gap client/src/pages/organization/Webhooks.jsx
used to have to disclose outright: "delivery outcomes are not recorded anywhere in this
stack."

Threading note, same discipline as services/moderation.py's own docstring: this module's
retry ticker (run_webhook_retries) runs directly on the shared asyncio event loop, NOT
inside a FastAPI BackgroundTasks threadpool slot the way services/email.py's sync
httpx.post calls do — so every DB access here goes through asyncio.to_thread (mirroring
moderation.tx()), and the actual outbound call uses httpx.AsyncClient rather than the sync
client email.py uses, or a slow/down subscriber endpoint would stall every other live
connection this process is serving for up to TIMEOUT_SECONDS.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from ..db import SessionLocal
from . import webhook_lifecycle
from . import webhook_security
from ..models import WebhookDelivery, WebhookEndpoint

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5.0
MAX_ATTEMPTS = 6
# Exponential backoff — same doubling shape hooks/useEventStream.js and
# hooks/useLiveKitViewer.js already use client-side for reconnects, mirrored server-side.
BACKOFF_BASE_SECONDS = 30


class _Bg:
    """Inline sender for the delivery ticker, which is already off the request path."""

    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — a health notice must not break delivery
            log.exception("Webhook health notice failed")


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """HMAC-SHA256 over "<timestamp>.<raw body>" — the exact contract Webhooks.jsx already
    documents to org admins (its VERIFY_SAMPLE code block). Mirrors
    services.payments.verify_webhook_signature's algorithm choice (the only other signed-
    webhook code in this app), just on the signing side instead of the verifying side."""
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, "sha256").hexdigest()


def enqueue(db, org_id, event_type: str, data: dict) -> None:
    """Insert one pending WebhookDelivery per enabled endpoint subscribed to event_type.
    Called inline at each trigger site (services/broadcast.py, routers/events.py) — same
    placement as email.py's background.add_task(send_X_email, ...) calls, just a plain
    call since this only ever does a DB write, never network I/O itself. The actual HTTP
    delivery is entirely the retry ticker's job, so a trigger site's own transaction never
    blocks on a subscriber's endpoint being slow or down."""
    endpoints = db.scalars(
        select(WebhookEndpoint).where(WebhookEndpoint.org_id == org_id, WebhookEndpoint.enabled.is_(True))
    ).all()
    now = datetime.now(timezone.utc)
    for ep in endpoints:
        if event_type not in (ep.events or []):
            continue
        # ZST-EC-001 DEV-006. THE production-delivery gate. Until this existed, `enabled`
        # was the only check, so any URL an administrator saved began receiving signed
        # customer payloads immediately — including a typo, or an internal address.
        #
        # Withheld events are not queued at all rather than queued-and-skipped: a queued
        # delivery to an unverified endpoint would sit in the log looking like a pending
        # obligation the platform intends to honour.
        deliverable, reason = webhook_security.is_deliverable(ep)
        if not deliverable:
            log.info("Webhook %s withheld for endpoint %s: %s", event_type, ep.id, reason)
            continue
        db.add(WebhookDelivery(
            endpoint_id=ep.id, event_type=event_type,
            payload={"event": event_type, "sent_at": now.isoformat(), "data": data},
            status="pending", next_attempt_at=now,
        ))
    db.commit()


def _load_delivery(delivery_id, endpoint_id) -> dict | None:
    db = SessionLocal()
    try:
        delivery = db.get(WebhookDelivery, delivery_id)
        endpoint = db.get(WebhookEndpoint, endpoint_id)
        if delivery is None or endpoint is None or delivery.status != "pending":
            return None
        return {
            "id": delivery.id, "event_type": delivery.event_type, "payload": delivery.payload,
            # Every active secret version, so a delivery during a rotation window is
            # signed with both and either verifier accepts it.
            "url": endpoint.url, "secrets": active_secrets(endpoint),
        }
    finally:
        db.close()


def signature_header(versions: list[tuple[int, str]], timestamp: int, body: bytes) -> str:
    """Build the X-Zoiko-Signature value across every currently-active secret version.

    Format stays backward compatible: `t=<ts>,v1=<hmac>` for a single secret, exactly what
    subscribers already parse. During a DEV-008 rotation a second entry is appended as
    `v1=<hmac-of-previous>`, so a consumer that scans for any matching v1 value verifies
    with EITHER secret while it migrates. No new algorithm, no format a current consumer
    cannot read — the migration mechanism has to work with the verifier people already
    wrote.
    """
    parts = [f"t={timestamp}"]
    for _version, secret in versions:
        parts.append(f"v1={sign(secret, timestamp, body)}")
    return ",".join(parts)


def active_secrets(endpoint) -> list[tuple[int, str]]:
    """Current secret first, then the retiring one while its overlap window is open."""
    now = datetime.now(timezone.utc)
    out = [(endpoint.secret_version or 1, endpoint.secret)]
    if (endpoint.previous_secret
            and endpoint.rotation_ends_at
            and endpoint.rotation_ends_at > now):
        out.append((endpoint.previous_secret_version or 0, endpoint.previous_secret))
    return out


async def _send(url: str, secrets: list, event_type: str, delivery_id, payload: dict) -> tuple[int | None, str | None]:
    """Returns (status_code, error) — exactly one meaningful. Real async HTTP, deliberately
    not the sync httpx.post email.py uses — see module docstring."""
    # Re-validated here, not only at registration. A hostname that resolved to a public
    # address when the endpoint was saved can resolve to a private one later, which is
    # exactly what DNS rebinding does.
    try:
        webhook_security.validate_webhook_url(url)
    except webhook_security.UnsafeWebhookUrl as exc:
        return None, f"blocked: {exc}"

    body = json.dumps(payload, default=str).encode()
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Zoiko-Event": event_type,
        "X-Zoiko-Delivery": str(delivery_id),
        "X-Zoiko-Signature": signature_header(secrets, ts, body),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(url, content=body, headers=headers)
        ok = 200 <= resp.status_code < 300
        return resp.status_code, None if ok else f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:  # noqa: BLE001 — a subscriber's outage must not raise here
        return None, str(exc)[:400]


def _finalize(delivery_id, status_code: int | None, error: str | None) -> None:
    db = SessionLocal()
    try:
        d = db.get(WebhookDelivery, delivery_id)
        if d is None:
            return
        d.attempt_count += 1
        d.last_response_code = status_code
        terminal = None
        if error is None:
            d.status, d.delivered_at, d.next_attempt_at = "delivered", datetime.now(timezone.utc), None
            terminal = "success"
        else:
            d.last_error = error
            if d.attempt_count >= MAX_ATTEMPTS:
                # ZST-EC-001 DEV-007: retained for controlled replay, not dropped.
                d.status, d.next_attempt_at = "dead_lettered", None
                d.dead_lettered_at = datetime.now(timezone.utc)
                terminal = "failure"
            else:
                delay = BACKOFF_BASE_SECONDS * (2 ** (d.attempt_count - 1))
                d.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        db.commit()

        # Health folds in TERMINAL outcomes only. A failed attempt that will be retried is
        # not a health signal — that is what keeps one transient 500 from alarming anyone.
        if terminal is not None:
            endpoint = db.get(WebhookEndpoint, d.endpoint_id)
            if endpoint is not None:
                if terminal == "success":
                    transition = webhook_lifecycle.record_success(db, endpoint)
                else:
                    transition = webhook_lifecycle.record_failure(db, endpoint)
                if transition:
                    webhook_lifecycle.notify_health(db, _Bg(), endpoint, transition)
    finally:
        db.close()


async def _attempt(delivery_id, endpoint_id) -> None:
    loaded = await asyncio.to_thread(_load_delivery, delivery_id, endpoint_id)
    if loaded is None:
        return  # already delivered/failed by a previous tick, or the endpoint was deleted
    status_code, error = await _send(
        loaded["url"], loaded["secrets"], loaded["event_type"], loaded["id"], loaded["payload"]
    )
    await asyncio.to_thread(_finalize, delivery_id, status_code, error)


def _due_delivery_ids() -> list[tuple]:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        rows = db.execute(
            select(WebhookDelivery.id, WebhookDelivery.endpoint_id)
            .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= now)
            .limit(50)
        ).all()
        return [(r.id, r.endpoint_id) for r in rows]
    finally:
        db.close()


async def run_webhook_retries(interval: float = 10.0) -> None:
    """Background ticker started from the app lifespan — same shape as
    moderation.run_scheduler/broadcast.run_sampler/ops.run_metric_sampler: one bad tick
    must not kill the loop. ponytail: same single-ticker/single-worker assumption those
    three already carry (see main.py's lifespan docstring) — with multiple uvicorn
    workers, pin this to one worker or a delivery could be attempted N times."""
    while True:
        await asyncio.sleep(interval)
        try:
            for delivery_id, endpoint_id in await asyncio.to_thread(_due_delivery_ids):
                await _attempt(delivery_id, endpoint_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the ticker
            log.exception("webhook retry tick failed")
