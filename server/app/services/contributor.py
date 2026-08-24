"""Contributor backstage domain: the invited-speaker session lifecycle (BRD Section 10 —
"Contributor and Backstage Experience"; what the codebase's data model calls the "speaker"
role, see models/event.py EventAssignment).

EXTENDS services/moderation.py the same way services/broadcast.py does — its actions
register into the shared dispatcher and its snapshot contribution registers as a snapshot
extra. Import direction is one-way (contributor -> moderation); routers/live.py imports
this module, which performs the registration.

State lives in two places, same split as the rest of the live domain:
  * models/live.py's ContributorSession (Postgres) — anything that must survive a dropped
    room: consent, preflight result, rehearsal, the invitation window itself.
  * services/bus.py presence (Redis/in-memory) — the moment-to-moment view every console
    renders live (`contributor_state`, self-reported mic/camera, return feed). Presence is
    wiped on room end (see bus.presence_clear); the ContributorSession row is not.

`admit`/`mute`/`remove`/`bring_live` deliberately delegate their LiveKit enforcement to
moderation._participant_action rather than re-implementing it — same room, same
"invite to stage" = publish-permission mechanism the audience-management console already
uses (moderation._participant_action's "stage"/"mute"/"remove" ops).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from ..crud import event as event_crud
from ..models import ContributorSession, EventAssignment, User
from . import bus, livekit
from . import moderation as mod


def session_out(s: ContributorSession) -> dict:
    return {
        "id": str(s.id), "user_id": str(s.user_id), "identity": s.identity, "state": s.state,
        "invited_at": mod._iso(s.invited_at),
        "join_window_start": mod._iso(s.join_window_start), "join_window_end": mod._iso(s.join_window_end),
        "expires_at": mod._iso(s.expires_at), "contribution_method": s.contribution_method,
        "consent_notice": s.consent_notice, "support_contact": s.support_contact,
        "consent_given": s.consent_given, "consent_at": mod._iso(s.consent_at),
        "preflight_result": s.preflight_result, "rehearsal_complete": s.rehearsal_complete,
        "rehearsal_at": mod._iso(s.rehearsal_at),
        "admitted_at": mod._iso(s.admitted_at), "brought_live_at": mod._iso(s.brought_live_at),
        "removed_at": mod._iso(s.removed_at), "removed_reason": s.removed_reason,
    }


def join_window_error(session: ContributorSession | None, now: datetime) -> str | None:
    """Pure gate check for routers/live.py's socket-connect rejection — unit-testable
    without a DB, same shape as crud.event.status_transition_error."""
    if session is None:
        return "You have not been invited to this event"
    if session.state == "removed":
        return "Your access to this event has been removed"
    if session.expires_at and now > session.expires_at:
        return "Your invitation has expired"
    if session.join_window_start and now < session.join_window_start:
        return "The backstage isn't open yet — check your invitation for the join window"
    if session.join_window_end and now > session.join_window_end:
        return "The backstage join window has closed"
    return None


def load_session_sync(event_id, user_id) -> ContributorSession | None:
    """Self-contained session lookup for routers/live.py's socket-connect gate — same
    shape as moderation.resolve_ctx (opens its own short-lived session; there is no
    request-scoped `db` available this early in the connection lifecycle)."""
    db = mod.SessionLocal()
    try:
        return event_crud.get_contributor_session(db, event_id, user_id)
    finally:
        db.close()


def mark_connected(event_id, user_id) -> None:
    """Stamp last_connected_at on a successful socket accept. Self-contained session,
    same reasoning as load_session_sync."""
    db = mod.SessionLocal()
    try:
        s = event_crud.get_contributor_session(db, event_id, user_id)
        if s:
            s.last_connected_at = datetime.now(timezone.utc)
            if s.state == "reconnecting":
                s.state = "connected"
            db.commit()
    finally:
        db.close()


def mark_disconnected(event_id, user_id) -> None:
    """Best-effort: a dropped socket doesn't always mean gone for good (network blip,
    backgrounded tab), so an active session flips to "reconnecting" rather than "removed"
    — only an explicit operator remove, or the join window elapsing, ends it for real.
    Self-contained session, same reasoning as load_session_sync."""
    db = mod.SessionLocal()
    try:
        s = event_crud.get_contributor_session(db, event_id, user_id)
        if s and s.state in ("connected", "ready", "on_standby", "live", "muted"):
            s.state = "reconnecting"
            s.last_disconnected_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def _session_by_identity(db, event_id, identity: str) -> ContributorSession | None:
    try:
        user_id = uuid.UUID(identity)
    except (ValueError, TypeError):
        return None
    return event_crud.get_contributor_session(db, event_id, user_id)


# ── self-service (VIEWER_ACTIONS-gated by can_contribute inside each handler, same style
# as moderation._feedback_submit's internal ctx.can_moderate guard) ──────────────────────

async def _consent(ctx, payload):
    if not ctx.can_contribute:
        return []
    now = datetime.now(timezone.utc)

    def work(db):
        s = event_crud.get_contributor_session(db, ctx.event_id, ctx.user_id)
        if s is None:
            return None
        s.consent_given, s.consent_at = True, now
        return session_out(s)

    s = await mod.tx(work)
    if not s:
        return []
    await bus.presence_upsert(ctx.event_id, ctx.identity, {"consent_given": True})
    return [("contributor", "session.update", s)]


_PREFLIGHT_BOOL_FIELDS = ("camera_ok", "mic_ok", "speaker_ok", "browser_supported", "framing_ok")


async def _preflight_result(ctx, payload):
    if not ctx.can_contribute:
        return []
    now = datetime.now(timezone.utc)
    result = {k: bool(payload.get(k)) for k in _PREFLIGHT_BOOL_FIELDS}
    quality = payload.get("network_quality")
    result["network_quality"] = str(quality)[:20] if quality else None
    # Speaker output and framing are best-effort signals — camera/mic actually working and
    # a supported browser are the non-negotiable minimum admit gates (see _admit below).
    result["passed"] = result["camera_ok"] and result["mic_ok"] and result["browser_supported"]
    result["tested_at"] = now.isoformat()

    def work(db):
        s = event_crud.get_contributor_session(db, ctx.event_id, ctx.user_id)
        if s is None:
            return None
        s.preflight_result = result
        if s.state == "waiting":
            s.state = "connected" if result["passed"] else "failed"
        return session_out(s)

    s = await mod.tx(work)
    if not s:
        return []
    await bus.presence_upsert(ctx.event_id, ctx.identity, {
        "contributor_state": s["state"], "preflight_ok": result["passed"],
    })
    return [("contributor", "session.update", s)]


async def _self_media(ctx, payload, field: str):
    if not ctx.can_contribute:
        return []
    on = bool(payload.get("on", True))
    rec = await bus.presence_upsert(ctx.event_id, ctx.identity, {field: on})
    return [("participants", "participant.update", rec)]


async def _select_return_feed(ctx, payload):
    if not ctx.can_contribute:
        return []
    feed = payload.get("feed") if payload.get("feed") in ("program", "none") else "program"
    rec = await bus.presence_upsert(ctx.event_id, ctx.identity, {"return_feed": feed})
    return [("participants", "participant.update", rec)]


async def _request_help(ctx, payload):
    if not ctx.can_contribute:
        return []
    act = await mod.tx(lambda db: mod.record(
        db, ctx, "mod", f"{ctx.name} requested help from backstage",
        audit="live.contributor.help", target_type="participant", target_id=ctx.identity,
        meta={"help_request": True}))
    return [("activity", "activity.new", act),
            ("contributor", "help.requested", {"identity": ctx.identity, "name": ctx.name})]


# ── operator actions ──────────────────────────────────────────────────────────────────
# Registered under contributor.ACTIONS, NOT HOST_ONLY — mirrors the existing stage.*
# precedent (broadcast.py): a moderator already has equivalent audience-management power
# via participant.stage, and the real gate on WHO can ever become a contributor is
# EventAssignment + the org-admin-only invite endpoint, not a second runtime tier.

async def _mark_rehearsed(ctx, payload):
    """Operator confirms this contributor has been through a rehearsal — the one
    readiness-gate field (crud.commercial.contributor_readiness_reasons) nothing else in
    this module ever sets, so without an explicit action for it arming would be permanently
    blocked for any event with an assigned speaker. Not tied to a state transition: an
    operator can mark this at any point the session exists, typically alongside or before
    admitting them."""
    identity = str(payload.get("identity") or "")
    if not identity:
        return []
    now = datetime.now(timezone.utc)

    def work(db):
        s = _session_by_identity(db, ctx.event_id, identity)
        if s is None or s.state == "removed":
            return None
        s.rehearsal_complete, s.rehearsal_at = True, now
        act = mod.record(db, ctx, "mod", "A contributor's rehearsal was marked complete",
                         audit="live.contributor.rehearsed", target_type="contributor_session", target_id=s.id)
        return session_out(s), act

    out = await mod.tx(work)
    if not out:
        return []
    s, act = out
    return [("contributor", "session.update", s), ("activity", "activity.new", act)]


async def _admit(ctx, payload):
    """connected -> ready. Blocked unless consent and a passing preflight are both on
    record — this gate (not a new LiveKit call) is what an admit adds over the identical
    plain stage.admit the general waiting room already offers."""
    identity = str(payload.get("identity") or "")
    if not identity:
        return []
    now = datetime.now(timezone.utc)

    def work(db):
        s = _session_by_identity(db, ctx.event_id, identity)
        if s is None:
            return None
        if s.state != "connected":
            return "Contributor has not completed preflight yet"
        if not s.consent_given or not (s.preflight_result or {}).get("passed"):
            return "Contributor has not given consent or passed preflight"
        s.state, s.admitted_by, s.admitted_at = "ready", ctx.user_id, now
        act = mod.record(db, ctx, "mod", "A contributor was admitted to the backstage",
                         audit="live.contributor.admit", target_type="contributor_session", target_id=s.id)
        return session_out(s), act

    out = await mod.tx(work)
    if isinstance(out, str):
        return out
    if not out:
        return []
    s, act = out
    await bus.presence_upsert(ctx.event_id, identity, {"contributor_state": "ready"})
    return [("contributor", "session.update", s), ("activity", "activity.new", act)]


async def _standby(ctx, payload):
    """ready/on_standby/live -> on_standby. Pulling someone off the live program back to
    standby also revokes their publish permission — otherwise they'd stay on-air while the
    console shows them as staged."""
    identity = str(payload.get("identity") or "")
    if not identity:
        return []

    def work(db):
        s = _session_by_identity(db, ctx.event_id, identity)
        if s is None:
            return None
        if s.state not in ("ready", "on_standby", "live"):
            return "Contributor is not in a state that can go on standby"
        was_live = s.state == "live"
        s.state = "on_standby"
        act = mod.record(db, ctx, "mod", "A contributor was placed on standby",
                         audit="live.contributor.standby", target_type="contributor_session", target_id=s.id)
        return session_out(s), act, was_live

    out = await mod.tx(work)
    if isinstance(out, str):
        return out
    if not out:
        return []
    s, act, was_live = out
    if was_live:
        await livekit.set_stage(ctx.room, identity, False)
    await bus.presence_upsert(ctx.event_id, identity, {"contributor_state": "on_standby", "on_stage": False})
    return [("contributor", "session.update", s), ("activity", "activity.new", act)]


async def _operator_action(ctx, payload, op: str):
    """bring_live | mute | remove — each delegates its LiveKit enforcement to the existing
    moderation._participant_action (stage/mute/remove ops), then layers the
    ContributorSession state transition on top."""
    identity = str(payload.get("identity") or "")
    if not identity:
        return []

    if op == "bring_live":
        def check(db):
            s = _session_by_identity(db, ctx.event_id, identity)
            return s is not None and s.state in ("ready", "on_standby")

        if not await mod.tx(check):
            return "Contributor must be admitted before going live"
        frames = await mod._participant_action(ctx, {**payload, "on_stage": True}, "stage")

        def work(db):
            s = _session_by_identity(db, ctx.event_id, identity)
            if s is None:
                return None
            s.state, s.brought_live_at = "live", datetime.now(timezone.utc)
            return session_out(s)

        s = await mod.tx(work)
        return [*frames, ("contributor", "session.update", s)] if s else frames

    if op == "mute":
        muted = bool(payload.get("muted", True))
        frames = await mod._participant_action(ctx, payload, "mute")

        def work(db):
            s = _session_by_identity(db, ctx.event_id, identity)
            if s is None:
                return None
            if muted and s.state == "live":
                s.state = "muted"
            elif not muted and s.state == "muted":
                s.state = "live"
            return session_out(s)

        s = await mod.tx(work)
        return [*frames, ("contributor", "session.update", s)] if s else frames

    if op == "remove":
        frames = await mod._participant_action(ctx, payload, "remove")

        def work(db):
            s = _session_by_identity(db, ctx.event_id, identity)
            if s is None:
                return None
            s.state = "removed"
            s.removed_by, s.removed_at = ctx.user_id, datetime.now(timezone.utc)
            s.removed_reason = (payload.get("reason") or "").strip()[:200] or None
            return session_out(s)

        s = await mod.tx(work)
        return [*frames, ("contributor", "session.update", s)] if s else frames

    return f"Unknown contributor op: {op}"


# ── snapshot contribution ─────────────────────────────────────────────────────

async def snapshot_extra(ctx) -> dict:
    """Merged into the socket's opening snapshot. Operators get the full backstage roster
    (every assigned speaker + their session, if invited); a connecting contributor also
    gets `my_contributor_state` so their own Backstage page renders on the first frame
    without a second round trip — same reasoning broadcast.snapshot_extra gives for
    `publish_token`.

    Deliberately NOT included for a plain viewer/registration connection: `contributors`
    carries names, consent status and preflight results — the BRD's own privacy rule
    (Section 10: "exclude contributor name/email from customer analytics/logs") means it
    has no business reaching an audience socket. Note the SAME isolation does not (yet)
    extend to the live `contributor.session.update` broadcasts the operator actions below
    publish — bus.publish has no per-recipient targeting at all today, the same
    room-wide-broadcast shape every other participant/chat feature in this codebase already
    uses (a chat author's real name reaches every viewer's socket the same way). Scoping
    that properly needs real per-recipient delivery in services/bus.py, which is a shared
    change affecting every live feature, not something to bolt on for this one."""
    if not (ctx.can_moderate or ctx.can_contribute):
        return {}

    def work(db):
        sessions = {
            s.user_id: s for s in db.scalars(
                select(ContributorSession).where(ContributorSession.event_id == ctx.event_id)
            ).all()
        }
        roster = None
        if ctx.can_moderate:
            assignments = db.scalars(
                select(EventAssignment).where(
                    EventAssignment.event_id == ctx.event_id, EventAssignment.role == "speaker")
            ).all()
            roster = []
            for a in assignments:
                u = db.get(User, a.user_id)
                s = sessions.get(a.user_id)
                roster.append({
                    "user_id": str(a.user_id),
                    "name": (u.full_name if u else None) or "Speaker",
                    "session": session_out(s) if s else None,
                })
        mine = sessions.get(ctx.user_id) if ctx.can_contribute else None
        return roster, (session_out(mine) if mine else None)

    roster, mine = await mod.tx(work)
    extra = {}
    if roster is not None:
        extra["contributors"] = roster
    if ctx.can_contribute:
        extra["my_contributor_state"] = mine
        # Same reasoning as broadcast.snapshot_extra's host publish_token: minted
        # unconditionally so the Backstage page's useLiveKitPublish wiring is a drop-in —
        # actually publishing is still gated client-side on the session having reached
        # "live" (operator's bring_live already flips the room-level publish permission
        # via livekit.set_stage; this token is what lets the browser attempt to publish
        # AT ALL, the permission grant is the real gate LiveKit itself enforces).
        extra["my_publish_token"] = (
            livekit.create_stream_token(ctx.identity, ctx.room, True) if livekit.configured() else None
        )
        extra["livekit_url"] = livekit.settings.LIVEKIT_URL or None
    return extra


# ── registration into the shared dispatcher ───────────────────────────────────

ACTIONS = {
    "contributor.consent": _consent,
    "contributor.preflight_result": _preflight_result,
    "contributor.toggle_mic": lambda c, p: _self_media(c, p, "self_mic_on"),
    "contributor.toggle_camera": lambda c, p: _self_media(c, p, "self_camera_on"),
    "contributor.select_return_feed": _select_return_feed,
    "contributor.request_help": _request_help,
    "contributor.admit": _admit,
    "contributor.standby": _standby,
    "contributor.mark_rehearsed": _mark_rehearsed,
    "contributor.bring_live": lambda c, p: _operator_action(c, p, "bring_live"),
    "contributor.mute": lambda c, p: _operator_action(c, p, "mute"),
    "contributor.remove": lambda c, p: _operator_action(c, p, "remove"),
}

# Self-service — anything a contributor does to/about themselves. Everything else in
# ACTIONS above (admit/standby/bring_live/mute/remove) is an operator action and falls
# through dispatch()'s default can_moderate gate, same as participant.* already does.
VIEWER_ACTIONS = frozenset({
    "contributor.consent", "contributor.preflight_result", "contributor.toggle_mic",
    "contributor.toggle_camera", "contributor.select_return_feed", "contributor.request_help",
})

mod.ACTIONS.update(ACTIONS)
mod.VIEWER_ACTIONS = mod.VIEWER_ACTIONS | VIEWER_ACTIONS
mod.SNAPSHOT_EXTRAS.append(snapshot_extra)
