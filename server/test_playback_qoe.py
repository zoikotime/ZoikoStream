"""Viewer playback quality (QoE): /organization/playback's Playback starts, Rebuffer ratio and
Failures by reason, end to end through the real routes.

The page used to print a dash under each of the three with "Client-side playback QoE is not
ingested": the player reported nothing durable, and the page read nothing. Now
GET /events/{id}/watch hands each AUDIENCE member a signed QoE token with their media, the
player sends cumulative per-session beacons (POST /api/playback/qoe), and
GET /organization/playback/qoe aggregates them server-side (services/playback_qoe.py).

Pinned here: who gets a token (the audience, never staff), idempotent ingestion (retries,
duplicates, out-of-order beacons), the aggregate rebuffer ratio, failures by reason, live vs
replay, organization and event isolation, the time window, malformed and forged beacons, and
that nothing secret is stored.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from starlette.testclient import TestClient

import app.main as m
from app.db import Base, SessionLocal
from app.models import (AuditLog, Event, EventRegistration, Organization, PlaybackSession,
                        ReplayEntitlement, User)
from app.routers import events as events_router
from app.routers import playback as playback_router
from app.security import create_access_token, create_registration_token, hash_password
from app.services import audience as audience_svc
from app.services import bus, livekit
from app.services import playback_qoe as qoe

UTC = timezone.utc
H = timedelta(hours=1)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    monkeypatch.setattr(events_router.livekit, "configured", lambda: True)
    monkeypatch.setattr(events_router.livekit, "create_stream_token", lambda *a, **k: "stream-token")
    monkeypatch.setattr(livekit, "object_exists", lambda key: True)
    # A signed URL is a credential: it must never reach the QoE table.
    monkeypatch.setattr(livekit, "signed_url", lambda key: "https://storage.example.invalid/replay.mp4?sig=SIGNED-SECRET")
    playback_router._session_windows.clear()


class Org:
    def __init__(self, label):
        db = SessionLocal()
        try:
            org = Organization(name=f"QoE {label} {uuid.uuid4().hex[:6]}", status="active")
            db.add(org)
            db.flush()
            admin = User(org_id=org.id, full_name=f"{label} Admin", role="org_admin", is_active=True,
                         email=f"qoe-{uuid.uuid4().hex[:10]}@example.com", username=f"qoe{uuid.uuid4().hex[:10]}",
                         password_hash=hash_password("x"), email_verified=True)
            host = User(org_id=org.id, full_name=f"{label} Host", role="host", is_active=True,
                        email=f"qoeh-{uuid.uuid4().hex[:10]}@example.com", username=f"qoeh{uuid.uuid4().hex[:10]}",
                        password_hash=hash_password("x"), email_verified=True)
            db.add_all([admin, host])
            db.commit()
            self.id, self.admin, self.host = org.id, admin.id, host.id
            self.events = []
        finally:
            db.close()

    def event(self, status="live", **kw):
        db = SessionLocal()
        try:
            now = datetime.now(UTC)
            ev = Event(org_id=self.id, created_by=self.admin, title=f"QoE {uuid.uuid4().hex[:5]}",
                       status=status, visibility=kw.pop("visibility", "public"),
                       start_time=kw.pop("start_time", now - H), end_time=kw.pop("end_time", now + H), **kw)
            db.add(ev)
            db.commit()
            self.events.append(ev.id)
            return ev.id
        finally:
            db.close()

    def registration(self, event_id):
        db = SessionLocal()
        try:
            reg = EventRegistration(event_id=event_id, name="Grace Viewer",
                                    email=f"grace-{uuid.uuid4().hex[:8]}@example.com")
            db.add(reg)
            db.commit()
            return reg.id, create_registration_token(reg)
        finally:
            db.close()

    def publish_replay(self, event_id):
        db = SessionLocal()
        try:
            db.add(ReplayEntitlement(event_id=event_id, scope="audience", publish_state="published",
                                     watermark_status="ready", watermarked_file_key="replays/qoe.mp4"))
            db.commit()
        finally:
            db.close()

    def client(self, who="admin"):
        db = SessionLocal()
        try:
            token = create_access_token(db.get(User, getattr(self, who)), remember=False, db=db)
        finally:
            db.close()
        c = TestClient(m.app)
        c.headers["Authorization"] = f"Bearer {token}"
        return c

    def cleanup(self):
        db = SessionLocal()
        try:
            ids = self.events
            if ids:
                for table in reversed(Base.metadata.sorted_tables):
                    for fk in table.foreign_keys:
                        if fk.column.table.name == "events" and table.name != "events":
                            db.execute(table.delete().where(fk.parent.in_(ids)))
                db.query(Event).filter(Event.id.in_(ids)).delete(synchronize_session=False)
            db.query(PlaybackSession).filter(PlaybackSession.org_id == self.id).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.org_id == self.id).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_([self.admin, self.host])).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id == self.id).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@pytest.fixture
def org():
    o = Org("A")
    try:
        yield o
    finally:
        o.cleanup()


@pytest.fixture
def other():
    o = Org("B")
    try:
        yield o
    finally:
        o.cleanup()


def watch(c, event_id, **params):
    r = c.get(f"/api/events/{event_id}/watch", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def viewer_token(o, event_id):
    """A registered viewer's live QoE token, through the real watch endpoint."""
    _, reg_token = o.registration(event_id)
    out = watch(TestClient(m.app), event_id, reg=reg_token)
    assert out["livekit_token"] and out["qoe_token"], out
    return out["qoe_token"]


def session(event_id, mode="live", *, age=2 * H):
    """A QoE token minted `age` ago, straight from the service: the figure tests need sessions
    that have existed long enough to have played for minutes (ingest refuses more watch time
    than a session's age). The watch-endpoint tests above cover how tokens are issued."""
    return qoe.issue_token(event_id, mode, now=datetime.now(UTC) - age)


def beacon(token, seq, *, content_type="text/plain;charset=UTF-8", **fields):
    body = {"token": token, "seq": seq, **fields}
    return TestClient(m.app).post("/api/playback/qoe", content=json.dumps(body),
                                  headers={"Content-Type": content_type})


def ok(token, seq, **fields):
    r = beacon(token, seq, **fields)
    assert r.status_code == 204, r.text


def played(token, seq=1, *, ms=60_000, stalls=0, stall_ms=0, failures=(), startup=800, ended=False):
    ok(token, seq, started=True, startup_ms=startup, playback_ms=ms, rebuffer_count=stalls,
       rebuffer_ms=stall_ms, failures=list(failures), ended=ended)


def summary(o, **params):
    r = o.client().get("/api/organization/playback/qoe", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def rows(sid=None, **filters):
    db = SessionLocal()
    try:
        q = db.query(PlaybackSession)
        if sid:
            q = q.filter(PlaybackSession.id == uuid.UUID(sid))
        for k, v in filters.items():
            q = q.filter(getattr(PlaybackSession, k) == v)
        return q.all()
    finally:
        db.close()


def sid_of(token):
    return qoe.read_token(token)["sid"]


# ── who is measured ─────────────────────────────────────────────────────────────────────

def test_a_registered_live_viewer_gets_a_live_token_tied_to_their_registration(org):
    eid = org.event()
    reg_id, reg_token = org.registration(eid)
    out = watch(TestClient(m.app), eid, reg=reg_token)
    claims = qoe.read_token(out["qoe_token"])
    assert claims["mode"] == "live" and claims["eid"] == str(eid)
    assert claims["rid"] == str(reg_id)
    # The same opaque key audience_sessions uses — never the identity, email or reg token.
    assert claims["vk"] == audience_svc.viewer_key(f"guest-{reg_id}")
    played(out["qoe_token"])
    (row,) = rows(sid_of(out["qoe_token"]))
    assert row.registration_id == reg_id and row.viewer_key == claims["vk"] and row.mode == "live"


def test_staff_of_the_organizing_organization_are_never_measured(org):
    eid = org.event()
    for who in ("admin", "host"):
        out = watch(org.client(who), eid)
        # They are handed the stream, but no QoE token: hosts and producers are not viewers.
        assert out["livekit_token"] and out["qoe_token"] is None


def test_an_anonymous_replay_viewer_is_measured_without_any_identity(org):
    eid = org.event("ended", start_time=datetime.now(UTC) - 3 * H, end_time=datetime.now(UTC) - 2 * H)
    org.publish_replay(eid)
    out = watch(TestClient(m.app), eid)
    assert out["recording_url"] and out["qoe_token"]
    claims = qoe.read_token(out["qoe_token"])
    assert claims["mode"] == "replay" and "vk" not in claims and "rid" not in claims
    played(out["qoe_token"], ms=30_000)
    (row,) = rows(sid_of(out["qoe_token"]))
    assert row.mode == "replay" and row.viewer_key is None and row.registration_id is None


def test_a_page_reload_is_a_new_session_and_a_new_start(org):
    eid = org.event()
    _, reg_token = org.registration(eid)
    first = watch(TestClient(m.app), eid, reg=reg_token)["qoe_token"]
    second = watch(TestClient(m.app), eid, reg=reg_token)["qoe_token"]
    assert sid_of(first) != sid_of(second)
    played(first)
    played(second)
    assert summary(org, event_id=eid)["live"]["playback_starts"] == 2


# ── ingestion is idempotent ─────────────────────────────────────────────────────────────

def test_one_start_is_one_session_however_often_the_beacon_is_retried(org):
    eid = org.event()
    token = session(eid)
    for _ in range(3):                                    # network retries of the same beacon
        played(token, 1, ms=10_000)
    # Later beacons of the SAME session (a reconnect continues it) carry the running totals.
    played(token, 2, ms=70_000, stalls=1, stall_ms=4_000)
    played(token, 3, ms=130_000, stalls=1, stall_ms=4_000)
    assert len(rows(sid_of(token))) == 1
    live = summary(org, event_id=eid)["live"]
    assert (live["sessions"], live["playback_starts"]) == (1, 1)
    assert (live["playback_duration_ms"], live["rebuffer_count"], live["rebuffer_duration_ms"]) == (130_000, 1, 4_000)


def test_an_older_or_repeated_beacon_never_rolls_totals_back(org):
    eid = org.event()
    token = session(eid)
    played(token, 5, ms=300_000, stalls=2, stall_ms=9_000)
    played(token, 4, ms=200_000, stalls=1, stall_ms=5_000)   # arrived late
    played(token, 5, ms=1_000, stalls=0)                     # a repeat of seq 5, altered
    (row,) = rows(sid_of(token))
    assert (row.last_seq, row.playback_duration_ms, row.rebuffer_count, row.rebuffer_duration_ms) \
        == (5, 300_000, 2, 9_000)


def test_the_first_startup_time_and_start_moment_are_kept(org):
    eid = org.event()
    token = session(eid)
    played(token, 1, startup=900)
    played(token, 2, startup=5, ms=120_000)
    (row,) = rows(sid_of(token))
    assert row.startup_ms == 900 and row.playback_started_at is not None


# ── the three figures ───────────────────────────────────────────────────────────────────

def test_rebuffer_ratio_is_total_stall_time_over_total_watch_time(org):
    eid = org.event()
    played(session(eid), ms=600_000, stalls=1, stall_ms=12_000)   # 2% on its own
    played(session(eid), ms=60_000, stalls=3, stall_ms=30_000)    # 50% on its own
    live = summary(org, event_id=eid)["live"]
    # (12 + 30) / (600 + 60) = 6.36% — NOT the 26% an average of per-viewer ratios would give.
    assert live["rebuffer_ratio"] == pytest.approx(42_000 / 660_000, abs=1e-4)
    assert live["rebuffer_count"] == 4


def test_the_spec_example_reads_two_percent(org):
    eid = org.event()
    played(session(eid), ms=600_000, stalls=1, stall_ms=12_000)
    assert summary(org, event_id=eid)["live"]["rebuffer_ratio"] == 0.02


def test_failures_are_grouped_by_reason_once_per_session(org):
    eid = org.event()
    a, b, c = (session(eid) for _ in range(3))
    ok(a, 1, failures=["network_error"])                  # failed before starting
    ok(a, 2, failures=["network_error", "autoplay_blocked"])
    ok(a, 3, failures=["network_error"])                  # the same reason again: still one
    ok(b, 1, failures=["network_error"])
    played(c)                                             # a clean session
    live = summary(org, event_id=eid)["live"]
    assert live["failures"] == [{"reason": "network_error", "count": 2},
                                {"reason": "autoplay_blocked", "count": 1}]
    assert (live["sessions"], live["playback_starts"], live["failed_sessions"]) == (3, 1, 2)


def test_sessions_that_never_played_report_no_ratio(org):
    eid = org.event()
    ok(session(eid), 1, failures=["media_connection_failed"])
    live = summary(org, event_id=eid)["live"]
    assert (live["sessions"], live["playback_starts"], live["rebuffer_ratio"]) == (1, 0, None)


def test_no_playback_reads_as_unmeasured_not_as_zero(org):
    eid = org.event()
    for mode in ("live", "replay"):
        figures = summary(org, event_id=eid)[mode]
        assert figures["sessions"] == 0 and figures["playback_starts"] == 0
        assert figures["rebuffer_ratio"] is None and figures["startup_ms_median"] is None
        assert figures["failures"] == []


def test_live_and_replay_are_reported_apart(org):
    live_eid = org.event()
    replay_eid = org.event("ended", start_time=datetime.now(UTC) - 3 * H, end_time=datetime.now(UTC) - 2 * H)
    org.publish_replay(replay_eid)
    played(viewer_token(org, live_eid), ms=100_000, stalls=1, stall_ms=1_000)
    played(session(replay_eid, "replay"), ms=100_000, stalls=1, stall_ms=50_000)
    out = summary(org)
    assert out["live"]["rebuffer_ratio"] == 0.01 and out["replay"]["rebuffer_ratio"] == 0.5
    assert out["total"]["playback_starts"] == 2 and "rebuffer_ratio" not in out["total"]


def test_median_live_start_time(org):
    eid = org.event()
    for startup in (400, 1_000, 3_000):
        played(session(eid), startup=startup)
    assert summary(org, window="24h")["live"]["startup_ms_median"] == 1_000


# ── isolation and the window ────────────────────────────────────────────────────────────

def test_another_organization_cannot_read_or_count_this_ones_playback(org, other):
    eid = org.event()
    played(session(eid))
    r = other.client().get("/api/organization/playback/qoe", params={"event_id": str(eid)})
    assert r.status_code == 404
    assert summary(other)["total"]["sessions"] == 0
    assert summary(org)["total"]["sessions"] == 1


def test_an_event_filter_counts_only_that_event(org):
    first, second = org.event(), org.event()
    played(session(first))
    played(session(second))
    played(session(second))
    assert summary(org, event_id=first)["live"]["playback_starts"] == 1
    assert summary(org, event_id=second)["live"]["playback_starts"] == 2
    assert summary(org)["live"]["playback_starts"] == 3


def test_every_figure_uses_the_same_window(org):
    eid = org.event()
    old, new = session(eid), session(eid)
    played(old, ms=100_000, stalls=1, stall_ms=50_000, failures=["decoder_error"])
    played(new, ms=100_000)
    db = SessionLocal()
    try:
        db.execute(update(PlaybackSession).where(PlaybackSession.id == uuid.UUID(sid_of(old)))
                   .values(created_at=datetime.now(UTC) - timedelta(days=3)))
        db.commit()
    finally:
        db.close()
    day = summary(org, event_id=eid, window="24h")["live"]
    assert (day["playback_starts"], day["rebuffer_ratio"], day["failures"]) == (1, 0.0, [])
    for window in ("7d", "30d", "all"):
        wide = summary(org, event_id=eid, window=window)["live"]
        assert wide["playback_starts"] == 2 and wide["rebuffer_ratio"] == 0.25
        assert wide["failures"] == [{"reason": "decoder_error", "count": 1}]
    assert org.client().get("/api/organization/playback/qoe", params={"window": "90d"}).status_code == 422


# ── what is refused ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fields", [
    {"playback_ms": -1, "started": True},
    {"started": True, "playback_ms": 1_000, "rebuffer_count": 1, "rebuffer_ms": 2_000},
    {"started": True, "playback_ms": 1_000, "rebuffer_ms": 500},          # stall time, no stall
    {"playback_ms": 1_000},                                                # watched before starting
    {"failures": ["https://cdn.example/x.mp4?sig=leak"]},                  # not a reason code
    {"error": "TypeError: at https://storage.example.invalid/x?sig=leak"},  # free text never rides along
    {"seq": 0},
])
def test_malformed_beacons_are_refused_and_store_nothing(org, fields):
    token = viewer_token(org, org.event())
    body = {"seq": 1, **fields}
    r = beacon(token, body.pop("seq"), **body)
    assert r.status_code == 422, r.text
    assert "leak" not in r.text and token not in r.text           # the refusal echoes no values
    assert rows(sid_of(token)) == []


def test_bodies_that_are_not_a_beacon_are_refused(org):
    c = TestClient(m.app)
    for raw in (b"not json", b"[1, 2]", b"", json.dumps({"seq": 1}).encode()):
        r = c.post("/api/playback/qoe", content=raw, headers={"Content-Type": "text/plain"})
        assert r.status_code == 422, (raw, r.text)
    big = json.dumps({"token": "x" * 5000, "seq": 1}).encode()
    assert c.post("/api/playback/qoe", content=big).status_code == 413


def test_forged_expired_and_borrowed_tokens_are_refused(org):
    eid = org.event()
    good = viewer_token(org, eid)
    expired = qoe.issue_token(eid, "live", now=datetime.now(UTC) - 25 * H)
    login = org.client().headers["Authorization"].removeprefix("Bearer ")
    forged = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    for token in (expired, login, forged):
        assert beacon(token, 1, failures=["unknown"]).status_code == 401
    # And the other way round: a QoE token is not a login.
    c = TestClient(m.app)
    c.headers["Authorization"] = f"Bearer {good}"
    assert c.get("/api/organization/playback/qoe").status_code == 401


def test_watch_time_cannot_exceed_the_sessions_age(org):
    eid = org.event()
    token = viewer_token(org, eid)
    r = beacon(token, 1, started=True, playback_ms=10 * 3600 * 1000)
    assert r.status_code == 422 and rows(sid_of(token)) == []


def test_a_beacon_for_a_deleted_event_is_gone(org):
    eid = org.event()
    token = qoe.issue_token(eid, "live")
    db = SessionLocal()
    try:
        db.query(Event).filter(Event.id == eid).delete()
        db.commit()
    finally:
        db.close()
    org.events.remove(eid)
    assert beacon(token, 1, failures=["unknown"]).status_code == 410


def test_a_runaway_player_is_slowed_per_session(org):
    token = viewer_token(org, org.event())
    codes = [beacon(token, seq, failures=["unknown"]).status_code for seq in range(1, 33)]
    assert codes[:playback_router.SESSION_LIMIT] == [204] * playback_router.SESSION_LIMIT
    assert codes[-1] == 429


def test_json_and_text_plain_beacons_are_both_accepted(org):
    eid = org.event()
    a, b = viewer_token(org, eid), viewer_token(org, eid)
    played(a)                                                    # text/plain (keepalive path)
    r = beacon(b, 1, content_type="application/json", started=True, playback_ms=1_000)
    assert r.status_code == 204
    assert summary(org, event_id=eid)["live"]["playback_starts"] == 2


def test_nothing_secret_is_stored(org):
    eid = org.event()
    reg_id, reg_token = org.registration(eid)
    out = watch(TestClient(m.app), eid, reg=reg_token)
    token = out["qoe_token"]
    played(token, failures=["network_error"])
    columns = {c.name for c in PlaybackSession.__table__.columns}
    assert columns == {"id", "org_id", "event_id", "mode", "viewer_key", "registration_id", "last_seq",
                       "playback_started_at", "startup_ms", "playback_duration_ms", "rebuffer_count",
                       "rebuffer_duration_ms", "failure_reasons", "ended_at", "created_at", "updated_at"}
    (row,) = rows(sid_of(token))
    stored = json.dumps({c: str(getattr(row, c)) for c in columns})
    for secret in (token, reg_token, "stream-token", "SIGNED-SECRET", "grace", "@example.com", "testclient"):
        assert secret not in stored

# ── how many sessions an event can be sent (inflation) ──────────────────────────────────
#
# A token is minted by every /watch call (the page polls it), but only a token's FIRST beacon
# creates a counted session, and new sessions are budgeted per event: per credential-backed
# viewer and per network address (services/playback_qoe.admit_new_session).

def replay_event(o):
    """An ended event with a published replay: what an anonymous viewer is measured on (a live
    stream asks a link-shareable event's viewers to register first)."""
    now = datetime.now(UTC)
    eid = o.event(status="ended", start_time=now - 3 * H, end_time=now - 2 * H)
    o.publish_replay(eid)
    return eid


def anonymous_token(event_id):
    """An anonymous replay viewer's token, through the real watch endpoint."""
    out = watch(TestClient(m.app), event_id)
    assert out["qoe_token"] and qoe.read_token(out["qoe_token"])["mode"] == "replay", out
    return out["qoe_token"]


def first_beacon(token):
    return beacon(token, 1, started=True, startup_ms=500, playback_ms=1_000).status_code


def test_rapid_watch_calls_cannot_mint_unlimited_sessions(org, monkeypatch):
    monkeypatch.setattr(qoe, "NEW_SESSIONS_PER_ADDRESS", 5)
    eid = replay_event(org)
    codes = [first_beacon(anonymous_token(eid)) for _ in range(9)]
    assert codes == [204] * 5 + [429] * 4
    assert len(rows(event_id=eid)) == 5
    assert summary(org, event_id=eid)["replay"]["playback_starts"] == 5


def test_watch_polls_that_never_report_cost_nothing(org, monkeypatch):
    monkeypatch.setattr(qoe, "NEW_SESSIONS_PER_ADDRESS", 1)
    eid = replay_event(org)
    for _ in range(10):
        anonymous_token(eid)                      # the page polling /watch: tokens nobody uses
    assert first_beacon(anonymous_token(eid)) == 204


def test_a_normal_viewer_gets_one_session_and_its_own_beacons_are_never_budgeted(org, monkeypatch):
    monkeypatch.setattr(qoe, "NEW_SESSIONS_PER_ADDRESS", 1)
    eid = replay_event(org)
    token = anonymous_token(eid)
    for seq in range(1, 8):                       # the session's own later beacons, and a retry
        assert beacon(token, seq, started=True, playback_ms=seq * 1_000).status_code == 204
    assert beacon(token, 7, started=True, playback_ms=7_000).status_code == 204
    assert len(rows(event_id=eid)) == 1


def test_a_genuinely_later_session_is_measured_again(org, monkeypatch):
    from app.services import api_usage
    monkeypatch.setattr(qoe, "NEW_SESSIONS_PER_ADDRESS", 1)
    eid = replay_event(org)
    assert first_beacon(anonymous_token(eid)) == 204
    assert first_beacon(anonymous_token(eid)) == 429
    later = api_usage.time.monotonic() + qoe.NEW_SESSION_WINDOW_SECONDS + 1
    monkeypatch.setattr(api_usage.time, "monotonic", lambda: later)
    assert first_beacon(anonymous_token(eid)) == 204
    assert len(rows(event_id=eid)) == 2


def test_a_registered_viewer_is_budgeted_as_one_viewer(org, monkeypatch):
    monkeypatch.setattr(qoe, "NEW_SESSIONS_PER_VIEWER", 2)
    eid = org.event()
    _, reg_token = org.registration(eid)
    tokens = [watch(TestClient(m.app), eid, reg=reg_token)["qoe_token"] for _ in range(4)]
    assert [first_beacon(t) for t in tokens] == [204, 204, 429, 429]
    # Another registered viewer, from the same address, is a different viewer.
    assert first_beacon(viewer_token(org, eid)) == 204
    assert all(r.registration_id is not None for r in rows(event_id=eid))


def test_an_anonymous_replay_viewer_still_works_under_the_budget(org):
    assert first_beacon(anonymous_token(replay_event(org))) == 204


def test_one_events_budget_never_touches_another(org, monkeypatch):
    monkeypatch.setattr(qoe, "NEW_SESSIONS_PER_ADDRESS", 1)
    first, second = replay_event(org), replay_event(org)
    assert first_beacon(anonymous_token(first)) == 204
    assert first_beacon(anonymous_token(first)) == 429
    assert first_beacon(anonymous_token(second)) == 204


def test_no_cross_organization_inflation(org, other, monkeypatch):
    monkeypatch.setattr(qoe, "NEW_SESSIONS_PER_ADDRESS", 1)
    mine, theirs = replay_event(org), replay_event(other)
    assert first_beacon(anonymous_token(mine)) == 204
    assert first_beacon(anonymous_token(mine)) == 429         # this event is spent...
    assert first_beacon(anonymous_token(theirs)) == 204       # ...the other organization's isn't
    # A token always counts for the event it names, whatever else the beacon says.
    token = anonymous_token(mine)
    r = TestClient(m.app).post("/api/playback/qoe", content=json.dumps(
        {"token": token, "seq": 1, "started": True, "org_id": str(other.id), "event_id": str(theirs)}))
    assert r.status_code == 422                                # naming an org/event is refused
    assert summary(other, event_id=theirs)["replay"]["sessions"] == 1


# ── malformed bodies and deleted events ─────────────────────────────────────────────────

def test_deeply_nested_json_is_a_clean_422_not_a_500(org):
    c = TestClient(m.app)
    for raw in (b"[" * 4000, b'{"token": "x", "seq": 1, "a": ' + b"[" * 4000):
        assert len(raw) <= playback_router.MAX_BODY_BYTES
        r = c.post("/api/playback/qoe", content=raw, headers={"Content-Type": "text/plain"})
        assert r.status_code == 422, r.text


def test_a_chunked_body_is_refused_once_past_the_cap(org):
    def chunks():
        for _ in range(64):
            yield b"x" * 1024
    r = TestClient(m.app).post("/api/playback/qoe", content=chunks(), headers={"Content-Type": "text/plain"})
    assert r.status_code == 413


def test_a_soft_deleted_event_is_no_longer_measured_or_counted(org):
    eid = org.event()
    token = session(eid)
    played(token, ms=60_000, stalls=1, stall_ms=1_000)
    assert summary(org)["live"]["sessions"] == 1
    db = SessionLocal()
    try:
        db.execute(update(Event).where(Event.id == eid).values(deleted_at=datetime.now(UTC)))
        db.commit()
    finally:
        db.close()
    assert beacon(token, 2, started=True, playback_ms=120_000).status_code == 410
    assert beacon(session(eid), 1, started=True, playback_ms=1_000).status_code == 410
    assert summary(org)["live"]["sessions"] == 0
    assert summary(org)["total"]["playback_starts"] == 0


# ── retention and indexes ───────────────────────────────────────────────────────────────

def test_rows_past_retention_are_purged_and_nothing_else(org, other):
    old, recent, theirs = session(org.event()), session(org.event()), session(other.event())
    for token in (old, recent, theirs):
        played(token)
    db = SessionLocal()
    try:
        db.execute(update(PlaybackSession)
                   .where(PlaybackSession.id.in_([uuid.UUID(sid_of(old)), uuid.UUID(sid_of(theirs))]))
                   .values(created_at=datetime.now(UTC) - timedelta(days=45)))
        db.commit()
        assert qoe.purge_expired(db, retention_days=0) == 0           # 0 disables it
        assert qoe.purge_expired(db, retention_days=30) >= 2
    finally:
        db.close()
    assert rows(sid_of(old)) == [] and rows(sid_of(theirs)) == []
    assert len(rows(sid_of(recent))) == 1


def test_the_table_carries_its_three_secondary_indexes():
    from sqlalchemy import text
    db = SessionLocal()
    try:
        names = set(db.scalars(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'playback_sessions'")).all())
    finally:
        db.close()
    assert {"ix_playback_sessions_org_created", "ix_playback_sessions_event_created",
            "ix_playback_sessions_registration_id"} <= names


def test_the_model_builds_on_sqlite_too():
    # Several suites build every table on SQLite: a Postgres-only type or default here once
    # failed all of them at table creation.
    from sqlalchemy import create_engine, inspect
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    names = {ix["name"] for ix in inspect(engine).get_indexes("playback_sessions")}
    assert "ix_playback_sessions_registration_id" in names
