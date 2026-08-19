"""Realtime bus + presence store for live events.

ONE logical bus per event. Every message is an envelope:

    {"channel": "chat", "type": "message.new", "data": {...}, "ts": 1723...}

`channel` is the spec's channel taxonomy — moderator | chat | participants | poll |
qa | announcement | activity | presence. They are *logical* channels multiplexed
over a single WebSocket per client: seven sockets per moderator would multiply
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

# Logical channels. "moderator" is the PRIVATE per-socket channel (snapshot, pong, error)
# — it predates the host console and keeps its name so the moderator client stays working;
# read it as "this connection's own control channel", not "moderators only".
CHANNELS = (
    "moderator", "chat", "participants", "poll", "qa", "announcement", "activity", "presence",
    # host console additions
    "broadcast",   # go live / pause / resume / end, preview, countdown, media settings
    "recording",   # start / pause / resume / stop + timer + storage
    "analytics",   # viewer count, peak, retention samples, engagement, distributions
    "stage",       # stage roster, hand-raise queue, waiting room admissions
    "reactions",   # 👍 ❤️ 👏 🔥 🎉 tap counters, broadcast to every viewer of the event
    "session",     # per-connection lifecycle (e.g. "removed") — addressed by identity;
                   # broadcast like everything else, but only the matching socket acts on it
)

# A slow client must never stall the event loop or the other subscribers, so each
# subscriber gets a bounded queue and we drop its OLDEST envelope when it overflows.
# ponytail: 200 is ~10s of a very busy room; raise it only if drops show up in logs.
QUEUE_MAX = 200

_local: dict[str, set[asyncio.Queue]] = {}      # event_id -> subscriber queues (this process)
_pumps: dict[str, asyncio.Task] = {}            # event_id -> redis subscriber task
_memory: dict[str, dict[str, dict]] = {}        # event_id -> identity -> participant (no-Redis fallback)
_redis = None


def eid(event_id) -> str:
    """Every public function coerces its event id through this: callers hand us UUID
    objects (routers) and strings (LiveKit webhooks), and the two must hit the same key."""
    return str(event_id)


def _key(event_id) -> str:
    return f"live:{eid(event_id)}"


async def redis():
    """Lazy shared client. None when REDIS_URL is unset — every caller degrades to
    in-process behaviour rather than failing."""
    global _redis
    if not settings.REDIS_URL:
        return None
    if _redis is None:
        from redis import asyncio as aioredis  # imported lazily: unused without REDIS_URL

        # Upstash (and most managed Redis) closes idle TCP connections; without
        # retry_on_timeout + a health check, the pool keeps handing out a dead socket
        # until it hard-fails (WinError 10054 / TimeoutError) instead of replacing it.
        _redis = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True,
            retry_on_timeout=True, health_check_interval=30,
        )
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


async def _pump(event_id: str) -> None:
    """Relay one event's Redis channel into this process's queues."""
    r = await redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_key(event_id))
    try:
        async for msg in pubsub.listen():
            if msg.get("type") == "message":
                try:
                    _fanout(event_id, json.loads(msg["data"]))
                except (ValueError, TypeError):
                    log.warning("live bus: undecodable payload on %s", _key(event_id))
    finally:
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
    if await redis() is not None and event_id not in _pumps:
        _pumps[event_id] = asyncio.create_task(_pump(event_id))
    try:
        yield q
    finally:
        subs.discard(q)
        if not subs:
            _local.pop(event_id, None)
            task = _pumps.pop(event_id, None)
            if task:
                task.cancel()


def local_subscribers(event_id) -> int:
    """Connections served by THIS worker — used for announcement delivery counts."""
    return len(_local.get(eid(event_id), ()))


async def shutdown() -> None:
    for task in _pumps.values():
        task.cancel()
    _pumps.clear()
    if _redis is not None:
        await _redis.aclose()


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


# ── reactions (👍 ❤️ 👏 🔥 🎉 …) ─────────────────────────────────────────────────
# Same shape as presence: ephemeral per-broadcast counters, not history. Concurrency is
# the whole point of this store existing separately from bus.state_set's read-modify-write
# — many viewers tap the same emoji at once, so "load count, add one, save count" WOULD
# lose taps. Redis HINCRBY is a single atomic server-side op across every worker; the
# in-process dict fallback increments synchronously with no `await` between the read and
# the write, so one worker can't interleave two increments either — same guarantee the
# no-Redis dev setup already relies on elsewhere in this module (presence, bans).

_reactions: dict[str, dict[str, int]] = {}   # event_id -> reaction_key -> count (no-Redis fallback)


def _rkey(event_id) -> str:
    return f"live:{eid(event_id)}:reactions"


async def reaction_incr(event_id, key: str) -> dict:
    """Atomically add one tap to `key` and return every counter for the event (not just
    the one that changed), so the broadcast envelope is always the full authoritative
    state and a client never has to merge partial updates."""
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        room = _reactions.setdefault(event_id, {})
        room[key] = room.get(key, 0) + 1
        return dict(room)
    await r.hincrby(_rkey(event_id), key, 1)
    raw = await r.hgetall(_rkey(event_id))
    return {k: int(v) for k, v in raw.items()}


async def reaction_all(event_id) -> dict:
    """Current counters for the event — what a joining/reconnecting client's snapshot uses."""
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        return dict(_reactions.get(event_id, {}))
    raw = await r.hgetall(_rkey(event_id))
    return {k: int(v) for k, v in raw.items()}