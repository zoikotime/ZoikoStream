"""Regression test for the live WebSocket + ZST-EC-001 ORG-010.

THE REGRESSION: `require_operational_org_access` is an HTTP-only dependency — it takes a
`Request` and resolves the caller through HTTPBearer. It was applied as a router-level
dependency to `live_router`, which carries the live WebSocket. FastAPI cannot construct
either for a WebSocket scope, so dependency resolution raised
`TypeError: HTTPBearer.__call__() missing 1 required positional argument: 'request'`,
answered as HTTP 500 *before the endpoint ran*. Every live socket failed, for every caller,
authorized or not — the console never got a snapshot, so `can_host` never arrived and Go Live
could not start.

The fix moves the check inside `live_socket`, calling the same policy core
(`services/org_state.blocked_reason`) so the rule is unchanged while the refusal can travel
as a WebSocket close code. These tests pin BOTH halves: the socket works again, AND a
restricted organization is still refused. Fixing the first by simply deleting the second
would have been a security regression dressed up as a bug fix.

Run: `pytest test_live_socket_org_state.py`
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

# app.main first: app/services/org.py imports engagement_score from broadcast while broadcast
# transitively imports org, so importing a service module first hits a pre-existing circular
# import. See test_moderator_retirement.py for the same note.
import app.main as main_module
from app.db import SessionLocal
from app.models import (
    ORG_STATE_ACTIVE,
    ORG_STATE_RESTRICTED,
    ORG_STATE_SUSPENDED,
    Event,
    EventAssignment,
    Organization,
    User,
)
from app.security import create_access_token
from app.services import org_state


@pytest.fixture
def live_event():
    """A real org + event + assigned host, plus an unassigned member of the same org and a
    host of a DIFFERENT org. Everything is deleted afterwards."""
    db = SessionLocal()
    made = {}
    try:
        org = Organization(name=f"ws-org-{uuid.uuid4().hex[:8]}", status=ORG_STATE_ACTIVE)
        other = Organization(name=f"ws-other-{uuid.uuid4().hex[:8]}", status=ORG_STATE_ACTIVE)
        db.add_all([org, other])
        db.flush()

        def mk(o, role):
            u = User(org_id=o.id, full_name=f"ws {role}", role=role, is_active=True,
                     email=f"ws-{role}-{uuid.uuid4().hex[:10]}@example.com",
                     username=f"ws{role}{uuid.uuid4().hex[:10]}", password_hash="x",
                     email_verified=True)
            db.add(u)
            return u

        # role="host", NOT org_admin: the EventAssignment below must be the only thing
        # granting broadcast control, same reasoning as e2e_fixtures.py.
        host = mk(org, "host")
        bystander = mk(org, "host")          # same org, no assignment
        outsider = mk(other, "host")         # different org entirely
        db.flush()

        ev = Event(org_id=org.id, created_by=host.id, title="WS regression event",
                   status="published")
        db.add(ev)
        db.flush()
        db.add(EventAssignment(event_id=ev.id, user_id=host.id, role="host"))
        db.commit()

        made = {
            "org_id": org.id, "other_org_id": other.id, "event_id": ev.id,
            "host_token": create_access_token(host, True),
            "bystander_token": create_access_token(bystander, True),
            "outsider_token": create_access_token(outsider, True),
        }
        yield made
    finally:
        if made:
            db.execute(delete(EventAssignment).where(EventAssignment.event_id == made["event_id"]))
            db.execute(delete(Event).where(Event.id == made["event_id"]))
            db.execute(delete(User).where(User.org_id.in_([made["org_id"], made["other_org_id"]])))
            db.execute(delete(Organization).where(
                Organization.id.in_([made["org_id"], made["other_org_id"]])))
            db.commit()
        db.close()


def _set_org_status(org_id, value):
    db = SessionLocal()
    try:
        org = db.get(Organization, org_id)
        org.status = value
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _fresh_bus_client():
    """services/bus.py caches ONE Redis client, created on whatever event loop first needed
    it. Another module in the same session that used asyncio.run() leaves that cached client
    bound to a loop which is now closed, and TestClient's socket then fails with
    "Event loop is closed" — a cross-module harness artefact, not an application fault (these
    tests pass 9/9 when this file runs alone). Dropping the cache lets each test's own loop
    create its own client.
    """
    from app.services import bus
    bus._redis = None
    yield
    bus._redis = None


def _url(event_id, token=None):
    base = f"/api/live/events/{event_id}/ws"
    return f"{base}?token={token}" if token else base


# ── the regression itself ─────────────────────────────────────────────────────────────────

def test_authorized_host_socket_opens_and_delivers_a_snapshot(live_event):
    """The exact case that returned HTTP 500. An authorized host must complete the handshake
    AND receive the opening snapshot — the frame that carries can_host, without which the
    console cannot offer Go Live."""
    client = TestClient(main_module.app)
    with client.websocket_connect(_url(live_event["event_id"], live_event["host_token"])) as ws:
        env = ws.receive_json()
        assert (env["channel"], env["type"]) == ("moderator", "snapshot")
        you = env["data"]["you"]
        assert you["can_host"] is True, "an assigned host must resolve to can_host"
        assert you["can_moderate"] is True
        assert env["data"]["event"] is not None, "the snapshot must carry the event"


def test_the_websocket_route_carries_no_http_only_dependency():
    """Guards the root cause directly rather than only its symptom: if an HTTP-only security
    dependency is ever attached to the router holding the WebSocket again, dependency
    resolution breaks for every caller before the endpoint runs. Asserting on the wiring
    means the next person adding a router-level gate finds out here."""
    gated = [r for r in main_module.app.routes
             if getattr(r, "dependencies", None) and "live" in str(getattr(r, "prefix", ""))]
    assert not gated, f"live_router must not carry router-level HTTP dependencies: {gated}"


# ── ORG-010 is still enforced (the half a naive fix would have dropped) ────────────────────

@pytest.mark.parametrize("state", [ORG_STATE_RESTRICTED, ORG_STATE_SUSPENDED])
def test_restricted_organization_is_refused_the_live_socket(live_event, state):
    """Running a live event is operational access, so a restricted or suspended tenant must
    not get the socket — with a policy-violation close code the client will not retry, not a
    500 and not a silent drop."""
    _set_org_status(live_event["org_id"], state)
    try:
        client = TestClient(main_module.app)
        with client.websocket_connect(_url(live_event["event_id"], live_event["host_token"])) as ws:
            frame = ws.receive()
        assert frame["type"] == "websocket.close", f"expected a close, got {frame['type']}"
        assert frame["code"] == 1008, f"expected 1008 policy violation, got {frame['code']}"
        assert state in (frame.get("reason") or "").lower()
    finally:
        _set_org_status(live_event["org_id"], ORG_STATE_ACTIVE)


def test_an_active_organization_is_not_affected_by_the_check(live_event):
    """The other side of the gate: the common case must be untouched."""
    db = SessionLocal()
    try:
        org = db.get(Organization, live_event["org_id"])
        assert org_state.blocked_reason(org, f"/api/live/events/{live_event['event_id']}/ws") is None
    finally:
        db.close()


# ── authentication / authorization are unchanged ───────────────────────────────────────────

def test_no_credentials_is_refused_not_accepted():
    client = TestClient(main_module.app)
    with client.websocket_connect(_url(uuid.uuid4())) as ws:
        frame = ws.receive()
    assert frame["type"] == "websocket.close"
    assert frame["code"] == 1008
    assert "session" in (frame.get("reason") or "").lower()


def test_a_garbage_token_is_refused():
    """An invalid token must be a deliberate policy close, not a retryable error — otherwise
    the browser retries an unusable credential forever (useEventStream FATAL_CODES)."""
    client = TestClient(main_module.app)
    with client.websocket_connect(_url(uuid.uuid4(), "not-a-jwt")) as ws:
        frame = ws.receive()
    assert frame["type"] == "websocket.close"
    assert frame["code"] == 1008


def test_a_host_from_another_organization_cannot_open_the_socket(live_event):
    """Org isolation: resolve_ctx returns None, so the socket is refused before any
    application data is sent."""
    client = TestClient(main_module.app)
    with client.websocket_connect(
            _url(live_event["event_id"], live_event["outsider_token"])) as ws:
        frame = ws.receive()
    assert frame["type"] == "websocket.close"
    assert frame["code"] == 1008


def test_an_unassigned_member_connects_but_holds_no_authority(live_event):
    """Object-level authorization. Being your org's "host" is not being THIS event's host: the
    socket opens (they are in the org) but the snapshot must deny both capabilities, which is
    what stops the console offering Go Live to someone who cannot perform it."""
    client = TestClient(main_module.app)
    with client.websocket_connect(
            _url(live_event["event_id"], live_event["bystander_token"])) as ws:
        env = ws.receive_json()
        assert (env["channel"], env["type"]) == ("moderator", "snapshot")
        you = env["data"]["you"]
        assert you["can_host"] is False
        assert you["can_moderate"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
