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
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import event as event_crud
from ..db import get_db
from ..models import EventRegistration, User
from ..ratelimit import SlidingWindow
from ..security import ALGORITHM, decode_registration_token
from ..services import bus, livekit
from ..services import moderation as mod
# Importing these registers the host/producer actions and the contributor-backstage
# actions into mod.ACTIONS, the host-only permission set, and their snapshot
# contributions. Import is one-way (broadcast/contributor -> moderation), which is why it
# happens here and not in moderation.
from ..services import broadcast  # noqa: F401
from ..services import contributor  # noqa: F401

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


def _registration_from_reg_token(reg: str | None, event_id: uuid.UUID, db: Session) -> EventRegistration | None:
    """The anonymous-viewer counterpart to _user_from_token: a self-serve name+email
    registration (routers/events.py register_for_event) stands in for a login. Same token
    the /watch HTTP endpoint already accepts as `?reg=`, reused here so one registration
    covers both video access and chat/Q&A/polls."""
    if not reg:
        return None
    email = decode_registration_token(reg, event_id)
    if not email:
        return None
    return event_crud.get_registration(db, event_id, email)


async def _accept(websocket: WebSocket, event_id: uuid.UUID) -> bool:
    """accept(), swallowing the one specific race that isn't a bug: resolve_ctx/ensure_state
    await a thread/Redis round-trip, and a client that navigates away or closes the tab
    mid-flight leaves the transport already torn down by the time we get here — uvicorn then
    rejects the belated accept with a RuntimeError ("Expected 'websocket.send' or
    'websocket.close', but got 'websocket.accept'"). Returns False so the caller bails out
    without touching the (already-dead) socket again; any OTHER RuntimeError — a real bug —
    still propagates."""
    try:
        await websocket.accept()
        return True
    except RuntimeError as exc:
        if "websocket.accept" not in str(exc):
            raise
        log.debug("live socket for event %s: client gone before accept completed: %s", event_id, exc)
        return False


@router.websocket("/events/{event_id}/ws")
async def live_socket(websocket: WebSocket, event_id: uuid.UUID, token: str | None = None, reg: str | None = None, link: str | None = None):
    # Real device/platform mix for the host's analytics panel, straight off the handshake.
    # Nothing is inferred beyond what the UA states; unknowns stay "Unknown".
    agent = broadcast.classify_ua(websocket.headers.get("user-agent"))
    db = next(get_db())
    try:
        user = _user_from_token(token, db)
        # Anonymous credentials are alternatives, not a privilege escalation: a valid
        # registration or host-issued access link can establish a viewer socket. If a
        # logged-in user is from the wrong org, we still allow a valid event-specific link
        # to admit them as a guest rather than letting the unrelated JWT block the share link.
        registration = _registration_from_reg_token(reg, event_id, db)
    finally:
        db.close()
    # Org isolation + per-event moderator check happen BEFORE any envelope is sent, so an
    # unauthorized socket never sees application data. We still `accept()` right before each
    # rejection: a WebSocket close code can only reach the BROWSER once the handshake has
    # completed — closing pre-accept is reported to the ASGI server as a bare HTTP 403 and
    # the browser's CloseEvent.code comes back as 1006 (spec-mandated for a failed handshake),
    # which silently defeats the client's FATAL_CODES-based reconnect-suppression
    # (useEventStream.js) and makes it retry an expired/invalid token forever.
    if user is None and registration is None and not link:
        if not await _accept(websocket, event_id):
            return
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired session")
        return

    ctx = None
    if user is not None:
        ctx = await asyncio.to_thread(mod.resolve_ctx, event_id, user)
    if ctx is None and registration is not None:
        ctx = await asyncio.to_thread(mod.resolve_ctx_from_registration, event_id, registration)
    if ctx is None and link:
        # Final credential path for a private event Share URL. This is intentionally
        # resolved server-side before accept/snapshot so a direct socket URL cannot bypass
        # the same event-bound, revocable access-link rules as GET /events/{id}/watch.
        ctx = await asyncio.to_thread(mod.resolve_ctx_from_access_link, event_id, link)
    if ctx is None:
        if not await _accept(websocket, event_id):
            return
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Event not found")
        return
    if await bus.is_banned(ctx.event_id, ctx.identity):
        if not await _accept(websocket, event_id):
            return
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="You have been removed from this event")
        return

    if ctx.can_contribute:
        # A backstage session is time-boxed (invite join window + expiry) and revocable
        # independent of ban/org membership — checked once at connect, same as is_banned
        # above, not per-frame.
        session = await asyncio.to_thread(contributor.load_session_sync, ctx.event_id, ctx.user_id)
        gate_error = contributor.join_window_error(session, datetime.now(timezone.utc))
        if gate_error:
            if not await _accept(websocket, event_id):
                return
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=gate_error)
            return

    # Rehydrate this event's live settings before anyone joins, so a freshly-booted worker
    # applies the host's waiting-room / chat state instead of serving defaults.
    state = await broadcast.ensure_state(ctx)

    if not await _accept(websocket, event_id):
        return
    limiter = SlidingWindow(RATE_LIMIT, RATE_WINDOW)

    async with bus.subscribe(ctx.event_id) as queue:
        # This connection is a participant too — one presence record per identity, so a
        # moderator watching from two tabs still counts once.
        role = "host" if ctx.can_host else "moderator" if ctx.can_moderate else \
            "speaker" if ctx.can_contribute else "viewer"
        if ctx.can_contribute:
            await asyncio.to_thread(contributor.mark_connected, ctx.event_id, ctx.user_id)
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
            """Only this task writes to the socket, so sends never interleave.

            One thing this loop watches for itself: a "session"/"removed" envelope
            addressed to THIS identity (published by moderation._participant_action on
            participant.remove / participant.ban — see services/moderation.py). It's
            broadcast to every connection on the event like anything else on the bus, but
            only the matching connection is meant to act on it — deliver it, then close
            this socket from the server side with the same policy-violation code the
            connect path already uses for a banned rejoin attempt, so a removed viewer
            can't just keep sitting on the page with a live socket to a room they were
            just kicked out of. useEventStream.js already treats that code as fatal and
            does not retry."""
            while True:
                env = await queue.get()
                await websocket.send_json(env)
                if (
                    env.get("channel") == "session"
                    and env.get("type") == "removed"
                    and env.get("data", {}).get("identity") == ctx.identity
                ):
                    await websocket.close(
                        code=status.WS_1008_POLICY_VIOLATION,
                        reason=env["data"].get("reason") or "Removed from event",
                    )
                    return

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
            if ctx.can_contribute:
                await asyncio.to_thread(contributor.mark_disconnected, ctx.event_id, ctx.user_id)


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

    kind = evt.event

    # Resolved via our own LiveIngressEndpoint row (by ingress_id), not evt.room: an
    # ingress-only lifecycle event can fire before any publisher has connected, and
    # IngressInfo does not reliably carry room_name until one does.
    if kind in ("ingress_started", "ingress_ended") and evt.ingress_info:
        updated = await asyncio.to_thread(broadcast.record_ingress_status, evt.ingress_info)
        if updated:
            ingress_event_id, data = updated
            await bus.publish(ingress_event_id, "moderator", "ingress.status", data)
            verb = "connected" if kind == "ingress_started" else "disconnected"
            await mod.feed_activity(ingress_event_id, "system", f"Live input {data['title']} {verb}", persist=True)
        return {"ok": True, "event": kind}

    event_id = mod.event_id_from_room(evt.room.name if evt.room else None)
    if not event_id:
        return {"ignored": evt.event}

    p = evt.participant
    # A secondary connection (a host's own console, or a contributor's return-feed monitor —
    # see services/livekit.py's secondary()/primary()) connects under a TAGGED identity so it
    # doesn't evict that same user's primary one. Presence must still be keyed by the PRIMARY
    # identity — otherwise a host's own publish shows up as a phantom separate participant,
    # and health_of() reports "No media is being published" over a broadcast that's actually
    # fine (see test_livekit_identity.py's test_tagged_identity_resolves_back_to_its_owner
    # docstring). primary() is a safe no-op on every other identity shape (guest/guest-link/
    # viewer/ingress/plain user id), so it's applied unconditionally here.
    identity = livekit.primary(p.identity) if p else None

    if kind == "participant_joined" and p:
        rec = await bus.presence_upsert(event_id, identity, {
            "name": p.name or identity, "role": "viewer", "muted": False,
            "speaking": False, "hand": False, "quality": "excellent",
        })
        await bus.publish(event_id, "participants", "participant.join", rec)
        await mod.feed_activity(event_id, "join", f"{rec['name']} joined the event")

    elif kind == "participant_left" and p:
        rec = await bus.presence_remove(event_id, identity)
        if rec:
            await bus.publish(event_id, "participants", "participant.leave", rec)
            await mod.feed_activity(event_id, "leave", f"{rec.get('name')} left the event")

    elif kind in _TRACK_EVENTS and p:
        rec = await bus.presence_upsert(event_id, identity, {"publishing": _TRACK_EVENTS[kind]})
        await bus.publish(event_id, "participants", "participant.update", rec)
        # LiveKit's server-side mute (services.livekit.mute_participant) only mutes the
        # tracks that existed at the moment it was called — it has no memory of "this
        # identity should stay muted." A track (re)published afterward (a reconnect, an
        # ICE restart, a renegotiation after a brief network blip, or simply the mute
        # call landing before the track finished publishing) always starts UNMUTED at the
        # SFU, so audio quietly starts flowing again a few seconds later while the console
        # still shows the mute icon as on — presence, not LiveKit, is what the UI trusts.
        # Presence is the source of truth for "should this identity be muted right now",
        # so re-assert the enforcement on every fresh publish rather than only at the
        # moment a moderator clicked mute.
        if kind == "track_published" and rec.get("muted"):
            await livekit.mute_participant(evt.room.name, p.identity, True)

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
        if not recording:
            # The ONLY place the real file size and final status exist — until now they
            # were dropped, so LiveRecording.size_bytes stayed NULL and the console could
            # never say whether a recording was actually captured.
            updated = await asyncio.to_thread(broadcast.record_egress_result, evt.egress_info)
            if updated:
                await bus.publish(event_id, "recording", "recording.update", updated)

    return {"ok": True, "event": kind}