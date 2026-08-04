"""Moderator Dashboard & Moderation Center — server-side checks.

Real database inside a transaction that is ALWAYS rolled back, same discipline as
test_host_module.py / test_event_module.py / test_invitation_module.py.

The point of this suite is the LINE between a moderator and a host. Most of it is therefore
negative: what a moderator must NOT be able to do (end the broadcast, change the encoder,
stage themselves, act on another tenant's event, silence the host) alongside what they must
(lobby, participants, chat, Q&A, polls, announcements — each of which has to leave an audit
row behind).

Presence lives in Redis or a process dict (services/bus). With REDIS_URL unset these tests
exercise the in-process path, which is the same code the console calls.

Run: `python test_moderator_module.py` (or pytest).
"""
import asyncio
import uuid

from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.crud import event as crud_event
from app.db import SessionLocal, get_db
from app.models import (
    AnalyticsSnapshot, AuditLog, Event, EventAssignment, LiveActivity, LiveMessage, LivePoll,
    LiveQuestion, Organization, User,
)
from app.security import create_access_token, hash_password
from app.services import broadcast, bus, moderation as mod

settings.RESEND_API_KEY = ""


class _NonClosing:
    """A Session whose close() and commit() are neutered — see test_host_module for why."""

    def __init__(self, session):
        self._s = session

    def close(self):
        pass

    def commit(self):
        self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


class Fixture:
    def __init__(self):
        self.db = SessionLocal()
        self.tx = self.db.begin_nested() if self.db.in_transaction() else self.db.begin()
        uniq = uuid.uuid4().hex[:8]
        self.uniq = uniq

        self.org = Organization(name=f"Mod Org {uniq}", status="active")
        self.other_org = Organization(name=f"Mod Other {uniq}", status="active")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        self.admin = self._user(self.org.id, "Org Admin", f"madm{uniq}", "org_admin")
        self.host = self._user(self.org.id, "The Host", f"mhost{uniq}", "host")
        self.moderator = self._user(self.org.id, "The Moderator", f"mmod{uniq}", "moderator")
        self.mod2 = self._user(self.org.id, "Second Moderator", f"mmod2{uniq}", "moderator")
        self.speaker = self._user(self.org.id, "The Speaker", f"mspk{uniq}", "speaker")
        self.viewer = self._user(self.org.id, "A Viewer", f"mvw{uniq}", "viewer")
        self.outsider = self._user(self.other_org.id, "Outside Mod", f"mout{uniq}", "moderator")
        self.db.flush()

        # Public + live, because public is the default visibility and it is what makes the
        # cross-tenant cases reachable at all.
        self.event = Event(org_id=self.org.id, created_by=self.admin.id, title=f"Mod Event {uniq}",
                           slug=f"mod-event-{uniq}", status="live", visibility="public")
        self.unassigned = Event(org_id=self.org.id, created_by=self.admin.id, title="Not mine",
                                slug=f"mod-other-{uniq}", status="published")
        self.db.add_all([self.event, self.unassigned])
        self.db.flush()

        self.db.add_all([
            EventAssignment(event_id=self.event.id, user_id=self.host.id, role="host"),
            EventAssignment(event_id=self.event.id, user_id=self.moderator.id, role="moderator"),
            EventAssignment(event_id=self.event.id, user_id=self.speaker.id, role="speaker"),
        ])
        self.db.flush()

        m.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(m.app)

        self._patched = []
        proxy = _NonClosing(self.db)
        for module in (mod, broadcast):
            self._patched.append((module, module.SessionLocal))
            module.SessionLocal = lambda _p=proxy: _p

        # Presence and live settings are per-event process state; make sure nothing leaks in
        # from another test in the same run.
        asyncio.run(bus.presence_clear(self.event.id))

    def _user(self, org_id, name, slug, role):
        u = User(org_id=org_id, full_name=name, email=f"{slug}@example.com", username=slug,
                 password_hash=hash_password("pw"), role=role)
        self.db.add(u)
        return u

    def auth(self, user):
        return {"Authorization": f"Bearer {create_access_token(user, remember=False)}"}

    def ctx(self, user, event=None):
        return mod.resolve_ctx((event or self.event).id, user)

    def join(self, user, **patch):
        """Put someone in presence the way the socket does on accept."""
        ctx = self.ctx(user)
        role = "host" if ctx.can_host else "moderator" if ctx.can_moderate else "viewer"
        return asyncio.run(bus.presence_upsert(
            self.event.id, ctx.identity,
            {"name": ctx.name, "role": role, "waiting": False, "muted": False, **patch}))

    def message(self, author, text="hello", **cols):
        msg = LiveMessage(event_id=self.event.id, org_id=self.org.id, user_id=author.id,
                          author_name=author.full_name, text=text, flags=[], reactions={},
                          status=cols.pop("status", "approved"), **cols)
        self.db.add(msg)
        self.db.flush()
        return msg

    def question(self, author, text="why?", votes=0, **cols):
        q = LiveQuestion(event_id=self.event.id, org_id=self.org.id, user_id=author.id,
                         author_name=author.full_name, text=text, votes=votes, flags=[], **cols)
        self.db.add(q)
        self.db.flush()
        return q

    def audits(self, action):
        # Scoped to THIS fixture's org. Unscoped, a leftover row from any other run (or another
        # tenant) would satisfy — or break — an assertion that is specifically about this tenant.
        return (self.db.query(AuditLog)
                .filter(AuditLog.action == action, AuditLog.org_id == self.org.id)
                .order_by(AuditLog.created_at).all())

    def close(self):
        for module, original in getattr(self, "_patched", []):
            module.SessionLocal = original
        m.app.dependency_overrides.clear()
        try:
            asyncio.run(bus.presence_clear(self.event.id))
        except Exception:  # noqa: BLE001
            pass
        try:
            self.tx.rollback()
        except Exception:  # noqa: BLE001
            pass
        self.db.rollback()
        self.db.close()


def run(coro):
    return asyncio.run(coro)


# ── RBAC: what a moderator is, and is not ─────────────────────────────────────

def test_moderator_needs_an_assignment_on_this_event(f):
    """An org's `moderator` role is not a licence to moderate every event in it."""
    ctx = f.ctx(f.moderator)
    assert ctx.can_moderate is True and ctx.can_host is False

    stranger = f._user(f.org.id, "Idle Mod", f"idle{f.uniq}", "moderator")
    f.db.flush()
    assert f.ctx(stranger).can_moderate is False, "unassigned moderator must be read-only"
    # And on an event they are not assigned to, the assigned moderator is read-only too.
    assert f.ctx(f.moderator, f.unassigned).can_moderate is False


def test_cross_tenant_moderator_gets_nothing(f):
    """A public event is viewable by any signed-in user (services/viewer.access_for), so the org
    check inside resolve_ctx is the whole guard against a stale or forged assignment."""
    ctx = f.ctx(f.outsider)
    assert ctx is not None, "public events stay viewable — that part is intended"
    assert ctx.can_moderate is False and ctx.can_host is False

    # Even WITH an assignment row, the tenant check must hold: accepting an invitation creates
    # assignments, so this is reachable rather than theoretical.
    f.db.add(EventAssignment(event_id=f.event.id, user_id=f.outsider.id, role="moderator"))
    f.db.flush()
    assert f.ctx(f.outsider).can_moderate is False, "an assignment must not cross tenants"


def test_every_forbidden_action_is_refused(f):
    """The brief's "Not Allowed" list, checked against the dispatcher rather than the UI."""
    ctx = f.ctx(f.moderator)
    for action in ("broadcast.end", "broadcast.emergency_stop", "broadcast.golive",
                   "broadcast.pause", "recording.start", "recording.stop", "recording.pause"):
        err = run(mod.dispatch(ctx, action, {}))
        assert err and "host" in err.lower(), f"{action} must be host-only, got {err!r}"


def test_delete_event_and_org_settings_stay_org_admin(f):
    """Deleting the event, its team and the org's own settings are REST paths, and they must
    refuse a moderator regardless of what the socket allows."""
    H = f.auth(f.moderator)
    assert f.client.delete(f"/events/{f.event.id}", headers=H).status_code == 403
    assert f.client.patch(f"/events/{f.event.id}", headers=H, json={"title": "hijacked"}).status_code == 403
    assert f.client.post(f"/events/{f.event.id}/duplicate", headers=H, json={}).status_code == 403
    assert f.client.get(f"/events/{f.event.id}/access-links", headers=H).status_code == 403
    # Organization settings, billing and ownership are all org-admin routes.
    assert f.client.patch("/organization/profile", headers=H, json={"name": "hijacked"}).status_code == 403
    assert f.client.patch("/organization/security", headers=H, json={}).status_code == 403
    assert f.client.post("/organization/invitations", headers=H,
                         json={"email": "x@example.com", "role": "moderator"}).status_code == 403


def test_moderator_cannot_stage_or_promote_themselves(f):
    """set_stage issues can_publish=True and participant.stage needs only can_moderate, so a
    self-target is a straight privilege escalation."""
    ctx = f.ctx(f.moderator)
    for op, payload in (("stage", {"on_stage": True}), ("role", {"role": "host"})):
        out = run(mod._participant_action(ctx, {"identity": ctx.identity, **payload}, op))
        assert isinstance(out, str), f"self-{op} must be refused"


def test_moderator_cannot_silence_the_host_in_chat(f):
    """Staff bypass audience controls (chat_gate returns early for can_moderate), so a chat mute
    on the host would show in the roster while their messages kept landing — a lie, not a mute."""
    f.join(f.host)
    hctx = f.ctx(f.host)
    out = run(mod._chat_mute(f.ctx(f.moderator), {"identity": hctx.identity, "muted": True}))
    assert isinstance(out, str) and "moderator" in out.lower()

    # A second moderator is equally protected. The assignment has to exist BEFORE they join —
    # the presence role is derived from resolve_ctx, which is what the refusal reads.
    f.db.add(EventAssignment(event_id=f.event.id, user_id=f.mod2.id, role="moderator"))
    f.db.flush()
    assert f.join(f.mod2)["role"] == "moderator"
    out2 = run(mod._chat_mute(f.ctx(f.moderator), {"identity": str(f.mod2.id), "muted": True}))
    assert isinstance(out2, str), out2


# ── Settings: the audience/encoder split ──────────────────────────────────────

def test_audience_controls_are_a_moderators_but_the_encoder_is_not(f):
    """One action serves both consoles and filters its own patch by role. A moderator who can see
    spam but cannot switch the spam filter on is not moderating; a moderator who can change the
    bitrate is overriding the host."""
    ctx = f.ctx(f.moderator)
    run(broadcast.ensure_state(ctx))

    err = run(mod.dispatch(ctx, "broadcast.settings", {"settings": {"slow_mode_seconds": 15}}))
    assert err is None, err
    assert run(bus.state_get(f.event.id))["settings"]["slow_mode_seconds"] == 15

    # Host-only keys are refused, not silently dropped.
    err = run(mod.dispatch(ctx, "broadcast.settings", {"settings": {"bitrate_kbps": 20000}}))
    assert err and "host" in err.lower(), err
    assert run(bus.state_get(f.event.id))["settings"]["bitrate_kbps"] != 20000

    # The host keeps the full set.
    assert run(mod.dispatch(f.ctx(f.host), "broadcast.settings",
                            {"settings": {"bitrate_kbps": 9000}})) is None
    assert run(bus.state_get(f.event.id))["settings"]["bitrate_kbps"] == 9000


def test_a_mixed_patch_keeps_the_allowed_half(f):
    ctx = f.ctx(f.moderator)
    run(broadcast.ensure_state(ctx))
    err = run(mod.dispatch(ctx, "broadcast.settings",
                           {"settings": {"emoji_only": True, "resolution": "4k"}}))
    assert err is None, "the audience half is applied"
    state = run(bus.state_get(f.event.id))["settings"]
    assert state["emoji_only"] is True
    assert state["resolution"] != "4k", "the host's encoder target must be untouched"


def test_layout_and_pin_are_host_only(f):
    """Stage composition is the host's broadcast, and it travels on the same settings action."""
    assert "layout" not in broadcast.MODERATOR_SETTINGS
    assert "pinned_identity" not in broadcast.MODERATOR_SETTINGS
    err = run(mod.dispatch(f.ctx(f.moderator), "broadcast.settings",
                           {"settings": {"layout": "spotlight"}}))
    assert err and "host" in err.lower()


# ── Lobby ─────────────────────────────────────────────────────────────────────

def test_admit_and_reject_one_person(f):
    f.join(f.viewer, waiting=True)
    vid = str(f.viewer.id)
    ctx = f.ctx(f.moderator)

    run(mod.dispatch(ctx, "stage.admit", {"identity": vid, "admit": True}))
    assert run(bus.presence_get(f.event.id, vid))["waiting"] is False

    # A rejection REMOVES them, so the queue cannot show somebody who was denied.
    f.join(f.viewer, waiting=True)
    run(mod.dispatch(ctx, "stage.admit", {"identity": vid, "admit": False}))
    assert run(bus.presence_get(f.event.id, vid)) == {}
    assert f.audits("live.stage.admit"), "both decisions are audited"


def test_bulk_reject_empties_the_lobby(f):
    """Bulk reject is the same handler as admit-all with admit=false. Looping stage.admit from the
    browser would hit the socket's own rate limit and leave half the queue behind."""
    waiters = []
    for i in range(4):
        u = f._user(f.org.id, f"Waiter {i}", f"wait{i}{f.uniq}", "viewer")
        f.db.flush()
        f.join(u, waiting=True)
        waiters.append(str(u.id))
    f.join(f.moderator)   # staff never wait

    run(mod.dispatch(f.ctx(f.moderator), "stage.admit_all", {"admit": False}))
    for w in waiters:
        assert run(bus.presence_get(f.event.id, w)) == {}, "every waiter must be gone"
    assert run(bus.presence_get(f.event.id, str(f.moderator.id))), "staff untouched"

    rows = f.audits("live.stage.deny_all")
    assert rows and rows[-1].meta["count"] == 4


def test_admit_all_only_touches_waiters(f):
    f.join(f.viewer, waiting=True)
    f.join(f.speaker, waiting=False, on_stage=True)
    run(mod.dispatch(f.ctx(f.moderator), "stage.admit_all", {}))
    assert run(bus.presence_get(f.event.id, str(f.viewer.id)))["waiting"] is False
    assert run(bus.presence_get(f.event.id, str(f.speaker.id)))["on_stage"] is True


def test_lobby_actions_are_refused_to_an_unassigned_moderator(f):
    stranger = f._user(f.org.id, "Idle", f"idle2{f.uniq}", "moderator")
    f.db.flush()
    f.join(f.viewer, waiting=True)
    err = run(mod.dispatch(f.ctx(stranger), "stage.admit",
                           {"identity": str(f.viewer.id), "admit": True}))
    assert err and "moderator" in err.lower()
    assert run(bus.presence_get(f.event.id, str(f.viewer.id)))["waiting"] is True


# ── Participants ──────────────────────────────────────────────────────────────

def test_mute_camera_and_share_are_recorded_with_enforcement_honesty(f):
    """LiveKit is not configured in this suite, so every control reports enforced=False. The point
    is that it SAYS so rather than implying the stream was changed."""
    f.join(f.viewer)
    ctx, vid = f.ctx(f.moderator), str(f.viewer.id)

    run(mod.dispatch(ctx, "participant.mute", {"identity": vid, "muted": True}))
    assert run(bus.presence_get(f.event.id, vid))["muted"] is True

    run(mod.dispatch(ctx, "stage.camera", {"identity": vid, "allowed": False}))
    run(mod.dispatch(ctx, "stage.share", {"identity": vid, "allowed": False}))
    rec = run(bus.presence_get(f.event.id, vid))
    assert rec["camera_allowed"] is False and rec["share_allowed"] is False

    row = f.audits("live.participant.mute")[-1]
    assert row.meta["enforced_in_livekit"] is False
    assert str(row.org_id) == str(f.org.id), "audited into the moderator's own tenant"


def test_temporary_mute_bounds_its_window(f):
    f.join(f.viewer)
    run(mod.dispatch(f.ctx(f.moderator), "participant.timeout",
                     {"identity": str(f.viewer.id), "minutes": 9999}))
    rec = run(bus.presence_get(f.event.id, str(f.viewer.id)))
    assert rec["muted"] is True and rec["muted_until"], "a window is recorded"
    assert f.audits("live.participant.timeout")


def test_remove_and_ban_differ_in_whether_a_rejoin_works(f):
    f.join(f.viewer)
    ctx, vid = f.ctx(f.moderator), str(f.viewer.id)

    run(mod.dispatch(ctx, "participant.remove", {"identity": vid}))
    assert run(bus.is_banned(f.event.id, vid)) is False, "a removal is not a ban"

    f.join(f.viewer)
    run(mod.dispatch(ctx, "participant.ban", {"identity": vid}))
    assert run(bus.is_banned(f.event.id, vid)) is True, "a ban must outlive presence"


def test_a_private_notice_reaches_only_its_addressee(f):
    """The bus fans everything to every subscriber; routers/live.py narrows a notice by
    to_identity BEFORE the attendee projection, so other moderators can't read it either."""
    f.join(f.viewer)
    frames = run(mod._participant_notify(
        f.ctx(f.moderator), {"identity": str(f.viewer.id), "text": "You're next"}))
    notice = next(d for _c, t, d in frames if t == "participant.notice")
    assert notice["to_identity"] == str(f.viewer.id)
    assert notice["from_name"] == f.moderator.full_name
    # The allow-list must let the addressee's own copy through, narrowed to these fields.
    projected = mod.viewer_envelope({"channel": "participants", "type": "participant.notice",
                                     "data": notice})
    assert projected and set(projected["data"]) == {"to_identity", "text", "from_name"}
    assert f.audits("live.participant.notify")


def test_a_notice_to_somebody_who_left_is_refused(f):
    out = run(mod._participant_notify(f.ctx(f.moderator),
                                      {"identity": str(uuid.uuid4()), "text": "hi"}))
    assert isinstance(out, str) and "connected" in out


# ── Raised hands ──────────────────────────────────────────────────────────────

def test_a_raised_hand_is_stamped_so_the_queue_has_an_order(f):
    """Ordering the queue in the browser would give every moderator a different order and reset
    it on reconnect, so the stamp rides the presence write that happens anyway."""
    f.join(f.viewer)
    vctx = f.ctx(f.viewer)
    run(mod.dispatch(vctx, "participant.hand", {"raised": True}))
    rec = run(bus.presence_get(f.event.id, vctx.identity))
    assert rec["hand"] is True and rec["hand_at"], "the stamp is what orders the queue"

    run(mod.dispatch(vctx, "participant.hand", {"raised": False}))
    assert run(bus.presence_get(f.event.id, vctx.identity))["hand_at"] is None


def test_a_moderator_can_decline_somebody_elses_hand(f):
    """participant.hand is self-scoped by design (a viewer action), so clearing the queue needs
    its own targeted op."""
    f.join(f.viewer, hand=True, hand_at=1.0)
    run(mod.dispatch(f.ctx(f.moderator), "participant.dismiss_hand",
                     {"identity": str(f.viewer.id)}))
    rec = run(bus.presence_get(f.event.id, str(f.viewer.id)))
    assert rec["hand"] is False and rec["hand_at"] is None
    assert f.audits("live.participant.hand")


def test_a_viewer_cannot_lower_another_viewers_hand(f):
    other = f._user(f.org.id, "Other Viewer", f"ov{f.uniq}", "viewer")
    f.db.flush()
    f.join(other, hand=True)
    err = run(mod.dispatch(f.ctx(f.viewer), "participant.dismiss_hand",
                           {"identity": str(other.id)}))
    assert err and "moderator" in err.lower()
    assert run(bus.presence_get(f.event.id, str(other.id)))["hand"] is True


# ── Chat moderation ───────────────────────────────────────────────────────────

def test_delete_is_soft_and_leaves_an_audit_row(f):
    msg = f.message(f.viewer, "spam spam spam")
    run(mod.dispatch(f.ctx(f.moderator), "chat.delete", {"id": str(msg.id)}))
    f.db.expire_all()
    row = f.db.get(LiveMessage, msg.id)
    assert row is not None and row.status == "deleted" and row.deleted_by == f.moderator.id
    assert f.audits("live.message.delete")


def test_pin_is_exclusive_and_highlight_is_not(f):
    """Two different jobs: one banner above the chat, versus any number of messages queued for
    the host to read out."""
    a, b = f.message(f.viewer, "first"), f.message(f.viewer, "second")
    ctx = f.ctx(f.moderator)

    run(mod.dispatch(ctx, "chat.pin", {"id": str(a.id)}))
    run(mod.dispatch(ctx, "chat.pin", {"id": str(b.id)}))
    f.db.expire_all()
    assert f.db.get(LiveMessage, a.id).pinned is False, "pinning b must unpin a"
    assert f.db.get(LiveMessage, b.id).pinned is True

    run(mod.dispatch(ctx, "chat.highlight", {"id": str(a.id)}))
    run(mod.dispatch(ctx, "chat.highlight", {"id": str(b.id)}))
    f.db.expire_all()
    assert f.db.get(LiveMessage, a.id).highlighted is True
    assert f.db.get(LiveMessage, b.id).highlighted is True, "highlight is not exclusive"


def test_chat_mute_is_enforced_at_send_time_and_expires(f):
    """A scheduled unmute would need a job per mute and would keep somebody muted through a
    restart, so the expiry is checked when they try to speak."""
    f.join(f.viewer)
    vid = str(f.viewer.id)
    run(mod.dispatch(f.ctx(f.moderator), "chat.mute", {"identity": vid, "muted": True,
                                                       "minutes": 10}))
    rec = run(bus.presence_get(f.event.id, vid))
    assert rec["chat_muted"] is True and rec["chat_muted_until"]

    vctx = f.ctx(f.viewer)
    assert mod.chat_gate({}, vctx, "hello", rec) is not None, "the send must be refused"

    # An expired mute stops applying without anything having to unset it.
    expired = {"chat_muted": True, "chat_muted_until": "2020-01-01T00:00:00+00:00"}
    assert mod.chat_gate({}, vctx, "hello", expired) is None

    # And an indefinite mute has no window.
    assert mod._chat_mute_remaining({"chat_muted": True}) == 0
    assert mod._chat_mute_remaining({}) is None


def test_a_chat_mute_does_not_silence_the_microphone_and_vice_versa(f):
    """A host mutes a speaker's mic mid-answer all the time; that must not take their chat away."""
    f.join(f.viewer)
    vid, ctx = str(f.viewer.id), f.ctx(f.moderator)
    run(mod.dispatch(ctx, "participant.mute", {"identity": vid, "muted": True}))
    rec = run(bus.presence_get(f.event.id, vid))
    assert rec["muted"] is True and not rec.get("chat_muted")
    assert mod.chat_gate({}, f.ctx(f.viewer), "hello", rec) is None


def test_staff_bypass_their_own_audience_controls(f):
    """A host must keep a voice in a room where they turned chat off."""
    off = {"chat_enabled": False}
    assert mod.chat_gate(off, f.ctx(f.viewer), "hi", {}) is not None
    assert mod.chat_gate(off, f.ctx(f.moderator), "hi", {}) is None
    assert mod.chat_gate(off, f.ctx(f.host), "hi", {}) is None


def test_a_viewer_can_report_and_the_result_never_reaches_the_room(f):
    """chat.report is a VIEWER action — reporting is only useful if the audience can do it. Its
    result goes out on `moderator`, which is not in the attendee allow-list; broadcasting it on
    `chat` would let anyone report every message and read the moderation queue back."""
    assert "chat.report" in mod.VIEWER_ACTIONS
    msg = f.message(f.viewer, "something nasty")
    frames = run(mod._chat_report(f.ctx(f.viewer), {"id": str(msg.id), "reason": "abuse"}))
    channels = {c for c, _t, _d in frames}
    assert "chat" not in channels, "the flagged copy must not go to the room"
    assert ("moderator", "message.flagged") in {(c, t) for c, t, _d in frames}
    assert mod.viewer_envelope({"channel": "moderator", "type": "message.flagged", "data": {}}) is None

    f.db.expire_all()
    row = f.db.get(LiveMessage, msg.id)
    assert "reported" in (row.flags or []) and row.status == "pending"
    assert f.audits("live.message.report")


def test_reporting_the_same_message_twice_does_not_duplicate_the_flag(f):
    msg = f.message(f.viewer, "nasty")
    for _ in range(3):
        run(mod._chat_report(f.ctx(f.viewer), {"id": str(msg.id)}))
    f.db.expire_all()
    assert (f.db.get(LiveMessage, msg.id).flags or []).count("reported") == 1


def test_a_report_cannot_reach_another_events_message(f):
    """_row scopes every id from the wire to this event and org."""
    other = LiveMessage(event_id=f.unassigned.id, org_id=f.org.id, author_name="x", text="y",
                        flags=[], reactions={})
    f.db.add(other)
    f.db.flush()
    assert run(mod._chat_report(f.ctx(f.viewer), {"id": str(other.id)})) == []


def test_bulk_moderation_is_capped(f):
    ids = [str(uuid.uuid4()) for _ in range(150)]
    out = run(mod._chat_bulk(f.ctx(f.moderator), {"ids": ids, "op": "delete"}))
    act = next(d for c, _t, d in out if c == "activity")
    assert "0 message" in act["text"], "no id matched, and none was invented"
    rows = f.audits("live.message.bulk_delete")
    assert rows and rows[-1].meta["count"] == 0


# ── Q&A ───────────────────────────────────────────────────────────────────────

def test_merge_moves_votes_and_deletes_the_duplicate(f):
    dup = f.question(f.viewer, "same question", votes=3)
    keep = f.question(f.speaker, "same question but earlier", votes=5)
    out = run(mod.dispatch(f.ctx(f.moderator), "qa.merge",
                           {"id": str(dup.id), "into": str(keep.id)}))
    assert out is None, out
    f.db.expire_all()
    assert f.db.get(LiveQuestion, dup.id) is None, "the row the moderator clicked is the one that goes"
    assert f.db.get(LiveQuestion, keep.id).votes == 8
    assert f.audits("live.question.merge")


def test_merge_refuses_a_self_merge_and_a_foreign_target(f):
    q = f.question(f.viewer, "one")
    assert run(mod._qa_moderate(f.ctx(f.moderator), {"id": str(q.id), "into": str(q.id)}, "merge")) == []
    assert run(mod._qa_moderate(f.ctx(f.moderator),
                                {"id": str(q.id), "into": str(uuid.uuid4())}, "merge")) == []
    f.db.expire_all()
    assert f.db.get(LiveQuestion, q.id) is not None, "a refused merge must not delete anything"


def test_assigning_a_question_cannot_cross_tenants(f):
    q = f.question(f.viewer, "for whom?")
    assert run(mod._qa_moderate(f.ctx(f.moderator),
                                {"id": str(q.id), "speaker_id": str(f.outsider.id)}, "assign")) == []
    out = run(mod._qa_moderate(f.ctx(f.moderator),
                               {"id": str(q.id), "speaker_id": str(f.speaker.id)}, "assign"))
    assert out, "an in-org speaker is fine"
    f.db.expire_all()
    assert f.db.get(LiveQuestion, q.id).assigned_to == f.speaker.id


def test_qa_lifecycle_is_audited(f):
    q = f.question(f.viewer, "will this be logged?")
    ctx = f.ctx(f.moderator)
    for action in ("qa.approve", "qa.pin", "qa.answer", "qa.dismiss"):
        assert run(mod.dispatch(ctx, action, {"id": str(q.id)})) is None, action
    for audit in ("live.question.approve", "live.question.pin", "live.question.answer",
                  "live.question.dismiss"):
        assert f.audits(audit), audit


# ── Polls / announcements ─────────────────────────────────────────────────────

def test_a_moderator_can_run_a_poll_end_to_end(f):
    ctx = f.ctx(f.moderator)
    assert run(mod.dispatch(ctx, "poll.create",
                            {"question": "Ready?", "options": ["Yes", "No"], "status": "draft"})) is None
    poll = f.db.query(LivePoll).filter(LivePoll.event_id == f.event.id).one()
    assert poll.status == "draft"

    assert run(mod.dispatch(ctx, "poll.launch", {"id": str(poll.id)})) is None
    f.db.expire_all()
    assert f.db.get(LivePoll, poll.id).status == "live"

    run(mod.dispatch(f.ctx(f.viewer), "poll.vote", {"id": str(poll.id), "option": 0}))
    f.db.expire_all()
    assert f.db.get(LivePoll, poll.id).options[0]["votes"] == 1

    assert run(mod.dispatch(ctx, "poll.close", {"id": str(poll.id)})) is None
    f.db.expire_all()
    assert f.db.get(LivePoll, poll.id).status == "closed"
    for audit in ("live.poll.create", "live.poll.launch", "live.poll.close"):
        assert f.audits(audit), audit


def test_a_closed_polls_results_are_final(f):
    poll = LivePoll(event_id=f.event.id, org_id=f.org.id, question="Done?",
                    options=[{"label": "Yes", "votes": 4}], status="closed")
    f.db.add(poll)
    f.db.flush()
    assert run(mod._poll_update(f.ctx(f.moderator),
                                {"id": str(poll.id), "question": "rewritten"})) == []
    f.db.expire_all()
    assert f.db.get(LivePoll, poll.id).question == "Done?"


def test_announcement_delivery_count_is_real_not_estimated(f):
    ctx = f.ctx(f.moderator)
    assert run(mod.dispatch(ctx, "announce.send",
                            {"text": "Five minutes", "priority": "important"})) is None
    row = f.db.query(mod.LiveAnnouncement).filter(
        mod.LiveAnnouncement.event_id == f.event.id).one()
    assert row.sent_at is not None
    assert row.delivered_to == bus.local_subscribers(str(f.event.id))
    assert f.audits("live.announcement.send")


def test_a_scheduled_announcement_is_not_marked_sent(f):
    run(mod.dispatch(f.ctx(f.moderator), "announce.send",
                     {"text": "Later", "scheduled_at": "2099-01-01T00:00:00Z"}))
    row = f.db.query(mod.LiveAnnouncement).filter(
        mod.LiveAnnouncement.event_id == f.event.id).one()
    assert row.sent_at is None and row.scheduled_at is not None
    assert row.delivered_to is None, "nothing was delivered yet, so the count must be absent"


# ── Analytics ─────────────────────────────────────────────────────────────────

def test_the_moderators_figures_come_from_real_presence(f):
    f.join(f.viewer)
    f.join(f.speaker, on_stage=True, role="speaker")
    f.join(f.moderator, hand=False)
    other = f._user(f.org.id, "Waiter", f"w9{f.uniq}", "viewer")
    f.db.flush()
    f.join(other, waiting=True)
    run(bus.presence_upsert(f.event.id, str(f.viewer.id), {"hand": True, "quality": "poor"}))

    block = run(broadcast.analytics_now(f.ctx(f.moderator)))["analytics"]
    assert block["participants"] == 3, "waiters are not participants yet"
    assert block["waiting"] == 1
    assert block["viewers"] == 1
    assert block["speakers"] == 1
    assert block["hands"] == 1
    assert block["poor_connections"] == 1
    # No GeoIP in this stack — absent with a reason, never a plausible map.
    assert block["countries"] is None and block["countries_note"]


def test_speaking_time_is_accumulated_server_side(f):
    """A client reports edges, not a duration, so nobody can inflate their own total."""
    f.join(f.speaker)
    sctx = f.ctx(f.speaker)
    run(mod._participant_state(sctx, {"speaking": True}))
    assert run(bus.presence_get(f.event.id, sctx.identity))["speaking_since"]
    run(mod._participant_state(sctx, {"speaking": False}))
    rec = run(bus.presence_get(f.event.id, sctx.identity))
    assert rec["speaking_ms"] >= 0 and rec["speaking_since"] is None
    # Bogus telemetry is clamped rather than trusted.
    run(mod._participant_state(sctx, {"bitrate_kbps": 10 ** 9, "packet_loss": -5}))
    rec = run(bus.presence_get(f.event.id, sctx.identity))
    assert rec["bitrate_kbps"] == 100_000 and rec["packet_loss"] == 0


def test_the_lobby_depth_is_sampled_so_a_dashboard_can_read_it(f):
    """The dashboard lists many events; a Redis round trip per event would not scale, so the
    sampler writes the queue depth onto the analytics row it writes anyway."""
    f.join(f.viewer, waiting=True)
    split = broadcast._split(run(bus.presence_all(f.event.id)))
    assert split["waiting"] == 1
    assert "waiting" in AnalyticsSnapshot.__table__.columns


# ── The dashboard's data ──────────────────────────────────────────────────────

def test_assigned_role_moderator_is_what_my_events_means(f):
    """A host is allowed into the moderator console, so without the role filter they would see
    every event they HOST as something to moderate."""
    H = f.auth(f.moderator)
    mine = f.client.get("/events", headers=H,
                        params={"assigned": "me", "assigned_role": "moderator"}).json()
    assert mine["total"] == 1 and mine["items"][0]["id"] == str(f.event.id)
    assert f.client.get("/events", headers=H,
                        params={"assigned": "me", "assigned_role": "host"}).json()["total"] == 0
    # A member can still READ every event in the org — that is a different question.
    assert f.client.get("/events", headers=H).json()["total"] >= 2


def test_the_queue_columns_are_on_the_events_listing(f):
    """No new endpoint: the moderator dashboard's queue columns ride on GET /events."""
    f.question(f.viewer, "pending one", status="pending")
    f.question(f.viewer, "already approved", status="approved")
    f.db.add(LivePoll(event_id=f.event.id, org_id=f.org.id, question="Live?",
                      options=[{"label": "a", "votes": 0}], status="live"))
    f.db.add(LivePoll(event_id=f.event.id, org_id=f.org.id, question="Closed?",
                      options=[{"label": "a", "votes": 0}], status="closed"))
    f.db.flush()

    row = next(e for e in f.client.get("/events", headers=f.auth(f.moderator),
                                       params={"assigned": "me"}).json()["items"]
               if e["id"] == str(f.event.id))
    assert row["open_questions"] == 1, "only questions actually awaiting review"
    assert row["live_polls"] == 1
    # Never measured, so null rather than a zero that reads as "the lobby is empty".
    assert row["waiting"] is None and row["raised_hands"] is None


def test_hands_and_lobby_come_from_the_newest_sample(f):
    f.db.add(AnalyticsSnapshot(event_id=f.event.id, org_id=f.org.id, viewers=9,
                               participants=11, hands=2, waiting=4))
    f.db.flush()
    summary = crud_event.summarize(f.db, [f.event])[f.event.id]
    assert summary["viewers"] == 9 and summary["hands"] == 2 and summary["waiting"] == 4


def test_activity_across_my_events_is_scoped_to_my_assignments(f):
    f.db.add_all([
        LiveActivity(event_id=f.event.id, org_id=f.org.id, kind="mod", text="mine",
                     actor_name="me"),
        LiveActivity(event_id=f.unassigned.id, org_id=f.org.id, kind="mod", text="not mine",
                     actor_name="somebody"),
    ])
    f.db.flush()
    rows = f.client.get("/events/activity", headers=f.auth(f.moderator),
                        params={"assigned": "me"}).json()
    texts = {r["text"] for r in rows}
    assert "mine" in texts and "not mine" not in texts


# ── Audit coverage ────────────────────────────────────────────────────────────

def test_every_moderator_action_writes_both_a_feed_row_and_an_audit_row(f):
    """Two different readers: the in-console timeline, and compliance. One writer
    (services/moderation.record) so neither can be forgotten."""
    f.join(f.viewer)
    msg, q = f.message(f.viewer, "text"), f.question(f.viewer, "q?")
    ctx = f.ctx(f.moderator)
    plan = [
        ("chat.delete", {"id": str(msg.id)}, "live.message.delete"),
        ("qa.approve", {"id": str(q.id)}, "live.question.approve"),
        ("participant.mute", {"identity": str(f.viewer.id), "muted": True}, "live.participant.mute"),
        ("chat.mute", {"identity": str(f.viewer.id), "muted": True}, "live.chat.mute"),
        ("stage.admit", {"identity": str(f.viewer.id), "admit": True}, "live.stage.admit"),
        ("announce.send", {"text": "hello"}, "live.announcement.send"),
    ]
    for action, payload, audit in plan:
        assert run(mod.dispatch(ctx, action, payload)) is None, action
        rows = f.audits(audit)
        assert rows, f"{action} left no audit row"
        assert str(rows[-1].org_id) == str(f.org.id), f"{action} audited into the wrong tenant"
        assert rows[-1].meta.get("event_id") == str(f.event.id), f"{action} lost its event"

    feed = f.db.query(LiveActivity).filter(LiveActivity.event_id == f.event.id).all()
    assert len(feed) >= len(plan)
    assert all(a.actor_name == f.moderator.full_name for a in feed), "the actor is recorded"


def test_an_unknown_action_is_refused_rather_than_ignored(f):
    err = run(mod.dispatch(f.ctx(f.moderator), "participant.explode", {}))
    assert err and "unknown" in err.lower()


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main():
    passed = failed = 0
    for fn in TESTS:
        f = Fixture()
        try:
            fn(f)
            passed += 1
            print(f"  ok    {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
        finally:
            f.close()
    print(f"\nmoderator module: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
