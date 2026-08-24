"""Dual-recording validation — BRD table 53: "Compare expected timeline, program duration,
audio, video, captions, gaps, and both paths. Select an approved replay source and record
the evidence." Real checks only: both paths actually captured, duration compared via
ffprobe within a tolerance, audio+video stream presence confirmed. Frame-level gap/
black-frame detection and caption QA are NOT implemented — genuinely large scope on their
own — and the evidence record says so explicitly rather than implying a check that never
ran (this codebase's "never fabricate" convention).

Also owns advancing the event's audience ReplayEntitlement (models/commercial.py) once a
real, usable replay source exists — the automated half of "never auto-publish on event
end" (BRD table 53): this only ever gets a replay to READY_FOR_REVIEW, never PUBLISHED.
Publishing itself stays a deliberate, separate operator action (routers/commercial.py's
existing publish endpoint, now surfaced in pages/admin/Media.jsx).

Same async-ticker shape as services/delivery.py's watermark pipeline — ffprobe needs the
file on local disk (services.livekit.download_to_temp), so this can't run inline on the
egress-stopped webhook without risking a slow request."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone

from sqlalchemy import select

from ..crud import commercial as commercial_crud
from ..db import SessionLocal
from ..models import Event, LiveRecording
from . import livekit

log = logging.getLogger(__name__)

# Dual paths are started/stopped by the same server-side triggers (services/broadcast.py's
# _recording_start/_stop_recording_rows) — real divergence beyond normal encoder/upload
# jitter means one path actually had a problem, not just timing noise.
DURATION_TOLERANCE_SECONDS = 15


def _ffprobe_info(local_path: str) -> dict | None:
    """duration_seconds/has_video/has_audio for one file, or None (never raises) if
    ffprobe is missing or the file can't be read at all — that itself is evidence
    (validate_recording_pair treats it as a failed path, not a silent pass)."""
    if shutil.which("ffprobe") is None:
        return None
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", local_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception:  # noqa: BLE001 — a probe failure must not crash the ticker
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    duration = None
    raw_duration = (data.get("format") or {}).get("duration")
    if raw_duration is not None:
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            duration = None
    streams = data.get("streams") or []
    return {
        "duration_seconds": duration,
        "has_video": any(s.get("codec_type") == "video" for s in streams),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def validate_recording_pair(db, primary: LiveRecording, secondary: LiveRecording) -> None:
    """Downloads and probes both paths, writes the SAME evidence + verdict to both rows —
    the evidence describes the comparison, not one side of it. Never raises; a download or
    probe failure is recorded as part of the verdict, not an exception."""
    evidence: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "gap_detection": "not implemented",
        "caption_qa": "not implemented",
    }
    probes: dict[str, dict] = {}
    verdict = "valid"

    for label, rec in (("primary", primary), ("secondary", secondary)):
        local_path = livekit.download_to_temp(rec.file_url)
        if local_path is None:
            evidence[label] = {"error": "Could not download the file for inspection"}
            verdict = "failed"
            continue
        probe = _ffprobe_info(local_path)
        try:
            os.remove(local_path)
        except OSError:
            pass
        if probe is None:
            evidence[label] = {"error": "Could not read the file (ffprobe failed)"}
            verdict = "failed"
            continue
        evidence[label] = probe
        probes[label] = probe

    if verdict != "failed":
        if len(probes) == 2:
            d1, d2 = probes["primary"].get("duration_seconds"), probes["secondary"].get("duration_seconds")
            if d1 is not None and d2 is not None:
                delta = round(abs(d1 - d2), 1)
                evidence["duration_delta_seconds"] = delta
                if delta > DURATION_TOLERANCE_SECONDS:
                    verdict = "degraded"
            else:
                evidence["duration_delta_seconds"] = None
                verdict = "degraded"
            for label in ("primary", "secondary"):
                if not probes[label].get("has_video") or not probes[label].get("has_audio"):
                    verdict = "degraded"
        else:
            verdict = "degraded"

    for rec in (primary, secondary):
        rec.validation_status = verdict
        rec.validation_evidence = evidence
    db.commit()


def on_recording_captured(db, recording: LiveRecording) -> None:
    """Call once a LiveRecording reaches status="stopped", enforced=True
    (services/broadcast.py::record_egress_result — the same place the file's existence is
    already about to be relevant). A single-path event has nothing to compare, so it goes
    straight to READY_FOR_REVIEW; a dual-path event's advancement instead happens from
    process_pending_validations once BOTH sides have real evidence."""
    if not recording.enforced or recording.status != "stopped":
        return
    if recording.role is not None:
        return  # dual path — run_validation_processor handles advancement, not this call
    if not livekit.object_exists(recording.file_url):
        return
    event = db.get(Event, recording.event_id)
    if event is None:
        return
    entitlement = commercial_crud.get_or_create_replay_entitlement(db, event, scope="audience")
    commercial_crud.advance_to_ready_for_review(db, entitlement)


def _pending_pairs(db) -> list[tuple[LiveRecording, LiveRecording]]:
    """event_ids with both a primary and secondary row captured but not yet validated."""
    rows = db.scalars(
        select(LiveRecording).where(
            LiveRecording.role.isnot(None), LiveRecording.status == "stopped",
            LiveRecording.enforced.is_(True), LiveRecording.validation_status.is_(None),
        )
    ).all()
    by_event: dict = {}
    for r in rows:
        by_event.setdefault(r.event_id, {})[r.role] = r
    return [(pair["primary"], pair["secondary"]) for pair in by_event.values() if "primary" in pair and "secondary" in pair]


def process_pending_validations(db) -> None:
    """Called from run_validation_processor via asyncio.to_thread — stays synchronous
    (subprocess + blocking GCS calls), same split services/delivery.py uses for watermark
    processing."""
    for primary, secondary in _pending_pairs(db):
        try:
            validate_recording_pair(db, primary, secondary)
            if primary.validation_status in ("valid", "degraded"):
                event = db.get(Event, primary.event_id)
                if event is not None:
                    entitlement = commercial_crud.get_or_create_replay_entitlement(db, event, scope="audience")
                    commercial_crud.advance_to_ready_for_review(db, entitlement)
        except Exception:  # noqa: BLE001 — one bad pair must not stop the batch
            log.exception("validation failed for event %s", primary.event_id)
            primary.validation_status = secondary.validation_status = "failed"
            db.commit()


async def run_validation_processor(interval: float = 20.0) -> None:
    """Background ticker started from the app lifespan — same shape as
    services/webhooks.py::run_webhook_retries and services/delivery.py::
    run_watermark_processor: one bad tick must not kill the loop."""
    def work():
        db = SessionLocal()
        try:
            process_pending_validations(db)
        finally:
            db.close()

    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(work)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the ticker
            log.exception("validation processor tick failed")
