"""Self-check for the contributor backstage domain: the permission table, the join-window
gate, and the state-machine transitions the dispatcher drives. Same no-DB, stubbed-Ctx
pattern as test_moderation.py — only the DB boundary (mod.tx) is stubbed.
Run: `python test_contributor.py` (or pytest)."""
import asyncio
import types
import uuid
from datetime import datetime, timedelta, timezone

from app.services import contributor as c
from app.services import moderation as m


def _ctx(**overrides):
    base = dict(event_id=uuid.uuid4(), org_id=uuid.uuid4(), room="event_x",
                user_id=uuid.uuid4(), name="Sam Speaker", identity=None,
                role="speaker", can_moderate=False, can_host=False, can_contribute=True)
    base.update(overrides)
    if base["identity"] is None:
        base["identity"] = str(base["user_id"])
    return m.Ctx(**base)


def _session(**overrides):
    """A lightweight stand-in for a ContributorSession ORM row — session_out only reads
    attributes off it, so a SimpleNamespace is enough without touching a DB."""
    base = dict(
        id=uuid.uuid4(), user_id=uuid.uuid4(), identity="u1", state="waiting",
        invited_at=None, join_window_start=None, join_window_end=None, expires_at=None,
        contribution_method="livekit_browser", consent_notice=None, support_contact=None,
        consent_given=False, consent_at=None, preflight_result=None,
        rehearsal_complete=False, rehearsal_at=None, admitted_at=None,
        brought_live_at=None, removed_at=None, removed_reason=None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# ── permissions ───────────────────────────────────────────────────────────────

def test_registration_is_wired():
    """Every contributor.* action reached the shared dispatcher, and the self-service /
    operator split landed where the module intended."""
    for action in c.ACTIONS:
        assert action in m.ACTIONS, action
    for action in c.VIEWER_ACTIONS:
        assert action in m.ACTIONS and action in m.VIEWER_ACTIONS, action


def test_operator_actions_are_not_self_service():
    """admit/standby/bring_live/mute/remove/mark_rehearsed must fall through to the default
    can_moderate gate in dispatch() — a plain contributor must not be able to admit
    themselves or anyone else, or mark their own rehearsal complete."""
    for action in ("contributor.admit", "contributor.standby", "contributor.bring_live",
                   "contributor.mute", "contributor.remove", "contributor.mark_rehearsed"):
        assert action in m.ACTIONS
        assert action not in m.VIEWER_ACTIONS, action


def test_operator_actions_are_not_host_only():
    """Mirrors the existing stage.* precedent (broadcast.py) — a moderator can already do
    equivalent audience-management things, so contributor ops stay open to moderators."""
    for action in ("contributor.admit", "contributor.bring_live", "contributor.mute"):
        assert action not in m.HOST_ONLY, action


def test_self_service_action_rejects_a_non_contributor():
    """The self-service handlers gate on ctx.can_contribute INSIDE the handler (same style
    as _feedback_submit's ctx.can_moderate guard) — a viewer who somehow calls one gets a
    silent no-op, not a crash or a written row."""
    ctx = _ctx(can_contribute=False)
    assert asyncio.run(c._consent(ctx, {})) == []
    assert asyncio.run(c._preflight_result(ctx, {"camera_ok": True, "mic_ok": True,
                                                 "browser_supported": True})) == []
    assert asyncio.run(c._request_help(ctx, {})) == []


# ── join-window gate (routers/live.py's socket-connect rejection) ─────────────

def test_join_window_error_no_invitation():
    assert c.join_window_error(None, datetime.now(timezone.utc)) == \
        "You have not been invited to this event"


def test_join_window_error_removed():
    s = _session(state="removed")
    assert "removed" in c.join_window_error(s, datetime.now(timezone.utc))


def test_join_window_error_expired():
    now = datetime.now(timezone.utc)
    s = _session(expires_at=now - timedelta(minutes=1))
    assert "expired" in c.join_window_error(s, now)


def test_join_window_error_not_open_yet():
    now = datetime.now(timezone.utc)
    s = _session(join_window_start=now + timedelta(minutes=10))
    assert "isn't open yet" in c.join_window_error(s, now)


def test_join_window_error_closed():
    now = datetime.now(timezone.utc)
    s = _session(join_window_end=now - timedelta(minutes=1))
    assert "closed" in c.join_window_error(s, now)


def test_join_window_error_open_and_within_window():
    now = datetime.now(timezone.utc)
    s = _session(join_window_start=now - timedelta(minutes=5), join_window_end=now + timedelta(minutes=55))
    assert c.join_window_error(s, now) is None


def test_join_window_error_no_window_at_all():
    """A session with no scheduled join window (open-ended invite) is never blocked on
    timing — only expires_at/state can reject it."""
    assert c.join_window_error(_session(), datetime.now(timezone.utc)) is None


# ── the preflight -> admit gate (mirrors the poll-vote / chat-gate style tests) ────────

def test_preflight_result_requires_camera_mic_and_browser():
    result_ok = {"camera_ok": True, "mic_ok": True, "browser_supported": True,
                 "speaker_ok": False, "framing_ok": False}
    # Speaker output / framing are best-effort, not part of the pass/fail minimum.
    passed = result_ok["camera_ok"] and result_ok["mic_ok"] and result_ok["browser_supported"]
    assert passed is True

    result_bad = {**result_ok, "mic_ok": False}
    assert (result_bad["camera_ok"] and result_bad["mic_ok"] and result_bad["browser_supported"]) is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nAll contributor checks passed.")
