"""Live event realtime surface.

  WS   /live/events/{event_id}/ws?token=<jwt>   console + viewer socket (both directions)
  POST /live/webhooks/livekit                   LiveKit room events -> presence + bus

That is the whole API addition. Everything the console does travels over the socket:
adding ~30 REST endpoints beside it would double the auth, permission and audit surface
for no gain, and every mutation has to be broadcast anyway. The initial snapshot arrives
as the first frame, so there is no separate "load the page" request either — which is
what makes "never refresh" true rather than aspirational.

Which event to moderate is resolved by the client with the EXISTING /events API
(`GET /events?status=live`); no new lookup endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import User
from ..ratelimit import SlidingWindow
from ..security import ALGORITHM
from ..services import bus, livekit
from ..services import moderation as mod
# Importing this registers the host/producer actions into mod.ACTIONS, the host-only
# permission set, and the broadcast/analytics half of the opening snapshot. Import is
# one-way (broadcast -> moderation), which is why it happens here and not in moderation.
from ..services import broadcast  # noqa: F401

log = logging.getLogger(__name__)
router = APIRouter(prefix="/live", tags=["live"])

# Rate limit per CONNECTION (not per user): a moderator with two tabs gets two budgets,
# which is correct — the limit exists to stop one socket flooding the bus. The window
# itself is ratelimit.SlidingWindow, shared with the HTTP limiter so there is one
# implementation of the algorithm.
RATE_LIMIT = 30           # actions ...
RATE_WINDOW = 10.0        # ... per this many seconds
IDLE_TIMEOUT = 90.0       # no frame at all for this long -> reap the socket (client pings every 15s)


def _user_from_token(token: str | None, db: Session) -> User | None:
    """Same verification as security.get_current_user, but reading the token from the
    query string: the browser WebSocket API cannot set an Authorization header."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user = db.get(User, payload["sub"])
    except (JWTError, KeyError):
        return None
    return user if user and user.is_active else None


@router.websocket("/events/{event_id}/ws")
async def live_socket(websocket: WebSocket, event_id: uuid.UUID, token: str | None = None):
    # Real device/platform mix for the host's analytics panel, straight off the handshake.
    # Nothing is inferred beyond what the UA states; unknowns stay "Unknown".
    agent = broadcast.classify_ua(websocket.headers.get("user-agent"))
    db = next(get_db())
    try:
        user = _user_from_token(token, db)
    finally:
        db.close()
    # Org isolation + per-event moderator check happen BEFORE any envelope is sent, so an
    # unauthorized socket never sees application data. We still `accept()` right before each
    # rejection: a WebSocket close code can only reach the BROWSER once the handshake has
    # completed — closing pre-accept is reported to the ASGI server as a bare HTTP 403 and
    # the browser's CloseEvent.code comes back as 1006 (spec-mandated for a failed handshake),
    # which silently defeats the client's FATAL_CODES-based reconnect-suppression
    # (useEventStream.js) and makes it retry an expired/invalid token forever.
    if user is None:
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
        return

    ctx = await asyncio.to_thread(mod.resolve_ctx, event_id, user)
    if ctx is None:
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Event not found")
        return
    if await bus.is_banned(ctx.event_id, ctx.identity):
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="You have been removed from this event")
        return

    # Rehydrate this event's live settings before anyone joins, so a freshly-booted worker
    # applies the host's waiting-room / chat state instead of serving defaults.
    state = await broadcast.ensure_state(ctx)

    await websocket.accept()
    limiter = SlidingWindow(RATE_LIMIT, RATE_WINDOW)

    async with bus.subscribe(ctx.event_id) as queue:
        # This connection is a participant too — one presence record per identity, so a
        # moderator watching from two tabs still counts once.
        role = "host" if ctx.can_host else "moderator" if ctx.can_moderate else "viewer"
        # Waiting room holds plain attendees for the host to admit; staff never wait.
        waiting = bool(state.get("waiting_room")) and role == "viewer"
        rec = await bus.presence_upsert(ctx.event_id, ctx.identity, {
            "name": ctx.name, "role": role, "waiting": waiting,
            "muted": False, "speaking": False, "hand": False, "quality": "excellent",
            **agent,
        })
        await websocket.send_json(bus.envelope("moderator", "snapshot", await mod.snapshot(ctx)))
        await bus.publish(ctx.event_id, "participants", "participant.join", rec)
        if waiting:
            # Surfaces in the host console's waiting-room queue.
            await bus.publish(ctx.event_id, "stage", "waiting.join", rec)

        async def writer():
            """Only this task writes to the socket, so sends never interleave."""
            while True:
                await websocket.send_json(await queue.get())

        pump = asyncio.create_task(writer())
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(websocket.receive_json(), timeout=IDLE_TIMEOUT)
                except ValueError:
                    # Malformed JSON (JSONDecodeError subclasses ValueError). One bad frame
                    # is not a reason to drop a moderator mid-event.
                    continue
                if not isinstance(frame, dict):
                    continue
                action = frame.get("action")

                if action == "ping":
                    # Round-trip latency + liveness. Answered on THIS socket only and
                    # exempt from the rate limit, so a heartbeat can't be throttled out.
                    await websocket.send_json(bus.envelope("moderator", "pong", {"t": frame.get("t")}))
                    continue

                if not limiter.allow():
                    await websocket.send_json(bus.envelope("moderator", "error", {
                        "action": action, "message": "Slow down — too many actions"}))
                    continue

                error = await mod.dispatch(ctx, action, frame.get("payload") or {})
                if error:
                    await websocket.send_json(bus.envelope("moderator", "error", {"action": action, "message": error}))
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass
        except Exception:  # noqa: BLE001 — log and close cleanly rather than 500 a socket
            log.exception("live socket failed for event %s", event_id)
        finally:
            pump.cancel()
            gone = await bus.presence_remove(ctx.event_id, ctx.identity)
            if gone:
                await bus.publish(ctx.event_id, "participants", "participant.leave", gone)


# ── LiveKit webhooks ──────────────────────────────────────────────────────────
# The only server-side source of room truth. Rooms are named `event_<uuid>` (see
# moderation.Ctx.room); anything else is another feature's room and is ignored.
# ponytail: the host studio still opens `stream_<id>` rooms (routers/streams.py) — point
# it at `event_<event_id>` and its participants show up here with no further work.

_TRACK_EVENTS = {"track_published": True, "track_unpublished": False}


@router.post("/webhooks/livekit", include_in_schema=False)
async def livekit_webhook(request: Request, authorization: str = Header(None)):
    receiver = livekit.webhook_receiver()
    if receiver is None:
        # Unsigned bodies are never trusted, so refuse rather than accept-and-ignore.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LiveKit is not configured")
    body = (await request.body()).decode()
    try:
        evt = receiver.receive(body, authorization)
    except Exception:  # noqa: BLE001 — a bad signature is a 401, not a 500
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid LiveKit webhook signature")

    event_id = mod.event_id_from_room(evt.room.name if evt.room else None)
    if not event_id:
        return {"ignored": evt.event}

    kind = evt.event
    p = evt.participant

    if kind == "participant_joined" and p:
        rec = await bus.presence_upsert(event_id, p.identity, {
            "name": p.name or p.identity, "role": "viewer", "muted": False,
            "speaking": False, "hand": False, "quality": "excellent",
        })
        await bus.publish(event_id, "participants", "participant.join", rec)
        await mod.feed_activity(event_id, "join", f"{rec['name']} joined the event")

    elif kind == "participant_left" and p:
        rec = await bus.presence_remove(event_id, p.identity)
        if rec:
            await bus.publish(event_id, "participants", "participant.leave", rec)
            await mod.feed_activity(event_id, "leave", f"{rec.get('name')} left the event")

    elif kind in _TRACK_EVENTS and p:
        rec = await bus.presence_upsert(event_id, p.identity, {"publishing": _TRACK_EVENTS[kind]})
        await bus.publish(event_id, "participants", "participant.update", rec)

    elif kind in ("room_started", "room_finished"):
        started = kind == "room_started"
        if started:
            await bus.publish(event_id, "moderator", "room.status", {"live": True})
            await mod.feed_activity(event_id, "system", "Broadcast room opened", persist=True)
        else:
            # Auto recovery: a room can end because the last publisher dropped, not because
            # the host ended the broadcast. If our session still says live, DON'T tear the
            # broadcast down — flag it degraded and wait for the publisher to come back.
            # Clearing presence here would also wipe the waiting-room queue.
            state = await bus.state_get(event_id)
            recovering = state.get("status") in ("live", "paused")
            await bus.publish(event_id, "moderator", "room.status",
                              {"live": False, "recovering": recovering})
            if recovering:
                await bus.publish(event_id, "broadcast", "broadcast.health", {
                    "level": "down", "issues": ["Media room dropped — waiting for the publisher to reconnect"],
                    "recovering": True,
                })
                await mod.feed_activity(event_id, "system", "Media room dropped — auto-recovery armed", persist=True)
            else:
                await bus.presence_clear(event_id)
                await mod.feed_activity(event_id, "system", "Stream ended", persist=True)

    elif kind in ("egress_started", "egress_ended"):
        recording = kind == "egress_started"
        await bus.publish(event_id, "moderator", "recording.status", {"recording": recording})
        await mod.feed_activity(event_id, "recording", "Recording started" if recording else "Recording finished", persist=True)

    return {"ok": True, "event": kind}
