"""Host / producer domain: broadcast lifecycle, recording, live control settings, stage
management and analytics.

This module EXTENDS services/moderation.py rather than standing beside it: its actions are
registered into the same dispatcher (one socket, one auth check, one audit path) and its
snapshot contribution is registered as a snapshot extra. Nothing here duplicates the
moderator console's chat/Q&A/poll/announcement logic — the host console reuses it.

Import direction is one-way (broadcast -> moderation) to keep it acyclic; routers/live.py
imports this module, which is what performs the registration.

Two rules this file sticks to:

  * A control that cannot be enforced says so. Every LiveKit call returns whether it
    actually took effect, and that boolean is stored and broadcast. The console shows
    "recorded, not enforced" instead of implying a stream was cut or a file was written.
  * No invented telemetry. Every analytics number below is computed from a real row, a
    real presence record or a real timestamp. Things with no source in this stack
    (per-viewer geography, encoder bitrate) are reported as null with a reason, never
    filled with a plausible number.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from ..crud import commercial as commercial_crud
from ..crud.admin import get_feature_flag_by_key
from ..crud.event import is_memorial_category
from ..models import (
    AnalyticsSnapshot,
    BroadcastSession,
    Event,
    LiveIngressEndpoint,
    LiveMessage,
    LivePoll,
    LiveQuestion,
    LiveRecording,
    Organization,
    Subscription,
    User,
)
from . import bus, livekit, platform_settings, validation, webhooks
from . import moderation as mod

log = logging.getLogger(__name__)

SAMPLE_SECONDS = 15          # analytics sampling cadence == retention-graph resolution
RETENTION_POINTS = 240       # ~1h of history at 15s in the initial snapshot

# Live control defaults. Seeded from the Event's stored feature flags on first go-live, then
# owned by the session so a mid-broadcast toggle never rewrites the event's configuration.
DEFAULT_SETTINGS = {
    "chat_enabled": True, "qa_enabled": True, "polls_enabled": True, "reactions_enabled": True,
    "slow_mode_seconds": 0, "subscriber_only": False, "emoji_only": False,
    "profanity_filter": True, "spam_filter": True, "auto_moderation": False,
    "waiting_room": False, "raise_hand_enabled": True, "allow_screen_share": True,
    # Media TARGETS — what the host asked the encoder for. What the browser actually
    # granted is reported back separately by the publisher (see participant.state).
    "resolution": "1080p", "framerate": 30, "bitrate_kbps": 4500, "adaptive": True,
    "noise_cancellation": True, "echo_cancellation": True, "auto_gain": True,
    "mic_gain": 100, "speaker_volume": 100, "background": "none",
    "recording_quality": "1080p", "auto_upload": True,
}

RESOLUTIONS = ("720p", "1080p", "2k", "4k")
BACKGROUNDS = ("none", "blur", "image")

# Whitelist + validation for the single settings action. A dict of specs beats twenty
# near-identical handlers, and an unknown key is dropped rather than trusted.
_BOOL = "bool"
SETTING_SPECS: dict[str, object] = {
    **{k: _BOOL for k in (
        "chat_enabled", "qa_enabled", "polls_enabled", "reactions_enabled", "subscriber_only",
        "emoji_only", "profanity_filter", "spam_filter", "auto_moderation", "waiting_room",
        "raise_hand_enabled", "allow_screen_share", "adaptive", "noise_cancellation",
        "echo_cancellation", "auto_gain", "auto_upload",
    )},
    "slow_mode_seconds": (0, 300),
    "framerate": (15, 60),
    "bitrate_kbps": (500, 20000),
    "mic_gain": (0, 200),
    "speaker_volume": (0, 100),
    "resolution": RESOLUTIONS,
    "recording_quality": RESOLUTIONS,
    "background": BACKGROUNDS,
}


def clean_settings(patch: dict, max_bitrate_kbps: int | None = None) -> dict:
    """Keep only known keys with valid values. Returns the accepted subset.

    `max_bitrate_kbps` narrows bitrate_kbps's own range (SETTING_SPECS' static 500-20000)
    down to the platform's configured ceiling (Settings.jsx's "Storage & Streaming Limits"
    panel, services.platform_settings.max_bitrate_kbps) — a host can still request less,
    never more, than what the platform allows."""
    specs = SETTING_SPECS
    if max_bitrate_kbps is not None:
        lo, hi = SETTING_SPECS["bitrate_kbps"]
        specs = {**SETTING_SPECS, "bitrate_kbps": (lo, min(hi, max_bitrate_kbps))}
    out = {}
    for key, spec in specs.items():
        if key not in patch:
            continue
        value = patch[key]
        if spec == _BOOL:
            out[key] = bool(value)
        elif isinstance(spec, tuple) and len(spec) == 2 and all(isinstance(x, int) for x in spec):
            try:
                out[key] = max(spec[0], min(int(value), spec[1]))
            except (TypeError, ValueError):
                continue
        elif isinstance(spec, tuple) and value in spec:
            out[key] = value
    return out


# ── session state ─────────────────────────────────────────────────────────────

def _iso(dt):
    return mod._iso(dt)


def session_out(s: BroadcastSession | None, settings: dict | None = None) -> dict:
    if s is None:
        return {"id": None, "status": "preview", "started_at": None, "paused_at": None,
                "ended_at": None, "paused_ms": 0, "peak_viewers": 0,
                "settings": {**DEFAULT_SETTINGS, **(settings or {})}}
    return {
        "id": str(s.id), "status": s.status, "started_at": _iso(s.started_at),
        "paused_at": _iso(s.paused_at), "ended_at": _iso(s.ended_at),
        "paused_ms": s.paused_ms, "peak_viewers": s.peak_viewers,
        "ended_reason": s.ended_reason,
        # The live copy wins: the bus is authoritative while a broadcast is running.
        "settings": {**DEFAULT_SETTINGS, **(s.settings or {}), **(settings or {})},
    }


# ── MED-007 recording health (ZST-EC-001) ───────────────────────────────────────────────
# Thin module-level wrappers so the tx workers below stay readable and the import stays lazy
# (recording_comms reaches broadcast indirectly through media_comms -> org_comms). Every one
# of these is called INSIDE the worker that commits the state it describes, so a notice can
# only ever describe a capture that authoritatively exists.

def recording_comms_notify_started(db, recording) -> None:
    """ZST-EC-001 MED-007. Internal operator + asset-owner notice."""
    try:
        from . import recording_comms

        recording_comms.notify_started(db, recording_comms.media_comms._Bg(), recording)
    except Exception:  # noqa: BLE001 - a notice must never break starting a recording
        log.exception("MED-007 start notice failed")


def recording_comms_notify_stopped(db, recording) -> None:
    try:
        from . import recording_comms

        recording_comms.notify_stopped(db, recording_comms.media_comms._Bg(), recording)
    except Exception:  # noqa: BLE001
        log.exception("MED-007 stop notice failed")


def recording_comms_record_health(db, recording) -> None:
    """Re-evaluate capture health after a path's outcome changed."""
    try:
        from . import recording_comms

        recording_comms.record_health(db, recording_comms.media_comms._Bg(), recording)
    except Exception:  # noqa: BLE001
        log.exception("MED-007 health evaluation failed")


def recording_out(r: LiveRecording) -> dict:
    return {
        "id": str(r.id), "status": r.status, "quality": r.quality,
        "started_at": _iso(r.started_at), "paused_at": _iso(r.paused_at),
        "stopped_at": _iso(r.stopped_at), "paused_ms": r.paused_ms,
        "size_bytes": r.size_bytes, "file_url": r.file_url,
        "auto_upload": r.auto_upload, "enforced": r.enforced, "error": r.error,
        # "primary" | "secondary" under dual recording (see _recording_start); unset for an
        # ordinary single-path event, same as every row before dual recording existed.
        "role": r.role,
    }


def record_egress_result(egress_info) -> dict | None:
    """Called from the egress_ended webhook — no Ctx here, this is LiveKit talking to us
    server-to-server, not a signed-in moderator. Finds the LiveRecording row by egress_id
    and writes back what actually happened: real size, final status, any error LiveKit
    reported. file_url is NOT touched — it already holds the object key we told LiveKit to
    write to (see _recording_start), which is what services.livekit.signed_url() needs;
    LiveKit's reported `location` is a gs:// URI, not something a browser can fetch."""
    db = mod.SessionLocal()
    try:
        r = db.scalar(select(LiveRecording).where(LiveRecording.egress_id == egress_info.egress_id))
        if r is None:
            return None
        info = egress_info.file_results[0] if egress_info.file_results else egress_info.file
        r.status = "failed" if egress_info.error else "stopped"
        r.stopped_at = r.stopped_at or datetime.now(timezone.utc)
        if info and info.size:
            # Delta, not absolute — this webhook can retry with the same final size, and
            # storage_used_gb is a running total across every recording the org has made.
            delta_bytes = info.size - (r.size_bytes or 0)
            r.size_bytes = info.size
            if delta_bytes:
                org = db.get(Organization, r.org_id)
                if org is not None:
                    org.storage_used_gb = round(float(org.storage_used_gb or 0) + delta_bytes / (1024 ** 3), 3)
        if egress_info.error:
            r.error = egress_info.error
        db.commit()
        db.refresh(r)
        # A single-path event's replay entitlement can advance straight to
        # READY_FOR_REVIEW here; a dual-path event's other side may still be recording,
        # so this is a no-op until services/validation.py's ticker sees both — see that
        # function's own docstring. Never publishes anything itself (BRD "never
        # auto-publish on event end").
        validation.on_recording_captured(db, r)
        # LiveKit has just told us what this path actually did. That is the only moment a
        # mid-capture path failure becomes knowable, so health is re-evaluated here and the
        # stop notice is attempted again (claim-guarded, so it sends at most once).
        recording_comms_record_health(db, r)
        recording_comms_notify_stopped(db, r)
        out = recording_out(r)
        webhooks.enqueue(db, r.org_id, "recording.failed" if egress_info.error else "recording.ready", {
            "event_id": str(r.event_id), "recording_id": out["id"], "role": out["role"],
        })
        return out
    finally:
        db.close()


def ingress_out(i: LiveIngressEndpoint) -> dict:
    return {
        "id": str(i.id), "event_id": str(i.event_id), "title": i.title,
        "input_type": i.input_type, "state": i.state, "enforced": i.enforced, "error": i.error,
    }


def record_ingress_status(ingress_info) -> tuple[str, dict] | None:
    """Called from the ingress_started/ingress_ended LiveKit webhook — no Ctx here, same
    reasoning as record_egress_result: this is LiveKit talking to us server-to-server, not
    a signed-in operator. Looked up by ingress_id, not room: IngressInfo doesn't reliably
    carry room_name before a room exists for an ingress-only lifecycle event (no publisher
    has connected yet), so resolving via our own row is the only reliable path.
    Returns (event_id, ingress_out dict) for the caller to broadcast, or None if the row is
    already gone (e.g. an admin deleted it before this webhook arrived)."""
    db = mod.SessionLocal()
    try:
        row = db.scalar(
            select(LiveIngressEndpoint).where(LiveIngressEndpoint.ingress_id == ingress_info.ingress_id)
        )
        if row is None:
            return None
        state = ingress_info.state
        row.state = livekit.ingress_state_name(state.status) if state else "inactive"
        if state and state.error:
            row.error = state.error
        db.commit()
        db.refresh(row)
        # ZST-EC-001 MED-002. Fold the raw status into the loss clock. This ANNOUNCES
        # nothing: whether a loss has persisted long enough to be an interruption
        # cannot be judged the instant it drops, so the threshold is evaluated on the
        # ticker (media_comms.sweep).
        try:
            from . import media_comms

            media_comms.observe_signal(db, row)
        except Exception:  # noqa: BLE001 - accounting must not break the webhook
            log.exception("MED-002 signal observation failed for ingress %s", row.id)
        return str(row.event_id), ingress_out(row)
    finally:
        db.close()


def _current_session(db, ctx) -> BroadcastSession | None:
    """The session this console is controlling: the newest one that hasn't ended."""
    return db.scalar(
        select(BroadcastSession)
        .where(BroadcastSession.event_id == ctx.event_id,
               BroadcastSession.org_id == ctx.org_id,
               BroadcastSession.ended_at.is_(None))
        .order_by(BroadcastSession.created_at.desc())
    )


_RECORDING_ROLE_ORDER = case(
    (LiveRecording.role == "primary", 0), (LiveRecording.role == "secondary", 1), else_=0
)


def _current_recordings(db, ctx) -> list[LiveRecording]:
    """Every row still active for this event (one, or two under dual recording — see
    _recording_start), primary first. Pause/resume/stop act on the whole set; callers that
    only want "the" recording for a summary field use actives[0]."""
    return db.scalars(
        select(LiveRecording)
        .where(LiveRecording.event_id == ctx.event_id,
               LiveRecording.org_id == ctx.org_id,
               LiveRecording.status.in_(("recording", "paused")))
        .order_by(_RECORDING_ROLE_ORDER, LiveRecording.created_at)
    ).all()


def _feature_enabled(db, key: str, default: bool) -> bool:
    """A Super Admin console-managed kill switch (FeatureFlag rows, admin-created — see
    models/feature_flag.py, whose own docstring flags that nothing reads them yet). No row
    for `key` means nobody has created it, which must mean "unchanged from default", not
    "off" — an absent flag can never itself turn a feature off."""
    flag = get_feature_flag_by_key(db, key)
    return flag.enabled if flag else default


def _storage_over_limit(db, org_id) -> bool:
    """True once the org's real usage (storage_used_gb, written from actual egress file
    sizes — see record_egress_result) has reached its plan's max_storage_gb, OR the
    platform-wide ceiling set in Settings.jsx's "Storage & Streaming Limits" panel
    (services.platform_settings.storage_ceiling_gb) — whichever is lower. A plan with no
    limit (Enterprise: max_storage_gb=None) falls through to the platform ceiling instead
    of never blocking, so an unlimited plan still respects a platform-wide cap the console
    admin set on purpose."""
    org = db.get(Organization, org_id)
    if org is None:
        return False
    sub = db.scalar(
        select(Subscription).where(
            Subscription.org_id == org_id, Subscription.status.in_(("active", "trial", "past_due")))
        .order_by(Subscription.started_at.desc())
    )
    plan = sub.plan if sub else None
    limits = [l for l in (plan.max_storage_gb if plan else None, platform_settings.storage_ceiling_gb(db)) if l is not None]
    if not limits:
        return False
    return float(org.storage_used_gb or 0) >= min(limits)


def _seed_settings(ev: Event | None) -> dict:
    """First go-live inherits the event's configured feature flags, so the console opens
    matching what the organiser set up rather than a generic default.
    A missing row (deleted mid-session) falls back to defaults rather than raising — this
    runs on every socket accept, and one stale id must not refuse every connection.

    Memorial events also force chat/qa/polls/raise_hand/reactions off here, independent of
    the stored Event columns — crud.event.create_event/update_event already clamp those
    columns, but this is the actual enforcement point the audience player reads from, so it
    stays correct even if a row somehow predates that clamp (doc Sec. 11.3/19, LE-AC-16).
    reactions_enabled has no Event-level column at all (it's session-only), so this is the
    only place it can be defaulted off for a memorial event."""
    if ev is None:
        return dict(DEFAULT_SETTINGS)
    memorial = is_memorial_category(ev.category)
    seeded = {
        **DEFAULT_SETTINGS,
        "chat_enabled": False if memorial else ev.chat_enabled,
        "qa_enabled": False if memorial else ev.qa_enabled,
        "polls_enabled": False if memorial else ev.polls_enabled,
        "waiting_room": ev.waiting_room_enabled,
        "raise_hand_enabled": False if memorial else ev.raise_hand_enabled,
        "allow_screen_share": ev.allow_screen_share,
        "auto_upload": ev.auto_start_recording or DEFAULT_SETTINGS["auto_upload"],
    }
    if memorial:
        seeded["reactions_enabled"] = False
    return seeded


async def ensure_state(ctx) -> dict:
    """Make sure the bus holds this event's live settings. Called on every socket accept:
    a worker that just booted (or a Redis that was flushed) rehydrates from the DB instead
    of silently serving defaults and, say, re-enabling a chat the host had turned off."""
    state = await bus.state_get(ctx.event_id)
    if state.get("settings"):
        return state

    def load(db):
        session = _current_session(db, ctx)
        ev = db.get(Event, ctx.event_id)
        settings = (session.settings if session and session.settings else None) or _seed_settings(ev)
        return settings, (session.status if session else "preview")

    settings, status = await mod.tx(load)
    # Flattened alongside `settings` because the chat hot path reads single keys directly.
    return await bus.state_set(ctx.event_id, {**settings, "settings": settings, "status": status})


async def _apply_settings(ctx, patch: dict) -> dict:
    """Persist a settings change to the session row and the bus in one step."""
    def work(db):
        session = _current_session(db, ctx)
        if session is None:
            return None
        session.settings = {**(session.settings or {}), **patch}
        return session.settings

    stored = await mod.tx(work)
    merged = {**DEFAULT_SETTINGS, **(stored or {}), **patch}
    await bus.state_set(ctx.event_id, {**patch, "settings": merged})
    return merged


# ── broadcast lifecycle ───────────────────────────────────────────────────────

def _golive_gate(db, ctx) -> str | None:
    """Commercial readiness check for the socket go-live path — the same authority the HTTP
    lifecycle route uses. Returns a blocking message, or None to proceed.

    An event already live/degraded is a reconnect or a duplicate click, not a new escalation,
    so it is not re-gated (that would refuse a legitimate recovery mid-broadcast).
    """
    ev = db.get(Event, ctx.event_id)
    if ev is None:
        return "Event not found"
    if ev.status in ("live", "degraded"):
        return None
    evaluation = commercial_crud.golive_readiness(db, ev)
    commercial_crud.audit_golive_decision(
        db, ev, evaluation, actor=db.get(User, ctx.user_id), target_state="live",
    )
    if evaluation["ready"]:
        return None
    return "Cannot go live — " + "; ".join(evaluation["blocking_reasons"])


async def _golive(ctx, payload):
    """Start (or restart) the broadcast. Idempotent: clicking Go Live twice does not create
    a second session or reset the elapsed clock.

    Commercial readiness is enforced BEFORE anything is created (ZST-LE-COM-001 I3, CF-3).
    This handler used to write `ev.status = "live"` straight from published/scheduled with no
    readiness call, which meant an R2 event with an unpaid required milestone and no reserved
    capacity could go live from the host console — bypassing the gate the PATCH lifecycle
    route enforced. Both paths now consult the same authority,
    crud.commercial.golive_block_reason.
    """
    now = datetime.now(timezone.utc)

    # Gate first: no LiveKit room, no BroadcastSession, no side effects at all until the
    # event is commercially allowed to deliver.
    gate = await mod.tx(lambda db: _golive_gate(db, ctx))
    if gate is not None:
        return [("host", "broadcast.error", {"error": gate, "code": "commercial_readiness_blocked"})]

    # Create the room first so a publisher has somewhere to join.
    enforced = await livekit.ensure_room(ctx.room)

    def work(db):
        ev = db.get(Event, ctx.event_id)
        session = _current_session(db, ctx)
        if session and session.status == "live":
            return session_out(session), None
        if session is None:
            session = BroadcastSession(event_id=ctx.event_id, org_id=ctx.org_id,
                                       settings=_seed_settings(ev), created_by=ctx.user_id)
            db.add(session)
        if session.status == "paused" and session.paused_at:
            session.paused_ms += int((now - session.paused_at).total_seconds() * 1000)
        session.status = "live"
        media_notify_started(db, session)
        session.started_at = session.started_at or now
        session.paused_at = None
        # The event's own lifecycle only moves forward from a publishable (or armed) state —
        # reuse the existing guard rather than writing "live" unconditionally. Readiness was
        # already cleared by _golive_gate above.
        if ev is not None and ev.status in ("published", "scheduled", "armed"):
            ev.status = "live"
            ev.start_time = ev.start_time or now
        act = mod.record(db, ctx, "system", "Host started the stream", audit="live.broadcast.golive",
                         target_type="broadcast_session", target_id=session.id,
                         meta={"enforced_in_livekit": enforced})
        return session_out(session), act

    out = await mod.tx(work)
    session, act = out
    await bus.state_set(ctx.event_id, {"status": "live", "started_at": session["started_at"]})
    if act:  # only the transition that actually happened, not a redundant "already live" call
        await mod.tx(lambda db: webhooks.enqueue(db, ctx.org_id, "session.started", {
            "event_id": str(ctx.event_id), "session_id": session["id"],
        }))
    frames = [("broadcast", "broadcast.update", {**session, "enforced": enforced})]
    if act:
        frames.append(("activity", "activity.new", act))
    return frames


async def _pause(ctx, payload):
    now = datetime.now(timezone.utc)

    def work(db):
        session = _current_session(db, ctx)
        if session is None or session.status != "live":
            return None
        session.status, session.paused_at = "paused", now
        return session_out(session), mod.record(
            db, ctx, "system", "Host paused the stream", audit="live.broadcast.pause",
            target_type="broadcast_session", target_id=session.id)

    out = await mod.tx(work)
    if not out:
        return "The broadcast isn't live"
    await bus.state_set(ctx.event_id, {"status": "paused"})
    await mod.tx(lambda db: webhooks.enqueue(db, ctx.org_id, "session.paused", {
        "event_id": str(ctx.event_id), "session_id": out[0]["id"],
    }))
    return [("broadcast", "broadcast.update", out[0]), ("activity", "activity.new", out[1])]


async def _resume(ctx, payload):
    now = datetime.now(timezone.utc)

    def work(db):
        session = _current_session(db, ctx)
        if session is None or session.status != "paused":
            return None
        if session.paused_at:
            session.paused_ms += int((now - session.paused_at).total_seconds() * 1000)
        session.status, session.paused_at = "live", None
        return session_out(session), mod.record(
            db, ctx, "system", "Host resumed the stream", audit="live.broadcast.resume",
            target_type="broadcast_session", target_id=session.id)

    out = await mod.tx(work)
    if not out:
        return "The broadcast isn't paused"
    await bus.state_set(ctx.event_id, {"status": "live"})
    return [("broadcast", "broadcast.update", out[0]), ("activity", "activity.new", out[1])]


async def _end(ctx, payload, emergency: bool = False):
    """End the broadcast. Emergency stop additionally kills the room, so every participant
    is disconnected rather than left publishing into a stream nobody is watching."""
    now = datetime.now(timezone.utc)
    reason = "emergency_stop" if emergency else "host"

    # Stop the recording first: a file that keeps rolling after the room dies is a corrupted
    # file. Returns None when nothing was rolling, so no pre-check query is needed.
    stopped = await _stop_recording_rows(ctx, now)
    closed = await livekit.close_room(ctx.room) if emergency else False

    def work(db):
        ev = db.get(Event, ctx.event_id)
        session = _current_session(db, ctx)
        if session is None:
            return None
        if session.status == "paused" and session.paused_at:
            session.paused_ms += int((now - session.paused_at).total_seconds() * 1000)
        session.status, session.ended_at, session.paused_at = "ended", now, None
        session.ended_reason = reason
        media_notify_ended(db, session)
        # Reuse the event lifecycle guard: "ended" is only legal from "live".
        if ev is not None and ev.status == "live":
            ev.status = "ended"
            ev.end_time = ev.end_time or now
        text = "Host triggered an emergency stop" if emergency else "Host ended the stream"
        return session_out(session), mod.record(
            db, ctx, "system", text, audit=f"live.broadcast.{reason}",
            target_type="broadcast_session", target_id=session.id,
            meta={"room_closed": closed})

    out = await mod.tx(work)
    if not out:
        return "There's no broadcast to end"
    await bus.state_set(ctx.event_id, {"status": "ended"})
    await mod.tx(lambda db: webhooks.enqueue(db, ctx.org_id, "session.ended", {
        "event_id": str(ctx.event_id), "session_id": out[0]["id"], "reason": reason,
    }))
    frames = [("broadcast", "broadcast.update", out[0]), ("activity", "activity.new", out[1])]
    # So the console's recording panel reflects the auto-stop, not just the broadcast end —
    # one frame per row (both paths under dual recording, see _recording_start).
    frames.extend(("recording", "recording.update", r) for r in stopped)
    return frames


async def _preview(ctx, payload):
    """Preview before going live: reserve the room so the host can check camera/mic against
    real infrastructure. No session row — nothing has been broadcast yet."""
    enforced = await livekit.ensure_room(ctx.room)
    state = await bus.state_set(ctx.event_id, {"status": "preview"})
    return [("broadcast", "broadcast.preview", {
        "status": "preview", "enforced": enforced,
        "settings": state.get("settings", DEFAULT_SETTINGS),
        # Consumed by hooks/useLiveKitPublish.js: the host studio connects and publishes the
        # already-acquired camera/mic tracks with this token once the broadcast goes live.
        "publish_token": livekit.create_stream_token(ctx.identity, ctx.room, True)
        if livekit.configured() else None,
        "livekit_url": livekit.settings.LIVEKIT_URL or None,
    })]


async def _countdown(ctx, payload):
    """Shared go-live countdown. Ephemeral: every console counts down off one server
    deadline rather than each running its own timer and drifting apart."""
    seconds = max(0, min(int(payload.get("seconds") or 10), 600))
    deadline = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    await bus.state_set(ctx.event_id, {"countdown_until": deadline.isoformat()})
    return [("broadcast", "broadcast.countdown", {"until": deadline.isoformat(), "seconds": seconds})]


_MEMORIAL_LOCKED_SETTINGS = ("chat_enabled", "qa_enabled", "polls_enabled", "raise_hand_enabled", "reactions_enabled")


async def _settings(ctx, payload):
    ceiling = await mod.tx(lambda db: platform_settings.max_bitrate_kbps(db))
    patch = clean_settings(payload.get("settings") or payload, max_bitrate_kbps=ceiling)
    if not patch:
        return "No recognised settings in that request"
    # A host cannot toggle chat/Q&A/polls/raise-hand/reactions on mid-broadcast for a
    # memorial event either — _seed_settings closes the initial state, this closes the
    # runtime one (doc Sec. 11.3/19, LE-AC-16, non-waivable).
    def _category(db):
        ev = db.get(Event, ctx.event_id)
        return ev.category if ev else None
    memorial = is_memorial_category(await mod.tx(_category))
    if memorial:
        patch = {k: v for k, v in patch.items() if k not in _MEMORIAL_LOCKED_SETTINGS}
        if not patch:
            return "Chat, Q&A, polls, raise hand, and reactions cannot be enabled for a memorial event"
    merged = await _apply_settings(ctx, patch)
    changed = ", ".join(f"{k}={patch[k]}" for k in sorted(patch))
    act = await mod.tx(lambda db: mod.record(
        db, ctx, "system", f"Host changed live settings ({changed})",
        audit="live.broadcast.settings", target_type="broadcast_session", meta=patch))
    return [("broadcast", "settings.update", {"settings": merged, "changed": patch}),
            ("activity", "activity.new", act)]


# ── recording ─────────────────────────────────────────────────────────────────

async def _stop_recording_rows(ctx, now) -> list[dict]:
    """Close out every active recording row (one, or two under dual recording — see
    _recording_start) and stop each egress independently. Shared by the explicit stop
    action and by ending the broadcast. A dual-recording pair is never assumed to succeed
    or fail together: each row's own stop_recording() result and error are its own."""
    active = await mod.tx(lambda db: [(str(r.id), r.egress_id) for r in _current_recordings(db, ctx)])
    if not active:
        return []
    errors = {rid: (await livekit.stop_recording(egress_id) if egress_id else None) for rid, egress_id in active}

    def work(db):
        rows, last = [], None
        for rid, _ in active:
            r = db.get(LiveRecording, uuid.UUID(rid))
            if r is None:
                continue
            if r.status == "paused" and r.paused_at:
                r.paused_ms += int((now - r.paused_at).total_seconds() * 1000)
            r.status, r.stopped_at, r.paused_at = "stopped", now, None
            if errors.get(rid):
                r.error = errors[rid]
            rows.append(recording_out(r))
            last = r
        if last is not None:
            # One notice per capture, not per path: notify_stopped resolves the capture
            # group and returns early while any sibling path is still running.
            recording_comms_notify_stopped(db, last)
        return rows

    return await mod.tx(work)


async def _recording_start(ctx, payload):
    now = datetime.now(timezone.utc)
    state = await bus.state_get(ctx.event_id)
    settings = state.get("settings") or DEFAULT_SETTINGS
    quality = payload.get("quality") if payload.get("quality") in RESOLUTIONS else settings.get("recording_quality", "1080p")

    existing = await mod.tx(lambda db: bool(_current_recordings(db, ctx)))
    if existing:
        return "A recording is already running"

    if not await mod.tx(lambda db: _feature_enabled(db, "recordings_enabled", default=True)):
        return "Recording is temporarily disabled platform-wide — contact support"

    if await mod.tx(lambda db: _storage_over_limit(db, ctx.org_id)):
        return "Storage limit reached for your plan — free up space or upgrade to keep recording"

    dual = await mod.tx(lambda db: commercial_crud.dual_recording_required(db, db.get(Event, ctx.event_id)))
    # Doc Section 14.1/17: R2/R3 events require two INDEPENDENT recording paths — same room,
    # two separate egress jobs, two separate rows/files — so a recorder failure on one never
    # means total loss. An ordinary event (dual False) keeps the exact single-row path this
    # always had.
    roles = ("primary", "secondary") if dual else (None,)
    started: list[tuple[str | None, str, str | None, str | None]] = []   # (role, filepath, egress_id, error)
    for role in roles:
        suffix = f"-{role}" if role else ""
        filepath = f"zoikostream/{ctx.org_id}/{ctx.event_id}/{int(now.timestamp())}{suffix}.mp4"
        egress_id, error = await livekit.start_recording(ctx.room, quality, filepath)
        started.append((role, filepath, egress_id, error))

    def work(db):
        rows = []
        session = _current_session(db, ctx)
        for role, filepath, egress_id, error in started:
            r = LiveRecording(
                event_id=ctx.event_id, org_id=ctx.org_id, status="recording", quality=quality,
                role=role, egress_id=egress_id, started_at=now, enforced=bool(egress_id), error=error,
                auto_upload=bool(settings.get("auto_upload", True)),
                file_url=filepath if egress_id else None, created_by=ctx.user_id,
            )
            if session:
                r.session_id = session.id
            db.add(r)
            db.flush()
            rows.append(r)
        captured = sum(1 for r in rows if r.enforced)
        note = (
            "Recording started" if captured == len(rows) and len(rows) == 1 else
            "Recording started (both paths captured)" if captured == len(rows) else
            f"Recording started (only {captured} of {len(rows)} path(s) captured — check LiveKit egress)" if captured else
            "Recording started (not captured — LiveKit egress unavailable)"
        )
        act = mod.record(db, ctx, "recording", note, audit="live.recording.start",
                         target_type="live_recording", target_id=rows[0].id,
                         meta={"quality": quality, "enforced": captured == len(rows), "dual": dual})
        # Committed first, announced second. The notice also establishes the capture's
        # health baseline, so a later path loss is a detectable transition rather than a
        # comparison against None.
        out = [recording_out(r) for r in rows]
        recording_comms_notify_started(db, rows[0])
        return out, act

    recs, act = await mod.tx(work)
    return [("recording", "recording.update", r) for r in recs] + [("activity", "activity.new", act)]


async def _recording_pause(ctx, payload, resume: bool = False):
    now = datetime.now(timezone.utc)

    def work(db):
        recs = _current_recordings(db, ctx)
        updated = []
        for r in recs:
            if resume:
                if r.status != "paused":
                    continue
                if r.paused_at:
                    r.paused_ms += int((now - r.paused_at).total_seconds() * 1000)
                r.status, r.paused_at = "recording", None
            else:
                if r.status != "recording":
                    continue
                r.status, r.paused_at = "paused", now
            updated.append(r)
        if not updated:
            return None
        verb = "resumed" if resume else "paused"
        text = f"Recording {verb}" if len(updated) == 1 else f"Recording {verb} (both paths)"
        act = mod.record(db, ctx, "recording", text, audit=f"live.recording.{verb}",
                         target_type="live_recording", target_id=updated[0].id)
        return [recording_out(r) for r in updated], act

    out = await mod.tx(work)
    if not out:
        return "No recording in that state"
    # ponytail: pause/resume is bookkeeping on OUR row(s) — LiveKit egress has no pause API,
    # so the captured file keeps rolling. The timer and logs reflect the host's intent;
    # trimming happens in post. Split into two egresses if a real gap is ever required.
    rows, act = out
    return [("recording", "recording.update", r) for r in rows] + [("activity", "activity.new", act)]


async def _recording_stop(ctx, payload):
    now = datetime.now(timezone.utc)
    stopped = await _stop_recording_rows(ctx, now)
    if not stopped:
        return "No recording is running"
    text = "Recording stopped" if len(stopped) == 1 else "Recording stopped (both paths)"
    act = await mod.tx(lambda db: mod.record(db, ctx, "recording", text,
                                             audit="live.recording.stop",
                                             target_type="live_recording", target_id=uuid.UUID(stopped[0]["id"])))
    return [("recording", "recording.update", r) for r in stopped] + [("activity", "activity.new", act)]


# ── stage management ──────────────────────────────────────────────────────────
# Roles, mute, stage in/out, remove and ban already exist as participant.* actions in the
# moderator console and are reused as-is. Only what the host console adds lives here.

async def _stage_media(ctx, payload, kind: str):
    """Force a participant's camera or screen share off (or allow it back on). LiveKit's
    publish permission is the real lever, so revoking it is what actually stops a track."""
    identity = str(payload.get("identity") or "")
    if not identity:
        return []
    allowed = bool(payload.get("allowed", True))
    enforced = await livekit.set_stage(ctx.room, identity, allowed)
    field = "camera_allowed" if kind == "camera" else "share_allowed"
    rec = await bus.presence_upsert(ctx.event_id, identity, {field: allowed})
    name = rec.get("name") or identity
    verb = "enabled" if allowed else "disabled"
    act = await mod.tx(lambda db: mod.record(
        db, ctx, "mod", f"{name}'s {kind} was {verb} by the host",
        audit=f"live.stage.{kind}", target_type="participant", target_id=identity,
        meta={"allowed": allowed, "enforced_in_livekit": enforced}))
    return [("participants", "participant.update", rec), ("activity", "activity.new", act),
            ("moderator", "action.result", {"op": f"stage.{kind}", "identity": identity,
                                            "enforced": enforced})]


async def _stage_admit(ctx, payload):
    """Waiting-room decision. Admit clears the flag; deny removes the person."""
    identity = str(payload.get("identity") or "")
    if not identity:
        return []
    admit = bool(payload.get("admit", True))
    if admit:
        rec = await bus.presence_upsert(ctx.event_id, identity, {"waiting": False})
        frames = [("participants", "participant.update", rec),
                  ("stage", "waiting.admitted", {"identity": identity})]
    else:
        await livekit.remove_participant(ctx.room, identity)
        rec = await bus.presence_remove(ctx.event_id, identity) or {"identity": identity}
        frames = [("participants", "participant.leave", rec),
                  ("stage", "waiting.denied", {"identity": identity})]
    name = rec.get("name") or identity
    act = await mod.tx(lambda db: mod.record(
        db, ctx, "mod", f"{name} was {'admitted from' if admit else 'denied at'} the waiting room",
        audit="live.stage.admit", target_type="participant", target_id=identity,
        meta={"admit": admit}))
    return [*frames, ("activity", "activity.new", act)]


async def _stage_admit_all(ctx, payload):
    """One click for the common case: a queue built up while the host was talking."""
    waiting = [p for p in await bus.presence_all(ctx.event_id) if p.get("waiting")]
    frames = []
    for p in waiting:
        rec = await bus.presence_upsert(ctx.event_id, p["identity"], {"waiting": False})
        frames.append(("participants", "participant.update", rec))
    act = await mod.tx(lambda db: mod.record(
        db, ctx, "mod", f"Host admitted {len(waiting)} waiting attendee(s)",
        audit="live.stage.admit_all", target_type="participant", meta={"count": len(waiting)}))
    return [*frames, ("stage", "waiting.cleared", {"count": len(waiting)}),
            ("activity", "activity.new", act)]


async def _stage_mute_all(ctx, payload):
    """Mute everyone who isn't running the show — the single most-used host control."""
    people = await bus.presence_all(ctx.event_id)
    targets = [p for p in people
               if p.get("role") not in ("host", "moderator") and not p.get("muted")]
    frames = []
    for p in targets:
        await livekit.mute_participant(ctx.room, p["identity"], True)
        rec = await bus.presence_upsert(ctx.event_id, p["identity"], {"muted": True})
        frames.append(("participants", "participant.update", rec))
    act = await mod.tx(lambda db: mod.record(
        db, ctx, "mod", f"Host muted {len(targets)} participant(s)",
        audit="live.stage.mute_all", target_type="participant", meta={"count": len(targets)}))
    return [*frames, ("activity", "activity.new", act)]


# ── analytics ─────────────────────────────────────────────────────────────────

# Minimal UA classification — no dependency, and only the three facts the console shows.
# Anything unrecognised is "Unknown", never guessed into a bucket.
_MOBILE = re.compile(r"iphone|ipod|android.*mobile|windows phone", re.I)
_TABLET = re.compile(r"ipad|android(?!.*mobile)|tablet", re.I)
_PLATFORMS = ((r"windows nt", "Windows"), (r"iphone|ipad|ipod", "iOS"), (r"mac os x", "macOS"),
              (r"android", "Android"), (r"cros", "ChromeOS"), (r"linux", "Linux"))
_BROWSERS = ((r"edg/", "Edge"), (r"opr/|opera", "Opera"), (r"chrome/|crios", "Chrome"),
             (r"firefox/|fxios", "Firefox"), (r"safari/", "Safari"))


def classify_ua(ua: str | None) -> dict:
    ua = ua or ""
    device = "Mobile" if _MOBILE.search(ua) else "Tablet" if _TABLET.search(ua) else "Desktop" if ua else "Unknown"
    def first(pairs):
        for pattern, label in pairs:
            if re.search(pattern, ua, re.I):
                return label
        return "Unknown"
    return {"device": device, "platform": first(_PLATFORMS), "browser": first(_BROWSERS)}


def _distribution(people: list[dict], key: str) -> list[dict]:
    counts: dict[str, int] = {}
    for p in people:
        counts[p.get(key) or "Unknown"] = counts.get(p.get(key) or "Unknown", 0) + 1
    return [{"label": k, "value": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]


def _split(people: list[dict]) -> dict:
    """Presence -> the counters the header and KPI row show. One pass, real records."""
    active = [p for p in people if not p.get("waiting")]
    return {
        "participants": len(active),
        "waiting": len(people) - len(active),
        "viewers": sum(1 for p in active if (p.get("role") or "viewer") == "viewer"),
        "speakers": sum(1 for p in active if p.get("role") == "speaker" or p.get("on_stage")),
        "moderators": sum(1 for p in active if p.get("role") == "moderator"),
        "hosts": sum(1 for p in active if p.get("role") == "host"),
        "hands": sum(1 for p in active if p.get("hand")),
        "poor_connections": sum(1 for p in active if p.get("quality") in ("poor", "lost")),
        "publishing": sum(1 for p in active if p.get("publishing")),
    }


def _watch_seconds(people: list[dict], now_ts: float) -> int | None:
    """Mean time-in-room of everyone currently connected, from real join timestamps."""
    stamps = [p["joined_at"] for p in people if p.get("joined_at") and not p.get("waiting")]
    if not stamps:
        return None
    return int(sum(now_ts - t for t in stamps) / len(stamps))


def engagement_score(counts: dict, peak: int) -> int:
    """Weighted interactions per viewer, capped at 100.

    score = 100 * (messages + 2*questions + 3*poll_votes + reactions) / (5 * peak_viewers)

    Questions and votes weigh more than chat because they cost the viewer more effort. The
    divisor treats "5 weighted interactions per viewer" as a fully-engaged room. It's a
    heuristic and it is written down here so nobody mistakes it for a measurement.
    """
    if peak <= 0:
        return 0
    weighted = counts.get("messages", 0) + 2 * counts.get("questions", 0) \
        + 3 * counts.get("poll_votes", 0) + counts.get("reactions", 0)
    return max(0, min(100, round(100 * weighted / (5 * peak))))


def _counts(db, event_id, org_id, since=None) -> dict:
    """Interaction totals from real rows. `since` narrows to a window (for chat rate)."""
    def count(model, *extra):
        stmt = select(func.count()).select_from(model).where(
            model.event_id == event_id, model.org_id == org_id, *extra)
        if since is not None:
            stmt = stmt.where(model.created_at >= since)
        return db.scalar(stmt) or 0

    messages = count(LiveMessage, LiveMessage.status != "deleted")
    questions = count(LiveQuestion)
    polls = db.scalars(select(LivePoll).where(LivePoll.event_id == event_id,
                                             LivePoll.org_id == org_id)).all()
    poll_votes = sum(o.get("votes", 0) for p in polls for o in (p.options or []))
    # Reactions are a JSON map per message; summing needs the rows. Bounded by HISTORY.
    reaction_rows = db.scalars(
        select(LiveMessage.reactions).where(LiveMessage.event_id == event_id,
                                           LiveMessage.org_id == org_id,
                                           LiveMessage.reactions.isnot(None))
    ).all()
    reactions = sum(sum(r.values()) for r in reaction_rows if isinstance(r, dict))
    return {"messages": messages, "questions": questions, "poll_votes": poll_votes,
            "reactions": reactions, "polls": len(polls)}


async def analytics_now(ctx) -> dict:
    """The live analytics block: real counters, real presence, honest nulls."""
    people = await bus.presence_all(ctx.event_id)
    state = await bus.state_get(ctx.event_id)
    split = _split(people)
    now = datetime.now(timezone.utc)

    def work(db):
        counts = _counts(db, ctx.event_id, ctx.org_id)
        recent = _counts(db, ctx.event_id, ctx.org_id, since=now - timedelta(minutes=1))
        session = _current_session(db, ctx)
        history = db.scalars(
            select(AnalyticsSnapshot)
            .where(AnalyticsSnapshot.event_id == ctx.event_id)
            .order_by(AnalyticsSnapshot.created_at.desc()).limit(RETENTION_POINTS)
        ).all()
        return counts, recent, (session.peak_viewers if session else 0), list(reversed(history))

    counts, recent, peak, history = await mod.tx(work)
    peak = max(peak, split["viewers"], int(state.get("peak_viewers") or 0))
    return {
        "analytics": {
            **split,
            "peak_viewers": peak,
            "avg_watch_seconds": _watch_seconds(people, now.timestamp()),
            "chat_per_minute": recent["messages"],
            "questions_asked": counts["questions"],
            "reactions": counts["reactions"],
            "poll_votes": counts["poll_votes"],
            "poll_participation": round(100 * counts["poll_votes"] / peak, 1) if peak else None,
            "engagement": engagement_score(counts, peak),
            "devices": _distribution(people, "device"),
            "platforms": _distribution(people, "platform"),
            "browsers": _distribution(people, "browser"),
            # No GeoIP in this stack; a fabricated map is worse than an absent one.
            "countries": None,
            "countries_note": "Country breakdown needs a GeoIP lookup (not integrated).",
            "retention": [
                {"t": mod._iso(h.created_at), "viewers": h.viewers,
                 "participants": h.participants, "on_stage": h.on_stage}
                for h in history
            ],
        }
    }


def media_notify_started(db, session) -> None:
    """ZST-EC-001 MED-004. INTERNAL operator notice only.

    Imports media_comms lazily and swallows every failure: a notification must never
    be able to stop a broadcast going live. It reaches no audience-facing sender —
    internal media state is not audience communication consent.
    """
    try:
        from . import media_comms

        media_comms.notify_session_started(db, media_comms._Bg(), session)
    except Exception:  # noqa: BLE001
        log.exception('MED-004 start notice failed')


def media_notify_ended(db, session) -> None:
    """ZST-EC-001 MED-004 end. Same posture as media_notify_started."""
    try:
        from . import media_comms

        media_comms.notify_session_ended(db, media_comms._Bg(), session)
    except Exception:  # noqa: BLE001
        log.exception('MED-004 end notice failed')


def _record_media_health(ctx, health: dict) -> dict:
    """Persist the governed health level for MED-005 and return it unchanged.

    Returns its input so the console payload is untouched — this is observation, not a
    second opinion. Repeated evaluations at the same level record and send nothing; only a
    transition is a communication event, which is what stops a polling loop from mailing an
    operator every tick.
    """
    try:
        from . import media_comms

        db = mod.SessionLocal()
        try:
            session = _current_session(db, ctx)
            if session is not None:
                media_comms.record_health(db, media_comms._Bg(), session, health)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - health reporting must not break the console payload
        log.exception("MED-005 health transition recording failed")
    return health


def health_of(split: dict, status: str, recording_enforced: bool | None) -> dict:
    """Live health from signals we actually have: is it live, is anybody publishing, how
    many connections report poor quality, did the recording attach."""
    issues = []
    if status == "live" and not split["publishing"]:
        issues.append("No media is being published")
    if status == "paused":
        issues.append("Broadcast is paused")
    if split["participants"] and split["poor_connections"] / max(split["participants"], 1) > 0.25:
        issues.append(f"{split['poor_connections']} participants on a poor connection")
    if recording_enforced is False:
        issues.append("Recording is not being captured")
    level = "down" if status == "live" and not split["publishing"] else "warn" if issues else "ok"
    return {"level": level, "issues": issues}


# ── snapshot contribution ─────────────────────────────────────────────────────

async def snapshot_extra(ctx) -> dict:
    """Merged into the socket's opening snapshot (registered below), so the host console
    paints a complete control room from the first frame."""
    state = await ensure_state(ctx)

    def work(db):
        session = _current_session(db, ctx)
        # Singular summary field stays pointed at the primary (or the only row) — the full
        # pair, when dual recording is running, is in `recordings` below.
        actives = _current_recordings(db, ctx)
        active = actives[0] if actives else None
        recent = db.scalars(
            select(LiveRecording)
            .where(LiveRecording.event_id == ctx.event_id, LiveRecording.org_id == ctx.org_id)
            .order_by(LiveRecording.created_at.desc()).limit(20)
        ).all()
        return (session_out(session, state.get("settings")),
                recording_out(active) if active else None,
                [recording_out(r) for r in recent])

    session, recording, recordings = await mod.tx(work)
    extra = await analytics_now(ctx)
    # The analytics block already carries the presence split, so health is derived from it
    # rather than fetching every participant a second time on the connect path.
    split = extra["analytics"]
    return {
        **extra,
        "broadcast": session,
        "recording": recording,
        "recordings": recordings,
        "can_host": ctx.can_host,
        "countdown_until": state.get("countdown_until"),
        # ZST-EC-001 MED-005. health_of() stays the ONE authoritative calculation; the
        # transition is recorded from its output so a level CHANGE can be announced without
        # this module or media_comms re-deriving health.
        "health": _record_media_health(
            ctx, health_of(split, session["status"],
                           recording["enforced"] if recording else None)),
        "livekit_url": livekit.settings.LIVEKIT_URL or None,
        # Present only for hosts, and only when LiveKit is configured. This is the token
        # hooks/useLiveKitPublish.js connects and publishes with once `live` is true — see
        # that hook and _preview's own publish_token above for the same token on the
        # pre-go-live path.
        "publish_token": livekit.create_stream_token(ctx.identity, ctx.room, True)
        if (ctx.can_host and livekit.configured()) else None,
    }


# ── analytics sampler ─────────────────────────────────────────────────────────

async def _sample_once() -> list[tuple[str, dict]]:
    """One pass over every non-ended session. Runs on its own ticker (see main.lifespan) —
    separate from the moderation scheduler so neither has to import the other."""
    sessions = await mod.tx(lambda db: [
        {"event_id": str(s.event_id), "org_id": str(s.org_id), "id": str(s.id),
         "status": s.status, "peak": s.peak_viewers}
        for s in db.scalars(
            select(BroadcastSession).where(BroadcastSession.ended_at.is_(None))).all()
    ])

    out = []
    for s in sessions:
        event_id = s["event_id"]
        people = await bus.presence_all(event_id)
        # Nobody connected and never started: nothing worth a row.
        if not people and s["status"] == "preview":
            continue
        split = _split(people)
        peak = max(s["peak"], split["viewers"])

        def write(db, s=s, split=split, peak=peak):
            counts = _counts(db, uuid.UUID(s["event_id"]), uuid.UUID(s["org_id"]))
            db.add(AnalyticsSnapshot(
                event_id=uuid.UUID(s["event_id"]), org_id=uuid.UUID(s["org_id"]),
                viewers=split["viewers"], participants=split["participants"],
                on_stage=split["speakers"] + split["hosts"], messages=counts["messages"],
                questions=counts["questions"], reactions=counts["reactions"], hands=split["hands"],
            ))
            if peak > s["peak"]:
                session = db.get(BroadcastSession, uuid.UUID(s["id"]))
                if session:
                    session.peak_viewers = peak
            return counts

        counts = await mod.tx(write)
        await bus.state_set(event_id, {"peak_viewers": peak})
        out.append((event_id, {
            **split, "peak_viewers": peak,
            "engagement": engagement_score(counts, peak),
            "avg_watch_seconds": _watch_seconds(people, datetime.now(timezone.utc).timestamp()),
            "health": health_of(split, s["status"], None),
            "t": datetime.now(timezone.utc).isoformat(),
        }))
    return out


async def run_sampler(interval: float = SAMPLE_SECONDS) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            for event_id, tick in await _sample_once():
                await bus.publish(event_id, "analytics", "analytics.tick", tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad sample must not kill the sampler
            log.exception("analytics sampler tick failed")


# ── registration into the shared dispatcher ───────────────────────────────────

ACTIONS = {
    "broadcast.golive": _golive,
    "broadcast.pause": _pause,
    "broadcast.resume": _resume,
    "broadcast.end": _end,
    "broadcast.emergency_stop": lambda c, p: _end(c, p, emergency=True),
    "broadcast.preview": _preview,
    "broadcast.countdown": _countdown,
    "broadcast.settings": _settings,
    "recording.start": _recording_start,
    "recording.pause": _recording_pause,
    "recording.resume": lambda c, p: _recording_pause(c, p, resume=True),
    "recording.stop": _recording_stop,
    "stage.camera": lambda c, p: _stage_media(c, p, "camera"),
    "stage.share": lambda c, p: _stage_media(c, p, "share"),
    "stage.admit": _stage_admit,
    "stage.admit_all": _stage_admit_all,
    "stage.mute_all": _stage_mute_all,
}

# Broadcast + recording control is host-only. Stage controls stay available to moderators
# (they already have participant.* powers, and muting the room is audience management).
HOST_ONLY = {a for a in ACTIONS if a.startswith(("broadcast.", "recording."))}

mod.ACTIONS.update(ACTIONS)
mod.HOST_ONLY.update(HOST_ONLY)
mod.SNAPSHOT_EXTRAS.append(snapshot_extra)
