"""End-to-end proof that a viewer reaction is an EPHEMERAL EVENT that reaches the host.

The reaction system is the Google Meet model, not a scoreboard. What this file has to
prove is therefore the opposite of what it proved before the change (it used to assert
that a tap moved an authoritative per-emoji total, which no surface displays any more):

    reaction.add (viewer socket)
        -> routers/live.py     budget_for() -> the reaction-only rate budget
        -> moderation.dispatch (permission gate + key validation + event-state gate)
        -> bus.reaction_tally  (internal analytics record, never sent to a client)
        -> bus.publish("reactions", "reaction.burst")
        -> EVERY other socket on the event, host console included   <- the mandatory bit
        -> and nothing in snapshot()                                <- no replay on reconnect

Nothing here touches the DB: _reaction_add and the reaction path are pure bus + settings
work, so no rows and no LiveKit room are needed to assert any of it.

Most tests run on the in-process fallback with REDIS_URL blanked, following the reasoning
in test_broadcast.py::_preview_status — bus.redis() short-circuits to a plain dict when the
URL is unset, so nothing is connected and no client is left bound to a dead event loop.
test_reactions_fan_out_across_workers_over_redis covers the OTHER backend deliberately,
since a real deployment sets REDIS_URL and that is the path that carries a reaction from a
viewer on one Cloud Run instance to a host on another.
"""
import asyncio
import types
import uuid

import pytest

# app.main FIRST, for the import-cycle reason spelled out in test_live_socket_connect.py:
# app/services/org.py does `from .broadcast import engagement_score` while broadcast.py
# transitively imports org.py, so reaching app.routers.live any other way fails collection.
import app.main  # noqa: F401  - import order matters; see above

from app.ratelimit import SlidingWindow
from app.services import bus
from app.services import moderation as m


@pytest.fixture(autouse=True)
def in_process_bus():
    """Force the no-Redis path for every test in this module (see the module docstring).
    Restores the configured URL afterwards so the rest of the suite is unaffected.

    Yields the ORIGINAL url: bus.settings is the same Settings singleton as
    app.config.settings, so blanking it here blanks it everywhere — a test that wants the
    real Redis back (the fan-out test below) has to be handed the value, it cannot re-read
    it from config."""
    url = bus.settings.REDIS_URL
    bus.settings.REDIS_URL = ""
    try:
        yield url
    finally:
        bus.settings.REDIS_URL = url


def _ctx(event_id, identity="viewer-a"):
    """A plain audience connection: no moderation, no host rights — the least-privileged
    caller that must still be able to react (reaction.add is in VIEWER_ACTIONS)."""
    return m.Ctx(event_id=event_id, org_id=uuid.uuid4(), room=f"event_{event_id}",
                 user_id=None, name="Ava", identity=identity, role="viewer",
                 can_moderate=False, can_host=False)


@pytest.fixture
def configured_redis_url(in_process_bus):
    """The deployment's real REDIS_URL, or a skip when there is none to talk to."""
    if not in_process_bus:
        pytest.skip("REDIS_URL is not configured — the in-process tests cover the fallback")
    return in_process_bus


@pytest.fixture
def event_id():
    """A fresh event id per test, with the bus's per-event state torn down afterwards —
    the in-process fallback stores are module-level dicts shared across tests, and the Redis
    ones have no TTL.

    presence_clear, NOT state_clear: state_clear only deletes the settings key, while
    presence_clear is the one that also drops the reaction tally (it is what the LiveKit
    room_finished webhook calls when a broadcast ends). Using the wrong one here would leave
    an event's settings or tally behind for the next test to inherit."""
    eid = uuid.uuid4()
    yield eid
    asyncio.run(bus.presence_clear(eid))


# ── the wire contract ─────────────────────────────────────────────────────────

def test_reaction_keys_match_the_frontend_list():
    """data/reactions.js REACTIONS is the client's render order; these keys are the wire
    contract. A key present on one side only renders nothing at all — the overlay drops an
    emoji it cannot look up rather than painting `undefined` over the video."""
    assert set(m.REACTION_KEYS) == {"like", "heart", "clap", "fire", "party"}


def test_a_tap_publishes_one_ephemeral_burst_and_no_total(event_id):
    """The shape of the thing, asserted field by field, because every field is a decision:

    * channel/type are what the clients match verbatim (EventWatch.jsx, useLiveEvent.js);
    * `reaction` is the wire key the overlay looks an emoji up by;
    * `id` is unique per INSTANCE, so two taps are two independent floating emoji;
    * and there is NO count anywhere — that is the whole point of the change.
    """
    async def scenario():
        async with bus.subscribe(event_id) as q:
            assert await m.dispatch(_ctx(event_id), "reaction.add", {"key": "heart"}) is None
            return q.get_nowait()

    env = asyncio.run(scenario())
    assert (env["channel"], env["type"]) == ("reactions", "reaction.burst")
    assert env["data"]["reaction"] == "heart"
    assert env["data"]["event_id"] == str(event_id)
    assert env["data"]["id"]
    # No total, under any name. A regression that reintroduced counts would land here.
    assert set(env["data"]) == {"event_id", "reaction", "id", "ts"}
    assert "reactions" not in env["data"] and "count" not in env["data"]


def test_the_burst_carries_no_viewer_identity(event_id):
    """The host must see WHAT was sent, never who sent it. Nothing identifying may travel:
    no identity, no display name, no user id, no email, no registration or access token.
    Asserted against the ctx's own values so a field renamed into the payload still fails."""
    ctx = m.Ctx(event_id=event_id, org_id=uuid.uuid4(), room=f"event_{event_id}",
                user_id=uuid.uuid4(), name="Ada Lovelace", identity="user-ada-42",
                role="viewer", can_moderate=False, can_host=False)

    async def scenario():
        async with bus.subscribe(event_id) as q:
            await m.dispatch(ctx, "reaction.add", {"key": "clap"})
            return q.get_nowait()

    data = asyncio.run(scenario())["data"]
    leaks = {str(ctx.identity), str(ctx.name), str(ctx.user_id), str(ctx.org_id)}
    assert not leaks & {str(v) for v in data.values()}
    assert not {"identity", "name", "user_id", "viewer_id", "email", "actor",
                "token"} & set(data)


def test_each_tap_gets_its_own_id_so_simultaneous_reactions_stay_separate(event_id):
    """Ten viewers reacting at once has to be ten floating emoji, not one. The overlay keys
    its DOM nodes on this id, so a repeated id would collapse them into a single element
    that never re-animates."""
    async def scenario():
        async with bus.subscribe(event_id) as q:
            for _ in range(10):
                await m.dispatch(_ctx(event_id), "reaction.add", {"key": "like"})
            return [q.get_nowait()["data"]["id"] for _ in range(10)]

    ids = asyncio.run(scenario())
    assert len(set(ids)) == 10


# ── who sees it: the multi-viewer + host requirement ─────────────────────────

def test_one_viewers_tap_reaches_every_other_socket_on_the_event(event_id):
    """The mandatory requirement, at the bus level: a viewer taps and a DIFFERENT
    connection — the host console is just another subscriber of the same event — receives
    the reaction. The tapper is told too, which is what lets the viewer's own overlay show
    the reaction without a separate local echo that could double up."""
    async def scenario():
        # Three independent connections on the same event, exactly as routers/live.py
        # opens them (one bus.subscribe per socket): the tapper, another viewer, the host.
        async with bus.subscribe(event_id) as tapper, \
                bus.subscribe(event_id) as other_viewer, \
                bus.subscribe(event_id) as host:
            await m.dispatch(_ctx(event_id, "viewer-a"), "reaction.add", {"key": "party"})
            return tapper.get_nowait(), other_viewer.get_nowait(), host.get_nowait()

    for env in asyncio.run(scenario()):
        assert (env["channel"], env["type"]) == ("reactions", "reaction.burst")
        assert env["data"]["reaction"] == "party"


def test_reactions_from_several_viewers_all_reach_the_host_in_order(event_id):
    """Ten different viewers, five different emoji: the host gets ten separate bursts, and
    none is merged, deduplicated or collapsed into a number."""
    keys = ["like", "heart", "party", "fire", "clap"] * 2

    async def scenario():
        async with bus.subscribe(event_id) as host:
            for i, key in enumerate(keys):
                await m.dispatch(_ctx(event_id, f"viewer-{i}"), "reaction.add", {"key": key})
            return [host.get_nowait() for _ in keys]

    envs = asyncio.run(scenario())
    assert [e["data"]["reaction"] for e in envs] == keys
    assert len({e["data"]["id"] for e in envs}) == len(keys)


def test_reactions_are_isolated_per_event():
    """bus.publish keys on event_id, so a tap on one event must never reach a subscriber of
    another. The clients ALSO check data.event_id (EventWatch.jsx), but this is the layer
    that has to make it true in the first place."""
    async def scenario():
        a, b = uuid.uuid4(), uuid.uuid4()
        try:
            async with bus.subscribe(a) as qa, bus.subscribe(b) as qb:
                await m.dispatch(_ctx(a), "reaction.add", {"key": "fire"})
                return qa.get_nowait(), qb.empty()
        finally:
            await bus.presence_clear(a)
            await bus.presence_clear(b)

    env_a, b_saw_nothing = asyncio.run(scenario())
    assert env_a["data"]["reaction"] == "fire"
    assert b_saw_nothing, "a reaction on event A reached a subscriber of event B"


# ── nothing to replay ────────────────────────────────────────────────────────

def test_the_connect_snapshot_carries_no_reaction_data(event_id, monkeypatch):
    """A host (or viewer) reconnect must not re-animate reactions that already happened.

    That is guaranteed structurally rather than by client-side filtering: after a burst of
    taps, the snapshot every fresh socket receives contains no reaction key at all, so
    there is nothing for a reconnect to replay. Only the DB half and the host-domain extras
    are stubbed; the reaction-relevant part of snapshot() is the real one."""
    monkeypatch.setattr(m, "SNAPSHOT_EXTRAS", [])

    async def fake_tx(fn):
        return {"event": {"id": str(event_id)}, "messages": [], "questions": [], "polls": []}

    monkeypatch.setattr(m, "tx", fake_tx)

    async def scenario():
        ctx = _ctx(event_id)
        for key in ("like", "like", "heart", "fire"):
            await m.dispatch(ctx, "reaction.add", {"key": key})
        return await m.snapshot(ctx), await bus.reaction_all(event_id)

    snap, tally = asyncio.run(scenario())
    assert "reactions" not in snap, "the snapshot would replay reactions on every reconnect"
    # The chat/Q&A/poll history a reconnect DOES legitimately restore is untouched.
    assert snap["messages"] == [] and snap["questions"] == [] and snap["polls"] == []
    # The taps did happen — they were recorded internally, they are just not sent anywhere.
    assert tally == {"like": 2, "heart": 1, "fire": 1}


def test_the_internal_tally_is_analytics_only_and_never_broadcast(event_id):
    """Reaction EVENT analytics is kept, deliberately, and kept SEPARATE from the
    animation: bus.reaction_tally records every tap for engagement reporting, and no
    envelope and no snapshot ever carries it."""
    async def scenario():
        async with bus.subscribe(event_id) as q:
            ctx = _ctx(event_id)
            for _ in range(3):
                await m.dispatch(ctx, "reaction.add", {"key": "heart"})
            envelopes = [q.get_nowait() for _ in range(3)]
            return envelopes, await bus.reaction_all(event_id)

    envelopes, tally = asyncio.run(scenario())
    assert tally == {"heart": 3}
    for env in envelopes:
        assert "3" not in {str(v) for v in env["data"].values() if not isinstance(v, float)}
        assert set(env["data"]) == {"event_id", "reaction", "id", "ts"}


def test_the_tally_is_dropped_when_the_broadcast_ends(event_id):
    """presence_clear is what the LiveKit room_finished webhook calls. The tally is
    per-broadcast bookkeeping, not history, so it must go with the rest of the event's
    ephemeral state rather than accumulating forever in Redis."""
    async def scenario():
        await m.dispatch(_ctx(event_id), "reaction.add", {"key": "like"})
        before = await bus.reaction_all(event_id)
        await bus.presence_clear(event_id)
        return before, await bus.reaction_all(event_id)

    before, after = asyncio.run(scenario())
    assert before == {"like": 1}
    assert after == {}


# ── who may react, and when ──────────────────────────────────────────────────

def test_a_viewer_needs_no_moderator_rights_to_react(event_id):
    """reaction.add must stay in VIEWER_ACTIONS — if it ever fell out, dispatch's
    can_moderate branch would reject every audience tap with an error frame."""
    assert "reaction.add" in m.VIEWER_ACTIONS
    assert "reaction.add" not in m.HOST_ONLY
    assert asyncio.run(m.dispatch(_ctx(event_id), "reaction.add", {"key": "like"})) is None


@pytest.mark.parametrize("payload", [
    {"key": "rocket"},        # not in the fixed set
    {"key": ""},
    {},                       # missing entirely
    {"key": "__proto__"},     # never let a wire value reach a broadcast payload
    {"key": ["heart"]},       # not even a string
])
def test_an_unsupported_key_is_rejected_and_publishes_nothing(event_id, payload):
    """A rejected tap returns an error string (the sender gets a moderator/error frame) and
    must publish nothing — the key travels to every client and is used there to look up an
    emoji, so an arbitrary wire value must never get that far."""
    async def scenario():
        async with bus.subscribe(event_id) as q:
            error = await m.dispatch(_ctx(event_id), "reaction.add", payload)
            return error, q.empty(), await bus.reaction_all(event_id)

    error, nothing_published, tally = asyncio.run(scenario())
    assert error == "Unsupported reaction"
    assert nothing_published
    assert tally == {}


def test_reactions_turned_off_rejects_the_tap(event_id):
    """The host console's existing Reactions toggle (data/host.js) and the non-waivable
    memorial-event lock (broadcast.py _seed_settings) both land on this settings flag."""
    async def scenario():
        await bus.state_set(event_id, {"reactions_enabled": False})
        async with bus.subscribe(event_id) as q:
            error = await m.dispatch(_ctx(event_id), "reaction.add", {"key": "like"})
            return error, q.empty(), await bus.reaction_all(event_id)

    error, nothing_published, tally = asyncio.run(scenario())
    assert error == "Reactions are turned off for this event"
    assert nothing_published
    assert tally == {}


def test_an_ended_event_rejects_reactions(event_id):
    """Event state gate: a viewer whose socket is still open when the host ends the
    broadcast must not be able to keep floating reactions over what is now a recording."""
    async def scenario():
        await bus.state_set(event_id, {"status": "ended"})
        async with bus.subscribe(event_id) as q:
            error = await m.dispatch(_ctx(event_id), "reaction.add", {"key": "heart"})
            return error, q.empty(), await bus.reaction_all(event_id)

    error, nothing_published, tally = asyncio.run(scenario())
    assert error == "This event has ended"
    assert nothing_published
    assert tally == {}


@pytest.mark.parametrize("status", ["live", "paused", "preview", "degraded"])
def test_every_other_broadcast_state_still_allows_reactions(event_id, status):
    """The gate above is specifically about ENDED. A paused broadcast is still running (the
    host is holding it), and a preview is the host checking their own console — neither is
    a reason to refuse the audience."""
    async def scenario():
        await bus.state_set(event_id, {"status": status})
        async with bus.subscribe(event_id) as q:
            error = await m.dispatch(_ctx(event_id), "reaction.add", {"key": "like"})
            return error, q.get_nowait()

    error, env = asyncio.run(scenario())
    assert error is None
    assert env["data"]["reaction"] == "like"


# ── rate limiting ────────────────────────────────────────────────────────────

def test_reactions_have_their_own_budget_separate_from_every_other_action():
    """Two properties of routers/live.py::budget_for, both of which matter:

    * a scripted reaction flood is bounded (REACTION_LIMIT in REACTION_WINDOW), and
    * it spends from the REACTION budget only — so a viewer hammering the reaction bar
      cannot spend the allowance their own chat/Q&A/poll actions need, and a chatty viewer
      cannot lose the ability to react.
    """
    from app.routers import live as live_router

    general = SlidingWindow(live_router.RATE_LIMIT, live_router.RATE_WINDOW)
    reactions = SlidingWindow(live_router.REACTION_LIMIT, live_router.REACTION_WINDOW)

    verdicts = [live_router.budget_for("reaction.add", general, reactions).allow()
                for _ in range(live_router.REACTION_LIMIT + 8)]
    assert verdicts[:live_router.REACTION_LIMIT] == [True] * live_router.REACTION_LIMIT
    assert not any(verdicts[live_router.REACTION_LIMIT:]), "a reaction flood was not bounded"

    # ...and the general budget is completely untouched by that flood.
    assert all(live_router.budget_for("chat.send", general, reactions).allow()
               for _ in range(live_router.RATE_LIMIT))


def test_normal_human_tapping_is_never_throttled():
    """"Prevent spam without making it feel slow": the ceiling has to sit above deliberate
    tapping. A person tapping as fast as they can manages roughly 5/s for a moment, so a
    realistic burst of 5 taps has to sail through with budget left over."""
    from app.routers import live as live_router

    reactions = SlidingWindow(live_router.REACTION_LIMIT, live_router.REACTION_WINDOW)
    assert all(reactions.allow() for _ in range(5))
    assert live_router.REACTION_LIMIT > 5


def test_an_over_budget_reaction_is_dropped_silently():
    """A reaction the server declines to fan out leaves the viewer nothing to act on, and a
    toast per dropped tap during a flood would itself be the flood. Every OTHER refusal is
    still reported — a viewer whose chat message is rejected has to be told why."""
    from app.routers import live as live_router

    assert "reaction.add" in live_router.SILENT_OVER_BUDGET
    assert not {"chat.send", "qa.ask", "poll.vote", "participant.hand"} & \
        live_router.SILENT_OVER_BUDGET


# ── the Redis path (what a real deployment actually runs) ─────────────────────

def test_reactions_fan_out_across_workers_over_redis(configured_redis_url):
    """The in-process tests above prove the logic; this proves the TRANSPORT a deployment
    with REDIS_URL set actually uses, and it is the requirement that a viewer connected to
    one Cloud Run instance is seen by a host connected to another. With Redis, bus.publish
    does NOT fan out locally — it round-trips through Redis pub/sub and back into each
    worker's _pump — so this is the only test here that would catch a Redis-only regression
    (a wrong key, bytes-vs-str decoding, a pump that never relays).

    Everything this test builds is torn down INSIDE its own event loop, and the shared
    bus._redis slot is put back exactly as found. That isolation is the whole reason this
    test is safe to leave in the default suite: this module's `asyncio.run` per test is the
    hazard test_broadcast.py::_preview_status and test_host_authorization.py both warn
    about — a Redis client (or a _pump task) created in one loop and touched after that loop
    closes surfaces as "Event loop is closed" in a LATER, unrelated test."""
    bus.settings.REDIS_URL = configured_redis_url   # undo the autouse fixture for this test
    previously_cached = bus._redis
    bus._redis = None                               # force a client bound to THIS test's loop
    event_id = uuid.uuid4()

    async def scenario():
        try:
            async with bus.subscribe(event_id) as viewer, bus.subscribe(event_id) as host:
                await m.dispatch(_ctx(event_id, "viewer-a"), "reaction.add", {"key": "party"})
                # Unlike the fallback, delivery here is a real network round trip, so the
                # envelope is not in the queue the instant dispatch returns.
                envs = await asyncio.gather(
                    asyncio.wait_for(viewer.get(), timeout=5),
                    asyncio.wait_for(host.get(), timeout=5),
                )
                tally = await bus.reaction_all(event_id)
            # Leaving the `async with` cancelled the Redis _pump task for this event, but
            # bus.subscribe does not await the cancellation — the pump's own finally still
            # has an `unsubscribe`/`aclose` to run. Yield the loop until it has actually
            # finished, so nothing is left half-torn-down for the loop's closure (or a
            # later test) to trip over.
            await asyncio.sleep(0)
            while bus._pumps.get(str(event_id)) is not None:
                await asyncio.sleep(0.01)
            return envs, tally
        finally:
            # presence_clear so the tally actually goes away — this one runs against the
            # deployment's real Redis, where a leaked key would live forever.
            await bus.presence_clear(event_id)
            # Close the client from the loop that created it; closing it from another loop
            # is itself an "Event loop is closed".
            if bus._redis is not None:
                await bus._redis.aclose()
            await asyncio.sleep(0)      # let the connection's transport finish closing

    try:
        (env_viewer, env_host), tally = asyncio.run(scenario())
    finally:
        # Restore rather than blank: whatever the suite had cached before this test is what
        # the rest of the suite expects to find, so this module leaves no global footprint.
        bus._redis = previously_cached

    for env in (env_viewer, env_host):
        assert (env["channel"], env["type"]) == ("reactions", "reaction.burst")
        assert env["data"]["reaction"] == "party"
        assert set(env["data"]) == {"event_id", "reaction", "id", "ts"}
    # Proves HINCRBY/HGETALL came back as ints keyed by str — a decode_responses regression
    # would show up here as an empty or string-valued tally.
    assert tally == {"party": 1}


# ── two real sockets, through the actual endpoint ─────────────────────────────

def _read_until(ws, channel, type_, limit=12):
    """Drain frames until the one we care about arrives.

    A freshly-opened viewer socket legitimately receives a burst first — the opening
    moderator/snapshot, its own participants/participant.join, then the other viewer's —
    so a bare receive_json() would assert against whichever of those happened to be next.
    `limit` keeps a missing envelope a failed assertion rather than a hung test.
    """
    for _ in range(limit):
        env = ws.receive_json()
        if (env.get("channel"), env.get("type")) == (channel, type_):
            return env
    raise AssertionError(f"no {channel}/{type_} envelope arrived within {limit} frames")


@pytest.fixture
def socket_app(event_id, monkeypatch):
    """The real WebSocket endpoint with only the auth/DB boundary stubbed — the same three
    seams test_moderation.py::test_socket_loop replaces. The receive loop, the rate
    limiters, the dispatcher, its permission gate and the bus are all the real ones, and
    every ctx handed back is a plain VIEWER (can_moderate False, can_host False), so these
    tests also prove an ordinary audience member is allowed to react.

    Returns (TestClient, ws url, {token: user_id}). An unknown token resolves to no user,
    which is what the invalid-access test needs."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import live as live_router

    viewers = {"tok-a": uuid.uuid4(), "tok-b": uuid.uuid4()}

    def fake_user(token, db):
        uid = viewers.get(token)
        if uid is None:
            return None
        # org_id None: routers/live.py's org-state gate only looks up an Organization for a
        # signed-in caller that HAS one, and org_state.blocked_reason(None, ...) permits —
        # the same pass-through a self-registered attendee gets. Nothing about reactions
        # depends on org state, so this keeps the DB out of the test.
        return types.SimpleNamespace(id=uid, email=f"{token}@example.com", is_active=True,
                                     role="viewer", org_id=None,
                                     full_name=f"Viewer {token[-1].upper()}")

    def fake_resolve_ctx(evt_id, user):
        return m.Ctx(event_id=event_id, org_id=uuid.uuid4(), room=f"event_{event_id}",
                     user_id=user.id, name=user.full_name, identity=str(user.id),
                     role="viewer", can_moderate=False, can_host=False)

    async def fake_snapshot(ctx):
        # Everything here is a stub EXCEPT the absence of reaction data, which is the real
        # contract (see test_the_connect_snapshot_carries_no_reaction_data).
        return {"event": {"id": str(event_id), "name": "Keynote", "status": "live"},
                "speakers": [], "messages": [], "questions": [], "polls": [],
                "announcements": [], "activity": [], "can_moderate": False,
                "livekit_enforced": False, "participants": await bus.presence_all(event_id),
                "you": {"identity": ctx.identity, "can_moderate": False, "can_host": False}}

    monkeypatch.setattr(live_router, "_user_from_token", fake_user)
    monkeypatch.setattr(live_router.mod, "resolve_ctx", fake_resolve_ctx)
    monkeypatch.setattr(live_router.mod, "snapshot", fake_snapshot)
    return TestClient(app), f"/api/live/events/{event_id}/ws", viewers


def test_two_viewers_see_each_others_reactions_over_real_sockets(socket_app, event_id):
    """The requirement asserted through the real endpoint rather than by calling the
    dispatcher directly: two connections are open at once, one taps, and the OTHER socket
    receives the reaction — no reconnect, no refresh, no polling. Then the second taps and
    the first receives it, so the path is proven in both directions.

    The second socket stands in for the Producer Console exactly: the host console
    subscribes to the same per-event bus over the same endpoint (hooks/useLiveEvent.js),
    and `reactions`/`reaction.burst` is not addressed to a role."""
    client, url, _ = socket_app

    with client.websocket_connect(f"{url}?token=tok-a") as ws_a, \
            client.websocket_connect(f"{url}?token=tok-b") as ws_b:
        snap_a = _read_until(ws_a, "moderator", "snapshot")
        assert "reactions" not in snap_a["data"], "a fresh socket was handed reaction state"

        # ── A taps 👍 -> B must see it ────────────────────────────────────────
        ws_a.send_json({"action": "reaction.add", "payload": {"key": "like"}})

        on_b = _read_until(ws_b, "reactions", "reaction.burst")
        assert on_b["data"]["reaction"] == "like", "the other socket never saw A's tap"
        assert on_b["data"]["event_id"] == str(event_id)
        # Nothing identifying reached the other side — not even A's own identity, which
        # this socket's ctx definitely knows.
        assert set(on_b["data"]) == {"event_id", "reaction", "id", "ts"}
        # The tapper is told too, which is what its own overlay animates.
        assert _read_until(ws_a, "reactions", "reaction.burst")["data"]["reaction"] == "like"

        # ── B taps ❤️ -> A must see it ────────────────────────────────────────
        ws_b.send_json({"action": "reaction.add", "payload": {"key": "heart"}})
        back_on_a = _read_until(ws_a, "reactions", "reaction.burst")
        assert back_on_a["data"]["reaction"] == "heart", "A never saw B's tap"

        # ── a rejected tap is reported, and floats nothing ────────────────────
        ws_a.send_json({"action": "reaction.add", "payload": {"key": "rocket"}})
        err = _read_until(ws_a, "moderator", "error")
        assert err["data"]["message"] == "Unsupported reaction"

        # ── a reconnecting socket inherits no reactions ───────────────────────
        with client.websocket_connect(f"{url}?token=tok-a") as ws_c:
            snap_c = _read_until(ws_c, "moderator", "snapshot")
            assert "reactions" not in snap_c["data"], \
                "a reconnect was handed reaction state it could replay"


def test_an_invalid_watch_credential_cannot_react_at_all(socket_app):
    """"Invalid viewer access cannot send reactions", enforced where it belongs: the socket
    that would carry reaction.add is refused before it is ever established. 1008 (not a
    retryable code) is what tells useEventStream.js to stop rather than loop on a dead
    credential."""
    from starlette.websockets import WebSocketDisconnect

    client, url, _ = socket_app

    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(f"{url}?token=not-a-real-token") as ws:
            ws.send_json({"action": "reaction.add", "payload": {"key": "heart"}})
            ws.receive_json()
    assert refused.value.code == 1008


def test_a_scripted_flood_is_capped_and_never_answered_with_an_error(socket_app):
    """Rate limiting through the real receive loop.

    Twenty taps as fast as the socket will carry them, then a `participant.state` action as
    a FENCE: it publishes through the same bus queue as the bursts, so once its envelope
    arrives every burst that was going to be published already has been. Exactly
    REACTION_LIMIT get through, and the dropped ones produce no error frames.
    """
    from app.routers import live as live_router

    client, url, _ = socket_app

    with client.websocket_connect(f"{url}?token=tok-a") as ws:
        _read_until(ws, "moderator", "snapshot")

        for _ in range(20):
            ws.send_json({"action": "reaction.add", "payload": {"key": "fire"}})
        ws.send_json({"action": "participant.state", "payload": {"muted": True}})

        bursts, errors = 0, 0
        for _ in range(60):
            env = ws.receive_json()
            key = (env.get("channel"), env.get("type"))
            if key == ("reactions", "reaction.burst"):
                bursts += 1
            elif key == ("moderator", "error"):
                errors += 1
            elif key == ("participants", "participant.update"):
                break
        else:
            raise AssertionError("the fence envelope never arrived")

    assert bursts == live_router.REACTION_LIMIT, \
        f"expected the flood to be capped at {live_router.REACTION_LIMIT}, got {bursts}"
    assert errors == 0, "a dropped reaction was answered with an error frame"


def test_a_reaction_flood_does_not_lock_the_viewer_out_of_chat(socket_app):
    """The other half of the separate-budget decision, through the real socket: after
    spending the entire reaction budget, the same connection's non-reaction actions still
    work. Before reactions had their own window, ~30 quick taps left a viewer unable to
    chat, ask a question or vote for the rest of the window."""
    client, url, _ = socket_app

    with client.websocket_connect(f"{url}?token=tok-a") as ws:
        _read_until(ws, "moderator", "snapshot")

        for _ in range(25):
            ws.send_json({"action": "reaction.add", "payload": {"key": "clap"}})
        # A viewer action that touches only the bus, so this stays a DB-free test.
        ws.send_json({"action": "participant.hand", "payload": {"hand": True}})

        raised = _read_until(ws, "participants", "participant.update", limit=60)
        assert raised["data"]["hand"] is True
