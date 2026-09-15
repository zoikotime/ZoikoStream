"""The host's microphone actions, and what they can and cannot actually do.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
LiveKit mutes PER TRACK. A viewer who publishes nothing has no track, so
livekit.mute_participant iterated an empty list, did nothing, and — because _with_room only
reports "the request did not raise" — answered success. The console then recorded muted=False
and showed the person as live while the room stayed silent. The host appeared to have
unmuted somebody's microphone, which is not a thing a server can do.

These pin the three separate operations the old single button conflated:
    permission to publish   livekit.set_stage      (the host really can do this)
    muting a live track     livekit.mute_participant (only if a track exists)
    turning a mic ON        nobody but the participant
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import livekit


class FakeTrack:
    def __init__(self, sid):
        self.sid = sid


class FakeInfo:
    def __init__(self, tracks):
        self.tracks = tracks


def room_service(tracks):
    """A stand-in LiveKit room service whose participant publishes `tracks`."""
    svc = AsyncMock()
    svc.get_participant = AsyncMock(return_value=FakeInfo(tracks))
    svc.mute_published_track = AsyncMock()
    svc.update_participant = AsyncMock()
    return svc


def with_service(svc):
    """Patch _with_room so the call runs against `svc` and reports like the real one."""
    async def fake(fn):
        try:
            await fn(svc)
            return True
        except Exception:
            return False
    return patch.object(livekit, "_with_room", fake)


# ── the heart of it: nothing to mute is not success ────────────────────────────────────

@pytest.mark.asyncio
async def test_unmuting_a_participant_with_no_tracks_reports_no_tracks():
    svc = room_service([])
    with with_service(svc):
        outcome = await livekit.mute_participant("room", "viewer-1", False)

    assert outcome == livekit.MUTE_NO_TRACKS
    # And it really did nothing, rather than doing something unobservable.
    svc.mute_published_track.assert_not_awaited()


@pytest.mark.asyncio
async def test_muting_a_live_track_reports_ok():
    svc = room_service([FakeTrack("TR_1")])
    with with_service(svc):
        outcome = await livekit.mute_participant("room", "speaker-1", True)

    assert outcome == livekit.MUTE_OK
    svc.mute_published_track.assert_awaited_once()


@pytest.mark.asyncio
async def test_every_published_track_is_muted():
    svc = room_service([FakeTrack("TR_1"), FakeTrack("TR_2")])
    with with_service(svc):
        assert await livekit.mute_participant("room", "speaker-1", True) == livekit.MUTE_OK
    assert svc.mute_published_track.await_count == 2


@pytest.mark.asyncio
async def test_a_livekit_failure_is_distinguishable_from_having_no_tracks():
    """Both leave the room silent, but they need different words in front of a host."""
    async def boom(_fn):
        return False
    with patch.object(livekit, "_with_room", boom):
        assert await livekit.mute_participant("room", "x", True) == livekit.MUTE_FAILED


@pytest.mark.asyncio
async def test_the_outcomes_are_distinct_and_all_truthy():
    """A caller that treats the result as a bool would read MUTE_FAILED as success, which is
    exactly the class of mistake this replaced. Pinned so the hazard stays visible."""
    values = {livekit.MUTE_OK, livekit.MUTE_NO_TRACKS, livekit.MUTE_FAILED}
    assert len(values) == 3
    assert all(bool(v) for v in values)


# ── the publish grant is real, and event-specific ──────────────────────────────────────

@pytest.mark.asyncio
async def test_inviting_to_stage_grants_publish_for_that_room_only():
    svc = room_service([])
    with with_service(svc):
        assert await livekit.set_stage("room-a", "viewer-1", True) is True

    req = svc.update_participant.await_args.args[0]
    assert req.room == "room-a"                 # scoped to ONE event's room
    assert req.identity == "viewer-1"           # and ONE participant
    assert req.permission.can_publish is True
    assert req.permission.can_subscribe is True


@pytest.mark.asyncio
async def test_removing_from_stage_revokes_publish_but_keeps_watching():
    svc = room_service([])
    with with_service(svc):
        assert await livekit.set_stage("room-a", "speaker-1", False) is True

    req = svc.update_participant.await_args.args[0]
    assert req.permission.can_publish is False
    # Demotion must not eject them from the audience.
    assert req.permission.can_subscribe is True


@pytest.mark.asyncio
async def test_a_viewer_is_not_granted_publish_anywhere_by_default():
    """Nothing in the stage call touches any other room or identity — the grant cannot leak
    into a second event, which is what "event-specific" has to mean."""
    svc = room_service([])
    with with_service(svc):
        await livekit.set_stage("room-a", "viewer-1", True)

    assert svc.update_participant.await_count == 1
    req = svc.update_participant.await_args.args[0]
    assert (req.room, req.identity) == ("room-a", "viewer-1")
