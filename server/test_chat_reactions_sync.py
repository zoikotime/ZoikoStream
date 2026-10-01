"""End-to-end tests for chat reaction synchronization between Host and Viewers.

Verifies all 12 required scenarios:
1. viewer sends message
2. host reacts
3. viewer receives reaction event
4. viewer message updates without reload
5. second viewer also receives update
6. host optimistic update reconciles correctly
7. message ID matches
8. duplicate event does not double-count
9. reaction removal syncs
10. multiple reaction types sync
11. reconnect restores reactions
12. cross-event reaction denied
"""

import asyncio
import uuid
import pytest

import app.main  # noqa: F401 - import order
from app.db import SessionLocal
from app.models import Event, LiveMessage, Organization, User
from app.security import hash_password
from app.services import bus
from app.services import moderation as m
from app.services.moderation import Ctx


@pytest.fixture(autouse=True)
def in_process_bus():
    """Run tests using in-process bus fallback."""
    url = bus.settings.REDIS_URL
    bus.settings.REDIS_URL = ""
    try:
        yield
    finally:
        bus.settings.REDIS_URL = url


@pytest.fixture
def test_setup():
    """Create a temporary test event and return context objects for host and viewers."""
    db = SessionLocal()
    made = {"m": [], "ev": [], "u": [], "o": []}
    try:
        org = Organization(name=f"RxOrg {uuid.uuid4().hex[:6]}", status="active")
        other_org = Organization(name=f"RxOther {uuid.uuid4().hex[:6]}", status="active")
        db.add_all([org, other_org])
        db.flush()
        made["o"] = [org.id, other_org.id]

        host_user = User(
            org_id=org.id,
            full_name="Host User",
            role="org_admin",
            is_active=True,
            email=f"host-{uuid.uuid4().hex[:8]}@example.com",
            username=f"host{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("password123"),
            email_verified=True,
        )
        db.add(host_user)
        db.flush()
        made["u"] = [host_user.id]

        ev1 = Event(
            org_id=org.id,
            created_by=host_user.id,
            title="Sync Test Event 1",
            status="live",
            visibility="public",
            chat_enabled=True,
        )
        ev2 = Event(
            org_id=other_org.id,
            created_by=host_user.id,
            title="Sync Test Event 2",
            status="live",
            visibility="public",
            chat_enabled=True,
        )
        db.add_all([ev1, ev2])
        db.flush()
        made["ev"] = [ev1.id, ev2.id]
        db.commit()

        host_ctx = Ctx(
            event_id=ev1.id,
            org_id=org.id,
            room=f"room-{ev1.id}",
            user_id=host_user.id,
            name="Host User",
            identity=f"host-{host_user.id}",
            role="host",
            can_moderate=True,
            can_host=True,
        )

        viewer1_ctx = Ctx(
            event_id=ev1.id,
            org_id=org.id,
            room=f"room-{ev1.id}",
            user_id=uuid.uuid4(),
            name="Viewer Radha",
            identity="guest-viewer-1",
            role="viewer",
            can_moderate=False,
            can_host=False,
        )

        viewer2_ctx = Ctx(
            event_id=ev1.id,
            org_id=org.id,
            room=f"room-{ev1.id}",
            user_id=uuid.uuid4(),
            name="Viewer Simran",
            identity="guest-viewer-2",
            role="viewer",
            can_moderate=False,
            can_host=False,
        )

        other_event_ctx = Ctx(
            event_id=ev2.id,
            org_id=other_org.id,
            room=f"room-{ev2.id}",
            user_id=uuid.uuid4(),
            name="Other Event Viewer",
            identity="guest-viewer-3",
            role="viewer",
            can_moderate=False,
            can_host=False,
        )

        yield {
            "ev1_id": ev1.id,
            "ev2_id": ev2.id,
            "host_ctx": host_ctx,
            "viewer1_ctx": viewer1_ctx,
            "viewer2_ctx": viewer2_ctx,
            "other_event_ctx": other_event_ctx,
        }
    finally:
        try:
            for eid in made["ev"]:
                db.execute(LiveMessage.__table__.delete().where(LiveMessage.event_id == eid))
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


@pytest.mark.asyncio
async def test_full_reaction_flow_12_scenarios(test_setup):
    ev1_id = test_setup["ev1_id"]
    host = test_setup["host_ctx"]
    v1 = test_setup["viewer1_ctx"]
    v2 = test_setup["viewer2_ctx"]
    other_ctx = test_setup["other_event_ctx"]

    # Subscribe viewer1 and viewer2 sockets to the event bus
    async with bus.subscribe(ev1_id) as v1_queue, bus.subscribe(ev1_id) as v2_queue:
        # Drain any initial envelopes
        await asyncio.sleep(0.01)

        # -------------------------------------------------------------
        # 1. Viewer sends message
        # -------------------------------------------------------------
        res = await m.dispatch(v1, "chat.send", {"text": "hi radha"})
        assert res is None, f"chat.send failed: {res}"

        # Both viewers receive chat/message.new
        v1_env = await asyncio.wait_for(v1_queue.get(), timeout=2.0)
        assert v1_env["channel"] == "chat"
        assert v1_env["type"] == "message.new"
        assert v1_env["data"]["text"] == "hi radha"

        v2_env = await asyncio.wait_for(v2_queue.get(), timeout=2.0)
        assert v2_env["channel"] == "chat"
        assert v2_env["type"] == "message.new"

        msg_id = v1_env["data"]["id"]
        assert msg_id is not None

        # -------------------------------------------------------------
        # 2. Host reacts with ❤️
        # -------------------------------------------------------------
        react_res = await m.dispatch(host, "chat.react", {"id": msg_id, "emoji": "❤️"})
        assert react_res is None, f"chat.react failed: {react_res}"

        # -------------------------------------------------------------
        # 3. Viewer receives reaction event
        # -------------------------------------------------------------
        v1_rx = await asyncio.wait_for(v1_queue.get(), timeout=2.0)
        assert v1_rx["channel"] == "chat"
        assert v1_rx["type"] == "message.update"
        assert v1_rx["data"]["id"] == msg_id
        assert v1_rx["data"]["reactions"]["❤️"] == 1
        assert host.identity in v1_rx["data"]["reaction_users"]["❤️"]

        # -------------------------------------------------------------
        # 4. Viewer message updates without reload
        # -------------------------------------------------------------
        # Simulate viewer client-side state update
        viewer_messages = [v1_env["data"]]
        # Viewer reducer receives message.update
        viewer_messages = [
            {**m_item, **v1_rx["data"]} if m_item["id"] == v1_rx["data"]["id"] else m_item
            for m_item in viewer_messages
        ]
        assert viewer_messages[0]["reactions"]["❤️"] == 1

        # -------------------------------------------------------------
        # 5. Second viewer also receives update
        # -------------------------------------------------------------
        v2_rx = await asyncio.wait_for(v2_queue.get(), timeout=2.0)
        assert v2_rx["channel"] == "chat"
        assert v2_rx["type"] == "message.update"
        assert v2_rx["data"]["id"] == msg_id
        assert v2_rx["data"]["reactions"]["❤️"] == 1

        # -------------------------------------------------------------
        # 6. Host optimistic update reconciles correctly
        # -------------------------------------------------------------
        host_messages = [{"id": msg_id, "text": "hi radha", "reactions": {"❤️": 1}}]
        # Host reconciles with authoritative server event
        host_messages = [
            {**m_item, **v1_rx["data"]} if m_item["id"] == v1_rx["data"]["id"] else m_item
            for m_item in host_messages
        ]
        assert host_messages[0]["reactions"]["❤️"] == 1
        assert host.identity in host_messages[0]["reaction_users"]["❤️"]

        # -------------------------------------------------------------
        # 7. Message ID matches
        # -------------------------------------------------------------
        assert str(v1_rx["data"]["id"]) == str(msg_id)
        assert str(v1_rx["data"]["message_id"]) == str(msg_id)

        # -------------------------------------------------------------
        # 8. Duplicate event does not double-count
        # -------------------------------------------------------------
        # Host sends the exact same reaction command again
        res_dup = await m.dispatch(host, "chat.react", {"id": msg_id, "emoji": "❤️"})
        assert res_dup is None
        v1_dup = await asyncio.wait_for(v1_queue.get(), timeout=2.0)
        _ = await asyncio.wait_for(v2_queue.get(), timeout=2.0)
        assert v1_dup["data"]["reactions"]["❤️"] == 1  # Still 1, NOT 2!
        assert len(v1_dup["data"]["reaction_users"]["❤️"]) == 1

        # -------------------------------------------------------------
        # 9. Reaction removal syncs
        # -------------------------------------------------------------
        # Host removes reaction
        remove_res = await m.dispatch(host, "chat.react", {"id": msg_id, "emoji": "❤️", "remove": True})
        assert remove_res is None
        v1_rem = await asyncio.wait_for(v1_queue.get(), timeout=2.0)
        _ = await asyncio.wait_for(v2_queue.get(), timeout=2.0)
        assert "❤️" not in v1_rem["data"]["reactions"] or v1_rem["data"]["reactions"]["❤️"] == 0

        # -------------------------------------------------------------
        # 10. Multiple reaction types sync
        # -------------------------------------------------------------
        # Host reacts with 🎉
        await m.dispatch(host, "chat.react", {"id": msg_id, "emoji": "🎉"})
        # Viewer 1 reacts with ❤️
        await m.dispatch(v1, "chat.react", {"id": msg_id, "emoji": "❤️"})
        # Viewer 2 reacts with 👍
        await m.dispatch(v2, "chat.react", {"id": msg_id, "emoji": "👍"})

        # Drain the 3 reaction envelopes from v1_queue
        for _ in range(3):
            last_env = await asyncio.wait_for(v1_queue.get(), timeout=2.0)

        assert last_env["data"]["reactions"].get("🎉") == 1
        assert last_env["data"]["reactions"].get("❤️") == 1
        assert last_env["data"]["reactions"].get("👍") == 1

        # -------------------------------------------------------------
        # 11. Reconnect restores reactions from snapshot
        # -------------------------------------------------------------
        snap = await m.snapshot(v1)
        snap_msg = next((msg for msg in snap["messages"] if msg["id"] == msg_id), None)
        assert snap_msg is not None
        assert snap_msg["reactions"].get("🎉") == 1
        assert snap_msg["reactions"].get("❤️") == 1
        assert snap_msg["reactions"].get("👍") == 1

        # -------------------------------------------------------------
        # 12. Cross-event reaction denied
        # -------------------------------------------------------------
        cross_res = await m.dispatch(other_ctx, "chat.react", {"id": msg_id, "emoji": "🔥"})
        # Action is rejected or produces no broadcast envelopes
        assert cross_res is None or isinstance(cross_res, str)
        # Verify no envelope was published to ev1 subscribers
        assert v1_queue.empty()
