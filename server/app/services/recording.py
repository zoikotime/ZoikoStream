"""LiveKit Egress integration for event recording -> GCS.

Recordings are finalized two ways: routers/webhooks.py applies egress_ended events as
they arrive (needs a publicly reachable URL -- see that module for tunnel setup), and
routers/recordings.py's /refresh polls get_egress_status() on demand as a fallback for
whenever the webhook hasn't fired yet (e.g. no tunnel in local dev).
"""
import uuid
from pathlib import Path

from livekit import api as lk_api

from app.config import settings
from app.models.recording import Recording

STATUS_MAP = {
    lk_api.EgressStatus.EGRESS_STARTING: "recording",
    lk_api.EgressStatus.EGRESS_ACTIVE: "recording",
    lk_api.EgressStatus.EGRESS_ENDING: "processing",
    lk_api.EgressStatus.EGRESS_COMPLETE: "ready",
    lk_api.EgressStatus.EGRESS_FAILED: "failed",
    lk_api.EgressStatus.EGRESS_ABORTED: "failed",
    lk_api.EgressStatus.EGRESS_LIMIT_REACHED: "failed",
}


def status_from_egress(egress_info) -> str:
    return STATUS_MAP.get(egress_info.status, "processing")


def apply_egress_update(recording: Recording, egress_info) -> None:
    """Updates a Recording row in place from a LiveKit EgressInfo (polled or via webhook)."""
    recording.status = status_from_egress(egress_info)
    if recording.status == "ready" and egress_info.file_results:
        f = egress_info.file_results[0]
        recording.file_url = f.location or None
        recording.duration_seconds = int(f.duration / 1_000_000_000) if f.duration else None


def _client() -> lk_api.LiveKitAPI:
    return lk_api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)


def _gcs_credentials() -> str:
    if not settings.GCS_CREDENTIALS_FILE:
        return ""
    return Path(settings.GCS_CREDENTIALS_FILE).read_text()


async def start_recording(room_name: str, stream_id: str):
    if not settings.GCS_BUCKET or not settings.GCS_CREDENTIALS_FILE:
        raise RuntimeError(
            "Recording storage isn't configured -- set GCS_BUCKET and GCS_CREDENTIALS_FILE in .env"
        )

    file_output = lk_api.EncodedFileOutput(
        file_type=lk_api.EncodedFileType.MP4,
        filepath=f"recordings/{stream_id}/{uuid.uuid4().hex}.mp4",
        gcp=lk_api.GCPUpload(credentials=_gcs_credentials(), bucket=settings.GCS_BUCKET),
    )
    req = lk_api.RoomCompositeEgressRequest(room_name=room_name, layout="speaker", file_outputs=[file_output])

    async with _client() as lk:
        return await lk.egress.start_room_composite_egress(req)


async def stop_recording(egress_id: str):
    async with _client() as lk:
        return await lk.egress.stop_egress(lk_api.StopEgressRequest(egress_id=egress_id))


async def get_egress_status(egress_id: str):
    async with _client() as lk:
        resp = await lk.egress.list_egress(lk_api.ListEgressRequest(egress_id=egress_id))
        return resp.items[0] if resp.items else None
