"""Live presence survives a second tab, a reconnect and a crashed worker's leftovers.

Production report: the watch page read "Watching now 0" during a live event, and the Producer
Console showed LIVE VIEWERS 0 and CONNECTED 0 ("incl. staff") while the host was plainly
connected. Presence is ONE record per identity, and every socket's close handler deleted it
outright — so closing a second tab, or the old socket of a reconnect closing after the new
one had registered, removed someone who was still connected. Identities are per user: a host
with the console open beside the watch page is one identity with two sockets. The record now
goes only when the identity's LAST live connection closes (services/bus.py, "live connections
per identity"; routers/live.py).

Real sockets through the real endpoint against the isolated test database, on the in-process
bus (no Redis), as the other live tests run. Nothing in sys.modules is replaced.
"""
import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import app.main as main_module
from app.db import SessionLocal
from app.models import ORG_STATE_ACTIVE, Event, EventAssignment, Organization, User
from app.routers import live as live_router
from app.security import create_access_token
from app.services import bus


@pytest.fixture(autouse=True)
def in_process_bus():
    url = bus.settings.REDIS_URL
    bus.settings.REDIS_URL = ""
    try:
        yield
    finally:
        bus.settings.REDIS_URL = url


@pytest.fixture
def live_event():
    db = SessionLocal()
    made = {}
    try:
        org = Organization(name=f"pres-org-{uuid.uuid4().hex[:8]}", status=ORG_STATE_ACTIVE)
        db.add(org)
        db.flush()
        host = User(org_id=org.id, full_name="Pres Host", role="host", is_active=True,
                    email=f"pres-{uuid.uuid4().hex[:10]}@example.com",
                    username=f"pres{uuid.uuid4().hex[:10]}", password_hash="x", email_verified=True)
        db.add(host)
        db.flush()
        ev = Event(org_id=org.id, created_by=host.id, title="Presence", status="published")
        db.add(ev)
        db.flush()
        db.add(EventAssignment(event_id=ev.id, user_id=host.id, role="host"))
        db.commit()
        made = {"org": org.id, "user": host.id, "event": ev.id,
                "token": create_access_token(host, True)}
        yield made
    finally:
        if made:
            asyncio.run(bus.presence_clear(made["event"]))
            from app.models.auth_session import AuthSession
            db.execute(delete(AuthSession).where(AuthSession.user_id == made["user"]))
            db.execute(delete(EventAssignment).where(EventAssignment.event_id == made["event"]))
            db.execute(delete(Event).where(Event.id == made["event"]))
            db.execute(delete(User).where(User.id == made["user"]))
            db.execute(delete(Organization).where(Organization.id == made["org"]))
            db.commit()
        db.close()


def _url(ev):
    return f"/api/live/events/{ev['event']}/ws?token={ev['token']}"


def _present(event_id):
    return [p["identity"] for p in asyncio.run(bus.presence_all(event_id))]


def _wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _open(client, ev):
    ws = client.websocket_connect(_url(ev)).__enter__()
    env = ws.receive_json()
    assert (env["channel"], env["type"]) == ("moderator", "snapshot")
    return ws, env["data"]["you"]["identity"]


def test_closing_one_of_two_tabs_keeps_the_person_present(live_event):
    client = TestClient(main_module.app)
    ws1, me = _open(client, live_event)
    ws2, again = _open(client, live_event)
    assert me == again                                   # one person, two sockets
    assert _present(live_event["event"]) == [me]         # counted once

    ws1.close()
    # Give the server's close handler time to run — it is what used to remove them.
    time.sleep(0.5)
    assert _present(live_event["event"]) == [me], "a still-connected tab was removed from presence"

    ws2.close()
    assert _wait_for(lambda: _present(live_event["event"]) == []), "the last tab closing must remove them"


def test_a_reconnect_that_lands_before_the_old_socket_closes(live_event):
    client = TestClient(main_module.app)
    old, me = _open(client, live_event)
    new, _ = _open(client, live_event)                   # the reconnect is already back…
    old.close()                                          # …when the old socket's close lands
    time.sleep(0.5)
    assert _present(live_event["event"]) == [me]
    new.close()
    assert _wait_for(lambda: _present(live_event["event"]) == [])


def test_a_single_socket_still_leaves_normally(live_event):
    client = TestClient(main_module.app)
    ws, me = _open(client, live_event)
    assert _present(live_event["event"]) == [me]
    ws.close()
    assert _wait_for(lambda: _present(live_event["event"]) == [])


def _sessions(event_id, identity):
    rec = bus._sessions_memory.get(str(event_id), {}).get(identity) or {}
    return rec.get("sessions", [])


def test_a_second_tab_closing_does_not_split_the_viewing_session(live_event):
    """The Stats tab's SESSIONS ADMITTED and REJOIN RATE count join-to-leave intervals per
    identity (services/viewing_sessions.py: rejoins = Σ(sessions per identity − 1)). Closing a
    second tab used to close the identity's interval while it was still connected, so its next
    presence update opened another one — one viewer read as six sessions and an 83% rejoin
    rate. The interval now stays open until the last socket leaves."""
    client = TestClient(main_module.app)
    ws1, me = _open(client, live_event)
    ws2, _ = _open(client, live_event)
    ws1.close()
    time.sleep(0.5)
    sessions = _sessions(live_event["event"], me)
    assert len(sessions) == 1 and sessions[0]["left_at"] is None, sessions
    ws2.close()
    assert _wait_for(lambda: _sessions(live_event["event"], me)[-1]["left_at"] is not None)
    assert len(_sessions(live_event["event"], me)) == 1


def test_a_real_leave_and_return_is_still_a_rejoin(live_event):
    client = TestClient(main_module.app)
    ws, me = _open(client, live_event)
    ws.close()
    assert _wait_for(lambda: _sessions(live_event["event"], me)[-1]["left_at"] is not None)
    again, _ = _open(client, live_event)
    assert len(_sessions(live_event["event"], me)) == 2      # came back: a second session
    again.close()


# ── the connection registry itself ─────────────────────────────────────────────────────────

def test_connections_are_counted_per_identity_and_event():
    ev, other = str(uuid.uuid4()), str(uuid.uuid4())
    run = asyncio.run
    run(bus.connection_open(ev, "u1", "a"))
    run(bus.connection_open(ev, "u1", "b"))
    run(bus.connection_open(ev, "u2", "c"))
    run(bus.connection_open(other, "u1", "d"))
    assert run(bus.connection_count(ev, "u1")) == 2
    assert run(bus.connection_close(ev, "u1", "a")) == 1
    assert run(bus.connection_close(ev, "u1", "a")) == 1      # closing twice is harmless
    assert run(bus.connection_close(ev, "u1", "b")) == 0
    assert run(bus.connection_count(ev, "u2")) == 1           # other people untouched
    assert run(bus.connection_count(other, "u1")) == 1        # other events untouched


def test_a_connection_not_refreshed_in_time_no_longer_counts(monkeypatch):
    ev = str(uuid.uuid4())
    run = asyncio.run
    run(bus.connection_open(ev, "u1", "dead"))                # a worker that crashed
    run(bus.connection_open(ev, "u1", "live"))
    later = time.time() + bus.CONN_STALE_SECONDS + 5
    run(bus.connection_touch(ev, "u1", "live"))               # still pinging
    monkeypatch.setattr(bus.time, "time", lambda: later)
    run(bus.connection_touch(ev, "u1", "live"))
    assert run(bus.connection_count(ev, "u1")) == 1           # only the live one is left


def test_a_socket_the_server_still_holds_is_never_stale():
    """The server reaps a socket after IDLE_TIMEOUT without a frame; until then it is live, even
    when a hidden tab's throttled timers ping only once a minute. A shorter staleness window
    would let a sibling tab's close (or the re-check) remove someone still connected."""
    assert bus.CONN_STALE_SECONDS > live_router.IDLE_TIMEOUT


def test_a_crashed_workers_leftover_does_not_keep_someone_present(monkeypatch):
    """A socket closes while its identity still lists a connection from a worker that died
    without running its close handler. After the staleness window the re-check removes the
    presence instead of leaving the person counted for the rest of the event."""
    ev = str(uuid.uuid4())
    run = asyncio.run
    monkeypatch.setattr(bus, "CONN_STALE_SECONDS", 0.2)
    run(bus.presence_upsert(ev, "u1", {"name": "Ghost", "role": "viewer"}))
    run(bus.connection_open(ev, "u1", "dead"))
    run(bus.connection_open(ev, "u1", "closing"))
    assert run(bus.connection_close(ev, "u1", "closing")) == 1   # the dead one still listed
    run(live_router._drop_if_abandoned(ev, "u1"))                # waits out the window
    assert [p["identity"] for p in run(bus.presence_all(ev))] == []


def test_the_recheck_leaves_a_genuinely_live_connection_alone(monkeypatch):
    ev = str(uuid.uuid4())
    run = asyncio.run
    monkeypatch.setattr(bus, "CONN_STALE_SECONDS", 0.2)
    run(bus.presence_upsert(ev, "u1", {"name": "Still here", "role": "viewer"}))
    run(bus.connection_open(ev, "u1", "live"))

    async def recheck_while_pinging():
        task = asyncio.create_task(live_router._drop_if_abandoned(ev, "u1"))
        while not task.done():                       # a live tab keeps pinging throughout
            await asyncio.sleep(0.1)
            await bus.connection_touch(ev, "u1", "live")
        await task

    run(recheck_while_pinging())
    assert [p["identity"] for p in run(bus.presence_all(ev))] == ["u1"]
    run(bus.presence_clear(ev))
