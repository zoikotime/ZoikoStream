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
    # speaker console additions
    "presentation",  # which deck/slide is on screen, and approval decisions
    "whiteboard",    # collaborative drawing objects
    # attendee additions
    "reaction",      # ephemeral floating reactions (never persisted — see services/attendee.py)
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

        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
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


async def presence_get(event_id, identity: str) -> dict:
    """One participant's current record, or {} if absent. Exists so a caller that needs to
    ACCUMULATE (speaking time) can read the prior value without pulling the whole roster."""
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        return dict(_memory.get(event_id, {}).get(identity) or {})
    raw = await r.hget(_pkey(event_id), identity)
    return json.loads(raw) if raw else {}


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
        _boards.pop(event_id, None)
        _counters.pop(f"reactions:{event_id}", None)
    else:
        await r.delete(_pkey(event_id), _bkey(event_id), _skey(event_id), _wkey(event_id),
                       _ckey(f"reactions:{event_id}"))


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


# ── whiteboard ────────────────────────────────────────────────────────────────
# One hash per event, object id -> object. Deliberately NOT in the DB and NOT in the state key
# above: a whiteboard is per-broadcast working material (like presence), and a stroke every few
# milliseconds would either be a write storm on Postgres or a full rewrite of the settings blob
# on every pen movement.
#
# ponytail: capped at MAX_BOARD_OBJECTS and dropped when the room ends. Export is client-side
# (SVG/PNG), so a whiteboard somebody wants to keep leaves as a file rather than a table. Add a
# `whiteboards` table only if a saved-and-reopened board is ever actually required.

MAX_BOARD_OBJECTS = 2000


def _wkey(event_id) -> str:
    return f"live:{eid(event_id)}:board"


_boards: dict[str, dict[str, dict]] = {}


async def board_add(event_id, obj: dict) -> dict | None:
    """Store one object. Returns None when the board is full, so the caller can say so rather
    than silently dropping the stroke a speaker just drew."""
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        board = _boards.setdefault(event_id, {})
        if len(board) >= MAX_BOARD_OBJECTS and obj["id"] not in board:
            return None
        board[obj["id"]] = obj
        return obj
    if await r.hlen(_wkey(event_id)) >= MAX_BOARD_OBJECTS and not await r.hexists(_wkey(event_id), obj["id"]):
        return None
    await r.hset(_wkey(event_id), obj["id"], json.dumps(obj, default=str))
    return obj


async def board_remove(event_id, obj_id: str) -> bool:
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        return _boards.get(event_id, {}).pop(obj_id, None) is not None
    return bool(await r.hdel(_wkey(event_id), obj_id))


async def board_all(event_id) -> list[dict]:
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        rows = list(_boards.get(event_id, {}).values())
    else:
        rows = [json.loads(v) for v in (await r.hgetall(_wkey(event_id))).values()]
    # Paint order is creation order — a later stroke sits on top of an earlier one.
    return sorted(rows, key=lambda o: o.get("at") or 0)


async def board_clear(event_id) -> int:
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        return len(_boards.pop(event_id, {}) or {})
    n = await r.hlen(_wkey(event_id))
    await r.delete(_wkey(event_id))
    return n


# ── counters ──────────────────────────────────────────────────────────────────
# Small named tallies that must survive a worker but are not worth a row: live reaction totals.
# HINCRBY is atomic, so unlike state_set this is safe with thousands of concurrent writers — which
# is exactly the situation reactions create.

def _ckey(name) -> str:
    return f"live:{name}"


_counters: dict[str, dict[str, int]] = {}


async def counter_bump(name: str, field: str, by: int = 1) -> dict:
    """Increment one field and return the whole tally."""
    r = await redis()
    if r is None:
        bucket = _counters.setdefault(name, {})
        bucket[field] = bucket.get(field, 0) + by
        return dict(bucket)
    await r.hincrby(_ckey(name), field, by)
    return {k: int(v) for k, v in (await r.hgetall(_ckey(name))).items()}


async def counter_all(name: str) -> dict:
    r = await redis()
    if r is None:
        return dict(_counters.get(name, {}))
    return {k: int(v) for k, v in (await r.hgetall(_ckey(name))).items()}


async def counter_clear(name: str) -> None:
    r = await redis()
    if r is None:
        _counters.pop(name, None)
    else:
        await r.delete(_ckey(name))


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
