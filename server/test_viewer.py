"""Self-check for the attendee (viewer) surface: who may open an event, when a playback
token may be issued, and the two server-side projections that keep console-only data off
an attendee's socket. Pure logic — no database, no Redis, no LiveKit.
Run: `python test_viewer.py` (or pytest)."""
import uuid
from types import SimpleNamespace

from app.services import moderation as m
from app.services import viewer as v

ORG = uuid.uuid4()
OTHER_ORG = uuid.uuid4()


def _event(**kw):
    # `registration_required` is part of the shape playback_blocked_reason reads — a stand-in that
    # omits it is testing a different object from the one production passes.
    return SimpleNamespace(
        **{"id": uuid.uuid4(), "org_id": ORG, "visibility": "public", "status": "live",
           "registration_required": False, **kw}
    )


def _user(role="viewer", org_id=ORG):
    return SimpleNamespace(id=uuid.uuid4(), role=role, org_id=org_id)


# ── access ────────────────────────────────────────────────────────────────────

def test_org_member_always_allowed_even_on_private():
    allowed, basis, _ = v.access_for(_event(visibility="private"), _user())
    assert allowed and basis == "organization_member"


def test_super_admin_bypasses_org():
    allowed, basis, _ = v.access_for(
        _event(visibility="private"), _user(role="super_admin", org_id=OTHER_ORG)
    )
    assert allowed and basis == "platform_admin"


def test_outsider_allowed_on_public_and_unlisted():
    for vis in ("public", "unlisted"):
        allowed, basis, _ = v.access_for(_event(visibility=vis), _user(org_id=OTHER_ORG))
        assert allowed, f"{vis} event refused an outsider"
        assert basis == f"{vis}_event"


def test_outsider_refused_on_private():
    allowed, _, reason = v.access_for(_event(visibility="private"), _user(org_id=OTHER_ORG))
    assert not allowed and reason


def test_user_with_no_org_does_not_match_events_without_one():
    """A null org_id must never satisfy the membership check by comparing equal to another
    null — that would hand every orgless account into every orgless event."""
    ev = _event(visibility="private")
    ev.org_id = None
    allowed, _, _ = v.access_for(ev, _user(org_id=None))
    assert not allowed


# ── playback gating ───────────────────────────────────────────────────────────

def test_playback_refused_unless_live(monkeypatch=None):
    v.livekit.configured = lambda: True
    assert v.playback_blocked_reason(_event(status="live")) is None
    for status in ("published", "scheduled", "ended", "cancelled"):
        assert v.playback_blocked_reason(_event(status=status)), f"{status} issued a token"


def test_playback_refused_without_livekit():
    v.livekit.configured = lambda: False
    assert v.playback_blocked_reason(_event(status="live"))
    v.livekit.configured = lambda: True  # restore for any later check


def test_registration_gates_the_media_not_the_page():
    """Registration is enforced on the MEDIA, like the passphrase and for the same reason: an
    attendee who hasn't signed up must still reach the landing page, because that is where the
    Register button is. access_for deliberately knows nothing about it."""
    v.livekit.configured = lambda: True
    gated = _event(status="live", registration_required=True)

    assert v.playback_blocked_reason(gated, registered=False) is not None
    assert v.playback_blocked_reason(gated, registered=True) is None
    # The organizing org and platform admins never face their own audience gate.
    assert v.playback_blocked_reason(gated, registered=False, exempt=True) is None
    # A caller that did not look it up gets no enforcement rather than a guess.
    assert v.playback_blocked_reason(gated, registered=None) is None
    # And an ungated event is unaffected either way.
    open_event = _event(status="live")
    assert v.playback_blocked_reason(open_event, registered=False) is None

    # The PAGE stays reachable — that is the whole point of keeping this out of access_for.
    allowed, _basis, _reason = v.access_for(gated, _user(org_id="somewhere-else"))
    assert allowed is True


# ── snapshot projection ───────────────────────────────────────────────────────

# Everything a host/moderator snapshot carries. The attendee projection must keep the
# left column and drop the right one.
_FULL_SNAPSHOT = {
    "event": {"id": "e1", "name": "Summit"},
    "speakers": [{"id": "s1"}],
    "messages": [], "questions": [], "polls": [], "announcements": [],
    "countdown_until": "2026-08-02T09:00:00Z",
    "livekit_url": "wss://x.livekit.cloud",
    "livekit_enforced": True,
    "can_moderate": True,
    "can_host": True,
    # — must not survive —
    "participants": [{"identity": "u1", "name": "Ada", "quality": "poor"}],
    "activity": [{"text": "Ada was removed"}],
    "analytics": {"viewers": 42, "engagement": 71, "poor_connections": 3, "peak_viewers": 90},
    "broadcast": {"status": "live", "settings": {"bitrate": 6000}},
    "recording": {"id": "r1"}, "recordings": [{"id": "r1"}],
    "health": {"level": "warn", "issues": ["3 participants on a poor connection"]},
    "publish_token": "SECRET.PUBLISH.JWT",
}

_MUST_NOT_LEAK = ("participants", "activity", "analytics", "broadcast", "recording",
                  "recordings", "health", "publish_token")


def test_viewer_snapshot_drops_every_privileged_key():
    out = m.viewer_snapshot(_FULL_SNAPSHOT)
    for key in _MUST_NOT_LEAK:
        assert key not in out, f"viewer snapshot leaked {key!r}"


def test_viewer_snapshot_keeps_what_the_page_needs():
    out = m.viewer_snapshot(_FULL_SNAPSHOT)
    assert out["event"]["name"] == "Summit"
    assert out["countdown_until"] and out["livekit_url"]
    # The viewer count survives as a bare aggregate, without the roster it came from.
    assert out["audience"] == {"viewers": 42}
    assert out["stream"] == {"status": "live"}


def test_viewer_snapshot_forces_permissions_false():
    """can_moderate/can_host arrive true here only because the fixture is a host's
    snapshot; the projection must never pass a true through to an attendee's client."""
    out = m.viewer_snapshot(_FULL_SNAPSHOT)
    assert out["can_moderate"] is False and out["can_host"] is False


def test_unknown_snapshot_key_is_dropped_not_passed_through():
    """The allow-list's whole point: a key added to the snapshot later is invisible to
    attendees until someone adds it to VIEWER_SNAPSHOT_KEYS on purpose."""
    out = m.viewer_snapshot({**_FULL_SNAPSHOT, "future_secret": "oops"})
    assert "future_secret" not in out


# ── envelope projection ───────────────────────────────────────────────────────

def _env(channel, kind, data):
    return {"channel": channel, "type": kind, "data": data}


def test_participation_channels_pass_through():
    for channel in ("chat", "qa", "poll", "announcement"):
        env = _env(channel, "message.new", {"text": "hi"})
        assert m.viewer_envelope(env) == env


def test_privileged_channels_are_dropped():
    for env in (
        _env("activity", "activity.new", {"text": "Ada was removed"}),
        _env("participants", "participant.join", {"identity": "u1", "name": "Ada"}),
        _env("recording", "recording.update", {"id": "r1"}),
        _env("broadcast", "broadcast.health", {"issues": ["encoder dropped"]}),
        _env("stage", "waiting.join", {"identity": "u1"}),
    ):
        assert m.viewer_envelope(env) is None, f"{env['channel']}/{env['type']} reached a viewer"


def test_analytics_tick_is_narrowed_to_the_viewer_count():
    out = m.viewer_envelope(_env("analytics", "analytics.tick", {
        "viewers": 42, "engagement": 71, "poor_connections": 3, "peak_viewers": 90,
        "avg_watch_seconds": 812, "health": {"level": "warn"},
    }))
    assert out["data"] == {"viewers": 42}


def test_room_status_keeps_only_liveness():
    out = m.viewer_envelope(_env("moderator", "room.status", {"live": True, "recovering": False}))
    assert out["data"] == {"live": True, "recovering": False}


def test_broadcast_update_keeps_only_status():
    out = m.viewer_envelope(_env("broadcast", "broadcast.update", {
        "status": "live", "settings": {"bitrate": 6000}, "peak_viewers": 90,
    }))
    assert out["data"] == {"status": "live"}


def test_missing_field_is_omitted_not_nulled():
    out = m.viewer_envelope(_env("moderator", "room.status", {"live": False}))
    assert out["data"] == {"live": False}


# ── the projection is actually WIRED to the socket ────────────────────────────

def test_viewer_socket_gets_projected_snapshot_and_filtered_feed():
    """The two projections above are only worth anything if routers/live.py applies them.
    This drives the real socket with can_moderate=False and asserts both halves: the opening
    frame is projected, and a privileged envelope published to the bus never arrives.

    Only the auth/DB boundary is stubbed — the loop, the bus and the writer are real.
    """
    import types

    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import live as live_router

    event_id, user_id = uuid.uuid4(), uuid.uuid4()
    ctx = m.Ctx(event_id=event_id, org_id=uuid.uuid4(), room=f"event_{event_id}",
                user_id=user_id, name="Attendee", identity=str(user_id),
                role="viewer", can_moderate=False, can_host=False)

    async def fake_snapshot(_ctx):
        return dict(_FULL_SNAPSHOT)

    originals = (live_router._user_from_token, live_router.mod.resolve_ctx, live_router.mod.snapshot)
    live_router._user_from_token = lambda token, db: types.SimpleNamespace(
        id=user_id, email="attendee@example.com", is_active=True, role="viewer", full_name="Attendee"
    )
    live_router.mod.resolve_ctx = lambda *_: ctx
    live_router.mod.snapshot = fake_snapshot

    try:
        client = TestClient(app)
        with client.websocket_connect(f"/live/events/{event_id}/ws?token=good") as ws:
            first = ws.receive_json()
            assert (first["channel"], first["type"]) == ("moderator", "snapshot")
            for key in _MUST_NOT_LEAK:
                assert key not in first["data"], f"the socket leaked {key!r} to an attendee"
            assert first["data"]["audience"] == {"viewers": 42}

            # Envelopes are driven through real ACTIONS rather than bus.publish: the bus is
            # in-process asyncio queues, so publishing from a second event loop would never
            # reach this socket's consumer.
            #
            # `participant.state` publishes on `participants` (filtered OUT for attendees);
            # `chat.typing` publishes on `chat` (allowed through). Sending the filtered one
            # FIRST means receiving the chat frame next proves the participants frame was
            # dropped, not merely delayed. The connect-time participant.join is filtered by
            # the same rule, which is why the snapshot isn't followed by a join frame here.
            ws.send_json({"action": "participant.state", "payload": {"muted": True, "quality": "poor"}})
            ws.send_json({"action": "chat.typing", "payload": {"typing": True}})

            nxt = ws.receive_json()
            assert (nxt["channel"], nxt["type"]) == ("chat", "typing"), (
                f"expected the participants envelope to be dropped, got {nxt['channel']}/{nxt['type']}"
            )
    finally:
        live_router._user_from_token, live_router.mod.resolve_ctx, live_router.mod.snapshot = originals


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nAll viewer checks passed.")
