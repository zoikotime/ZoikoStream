"""Realtime bus + presence store for live events.

ONE logical bus per event. Every message is an envelope:

    {"channel": "chat", "type": "message.new", "data": {...}, "ts": 1723...}

`channel` is the spec's channel taxonomy — moderator | chat | participants | poll |
qa | announcement | activity | presence. They are *logical* channels multiplexed
over a single WebSocket per client: seven sockets per operator would multiply
connections and reconnect logic 7x and buy nothing, since every panel of the
console is open at once anyway.

Transport: in-process asyncio queues, optionally bridged through Redis Pub/Sub.
ponytail: REDIS_URL blank = single-process fan-out (dev, one worker). Set it and the
same code path spans workers — publish goes to Redis only, and the per-event
subscriber task delivers back to local queues, so nothing is ever delivered twice.

Presence lives here too (not in the DB): it is ephemeral by nature and is written on
every join/leave/mute/speak. Redis hash when configured, process dict otherwise.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from ..config import settings

log = logging.getLogger(__name__)

# Logical channels. "moderator" is the PRIVATE per-socket channel (snapshot, pong, error).
# The NAME is wire protocol, not a role: the client matches on it verbatim (useLiveEvent.js,
# EventWatch.jsx, Backstage.jsx), so renaming it would be a breaking change to every live
# surface at once for no behavioural gain — and it long outlived the role it was named after,
# which is now retired. Read it as "this connection's own control channel".
CHANNELS = (
    "moderator", "chat", "participants", "poll", "qa", "announcement", "activity", "presence",
    # host console additions
    "broadcast",   # go live / pause / resume / end, preview, countdown, media settings
    "recording",   # start / pause / resume / stop + timer + storage
    "analytics",   # viewer count, peak, retention samples, engagement, distributions
    "stage",       # stage roster, hand-raise queue, waiting room admissions
    "reactions",   # one ephemeral tap per envelope (never a total), fanned out to
                   # every socket on the event — host console included
    "session",     # per-connection lifecycle (e.g. "removed") — addressed by identity;
                   # broadcast like everything else, but only the matching socket acts on it
)

# A slow client must never stall the event loop or the other subscribers, so each
# subscriber gets a bounded queue and we drop its OLDEST envelope when it overflows.
# ponytail: 200 is ~10s of a very busy room; raise it only if drops show up in logs.
QUEUE_MAX = 200

# How long subscribe() will wait for a Redis pump's SUBSCRIBE to take effect. Generous for a
# round-trip to a healthy Redis, short enough that an unreachable one does not hold up a
# connection — it degrades to "may miss the next envelope" instead, which is what the code
# did unconditionally before.
PUMP_READY_TIMEOUT = 5.0

_local: dict[str, set[asyncio.Queue]] = {}      # event_id -> subscriber queues (this process)
_pumps: dict[str, asyncio.Task] = {}            # event_id -> redis subscriber task
# event_id -> "this pump's Redis SUBSCRIBE is live". Redis Pub/Sub keeps no backlog, so a
# subscriber that is merely *scheduled* receives nothing; see subscribe() below.
_pump_ready: dict[str, asyncio.Event] = {}
_memory: dict[str, dict[str, dict]] = {}        # event_id -> identity -> participant (no-Redis fallback)
_redis = None
# The event loop `_redis`'s connection pool is bound to. See redis() below for why a client
# cannot outlive its loop.
_redis_loop: asyncio.AbstractEventLoop | None = None


def eid(event_id) -> str:
    """Every public function coerces its event id through this: callers hand us UUID
    objects (routers) and strings (LiveKit webhooks), and the two must hit the same key."""
    return str(event_id)


def _key(event_id) -> str:
    return f"live:{eid(event_id)}"


async def redis():
    """Lazy shared client, per event loop. None when REDIS_URL is unset — every caller
    degrades to in-process behaviour rather than failing.

    Cached PER LOOP, not just once. A redis-asyncio pool binds each connection — and the
    futures its parser awaits — to the loop that created it, so handing one client to a
    second loop fails from deep inside the parser with "got Future attached to a different
    loop", or "Event loop is closed" once the first loop has gone.

    The server runs a single loop for the life of the process and so takes the fast path
    every time: one client, created once. A test suite is the case that made this necessary —
    several suites call asyncio.run() per test, which builds and destroys a loop each time,
    and the module-level client from the first of them was still being handed out to all the
    rest.
    """
    global _redis, _redis_loop
    if not settings.REDIS_URL:
        return None

    loop = asyncio.get_running_loop()
    if _redis is not None and _redis_loop is not loop:
        # Deliberately dropped WITHOUT aclose(): closing a pool has to run on the loop that
        # owns its connections, and that loop is exactly what we no longer have. Its sockets
        # are released when the object is finalized. Any pump task started on that loop died
        # with it, so its entry is cleared too rather than left to be cancelled from here.
        _redis = None
        _redis_loop = None
        _pumps.clear()
        _pump_ready.clear()

    if _redis is None:
        from redis import asyncio as aioredis  # imported lazily: unused without REDIS_URL

        # Upstash (and most managed Redis) closes idle TCP connections; without
        # retry_on_timeout + a health check, the pool keeps handing out a dead socket
        # until it hard-fails (WinError 10054 / TimeoutError) instead of replacing it.
        _redis = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True,
            retry_on_timeout=True, health_check_interval=30,
        )
        _redis_loop = loop
    return _redis


# ── publish / subscribe ───────────────────────────────────────────────────────

def envelope(channel: str, type_: str, data: dict | None = None) -> dict:
    return {"channel": channel, "type": type_, "data": data or {}, "ts": time.time()}


def _fanout(event_id, env: dict) -> None:
    for q in _local.get(eid(event_id), ()):
        if q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()  # drop oldest
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(env)


async def publish(event_id, channel: str, type_: str, data: dict | None = None) -> dict:
    """Broadcast to every subscriber of this event (all workers when Redis is on)."""
    event_id = eid(event_id)
    env = envelope(channel, type_, data)
    r = await redis()
    if r is None:
        _fanout(event_id, env)
    else:
        # Redis echoes to this worker's pump too, so we must NOT also fan out locally.
        await r.publish(_key(event_id), json.dumps(env, default=str))
    return env


async def _pump(event_id: str, ready: asyncio.Event) -> None:
    """Relay one event's Redis channel into this process's queues.

    `ready` is set once the SUBSCRIBE has actually taken effect — not when this task is
    created. Callers wait on it; see subscribe().
    """
    r = await redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_key(event_id))
    ready.set()
    try:
        async for msg in pubsub.listen():
            if msg.get("type") == "message":
                try:
                    _fanout(event_id, json.loads(msg["data"]))
                except (ValueError, TypeError):
                    log.warning("live bus: undecodable payload on %s", _key(event_id))
    finally:
        if _pump_ready.get(event_id) is ready:
            _pump_ready.pop(event_id, None)
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(_key(event_id))
            await pubsub.aclose()


@contextlib.asynccontextmanager
async def subscribe(event_id):
    """`async with subscribe(id) as q:` — yields a queue of envelopes. The Redis pump
    is ref-counted: started with the first subscriber, cancelled with the last."""
    event_id = eid(event_id)
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    subs = _local.setdefault(event_id, set())
    subs.add(q)
    if await redis() is not None:
        task = _pumps.get(event_id)
        if task is None or task.done():
            ready = asyncio.Event()
            _pump_ready[event_id] = ready
            _pumps[event_id] = asyncio.create_task(_pump(event_id, ready))
        # WAIT for the pump's SUBSCRIBE to be live, rather than merely having created the
        # task. With Redis on, publish() sends to Redis ONLY and relies on the pump to relay
        # the envelope back into this process — and Redis Pub/Sub has no backlog for a
        # subscriber that arrives late. So everything published between this function
        # returning and the SUBSCRIBE landing was delivered to nobody here.
        #
        # That window is a Redis round-trip, and routers/live.py publishes inside it: the
        # socket subscribes, sends its snapshot, then publishes its own participant.join.
        # The join was being dropped, and with it anything else on the event in those few
        # milliseconds — including a `session`/`removed` envelope the writer task is
        # watching for. It showed up as test_socket_loop hanging on its second frame; on a
        # live event it is a viewer silently missing the next thing that happens.
        ready = _pump_ready.get(event_id)
        if ready is not None and not ready.is_set():
            try:
                await asyncio.wait_for(ready.wait(), PUMP_READY_TIMEOUT)
            except asyncio.TimeoutError:
                # Bounded on purpose: an unreachable Redis must degrade to the old
                # best-effort behaviour, not wedge the connection that is waiting to serve a
                # viewer. Logged because a subscriber that starts deaf is worth knowing about.
                log.warning("live bus: redis pump for %s not ready after %ss; "
                            "envelopes published now may not reach this worker",
                            event_id, PUMP_READY_TIMEOUT)
    try:
        yield q
    finally:
        subs.discard(q)
        if not subs:
            _local.pop(event_id, None)
            task = _pumps.pop(event_id, None)
            _pump_ready.pop(event_id, None)
            if task:
                task.cancel()


def local_subscribers(event_id) -> int:
    """Connections served by THIS worker — used for announcement delivery counts."""
    return len(_local.get(eid(event_id), ()))


async def shutdown() -> None:
    """Release the bus's shared resources. Called from the application lifespan.

    Everything here is scoped to the CURRENT loop, because that is the only loop whose
    objects this coroutine can legally touch. A pump task or a connection pool belonging to
    a loop that has already closed cannot be cancelled or closed from here — cancel() and
    aclose() both schedule through the owning loop and raise "Event loop is closed". It is
    already dead; dropping the reference is the whole of the cleanup it can be given.

    This is not error suppression: nothing is caught. The check is on the resource's owner,
    so a genuine failure closing a client that DOES belong to this loop still propagates —
    which is what a shutdown hook is for.
    """
    global _redis, _redis_loop
    loop = asyncio.get_running_loop()

    for task in _pumps.values():
        if task.get_loop() is loop:
            task.cancel()
    _pumps.clear()

    client, client_loop = _redis, _redis_loop
    _redis = None
    _redis_loop = None
    if client is not None and client_loop is loop:
        await client.aclose()


# ── presence (participants) ───────────────────────────────────────────────────
# One record per identity: {identity, name, role, muted, speaking, hand, quality,
# on_stage, banned, joined_at}. Identity is the LiveKit identity == user id (or
# "viewer-<uuid>" for anonymous viewers), so the console WS and the LiveKit webhook
# update the SAME record instead of double-counting a person.

def _pkey(event_id) -> str:
    return f"live:{eid(event_id)}:participants"


async def presence_upsert(event_id, identity: str, patch: dict) -> dict:
    """Merge `patch` into a participant, creating it if absent. Returns the full record."""
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        room = _memory.setdefault(event_id, {})
        rec = {**room.get(identity, {"identity": identity, "joined_at": time.time()}), **patch}
        room[identity] = rec
        return rec
    raw = await r.hget(_pkey(event_id), identity)
    base = json.loads(raw) if raw else {"identity": identity, "joined_at": time.time()}
    rec = {**base, **patch}
    await r.hset(_pkey(event_id), identity, json.dumps(rec, default=str))
    return rec


async def presence_remove(event_id, identity: str) -> dict | None:
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        return _memory.get(event_id, {}).pop(identity, None)
    raw = await r.hget(_pkey(event_id), identity)
    await r.hdel(_pkey(event_id), identity)
    return json.loads(raw) if raw else None


async def presence_all(event_id) -> list[dict]:
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        rows = list(_memory.get(event_id, {}).values())
    else:
        rows = [json.loads(v) for v in (await r.hgetall(_pkey(event_id))).values()]
    return sorted(rows, key=lambda p: p.get("joined_at") or 0)


async def presence_clear(event_id) -> None:
    """Called when the room ends — presence is per-broadcast, not history."""
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        _memory.pop(event_id, None)
        _state.pop(event_id, None)
        _bans.pop(event_id, None)
        _reactions.pop(event_id, None)
    else:
        await r.delete(_pkey(event_id), _bkey(event_id), _skey(event_id), _rkey(event_id))


# ── live session state (broadcast + chat/Q&A settings) ────────────────────────
# The HOT path: every chat message checks whether chat is enabled, whether slow mode
# applies, and so on. Reading that from Postgres per message is a query per message at
# 100k viewers, so the bus holds the authoritative live copy and the DB
# (models.live.BroadcastSession) holds the durable one. A cold worker rehydrates from the
# DB via services.broadcast.ensure_state.

_state: dict[str, dict] = {}


def _skey(event_id) -> str:
    return f"live:{eid(event_id)}:state"


async def state_get(event_id) -> dict:
    r = await redis()
    if r is None:
        return dict(_state.get(eid(event_id), {}))
    raw = await r.get(_skey(event_id))
    return json.loads(raw) if raw else {}


async def state_set(event_id, patch: dict) -> dict:
    """Merge `patch` into the live state and return the whole thing.
    ponytail: read-modify-write, not a Lua CAS. Only hosts write here and only on a
    deliberate control action, so there is no contention to lose — revisit if a
    background writer ever touches it."""
    merged = {**(await state_get(event_id)), **patch}
    r = await redis()
    if r is None:
        _state[eid(event_id)] = merged
    else:
        await r.set(_skey(event_id), json.dumps(merged, default=str))
    return merged


async def state_clear(event_id) -> None:
    r = await redis()
    if r is None:
        _state.pop(eid(event_id), None)
    else:
        await r.delete(_skey(event_id))


# ── bans ──────────────────────────────────────────────────────────────────────
# A ban must outlive the presence record (the point is that a reconnect is refused),
# so it is a separate set rather than a flag on a row we delete on disconnect.

_bans: dict[str, set[str]] = {}


def _bkey(event_id) -> str:
    return f"live:{eid(event_id)}:bans"


async def ban(event_id, identity: str) -> None:
    r = await redis()
    if r is None:
        _bans.setdefault(eid(event_id), set()).add(identity)
    else:
        await r.sadd(_bkey(event_id), identity)


async def is_banned(event_id, identity: str) -> bool:
    r = await redis()
    if r is None:
        return identity in _bans.get(eid(event_id), ())
    return bool(await r.sismember(_bkey(event_id), identity))


# ── reactions (👍 ❤️ 👏 🔥 🎉 …) ──────────────────────────────────────────────────
# What a viewer tap ACTUALLY produces is one ephemeral `reactions`/`reaction.burst`
# envelope (services/moderation.py::_reaction_add) that every socket on the event animates
# once and forgets — the Google-Meet model. No client is ever sent a total, and none is
# stored here for a client to derive one from.
#
# The tally below is therefore NOT the transport and NOT a UI number: it is the internal
# per-broadcast record of how many reaction events happened, kept so engagement analytics
# has something to read, and dropped with the rest of the event's ephemeral state by
# presence_clear when the room ends. Nothing renders it.
#
# Redis HINCRBY is a single atomic server-side op across every worker; the in-process dict
# fallback increments synchronously with no `await` between the read and the write, so one
# worker can't interleave two increments either — the same guarantee the no-Redis dev
# setup already relies on elsewhere in this module (presence, bans).

_reactions: dict[str, dict[str, int]] = {}   # event_id -> reaction_key -> count (no-Redis fallback)


def _rkey(event_id) -> str:
    return f"live:{eid(event_id)}:reactions"


async def reaction_tally(event_id, key: str) -> None:
    """Record one reaction event against `key`. Write-only, by design.

    This used to be `reaction_incr`, which also read the whole hash back because the
    broadcast envelope carried authoritative totals. Nothing is broadcast a total any more,
    so that HGETALL was pure per-tap cost on the hottest action a viewer has — the write is
    all that is left, and no caller needs a return value.
    """
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        room = _reactions.setdefault(event_id, {})
        room[key] = room.get(key, 0) + 1
        return
    await r.hincrby(_rkey(event_id), key, 1)


async def reaction_all(event_id) -> dict:
    """This broadcast's internal reaction-event tally, for analytics. Never sent to a
    client: no viewer or host surface displays reaction totals (see the note above)."""
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        return dict(_reactions.get(event_id, {}))
    raw = await r.hgetall(_rkey(event_id))
    return {k: int(v) for k, v in raw.items()}
