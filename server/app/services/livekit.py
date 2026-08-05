"""LiveKit access tokens + server-side room control.

The room-control helpers are what make a moderator action REAL: without them, muting a
participant only repaints the console. Every one of them no-ops (returns False) when
LiveKit is unconfigured, so the moderation console still works end-to-end in dev — the
state change and the broadcast happen either way, the media enforcement is what's missing.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from google.cloud import storage as gcs_storage
from google.oauth2 import service_account
from livekit import api

from app.config import settings

log = logging.getLogger(__name__)


def create_stream_token(
    identity: str,
    room_name: str,
    can_publish: bool
):

    grant = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=can_publish,
        can_subscribe=True
    )


    token = api.AccessToken(
        settings.LIVEKIT_API_KEY,
        settings.LIVEKIT_API_SECRET
    )


    token.with_identity(identity)
    token.with_grants(grant)


    return token.to_jwt()


# ── server-side room control ──────────────────────────────────────────────────

def configured() -> bool:
    return bool(settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET)


async def _with_room(fn):
    """Run `fn(room_service)` against a short-lived client. Returns False when LiveKit
    isn't configured or the call fails — callers treat that as "not enforced", never as
    a reason to skip the state change."""
    if not configured():
        return False
    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        await fn(lk.room)
        return True
    except Exception as exc:  # noqa: BLE001 - LiveKit errors must not fail a moderator action
        log.warning("livekit room control failed: %s", exc)
        return False
    finally:
        await lk.aclose()


async def mute_participant(room: str, identity: str, muted: bool) -> bool:
    """Mute/unmute every published track of a participant (LiveKit mutes per track)."""

    async def call(svc):
        info = await svc.get_participant(api.RoomParticipantIdentity(room=room, identity=identity))
        for track in info.tracks:
            await svc.mute_published_track(
                api.MuteRoomTrackRequest(room=room, identity=identity, track_sid=track.sid, muted=muted)
            )

    return await _with_room(call)


async def remove_participant(room: str, identity: str) -> bool:
    return await _with_room(
        lambda svc: svc.remove_participant(api.RoomParticipantIdentity(room=room, identity=identity))
    )


async def set_stage(room: str, identity: str, on_stage: bool) -> bool:
    """Invite to / remove from stage = the publish permission, which is what "on stage"
    actually means in LiveKit."""
    return await _with_room(
        lambda svc: svc.update_participant(
            api.UpdateParticipantRequest(
                room=room,
                identity=identity,
                permission=api.ParticipantPermission(can_subscribe=True, can_publish=on_stage, can_publish_data=True),
            )
        )
    )


async def ensure_room(room: str, empty_timeout: int = 600) -> bool:
    """Create the room ahead of the first publisher so a Go Live click has somewhere to
    land. Already-exists is success, not an error."""
    return await _with_room(
        lambda svc: svc.create_room(api.CreateRoomRequest(name=room, empty_timeout=empty_timeout))
    )


async def close_room(room: str) -> bool:
    return await _with_room(lambda svc: svc.delete_room(api.DeleteRoomRequest(room=room)))


# ── recording (egress) ────────────────────────────────────────────────────────
# Recording is composite room egress. LiveKit needs somewhere to PUT the file: with no
# s3/gcp/azure block it writes inside its own container, which on LiveKit Cloud means the
# request is rejected. We surface that as (None, error) so the console can say "recording
# not enforced" instead of implying a file exists. When GCS is configured (below), every
# egress gets a real destination and this stops happening.

# Presets only reach 1080p; 2K/4K need explicit encoding options.
_PRESETS = {
    "720p": api.EncodingOptionsPreset.H264_720P_30,
    "1080p": api.EncodingOptionsPreset.H264_1080P_30,
}
_ADVANCED = {
    "2k": (2560, 1440),
    "4k": (3840, 2160),
}


@lru_cache(maxsize=1)
def _gcs_credentials_json() -> str | None:
    """Raw contents of the service account key file, read once per process. LiveKit's
    GCPUpload wants the JSON as a string, not a path — the egress worker talks to GCS
    directly, this process never touches the uploaded bytes."""
    if not settings.GCS_CREDENTIALS_PATH:
        return None
    try:
        return Path(settings.GCS_CREDENTIALS_PATH).read_text()
    except OSError as exc:
        log.warning("Couldn't read GCS_CREDENTIALS_PATH: %s", exc)
        return None


def gcs_configured() -> bool:
    return bool(settings.GCS_BUCKET and _gcs_credentials_json())


@lru_cache(maxsize=1)
def _gcs_client() -> gcs_storage.Client | None:
    creds_json = _gcs_credentials_json()
    if not creds_json:
        return None
    info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(info)
    return gcs_storage.Client(credentials=creds, project=info.get("project_id"))


def signed_url(object_key: str, expires_minutes: int = 180) -> str | None:
    """A time-limited playback URL for a private recording. None if GCS isn't configured,
    the object doesn't exist, or signing fails — never a broken/expired-looking link."""
    if not object_key:
        return None
    client = _gcs_client()
    if client is None:
        return None
    try:
        blob = client.bucket(settings.GCS_BUCKET).blob(object_key)
        return blob.generate_signed_url(
            version="v4", expiration=timedelta(minutes=expires_minutes), method="GET"
        )
    except Exception as exc:  # noqa: BLE001 — a signing failure must not break the page
        log.warning("GCS signed URL failed for %s: %s", object_key, exc)
        return None


def delete_object(object_key: str) -> bool:
    """Best-effort delete of a recording's file. Returns False (never raises) if GCS isn't
    configured or the object is already gone — the DB row is the source of truth for
    whether a recording exists from the app's point of view, so a storage-side miss must
    not block removing it."""
    if not object_key:
        return False
    client = _gcs_client()
    if client is None:
        return False
    try:
        client.bucket(settings.GCS_BUCKET).blob(object_key).delete()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("GCS delete failed for %s: %s", object_key, exc)
        return False


def _egress_request(room: str, quality: str, filepath: str) -> api.RoomCompositeEgressRequest:
    output = api.EncodedFileOutput(file_type=api.EncodedFileType.MP4, filepath=filepath)
    creds_json = _gcs_credentials_json()
    if creds_json and settings.GCS_BUCKET:
        output.gcp.CopyFrom(api.GCPUpload(credentials=creds_json, bucket=settings.GCS_BUCKET))
    req = api.RoomCompositeEgressRequest(room_name=room, file_outputs=[output])
    if quality in _ADVANCED:
        width, height = _ADVANCED[quality]
        req.advanced.CopyFrom(api.EncodingOptions(width=width, height=height, framerate=30))
    else:
        req.preset = _PRESETS.get(quality, _PRESETS["1080p"])
    return req


async def start_recording(room: str, quality: str, filepath: str) -> tuple[str | None, str | None]:
    """Returns (egress_id, error). Exactly one is set."""
    if not configured():
        return None, "LiveKit is not configured"
    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        info = await lk.egress.start_room_composite_egress(_egress_request(room, quality, filepath))
        return info.egress_id, None
    except Exception as exc:  # noqa: BLE001 — a failed egress must not fail the host's click
        log.warning("livekit egress start failed: %s", exc)
        return None, str(exc)[:400]
    finally:
        await lk.aclose()


async def stop_recording(egress_id: str) -> str | None:
    """Returns an error string, or None on success."""
    if not configured() or not egress_id:
        return None
    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("livekit egress stop failed: %s", exc)
        return str(exc)[:400]
    finally:
        await lk.aclose()


def webhook_receiver() -> api.WebhookReceiver | None:
    """Verifies the Authorization JWT on LiveKit webhook posts. None when unconfigured —
    the endpoint then rejects everything rather than trusting unsigned bodies."""
    if not configured():
        return None
    return api.WebhookReceiver(api.TokenVerifier(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET))
