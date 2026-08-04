"""LiveKit access tokens + server-side room control.

The room-control helpers are what make a moderator action REAL: without them, muting a
participant only repaints the console. Every one of them no-ops (returns False) when
LiveKit is unconfigured, so the moderation console still works end-to-end in dev — the
state change and the broadcast happen either way, the media enforcement is what's missing.
"""

from __future__ import annotations

import logging

from livekit import api

from app.config import settings
from app.services import storage

log = logging.getLogger(__name__)


# Track sources, as the JWT grant spells them (VideoGrants.can_publish_sources is a list of
# strings) and as the room-control API spells them (ParticipantPermission.can_publish_sources is
# a list of the TrackSource enum). Two vocabularies for one concept, so they are mapped once.
CAMERA, MICROPHONE, SCREEN_SHARE, SCREEN_SHARE_AUDIO = (
    "camera", "microphone", "screen_share", "screen_share_audio",
)
ALL_SOURCES = (CAMERA, MICROPHONE, SCREEN_SHARE, SCREEN_SHARE_AUDIO)
# What a speaker gets by default: their face and their voice. Screen share is granted
# separately, because "may talk" and "may put anything on the main screen" are different
# decisions — see services/speaker.py.
SPEAKER_SOURCES = (CAMERA, MICROPHONE)

_SOURCE_ENUM = {
    CAMERA: api.TrackSource.CAMERA,
    MICROPHONE: api.TrackSource.MICROPHONE,
    SCREEN_SHARE: api.TrackSource.SCREEN_SHARE,
    SCREEN_SHARE_AUDIO: api.TrackSource.SCREEN_SHARE_AUDIO,
}


def clean_sources(sources) -> list[str]:
    """Keep only names LiveKit knows, in a stable order. An unknown source is dropped rather
    than passed through — a typo must not silently widen a grant."""
    return [s for s in ALL_SOURCES if s in set(sources or ())]


def create_stream_token(
    identity: str,
    room_name: str,
    can_publish: bool,
    sources=None,
):
    """Mint a room token.

    `sources` narrows WHAT may be published (camera/microphone/screen_share). Omit it for the
    historic all-or-nothing behaviour: a host publishes everything, an attendee publishes
    nothing. A speaker gets camera+microphone and no screen share until somebody grants it,
    which is what stops an on-stage speaker putting their desktop on the main screen unasked.

    The grant is only half the enforcement — `set_publish_sources` below updates the live
    permission at the SFU, which is what governs an ALREADY-connected publisher. Tokens cannot
    be revoked once issued, so the permission is the real lever and the token is only the join
    credential.
    """
    allowed = clean_sources(sources) if sources is not None else None

    grant = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=can_publish,
        can_subscribe=True,
    )
    if can_publish and allowed is not None:
        grant.can_publish_sources = allowed

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
    actually means in LiveKit.

    Going on stage grants camera + microphone only. Screen share is deliberately NOT included:
    it is granted per-source by set_publish_sources, so staging a panellist does not also hand
    them the main screen.
    """
    return await set_publish_sources(room, identity, SPEAKER_SOURCES if on_stage else ())


async def set_publish_sources(room: str, identity: str, sources) -> bool:
    """Set exactly which track sources a participant may publish. An empty list is "off stage".

    This exists because the old code expressed every media control as `can_publish` on/off, so
    "turn this speaker's camera off" and "stop this speaker sharing their screen" both revoked
    the whole publish permission — cutting their microphone mid-sentence. Sources are the level
    the controls are actually about.
    """
    allowed = clean_sources(sources)
    return await _with_room(
        lambda svc: svc.update_participant(
            api.UpdateParticipantRequest(
                room=room,
                identity=identity,
                permission=api.ParticipantPermission(
                    can_subscribe=True,
                    can_publish=bool(allowed),
                    can_publish_data=True,
                    can_publish_sources=[_SOURCE_ENUM[s] for s in allowed],
                ),
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
# request is rejected. `storage.egress_output` supplies that destination when a bucket is
# configured; when it isn't, we still surface (None, error) so the console can say "recording
# not enforced" instead of implying a file exists.

# Presets only reach 1080p; 2K/4K need explicit encoding options.
_PRESETS = {
    "720p": api.EncodingOptionsPreset.H264_720P_30,
    "1080p": api.EncodingOptionsPreset.H264_1080P_30,
}
_ADVANCED = {
    "2k": (2560, 1440),
    "4k": (3840, 2160),
}


def _egress_request(room: str, quality: str, filepath: str,
                    download_name: str | None = None) -> api.RoomCompositeEgressRequest:
    output = storage.egress_output(filepath, filename=download_name)
    if output is None:
        # No bucket: keep the historic shape so the request is still well-formed and LiveKit's own
        # refusal is what gets reported, rather than us guessing at its policy.
        output = api.EncodedFileOutput(file_type=api.EncodedFileType.MP4, filepath=filepath)
    req = api.RoomCompositeEgressRequest(room_name=room, file_outputs=[output])
    if quality in _ADVANCED:
        width, height = _ADVANCED[quality]
        req.advanced.CopyFrom(api.EncodingOptions(width=width, height=height, framerate=30))
    else:
        req.preset = _PRESETS.get(quality, _PRESETS["1080p"])
    return req


async def start_recording(room: str, quality: str, filepath: str,
                          download_name: str | None = None) -> tuple[str | None, str | None]:
    """Returns (egress_id, error). Exactly one is set."""
    if not configured():
        return None, "LiveKit is not configured"
    if not storage.configured():
        # Refuse EARLY rather than letting LiveKit accept an egress it cannot deliver. Without a
        # bucket a Cloud egress is rejected anyway and a self-hosted one writes into a container
        # nobody can read — either way the row would claim a capture that produces no file.
        return None, "No recording storage configured (set S3_BUCKET) — capture not retained"
    lk = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    try:
        info = await lk.egress.start_room_composite_egress(
            _egress_request(room, quality, filepath, download_name)
        )
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
