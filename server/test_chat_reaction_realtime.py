"""Chat reactions over REAL live sockets: host and viewers on the same event see the same
confirmed reaction state, live, with no refresh.

test_chat_reactions_sync.py proves the dispatcher. This proves the wire: two or three actual
WebSocket connections (routers/live.py), the bus fan-out, and the persisted row a refresh
reads back.

    1-4  viewer sends; host reacts ❤️; the viewer's socket gets the update with ❤️ = 1
    5-6  host removes; the viewer's socket gets ❤️ gone
    7-8  host and a second viewer both react; every socket reports ❤️ = 2
    9    a fresh connection (refresh) loads the persisted reaction in its snapshot
    10   a repeated add is idempotent: the count stays 2
    11   another event's sockets never receive it, and a message id from event A cannot be
         reacted to from event B
"""
import uuid
from datetime import datetime, timedelta, timezone

import anyio.from_thread
import pytest
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import (AuditLog, Event, EventAssignment, EventRegistration, LiveActivity, LiveMessage,
                        Organization, User)
from app.security import create_access_token, create_registration_token, hash_password
from app.services import bus

HEART = "❤️"


@pytest.fixture(autouse=True)
def _in_process_bus(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    yield


@pytest.fixture
def world():
    db = SessionLocal()
    org = Organization(name=f"RxLive {uuid.uuid4().hex[:6]}", status="active")
    db.add(org)
    db.flush()
    host = User(org_id=org.id, full_name="Host", role="org_admin", is_active=True,
                email=f"rxl-{uuid.uuid4().hex[:8]}@example.com", username=f"rxl{uuid.uuid4().hex[:8]}",
                password_hash=hash_password("x"), email_verified=True)
    db.add(host)
    db.flush()
    now = datetime.now(timezone.utc)

    def event(title):
        ev = Event(org_id=org.id, created_by=host.id, title=title, status="live", visibility="public",
                   chat_enabled=True, start_time=now - timedelta(minutes=5), end_time=now + timedelta(hours=1))
        db.add(ev)
        db.flush()
        return ev

    a, b = event("Event A"), event("Event B")

    def registrant(ev, name):
        r = EventRegistration(event_id=ev.id, name=name, email=f"{uuid.uuid4().hex[:8]}@example.com")
        db.add(r)
        db.flush()
        return r

    v1, v2, vb = registrant(a, "Simran"), registrant(a, "Radha"), registrant(b, "Other")
    db.commit()
    ns = type("W", (), {"db": db, "org": org, "host": host, "a": a, "b": b, "v1": v1, "v2": v2, "vb": vb})()
    try:
        yield ns
    finally:
        db.rollback()
        ids = [a.id, b.id]
        for model in (LiveActivity, LiveMessage, EventRegistration, EventAssignment):
            db.query(model).filter(model.event_id.in_(ids)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.org_id == org.id).delete(synchronize_session=False)
        db.query(Event).filter(Event.id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id == host.id).delete(synchronize_session=False)
        db.query(Organization).filter(Organization.id == org.id).delete(synchronize_session=False)
        db.commit()
        db.close()


@pytest.fixture
def c():
    """ONE event loop for every socket, as in production (one loop per worker). A plain
    TestClient gives each websocket_connect its own portal thread and loop, and the
    in-process bus hands envelopes between sockets through asyncio queues that belong to a
    single loop: across loops a waiting reader is not reliably woken, which is a property of
    the harness, not of the product. No lifespan is run, so no background tickers start."""
    client = TestClient(m.app)
    with anyio.from_thread.start_blocking_portal() as portal:
        client.portal = portal
        yield client


def host_url(w, ev):
    return f"/api/live/events/{ev.id}/ws?token={create_access_token(w.host, remember=False)}"


def viewer_url(ev, reg):
    return f"/api/live/events/{ev.id}/ws?reg={create_registration_token(reg)}"


def next_of(ws, channel, type_, limit=60):
    """The next envelope of this kind (other traffic — presence, activity — is skipped)."""
    for _ in range(limit):
        env = ws.receive_json()
        if env.get("channel") == channel and env.get("type") == type_:
            return env
        if env.get("type") == "error":
            raise AssertionError(f"server refused: {env.get('data')}")
    raise AssertionError(f"no {channel}/{type_} envelope")


def nothing_before_pong(ws, channel, type_):
    """True when no channel/type envelope arrives before the reply to a ping we send now —
    a negative check that cannot hang."""
    ws.send_json({"action": "ping", "t": 1})
    for _ in range(60):
        env = ws.receive_json()
        if env.get("type") == "pong":
            return True
        if env.get("channel") == channel and env.get("type") == type_:
            return False
    raise AssertionError("no pong")


def react(ws, message_id, emoji=HEART, remove=False):
    ws.send_json({"action": "chat.react", "payload": {
        "id": message_id, "message_id": message_id, "emoji": emoji, "reaction": emoji,
        "remove": remove, "action": "remove" if remove else "add"}})


def test_host_reactions_reach_every_viewer_live_and_survive_a_refresh(world, c):
    with c.websocket_connect(host_url(world, world.a)) as hws, \
         c.websocket_connect(viewer_url(world.a, world.v1)) as v1, \
         c.websocket_connect(viewer_url(world.a, world.v2)) as v2, \
         c.websocket_connect(viewer_url(world.b, world.vb)) as other:
        for ws in (hws, v1, v2, other):
            next_of(ws, "moderator", "snapshot")

        # 1. viewer sends
        v1.send_json({"action": "chat.send", "payload": {"text": "hi radha"}})
        mid = next_of(v1, "chat", "message.new")["data"]["id"]
        for ws in (hws, v2):
            assert next_of(ws, "chat", "message.new")["data"]["id"] == mid

        # 2-4. host reacts ❤️ -> the viewers' sockets get the confirmed state
        react(hws, mid)
        host_identity = str(world.host.id)
        for ws in (hws, v1, v2):
            upd = next_of(ws, "chat", "message.update")["data"]
            assert upd["id"] == upd["message_id"] == mid
            assert upd["event_id"] == str(world.a.id)
            assert upd["reactions"] == {HEART: 1} and upd["reaction_count"] == 1
            assert upd["reaction_users"] == {HEART: [host_identity]}
            assert upd["text"] == "hi radha" and upd["name"] == "Simran"   # the message is intact
            assert upd["updated_at"]

        # 11a. another event's socket hears nothing about it
        assert nothing_before_pong(other, "chat", "message.update")

        # 5-6. host removes -> gone everywhere
        react(hws, mid, remove=True)
        for ws in (hws, v1, v2):
            upd = next_of(ws, "chat", "message.update")["data"]
            assert upd["reactions"] == {} and upd["reaction_count"] == 0

        # 7-8. host and a second viewer react -> 2 on every socket
        react(hws, mid)
        for ws in (hws, v1, v2):
            assert next_of(ws, "chat", "message.update")["data"]["reactions"] == {HEART: 1}
        react(v2, mid)
        for ws in (v2, hws, v1):        # the reactor first: a refusal reaches only its socket
            upd = next_of(ws, "chat", "message.update")["data"]
            assert upd["reactions"] == {HEART: 2}
            assert len(upd["reaction_users"][HEART]) == 2

        # 10. the same add again is idempotent: still 2, never 3
        react(v2, mid)
        for ws in (v2, hws, v1):
            assert next_of(ws, "chat", "message.update")["data"]["reactions"] == {HEART: 2}

        # different emojis are independent
        react(v1, mid, emoji="🎉")
        upd = next_of(hws, "chat", "message.update")["data"]
        assert upd["reactions"] == {HEART: 2, "🎉": 1}

    # 9. a refresh reads it back from the database
    with c.websocket_connect(viewer_url(world.a, world.v1)) as again:
        snap = next_of(again, "moderator", "snapshot")["data"]
        (msg,) = [x for x in snap["messages"] if x["id"] == mid]
        assert msg["reactions"] == {HEART: 2, "🎉": 1}
    world.db.expire_all()
    row = world.db.get(LiveMessage, uuid.UUID(mid))
    assert row.reactions == {HEART: 2, "🎉": 1}


def test_a_message_from_one_event_cannot_be_reacted_to_from_another(world, c):
    with c.websocket_connect(viewer_url(world.a, world.v1)) as v1, \
         c.websocket_connect(viewer_url(world.b, world.vb)) as other:
        next_of(v1, "moderator", "snapshot")
        next_of(other, "moderator", "snapshot")
        v1.send_json({"action": "chat.send", "payload": {"text": "only in A"}})
        mid = next_of(v1, "chat", "message.new")["data"]["id"]
        react(other, mid)                                   # event B socket, event A message
        assert nothing_before_pong(other, "chat", "message.update")
        assert nothing_before_pong(v1, "chat", "message.update")
    world.db.expire_all()
    assert not world.db.get(LiveMessage, uuid.UUID(mid)).reactions
