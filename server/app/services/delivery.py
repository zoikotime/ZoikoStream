"""Controlled customer export — the BRD's "authorized, audited, expiring delivery of the
validated replay asset to the approved customer/family contact... do not expose a public
download link" (LE-AC-18). Shares its token/audit mechanism with services/report.py's
report release — see models/live.py::CustomerDelivery's docstring for why they're one
table.

Eligibility is deliberately the same real gate list_recordings/delete_recording already
use (status="stopped", enforced=True, not under legal hold) — NOT a hard gate on
validation_status, since nothing in this codebase currently sets that field (the
dual-recording comparison/validation pipeline itself is a separate, larger piece of work).
validation_status is surfaced to the admin as an honest label instead.

Watermarking (BRD "policy watermark", LE-AC-12) runs asynchronously, on the same
background-ticker pattern services/webhooks.py already established — a real recording can
run 1-3 hours, so burning it inline in the create call would hang the request. The
delivery EMAIL still goes out at creation time, not once watermarking finishes: the raw
token is only ever held in memory for that one response (crud/delivery.py never persists
it, matching EventAccessLink's reveal-once design), so a ticker running later has no way
to construct the link at all. The /deliveries/{token} landing page carries the "still
preparing" state instead — see routers/deliveries.py.

This same ticker also burns the watermark for a PUBLISHED replay (models.commercial.
ReplayEntitlement, watermarked once per event rather than per recipient — there's no
single "recipient" for a hosted replay page) — see process_pending_replay_watermarks below.
Kept in this module rather than a 7th ticker: both jobs are the exact same ffmpeg/GCS
primitive at the exact same cadence, just against a different row type."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select

from ..crud import admin as admin_crud
from ..crud import delivery as delivery_crud
from ..crud import event as event_crud
from ..db import SessionLocal
from ..email import send_customer_export_email
from ..models import CustomerDelivery, Event, LiveRecording, ReplayEntitlement
from . import livekit, watermark

log = logging.getLogger(__name__)


class ExportNotEligible(Exception):
    """Raised when a recording isn't in a state that can be exported. Message is meant to
    reach the org admin as-is (matches delete_recording's own 409 message convention)."""


def export_eligibility(rec: LiveRecording, held: bool) -> tuple[bool, str | None]:
    """Pure check, unit-testable without a DB — (eligible, reason_if_not)."""
    if rec.status != "stopped" or not rec.enforced:
        return False, "This recording has no captured file to export yet."
    if rec.legal_hold or held:
        return False, "This recording is under legal hold and cannot be exported."
    return True, None


def create_export(db, recording: LiveRecording, event, *, recipient_name, recipient_email,
                   expires_in_days, actor) -> tuple:
    """Returns (CustomerDelivery, raw_token). Caller (router) turns the raw token into the
    /deliveries/{token} URL — this layer never constructs URLs, matching services/webhooks.py
    and crud/event.py's own separation between "generate a secret" and "shape a link"."""
    held = admin_crud.event_under_legal_hold(db, recording.event_id)
    eligible, reason = export_eligibility(recording, held)
    if not eligible:
        raise ExportNotEligible(reason)

    delivery, raw = delivery_crud.create_delivery(
        db, recording.event_id, recording.org_id, "export",
        recording_id=recording.id, recipient_name=recipient_name, recipient_email=recipient_email,
        expires_in_days=expires_in_days, created_by=actor.id if actor else None,
    )
    admin_crud.create_audit_log(
        db, actor=actor, action="export.create", target_type="customer_delivery",
        target_id=delivery.id, org_id=recording.org_id,
        meta={"event_id": str(recording.event_id), "recording_id": str(recording.id),
              "recipient_email": recipient_email},
    )
    return delivery, raw


def deliver_export_email(db, delivery, event_title: str, export_url: str) -> None:
    """Sent at creation time, not once watermarking finishes — see module docstring for
    why. The link works immediately; the landing page itself shows "still preparing" until
    watermark_status flips to ready."""
    send_customer_export_email(
        delivery.recipient_email, delivery.recipient_name, event_title, export_url,
        delivery.expires_at,
    )
    delivery.delivered_at = datetime.now(timezone.utc)
    db.commit()


# ── Watermark processing (background ticker — see main.py's lifespan) ──────────────────

def _cleanup(*paths) -> None:
    for p in paths:
        try:
            if p:
                os.remove(p)
        except OSError:
            pass


def _process_one_watermark(db, delivery: CustomerDelivery) -> None:
    recording = db.get(LiveRecording, delivery.recording_id) if delivery.recording_id else None
    if recording is None:
        delivery.watermark_status, delivery.watermark_error = "failed", "The source recording no longer exists"
        db.commit()
        return

    local_in = livekit.download_to_temp(recording.file_url)
    if local_in is None:
        delivery.watermark_status, delivery.watermark_error = "failed", "Could not download the source recording"
        db.commit()
        return

    local_out = f"{local_in}.watermarked.mp4"
    text = (f"Prepared for {delivery.recipient_name} — confidential, do not redistribute — "
            f"{datetime.now(timezone.utc):%Y-%m-%d}")
    ok, error = watermark.burn(local_in, local_out, text)
    if not ok:
        delivery.watermark_status, delivery.watermark_error = "failed", error
        db.commit()
        _cleanup(local_in, local_out)
        return

    object_key = f"exports/{delivery.id}.mp4"
    uploaded = livekit.upload_object(local_out, object_key)
    _cleanup(local_in, local_out)
    if not uploaded:
        delivery.watermark_status, delivery.watermark_error = "failed", "Could not upload the watermarked file"
        db.commit()
        return

    delivery.watermarked_file_key = object_key
    delivery.watermark_status = "ready"
    delivery.watermark_error = None
    db.commit()


def process_pending_watermarks(db) -> None:
    """Called from run_watermark_processor via asyncio.to_thread — this function itself
    stays synchronous (subprocess + blocking GCS calls), same split services/webhooks.py
    uses between its async ticker and its sync DB/work functions."""
    pending = db.scalars(
        select(CustomerDelivery).where(
            CustomerDelivery.kind == "export", CustomerDelivery.watermark_status == "pending",
        )
    ).all()
    for delivery in pending:
        try:
            _process_one_watermark(db, delivery)
        except Exception:  # noqa: BLE001 — one bad delivery must not stop the batch
            log.exception("watermark processing failed for delivery %s", delivery.id)
            delivery.watermark_status, delivery.watermark_error = "failed", "Unexpected error"
            db.commit()


# ── Replay watermark processing (same ticker, different row type — see module docstring) ──

def _process_one_replay_watermark(db, entitlement: ReplayEntitlement) -> None:
    event = db.get(Event, entitlement.event_id)
    if event is None:
        entitlement.watermark_status, entitlement.watermark_error = "failed", "Event no longer exists"
        db.commit()
        return

    # Same selection routers/events.py::watch_event will use once this is ready — primary-
    # first, validation-aware (crud.event.list_replay_candidates), confirmed against real
    # storage rather than trusting a "stopped" status alone.
    recording = next(
        (r for r in event_crud.list_replay_candidates(db, event.id) if livekit.object_exists(r.file_url)),
        None,
    )
    if recording is None:
        entitlement.watermark_status, entitlement.watermark_error = "failed", "No valid recording available to watermark"
        db.commit()
        return

    local_in = livekit.download_to_temp(recording.file_url)
    if local_in is None:
        entitlement.watermark_status, entitlement.watermark_error = "failed", "Could not download the source recording"
        db.commit()
        return

    # Not personalized like the export's "Prepared for {recipient}" — a hosted replay page
    # has no single viewer to name, so this is a static policy mark (org + platform),
    # matching the BRD's own "policy watermark" (not "advanced forensic watermarking").
    org_name = event.organization.name if event.organization else "ZoikoStream"
    local_out = f"{local_in}.watermarked.mp4"
    text = f"{org_name} — ZoikoStream — do not redistribute"
    ok, error = watermark.burn(local_in, local_out, text)
    if not ok:
        entitlement.watermark_status, entitlement.watermark_error = "failed", error
        db.commit()
        _cleanup(local_in, local_out)
        return

    object_key = f"replays/{entitlement.id}.mp4"
    uploaded = livekit.upload_object(local_out, object_key)
    _cleanup(local_in, local_out)
    if not uploaded:
        entitlement.watermark_status, entitlement.watermark_error = "failed", "Could not upload the watermarked file"
        db.commit()
        return

    entitlement.watermarked_file_key = object_key
    entitlement.source_recording_id = recording.id
    entitlement.watermark_status = "ready"
    entitlement.watermark_error = None
    db.commit()


def process_pending_replay_watermarks(db) -> None:
    pending = db.scalars(
        select(ReplayEntitlement).where(ReplayEntitlement.watermark_status == "pending")
    ).all()
    for entitlement in pending:
        try:
            _process_one_replay_watermark(db, entitlement)
        except Exception:  # noqa: BLE001 — one bad entitlement must not stop the batch
            log.exception("replay watermark processing failed for entitlement %s", entitlement.id)
            entitlement.watermark_status, entitlement.watermark_error = "failed", "Unexpected error"
            db.commit()


async def run_watermark_processor(interval: float = 15.0) -> None:
    """Background ticker started from the app lifespan — same shape as
    moderation.run_scheduler/broadcast.run_sampler/ops.run_metric_sampler/
    webhooks.run_webhook_retries: one bad tick must not kill the loop."""
    def work():
        db = SessionLocal()
        try:
            process_pending_watermarks(db)
            process_pending_replay_watermarks(db)
        finally:
            db.close()

    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(work)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the ticker
            log.exception("watermark processor tick failed")
