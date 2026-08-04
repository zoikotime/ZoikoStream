"""Host Dashboard & Live Control Room — server-side checks.

Real database inside a transaction that is ALWAYS rolled back, same discipline as
test_event_module.py / test_invitation_module.py. The publisher itself is browser code and
cannot be exercised here; what IS testable server-side is everything it depends on — who may
obtain a publish grant, whether the layout/pin settings validate, whether speaking time
accumulates, whether the egress webhook records a real file, and whether "my events" means
assigned rather than "every event in the org".

Run: `python test_host_module.py` (or pytest).
"""
import asyncio
import uuid
from types import SimpleNamespace

from sqlalchemy import text
from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.db import SessionLocal, get_db
from app.models import Event, EventAssignment, LiveActivity, LiveRecording, Organization, User
from app.security import create_access_token, hash_password
from app.services import broadcast, moderation as mod

settings.RESEND_API_KEY = ""


class _NonClosing:
    """A Session whose close() and commit() are neutered.

    The code under test opens and closes its own session and commits freely; both would end the
    transaction this suite rolls back. Everything else passes straight through, so the queries
    being tested are the real ones. flush() stands in for commit() so later reads in the same
    transaction see the writes.
    """

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

        self.org = Organization(name=f"Host Org {uniq}", status="active")
        self.other_org = Organization(name=f"Other Org {uniq}", status="active")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        self.admin = self._user(self.org.id, "Host Admin", f"hadm{uniq}", "org_admin")
        self.host = self._user(self.org.id, "The Host", f"hhost{uniq}", "host")
        self.moderator = self._user(self.org.id, "The Moderator", f"hmod{uniq}", "moderator")
        self.speaker = self._user(self.org.id, "The Speaker", f"hspk{uniq}", "speaker")
        # An org_admin of a DIFFERENT tenant — the cross-tenant takeover case.
        self.outsider = self._user(self.other_org.id, "Outsider Admin", f"hout{uniq}", "org_admin")
        self.db.flush()

        # A public, live event: public visibility is the default, and it is what made the
        # cross-tenant hole reachable.
        self.event = Event(org_id=self.org.id, created_by=self.admin.id, title=f"Host Event {uniq}",
                           slug=f"host-event-{uniq}", status="live", visibility="public")
        self.other_event = Event(org_id=self.org.id, created_by=self.admin.id, title="Unassigned",
                                 slug=f"unassigned-{uniq}", status="published")
        self.db.add_all([self.event, self.other_event])
        self.db.flush()

        self.db.add_all([
            EventAssignment(event_id=self.event.id, user_id=self.host.id, role="host"),
            EventAssignment(event_id=self.event.id, user_id=self.moderator.id, role="moderator"),
            EventAssignment(event_id=self.event.id, user_id=self.speaker.id, role="speaker"),
        ])
        self.db.flush()

        m.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(m.app)

        # services/moderation.resolve_ctx and services/broadcast open their OWN sessions (they
        # run off the socket, not a request), so they would not see this fixture's uncommitted
        # rows. Point their session factory at ours, with close() neutered — the code under test
        # closes the session it opens, and that must not end the transaction we roll back.
        self._patched = []
        proxy = _NonClosing(self.db)
        for module in (mod, broadcast):
            self._patched.append((module, module.SessionLocal))
            module.SessionLocal = lambda _p=proxy: _p

    def _user(self, org_id, name, slug, role):
        u = User(org_id=org_id, full_name=name, email=f"{slug}@example.com", username=slug,
                 password_hash=hash_password("pw"), role=role)
        self.db.add(u)
        return u

    def auth(self, user):
        return {"Authorization": f"Bearer {create_access_token(user, remember=False)}"}

    def close(self):
        for module, original in getattr(self, "_patched", []):
            module.SessionLocal = original
        m.app.dependency_overrides.clear()
        try:
            self.tx.rollback()
        except Exception:  # noqa: BLE001
            pass
        self.db.rollback()
        self.db.close()


def _ctx(f, user, event=None):
    return mod.resolve_ctx((event or f.event).id, user)


# ── Host permissions / RBAC ───────────────────────────────────────────────────

def test_only_an_assigned_host_gets_broadcast_control(f):
    """can_host is what gates every HOST_ONLY action AND the publish token. An org's `host` role
    is not enough — the assignment on THIS event is the grant."""
    assert _ctx(f, f.host).can_host is True
    # A moderator runs the audience; they must never be able to end the stream.
    mctx = _ctx(f, f.moderator)
    assert mctx.can_moderate is True and mctx.can_host is False
    # A speaker is on stage, not in charge.
    sctx = _ctx(f, f.speaker)
    assert sctx.can_host is False

    unassigned = f._user(f.org.id, "Other Host", f"oth{f.uniq}", "host")
    f.db.flush()
    assert _ctx(f, unassigned).can_host is False, "an org host is not a host of every event"


def test_cross_tenant_org_admin_cannot_host_a_public_event(f):
    """The regression that matters most in this module. Public visibility is the DEFAULT, and
    services/viewer.access_for admits any signed-in user to a public event — so before the fix
    an org_admin of any other tenant resolved with can_host=True, which is a publish token plus
    broadcast.emergency_stop on somebody else's live event, audited into the wrong org."""
    ctx = _ctx(f, f.outsider)
    assert ctx is not None, "a public event is still viewable — that part is intended"
    assert ctx.can_host is False, "cross-tenant org_admin must NOT get broadcast control"
    assert ctx.can_moderate is False, "nor moderation"

    # The org's OWN admin still runs their own events.
    assert _ctx(f, f.admin).can_host is True


def test_private_event_refuses_an_outsider_outright(f):
    priv = Event(org_id=f.org.id, created_by=f.admin.id, title="Private", status="live",
                 slug=f"priv-{f.uniq}", visibility="private")
    f.db.add(priv)
    f.db.flush()
    assert _ctx(f, f.outsider, priv) is None, "the socket must be refused, not merely limited"


def test_publish_token_only_for_a_host_and_never_to_a_viewer(f):
    """The token is minted in snapshot_extra gated on can_host, and stripped from the attendee
    projection by an allow-list."""
    host_snap = asyncio.run(broadcast.snapshot_extra(_ctx(f, f.host)))
    assert host_snap["publish_token"], "an assigned host must get a publish grant"
    assert host_snap["publish_identity"].endswith("#host")

    mod_snap = asyncio.run(broadcast.snapshot_extra(_ctx(f, f.moderator)))
    assert mod_snap["publish_token"] is None, "a moderator must not get a publish grant"

    # And the attendee projection drops it even if it were present.
    projected = mod.viewer_snapshot({**host_snap, "event": {}, "can_host": True})
    assert "publish_token" not in projected
    assert projected["can_host"] is False


def test_publisher_identity_is_distinct_from_the_attendee_one(f):
    """LiveKit allows one connection per identity per room and drops the older one. The attendee
    playback token uses the bare user id, so a host with the watch page open in another tab would
    have kicked their own broadcast off air."""
    ctx = _ctx(f, f.host)
    pub = broadcast.publisher_identity(ctx.identity)
    assert pub != ctx.identity
    assert broadcast.base_identity(pub) == ctx.identity, "presence must key on the BARE id"
    assert broadcast.base_identity(ctx.identity) == ctx.identity


def test_preview_never_broadcasts_a_publish_token(f):
    """Whatever a handler returns is fanned to EVERY subscriber by mod.dispatch, and the feed is
    narrowed only for attendees — so a moderator would have received the host's credential."""
    frames = asyncio.run(broadcast._preview(_ctx(f, f.host), {}))
    for _channel, _type, data in frames:
        assert "publish_token" not in data, "a publish token must never ride a broadcast frame"


def test_broadcast_control_is_host_only(f):
    """Every broadcast.* and recording.* action is gated on can_host, so a moderator cannot end
    the stream or stop the capture."""
    assert broadcast.HOST_ONLY, "the host-only set must not be empty"
    for action in ("broadcast.golive", "broadcast.end", "broadcast.emergency_stop",
                   "recording.start", "recording.stop"):
        assert action in broadcast.HOST_ONLY, action
    # Stage controls stay available to moderators — that is audience management.
    for action in ("stage.mute_all", "stage.admit"):
        assert action not in broadcast.HOST_ONLY, action

    err = asyncio.run(mod.dispatch(_ctx(f, f.moderator), "broadcast.end", {}))
    assert err and "host" in err.lower(), err


def test_a_moderator_cannot_stage_or_promote_themselves(f):
    """Staging somebody is a moderator's job; staging YOURSELF grants publish. set_stage issues
    can_publish=True, and participant.stage needs only can_moderate."""
    ctx = _ctx(f, f.moderator)
    for op in ("stage", "role"):
        out = asyncio.run(mod._participant_action(ctx, {"identity": ctx.identity, "on_stage": True,
                                                        "role": "host"}, op))
        assert isinstance(out, str), f"self-{op} must be refused"


# ── Assigned events (the host dashboard's data) ───────────────────────────────

def test_assigned_me_returns_only_my_events(f):
    H = f.auth(f.host)
    all_events = f.client.get("/events", headers=H).json()
    mine = f.client.get("/events", headers=H, params={"assigned": "me"}).json()
    assert all_events["total"] >= 2, "a member can READ every event in the org"
    assert mine["total"] == 1, "but 'my events' means assigned"
    assert mine["items"][0]["id"] == str(f.event.id)


def test_several_roles_on_one_event_do_not_duplicate_it(f):
    """An EXISTS subquery, not a join: a person can be host AND speaker on one event, and a join
    would return it twice, corrupting both the page and the total."""
    f.db.add(EventAssignment(event_id=f.event.id, user_id=f.host.id, role="speaker"))
    f.db.flush()
    mine = f.client.get("/events", headers=f.auth(f.host), params={"assigned": "me"}).json()
    assert mine["total"] == 1 and len(mine["items"]) == 1


def test_assigned_role_narrows_and_validates(f):
    H = f.auth(f.host)
    assert f.client.get("/events", headers=H,
                        params={"assigned": "me", "assigned_role": "host"}).json()["total"] == 1
    assert f.client.get("/events", headers=H,
                        params={"assigned": "me", "assigned_role": "moderator"}).json()["total"] == 0
    assert f.client.get("/events", headers=H,
                        params={"assigned": "me", "assigned_role": "wizard"}).status_code == 422
    # Only "me" — an arbitrary user id would let any member enumerate someone else's workload.
    assert f.client.get("/events", headers=H, params={"assigned": str(f.admin.id)}).status_code == 422


def test_assigned_is_still_org_scoped(f):
    """An outsider assigned to nothing sees nothing, and cannot see this org's events at all."""
    ids = {e["id"] for e in f.client.get("/events", headers=f.auth(f.outsider)).json()["items"]}
    assert str(f.event.id) not in ids


def test_activity_feed_is_scoped_to_my_events(f):
    f.db.add_all([
        LiveActivity(event_id=f.event.id, org_id=f.org.id, kind="system", text="mine happened"),
        LiveActivity(event_id=f.other_event.id, org_id=f.org.id, kind="system", text="theirs happened"),
    ])
    f.db.flush()
    mine = f.client.get("/events/activity", headers=f.auth(f.host), params={"assigned": "me"}).json()
    assert [a["text"] for a in mine] == ["mine happened"]
    assert mine[0]["event_title"] == f.event.title, "the feed names the event it came from"
    # An admin sees the whole org; an outsider sees nothing.
    assert len(f.client.get("/events/activity", headers=f.auth(f.admin)).json()) == 2
    assert f.client.get("/events/activity", headers=f.auth(f.outsider)).json() == []
    assert f.client.get("/events/activity", headers=f.auth(f.host),
                        params={"assigned": "someone"}).status_code == 422


# ── Layout / pin as validated broadcast settings ──────────────────────────────

def test_layout_and_pin_validate_as_settings(f):
    """Layout lives in the broadcast settings so it persists on the session and broadcasts to
    every console — no new table, no new endpoint, and the existing whitelist validates it."""
    assert broadcast.clean_settings({"layout": "spotlight"}) == {"layout": "spotlight"}
    assert broadcast.clean_settings({"layout": "hologram"}) == {}, "unknown layout must be dropped"
    for layout in broadcast.LAYOUTS:
        assert broadcast.clean_settings({"layout": layout})["layout"] == layout

    # pinned_identity is free text, so it is bounded and normalized rather than trusted.
    assert broadcast.clean_settings({"pinned_identity": "  abc  "}) == {"pinned_identity": "abc"}
    assert broadcast.clean_settings({"pinned_identity": ""}) == {"pinned_identity": ""}
    assert len(broadcast.clean_settings({"pinned_identity": "x" * 500})["pinned_identity"]) == 128
    # The whitelist still drops anything unknown.
    assert broadcast.clean_settings({"evil": "x", "layout": "grid"}) == {"layout": "grid"}


def test_layout_defaults_exist_so_a_console_never_renders_undefined(f):
    assert broadcast.DEFAULT_SETTINGS["layout"] == "grid"
    assert broadcast.DEFAULT_SETTINGS["pinned_identity"] == ""


# ── Publisher telemetry ingest ────────────────────────────────────────────────

def test_publisher_telemetry_is_clamped(f):
    """Bitrate, loss, RTT and fps come from the browser's peer connection — the only place they
    exist — so they arrive over participant.state and must be bounded, not trusted."""
    ctx = _ctx(f, f.host)
    asyncio.run(mod._participant_state(ctx, {
        "bitrate_kbps": 10 ** 9, "packet_loss": 5000, "rtt_ms": -20, "fps": 9999,
    }))
    rec = asyncio.run(broadcast.bus.presence_get(ctx.event_id, ctx.identity))
    assert rec["bitrate_kbps"] == 100_000
    assert rec["packet_loss"] == 100
    assert rec["rtt_ms"] == 0
    assert rec["fps"] == 240
    # Garbage is dropped rather than stored.
    asyncio.run(mod._participant_state(ctx, {"bitrate_kbps": "banana"}))
    rec = asyncio.run(broadcast.bus.presence_get(ctx.event_id, ctx.identity))
    assert rec["bitrate_kbps"] == 100_000, "an unparseable value must not overwrite a good one"


def test_speaking_time_accumulates_on_the_falling_edge(f):
    """The publisher reports EDGES, not durations, so the server owns the arithmetic and a client
    cannot inflate its own total."""
    ctx = _ctx(f, f.host)
    asyncio.run(mod._participant_state(ctx, {"speaking": True}))
    rec = asyncio.run(broadcast.bus.presence_get(ctx.event_id, ctx.identity))
    assert rec["speaking"] is True and rec["speaking_since"], "the start must be stamped"
    assert not rec.get("speaking_ms"), "nothing accrues until they stop"

    # Backdate the start so the elapsed window is deterministic.
    asyncio.run(broadcast.bus.presence_upsert(
        ctx.event_id, ctx.identity, {"speaking_since": rec["speaking_since"] - 5}))
    asyncio.run(mod._participant_state(ctx, {"speaking": False}))
    rec = asyncio.run(broadcast.bus.presence_get(ctx.event_id, ctx.identity))
    assert rec["speaking"] is False
    assert 4500 <= rec["speaking_ms"] <= 6000, rec["speaking_ms"]
    assert rec["speaking_since"] is None

    # A second stretch ADDS rather than replaces.
    asyncio.run(mod._participant_state(ctx, {"speaking": True}))
    r2 = asyncio.run(broadcast.bus.presence_get(ctx.event_id, ctx.identity))
    asyncio.run(broadcast.bus.presence_upsert(
        ctx.event_id, ctx.identity, {"speaking_since": r2["speaking_since"] - 3}))
    asyncio.run(mod._participant_state(ctx, {"speaking": False}))
    rec = asyncio.run(broadcast.bus.presence_get(ctx.event_id, ctx.identity))
    assert rec["speaking_ms"] > 7000, "the second stretch must add to the first"


def test_speaking_leaderboard_counts_the_stretch_in_progress(f):
    """Someone still talking would otherwise appear frozen at their previous total."""
    now = broadcast.datetime.now(broadcast.timezone.utc).timestamp()
    rows = broadcast._speaking_leaderboard([
        {"identity": "a", "name": "Ann", "speaking_ms": 10_000},
        {"identity": "b", "name": "Bob", "speaking_ms": 1_000, "speaking": True,
         "speaking_since": now - 30},
        {"identity": "c", "name": "Cal"},                       # never spoke -> omitted
        {"identity": "d", "name": "Dee", "speaking_ms": 99_000, "waiting": True},  # in the lobby
    ])
    names = [r["name"] for r in rows]
    assert names == ["Bob", "Ann"], names
    assert rows[0]["seconds"] >= 30
    assert "Cal" not in names and "Dee" not in names


def test_publisher_telemetry_aggregate_is_absent_when_nobody_publishes(f):
    """A zero would read as a measurement; absent is the honest answer."""
    empty = broadcast._publisher_telemetry([{"identity": "a", "publishing": False}])
    assert empty["publishers"] == 0 and empty["bitrate_kbps"] is None and empty["rtt_ms"] is None
    live = broadcast._publisher_telemetry([
        {"identity": "a", "publishing": True, "bitrate_kbps": 2000, "rtt_ms": 40, "packet_loss": 1},
        {"identity": "b", "publishing": True, "bitrate_kbps": 1000, "rtt_ms": 60, "packet_loss": 3},
    ])
    assert live["publishers"] == 2
    assert live["bitrate_kbps"] == 3000, "bitrate is the SUM — it is what the room is sending"
    assert live["rtt_ms"] == 50 and live["packet_loss"] == 2, "latency/loss are averages"


# ── Recording ─────────────────────────────────────────────────────────────────

def test_recording_start_claims_the_slot_before_spending(f):
    """A double-clicked Record button used to start TWO egresses, and only the newest row was ever
    stopped — the first kept capturing with nothing pointing at it."""
    ctx = _ctx(f, f.host)
    first = asyncio.run(broadcast._recording_start(ctx, {}))
    assert isinstance(first, list), first
    second = asyncio.run(broadcast._recording_start(ctx, {}))
    assert isinstance(second, str) and "already running" in second
    rows = f.db.execute(
        text("select count(*) from live_recordings where event_id = :e"), {"e": f.event.id}
    ).scalar()
    assert rows == 1, "the second click must not create a second recording"


def test_egress_result_records_the_real_file(f):
    """egress_ended is the ONLY place the file's size and location exist; they were being dropped,
    so size_bytes stayed NULL and the console could never say a capture actually happened."""
    rec = LiveRecording(event_id=f.event.id, org_id=f.org.id, status="recording",
                        egress_id=f"eg-{f.uniq}", enforced=False)
    f.db.add(rec)
    f.db.flush()

    # A size past the old INTEGER ceiling (2 GB), which a 1080p multi-hour capture passes.
    big = 5_000_000_000
    info = SimpleNamespace(
        egress_id=f"eg-{f.uniq}", error=None,
        file_results=[SimpleNamespace(size=big, location="s3://bucket/final.mp4", filename=None)],
    )
    out = broadcast.record_egress_result(info)
    assert out is not None, "the row must be found by its egress id"
    f.db.expire_all()
    row = f.db.get(LiveRecording, rec.id)
    assert row.size_bytes == big, "BigInteger — an INTEGER column would overflow here"
    assert row.file_url == "s3://bucket/final.mp4"
    assert row.enforced is True, "bytes on disk are the proof that it was captured"
    assert row.status == "stopped"

    # An unknown egress id matches nothing — a replayed or foreign webhook writes nowhere.
    assert broadcast.record_egress_result(SimpleNamespace(egress_id="nope", error=None,
                                                          file_results=[])) is None
    assert broadcast.record_egress_result(SimpleNamespace(egress_id=None)) is None


def test_egress_failure_is_recorded_as_not_enforced(f):
    rec = LiveRecording(event_id=f.event.id, org_id=f.org.id, status="recording",
                        egress_id=f"egf-{f.uniq}", enforced=True)
    f.db.add(rec)
    f.db.flush()
    broadcast.record_egress_result(SimpleNamespace(
        egress_id=f"egf-{f.uniq}", error="no output configured", file_results=[]))
    f.db.expire_all()
    row = f.db.get(LiveRecording, rec.id)
    assert row.enforced is False and "no output" in (row.error or "")


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_reports_down_when_live_with_no_publisher(f):
    """The one failure a host cannot recover from is finding out from the audience."""
    down = broadcast.health_of({"publishing": 0, "participants": 3, "poor_connections": 0}, "live", None)
    assert down["level"] == "down" and any("published" in i for i in down["issues"])
    ok = broadcast.health_of({"publishing": 1, "participants": 3, "poor_connections": 0}, "live", None)
    assert ok["level"] == "ok" and not ok["issues"]
    # A recording that is not being captured is a warning, not a silent success.
    warn = broadcast.health_of({"publishing": 1, "participants": 1, "poor_connections": 0}, "live", False)
    assert warn["level"] == "warn"


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
    print(f"\nhost module: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
