"""Self-check for the live moderation console: content detection, permission table,
option/date parsing, bus fan-out + presence, and the WebSocket loop itself.
Pure logic + in-process bus + a stubbed DB boundary — no database and no Redis needed.
Run: `python test_moderation.py` (or pytest)."""
import asyncio
import types
import uuid

from app.services import bus
from app.services import moderation as m


# ── automatic content detection ───────────────────────────────────────────────

def test_profanity():
    assert "profanity" in m.flag_text("this shit is broken")
    assert m.flag_text("this is broken") == []


def test_links_but_not_js_filenames():
    assert "link" in m.flag_text("slides at https://zoikostream.dev/summit")
    assert "link" in m.flag_text("go to spam-link.example now")
    # The TLD allowlist exists for exactly this: a tech chat says "React.js" constantly.
    assert m.flag_text("we use React.js and Node.js") == []


def test_spam_shouting_and_runs():
    assert "spam" in m.flag_text("FREE MONEY CLICK NOW")          # 4 all-caps words
    assert "spam" in m.flag_text("sooooooooo good")               # character run
    assert "spam" not in m.flag_text("The API is GA now")         # 2 short caps words is normal
    # A lowercase url must not dilute the caps ratio away.
    assert "spam" in m.flag_text("BUY CHEAP FOLLOWERS >> spam-link.example")


def test_duplicate_ignores_case_and_punctuation():
    assert "duplicate" in m.flag_text("Hello There!!", [m.normalize("hello   there")])
    assert "duplicate" not in m.flag_text("Hello There", [m.normalize("something else")])


# ── input coercion ────────────────────────────────────────────────────────────

def test_clean_options():
    assert m._clean_options(["A", "", "  B  "]) == [
        {"label": "A", "votes": 0}, {"label": "B", "votes": 0}]
    # Existing polls round-trip their vote counts instead of being reset by an edit.
    assert m._clean_options([{"label": "C", "votes": 4}]) == [{"label": "C", "votes": 4}]
    assert len(m._clean_options([f"opt{i}" for i in range(40)])) == 10   # capped


def test_editing_a_live_poll_keeps_its_votes():
    """The poll editor sends labels only. If the merge broke, correcting a typo mid-vote
    would zero every count already cast."""
    existing = [{"label": "Yes", "votes": 40}, {"label": "No", "votes": 12}]
    merged = m._merge_votes(existing, m._clean_options(["Yes", "No", "Maybe"]))
    assert merged == [{"label": "Yes", "votes": 40}, {"label": "No", "votes": 12},
                      {"label": "Maybe", "votes": 0}]
    # A renamed option is a new option — it must not inherit a stranger's votes.
    assert m._merge_votes(existing, m._clean_options(["Absolutely"])) == [
        {"label": "Absolutely", "votes": 0}]


def test_parse_dt():
    assert m._parse_dt("2026-07-29T10:00:00Z").tzinfo is not None
    assert m._parse_dt("2026-07-29T10:00:00").tzinfo is not None        # naive -> UTC
    assert m._parse_dt("not a date") is None
    assert m._parse_dt(None) is None


# ── permissions ───────────────────────────────────────────────────────────────

def test_viewer_actions_are_the_safe_subset():
    """A viewer may participate, never moderate. This is the guard the socket relies on,
    so a new destructive action added to ACTIONS is moderator-only by default."""
    for action in ("chat.delete", "chat.bulk", "participant.ban", "participant.remove",
                   "poll.create", "poll.close", "announce.send", "qa.dismiss"):
        assert action in m.ACTIONS, action
        assert action not in m.VIEWER_ACTIONS, action
    for action in m.VIEWER_ACTIONS:
        assert action in m.ACTIONS, action


def test_unknown_action_is_rejected():
    ctx = m.Ctx(event_id="e", org_id="o", room="r", user_id=None, name="Ava",
                identity="u1", role="viewer", can_moderate=False)
    assert asyncio.run(m.dispatch(ctx, "chat.nuke", {}))                 # unknown -> error
    assert asyncio.run(m.dispatch(ctx, "chat.delete", {"id": "x"}))      # viewer -> error


# ── bus fan-out + presence (in-process path) ──────────────────────────────────

def test_publish_reaches_every_subscriber():
    async def run():
        async with bus.subscribe("evt-1") as a, bus.subscribe("evt-1") as b:
            await bus.publish("evt-1", "chat", "message.new", {"text": "hi"})
            await bus.publish("evt-2", "chat", "message.new", {"text": "other room"})
            return a.get_nowait(), b.get_nowait(), a.empty()

    first, second, isolated = asyncio.run(run())
    assert first["channel"] == "chat" and first["data"]["text"] == "hi"
    assert second["data"]["text"] == "hi"
    assert isolated, "an event must not receive another event's traffic"


def test_subscriber_cleanup():
    async def run():
        async with bus.subscribe("evt-3"):
            pass
        return bus.local_subscribers("evt-3")

    assert asyncio.run(run()) == 0


def test_presence_upsert_merges_and_uuid_coerces():
    import uuid as _uuid

    async def run():
        eid = _uuid.uuid4()
        await bus.presence_upsert(eid, "u1", {"name": "Ava", "muted": False})
        # A UUID and its string form must address the SAME record (routers pass UUIDs,
        # LiveKit webhooks pass strings).
        rec = await bus.presence_upsert(str(eid), "u1", {"muted": True})
        everyone = await bus.presence_all(eid)
        await bus.ban(eid, "u2")
        return rec, everyone, await bus.is_banned(str(eid), "u2"), await bus.is_banned(eid, "u1")

    rec, everyone, banned, not_banned = asyncio.run(run())
    assert rec["name"] == "Ava" and rec["muted"] is True   # patch merged, name kept
    assert len(everyone) == 1
    assert banned and not not_banned


# ── the socket loop ───────────────────────────────────────────────────────────

def test_socket_loop():
    """One connection, end to end: auth gate, snapshot-first, presence join, ping/pong,
    unknown-action error, an ephemeral broadcast, self-reported media state, and the rate
    limiter. Only the DB boundary is stubbed — the loop, dispatcher, limiter and bus are
    the real ones. This is the piece where a regression means "the console goes silent",
    so it's worth the stubbing."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import live as live_router

    event_id, user_id = uuid.uuid4(), uuid.uuid4()
    ctx = m.Ctx(event_id=event_id, org_id=uuid.uuid4(), room=f"event_{event_id}",
                user_id=user_id, name="Ava Chen", identity=str(user_id),
                role="org_admin", can_moderate=True)

    async def fake_snapshot(_ctx):
        return {"event": {"id": str(event_id), "name": "Keynote", "status": "live"},
                "speakers": [], "messages": [], "questions": [], "polls": [],
                "announcements": [], "activity": [], "can_moderate": True,
                "livekit_enforced": False, "participants": await bus.presence_all(event_id)}

    originals = (live_router._user_from_token, live_router.mod.resolve_ctx, live_router.mod.snapshot)
    live_router._user_from_token = lambda token, db: (
        types.SimpleNamespace(id=user_id, email="ava@example.com", is_active=True,
                              role="org_admin", full_name="Ava Chen")
        if token == "good" else None
    )
    live_router.mod.resolve_ctx = lambda *_: ctx
    live_router.mod.snapshot = fake_snapshot

    try:
        client = TestClient(app)
        url = f"/api/live/events/{event_id}/ws"

        # An invalid token is refused BEFORE accept, so it never sees an envelope.
        try:
            with client.websocket_connect(f"{url}?token=bad"):
                raise AssertionError("a bad token must not be accepted")
        except AssertionError:
            raise
        except Exception:
            pass

        with client.websocket_connect(f"{url}?token=good") as ws:
            first = ws.receive_json()
            assert (first["channel"], first["type"]) == ("moderator", "snapshot")
            assert first["data"]["event"]["name"] == "Keynote"

            join = ws.receive_json()
            assert join["type"] == "participant.join"
            assert join["data"]["name"] == "Ava Chen" and join["data"]["role"] == "moderator"

            ws.send_json({"action": "ping", "t": 12345})
            pong = ws.receive_json()
            assert pong["type"] == "pong" and pong["data"]["t"] == 12345

            ws.send_json({"action": "chat.explode", "payload": {}})
            err = ws.receive_json()
            assert err["type"] == "error" and "Unknown action" in err["data"]["message"]

            ws.send_json({"action": "chat.typing", "payload": {"typing": True}})
            typing = ws.receive_json()
            assert (typing["channel"], typing["type"]) == ("chat", "typing")
            assert typing["data"]["name"] == "Ava Chen"

            ws.send_json({"action": "participant.state", "payload": {"muted": True, "quality": "poor"}})
            upd = ws.receive_json()
            assert upd["data"]["muted"] is True and upd["data"]["quality"] == "poor"

            # Flooding trips the limiter and must NOT drop the connection.
            for i in range(40):
                ws.send_json({"action": "chat.typing", "payload": {"typing": bool(i % 2)}})
            assert any(
                f["type"] == "error" and "Slow down" in f["data"]["message"]
                for f in (ws.receive_json() for _ in range(60))
            ), "the rate limiter never engaged"
    finally:
        live_router._user_from_token, live_router.mod.resolve_ctx, live_router.mod.snapshot = originals


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nAll moderation checks passed.")
