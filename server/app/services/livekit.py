"""LiveKit access tokens + server-side room control.

The room-control helpers are what make a moderation action REAL: without them, muting a
participant only repaints the console. Every one of them no-ops (returns False) when
LiveKit is unconfigured, so the moderation console still works end-to-end in dev — the
state change and the broadcast happen either way, the media enforcement is what's missing.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from google.cloud import storage as gcs_storage
from google.oauth2 import service_account
from livekit import api

from app.config import settings

log = logging.getLogger(__name__)


# BRD "Secure" stage requirement: "short-lived playback authorization" for the audience
# player (routers/events.py's watch_event, can_publish=False). Left unset, the SDK falls
# back to an undocumented ~6h default — bounding it explicitly is the fix. 4 hours covers
# a real live memorial service plus margin (viewers typically load the watch page once,
# at or shortly before go-live, and useLiveKitViewer.js doesn't currently re-fetch a token
# on reconnect — see that file's own docstring — so this can't be cut much tighter without
# also adding a refresh mechanism, which is deliberately out of scope for this pass).
PLAYBACK_TOKEN_TTL = timedelta(hours=4)


# ── secondary connection identities ────────────────────────────────────────────
# LiveKit allows exactly ONE connection per identity in a room — a second connection on the
# same identity EVICTS the first. Every token minted for a signed-in user used identity=
# str(user.id) (moderation.resolve_ctx), so a user holding more than one simultaneous LiveKit
# connection for the same event collided:
#   * the host console (a publish connection, see services/broadcast.py's _preview/
#     snapshot_extra) vs. that same host visiting the public watch page (a subscribe
#     connection, routers/events.py's watch_event) — both str(user.id).
#   * the Backstage page's own return-feed monitor (client/src/pages/speaker/Backstage.jsx,
#     a subscribe-only token from watch_event) vs. that same contributor's own mic/cam
#     publish connection (services/contributor.py's my_publish_token) — again both
#     str(user.id).
# Each collision meant both connections saw a Disconnected(DUPLICATE_IDENTITY), each
# reconnected, each eviction re-triggered the other — burning both retry budgets in seconds
# and leaving BOTH sides on "couldn't reconnect" (see client/src/hooks/livekitDisconnect.js,
# added for exactly this symptom, and server/test_livekit_identity.py's own docstring).
#
# secondary()/primary() are the fix: a SECONDARY connection for a user (their host-console
# publish, or their own return-feed monitor) gets a distinguishable identity so it can
# coexist with that same user's PRIMARY connection (their audience/moderation-target
# identity, or a contributor's own publish token) in the same room. Deliberately NOT used
# for every connection — the primary identity is what moderator actions (mute/promote/
# remove — services/moderation.py's _participant_action, services/contributor.py's
# _operator_action) and the LiveKit webhook's presence bookkeeping key on, so tagging it
# would silently address (or presence-track) a participant that doesn't exist under that
# name. Only the two connections documented above ever call secondary(); everything else
# (ordinary viewers, guest/link/anonymous identities, ingress endpoints, a contributor's own
# publish token) is untouched and stays exactly as it already was.
#
# Format is deliberately NOT the existing "prefix-uuid" convention used elsewhere in this
# codebase (guest-<id>, guest-link-<id>, viewer-<id>, ingress-<id>) — those all use a plain
# hyphen, which a UUID payload also contains, so a naive split() would be ambiguous. "::" is
# a delimiter none of those, and no UUID, can ever contain, which is what lets primary()
# safely pass every one of them through completely unchanged (see test_livekit_identity.py's
# test_tagged_identity_resolves_back_to_its_owner).
_SECONDARY_TAGS = ("host", "monitor")


def secondary(identity: str, tag: str) -> str:
    """A distinguishable LiveKit identity for `identity`'s SECOND simultaneous connection to
    the same room, so it doesn't evict (or get evicted by) their primary one. `tag` must be
    one of _SECONDARY_TAGS."""
    if tag not in _SECONDARY_TAGS:
        raise ValueError(f"unknown secondary connection tag: {tag!r}")
    return f"{tag}::{identity}"


def primary(identity: str) -> str:
    """The reverse of secondary(): strips a known tag prefix to recover the identity a
    presence record / moderation action should key on. Anything that isn't one of OUR
    tags — a plain user id, a guest/guest-link/viewer/ingress identity — passes through
    unchanged, so this is always safe to call on any identity a LiveKit webhook hands us
    (routers/live.py), tagged or not."""
    for tag in _SECONDARY_TAGS:
        prefix = f"{tag}::"
        if identity.startswith(prefix):
            return identity[len(prefix):]
    return identity


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
    # Publish tokens (host/contributor) are deliberately left on the SDK default — a host
    # or speaker's session can legitimately run long, and shortening THAT token is a
    # separate tradeoff the BRD doesn't ask for here. Only playback (can_publish=False) is
    # bounded, per the "short-lived playback authorization" requirement specifically.
    if not can_publish:
        token.with_ttl(PLAYBACK_TOKEN_TTL)


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
    except Exception as exc:  # noqa: BLE001 - LiveKit errors must not fail a moderation action
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


async def participant_connected(room: str, identity: str) -> bool:
    """Whether this EXACT identity currently holds a live connection in the room.

    Exact, not "is this person here": a contributor backstage is connected under their
    tagged monitor identity (secondary(id, "monitor")) while their publishing identity — the
    bare id this asks about — is not in the room at all yet. That distinction is the whole
    point of the call: see _operator_action's bring_live, which uses it to tell "LiveKit
    refused to stage someone who IS here" (a real enforcement failure) apart from "there is
    nobody under that identity to stage yet" (the normal pre-go-live state, where the publish
    grant rides on the token the browser fetches once it goes live, not on this call).

    False on any error, including a not-configured LiveKit — a caller that cannot confirm
    presence must fall back to the permissive path, never block a legitimate action."""
    return await _with_room(
        lambda svc: svc.get_participant(api.RoomParticipantIdentity(room=room, identity=identity))
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


def gcs_config_error() -> str | None:
    """A specific, actionable diagnosis of why recording uploads won't work — or None if
    they will. Split out from _gcs_credentials_json's own logging (which only ever fires once
    per process, thanks to lru_cache) so a caller that needs to explain the failure to a
    *user* (a host clicking Record, an admin at startup) always gets a fresh, precise reason
    instead of nothing on every call after the first.

    Named separately from _gcs_credentials_json's cached parse so it's cheap to call
    speculatively (startup, every recording.start) without re-reading the file each time —
    it reuses that cached result rather than re-parsing."""
    if not settings.GCS_BUCKET:
        return "GCS_BUCKET is not set — recordings will not be uploaded anywhere"
    path = settings.GCS_CREDENTIALS_PATH
    if not path:
        return "GCS_CREDENTIALS_PATH is not set — egress has no destination credentials"
    if path.startswith(("http://", "https://")):
        return (
            "GCS_CREDENTIALS_PATH is a URL (looks like a Google Cloud Console link), not a "
            "path to a downloaded service-account JSON key file. Download a key for a "
            "service account with Storage Object Admin on the bucket, and point this at "
            "that file's path."
        )
    raw = _gcs_credentials_json()
    if raw is None:
        return (f"GCS_CREDENTIALS_PATH ({path}) could not be read as a valid service-account "
                "JSON key file — see the server log for the underlying read/parse error")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return f"GCS_CREDENTIALS_PATH ({path}) is not valid JSON"
    if info.get("type") != "service_account" or not info.get("client_email"):
        return (f"GCS_CREDENTIALS_PATH ({path}) is valid JSON but doesn't look like a "
                "downloaded service-account key (missing type=service_account/client_email)")
    return None


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


def download_to_temp(object_key: str) -> str | None:
    """Pulls a recording's raw bytes onto local disk for local processing (currently:
    services/watermark.py's ffmpeg pass) — the one case that needs the actual file rather
    than a signed URL a browser can stream. Caller owns cleanup of the returned path.
    None (never raises) on any failure, same posture as every other function here."""
    if not object_key:
        return None
    client = _gcs_client()
    if client is None:
        return None
    try:
        fd, local_path = tempfile.mkstemp(suffix=Path(object_key).suffix or ".mp4")
        os.close(fd)
        client.bucket(settings.GCS_BUCKET).blob(object_key).download_to_filename(local_path)
        return local_path
    except Exception as exc:  # noqa: BLE001 — a download failure must not raise into a ticker
        log.warning("GCS download failed for %s: %s", object_key, exc)
        return None


def upload_object(local_path: str, object_key: str) -> bool:
    """Uploads a local file (a watermark burn's output) to GCS at object_key. The
    counterpart to download_to_temp — everything else in this module only ever reads what
    LiveKit's own egress already wrote (see _egress_request's GCPUpload)."""
    client = _gcs_client()
    if client is None:
        return False
    try:
        client.bucket(settings.GCS_BUCKET).blob(object_key).upload_from_filename(local_path)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("GCS upload failed for %s: %s", object_key, exc)
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
