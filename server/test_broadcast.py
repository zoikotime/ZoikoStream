"""Self-check for the host / producer console: the host-only permission gate, chat-control
enforcement, settings validation, and the analytics maths.

Pure logic + the in-process bus — no database and no Redis. The point of most of these is
that a control which *renders* as "off" is actually enforced server-side, and that a
moderator cannot reach broadcast control.
Run: `python test_broadcast.py` (or pytest)."""
import asyncio
import types
import uuid

from app.services import broadcast as bc
from app.services import moderation as m


def _ctx(*, can_moderate=False, can_host=False, role="viewer", event_id=None):
    return m.Ctx(event_id=event_id or uuid.uuid4(), org_id=uuid.uuid4(), room="event_x",
                 user_id=uuid.uuid4(), name="Ava Chen", identity="u1", role=role,
                 can_moderate=can_moderate, can_host=can_host)


# ── the host-only gate ────────────────────────────────────────────────────────

def test_broadcast_control_is_host_only():
    """A moderator runs the audience; they must not be able to end the stream or stop the
    recording. This is the guard that makes that true."""
    moderator = _ctx(can_moderate=True, role="moderator")
    viewer = _ctx()
    for action in ("broadcast.golive", "broadcast.end", "broadcast.emergency_stop",
                   "broadcast.pause", "broadcast.settings", "recording.start", "recording.stop"):
        assert action in m.ACTIONS, f"{action} not registered"
        assert action in m.HOST_ONLY, f"{action} must be host-only"
        assert asyncio.run(m.dispatch(moderator, action, {})), f"moderator reached {action}"
        assert asyncio.run(m.dispatch(viewer, action, {})), f"viewer reached {action}"


def test_stage_controls_stay_open_to_moderators():
    """Stage/mute controls are audience management — moderators already have participant.*
    powers, so gating these to hosts would be a regression, not extra safety."""
    for action in ("stage.mute_all", "stage.admit", "stage.admit_all", "stage.camera", "stage.share"):
        assert action in m.ACTIONS
        assert action not in m.HOST_ONLY
        assert action not in m.VIEWER_ACTIONS   # ...but never open to plain viewers


def test_registration_is_wired():
    assert bc.snapshot_extra in m.SNAPSHOT_EXTRAS
    assert m.ACTIONS["broadcast.golive"] is bc.ACTIONS["broadcast.golive"]


# ── chat control actually enforces ────────────────────────────────────────────
# These hit moderation.chat_gate / slow_mode_error / enabled_flags directly: that IS the
# policy, and testing it here keeps the check DB-free and the rules readable in one place.

VIEWER = _ctx()
SPEAKER = _ctx(role="speaker")
HOST = _ctx(can_moderate=True, can_host=True, role="host")


def test_chat_disabled_blocks_viewers_but_not_staff():
    off = {"chat_enabled": False}
    assert m.chat_gate(off, VIEWER, "hello") == "Chat is turned off"
    # A host talking in their own room is not subject to their own audience controls.
    assert m.chat_gate(off, HOST, "hello") is None


def test_emoji_only_mode():
    on = {"emoji_only": True}
    assert m.chat_gate(on, VIEWER, "great talk") == "Emoji-only mode is on"
    assert m.chat_gate(on, VIEWER, "🎉🎉") is None
    # A multi-codepoint ZWJ family must not be mistaken for text.
    assert m.chat_gate(on, VIEWER, "👨‍👩‍👧") is None
    assert m.chat_gate(on, VIEWER, "🎉 nice") == "Emoji-only mode is on"


def test_subscriber_only_mode():
    on = {"subscriber_only": True}
    assert m.chat_gate(on, VIEWER, "hi") == "Chat is limited to members right now"
    assert m.chat_gate(on, SPEAKER, "hi") is None


def test_no_settings_means_no_restrictions():
    """A fresh event with nothing configured must not accidentally block chat."""
    assert m.chat_gate({}, VIEWER, "hello") is None


def test_slow_mode():
    assert m.slow_mode_error(10, 3.0) == "Slow mode is on — wait 8s"
    assert m.slow_mode_error(10, 10.0) is None      # exactly at the limit is allowed
    assert m.slow_mode_error(0, 0.1) is None        # disabled
    assert m.slow_mode_error(10, None) is None      # no previous message from this author


def test_filter_toggles_are_respected():
    """'Spam filter: off' has to mean it — the detection still runs, it just stops holding
    messages back."""
    flags = ["profanity", "spam", "link", "duplicate"]
    assert m.enabled_flags(flags, {}) == flags                     # default: all on
    assert m.enabled_flags(flags, {"spam_filter": False}) == ["profanity"]
    assert m.enabled_flags(flags, {"profanity_filter": False}) == ["spam", "link", "duplicate"]
    assert m.enabled_flags(flags, {"spam_filter": False, "profanity_filter": False}) == []


def test_empty_message_is_dropped_not_errored():
    assert asyncio.run(m.ACTIONS["chat.send"](VIEWER, {"text": "   "})) == []


# ── settings validation ───────────────────────────────────────────────────────

def test_clean_settings_whitelists_and_clamps():
    out = bc.clean_settings({
        "chat_enabled": 0,              # coerced to bool
        "slow_mode_seconds": "9999",    # clamped to the max
        "framerate": 999,               # clamped
        "mic_gain": -50,                # clamped to the min
        "resolution": "8k",             # not an allowed value -> dropped
        "background": "blur",
        "drop_tables": "yes",           # unknown key -> dropped
    })
    assert out == {"chat_enabled": False, "slow_mode_seconds": 300, "framerate": 60,
                   "mic_gain": 0, "background": "blur"}


def test_clean_settings_ignores_unparseable_numbers():
    assert bc.clean_settings({"framerate": "fast"}) == {}
    assert bc.clean_settings({}) == {}


def test_settings_action_rejects_an_empty_patch():
    host = _ctx(can_host=True, can_moderate=True, role="host")
    assert asyncio.run(m.dispatch(host, "broadcast.settings", {"settings": {"nope": 1}})) \
        == "No recognised settings in that request"


# ── analytics maths ───────────────────────────────────────────────────────────

def test_split_counts_roles_and_excludes_waiting():
    people = [
        {"role": "host", "publishing": True},
        {"role": "moderator"},
        {"role": "speaker", "quality": "poor"},
        {"role": "viewer", "hand": True},
        {"role": "viewer", "quality": "lost"},
        {"role": "viewer", "waiting": True},          # in the lobby, not in the room
    ]
    s = bc._split(people)
    assert s["participants"] == 5 and s["waiting"] == 1
    assert s["viewers"] == 2 and s["speakers"] == 1 and s["moderators"] == 1 and s["hosts"] == 1
    assert s["hands"] == 1 and s["poor_connections"] == 2 and s["publishing"] == 1


def test_engagement_is_bounded_and_safe_at_zero():
    assert bc.engagement_score({"messages": 10}, 0) == 0          # no divide-by-zero
    assert bc.engagement_score({}, 100) == 0
    # Absurd interaction volume saturates at 100 rather than reporting 4000%.
    assert bc.engagement_score({"messages": 99999}, 10) == 100
    assert 0 < bc.engagement_score({"messages": 50, "questions": 10}, 100) < 100


def test_health_levels():
    healthy = {"publishing": 2, "participants": 10, "poor_connections": 0}
    assert bc.health_of(healthy, "live", True)["level"] == "ok"
    # Live with nothing being published is the one case that's genuinely down.
    assert bc.health_of({**healthy, "publishing": 0}, "live", True)["level"] == "down"
    assert bc.health_of(healthy, "paused", True)["level"] == "warn"
    assert "Recording is not being captured" in bc.health_of(healthy, "live", False)["issues"]
    # Not live yet -> no publisher expected, so not an alarm.
    assert bc.health_of({**healthy, "publishing": 0}, "preview", None)["level"] == "ok"


def test_watch_seconds_from_real_join_stamps():
    now = 1_000_000.0
    assert bc._watch_seconds([], now) is None                       # nobody -> null, not 0
    assert bc._watch_seconds([{"joined_at": now - 60}, {"joined_at": now - 120}], now) == 90
    # Someone still in the waiting room hasn't been watching.
    assert bc._watch_seconds([{"joined_at": now - 60, "waiting": True}], now) is None


def test_distribution_buckets_unknowns():
    rows = [{"device": "Desktop"}, {"device": "Desktop"}, {"device": "Mobile"}, {}]
    assert bc._distribution(rows, "device") == [
        {"label": "Desktop", "value": 2}, {"label": "Mobile", "value": 1},
        {"label": "Unknown", "value": 1}]


def test_classify_ua():
    assert bc.classify_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537") == {
        "device": "Desktop", "platform": "Windows", "browser": "Chrome"}
    assert bc.classify_ua("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile Safari/604")["device"] == "Mobile"
    assert bc.classify_ua("Mozilla/5.0 (iPad; CPU OS 17_0) Safari/604")["device"] == "Tablet"
    assert bc.classify_ua("Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile Safari/537")["platform"] == "Android"
    # Edge must not be mistaken for Chrome (its UA contains both).
    assert bc.classify_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537 Edg/120")["browser"] == "Edge"
    assert bc.classify_ua(None) == {"device": "Unknown", "platform": "Unknown", "browser": "Unknown"}


def test_session_out_tolerates_no_session():
    out = bc.session_out(None)
    assert out["status"] == "preview" and out["id"] is None
    assert out["settings"]["chat_enabled"] is True          # defaults are complete
    assert set(bc.DEFAULT_SETTINGS) <= set(out["settings"])


def _fake_event(**overrides):
    base = dict(
        category="Webinar", chat_enabled=True, qa_enabled=True, polls_enabled=True,
        waiting_room_enabled=False, raise_hand_enabled=True, allow_screen_share=True,
        auto_start_recording=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_seed_settings_respects_the_events_own_flags_for_a_normal_event():
    seeded = bc._seed_settings(_fake_event())
    assert seeded["chat_enabled"] is True
    assert seeded["qa_enabled"] is True
    assert seeded["polls_enabled"] is True
    assert seeded["raise_hand_enabled"] is True
    assert seeded["reactions_enabled"] is True  # untouched default for non-memorial


def test_seed_settings_forces_interaction_off_for_memorial_events():
    """doc Sec. 11.3/19, LE-AC-16 (non-waivable): no audience chat/comments/reactions/
    guestbook surface for the memorial launch, even if the row's own columns say True
    (e.g. an event whose category was switched to memorial after creation)."""
    ev = _fake_event(category="Funeral / Memorial", chat_enabled=True, qa_enabled=True,
                     polls_enabled=True, raise_hand_enabled=True)
    seeded = bc._seed_settings(ev)
    assert seeded["chat_enabled"] is False
    assert seeded["qa_enabled"] is False
    assert seeded["polls_enabled"] is False
    assert seeded["raise_hand_enabled"] is False
    assert seeded["reactions_enabled"] is False  # no Event column at all for this one


def test_seed_settings_tolerates_no_event():
    assert bc._seed_settings(None) == dict(bc.DEFAULT_SETTINGS)


def _fake_recording(**overrides):
    """recording_out only reads attributes off the row, so a SimpleNamespace stands in for
    a LiveRecording without a DB — same technique as test_contributor.py's _session."""
    base = dict(
        id=uuid.uuid4(), status="recording", quality="1080p", started_at=None, paused_at=None,
        stopped_at=None, paused_ms=0, size_bytes=None, file_url=None, auto_upload=True,
        enforced=True, error=None, role=None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_recording_out_carries_role():
    """Under dual recording (services/broadcast.py._recording_start), the primary/secondary
    role is what lets the console (and crud.event.list_replay_candidates) tell the two rows
    apart — a plain single-path recording's role stays unset, unchanged from before dual
    recording existed."""
    assert bc.recording_out(_fake_recording(role="primary"))["role"] == "primary"
    assert bc.recording_out(_fake_recording(role="secondary"))["role"] == "secondary"
    assert bc.recording_out(_fake_recording())["role"] is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nAll broadcast checks passed.")
