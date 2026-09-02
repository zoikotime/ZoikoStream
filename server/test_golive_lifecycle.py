"""Go Live lifecycle over the REAL live WebSocket: go live -> reconnect -> end.

NOT a mocked test. It opens an actual WebSocket against the actual ASGI app, sends the real
`broadcast.golive` / `broadcast.end` actions through the real `mod.dispatch` permission gate,
and asserts on real Postgres rows. Nothing about LiveKit, the bus, authorization or the
broadcast service is stubbed.

WHY THIS EXISTS ALONGSIDE THE BROWSER SUITE: client/e2e/live-streaming.spec.js verifies the
same lifecycle end to end with real media, and its media assertions PASS (host publish
confirmed, viewer receives real video+audio). What it cannot do on a developer machine is get
past the LIVE badge on a *freshly opened* page more than ~20s after go-live, because:

  * `presence["publishing"]` has exactly ONE writer — the LiveKit webhook handler in
    routers/live.py, fired by LiveKit Cloud calling back into this server;
  * a local backend on 127.0.0.1 is not reachable from LiveKit Cloud, so that callback never
    arrives and `publishing` stays 0;
  * broadcast.health_of() then reports "down", and after DEGRADE_GRACE_SECONDS the sampler
    marks Event.status "degraded";
  * the watch page's STATUS_LABEL maps only live/ended, so "degraded" renders as "Upcoming".

Media is genuinely flowing the whole time — the server simply never hears about it. That is an
environment limitation of running the media plane against Cloud while the control plane is on
localhost, not an application fault, so these tests assert the lifecycle through the control
plane, which is unaffected by it.

Run: `pytest test_golive_lifecycle.py`
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

# app.main first — see test_moderator_retirement.py for the pre-existing circular import.
import app.main as main_module
from app.db import SessionLocal
from app.models import (
    ORG_STATE_ACTIVE,
    BroadcastSession,
    Event,
    EventAssignment,
    LiveActivity,
    LiveRecording,
    Organization,
    User,
)
from app.security import create_access_token


@pytest.fixture(autouse=True)
def _fresh_bus_client():
    """Same cross-module Redis-client-per-event-loop artefact documented in
    test_live_socket_org_state.py."""
    from app.services import bus
    bus._redis = None
    yield
    bus._redis = None


@pytest.fixture
def host_event():
    """Mirrors server/e2e_fixtures.py exactly: platform role "host" (NOT org_admin, so the
    EventAssignment is the only thing granting broadcast control) and an event that passes
    the commercial go-live gate — published, public, no registration, and crucially NO
    assigned speaker, since golive_readiness blocks on a speaker who has not consented."""
    db = SessionLocal()
    made = {}
    try:
        org = Organization(name=f"gl-org-{uuid.uuid4().hex[:8]}", status=ORG_STATE_ACTIVE)
        db.add(org)
        db.flush()
        host = User(org_id=org.id, full_name="GL Host", role="host", is_active=True,
                    email=f"gl-host-{uuid.uuid4().hex[:10]}@example.com",
                    username=f"glhost{uuid.uuid4().hex[:10]}", password_hash="x",
                    email_verified=True)
        viewer = User(org_id=org.id, full_name="GL Viewer", role="viewer", is_active=True,
                      email=f"gl-view-{uuid.uuid4().hex[:10]}@example.com",
                      username=f"glview{uuid.uuid4().hex[:10]}", password_hash="x",
                      email_verified=True)
        db.add_all([host, viewer])
        db.flush()
        ev = Event(org_id=org.id, created_by=host.id, title="Go Live lifecycle test",
                   status="published", visibility="public", registration_required=False,
                   chat_enabled=True, qa_enabled=True, polls_enabled=True)
        db.add(ev)
        db.flush()
        db.add(EventAssignment(event_id=ev.id, user_id=host.id, role="host"))
        db.commit()
        made = {"org_id": org.id, "event_id": ev.id,
                "host_token": create_access_token(host, True),
                "viewer_token": create_access_token(viewer, True)}
        yield made
    finally:
        if made:
            eid, oid = made["event_id"], made["org_id"]
            db.execute(delete(LiveRecording).where(LiveRecording.event_id == eid))
            db.execute(delete(BroadcastSession).where(BroadcastSession.event_id == eid))
            db.execute(delete(EventAssignment).where(EventAssignment.event_id == eid))
            db.execute(delete(LiveActivity).where(LiveActivity.event_id == eid))
            db.execute(delete(Event).where(Event.id == eid))
            db.execute(delete(User).where(User.org_id == oid))
            db.execute(delete(Organization).where(Organization.id == oid))
            db.commit()
        db.close()


def _url(event_id, token):
    return f"/api/live/events/{event_id}/ws?token={token}"


def _drain_for(ws, channel, type_, budget=40):
    """Read frames until the wanted envelope arrives. The socket multiplexes many channels,
    so the answer to an action is rarely the very next frame."""
    for _ in range(budget):
        env = ws.receive_json()
        if env.get("channel") == channel and env.get("type") == type_:
            return env
    return None


def _db_state(event_id):
    db = SessionLocal()
    try:
        ev = db.get(Event, event_id)
        sessions = db.scalars(
            select(BroadcastSession).where(BroadcastSession.event_id == event_id)).all()
        return {
            "event_status": ev.status,
            "sessions": len(sessions),
            "open_sessions": sum(1 for s in sessions if s.ended_at is None),
            "live_sessions": sum(1 for s in sessions if s.status == "live"),
            # "active" = started and not yet stopped, the same shape the browser suite's
            # dbState() helper uses.
            "active_recordings": len(db.scalars(
                select(LiveRecording).where(LiveRecording.event_id == event_id,
                                            LiveRecording.status.in_(("recording", "paused")))
            ).all()),
        }
    finally:
        db.close()


# ── Phase 2: Go Live ──────────────────────────────────────────────────────────────────────

def test_host_goes_live_over_the_real_socket(host_event):
    """The full authorized path: snapshot -> can_host -> broadcast.golive -> DB is live.
    Also pins the reported bug that recording must NOT auto-start on go-live."""
    client = TestClient(main_module.app)
    with client.websocket_connect(_url(host_event["event_id"], host_event["host_token"])) as ws:
        snap = ws.receive_json()
        assert (snap["channel"], snap["type"]) == ("moderator", "snapshot")
        assert snap["data"]["you"]["can_host"] is True

        ws.send_json({"action": "broadcast.golive", "payload": {}})
        update = _drain_for(ws, "broadcast", "broadcast.update")
        assert update is not None, "no broadcast.update came back from broadcast.golive"
        assert update["data"]["status"] == "live"

    state = _db_state(host_event["event_id"])
    assert state["event_status"] == "live", "Event.status did not become live"
    assert state["live_sessions"] == 1, "expected exactly one live BroadcastSession"
    assert state["active_recordings"] == 0, "recording auto-started on Go Live"


def test_a_non_host_cannot_go_live(host_event):
    """The authorization boundary that matters most: a signed-in viewer in the SAME org, on
    the same event, must be refused broadcast control — and refused by the server, not by the
    UI hiding a button."""
    client = TestClient(main_module.app)
    with client.websocket_connect(_url(host_event["event_id"], host_event["viewer_token"])) as ws:
        snap = ws.receive_json()
        assert snap["data"]["you"]["can_host"] is False
        ws.send_json({"action": "broadcast.golive", "payload": {}})
        err = _drain_for(ws, "moderator", "error")
        assert err is not None, "viewer's broadcast.golive was not refused"
        assert "host" in err["data"]["message"].lower()

    assert _db_state(host_event["event_id"])["event_status"] != "live"


# ── Phase 7: reconnect / refresh ──────────────────────────────────────────────────────────

def test_reconnecting_restores_state_without_a_duplicate_session(host_event):
    """What a browser refresh does: drop the socket, open a new one. The rebuilt snapshot must
    carry the SAME live broadcast and the same authority, and going live again must NOT open a
    second session (the guard against duplicate/stale publishing sessions)."""
    client = TestClient(main_module.app)
    with client.websocket_connect(_url(host_event["event_id"], host_event["host_token"])) as ws:
        ws.receive_json()
        ws.send_json({"action": "broadcast.golive", "payload": {}})
        assert _drain_for(ws, "broadcast", "broadcast.update") is not None
    after_first = _db_state(host_event["event_id"])
    assert after_first["event_status"] == "live"

    # Reconnect, exactly as a refreshed page would.
    with client.websocket_connect(_url(host_event["event_id"], host_event["host_token"])) as ws:
        snap = ws.receive_json()
        assert (snap["channel"], snap["type"]) == ("moderator", "snapshot")
        assert snap["data"]["you"]["can_host"] is True, "authority was not recalculated on reconnect"
        assert snap["data"]["broadcast"]["status"] == "live", \
            "the rebuilt snapshot lost the live broadcast state"
        # A second go-live on an already-live event must be idempotent, not a new session.
        ws.send_json({"action": "broadcast.golive", "payload": {}})
        _drain_for(ws, "broadcast", "broadcast.update")

    after_reconnect = _db_state(host_event["event_id"])
    assert after_reconnect["open_sessions"] == 1, \
        f"reconnect created a duplicate session: {after_reconnect}"
    assert after_reconnect["event_status"] == "live"


# ── Phase 8: End Event ────────────────────────────────────────────────────────────────────

def test_end_event_closes_everything(host_event):
    """End Event must transition the event, close the session and leave nothing active — the
    stale-session/stale-recording check."""
    client = TestClient(main_module.app)
    with client.websocket_connect(_url(host_event["event_id"], host_event["host_token"])) as ws:
        ws.receive_json()
        ws.send_json({"action": "broadcast.golive", "payload": {}})
        assert _drain_for(ws, "broadcast", "broadcast.update") is not None
        assert _db_state(host_event["event_id"])["event_status"] == "live"

        ws.send_json({"action": "broadcast.end", "payload": {}})
        ended = _drain_for(ws, "broadcast", "broadcast.update")
        assert ended is not None, "no broadcast.update came back from broadcast.end"
        assert ended["data"]["status"] == "ended"

    final = _db_state(host_event["event_id"])
    assert final["event_status"] == "ended", f"event did not end: {final}"
    assert final["open_sessions"] == 0, "a BroadcastSession remained open after End Event"
    assert final["active_recordings"] == 0, "a recording remained active after End Event"


def test_a_non_host_cannot_end_the_broadcast(host_event):
    """The other half of the boundary, on the action that destroys a live event."""
    client = TestClient(main_module.app)
    with client.websocket_connect(_url(host_event["event_id"], host_event["host_token"])) as ws:
        ws.receive_json()
        ws.send_json({"action": "broadcast.golive", "payload": {}})
        assert _drain_for(ws, "broadcast", "broadcast.update") is not None

    with client.websocket_connect(_url(host_event["event_id"], host_event["viewer_token"])) as ws:
        ws.receive_json()
        ws.send_json({"action": "broadcast.end", "payload": {}})
        err = _drain_for(ws, "moderator", "error")
        assert err is not None, "viewer's broadcast.end was not refused"

    assert _db_state(host_event["event_id"])["event_status"] == "live", \
        "a viewer managed to end the broadcast"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
