"""What a rejoin must not destroy, and what it has to put back.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
livekit.set_stage works through UpdateParticipant, which applies to the LIVE participant
session. A reconnect is a new session, and its permissions come from the TOKEN — which for a
viewer is minted can_publish=False. Nothing re-applied the grant, and participant_joined
additionally wrote role="viewer" unconditionally while leaving `on_stage` alone, so a speaker
who dropped for three seconds came back as a viewer who was somehow still on stage, holding
no publish right, in front of a console that said otherwise.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services import bus


@pytest.fixture
def event_id():
    return uuid.uuid4()


# ── the presence half, exercised directly against the bus ──────────────────────────────

@pytest.mark.asyncio
async def test_a_first_arrival_gets_viewer_defaults(event_id):
    await bus.presence_clear(event_id)
    rec = await bus.presence_upsert(event_id, "newcomer", {
        "name": "Newcomer", "role": "viewer", "on_stage": False,
        "muted": False, "speaking": False, "hand": False, "quality": "excellent",
    })
    assert rec["role"] == "viewer"
    assert rec["on_stage"] is False
    await bus.presence_clear(event_id)


@pytest.mark.asyncio
async def test_a_rejoin_patch_does_not_flatten_an_existing_speaker(event_id):
    """The shape of the fix: the join handler now patches only name/quality for somebody it
    has seen before, so the moderation-owned fields survive."""
    await bus.presence_clear(event_id)
    await bus.presence_upsert(event_id, "speaker-1", {
        "name": "Nani", "role": "speaker", "on_stage": True, "muted": True,
    })

    # Exactly what participant_joined now writes for a KNOWN participant.
    rec = await bus.presence_upsert(event_id, "speaker-1",
                                    {"name": "Nani", "quality": "excellent"})

    assert rec["role"] == "speaker", "a reconnect demoted a speaker"
    assert rec["on_stage"] is True, "a reconnect took them off stage"
    assert rec["muted"] is True, "a reconnect silently unmuted them"
    await bus.presence_clear(event_id)


@pytest.mark.asyncio
async def test_role_and_on_stage_never_contradict_after_a_rejoin(event_id):
    """The contradictory state the audit found: role=viewer while on_stage=True."""
    await bus.presence_clear(event_id)
    await bus.presence_upsert(event_id, "speaker-1", {"role": "speaker", "on_stage": True})
    rec = await bus.presence_upsert(event_id, "speaker-1",
                                    {"name": "Nani", "quality": "excellent"})

    on_stage = rec.get("on_stage")
    role = rec.get("role")
    assert not (on_stage and role == "viewer"), (
        f"contradictory state after rejoin: role={role!r} on_stage={on_stage!r}")
    await bus.presence_clear(event_id)


# ── the permission half ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restoring_the_grant_asks_for_publish_not_a_microphone(event_id):
    """set_stage restores the RIGHT to publish. Nothing about it captures a microphone —
    the browser still needs an explicit click, which is the consent rule."""
    from app.services import livekit

    svc = AsyncMock()
    svc.update_participant = AsyncMock()

    async def fake_with_room(fn):
        await fn(svc)
        return True

    with patch.object(livekit, "_with_room", fake_with_room):
        assert await livekit.set_stage("room-a", "speaker-1", True) is True

    req = svc.update_participant.await_args.args[0]
    assert req.permission.can_publish is True
    assert req.permission.can_subscribe is True
    assert req.room == "room-a" and req.identity == "speaker-1"
    # The ONLY call made is the permission update — nothing here touches media.
    assert svc.update_participant.await_count == 1
    assert svc.method_calls == [] or all(
        c[0] == "update_participant" for c in svc.method_calls)


@pytest.mark.asyncio
async def test_an_ordinary_viewer_is_not_handed_publish_on_rejoin(event_id):
    """Only a participant whose presence says on_stage gets the grant back. A plain viewer
    rejoining must stay can_publish=False, which is what their token already says."""
    await bus.presence_clear(event_id)
    await bus.presence_upsert(event_id, "viewer-1", {"role": "viewer", "on_stage": False})
    prior = next((r for r in await bus.presence_all(event_id)
                  if r.get("identity") == "viewer-1"), None)

    # This is the exact condition the join handler uses.
    assert prior is not None
    assert not prior.get("on_stage"), "an ordinary viewer must not trigger a stage restore"
    await bus.presence_clear(event_id)


@pytest.mark.asyncio
async def test_a_demoted_viewer_does_not_get_their_grant_back(event_id):
    """Demotion writes on_stage False, so the restore condition is false for them too."""
    await bus.presence_clear(event_id)
    await bus.presence_upsert(event_id, "ex-speaker", {"role": "speaker", "on_stage": True})
    await bus.presence_upsert(event_id, "ex-speaker", {"role": "viewer", "on_stage": False})

    prior = next((r for r in await bus.presence_all(event_id)
                  if r.get("identity") == "ex-speaker"), None)
    assert prior["on_stage"] is False
    assert prior["role"] == "viewer"
    await bus.presence_clear(event_id)
