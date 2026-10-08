"""Sign-in sessions: idle timeout, absolute lifetime, sign-out, enforced by the server.

The defect: a sign-in was a stateless 24-hour (30-day with "Remember me") JWT that nothing on
the server tracked, so closing the browser and reopening it hours later went straight back
into the dashboard. Each sign-in is now a server-side session (services/auth_sessions.py)
checked on every request.

Time is controlled, never waited for: `clock` replaces the session service's one clock.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.main as m
from app.config import settings
from app.db import SessionLocal
from app.models import AuthSession, Event, Organization, SignInEvent, User
from app.routers import live as live_router
from app.security import ALGORITHM, create_access_token, hash_password
from app.services import auth_sessions, bus, livekit

PASSWORD = "correct-horse-battery"
IDLE = timedelta(minutes=30)         # the defaults this file asserts (config.py)
ABSOLUTE = timedelta(hours=12)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "LIVEKIT_URL", "")
    assert not livekit.configured()
    monkeypatch.setattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 30)
    monkeypatch.setattr(settings, "SESSION_ABSOLUTE_TIMEOUT_HOURS", 12)
    monkeypatch.setattr(settings, "SESSION_ACTIVITY_MIN_INTERVAL_SECONDS", 60)


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def advance(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(auth_sessions, "_now", lambda: c.now)
    return c


class World:
    def __init__(self, security=None):
        db = SessionLocal()
        try:
            self.orgs, self.users, self.events = [], [], []
            org = Organization(name=f"Sessions {uuid.uuid4().hex[:6]}", status="active",
                               security=security or {})
            db.add(org)
            db.flush()
            self.orgs.append(org.id)
            self.org = org.id
            self.admin, self.admin_email = self._user(db, "org_admin")
            self.host, _ = self._user(db, "host")
            db.commit()
        finally:
            db.close()

    def _user(self, db, role):
        email = f"sess-{uuid.uuid4().hex[:10]}@example.com"
        u = User(org_id=self.org, full_name=role.title(), role=role, is_active=True, email=email,
                 username=f"se{uuid.uuid4().hex[:10]}", password_hash=hash_password(PASSWORD),
                 email_verified=True)
        db.add(u)
        db.flush()
        self.users.append(u.id)
        return u.id, email

    def token(self, user_id=None, remember=False):
        """A sign-in through the same function /auth/login uses."""
        db = SessionLocal()
        try:
            return create_access_token(db.get(User, user_id or self.admin), remember=remember, db=db)
        finally:
            db.close()

    def login(self, remember):
        r = TestClient(m.app).post("/api/auth/login", json={
            "identifier": self.admin_email, "password": PASSWORD, "remember": remember})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    def session_row(self, token):
        sid = jwt.get_unverified_claims(token)["sid"]
        db = SessionLocal()
        try:
            return db.get(AuthSession, auth_sessions._hash(sid))
        finally:
            db.close()

    def public_event(self):
        db = SessionLocal()
        try:
            ev = Event(org_id=self.org, created_by=self.admin, title="Public", status="scheduled",
                       visibility="public", start_time=datetime.now(timezone.utc) + timedelta(days=1))
            db.add(ev)
            db.commit()
            self.events.append(ev.id)
            return ev.id
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(Event).filter(Event.id.in_(self.events)).delete(synchronize_session=False)
            # Real /auth/login calls write the sign-in audit trail.
            db.query(SignInEvent).filter(SignInEvent.user_id.in_(self.users)).delete(synchronize_session=False)
            # auth_sessions go with their users (ON DELETE CASCADE).
            db.query(User).filter(User.id.in_(self.users)).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id.in_(self.orgs)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@pytest.fixture
def world():
    w = World()
    try:
        yield w
    finally:
        w.cleanup()


def call(token, method="GET", path="/api/auth/me"):
    return TestClient(m.app).request(method, path, headers={"Authorization": f"Bearer {token}"})


def ended(response, code="SESSION_EXPIRED", reason=None):
    detail = response.json().get("detail") or {}
    return (response.status_code == 401 and isinstance(detail, dict) and detail.get("code") == code
            and (reason is None or detail.get("reason") == reason))


# ── the basics ───────────────────────────────────────────────────────────────────────────

def test_a_fresh_session_works_and_reports_its_deadlines(world, clock):
    token = world.token()
    assert call(token).status_code == 200
    status = call(token, path="/api/auth/session").json()
    assert status["idle_timeout_seconds"] == int(IDLE.total_seconds())
    absolute = datetime.fromisoformat(status["absolute_expires_at"])
    assert absolute - clock.now == ABSOLUTE
    assert datetime.fromisoformat(status["idle_expires_at"]) - clock.now == IDLE
    # The token itself dies with the session, not 24 hours / 30 days later.
    assert jwt.get_unverified_claims(token)["exp"] == int(absolute.timestamp())


def test_an_idle_session_expires(world, clock):
    token = world.token()
    clock.advance(minutes=31)
    r = call(token)
    assert ended(r, reason="idle")
    assert r.json()["detail"]["message"] == "Your session expired due to inactivity. Please sign in again."
    assert world.session_row(token).revoked_reason == "idle"


def test_activity_slides_the_idle_window(world, clock):
    token = world.token()
    clock.advance(minutes=20)
    assert call(token, "POST", "/api/auth/session/activity").status_code == 200
    clock.advance(minutes=25)                      # 45 min after sign-in, 25 after activity
    assert call(token).status_code == 200
    clock.advance(minutes=6)                       # 31 min after the last activity
    assert ended(call(token), reason="idle")


def test_ordinary_requests_and_polling_never_extend_a_session(world, clock):
    """Dashboard polling, /me and status checks are authenticated requests, but not activity."""
    token = world.token()
    before = world.session_row(token).last_activity_at
    for _ in range(3):
        clock.advance(minutes=9)
        for path in ("/api/auth/me", "/api/auth/session", "/api/organization/overview"):
            assert call(token, path=path).status_code == 200, path
    assert world.session_row(token).last_activity_at == before
    clock.advance(minutes=4)                       # 31 min, with polling all along
    assert ended(call(token, path="/api/organization/overview"), reason="idle")


def test_the_absolute_limit_ends_even_an_active_session(world, clock):
    token = world.token()
    elapsed = timedelta()
    while elapsed + timedelta(minutes=25) < ABSOLUTE:
        clock.advance(minutes=25)
        elapsed += timedelta(minutes=25)
        assert call(token, "POST", "/api/auth/session/activity").status_code == 200
    clock.advance(minutes=30)                      # past 12 h, activity notwithstanding
    assert ended(call(token), reason="absolute")
    assert ended(call(token, "POST", "/api/auth/session/activity"), reason="absolute")


def test_an_ended_session_is_a_structured_401_never_a_server_error(world, clock):
    token = world.token()
    clock.advance(hours=1)
    for path in ("/api/auth/me", "/api/organization/overview", "/api/organization/branding"):
        r = call(token, path=path)
        assert r.status_code == 401, path
        assert r.headers.get("www-authenticate") == "Bearer"
        assert r.json()["detail"]["code"] == "SESSION_EXPIRED"
        assert "schema" not in r.text.lower() and "database" not in r.text.lower()


# ── extension, sign-out, revocation ──────────────────────────────────────────────────────

def test_an_expired_session_cannot_be_extended(world, clock):
    token = world.token()
    clock.advance(minutes=31)
    assert ended(call(token, "POST", "/api/auth/session/activity"), reason="idle")
    assert ended(call(token), reason="idle")       # and it stays ended


def test_sign_out_revokes_the_session_server_side(world, clock):
    token = world.token()
    assert call(token, "POST", "/api/auth/logout").status_code == 204
    assert world.session_row(token).revoked_reason == "logout"
    # The same token, presented again (a copy left in another tab, a stolen copy): refused.
    assert ended(call(token), code="SESSION_REVOKED")
    assert ended(call(token, "POST", "/api/auth/session/activity"), code="SESSION_REVOKED")


def test_reopening_the_browser_after_the_idle_window_requires_sign_in(world, clock):
    """The reported bug: the stored token outlived the user's absence."""
    token = world.token(remember=True)
    clock.advance(hours=3)                         # browser closed for an afternoon
    assert ended(call(token), reason="idle")


def test_reopening_within_the_idle_window_keeps_the_session(world, clock):
    token = world.token(remember=True)
    clock.advance(minutes=10)
    assert call(token).status_code == 200


def test_remember_me_changes_persistence_never_the_lifetime(world, clock):
    """Recorded on the session and reported to the client (which decides where to keep the
    credential); the idle and absolute limits are identical either way."""
    remembered, forgotten = world.login(remember=True), world.login(remember=False)
    a, b = call(remembered, path="/api/auth/session").json(), call(forgotten, path="/api/auth/session").json()
    assert a["remember"] is True and b["remember"] is False
    assert a["idle_timeout_seconds"] == b["idle_timeout_seconds"]
    assert abs(datetime.fromisoformat(a["absolute_expires_at"])
               - datetime.fromisoformat(b["absolute_expires_at"])) < timedelta(seconds=5)
    clock.advance(minutes=31)
    assert ended(call(remembered), reason="idle"), "Remember me must never make a session immortal"


def test_sessions_on_different_devices_are_independent(world, clock):
    chrome, edge = world.token(), world.token()
    clock.advance(minutes=20)
    assert call(chrome, "POST", "/api/auth/session/activity").status_code == 200
    assert call(edge, "POST", "/api/auth/logout").status_code == 204
    assert call(chrome).status_code == 200, "signing out one device must not sign out another"
    clock.advance(minutes=11)                      # 31 min after edge's sign-in; chrome active at 20
    assert call(chrome).status_code == 200
    assert ended(call(edge), code="SESSION_REVOKED")


def test_a_deactivated_user_can_neither_use_nor_extend_a_session(world, clock):
    token = world.token()
    db = SessionLocal()
    try:
        db.get(User, world.admin).is_active = False
        db.commit()
    finally:
        db.close()
    assert call(token).status_code == 401
    assert call(token, "POST", "/api/auth/session/activity").status_code == 401


# ── forged, stale and foreign credentials ────────────────────────────────────────────────

def _sign(claims):
    key = settings.SECRET_KEY
    return jwt.encode(claims, key, algorithm=ALGORITHM)


def test_a_token_from_before_sessions_existed_is_refused(world, clock):
    """Grandfathering decision: pre-rollout tokens carry no session, so they are refused and
    the user signs in once. A stale token in a browser cannot keep the old behaviour."""
    legacy = _sign({"sub": str(world.admin), "role": "org_admin",
                    "exp": datetime.now(timezone.utc) + timedelta(days=20)})
    assert ended(call(legacy), reason="reauth")


def test_a_made_up_or_borrowed_session_id_is_refused(world, clock):
    made_up = _sign({"sub": str(world.admin), "role": "org_admin", "sid": "not-a-session",
                     "exp": datetime.now(timezone.utc) + timedelta(hours=1)})
    assert ended(call(made_up), reason="reauth")
    # Another user's live session cannot be attached to this user's identity.
    host_sid = jwt.get_unverified_claims(world.token(world.host))["sid"]
    borrowed = _sign({"sub": str(world.admin), "role": "org_admin", "sid": host_sid,
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)})
    assert ended(call(borrowed), reason="reauth")


def test_session_ids_are_unpredictable_and_never_stored(world, clock):
    sids = {jwt.get_unverified_claims(world.token())["sid"] for _ in range(5)}
    assert len(sids) == 5 and all(len(s) >= 40 for s in sids)
    token = world.token()
    sid = jwt.get_unverified_claims(token)["sid"]
    row = world.session_row(token)
    assert row.id != sid and len(row.id) == 64      # sha256 only


# ── efficiency, policy, sockets, public pages ────────────────────────────────────────────

def test_activity_writes_are_throttled(world, clock):
    token = world.token()
    first = world.session_row(token).last_activity_at
    clock.advance(seconds=30)
    call(token, "POST", "/api/auth/session/activity")
    assert world.session_row(token).last_activity_at == first, "no write inside the interval"
    clock.advance(seconds=40)
    call(token, "POST", "/api/auth/session/activity")
    assert world.session_row(token).last_activity_at > first


def test_an_organization_policy_can_only_shorten_the_absolute_limit(clock):
    w = World(security={"session_timeout": "1 hour"})
    try:
        token = w.token()
        for _ in range(2):
            clock.advance(minutes=25)
            assert call(token, "POST", "/api/auth/session/activity").status_code == 200
        clock.advance(minutes=11)                  # 61 min
        assert ended(call(token), reason="absolute")
    finally:
        w.cleanup()


def test_a_websocket_cannot_connect_on_an_ended_session(world, clock):
    token = world.token()
    db = SessionLocal()
    try:
        assert live_router._user_from_token(token, db) is not None
        clock.advance(minutes=31)
        assert live_router._user_from_token(token, db) is None
    finally:
        db.close()


def test_a_public_page_treats_an_ended_session_as_anonymous(world, clock):
    """An expired session must not break a public page (a viewer watching a public event)."""
    event_id = world.public_event()
    token = world.token()
    clock.advance(minutes=31)
    r = call(token, path=f"/api/events/{event_id}/watch")
    assert r.status_code == 200, r.text


def test_an_active_producer_stays_signed_in_and_an_abandoned_one_does_not(world, clock):
    """The host console reports activity while a broadcast is live (client side: the live
    hold in useSessionKeeper). A producer doing that for three hours stays signed in; a
    console that stops reporting — the broadcast ended, the tab was abandoned — expires."""
    producer, abandoned = world.token(world.host), world.token(world.host)
    for _ in range(36):                            # every 5 minutes for 3 hours
        clock.advance(minutes=5)
        assert call(producer, "POST", "/api/auth/session/activity").status_code == 200
    assert call(producer).status_code == 200
    assert ended(call(abandoned), reason="idle")


def test_a_presenter_hold_cannot_outlast_the_absolute_limit(world, clock):
    """What the speaker/host hold sends (one activity report every few minutes) keeps a session
    through the idle window, but never past its maximum length."""
    presenter = world.token(world.host)
    elapsed = timedelta()
    while elapsed + timedelta(minutes=4) < ABSOLUTE:
        clock.advance(minutes=4)
        elapsed += timedelta(minutes=4)
        assert call(presenter, "POST", "/api/auth/session/activity").status_code == 200
    clock.advance(minutes=5)
    assert ended(call(presenter, "POST", "/api/auth/session/activity"), reason="absolute")
    assert ended(call(presenter), reason="absolute")


# ── an open live socket ends with its session (routers/live.py session_guard) ────────────
# The socket used to check the session at connect only, so an already-open console stayed
# privileged after the session expired, was signed out, or the user was disabled. It is now
# re-checked on a beat; the beat is shortened here, and the server's idle reaper too, so a
# broken guard fails these tests within seconds instead of hanging them.

@pytest.fixture
def fast_guard(monkeypatch):
    monkeypatch.setattr(live_router, "SESSION_RECHECK_SECONDS", 0.05)
    monkeypatch.setattr(live_router, "IDLE_TIMEOUT", 5.0)


def socket(token, event_id):
    return TestClient(m.app).websocket_connect(f"/api/live/events/{event_id}/ws?token={token}")


def until_closed(ws, beats=60):
    """Every frame until the server closes the socket, and how it closed.

    Bounded: it pings and reads up to each pong, so a server that never closes the socket fails
    the test after `beats` round trips. (Starlette's TestClient never wakes a reader whose
    server-side handler returned without closing, so a plain read could hang the suite.)"""
    frames = []
    for _ in range(beats):
        time.sleep(0.05)
        try:
            ws.send_json({"action": "ping", "t": 0})
            while True:
                frame = ws.receive_json()
                frames.append(frame)
                if frame.get("type") == "pong":
                    break
        except WebSocketDisconnect as exc:
            return frames, exc.code, exc.reason
    raise AssertionError("the server never closed the socket")


def still_open(ws):
    """Let a few guard beats run, then prove the socket answers a ping (a close instead of the
    pong raises WebSocketDisconnect, failing the test)."""
    time.sleep(0.3)
    ws.send_json({"action": "ping", "t": 1})
    for _ in range(100):
        if ws.receive_json().get("type") == "pong":
            return True
    return False


def session_ended(frames, code, reason, expected):
    told = [f for f in frames if f.get("channel") == "session" and f.get("type") == "ended"]
    return code == 4401 and reason == expected and told and told[-1]["data"]["reason"] == expected


def test_an_open_socket_closes_when_its_session_goes_idle(world, clock, fast_guard):
    event_id, token = world.public_event(), world.token()
    with socket(token, event_id) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        assert still_open(ws)
        clock.advance(minutes=31)
        frames, code, reason = until_closed(ws)
    assert session_ended(frames, code, reason, "idle")


def test_socket_heartbeats_never_keep_a_session_alive(world, clock, fast_guard):
    event_id, token = world.public_event(), world.token()
    before = world.session_row(token).last_activity_at
    with socket(token, event_id) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        for _ in range(3):                         # 24 minutes of pings, nothing else
            clock.advance(minutes=8)
            assert still_open(ws)
        assert world.session_row(token).last_activity_at == before
        clock.advance(minutes=7)                   # 31 minutes since the last real activity
        frames, code, reason = until_closed(ws)
    assert session_ended(frames, code, reason, "idle")


def test_an_active_presenter_keeps_both_the_session_and_the_socket(world, clock, fast_guard):
    """Two hours on air with no input but the presenter hold's activity reports."""
    event_id, token = world.public_event(), world.token()
    with socket(token, event_id) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        for _ in range(6):
            clock.advance(minutes=20)
            assert call(token, "POST", "/api/auth/session/activity").status_code == 200
            assert still_open(ws)
    assert call(token).status_code == 200


def test_the_absolute_limit_closes_even_an_active_presenter_socket(world, clock, fast_guard):
    event_id, token = world.public_event(), world.token()
    with socket(token, event_id) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        elapsed = timedelta()
        while elapsed + timedelta(minutes=25) < ABSOLUTE:
            clock.advance(minutes=25)
            elapsed += timedelta(minutes=25)
            assert call(token, "POST", "/api/auth/session/activity").status_code == 200
        assert still_open(ws)
        clock.advance(minutes=30)
        frames, code, reason = until_closed(ws)
    assert session_ended(frames, code, reason, "absolute")


def test_signing_out_closes_the_open_socket(world, clock, fast_guard):
    event_id, token = world.public_event(), world.token()
    with socket(token, event_id) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        assert call(token, "POST", "/api/auth/logout").status_code == 204
        frames, code, reason = until_closed(ws)
    assert session_ended(frames, code, reason, "revoked")


def test_a_deactivated_user_loses_the_open_socket(world, clock, fast_guard):
    event_id, token = world.public_event(), world.token()
    with socket(token, event_id) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        db = SessionLocal()
        try:
            db.get(User, world.admin).is_active = False
            db.commit()
        finally:
            db.close()
        frames, code, reason = until_closed(ws)
    assert session_ended(frames, code, reason, "reauth")


def test_a_restricted_organization_loses_the_open_socket(world, clock, fast_guard):
    event_id, token = world.public_event(), world.token()
    with socket(token, event_id) as ws:
        assert ws.receive_json()["type"] == "snapshot"
        db = SessionLocal()
        try:
            db.get(Organization, world.org).status = "suspended"
            db.commit()
        finally:
            db.close()
        _, code, reason = until_closed(ws)
    # A deliberate, final refusal (not a session end): same code as at connect.
    assert code == 1008 and reason == "This organization is suspended"


def test_connecting_on_an_ended_session_says_the_session_ended(world, clock, fast_guard):
    """So the client signs out with the reason, as for an HTTP 401, instead of showing a bare
    'refused' state."""
    event_id, token = world.public_event(), world.token()
    clock.advance(minutes=31)
    with socket(token, event_id) as ws:
        frames, code, reason = until_closed(ws)
    assert not [f for f in frames if f.get("type") == "snapshot"] and code == 4401 and reason == "idle"
    # A token this server never signed is still the plain refusal.
    with socket("not-a-real-token", event_id) as ws:
        _, code, _ = until_closed(ws)
    assert code == 1008
