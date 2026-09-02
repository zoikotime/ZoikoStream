"""Self-check for the host / producer console: the host-only permission gate, chat-control
enforcement, settings validation, and the analytics maths.

Pure logic + the in-process bus — no database and no Redis. The point of most of these is
that a control which *renders* as "off" is actually enforced server-side, and that a
connection which can run the audience but is not the assigned host cannot reach broadcast
control.
Run: `python test_broadcast.py` (or pytest)."""
import asyncio
import types
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    AnalyticsSnapshot, BroadcastSession, Event, LiveActivity, LiveRecording, Organization, User,
)
from app.services import broadcast as bc
from app.services import moderation as m


def _ctx(*, can_moderate=False, can_host=False, role="viewer", event_id=None):
    return m.Ctx(event_id=event_id or uuid.uuid4(), org_id=uuid.uuid4(), room="event_x",
                 user_id=uuid.uuid4(), name="Ava Chen", identity="u1", role=role,
                 can_moderate=can_moderate, can_host=can_host)


# ── the host-only gate ────────────────────────────────────────────────────────

def test_broadcast_control_is_host_only():
    """can_moderate is NOT enough to end a stream — only can_host is. Still the right test
    after the moderator role was retired: the audience-vs-broadcast split is now what keeps a
    grandfathered moderator EventAssignment (models/event.LEGACY_ASSIGNMENT_ROLES, which
    resolves to can_moderate with can_host False) away from go-live, end and recording.
    Role label is "host" because presence no longer has a moderator label; the flags, not the
    label, are what the gate reads."""
    audience_only = _ctx(can_moderate=True, can_host=False, role="host")
    viewer = _ctx()
    for action in ("broadcast.golive", "broadcast.end", "broadcast.emergency_stop",
                   "broadcast.pause", "broadcast.settings", "recording.start", "recording.stop"):
        assert action in m.ACTIONS, f"{action} not registered"
        assert action in m.HOST_ONLY, f"{action} must be host-only"
        assert asyncio.run(m.dispatch(audience_only, action, {})), f"non-host reached {action}"
        assert asyncio.run(m.dispatch(viewer, action, {})), f"viewer reached {action}"


def test_stage_controls_stay_open_to_audience_management():
    """Stage/mute controls are audience management — whoever runs the audience already has
    participant.* powers, so gating these to can_host would be a regression, not extra safety.
    It is also what lets a grandfathered moderator assignment keep doing its job."""
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
        # A second staff connection. Presence has no "moderator" label any more — the role was
        # retired and routers/live.py labels every staff connection "host" — so this counts
        # towards `hosts`, and `_split` no longer emits a `moderators` key at all.
        {"role": "host"},
        {"role": "speaker", "quality": "poor"},
        {"role": "viewer", "hand": True},
        {"role": "viewer", "quality": "lost"},
        {"role": "viewer", "waiting": True},          # in the lobby, not in the room
    ]
    s = bc._split(people)
    assert s["participants"] == 5 and s["waiting"] == 1
    assert s["viewers"] == 2 and s["speakers"] == 1 and s["hosts"] == 2
    assert "moderators" not in s, "the retired role must not linger as a permanently-zero counter"
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


def _preview_status(existing_status):
    """Runs _preview against a fresh in-process bus state seeded with `existing_status`, and
    reports (status it published, status it left in the shared bus state).

    Blanks REDIS_URL rather than clearing bus._redis: bus.redis() short-circuits to the
    in-process dict when the URL is unset, so nothing new is connected. Clearing the cached
    client instead makes the NEXT redis() build one bound to this asyncio.run()'s loop, which
    then dies with it and fails an unrelated later test in the same process with
    "Event loop is closed". LiveKit is stubbed for the same reason a unit test shouldn't need
    a network: _preview's room reservation is not what this asserts."""
    from app.services import bus
    from app.services import livekit as lk

    ctx = _ctx(can_host=True, role="org_admin")
    url, ensure = bus.settings.REDIS_URL, lk.ensure_room

    async def _stub_ensure_room(room, empty_timeout=600):
        return True

    bus.settings.REDIS_URL = ""
    lk.ensure_room = _stub_ensure_room
    try:
        async def run():
            if existing_status:
                await bus.state_set(ctx.event_id, {"status": existing_status})
            frames = await bc._preview(ctx, {})
            return frames[0][2]["status"], (await bus.state_get(ctx.event_id)).get("status")

        return asyncio.run(run())
    finally:
        bus.settings.REDIS_URL = url
        lk.ensure_room = ensure


def test_preview_never_demotes_a_broadcast_that_is_on_air():
    """THE BUG: _preview wrote status="preview" unconditionally — into the SHARED bus state
    and out to every console on the event. A host who armed the preview mid-broadcast knocked
    their own live broadcast back to "preview" everywhere; on the client that flips `live`
    false, which is exactly what hooks/useLiveKitPublish.js gates `enabled` on, so the
    publisher tore itself down and viewers lost audio and video while the host still looked
    live. Reserving the room and minting the token are safe while live; only the status
    write ever needed to be conditional."""
    for on_air in ("live", "paused"):
        published, stored = _preview_status(on_air)
        assert published == on_air, f"preview published {published!r} over a {on_air} broadcast"
        assert stored == on_air, f"preview stored {stored!r} over a {on_air} broadcast"


def test_preview_still_marks_an_idle_event_as_previewing():
    for idle in (None, "preview", "ended"):
        published, stored = _preview_status(idle)
        assert published == "preview"
        assert stored == "preview"


def _fake_event(**overrides):
    base = dict(
        category="Webinar", chat_enabled=True, qa_enabled=True, polls_enabled=True,
        waiting_room_enabled=False, raise_hand_enabled=True, allow_screen_share=True,
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


def test_seed_settings_auto_upload_uses_the_plain_default():
    """Audit fix: auto_upload (upload-on-stop) used to be silently driven by
    Event.auto_start_recording — a different setting (whether recording auto-STARTS, which
    it never did — see ACTIONS/_recording_start, always a deliberate host action) that has
    since been removed from the Event model/schema entirely as dead, unexposed config.
    auto_upload now always seeds from the plain default, independent of anything on Event."""
    assert bc._seed_settings(_fake_event())["auto_upload"] == bc.DEFAULT_SETTINGS["auto_upload"]


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


# ── mark_degraded / mark_recovered / session-scoped recordings (real DB) ───────
# Same real-DB-with-cleanup pattern as test_registration.py: these write actual rows through
# app.db.SessionLocal (the functions under test use mod.tx internally, a separate connection
# from this test's own `db`, so setup/teardown commit explicitly rather than relying on a
# rollback — a rollback here would not undo what the other connection already committed).

NOW = datetime.now(timezone.utc)


def _db_org(db):
    o = Organization(name=f"org-test-{uuid.uuid4().hex[:8]}", status="active")
    db.add(o)
    db.flush()
    return o


def _db_user(db, org):
    u = User(
        org_id=org.id, full_name="Test Owner", role="org_admin", is_active=True,
        email=f"t{uuid.uuid4().hex[:10]}@example.com",
        username=f"u{uuid.uuid4().hex[:10]}", password_hash="x",
    )
    db.add(u)
    db.flush()
    return u


def _db_event(db, org, creator, status="live", **kw):
    e = Event(org_id=org.id, created_by=creator.id, title="Broadcast Test Event",
              status=status, start_time=NOW - timedelta(minutes=5), visibility="public", **kw)
    db.add(e)
    db.flush()
    return e


def _db_cleanup(db, org, user, ev, extra_rows=()):
    # mark_degraded/mark_recovered/_golive etc. write LiveActivity rows through their own
    # (mod.tx-opened) connection — this session must re-query for them rather than assume
    # nothing landed, same reasoning test_registration.py's _cleanup gives for committing
    # its own setup before the endpoint under test runs.
    db.query(LiveActivity).filter(LiveActivity.event_id == ev.id).delete()
    # A real dev server's ticker (services/broadcast.py::run_sampler) may be running against
    # this same database and can sample a test event that briefly went "live" before this
    # cleanup runs, writing an AnalyticsSnapshot that would otherwise FK-block deleting the
    # event.
    db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.event_id == ev.id).delete()
    for row in extra_rows:
        db.delete(row)
    db.delete(ev)
    db.delete(user)
    db.delete(org)
    db.commit()


def test_mark_degraded_and_recovered_round_trip():
    """The core of the audit's Critical #1/#2 fix: a producer disconnect now has to survive
    as a real Postgres value, not just an ephemeral bus event. Also proves the no-op guards —
    mark_degraded only fires from "live", mark_recovered only from "degraded" — so calling
    either on every sampler tick (services/broadcast.py's extended _sample_once) can never
    stomp on an event a host has since ended."""
    db = SessionLocal()
    org = _db_org(db)
    user = _db_user(db, org)
    ev = _db_event(db, org, user, status="live")
    db.commit()
    try:
        assert asyncio.run(bc.mark_degraded(str(ev.id), str(org.id), "No media is being published")) is True
        db.expire_all()
        assert db.get(Event, ev.id).status == "degraded"

        # No-op: already degraded, not "live".
        assert asyncio.run(bc.mark_degraded(str(ev.id), str(org.id), "again")) is False

        assert asyncio.run(bc.mark_recovered(str(ev.id), str(org.id))) is True
        db.expire_all()
        assert db.get(Event, ev.id).status == "live"

        # No-op: already live, not "degraded".
        assert asyncio.run(bc.mark_recovered(str(ev.id), str(org.id))) is False
    finally:
        _db_cleanup(db, org, user, ev)
        db.close()


def test_mark_degraded_does_not_touch_an_ended_event():
    """Required behavior #7 from the audit: 'avoid incorrectly marking a normally ended
    event as degraded'. mark_degraded's guard (status must be "live") makes this structural,
    not timing-dependent — even if a stale sampler tick for an already-ended event's session
    runs after End, it can't regress the event backward out of "ended"."""
    db = SessionLocal()
    org = _db_org(db)
    user = _db_user(db, org)
    ev = _db_event(db, org, user, status="ended")
    db.commit()
    try:
        assert asyncio.run(bc.mark_degraded(str(ev.id), str(org.id), "stale tick")) is False
        db.expire_all()
        assert db.get(Event, ev.id).status == "ended"
    finally:
        _db_cleanup(db, org, user, ev)
        db.close()


def test_current_recordings_ignores_orphan_from_an_ended_session():
    """Audit finding: _current_recordings used to be scoped by event_id alone, so a recording
    row orphaned by a crash in an earlier (now-ended) BroadcastSession could still be reported
    "active" once a later, unrelated Go Live opened a new session for the same event. Scoping
    by the CURRENT session's id (see _current_recordings) is what this proves."""
    db = SessionLocal()
    org = _db_org(db)
    user = _db_user(db, org)
    ev = _db_event(db, org, user)
    old_session = BroadcastSession(event_id=ev.id, org_id=org.id, status="ended",
                                   started_at=NOW - timedelta(hours=1), ended_at=NOW - timedelta(minutes=30))
    new_session = BroadcastSession(event_id=ev.id, org_id=org.id, status="live", started_at=NOW)
    db.add_all([old_session, new_session])
    db.flush()
    orphan = LiveRecording(event_id=ev.id, org_id=org.id, session_id=old_session.id,
                           status="recording", started_at=NOW - timedelta(minutes=45))
    db.add(orphan)
    db.commit()
    try:
        ctx = m.Ctx(event_id=ev.id, org_id=org.id, room=f"event_{ev.id}", user_id=user.id,
                   name="Test Host", identity=str(user.id), role="host",
                   can_moderate=True, can_host=True)
        active = bc._current_recordings(db, ctx)
        assert active == [], "an orphaned recording from an ENDED session must not read as active"
    finally:
        _db_cleanup(db, org, user, ev, extra_rows=[orphan, old_session, new_session])
        db.close()


def test_concurrent_golive_creates_exactly_one_open_session():
    """The actual race, not just the index in isolation: two concurrent Go Live requests for
    the same event must resolve to one open BroadcastSession. Exercises the real _golive
    (commercial gate, LiveKit ensure_room, the INSERT, and the IntegrityError retry) — a
    fabricated DB-locking test wouldn't prove the retry path in _golive.py:_golive itself
    actually gets hit and actually recovers cleanly."""
    db = SessionLocal()
    org = _db_org(db)
    user = _db_user(db, org)
    ev = _db_event(db, org, user, status="published")
    db.commit()
    try:
        ctx = m.Ctx(event_id=ev.id, org_id=org.id, room=f"event_{ev.id}", user_id=user.id,
                   name="Test Host", identity=str(user.id), role="host",
                   can_moderate=True, can_host=True)

        async def race():
            return await asyncio.gather(bc._golive(ctx, {}), bc._golive(ctx, {}))

        results = asyncio.run(race())
        # Neither request may raise/surface the IntegrityError to the caller — both must
        # resolve to a normal frame list.
        for r in results:
            assert isinstance(r, list), f"a concurrent go-live leaked an error instead of resolving: {r!r}"

        db.expire_all()
        open_sessions = db.scalars(
            select(BroadcastSession).where(BroadcastSession.event_id == ev.id,
                                           BroadcastSession.ended_at.is_(None))
        ).all()
        assert len(open_sessions) == 1, \
            f"expected exactly one open BroadcastSession, found {len(open_sessions)}"
        assert db.get(Event, ev.id).status == "live"
    finally:
        db.expire_all()
        sessions = db.scalars(select(BroadcastSession).where(BroadcastSession.event_id == ev.id)).all()
        _db_cleanup(db, org, user, ev, extra_rows=sessions)
        db.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nAll broadcast checks passed.")
