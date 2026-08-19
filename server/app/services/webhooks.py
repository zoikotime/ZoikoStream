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
from ..models import WebhookDelivery, WebhookEndpoint

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5.0
MAX_ATTEMPTS = 6
# Exponential backoff — same doubling shape hooks/useEventStream.js and
# hooks/useLiveKitViewer.js already use client-side for reconnects, mirrored server-side.
BACKOFF_BASE_SECONDS = 30


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
            "url": endpoint.url, "secret": endpoint.secret,
        }
    finally:
        db.close()


async def _send(url: str, secret: str, event_type: str, delivery_id, payload: dict) -> tuple[int | None, str | None]:
    """Returns (status_code, error) — exactly one meaningful. Real async HTTP, deliberately
    not the sync httpx.post email.py uses — see module docstring."""
    body = json.dumps(payload, default=str).encode()
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Zoiko-Event": event_type,
        "X-Zoiko-Delivery": str(delivery_id),
        "X-Zoiko-Signature": f"t={ts},v1={sign(secret, ts, body)}",
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
        if error is None:
            d.status, d.delivered_at, d.next_attempt_at = "delivered", datetime.now(timezone.utc), None
        else:
            d.last_error = error
            if d.attempt_count >= MAX_ATTEMPTS:
                d.status, d.next_attempt_at = "failed", None
            else:
                delay = BACKOFF_BASE_SECONDS * (2 ** (d.attempt_count - 1))
                d.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        db.commit()
    finally:
        db.close()


async def _attempt(delivery_id, endpoint_id) -> None:
    loaded = await asyncio.to_thread(_load_delivery, delivery_id, endpoint_id)
    if loaded is None:
        return  # already delivered/failed by a previous tick, or the endpoint was deleted
    status_code, error = await _send(
        loaded["url"], loaded["secret"], loaded["event_type"], loaded["id"], loaded["payload"]
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
