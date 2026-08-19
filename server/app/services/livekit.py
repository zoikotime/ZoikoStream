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
    directly, this process never touches the uploaded bytes.

    This one is NOT optional in production even though _gcs_client() below can fall back
    to ambient credentials: LiveKit Cloud's egress workers run on LiveKit's own
    infrastructure, outside this project entirely, so they have no access to Cloud Run's
    attached service account (the "just use ADC" trick only works for code running
    *inside* this GCP project). A portable key is the only thing egress can authenticate
    with. In Cloud Run, mount it from Secret Manager as a file (Cloud Run -> Edit & Deploy
    -> Secrets -> "Mount as volume") and point this at the mount path — never bake the key
    into the image or commit it."""
    if not settings.GCS_CREDENTIALS_PATH:
        return None
    try:
        raw = Path(settings.GCS_CREDENTIALS_PATH).read_text()
    except OSError as exc:
        log.error("Couldn't read GCS_CREDENTIALS_PATH (%s): %s — recording uploads will be "
                  "rejected by LiveKit Cloud until this is fixed", settings.GCS_CREDENTIALS_PATH, exc)
        return None
    try:
        json.loads(raw)  # validate now so a bad key fails loudly here, not deep in an egress call
    except json.JSONDecodeError as exc:
        log.error("GCS_CREDENTIALS_PATH (%s) is set but isn't valid JSON — it must be a "
                  "downloaded service-account key file, not e.g. a console URL: %s",
                  settings.GCS_CREDENTIALS_PATH, exc)
        return None
    return raw


def gcs_configured() -> bool:
    """Whether recording uploads (the thing that actually needs the portable JSON key,
    not just this process's own reads) will work."""
    return bool(settings.GCS_BUCKET and _gcs_credentials_json())


@lru_cache(maxsize=1)
def _gcs_client() -> gcs_storage.Client | None:
    """This process's own client for reading back what egress already uploaded (signed
    URLs, existence checks, deletes) — separate from the credential egress itself needs
    (see _gcs_credentials_json). Prefers the same explicit key so behavior matches egress;
    falls back to Application Default Credentials so this still works on Cloud Run purely
    from the service's own attached identity (grant it Storage Object Admin on the bucket)
    even before a key file exists, and locally via `gcloud auth application-default login`."""
    if not settings.GCS_BUCKET:
        return None
    creds_json = _gcs_credentials_json()
    if creds_json:
        try:
            info = json.loads(creds_json)
            creds = service_account.Credentials.from_service_account_info(info)
            return gcs_storage.Client(credentials=creds, project=info.get("project_id"))
        except Exception as exc:  # noqa: BLE001 — fall through to ADC rather than go dark
            log.warning("Explicit GCS credentials failed to load, trying ADC instead: %s", exc)
    try:
        return gcs_storage.Client()  # auto-discovers ADC; raises DefaultCredentialsError if none
    except Exception as exc:  # noqa: BLE001 — no credentials available anywhere is not fatal
        log.warning("No usable GCS credentials (explicit key or ADC): %s", exc)
        return None


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


def object_exists(object_key: str) -> bool:
    """Whether the recording's file is actually sitting in the bucket. LiveKit accepting an
    egress request only means it WILL try to write the file — the row goes to "stopped" as
    soon as the host clicks stop, before the upload (or the egress itself) is confirmed to
    have succeeded. Without this, a silently-failed egress (network blip, no publisher ever
    attached, etc.) reads as a normal recording and viewers get a signed URL to nothing."""
    if not object_key:
        return False
    client = _gcs_client()
    if client is None:
        return False
    try:
        return client.bucket(settings.GCS_BUCKET).blob(object_key).exists()
    except Exception as exc:  # noqa: BLE001 — a storage hiccup must not break the watch page
        log.warning("GCS existence check failed for %s: %s", object_key, exc)
        return False


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


# ── ingress (RTMP/WHIP publish-in) ────────────────────────────────────────────
# An on-site encoder (or a WHIP-capable device) publishes directly into an event's room as
# a named participant — the hardware-contribution path, distinct from a browser
# contributor's app-mediated WHIP session (services/contributor.py). One LiveKit room per
# event everywhere else in this file (Ctx.room); ingress targets that same room by name,
# no separate room concept of its own.
#
# The installed livekit-api SDK's IngressInput enum has RTMP_INPUT and WHIP_INPUT only —
# no SRT_INPUT. Upgrading livekit-api (unpinned in requirements.txt) is a prerequisite if
# SRT is ever needed; don't offer it in the console until then.
INGRESS_TYPES = {"rtmp": api.IngressInput.RTMP_INPUT, "whip": api.IngressInput.WHIP_INPUT}


async def create_ingress(room: str, input_type: str, name: str, identity: str):
    """Returns (IngressInfo | None, error | None) — start_recording's shape, not
    _with_room's: the console needs to show WHY provisioning failed, not just whether it
    did. `IngressInfo.stream_key` is populated on this response only — it is never fetched
    or stored again after this call (see ingress_stream_key below and models/live.py's
    LiveIngressEndpoint docstring for why)."""
    if not configured():
        return None, "LiveKit is not configured"
    kind = INGRESS_TYPES.get(input_type)
    if kind is None:
        return None, f"Unsupported input type: {input_type}"
    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        info = await lk.ingress.create_ingress(api.CreateIngressRequest(
            input_type=kind, name=name, room_name=room,
            participant_identity=identity, participant_name=name,
        ))
        return info, None
    except Exception as exc:  # noqa: BLE001 — a failed provision must not fail the admin's click
        log.warning("livekit ingress create failed: %s", exc)
        return None, str(exc)[:400]
    finally:
        await lk.aclose()


async def ingress_credentials(ingress_id: str) -> tuple[str | None, str | None]:
    """Returns (ingest_url, stream_key) — the two fields the detail sheet needs, fetched
    live from LiveKit rather than ever persisted locally (see create_ingress). Both None
    — never raises — if unconfigured, the ingress is gone, or the call fails; the caller
    shows "unavailable", same as a missing recording file elsewhere in this stack."""
    if not configured() or not ingress_id:
        return None, None
    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        resp = await lk.ingress.list_ingress(api.ListIngressRequest(ingress_id=ingress_id))
        if not resp.items:
            return None, None
        info = resp.items[0]
        return info.url or None, info.stream_key or None
    except Exception as exc:  # noqa: BLE001
        log.warning("livekit ingress key lookup failed: %s", exc)
        return None, None
    finally:
        await lk.aclose()


def ingress_state_name(status_value: int) -> str:
    """LiveKit's IngressState.Status enum ("ENDPOINT_PUBLISHING", ...) mapped to this app's
    own lowercase vocabulary (models/live.py's INGRESS_STATES) — used by the ingress_started/
    ingress_ended webhook handler (routers/live.py) to write LiveIngressEndpoint.state.
    Looked up by name rather than a hardcoded ordinal map, so it stays correct even if the
    SDK ever reorders the enum."""
    name = api.IngressState.Status.Name(status_value)
    return name.removeprefix("ENDPOINT_").lower()


async def delete_ingress(ingress_id: str) -> bool:
    """Same boolean/swallow/log contract as close_room — state change (the DB row) happens
    either way; this reports whether LiveKit itself was actually told to stop it."""
    if not configured() or not ingress_id:
        return False
    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        await lk.ingress.delete_ingress(api.DeleteIngressRequest(ingress_id=ingress_id))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("livekit ingress delete failed: %s", exc)
        return False
    finally:
        await lk.aclose()


def webhook_receiver() -> api.WebhookReceiver | None:
    """Verifies the Authorization JWT on LiveKit webhook posts. None when unconfigured —
    the endpoint then rejects everything rather than trusting unsigned bodies."""
    if not configured():
        return None
    return api.WebhookReceiver(api.TokenVerifier(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET))
