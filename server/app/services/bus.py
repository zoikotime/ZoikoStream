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
import random
import secrets
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


_transient: tuple[type[BaseException], ...] | None = None


def transient_errors() -> tuple[type[BaseException], ...]:
    """Exception types that mean "Redis is unreachable right now", as opposed to "the caller
    asked for something impossible".

    Exposed so callers can degrade on a connectivity fault WITHOUT importing redis
    themselves — which matters because this module is deliberately usable with redis absent
    or unconfigured, and because a bare `except Exception` around a Redis call would also
    swallow the JSON and type errors that are real defects.

    Empty when redis is not installed: with no client there is nothing to catch, and an empty
    tuple in an `except` clause is legal and matches nothing.
    """
    global _transient
    if _transient is None:
        try:
            from redis import exceptions as rexc
        except ImportError:  # pragma: no cover — redis is in requirements.txt
            _transient = ()
        else:
            # ConnectionError covers the reset/refused/DNS family; TimeoutError covers both
            # "Timeout connecting to server" (the production error) and a timed-out command.
            # BusyLoadingError is a provider restart, which is transient by definition.
            _transient = (rexc.ConnectionError, rexc.TimeoutError, rexc.BusyLoadingError)
    return _transient


async def redis():
    """Lazy shared client, per event loop. None when REDIS_URL is unset — every caller
    degrades to in-process behaviour rather than failing.

    ONE client, and therefore one connection pool, per process. Never build a second per
    request or per event: on Cloud Run the ceiling that matters is
    (instances x pools x max_connections) against the provider's per-plan concurrent-connection
    cap, and blowing through it does not surface as a clean "too many clients" — new connects
    simply stop completing, which arrives here as `TimeoutError: Timeout connecting to server`.

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
        # Lazily imported: unused without REDIS_URL. Nothing is awaited between the None
        # check and the assignment, so no two coroutines can race into building two pools.
        from redis import asyncio as aioredis
        from redis.backoff import ExponentialBackoff
        from redis.retry import Retry

        # Upstash (and most managed Redis) closes idle TCP connections; without
        # retry_on_timeout + a health check, the pool keeps handing out a dead socket
        # until it hard-fails (WinError 10054 / TimeoutError) instead of replacing it.
        #
        # The timeouts are explicit rather than left to redis-py's defaults so that the
        # budget is visible and tunable per deployment: a background ticker on a
        # CPU-throttled Cloud Run instance is the one caller most likely to blow a connect
        # budget, and it is also the one whose failure must stay cheap.
        _redis = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            socket_keepalive=True,
            retry_on_timeout=True, health_check_interval=30,
            # Bounded and backed off. The default retry policy is 10 attempts starting at
            # 10ms, which against an unreachable provider is a hot loop per caller; three
            # attempts over ~0.1s fixes a dropped socket and gives up on an outage.
            retry=Retry(ExponentialBackoff(cap=0.5, base=0.05), settings.REDIS_RETRIES),
        )
        _redis_loop = loop
    return _redis


# Reported by the health/readiness path. Deliberately a fixed vocabulary and nothing else:
# no hostname, no port, no username, no password, no provider name, no topology. Which
# Redis this is, and where, is not something an unauthenticated caller needs to learn from
# a status string, and REDIS_URL carries the credential inline.
REDIS_AVAILABLE = "available"
REDIS_TIMEOUT = "timeout"
REDIS_UNAVAILABLE = "unavailable"
REDIS_DISABLED = "disabled"


def status_of(exc: BaseException) -> str:
    """Classify an already-caught connectivity fault into the vocabulary above, for
    structured logs. Takes the exception rather than the URL precisely so that a caller
    logging "why did Redis fail" cannot accidentally log WHERE Redis is."""
    from redis import exceptions as rexc
    return REDIS_TIMEOUT if isinstance(exc, rexc.TimeoutError) else REDIS_UNAVAILABLE


async def ping() -> str:
    """One PING against the shared pool. Returns one of the REDIS_* constants above.

    Never raises: this is the diagnostic a readiness probe and the sampler's structured logs
    call, and a health check that can itself fail is not a health check. "disabled" is not a
    fault — it is a correctly-configured single-instance deployment (see config.validate).
    """
    r = await redis()
    if r is None:
        return REDIS_DISABLED
    try:
        await r.ping()
    except transient_errors() as exc:
        return status_of(exc)
    except Exception:  # noqa: BLE001 — a probe reports, it does not propagate
        log.exception("redis health probe failed for a reason that is not a connection fault")
        return REDIS_UNAVAILABLE
    return REDIS_AVAILABLE


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


# Backoff for a pump that lost its subscription. Capped and jittered: during a provider
# outage EVERY event's pump on EVERY instance reconnects at once, and an unjittered retry
# turns that into a thundering herd against the endpoint that is already struggling.
PUMP_RETRY_MIN = 1.0
PUMP_RETRY_MAX = 30.0


async def _pump(event_id: str, ready: asyncio.Event | None = None) -> None:
    """Relay one event's Redis channel into this process's queues, resubscribing for as long
    as anybody is listening.

    `ready` is set once the SUBSCRIBE has actually taken effect — not when this task is
    created. Callers wait on it; see subscribe(). It is optional so the pump can also be
    driven directly (tests do), and it is CLEARED again whenever the subscription drops, so
    that a subscriber arriving mid-outage waits out the resubscribe instead of being told a
    dead pump is live.

    The loop is the fix for a silent, permanent failure: a dropped connection used to raise
    straight out of this task, and because `_pumps` still held the (now dead) task,
    subscribe() saw `event_id in _pumps` and never started a replacement. One momentary
    Redis blip therefore cost that event its cross-worker fan-out — chat, Q&A, polls,
    reactions, presence, every channel — until the LAST local subscriber disconnected, with
    nothing in the logs after the initial error and no way for a host to recover but a
    reload. The task is cancelled by subscribe() when the last subscriber leaves, which is
    what still terminates it.
    """
    delay = PUMP_RETRY_MIN
    try:
        while True:
            try:
                r = await redis()
                pubsub = r.pubsub()
                await pubsub.subscribe(_key(event_id))
                delay = PUMP_RETRY_MIN  # a successful (re)subscribe resets the backoff
                if ready is not None:
                    ready.set()
                try:
                    async for msg in pubsub.listen():
                        if msg.get("type") == "message":
                            try:
                                _fanout(event_id, json.loads(msg["data"]))
                            except (ValueError, TypeError):
                                log.warning("live bus: undecodable payload on %s", _key(event_id))
                finally:
                    if ready is not None:
                        ready.clear()  # this subscription is no longer live
                    with contextlib.suppress(Exception):
                        await pubsub.unsubscribe(_key(event_id))
                        await pubsub.aclose()
            except asyncio.CancelledError:
                raise  # the last subscriber left, or the process is shutting down
            except transient_errors() as exc:
                log.warning("live bus: pump for %s lost its connection (redis_status=%s); "
                            "resubscribing in ~%.0fs", _key(event_id), status_of(exc), delay)
            except Exception:  # noqa: BLE001 — see below
                # Logged with its traceback, not swallowed: an unexpected fault here is a
                # defect and has to be visible. It still retries rather than leaving the event
                # silently unbridged, and the capped backoff keeps a persistent one from
                # spinning.
                log.exception("live bus: pump for %s failed unexpectedly; resubscribing in ~%.0fs",
                              _key(event_id), delay)
            await asyncio.sleep(delay + random.uniform(0, delay / 2))
            delay = min(delay * 2, PUMP_RETRY_MAX)
    finally:
        # Only on the way out for good — the retry loop above never reaches here.
        if ready is not None and _pump_ready.get(event_id) is ready:
            _pump_ready.pop(event_id, None)


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
    _pump_ready.clear()

    # Clear the cache as well as closing it: a process that starts a new event loop
    # afterwards (tests do exactly this) must build a fresh pool rather than reuse one whose
    # connections are bound to the loop that just died.
    client, client_loop = _redis, _redis_loop
    _redis = None
    _redis_loop = None
    if client is not None and client_loop is loop:
        await client.aclose()


# ── presence (participants) ───────────────────────────────────────────────────
# One record per identity: {identity, name, role, muted, speaking, hand, quality,
# on_stage, banned, joined_at, last_seen}. Identity is the LiveKit identity == user id (or
# "viewer-<uuid>" for anonymous viewers), so the console WS and the LiveKit webhook
# update the SAME record instead of double-counting a person.

_sessions_memory: dict[str, dict[str, dict]] = {}
_summary_memory: dict[str, dict] = {}
_sync_redis_client = None


def sync_redis():
    """Synchronous Redis client for read-only analytics aggregation."""
    global _sync_redis_client
    if not settings.REDIS_URL:
        return None
    if _sync_redis_client is None:
        try:
            import redis as _sync_redis
            _sync_redis_client = _sync_redis.Redis.from_url(
                settings.REDIS_URL, decode_responses=True, socket_timeout=2.0, socket_connect_timeout=2.0
            )
        except Exception:
            _sync_redis_client = None
    return _sync_redis_client


def _pkey(event_id) -> str:
    return f"live:{eid(event_id)}:participants"


def _sessions_key(event_id) -> str:
    return f"live:{eid(event_id)}:sessions"


def _summary_key(event_id) -> str:
    return f"analytics:{eid(event_id)}:summary"


# Coarse client class recorded on each viewing session (services/broadcast.classify_ua):
# device and browser family only, never the user-agent string, so a session can be counted
# per browser class without anything that could fingerprint a person.
SESSION_AGENT_FIELDS = ("device", "browser")


def _open_or_extend(sessions: list, now: float, agent: dict | None) -> None:
    """Extend the identity's open interval, or open a new one.

    A new interval is one viewing session. It gets an opaque random id (`sid`). That id is
    never derived from the person, so two sessions are never claimed to be the same viewer
    by anything other than the credential-backed identity they were recorded under.
    """
    agent = {k: agent[k] for k in SESSION_AGENT_FIELDS if agent and agent.get(k)}
    if sessions and sessions[-1].get("left_at") is None:
        sessions[-1]["last_seen"] = max(sessions[-1].get("last_seen") or now, now)
        for k, v in agent.items():
            sessions[-1].setdefault(k, v)
    else:
        sessions.append({"sid": secrets.token_hex(8), "joined_at": now, "last_seen": now,
                         "left_at": None, **agent})


async def session_record_join(event_id, identity: str, role: str = "viewer", name: str | None = None,
                              joined_at: float | None = None, agent: dict | None = None) -> dict:
    """Record a viewer join into the event session ledger."""
    event_id = eid(event_id)
    now = joined_at if joined_at is not None else time.time()
    r = await redis()
    if r is None:
        store = _sessions_memory.setdefault(event_id, {})
        user_record = store.get(identity, {
            "identity": identity, "role": role, "name": name, "sessions": []
        })
        user_record["role"] = role
        if name:
            user_record["name"] = name
        _open_or_extend(user_record.setdefault("sessions", []), now, agent)
        store[identity] = user_record
        return user_record

    raw = await r.hget(_sessions_key(event_id), identity)
    user_record = json.loads(raw) if raw else {
        "identity": identity, "role": role, "name": name, "sessions": []
    }
    user_record["role"] = role
    if name:
        user_record["name"] = name
    _open_or_extend(user_record.setdefault("sessions", []), now, agent)
    await r.hset(_sessions_key(event_id), identity, json.dumps(user_record, default=str))
    return user_record


def _apply_playback(session: dict, startup_ms: int | None, failure: str | None) -> None:
    # Only the FIRST startup of a session is its startup time; a later report is a re-attach.
    if startup_ms is not None and session.get("startup_ms") is None:
        session["startup_ms"] = startup_ms
    if failure:
        kinds = session.setdefault("failure_kinds", {})
        kinds[failure] = kinds.get(failure, 0) + 1
        session["failures"] = sum(kinds.values())


async def session_record_playback(event_id, identity: str, startup_ms: int | None = None,
                                  failure: str | None = None) -> bool:
    """Attach the viewer's own playback report (time to first frame, a playback failure) to
    their OPEN viewing session. Returns False when there is no open session to attach it to:
    a report is never allowed to create a session."""
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        rec = _sessions_memory.get(event_id, {}).get(identity)
        sessions = rec.get("sessions", []) if rec else []
        if not sessions or sessions[-1].get("left_at") is not None:
            return False
        _apply_playback(sessions[-1], startup_ms, failure)
        return True
    raw = await r.hget(_sessions_key(event_id), identity)
    if not raw:
        return False
    rec = json.loads(raw)
    sessions = rec.get("sessions", [])
    if not sessions or sessions[-1].get("left_at") is not None:
        return False
    _apply_playback(sessions[-1], startup_ms, failure)
    await r.hset(_sessions_key(event_id), identity, json.dumps(rec, default=str))
    return True


async def session_record_heartbeat(event_id, identity: str, timestamp: float | None = None) -> None:
    """Update last_seen timestamp on both active presence and current session."""
    event_id = eid(event_id)
    now = timestamp if timestamp is not None else time.time()
    r = await redis()
    if r is None:
        room = _memory.get(event_id, {})
        if identity in room:
            room[identity]["last_seen"] = now
        store = _sessions_memory.get(event_id, {})
        if identity in store:
            sessions = store[identity].get("sessions", [])
            if sessions and sessions[-1].get("left_at") is None:
                sessions[-1]["last_seen"] = now
        return

    raw_p = await r.hget(_pkey(event_id), identity)
    if raw_p:
        rec = json.loads(raw_p)
        rec["last_seen"] = now
        await r.hset(_pkey(event_id), identity, json.dumps(rec, default=str))

    raw_s = await r.hget(_sessions_key(event_id), identity)
    if raw_s:
        u_rec = json.loads(raw_s)
        sessions = u_rec.get("sessions", [])
        if sessions and sessions[-1].get("left_at") is None:
            sessions[-1]["last_seen"] = now
            await r.hset(_sessions_key(event_id), identity, json.dumps(u_rec, default=str))


async def session_record_leave(event_id, identity: str, left_at: float | None = None) -> dict | None:
    """Close the active session for an identity."""
    event_id = eid(event_id)
    now = left_at if left_at is not None else time.time()
    r = await redis()
    if r is None:
        store = _sessions_memory.get(event_id, {})
        if identity in store:
            sessions = store[identity].get("sessions", [])
            if sessions and sessions[-1].get("left_at") is None:
                sessions[-1]["left_at"] = now
                sessions[-1]["last_seen"] = now
            return store[identity]
        return None

    raw_s = await r.hget(_sessions_key(event_id), identity)
    if raw_s:
        u_rec = json.loads(raw_s)
        sessions = u_rec.get("sessions", [])
        if sessions and sessions[-1].get("left_at") is None:
            sessions[-1]["left_at"] = now
            sessions[-1]["last_seen"] = now
            await r.hset(_sessions_key(event_id), identity, json.dumps(u_rec, default=str))
        return u_rec
    return None


_SESSION_DETAIL_FIELDS = ("sid", *SESSION_AGENT_FIELDS, "startup_ms", "failures", "failure_kinds")


def _extract_all_intervals(sessions_data: dict[str, dict], presence_data: dict[str, dict] | None = None) -> list[dict]:
    out: list[dict] = []
    seen_identities: set[str] = set()

    for identity, u_rec in sessions_data.items():
        seen_identities.add(identity)
        role = u_rec.get("role", "viewer")
        name = u_rec.get("name")
        for s in u_rec.get("sessions", []):
            out.append({
                "identity": identity,
                "role": role,
                "name": name,
                "joined_at": s.get("joined_at"),
                "last_seen": s.get("last_seen"),
                "left_at": s.get("left_at"),
                # Per-session detail for services/viewing_sessions.py. Absent on intervals
                # recorded before it existed, which every reader treats as "not reported".
                **{k: s[k] for k in _SESSION_DETAIL_FIELDS if k in s},
            })

    if presence_data:
        for identity, p in presence_data.items():
            has_active = any(row["identity"] == identity and row["left_at"] is None for row in out)
            if not has_active and p.get("joined_at"):
                out.append({
                    "identity": identity,
                    "role": p.get("role", "viewer"),
                    "name": p.get("name"),
                    "joined_at": p.get("joined_at"),
                    "last_seen": p.get("last_seen") or p.get("joined_at"),
                    "left_at": None,
                    "waiting": p.get("waiting", False),
                    "on_stage": p.get("on_stage", False),
                })
    return out


async def session_get_all(event_id) -> list[dict]:
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        s_data = _sessions_memory.get(event_id, {})
        p_data = _memory.get(event_id, {})
        return _extract_all_intervals(s_data, p_data)

    raw_sessions = await r.hgetall(_sessions_key(event_id))
    s_data = {k: json.loads(v) for k, v in raw_sessions.items()}
    raw_presence = await r.hgetall(_pkey(event_id))
    p_data = {k: json.loads(v) for k, v in raw_presence.items()}
    return _extract_all_intervals(s_data, p_data)


def session_get_all_sync(event_id) -> list[dict]:
    event_id = eid(event_id)
    r = sync_redis()
    if r is None:
        s_data = _sessions_memory.get(event_id, {})
        p_data = _memory.get(event_id, {})
        return _extract_all_intervals(s_data, p_data)
    try:
        raw_sessions = r.hgetall(_sessions_key(event_id))
        s_data = {k: json.loads(v) for k, v in raw_sessions.items()}
        raw_presence = r.hgetall(_pkey(event_id))
        p_data = {k: json.loads(v) for k, v in raw_presence.items()}
        return _extract_all_intervals(s_data, p_data)
    except Exception:
        s_data = _sessions_memory.get(event_id, {})
        p_data = _memory.get(event_id, {})
        return _extract_all_intervals(s_data, p_data)


async def summary_get(event_id) -> dict | None:
    event_id = eid(event_id)
    r = await redis()
    if r is None:
        return _summary_memory.get(event_id)
    raw = await r.get(_summary_key(event_id))
    return json.loads(raw) if raw else _summary_memory.get(event_id)


def summary_get_sync(event_id) -> dict | None:
    event_id = eid(event_id)
    r = sync_redis()
    if r is None:
        return _summary_memory.get(event_id)
    try:
        raw = r.get(_summary_key(event_id))
        return json.loads(raw) if raw else _summary_memory.get(event_id)
    except Exception:
        return _summary_memory.get(event_id)


async def summary_set(event_id, summary: dict) -> None:
    event_id = eid(event_id)
    _summary_memory[event_id] = summary
    r = await redis()
    if r is not None:
        try:
            await r.set(_summary_key(event_id), json.dumps(summary, default=str))
        except Exception:
            pass


def summary_set_sync(event_id, summary: dict) -> None:
    event_id = eid(event_id)
    _summary_memory[event_id] = summary
    r = sync_redis()
    if r is not None:
        try:
            r.set(_summary_key(event_id), json.dumps(summary, default=str))
        except Exception:
            pass


async def session_finalize(event_id, end_time: float | None = None, broadcast_start_ts: float | None = None) -> dict:
    """Close all open sessions and calculate durable watch time summary.

    The summary also carries the per-session metrics (services/viewing_sessions.py) under
    "sessions", so they survive in BroadcastSession.settings["analytics_summary"] once the
    in-memory ledger is gone. `broadcast_start_ts` anchors the join-time distribution; it is
    omitted (and the distribution with it) when the caller does not know the start."""
    from app.services.watch_time import calculate_viewer_watch_time
    from app.services.viewing_sessions import summarize_sessions
    event_id = eid(event_id)
    now = end_time if end_time is not None else time.time()

    # 1. Sync any active presence into sessions
    presence_items = await presence_all(event_id)
    for p in presence_items:
        ident = p.get("identity")
        if ident:
            await session_record_join(
                event_id, ident,
                role=p.get("role", "viewer"),
                name=p.get("name"),
                joined_at=p.get("joined_at"),
            )
            if p.get("last_seen"):
                await session_record_heartbeat(event_id, ident, p["last_seen"])

    # 2. Close all open sessions
    r = await redis()
    if r is None:
        store = _sessions_memory.get(event_id, {})
        for u_rec in store.values():
            for s in u_rec.get("sessions", []):
                if s.get("left_at") is None:
                    s["left_at"] = min(s.get("last_seen") or now, now)
        all_sessions = _extract_all_intervals(store)
    else:
        raw_sessions = await r.hgetall(_sessions_key(event_id))
        s_data = {k: json.loads(v) for k, v in raw_sessions.items()}
        for ident, u_rec in s_data.items():
            changed = False
            for s in u_rec.get("sessions", []):
                if s.get("left_at") is None:
                    s["left_at"] = min(s.get("last_seen") or now, now)
                    changed = True
            if changed:
                await r.hset(_sessions_key(event_id), ident, json.dumps(u_rec, default=str))
        all_sessions = _extract_all_intervals(s_data)

    summary = calculate_viewer_watch_time(all_sessions, now_ts=now, event_end_ts=now)
    summary["sessions"] = summarize_sessions(all_sessions, broadcast_start_ts=broadcast_start_ts,
                                             now_ts=now, event_end_ts=now)
    summary["finalized_at"] = now
    await summary_set(event_id, summary)
    return summary


async def presence_upsert(event_id, identity: str, patch: dict) -> dict:
    """Merge `patch` into a participant, creating it if absent. Returns the full record."""
    event_id = eid(event_id)
    now = time.time()
    r = await redis()
    if r is None:
        room = _memory.setdefault(event_id, {})
        base = room.get(identity, {"identity": identity, "joined_at": now, "last_seen": now})
        rec = {**base, **patch}
        if "last_seen" not in patch and "last_seen" not in rec:
            rec["last_seen"] = rec.get("joined_at", now)
        room[identity] = rec
        await session_record_join(
            event_id, identity,
            role=rec.get("role", "viewer"),
            name=rec.get("name"),
            joined_at=rec.get("joined_at"),
            agent=rec,
        )
        return rec
    raw = await r.hget(_pkey(event_id), identity)
    base = json.loads(raw) if raw else {"identity": identity, "joined_at": now, "last_seen": now}
    rec = {**base, **patch}
    if "last_seen" not in patch and "last_seen" not in rec:
        rec["last_seen"] = rec.get("joined_at", now)
    await r.hset(_pkey(event_id), identity, json.dumps(rec, default=str))
    await session_record_join(
        event_id, identity,
        role=rec.get("role", "viewer"),
        name=rec.get("name"),
        joined_at=rec.get("joined_at"),
        agent=rec,
    )
    return rec


async def presence_remove(event_id, identity: str) -> dict | None:
    event_id = eid(event_id)
    await session_record_leave(event_id, identity)
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
    try:
        await session_finalize(event_id)
    except Exception:
        pass
    r = await redis()
    if r is None:
        _memory.pop(event_id, None)
        _state.pop(event_id, None)
        _bans.pop(event_id, None)
        _reactions.pop(event_id, None)
        _admitted_memory.pop(event_id, None)
    else:
        await r.delete(_pkey(event_id), _bkey(event_id), _skey(event_id), _rkey(event_id),
                       _akey(event_id))


# ── admission ledger (capacity protection, services/admission.py) ─────────────────────────
# identity -> {"admitted_at", "seen"} for every viewer this broadcast has admitted. It exists
# so a viewer who briefly drops (socket blip, page refresh, phone sleep) is recognised on the
# way back in and never loses their place to the ceiling. Presence cannot do that: it is
# deleted on every disconnect. Written only by GET /watch (sync) and the socket heartbeat
# (async); cleared with presence when the room ends.

_admitted_memory: dict[str, dict[str, dict]] = {}


def _akey(event_id) -> str:
    return f"live:{eid(event_id)}:admitted"


def admission_decide_sync(event_id, identity: str, ceiling: int | None, hold_seconds: float,
                          now: float | None = None) -> tuple[bool, int | None]:
    """(admitted, occupied) for `identity` against `ceiling`.

    * already admitted this broadcast -> admitted, always (and its `seen` refreshed);
    * no ceiling                     -> admitted and recorded, so that a ceiling configured
                                        mid-event never displaces anyone already watching;
    * otherwise admitted only while fewer than `ceiling` admitted viewers have been seen
      within `hold_seconds`.

    Read-modify-write without a lock, like state_set: two simultaneous first-time viewers can
    both take the last slot. The ceiling is a protective soft limit, and overshooting it by a
    handful is the safe direction to be wrong in. Redis unreachable -> admitted (fail open).
    Never blocking a viewer because the ledger itself is down."""
    event_id = eid(event_id)
    now = now if now is not None else time.time()
    r = sync_redis()
    if r is None:
        store = _admitted_memory.setdefault(event_id, {})
        return _admission_apply(store, identity, ceiling, hold_seconds, now, write=store.__setitem__)
    try:
        raw = r.hgetall(_akey(event_id))
        store = {k: json.loads(v) for k, v in raw.items()}
        return _admission_apply(
            store, identity, ceiling, hold_seconds, now,
            write=lambda ident, rec: r.hset(_akey(event_id), ident, json.dumps(rec)))
    except Exception:  # noqa: BLE001 — see the docstring: the ledger failing must not block anyone
        log.warning("admission ledger unavailable for event %s — admitting", event_id)
        return True, None


def _admission_apply(store: dict, identity: str, ceiling, hold_seconds, now, write):
    occupied = sum(1 for ident, rec in store.items()
                   if ident != identity and (rec.get("seen") or 0) >= now - hold_seconds)
    known = store.get(identity)
    if known is not None or ceiling is None or occupied < ceiling:
        write(identity, {"admitted_at": (known or {}).get("admitted_at", now), "seen": now})
        return True, occupied + 1
    return False, occupied


async def admission_touch(event_id, identity: str, now: float | None = None) -> None:
    """Refresh an ADMITTED viewer's `seen` (socket heartbeat). Never admits anyone."""
    event_id = eid(event_id)
    now = now if now is not None else time.time()
    r = await redis()
    if r is None:
        rec = _admitted_memory.get(event_id, {}).get(identity)
        if rec is not None:
            rec["seen"] = now
        return
    raw = await r.hget(_akey(event_id), identity)
    if raw:
        rec = json.loads(raw)
        rec["seen"] = now
        await r.hset(_akey(event_id), identity, json.dumps(rec))


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
