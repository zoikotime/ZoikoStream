"""Starting a recording when LiveKit egress will not take the job.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
Production: the host clicked Record, LiveKit's egress service did not answer
(`ServerError(code=unavailable, message=twirp error unknown: no response from servers,
status=503)`), and _recording_start wrote the row as status="recording" anyway. Three
consequences, all of them wrong:

  * _current_recordings counted the row as live, so the host's NEXT click came back
    "A recording is already running" — they could never retry;
  * the console started a timer for a file that did not exist;
  * the failure was silent until Event Details showed "Not captured" after the event.

A path with no egress_id is now terminal from the start (status="failed", stopped_at set),
the attempt and the provider's message are still persisted for diagnostics, and a
`recording.error` envelope tells the host on the click that failed.

The 503 itself is infrastructure — an unreachable Egress worker — and nothing here retries
or hides it. These pin how the application BEHAVES when it happens.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.main  # noqa: F401  - import order; see test_live_socket_connect.py

from app.db import SessionLocal
from app.models import Event, LiveRecording, Organization, User
from app.security import hash_password
from app.services import broadcast as bc
from app.services import bus
from app.services import livekit as lk
from app.services import moderation as mod

UTC = timezone.utc

# The exact string LiveKit's Python SDK produced in production.
TWIRP_503 = ("ServerError(code=unavailable, message=twirp error unknown: "
             "no response from servers, status=503)")


@pytest.fixture(autouse=True)
def in_process_bus():
    url = bus.settings.REDIS_URL
    bus.settings.REDIS_URL = ""
    try:
        yield
    finally:
        bus.settings.REDIS_URL = url


@pytest.fixture
def sent(monkeypatch):
    out = []
    real = bus.publish

    async def spy(event_id, channel, type_, data=None):
        out.append((channel, type_, data))
        return await real(event_id, channel, type_, data)

    monkeypatch.setattr(bus, "publish", spy)
    return out


@pytest.fixture
def world():
    db = SessionLocal()
    made = {"ev": [], "u": [], "o": []}
    try:
        org = Organization(name=f"RecCo {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        made["o"] = [org.id]
        user = User(org_id=org.id, full_name="Vihari Host", role="org_admin", is_active=True,
                    email=f"rec-{uuid.uuid4().hex[:10]}@example.com",
                    username=f"rec{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password("x"), email_verified=True)
        db.add(user)
        db.flush()
        made["u"] = [user.id]
        ev = Event(org_id=org.id, created_by=user.id, title="test", status="live",
                   visibility="public", recording_enabled=True,
                   start_time=datetime.now(UTC) - timedelta(minutes=5))
        db.add(ev)
        db.flush()
        made["ev"] = [ev.id]
        db.commit()
        yield {"db": db, "org": org, "event": ev, "user": user}
    finally:
        try:
            for eid in made["ev"]:
                db.query(LiveRecording).filter(LiveRecording.event_id == eid).delete()
                db.execute(Event.__table__.delete().where(Event.id == eid))
            for uid in made["u"]:
                db.execute(User.__table__.delete().where(User.id == uid))
            for oid in made["o"]:
                db.execute(Organization.__table__.delete().where(Organization.id == oid))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def _ctx(world):
    ev = world["event"]
    return mod.Ctx(event_id=ev.id, org_id=ev.org_id, room=f"event_{ev.id}",
                   user_id=world["user"].id, name="Vihari Host",
                   identity=str(world["user"].id), role="org_admin",
                   can_moderate=True, can_host=True)


def start(world, payload=None):
    return asyncio.run(mod.dispatch(_ctx(world), "recording.start", payload or {}))


def rows(world):
    """Every recording row for this event, oldest first. expire_all() because the action
    committed on a different session."""
    world["db"].expire_all()
    return (world["db"].query(LiveRecording)
            .filter(LiveRecording.event_id == world["event"].id)
            .order_by(LiveRecording.started_at).all())


@pytest.fixture
def egress_fails(monkeypatch):
    """LiveKit's egress service does not answer — the production 503."""
    async def boom(room, quality, filepath):
        return None, TWIRP_503
    monkeypatch.setattr(lk, "start_recording", boom)
    monkeypatch.setattr(lk, "gcs_config_error", lambda: None)


@pytest.fixture
def egress_works(monkeypatch):
    async def ok(room, quality, filepath):
        return f"EG_{uuid.uuid4().hex[:8]}", None
    monkeypatch.setattr(lk, "start_recording", ok)
    monkeypatch.setattr(lk, "gcs_config_error", lambda: None)


# ── 2. a failed start is failed, not "recording" ───────────────────────────────────────

def test_a_refused_egress_is_persisted_as_failed(world, sent, egress_fails):
    assert start(world) is None            # the action itself does not error out

    (r,) = rows(world)
    assert r.status == "failed"            # was "recording" — the whole bug
    assert r.enforced is False
    assert r.egress_id is None
    assert r.stopped_at is not None        # terminal, so nothing treats it as in flight


def test_the_provider_error_is_kept_verbatim(world, sent, egress_fails):
    start(world)

    (r,) = rows(world)
    assert r.error == TWIRP_503
    assert "503" in r.error


def test_no_file_url_is_invented_for_a_capture_that_never_began(world, sent, egress_fails):
    start(world)
    assert rows(world)[0].file_url is None


def test_the_host_is_told_on_the_click_that_failed(world, sent, egress_fails):
    start(world)

    errors = [e for e in sent if e[0] == "recording" and e[1] == "recording.error"]
    assert len(errors) == 1, sent
    assert "503" in errors[0][2]["message"]
    # The row still goes out, so the console can reconcile rather than guess.
    assert any(e[1] == "recording.update" for e in sent)


# ── 3. a failed attempt must not block a retry ─────────────────────────────────────────

def test_a_failed_attempt_is_not_already_recording(world, sent, egress_fails):
    """THE REPORTED SYMPTOM. The second click used to come back "A recording is already
    running" and the host could never retry."""
    start(world)

    second = start(world)

    assert second != "A recording is already running"
    assert second is None
    assert len(rows(world)) == 2           # the retry really was attempted


def test_a_retry_succeeds_once_egress_recovers(world, sent, egress_fails, monkeypatch):
    start(world)
    assert rows(world)[0].status == "failed"

    async def ok(room, quality, filepath):
        return "EG_recovered", None
    monkeypatch.setattr(lk, "start_recording", ok)

    assert start(world) is None
    live = [r for r in rows(world) if r.status == "recording"]
    assert len(live) == 1
    assert live[0].egress_id == "EG_recovered"
    assert live[0].enforced is True


# ── 1 & 4. the success path, and idempotency ───────────────────────────────────────────

def test_a_successful_start_is_active_immediately(world, sent, egress_works):
    assert start(world) is None

    (r,) = rows(world)
    assert r.status == "recording"
    assert r.enforced is True
    assert r.egress_id
    # One update envelope, carrying the running state — the console needs nothing else.
    updates = [e for e in sent if e[1] == "recording.update"]
    assert len(updates) == 1
    assert updates[0][2]["status"] == "recording"
    # …and no error was reported for a start that worked.
    assert not [e for e in sent if e[1] == "recording.error"]


def test_a_second_start_while_genuinely_recording_creates_no_second_egress(world, sent, egress_works):
    start(world)

    assert start(world) == "A recording is already running"
    assert len(rows(world)) == 1


# ── 6. End Event over a failed attempt ─────────────────────────────────────────────────

def test_stopping_after_a_failed_start_leaves_it_not_captured(world, sent, egress_fails):
    start(world)

    # Nothing is active, so the stop path has no row to finalise and must not invent one.
    asyncio.run(mod.dispatch(_ctx(world), "recording.stop", {}))

    (r,) = rows(world)
    assert r.status == "failed"
    assert r.enforced is False
    assert r.error == TWIRP_503
    assert r.file_url is None
