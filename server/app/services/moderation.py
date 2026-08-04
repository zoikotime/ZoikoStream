"""Moderation domain logic: automatic content flags, the console snapshot, and the
one dispatcher every realtime action goes through.

Why one dispatcher instead of ~30 REST endpoints: the console is a socket-first surface.
Actions arriving on the same socket that carries the updates means one auth check, one
permission table, one audit path and one broadcast path — and a moderator action shows
up in every other console in a single round trip.

Threading note: the app uses SYNC SQLAlchemy. Calling it directly from an async socket
handler would block the event loop for every other connection, which is exactly what
breaks at the 100-moderator target. So all DB work goes through `tx()`, which runs a
short-lived Session in a worker thread. Short-lived also matters against the Supabase
pooler: a socket open for two hours must not pin a pooled connection for two hours.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from ..crud.admin import create_audit_log
from ..db import SessionLocal
from ..models import (
    Event,
    EventAssignment,
    LiveActivity,
    LiveAnnouncement,
    LiveMessage,
    LivePoll,
    LiveQuestion,
    User,
)
from . import bus, livekit, viewer

# How much history a reconnecting console loads. ponytail: a fixed window, not paging —
# a moderator needs the recent room, and the full chat log is an export concern.
HISTORY_LIMIT = 200
DUPLICATE_WINDOW = timedelta(minutes=2)


# ── automatic content detection ───────────────────────────────────────────────
# ponytail: heuristics, not ML. They only FLAG (never auto-delete), so a false positive
# costs a moderator one glance. Swap in a real classifier behind flag_text() if that stops
# being good enough — nothing else needs to change.

_PROFANITY = frozenset(
    "fuck fucking shit bitch bastard asshole dick cunt whore slut nigger faggot retard".split()
)
# Bare domains are matched against a TLD allowlist ON PURPOSE: a broad `\w+\.\w{2,}` also
# flags "React.js" and "Node.js", and a tech-keynote chat is full of those.
_TLD = "com|net|org|io|co|dev|ru|cn|xyz|link|info|biz|top|shop|site|online|club|example|gg|me|ly"
_LINK = re.compile(rf"https?://\S+|www\.\S+|\b[\w-]+\.(?:{_TLD})\b(?:/\S*)?", re.I)
_WORD = re.compile(r"[a-z']+")
_RUN = re.compile(r"(.)\1{5,}")  # "aaaaaaa", "!!!!!!!"


def normalize(text: str) -> str:
    """Comparison key for duplicate detection — case, spacing and punctuation insensitive."""
    return " ".join(_WORD.findall(text.lower()))


def flag_text(text: str, recent: list[str] | None = None) -> list[str]:
    """Return any of: profanity | link | spam | duplicate. `recent` is the same author's
    recent normalized messages."""
    flags = []
    words = set(_WORD.findall(text.lower()))
    if words & _PROFANITY:
        flags.append("profanity")

    links = _LINK.findall(text)
    if links:
        flags.append("link")

    # Shouting, two ways: several ALL-CAPS words (survives a lowercase url in the same
    # message, which a whole-string caps ratio does not), or a mostly-uppercase message.
    letters = [c for c in text if c.isalpha()]
    caps_words = sum(1 for w in text.split() if len(w) >= 3 and w.isupper())
    shouting = caps_words >= 3 or (len(letters) > 12 and sum(c.isupper() for c in letters) / len(letters) > 0.7)
    if len(links) >= 3 or shouting or _RUN.search(text):
        flags.append("spam")

    if recent and normalize(text) in recent:
        flags.append("duplicate")
    return flags


# ── request context ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Ctx:
    """Everything an action needs about who is asking, resolved ONCE at socket accept
    so no handler re-queries permissions per message."""

    event_id: uuid.UUID
    org_id: uuid.UUID
    room: str                 # LiveKit room name for this event
    user_id: uuid.UUID | None
    name: str
    identity: str             # LiveKit identity; matches the presence record key
    role: str                 # platform role
    can_moderate: bool
    # Broadcast control (go live, end, record, emergency stop) is HOST-only. A moderator
    # runs the audience; they must not be able to end the stream.
    can_host: bool = False
    # An assigned speaker/panellist. Sits BETWEEN an attendee and a moderator: they publish
    # media and answer the questions routed to them, but they run nothing. Staff are speakers
    # too — a host presenting their own slides needs the same tools.
    can_speak: bool = False

    @property
    def actor(self):
        """Minimal stand-in for the User row that create_audit_log needs (id + email)."""
        return SimpleNamespace(id=self.user_id, email=self.name)


def resolve_ctx(event_id: uuid.UUID, user: User) -> Ctx | None:
    """Load the event with org isolation and work out whether this user may moderate it.
    Returns None when the event isn't visible to the user's org -> socket is refused."""
    db = SessionLocal()
    try:
        ev = db.scalar(select(Event).where(Event.id == event_id, Event.deleted_at.is_(None)))
        if ev is None:
            return None
        # Same rule as the attendee landing endpoint (services.viewer.access_for), so an
        # attendee of a PUBLIC event can't be handed the page and then refused the socket
        # that carries its viewer count. Org isolation for private events is unchanged —
        # access_for still requires membership for those.
        allowed, _, _ = viewer.access_for(ev, user)
        if not allowed:
            return None

        # Org admins and above moderate any event in their org. A moderator/host/speaker
        # must be ASSIGNED to this specific event — an org's moderator is not automatically
        # a moderator of every event in it.
        can = can_host = can_speak = False
        # An org admin runs their OWN organization's events. The org_id check is the whole
        # guard: access_for above admits any signed-in user to a PUBLIC event (and public is
        # the default visibility), so without it an org_admin of any other tenant arrived here
        # with can_host=True — a publish token plus every HOST_ONLY action
        # (broadcast.golive/end/emergency_stop, recording.*) on somebody else's live event, with
        # ctx.org_id set to the victim's org so the audit rows landed in the wrong tenant.
        # super_admin is platform-wide by definition and keeps the bypass.
        if user.role == "super_admin":
            can = can_host = can_speak = True
        elif user.role == "org_admin" and user.org_id == ev.org_id:
            can = can_host = can_speak = True
        elif user.role in ("moderator", "host", "speaker"):
            # The Event.org_id == user.org_id join is what makes an assignment confer power
            # only inside its own tenant. It is not reachable today (every write path to
            # EventAssignment goes through routers/events._require_org_members, which refuses
            # a non-member), but it BECOMES load-bearing now that accepting an invitation
            # creates assignments: without it, a stale assignment plus access_for's "any
            # signed-in user may open a public event" would hand can_host to an outsider.
            roles = set(db.scalars(
                select(EventAssignment.role)
                .join(Event, Event.id == EventAssignment.event_id)
                .where(
                    EventAssignment.event_id == ev.id,
                    EventAssignment.user_id == user.id,
                    Event.org_id == user.org_id,
                )
            ).all())
            can = bool(roles & {"moderator", "host"})
            # Only an assigned HOST gets broadcast control — being the org's host role is
            # not enough, and a moderator assignment never grants it.
            can_host = "host" in roles
            # A speaker or panellist assignment is what puts somebody on stage. `panelist` is
            # in ASSIGNMENT_ROLES as a credited team role; here it earns the same console,
            # because a panellist on a panel is a speaker in every way that matters to the
            # media layer. A host is a speaker too — they present their own slides.
            can_speak = bool(roles & {"speaker", "panelist", "host"})

        return Ctx(
            event_id=ev.id,
            org_id=ev.org_id,
            room=f"event_{ev.id}",
            user_id=user.id,
            name=user.full_name or user.email,
            identity=str(user.id),
            role=user.role,
            can_moderate=can,
            can_host=can_host,
            can_speak=can_speak,
        )
    finally:
        db.close()


# ── serializers ───────────────────────────────────────────────────────────────
# Keys match what the console components already render, so no client-side mapping layer.

def _iso(dt):
    return dt.isoformat() if dt else None


def message_out(m: LiveMessage) -> dict:
    return {
        "id": str(m.id), "name": m.author_name, "user_id": str(m.user_id) if m.user_id else None,
        "text": m.text, "status": m.status, "pinned": m.pinned, "highlighted": m.highlighted,
        "flags": m.flags or [], "flagged": bool(m.flags), "reactions": m.reactions or {},
        "reply_to": str(m.reply_to) if m.reply_to else None, "note": m.note,
        "created_at": _iso(m.created_at),
    }


def question_out(q: LiveQuestion) -> dict:
    return {
        "id": str(q.id), "name": q.author_name, "text": q.text, "votes": q.votes,
        "status": q.status, "pinned": q.pinned, "assigned_name": q.assigned_name,
        "assigned_to": str(q.assigned_to) if q.assigned_to else None,
        "answer_text": q.answer_text, "answered_at": _iso(q.answered_at),
        "flags": q.flags or [], "created_at": _iso(q.created_at),
    }


def poll_out(p: LivePoll) -> dict:
    return {
        "id": str(p.id), "question": p.question, "options": p.options or [], "status": p.status,
        "scheduled_at": _iso(p.scheduled_at), "closes_at": _iso(p.closes_at),
        "launched_at": _iso(p.launched_at), "closed_at": _iso(p.closed_at),
        "votes": sum(o.get("votes", 0) for o in (p.options or [])),
        "created_at": _iso(p.created_at),
    }


def announcement_out(a: LiveAnnouncement) -> dict:
    return {
        "id": str(a.id), "text": a.text, "priority": a.priority,
        "scheduled_at": _iso(a.scheduled_at), "sent_at": _iso(a.sent_at),
        "delivered_to": a.delivered_to,
        "status": "sent" if a.sent_at else "scheduled" if a.scheduled_at else "draft",
        "created_at": _iso(a.created_at),
    }


def activity_out(a: LiveActivity) -> dict:
    return {
        "id": str(a.id), "kind": a.kind, "text": a.text, "actor": a.actor_name,
        "created_at": _iso(a.created_at),
    }


# ── DB access off the event loop ──────────────────────────────────────────────

def _run(fn):
    db = SessionLocal()
    try:
        out = fn(db)
        db.commit()
        return out
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def tx(fn):
    """Run `fn(db)` in a worker thread with a short-lived Session, and commit."""
    return await asyncio.to_thread(_run, fn)


def _scoped(model, ctx: Ctx):
    return select(model).where(model.event_id == ctx.event_id, model.org_id == ctx.org_id)


def _row(db, model, ctx: Ctx, row_id):
    """Fetch one row with org+event isolation. Never trust an id from the wire."""
    try:
        rid = uuid.UUID(str(row_id))
    except (ValueError, TypeError, AttributeError):
        return None
    return db.scalar(_scoped(model, ctx).where(model.id == rid))


async def feed_activity(event_id: str, kind: str, text: str, *, actor: str = "LiveKit",
                        persist: bool = False) -> None:
    """Push one line onto the activity feed for an event we have no Ctx for (LiveKit
    webhooks: the actor is the media server, not a signed-in moderator). `record()` is the
    Ctx-bound equivalent used by moderator actions.

    ponytail: joins/leaves are NOT persisted (persist=False). At the 10k-viewer target that
    would be a write storm for information the presence snapshot already answers ("who is
    here now"). Room/recording milestones ARE persisted — they're the timeline a moderator
    scrolls back through.
    """
    if persist:
        row = await tx(lambda db: activity_out(_webhook_activity_row(db, event_id, kind, text, actor)))
    else:
        row = {"id": str(uuid.uuid4()), "kind": kind, "text": text,
               "created_at": None, "actor": None, "ephemeral": True}
    await bus.publish(event_id, "activity", "activity.new", row)


def _webhook_activity_row(db, event_id: str, kind: str, text: str, actor: str) -> LiveActivity:
    ev = db.get(Event, uuid.UUID(event_id))
    row = LiveActivity(event_id=ev.id, org_id=ev.org_id, kind=kind, text=text, actor_name=actor)
    db.add(row)
    db.flush()
    return row


def event_id_from_room(name: str | None) -> str | None:
    """Rooms are named `event_<uuid>` (see Ctx.room). Anything else belongs to another
    feature and is ignored by the webhook handler."""
    if not name or not name.startswith("event_"):
        return None
    try:
        return str(uuid.UUID(name[len("event_"):]))
    except ValueError:
        return None


def record(db, ctx: Ctx, kind: str, text: str, *, audit: str | None = None,
           target_type: str | None = None, target_id=None, meta: dict | None = None) -> dict:
    """Append to the console timeline; also write the compliance audit row when `audit`
    is given (every moderator action passes one). Single writer for both."""
    row = LiveActivity(event_id=ctx.event_id, org_id=ctx.org_id, kind=kind, text=text,
                       actor_name=ctx.name, meta=meta)
    db.add(row)
    db.flush()
    if audit:
        create_audit_log(db, actor=ctx.actor, action=audit, target_type=target_type,
                         target_id=target_id, org_id=ctx.org_id,
                         meta={"event_id": str(ctx.event_id), **(meta or {})})
    return activity_out(row)


# ── snapshot (initial load / after reconnect) ─────────────────────────────────

def _snapshot(db, ctx: Ctx) -> dict:
    ev = db.get(Event, ctx.event_id)
    host = db.scalar(
        select(User).join(EventAssignment, EventAssignment.user_id == User.id)
        .where(EventAssignment.event_id == ctx.event_id, EventAssignment.role == "host")
    )
    speakers = db.scalars(
        select(User).join(EventAssignment, EventAssignment.user_id == User.id)
        .where(EventAssignment.event_id == ctx.event_id, EventAssignment.role == "speaker")
    ).all()

    def recent(model, limit=HISTORY_LIMIT):
        return list(reversed(db.scalars(_scoped(model, ctx).order_by(model.created_at.desc()).limit(limit)).all()))

    started = ev.start_time if ev.status == "live" else None
    return {
        "event": {
            "id": str(ev.id), "name": ev.title or "Untitled event", "status": ev.status,
            "host": host.full_name if host else None,
            "recording": bool(ev.recording_enabled),
            "started_at": _iso(started),
            "features": {
                "chat": ev.chat_enabled, "qa": ev.qa_enabled, "polls": ev.polls_enabled,
                "raise_hand": ev.raise_hand_enabled,
            },
        },
        "speakers": [{"id": str(s.id), "name": s.full_name or s.email} for s in speakers],
        "messages": [message_out(m) for m in recent(LiveMessage) if m.status != "deleted"],
        "questions": [question_out(q) for q in recent(LiveQuestion)],
        "polls": [poll_out(p) for p in reversed(recent(LivePoll, 50))],
        "announcements": [announcement_out(a) for a in reversed(recent(LiveAnnouncement, 50))],
        "activity": [activity_out(a) for a in reversed(recent(LiveActivity, 100))],
        "can_moderate": ctx.can_moderate,
        "livekit_enforced": livekit.configured(),
    }


# Extra snapshot contributors, appended by services/broadcast.py at import. Keeps the host
# console's broadcast/recording/analytics block in the FIRST frame without this module
# having to know the host domain exists.
SNAPSHOT_EXTRAS: list = []


async def snapshot(ctx: Ctx) -> dict:
    snap = await tx(lambda db: _snapshot(db, ctx))
    snap["participants"] = await bus.presence_all(ctx.event_id)
    for extra in SNAPSHOT_EXTRAS:
        snap.update(await extra(ctx))
    return snap


# ── viewer projection ─────────────────────────────────────────────────────────
# The socket is shared by the host console, the moderator console and plain attendees, so
# the snapshot above and every envelope on the bus are built for the most privileged
# reader. Projecting them down for attendees has to happen on the SERVER: a client that
# merely declines to render the analytics block has still received it.
#
# Both projections are ALLOW-lists. A future snapshot key or bus channel is invisible to
# attendees until someone deliberately adds it here — the failure mode of forgetting is a
# missing panel, not a leak.

# Snapshot keys an attendee may see. Everything else (participants roster, activity feed,
# analytics, broadcast session, recordings, health, publish_token) is dropped.
VIEWER_SNAPSHOT_KEYS = frozenset({
    "event", "speakers", "messages", "questions", "polls", "announcements",
    "can_moderate", "can_host", "livekit_enforced", "countdown_until", "livekit_url",
    # Attendee additions: my own identity (so the client can tell its own messages, questions and
    # reactions apart), the room's running reaction tally, and the resources shared with the
    # audience. All three are either the caller's own or already public to the room.
    "identity", "reactions", "resources",
})

# Whole channels an attendee may receive unfiltered — the ones they participate in. `reaction` is
# ephemeral and carries only an emoji, a display name and the running totals.
VIEWER_CHANNELS = frozenset({"chat", "qa", "poll", "announcement", "reaction"})

# Individual envelope types from privileged channels that carry something an attendee
# legitimately needs, narrowed to the exact fields. `viewers` is the count on the player
# badge; `status`/`live` drive the LIVE indicator; `recording` tells the room it is being
# recorded, which attendees are entitled to know.
VIEWER_ENVELOPE_FIELDS = {
    # A private reply from a moderator. Already narrowed to ONE recipient by routers/live.py
    # before this projection runs, so what this entry decides is which fields that one person
    # sees — not who receives it.
    ("participants", "participant.notice"): ("to_identity", "text", "from_name"),
    ("moderator", "room.status"): ("live", "recovering"),
    ("moderator", "recording.status"): ("recording",),
    ("broadcast", "broadcast.update"): ("status",),
    ("broadcast", "broadcast.countdown"): ("until",),
    ("analytics", "analytics.tick"): ("viewers",),
}


def viewer_snapshot(snap: dict) -> dict:
    """Attendee-safe projection of the opening snapshot. Adds back the one aggregate an
    attendee is entitled to — the live viewer count — WITHOUT the presence roster or the
    engagement/health figures it was computed alongside."""
    out = {k: v for k, v in snap.items() if k in VIEWER_SNAPSHOT_KEYS}
    analytics = snap.get("analytics") or {}
    out["audience"] = {"viewers": analytics.get("viewers", 0)}
    # Broadcast status only — not the session object, which carries the host's settings.
    out["stream"] = {"status": (snap.get("broadcast") or {}).get("status")}
    out["can_moderate"] = False
    out["can_host"] = False
    return out


def viewer_envelope(env: dict) -> dict | None:
    """Attendee-safe projection of one bus envelope, or None to drop it entirely."""
    channel, kind = env.get("channel"), env.get("type")
    if channel in VIEWER_CHANNELS:
        return env
    fields = VIEWER_ENVELOPE_FIELDS.get((channel, kind))
    if fields is None:
        return None
    data = env.get("data") or {}
    return {**env, "data": {k: data[k] for k in fields if k in data}}


# ── speaker projection ────────────────────────────────────────────────────────
# A third tier, between the attendee and the console. A speaker is ON the broadcast, so they
# legitimately need the stage roster (to see who else is up), their own media grant, the
# presentation and whiteboard state, and their speaking-time figure. They are NOT running the
# event, so the moderation queue, the audit timeline, recordings and the full analytics block
# stay out.
#
# Same allow-list discipline as the attendee projection, and for the same reason: a client that
# merely declines to render a panel has still received its data.

SPEAKER_SNAPSHOT_KEYS = frozenset({
    "event", "speakers", "messages", "questions", "polls", "announcements",
    "can_moderate", "can_host", "can_speak", "livekit_enforced", "countdown_until",
    "livekit_url", "participants",
    # services/speaker.py's own contribution: my assets, the live presentation, the whiteboard,
    # my notes and my speaking time. Every key is either mine or already public to the room.
    "presentation", "whiteboard", "assets", "notes", "stage", "publish_token",
    "publish_identity", "publish_sources", "speaking", "assigned_questions", "identity",
    "reactions",
})

# Channels a speaker receives unfiltered: the ones they take part in, plus the two that carry
# the presentation and the whiteboard they are collaborating on.
SPEAKER_CHANNELS = frozenset({"chat", "qa", "poll", "announcement", "presentation", "whiteboard"})

# Individual envelopes from privileged channels. A speaker needs to know the room is live, that
# it is being recorded, how many people are watching, and when their own grant changes.
SPEAKER_ENVELOPE_FIELDS = {
    ("participants", "participant.notice"): ("to_identity", "text", "from_name"),
    ("moderator", "room.status"): ("live", "recovering"),
    ("moderator", "recording.status"): ("recording",),
    ("broadcast", "broadcast.update"): ("status",),
    ("broadcast", "broadcast.countdown"): ("until",),
    ("analytics", "analytics.tick"): ("viewers", "participants", "speakers", "hands"),
    # Stage changes: a speaker must see themselves being invited up or taken down, and who else
    # is on the stage. The presence record is already visible to them in the snapshot roster.
    ("participants", "participant.join"): None,
    ("participants", "participant.update"): None,
    ("participants", "participant.leave"): None,
    ("stage", "waiting.admitted"): ("identity",),
}

# Presence fields a speaker may see about OTHER people. The full record carries a moderator's
# working notes on somebody — chat mutes, timeouts, ban state, per-person telemetry — which is
# audience management, not the stage.
SPEAKER_PRESENCE_KEYS = frozenset({
    "identity", "name", "role", "on_stage", "speaking", "muted", "hand", "publishing",
    "camera_allowed", "share_allowed", "joined_at", "quality",
})


def speaker_presence(rec: dict) -> dict:
    return {k: v for k, v in (rec or {}).items() if k in SPEAKER_PRESENCE_KEYS}


def speaker_snapshot(snap: dict) -> dict:
    """Speaker-safe projection of the opening snapshot."""
    out = {k: v for k, v in snap.items() if k in SPEAKER_SNAPSHOT_KEYS}
    out["participants"] = [speaker_presence(p) for p in (snap.get("participants") or [])]
    analytics = snap.get("analytics") or {}
    # The audience figures a presenter is entitled to, without the engagement/health block.
    out["audience"] = {
        "viewers": analytics.get("viewers", 0),
        "participants": analytics.get("participants", 0),
        "speakers": analytics.get("speakers", 0),
        "hands": analytics.get("hands", 0),
    }
    out["stream"] = {"status": (snap.get("broadcast") or {}).get("status")}
    out["can_moderate"] = False
    out["can_host"] = False
    return out


def speaker_envelope(env: dict) -> dict | None:
    """Speaker-safe projection of one bus envelope, or None to drop it."""
    channel, kind = env.get("channel"), env.get("type")
    if channel in SPEAKER_CHANNELS:
        return env
    if (channel, kind) not in SPEAKER_ENVELOPE_FIELDS:
        return None
    fields = SPEAKER_ENVELOPE_FIELDS[(channel, kind)]
    data = env.get("data") or {}
    # None means "the whole payload, narrowed by the presence allow-list instead" — used for the
    # participant.* envelopes, whose shape is a presence record rather than a fixed field set.
    if fields is None:
        return {**env, "data": speaker_presence(data)}
    return {**env, "data": {k: data[k] for k in fields if k in data}}


# ── actions ───────────────────────────────────────────────────────────────────
# Each handler is `async (ctx, payload) -> list[(channel, type, data)]`; the dispatcher
# publishes whatever comes back. Handlers that touch the DB wrap it in tx().

# Actions any authenticated attendee may perform. Everything else needs can_moderate.
VIEWER_ACTIONS = frozenset({
    "chat.send", "chat.typing", "chat.react", "qa.ask", "qa.vote", "poll.vote",
    "participant.hand", "participant.state",
    # Reporting abuse is only useful if the AUDIENCE can do it. It flags for review and never
    # deletes, and its result goes back on a channel attendees don't receive (see _chat_report).
    "chat.report",
})


def _text(payload, key="text", limit=2000) -> str:
    value = (payload.get(key) or "").strip()
    return value[:limit]


# chat ------------------------------------------------------------------------

# Emoji-only mode: a message must be nothing but emoji/whitespace. Covers the pictographic
# blocks plus variation selectors and ZWJ so multi-codepoint emoji ("👨‍👩‍👧") pass.
_EMOJI_ONLY = re.compile(
    r"^[\s‍️☀-➿\U0001f000-\U0001faff\U0001f1e6-\U0001f1ff#*0-9⃣]+$"
)


# The host's chat controls as PURE decisions, so the policy can be read (and tested) in one
# place instead of being buried in a DB transaction. Enforcement is the whole point: a
# toggle that only changes an icon is worse than no toggle, because the host believes chat
# is off. Staff bypass their own audience controls — a host must keep a voice in their room.

def chat_gate(settings: dict, ctx: Ctx, text: str, presence: dict | None = None) -> str | None:
    """Reason to reject this message outright, or None to allow it.

    `presence` is the AUTHOR's own presence record, carrying a per-person chat mute. That is
    deliberately separate from `muted` (which is the microphone): a host mutes a speaker's mic
    mid-answer all the time and must not silence their chat as a side effect.
    """
    if ctx.can_moderate:
        return None
    if settings.get("chat_enabled") is False:
        return "Chat is turned off"
    muted_until = _chat_mute_remaining(presence or {})
    if muted_until is not None:
        return ("You've been muted in chat" if muted_until <= 0
                else f"You've been muted in chat for another {muted_until} min")
    if settings.get("emoji_only") and not _EMOJI_ONLY.match(text):
        return "Emoji-only mode is on"
    if settings.get("subscriber_only") and ctx.role == "viewer":
        return "Chat is limited to members right now"
    return None


def _chat_mute_remaining(presence: dict) -> int | None:
    """Minutes left on this person's chat mute, 0 for an indefinite one, None if not muted.

    The expiry is checked at SEND time rather than by a timer: a scheduled unmute would need a
    job per mute and would silently keep somebody muted if the worker restarted.
    """
    if not presence.get("chat_muted"):
        return None
    until = _parse_dt(presence.get("chat_muted_until"))
    if until is None:
        return 0
    left = (until - datetime.now(timezone.utc)).total_seconds()
    return None if left <= 0 else max(1, int(left // 60) + 1)


def slow_mode_error(slow_seconds: int, since_last: float | None) -> str | None:
    """Slow mode, measured against this author's own previous message."""
    if not slow_seconds or since_last is None or since_last >= slow_seconds:
        return None
    return f"Slow mode is on — wait {int(slow_seconds - since_last) + 1}s"


# Which automatic detections the host currently cares about. flag_text always runs; these
# toggles decide whether a detection actually holds the message back.
_FLAG_TOGGLES = {"profanity": "profanity_filter", "spam": "spam_filter",
                 "duplicate": "spam_filter", "link": "spam_filter"}


def enabled_flags(flags: list[str], settings: dict) -> list[str]:
    """Drop detections whose filter the host switched off, so 'spam filter: off' means it."""
    return [f for f in flags if settings.get(_FLAG_TOGGLES.get(f, ""), True)]


async def _chat_send(ctx, payload):
    text = _text(payload)
    if not text:
        return []

    # Read from the bus, not the DB — this runs once per message. Presence is only needed for
    # the chat-mute check, which staff bypass, so it is not fetched on the moderator path.
    settings = await bus.state_get(ctx.event_id)
    presence = None if ctx.can_moderate else await bus.presence_get(ctx.event_id, ctx.identity)
    blocked_reason = chat_gate(settings, ctx, text, presence)
    if blocked_reason:
        return blocked_reason

    slow = int(settings.get("slow_mode_seconds") or 0)
    auto_mod = bool(settings.get("auto_moderation"))
    now = datetime.now(timezone.utc)

    def work(db):
        since = now - DUPLICATE_WINDOW
        prior = db.scalars(
            _scoped(LiveMessage, ctx).where(
                LiveMessage.user_id == ctx.user_id, LiveMessage.created_at >= since
            ).order_by(LiveMessage.created_at.desc()).limit(10)
        ).all()

        if not ctx.can_moderate and prior:
            error = slow_mode_error(slow, (now - prior[0].created_at).total_seconds())
            if error:
                return error

        flags = enabled_flags(flag_text(text, [normalize(p.text) for p in prior]), settings)
        # A reply_to pointing at another event's message (or nothing) is dropped, not
        # trusted — _row already scopes the lookup, so this just tolerates a miss.
        parent = _row(db, LiveMessage, ctx, payload.get("reply_to")) if payload.get("reply_to") else None
        # Auto-moderation removes flagged content outright; otherwise it's held for a human.
        blocked = bool(flags) and auto_mod and not ctx.can_moderate
        msg = LiveMessage(
            event_id=ctx.event_id, org_id=ctx.org_id, user_id=ctx.user_id, author_name=ctx.name,
            text=text, flags=flags, reactions={},
            status="deleted" if blocked else "pending" if flags else "approved",
            deleted_at=now if blocked else None,
            reply_to=parent.id if parent else None,
        )
        db.add(msg)
        db.flush()
        if blocked:
            # Kept in the chat log for audit, never broadcast. The feed records the removal.
            return record(db, ctx, "mod", f"Auto-moderation removed a message from {ctx.name}",
                          audit="live.message.auto_removed", target_type="live_message",
                          target_id=msg.id, meta={"flags": flags}), True
        return message_out(msg), False

    out = await tx(work)
    if isinstance(out, str):
        return out                                   # rejection -> error frame to the sender
    data, blocked = out
    if blocked:
        return [("activity", "activity.new", data)]
    return [("chat", "message.new", data)]


async def _chat_typing(ctx, payload):
    # Ephemeral: never persisted, never audited.
    return [("chat", "typing", {"name": ctx.name, "identity": ctx.identity,
                                "typing": bool(payload.get("typing", True))})]


async def _chat_react(ctx, payload):
    emoji = _text(payload, "emoji", 8)
    if not emoji:
        return []

    def work(db):
        m = _row(db, LiveMessage, ctx, payload.get("id"))
        if not m:
            return None
        counts = dict(m.reactions or {})
        counts[emoji] = counts.get(emoji, 0) + 1
        m.reactions = counts
        return message_out(m)

    msg = await tx(work)
    return [("chat", "message.update", msg)] if msg else []


async def _chat_moderate(ctx, payload, op: str):
    """approve | pin | highlight | delete | note — one body, since they differ only in the
    field they set and the sentence they log."""

    def work(db):
        m = _row(db, LiveMessage, ctx, payload.get("id"))
        if not m:
            return None
        if op == "approve":
            m.status, m.flags = "approved", []
            text = f"Approved a message from {m.author_name}"
        elif op == "highlight":
            # Non-exclusive, unlike pin: several messages can be queued for the host to read.
            m.highlighted = not m.highlighted
            text = f"{'Highlighted' if m.highlighted else 'Unhighlighted'} a message from {m.author_name}"
        elif op == "pin":
            pin = not m.pinned
            if pin:  # only one pinned message at a time
                for other in db.scalars(_scoped(LiveMessage, ctx).where(LiveMessage.pinned.is_(True))).all():
                    other.pinned = False
            m.pinned = pin
            text = f"{'Pinned' if pin else 'Unpinned'} a message from {m.author_name}"
        elif op == "note":
            m.note = _text(payload, "note", 500)
            text = f"Added a note on {m.author_name}'s message"
        else:  # delete — soft, so the chat log survives for export/compliance
            m.status, m.deleted_at, m.deleted_by = "deleted", datetime.now(timezone.utc), ctx.user_id
            text = f"Deleted a message from {m.author_name}"
        act = record(db, ctx, "chat", text, audit=f"live.message.{op}",
                     target_type="live_message", target_id=m.id)
        return message_out(m), act

    out = await tx(work)
    if not out:
        return []
    msg, act = out
    kind = "message.delete" if op == "delete" else "message.update"
    return [("chat", kind, msg), ("activity", "activity.new", act)]


async def _chat_bulk(ctx, payload):
    """Bulk delete/approve. ponytail: capped at 100 ids so one socket frame can't ask for
    an unbounded transaction."""
    ids = [str(i) for i in (payload.get("ids") or [])][:100]
    op = payload.get("op") if payload.get("op") in ("delete", "approve") else "delete"
    if not ids:
        return []

    def work(db):
        rows = []
        for raw in ids:
            m = _row(db, LiveMessage, ctx, raw)
            if not m:
                continue
            if op == "approve":
                m.status, m.flags = "approved", []
            else:
                m.status, m.deleted_at, m.deleted_by = "deleted", datetime.now(timezone.utc), ctx.user_id
            rows.append(message_out(m))
        act = record(db, ctx, "mod", f"Bulk {op}d {len(rows)} message(s)", audit=f"live.message.bulk_{op}",
                     target_type="live_message", meta={"count": len(rows)})
        return rows, act

    rows, act = await tx(work)
    kind = "message.delete" if op == "delete" else "message.update"
    return [("chat", kind, r) for r in rows] + [("activity", "activity.new", act)]


async def _chat_mute(ctx, payload):
    """Silence one person in CHAT, optionally for a while. Separate from participant.mute,
    which is their microphone — the two are different punishments and a moderator needs to be
    able to apply either without the other.

    Enforced in chat_gate at send time, so it survives a reconnect (the mute lives on the
    presence record, not on the socket) and expires without a scheduled job.
    """
    identity = str(payload.get("identity") or "")
    if not identity:
        return []
    muted = bool(payload.get("muted", True))
    minutes = payload.get("minutes")
    current = await bus.presence_get(ctx.event_id, identity)
    # Staff bypass their own audience controls (chat_gate returns early for can_moderate), so
    # "muted" would show in the roster while their messages still landed. Refuse instead of
    # displaying a control that does nothing.
    if muted and current.get("role") in ("host", "moderator"):
        return "Hosts and moderators can't be muted in chat"

    patch = {"chat_muted": muted, "chat_muted_until": None}
    if muted and minutes:
        try:
            span = max(1, min(int(minutes), 1440))
        except (TypeError, ValueError):
            span = 5
        patch["chat_muted_until"] = (datetime.now(timezone.utc) + timedelta(minutes=span)).isoformat()

    rec = await bus.presence_upsert(ctx.event_id, identity, patch)
    name = rec.get("name") or identity
    window = f" for {minutes} min" if muted and patch["chat_muted_until"] else ""
    act = await tx(lambda db: record(
        db, ctx, "mod", f"{name} was {'muted' if muted else 'unmuted'} in chat{window}",
        audit="live.chat.mute", target_type="participant", target_id=identity,
        meta={"muted": muted, "until": patch["chat_muted_until"]}))
    return [("participants", "participant.update", rec), ("activity", "activity.new", act)]


async def _chat_report(ctx, payload):
    """An ATTENDEE reporting a message. Flags it for review — never deletes it, and never
    tells the reporter whether a moderator acted, so reporting cannot be used to probe the
    moderation queue.

    A viewer action on purpose: "Participant Reports Abuse" is only useful if the audience can
    actually raise it. The socket's own rate limiter bounds how fast anyone can report.
    """
    reason = _text(payload, "reason", 200)

    def work(db):
        m = _row(db, LiveMessage, ctx, payload.get("id"))
        if not m or m.status == "deleted":
            return None
        flags = list(m.flags or [])
        if "reported" not in flags:
            flags.append("reported")
            m.flags = flags
        # Pull it back into the review queue; an already-deleted message is left alone above.
        if m.status == "approved":
            m.status = "pending"
        act = record(db, ctx, "mod", f"{ctx.name} reported a message from {m.author_name}",
                     audit="live.message.report", target_type="live_message", target_id=m.id,
                     meta={"reason": reason} if reason else None)
        return message_out(m), act

    out = await tx(work)
    if not out:
        return []
    msg, act = out
    # NOT on the `chat` channel: attendees receive that one unfiltered (VIEWER_CHANNELS), so
    # broadcasting the flagged copy would let anyone report every message and read the room's
    # moderation state back off their own screen. `moderator` is not in the attendee allow-list,
    # so this update reaches consoles only.
    return [("moderator", "message.flagged", msg), ("activity", "activity.new", act)]


# Q&A -------------------------------------------------------------------------

async def _qa_ask(ctx, payload):
    text = _text(payload, limit=1000)
    if not text:
        return []

    def work(db):
        q = LiveQuestion(event_id=ctx.event_id, org_id=ctx.org_id, user_id=ctx.user_id,
                         author_name=ctx.name, text=text, flags=flag_text(text))
        db.add(q)
        db.flush()
        return question_out(q), record(db, ctx, "qa", f"New question from {ctx.name}")

    q, act = await tx(work)
    return [("qa", "question.new", q), ("activity", "activity.new", act)]


async def _qa_vote(ctx, payload):
    def work(db):
        q = _row(db, LiveQuestion, ctx, payload.get("id"))
        if not q:
            return None
        # ponytail: no per-user vote ledger — one upvote row per person needs its own
        # table; add live_question_votes if vote-stuffing shows up.
        q.votes = max(0, q.votes + (-1 if payload.get("down") else 1))
        return question_out(q)

    q = await tx(work)
    return [("qa", "question.update", q)] if q else []


_QA_OPS = {
    "approve": ("approved", "Approved a question from {name}"),
    "answer": ("answered", "Marked a question from {name} as answered"),
    "dismiss": ("dismissed", "Dismissed a question from {name}"),
}


async def _qa_moderate(ctx, payload, op: str):
    def work(db):
        q = _row(db, LiveQuestion, ctx, payload.get("id"))
        if not q:
            return None
        if op in _QA_OPS:
            q.status, template = _QA_OPS[op]
            text = template.format(name=q.author_name)
        elif op == "pin":
            pin = not q.pinned
            if pin:
                for other in db.scalars(_scoped(LiveQuestion, ctx).where(LiveQuestion.pinned.is_(True))).all():
                    other.pinned = False
            q.pinned = pin
            text = f"{'Pinned' if pin else 'Unpinned'} a question"
        elif op == "merge":
            # Fold a duplicate INTO another question: votes move across, the duplicate goes.
            # `id` is the one being absorbed and `into` is the survivor, so the row the
            # moderator clicked is the one that disappears — the other order silently deletes
            # the question they were looking at.
            target = _row(db, LiveQuestion, ctx, payload.get("into"))
            if target is None or target.id == q.id:
                return None
            qid, author = str(q.id), q.author_name
            target.votes = max(0, target.votes + q.votes)
            db.delete(q)
            act = record(db, ctx, "qa", f"Merged {author}'s duplicate question into another",
                         audit="live.question.merge", target_type="live_question", target_id=qid,
                         meta={"into": str(target.id)})
            # Two frames: the duplicate leaves every console, the survivor's count goes up.
            return {"id": qid}, act, True, question_out(target)
        elif op == "assign":
            # Org isolation: a speaker_id from the wire must belong to THIS event's org,
            # or the console could route a question to a stranger in another tenant.
            speaker = None
            if payload.get("speaker_id"):
                speaker = db.scalar(
                    select(User).where(User.id == payload["speaker_id"],
                                       User.org_id == ctx.org_id,
                                       User.deleted_at.is_(None))
                )
                if speaker is None:
                    return None
            q.assigned_to = speaker.id if speaker else None
            q.assigned_name = (speaker.full_name or speaker.email) if speaker else None
            text = f"Assigned a question to {q.assigned_name}" if speaker else "Unassigned a question"
        else:  # delete
            # Read what we need BEFORE the delete: record() flushes, after which a deleted
            # instance's attributes are gone.
            qid, author = str(q.id), q.author_name
            db.delete(q)
            act = record(db, ctx, "qa", f"Deleted a question from {author}",
                         audit="live.question.delete", target_type="live_question", target_id=qid)
            return {"id": qid}, act, True
        act = record(db, ctx, "qa", text, audit=f"live.question.{op}",
                     target_type="live_question", target_id=q.id)
        return question_out(q), act, False

    out = await tx(work)
    if not out:
        return []
    # `rest` is only populated by merge, which also has to push the SURVIVOR's new vote count.
    data, act, deleted, *rest = out
    frames = [("qa", "question.delete" if deleted else "question.update", data)]
    if rest and rest[0]:
        frames.append(("qa", "question.update", rest[0]))
    frames.append(("activity", "activity.new", act))
    return frames


async def _qa_respond(ctx, payload):
    """A SPEAKER answering a question routed to them, and marking it done.

    Authorization is per-ROW, not per-role: `can_speak` gets you into this handler, but the
    question has to be assigned to YOU. Without that, any panellist on the event could answer
    (and close) every other panellist's questions. A moderator may answer anything — they route
    the queue, and they already can via qa.answer.
    """
    text = _text(payload, "answer", 2000)
    completed = bool(payload.get("completed", True))

    def work(db):
        q = _row(db, LiveQuestion, ctx, payload.get("id"))
        if not q:
            return None
        if not ctx.can_moderate and q.assigned_to != ctx.user_id:
            return "That question isn't assigned to you"
        if text:
            q.answer_text = text
        if completed:
            q.status = "answered"
            q.answered_by = ctx.user_id
            q.answered_at = datetime.now(timezone.utc)
        verb = "answered" if completed else "replied to"
        act = record(db, ctx, "qa", f"{ctx.name} {verb} a question from {q.author_name}",
                     audit="live.question.respond", target_type="live_question", target_id=q.id,
                     meta={"completed": completed, "has_answer": bool(text)})
        return question_out(q), act

    out = await tx(work)
    if out is None:
        return []
    if isinstance(out, str):
        return out
    return [("qa", "question.update", out[0]), ("activity", "activity.new", out[1])]


async def _qa_escalate(ctx, payload):
    """"Flag for moderator" — a speaker handing a question back rather than answering it.

    A flag, never a delete: the moderator decides what happens to it. Same per-row rule as
    responding, so escalating is not a way to touch somebody else's queue.
    """
    reason = _text(payload, "reason", 200)

    def work(db):
        q = _row(db, LiveQuestion, ctx, payload.get("id"))
        if not q:
            return None
        if not ctx.can_moderate and q.assigned_to != ctx.user_id:
            return "That question isn't assigned to you"
        flags = list(q.flags or [])
        if "escalated" not in flags:
            flags.append("escalated")
            q.flags = flags
        q.status = "pending"       # back into the moderator's review queue
        act = record(db, ctx, "qa", f"{ctx.name} flagged a question for a moderator",
                     audit="live.question.escalate", target_type="live_question", target_id=q.id,
                     meta={"reason": reason} if reason else None)
        return question_out(q), act

    out = await tx(work)
    if out is None:
        return []
    if isinstance(out, str):
        return out
    return [("qa", "question.update", out[0]), ("activity", "activity.new", out[1])]


# polls -----------------------------------------------------------------------

def _clean_options(raw) -> list[dict]:
    """Accepts ["A","B"] or [{label,votes}] and always yields [{label, votes}] — the
    shape the console renders and the DB stores."""
    out = []
    for o in raw or []:
        label = (o.get("label") if isinstance(o, dict) else o) or ""
        label = str(label).strip()[:160]
        if label:
            out.append({"label": label, "votes": int(o.get("votes", 0)) if isinstance(o, dict) else 0})
    return out[:10]


def _merge_votes(existing: list, incoming: list) -> list:
    """Carry votes across a poll edit, matched by label. The editor sends labels only, so
    without this, fixing a typo in a live poll's wording would silently reset every vote
    already cast for the options that didn't change."""
    prior = {o.get("label"): o.get("votes", 0) for o in (existing or [])}
    return [{**o, "votes": o["votes"] or prior.get(o["label"], 0)} for o in incoming]


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _poll_create(ctx, payload):
    question = _text(payload, "question", 300)
    options = _clean_options(payload.get("options"))
    if not question or len(options) < 2:
        return []
    scheduled = _parse_dt(payload.get("scheduled_at"))
    launch_now = not scheduled and payload.get("status", "live") == "live"
    seconds = int(payload.get("duration_seconds") or 0)
    now = datetime.now(timezone.utc)

    def work(db):
        p = LivePoll(
            event_id=ctx.event_id, org_id=ctx.org_id, question=question, options=options,
            status="live" if launch_now else "scheduled" if scheduled else "draft",
            scheduled_at=scheduled, created_by=ctx.user_id,
            launched_at=now if launch_now else None,
            closes_at=(now + timedelta(seconds=seconds)) if launch_now and seconds else None,
        )
        db.add(p)
        db.flush()
        verb = "Launched" if launch_now else "Scheduled" if scheduled else "Drafted"
        act = record(db, ctx, "poll", f"{verb} poll: {question}", audit="live.poll.create",
                     target_type="live_poll", target_id=p.id)
        return poll_out(p), act

    p, act = await tx(work)
    return [("poll", "poll.new", p), ("activity", "activity.new", act)]


async def _poll_update(ctx, payload):
    def work(db):
        p = _row(db, LivePoll, ctx, payload.get("id"))
        if not p:
            return None
        if p.status == "closed":
            return None  # a closed poll's results are final
        if payload.get("question"):
            p.question = _text(payload, "question", 300)
        options = _clean_options(payload.get("options"))
        if options and len(options) >= 2:
            p.options = _merge_votes(p.options, options)
        if "scheduled_at" in payload:
            p.scheduled_at = _parse_dt(payload.get("scheduled_at"))
            p.status = "scheduled" if p.scheduled_at else p.status
        act = record(db, ctx, "poll", f"Edited poll: {p.question}", audit="live.poll.update",
                     target_type="live_poll", target_id=p.id)
        return poll_out(p), act

    out = await tx(work)
    if not out:
        return []
    return [("poll", "poll.update", out[0]), ("activity", "activity.new", out[1])]


async def _poll_lifecycle(ctx, payload, op: str):
    now = datetime.now(timezone.utc)
    seconds = int(payload.get("duration_seconds") or 0)

    def work(db):
        p = _row(db, LivePoll, ctx, payload.get("id"))
        if not p:
            return None
        if op == "launch":
            p.status, p.launched_at = "live", now
            p.closes_at = now + timedelta(seconds=seconds) if seconds else None
            text = f"Launched poll: {p.question}"
        elif op == "close":
            p.status, p.closed_at, p.closes_at = "closed", now, None
            text = f"Closed poll: {p.question}"
        else:  # delete
            # Capture before the delete — record() flushes, and a deleted instance's
            # attributes are unavailable after that.
            pid, question = str(p.id), p.question
            db.delete(p)
            act = record(db, ctx, "poll", f"Deleted poll: {question}", audit="live.poll.delete",
                         target_type="live_poll", target_id=pid)
            return {"id": pid}, act, True
        act = record(db, ctx, "poll", text, audit=f"live.poll.{op}",
                     target_type="live_poll", target_id=p.id)
        return poll_out(p), act, False

    out = await tx(work)
    if not out:
        return []
    data, act, deleted = out
    return [("poll", "poll.delete" if deleted else "poll.update", data), ("activity", "activity.new", act)]


async def _poll_vote(ctx, payload):
    index = payload.get("option")

    def work(db):
        p = _row(db, LivePoll, ctx, payload.get("id"))
        if not p or p.status != "live":
            return None
        options = [dict(o) for o in (p.options or [])]
        if not isinstance(index, int) or not 0 <= index < len(options):
            return None
        options[index]["votes"] = options[index].get("votes", 0) + 1
        p.options = options  # reassign: JSON columns don't track in-place mutation
        return poll_out(p)

    p = await tx(work)
    return [("poll", "poll.update", p)] if p else []


# announcements ---------------------------------------------------------------

async def _announce_send(ctx, payload):
    text = _text(payload, limit=1000)
    if not text:
        return []
    priority = payload.get("priority") if payload.get("priority") in ("normal", "important", "urgent") else "normal"
    scheduled = _parse_dt(payload.get("scheduled_at"))
    now = datetime.now(timezone.utc)
    # Honest number: connections this worker is serving right now, not an estimate.
    delivered = None if scheduled else bus.local_subscribers(ctx.event_id)

    def work(db):
        a = LiveAnnouncement(event_id=ctx.event_id, org_id=ctx.org_id, text=text, priority=priority,
                             scheduled_at=scheduled, sent_at=None if scheduled else now,
                             delivered_to=delivered, created_by=ctx.user_id)
        db.add(a)
        db.flush()
        verb = "Scheduled" if scheduled else "Broadcast"
        act = record(db, ctx, "system", f"{verb} an announcement", audit="live.announcement.send",
                     target_type="live_announcement", target_id=a.id, meta={"priority": priority})
        return announcement_out(a), act

    a, act = await tx(work)
    out = [("announcement", "announcement.new", a), ("activity", "activity.new", act)]
    return out


async def _announce_delete(ctx, payload):
    def work(db):
        a = _row(db, LiveAnnouncement, ctx, payload.get("id"))
        if not a:
            return None
        aid = str(a.id)   # captured before the delete; record() flushes it away
        db.delete(a)
        return {"id": aid}, record(db, ctx, "system", "Removed an announcement",
                                   audit="live.announcement.delete",
                                   target_type="live_announcement", target_id=aid)

    out = await tx(work)
    if not out:
        return []
    return [("announcement", "announcement.delete", out[0]), ("activity", "activity.new", out[1])]


# participants ----------------------------------------------------------------
# Presence state is authoritative in Redis; LiveKit is asked to ENFORCE the change.
# The state update + broadcast happen whether or not LiveKit is configured, so the
# console is never lying about what it was told to do (see snapshot.livekit_enforced).

def allowed_sources(rec: dict) -> tuple[str, ...]:
    """Which LiveKit track sources this participant may publish, derived from their presence
    record. One pure function, so staging, the camera/share controls and a speaker's own token
    grant can never disagree about what somebody is allowed to send.

    Screen share defaults differ BY ROLE deliberately: the host is the one presenting, so they
    have it; a speaker or panellist needs an explicit grant. That is what stops somebody invited
    up to answer one question from putting their desktop on the main screen. The camera/mic
    flags read `is not False` so an absent key means allowed — presence records predate them.
    """
    role = rec.get("role") or "viewer"
    if rec.get("banned") or rec.get("waiting"):
        return ()
    # A host or speaker publishes by virtue of their role; anyone else has to be staged.
    if not (rec.get("on_stage") or role in ("host", "speaker")):
        return ()
    out = []
    if rec.get("camera_allowed") is not False:
        out.append(livekit.CAMERA)
    if rec.get("mic_allowed") is not False:
        out.append(livekit.MICROPHONE)
    if rec.get("share_allowed", role == "host"):
        out.extend((livekit.SCREEN_SHARE, livekit.SCREEN_SHARE_AUDIO))
    return tuple(out)


# Alias used inside _participant_action, where `allowed_sources` would read ambiguously next to
# the local `allowed` variable.
_sources_for = allowed_sources


async def _participant_hand(ctx, payload):
    raised = bool(payload.get("raised", True))
    # `hand_at` is what gives the moderator's hand queue its ORDER. Stamped here, on a write
    # that happens anyway, because the alternative — the console remembering the order it first
    # saw each hand — loses the queue on every reconnect and on a second moderator's screen.
    rec = await bus.presence_upsert(ctx.event_id, ctx.identity, {
        "hand": raised,
        "hand_at": datetime.now(timezone.utc).timestamp() if raised else None,
    })
    return [("participants", "participant.update", rec)]


_QUALITY = ("excellent", "good", "poor", "lost")


async def _participant_state(ctx, payload):
    """A client reporting its OWN media state. LiveKit webhooks carry joins/leaves and
    track publishes, but mute / speaking / connection-quality are client-room events — the
    participant is the only server-side source for them, so the console shows what was
    actually reported rather than a guess. Always scoped to ctx.identity: nobody can
    report state on somebody else."""
    patch = {}
    if "muted" in payload:
        patch["muted"] = bool(payload["muted"])
    if payload.get("quality") in _QUALITY:
        patch["quality"] = payload["quality"]
    # Live encoder telemetry, reported by the publisher because only the browser's peer
    # connection knows it. Bounded and coerced — these land on a record every console reads.
    for key, cap in (("bitrate_kbps", 100_000), ("packet_loss", 100), ("rtt_ms", 60_000),
                     ("fps", 240)):
        if key in payload:
            try:
                patch[key] = max(0, min(int(payload[key]), cap))
            except (TypeError, ValueError):
                pass

    if "speaking" in payload:
        speaking = bool(payload["speaking"])
        patch["speaking"] = speaking
        # Speaking TIME, accumulated on the falling edge. The publisher reports edges, not a
        # duration, so the server owns the arithmetic and a client cannot inflate its own total.
        # presence_upsert is a read-modify-write (bus.py), but every write for one identity
        # comes from that identity's single socket task, so there is no concurrent writer to
        # lose an update to.
        current = await bus.presence_get(ctx.event_id, ctx.identity)
        now_ts = datetime.now(timezone.utc).timestamp()
        if speaking:
            if not current.get("speaking"):
                patch["speaking_since"] = now_ts
        elif current.get("speaking") and current.get("speaking_since"):
            elapsed = max(0.0, now_ts - float(current["speaking_since"]))
            patch["speaking_ms"] = int(current.get("speaking_ms") or 0) + int(elapsed * 1000)
            patch["speaking_since"] = None

    if not patch:
        return []
    rec = await bus.presence_upsert(ctx.event_id, ctx.identity, patch)
    return [("participants", "participant.update", rec)]


async def _participant_action(ctx, payload, op: str):
    identity = str(payload.get("identity") or "")
    if not identity:
        return []
    # Self-target refusal on the two ops that GRANT something. Staging somebody is a
    # moderator's job, but staging YOURSELF is a privilege escalation: livekit.set_stage
    # issues can_publish=True, which overrides the subscribe-only grant the token carried, and
    # participant.stage needs only can_moderate (it is not in HOST_ONLY). Same for writing
    # yourself a "host" presence role. Every other op (mute, timeout, ban, remove) only takes
    # something away, so self-targeting those is harmless.
    if op in ("stage", "role") and identity == ctx.identity:
        return "You can't change your own stage access or role"
    now = datetime.now(timezone.utc)
    patch: dict = {}
    enforced = True

    if op == "hand":
        # Decline a raised hand. The self-service participant.hand action is scoped to the
        # sender's own identity, so a moderator clearing the queue needs its own op.
        patch = {"hand": False, "hand_at": None}
        enforced = True     # nothing to enforce in LiveKit: a raised hand is our own state
        text = "{name}'s raised hand was declined"
    elif op == "mute":
        muted = bool(payload.get("muted", True))
        patch = {"muted": muted}
        enforced = await livekit.mute_participant(ctx.room, identity, muted)
        text = "{name} was " + ("muted" if muted else "unmuted")
    elif op == "timeout":
        minutes = max(1, min(int(payload.get("minutes") or 5), 120))
        patch = {"muted": True, "muted_until": (now + timedelta(minutes=minutes)).isoformat()}
        enforced = await livekit.mute_participant(ctx.room, identity, True)
        text = "{name} was muted for " + f"{minutes} min"
    elif op == "stage":
        on = bool(payload.get("on_stage", True))
        patch = {"on_stage": on}
        # Only relabel an ATTENDEE. The old code wrote role="speaker"/"viewer" unconditionally,
        # which demoted an assigned speaker or moderator to "viewer" the moment they were taken
        # off stage — losing their roster grouping and, now, their publish grant.
        current = await bus.presence_get(ctx.event_id, identity)
        if (current.get("role") or "viewer") == "viewer" and on:
            patch["role"] = "speaker"
        # Staging grants camera + microphone, never screen share: see broadcast.allowed_sources.
        enforced = await livekit.set_publish_sources(
            ctx.room, identity, _sources_for({**current, **patch}))
        text = "{name} was " + ("invited to the stage" if on else "removed from the stage")
    elif op == "role":
        role = payload.get("role") if payload.get("role") in ("host", "speaker", "moderator", "viewer") else "viewer"
        patch = {"role": role}
        text = "{name} is now " + role
    elif op == "ban":
        patch = {"banned": True}
        enforced = await livekit.remove_participant(ctx.room, identity)
        text = "{name} was banned from the event"
    else:  # remove
        enforced = await livekit.remove_participant(ctx.room, identity)
        text = "{name} was removed from the event"

    if op in ("remove", "ban"):
        rec = await bus.presence_remove(ctx.event_id, identity) or {"identity": identity}
        if op == "ban":
            await bus.ban(ctx.event_id, identity)  # outlives presence, so a rejoin is refused
    else:
        rec = await bus.presence_upsert(ctx.event_id, identity, patch)

    name = rec.get("name") or identity
    sentence = text.format(name=name)
    act = await tx(lambda db: record(
        db, ctx, "role" if op == "role" else "mod", sentence, audit=f"live.participant.{op}",
        target_type="participant", target_id=identity,
        meta={"enforced_in_livekit": enforced, **patch},
    ))
    kind = "participant.leave" if op == "remove" else "participant.update"
    return [("participants", kind, rec), ("activity", "activity.new", act),
            ("moderator", "action.result", {"op": op, "identity": identity, "enforced": enforced})]


async def _participant_notify(ctx, payload):
    """Send ONE participant a private notice — "we'll come to you after this section", the
    answer to a raised hand.

    Delivery is a normal bus envelope carrying `to_identity`; routers/live.py drops it on every
    socket whose identity doesn't match. Directing it in the writer rather than adding a
    per-user transport keeps one bus, one auth check and one audit path — and the check runs
    before the attendee projection, so other moderators don't see private replies either.
    """
    identity = str(payload.get("identity") or "")
    text = _text(payload, limit=500)
    if not identity or not text:
        return []
    rec = await bus.presence_get(ctx.event_id, identity)
    if not rec:
        return "That person is no longer connected"
    name = rec.get("name") or identity
    act = await tx(lambda db: record(
        db, ctx, "mod", f"Replied privately to {name}", audit="live.participant.notify",
        target_type="participant", target_id=identity, meta={"text": text}))
    return [("participants", "participant.notice",
             {"to_identity": identity, "text": text, "from_name": ctx.name}),
            ("activity", "activity.new", act)]


# dispatcher ------------------------------------------------------------------

ACTIONS: dict[str, callable] = {
    "chat.send": _chat_send,
    "chat.typing": _chat_typing,
    "chat.react": _chat_react,
    "chat.approve": lambda c, p: _chat_moderate(c, p, "approve"),
    "chat.pin": lambda c, p: _chat_moderate(c, p, "pin"),
    "chat.delete": lambda c, p: _chat_moderate(c, p, "delete"),
    "chat.note": lambda c, p: _chat_moderate(c, p, "note"),
    "chat.highlight": lambda c, p: _chat_moderate(c, p, "highlight"),
    "chat.bulk": _chat_bulk,
    "chat.mute": _chat_mute,
    "chat.report": _chat_report,
    "qa.ask": _qa_ask,
    "qa.vote": _qa_vote,
    "qa.approve": lambda c, p: _qa_moderate(c, p, "approve"),
    "qa.answer": lambda c, p: _qa_moderate(c, p, "answer"),
    "qa.dismiss": lambda c, p: _qa_moderate(c, p, "dismiss"),
    "qa.pin": lambda c, p: _qa_moderate(c, p, "pin"),
    "qa.assign": lambda c, p: _qa_moderate(c, p, "assign"),
    "qa.merge": lambda c, p: _qa_moderate(c, p, "merge"),
    "qa.delete": lambda c, p: _qa_moderate(c, p, "delete"),
    # Speaker-tier (registered into SPEAKER_ACTIONS by services/speaker.py): both check that the
    # question is assigned to the caller, so can_speak alone is not enough.
    "qa.respond": _qa_respond,
    "qa.escalate": _qa_escalate,
    "poll.create": _poll_create,
    "poll.update": _poll_update,
    "poll.launch": lambda c, p: _poll_lifecycle(c, p, "launch"),
    "poll.close": lambda c, p: _poll_lifecycle(c, p, "close"),
    "poll.delete": lambda c, p: _poll_lifecycle(c, p, "delete"),
    "poll.vote": _poll_vote,
    "announce.send": _announce_send,
    "announce.delete": _announce_delete,
    "participant.hand": _participant_hand,
    "participant.state": _participant_state,
    "participant.notify": _participant_notify,
    "participant.dismiss_hand": lambda c, p: _participant_action(c, p, "hand"),
    "participant.mute": lambda c, p: _participant_action(c, p, "mute"),
    "participant.timeout": lambda c, p: _participant_action(c, p, "timeout"),
    "participant.stage": lambda c, p: _participant_action(c, p, "stage"),
    "participant.role": lambda c, p: _participant_action(c, p, "role"),
    "participant.ban": lambda c, p: _participant_action(c, p, "ban"),
    "participant.remove": lambda c, p: _participant_action(c, p, "remove"),
}

# Broadcast-control actions, filled in by services/broadcast.py at import (which is
# imported by routers/live.py). They live in the same registry so the host and moderator
# consoles share ONE socket, one permission gate and one audit path — but they are gated
# on can_host, so a moderator cannot end the stream or stop the recording.
HOST_ONLY: set[str] = set()

# Actions an assigned SPEAKER may perform, filled in by services/speaker.py at import. Same
# registry again, so the fourth console does not bring a fourth permission model. Moderators and
# hosts can do all of these too (they are speakers by resolve_ctx), which is what lets a host
# present their own slides.
SPEAKER_ACTIONS: set[str] = set()


async def dispatch(ctx: Ctx, action: str, payload: dict) -> str | None:
    """Run an action and broadcast its envelopes. Returns an error string for the caller
    to send back on its own socket, or None on success.

    Four tiers, most privileged first — the order matters, because an action in two sets must be
    judged by the STRICTEST one:

        HOST_ONLY       -> can_host      (go live, end, record)
        SPEAKER_ACTIONS -> can_speak     (present, whiteboard, answer my questions)
        VIEWER_ACTIONS  -> anybody       (chat, ask, vote, raise hand, report)
        everything else -> can_moderate

    A handler may also RETURN a string to reject the action (chat controls do this), which
    reaches the sender as an error frame without touching anybody else's console."""
    handler = ACTIONS.get(action)
    if handler is None:
        return f"Unknown action: {action}"
    if action in HOST_ONLY:
        if not ctx.can_host:
            return "Only the event host can control the broadcast"
    elif action in SPEAKER_ACTIONS:
        # can_moderate is included so a moderator can drive the presentation for a speaker who
        # is having trouble — the common live save, and they are already trusted with more.
        if not (ctx.can_speak or ctx.can_moderate):
            return "Only a speaker on this event can do that"
    elif action not in VIEWER_ACTIONS and not ctx.can_moderate:
        return "You are not a moderator of this event"

    result = await handler(ctx, payload)
    if isinstance(result, str):
        return result
    for channel, type_, data in result or []:
        await bus.publish(ctx.event_id, channel, type_, data)
    return None


# ── scheduler (scheduled polls / announcements, poll countdowns) ──────────────

def _due(db) -> list[tuple[str, str, dict]]:
    """Flip everything whose time has come. One query set, one pass — runs on a single
    ticker, so a scheduled item fires once even with several workers only if ONE worker
    runs the ticker.
    ponytail: single-ticker assumption. Run the app with one scheduler (or move this to a
    Redis lock / cron worker) before scaling out, or a scheduled poll double-fires."""
    now = datetime.now(timezone.utc)
    out = []
    for p in db.scalars(select(LivePoll).where(LivePoll.status == "scheduled",
                                               LivePoll.scheduled_at <= now)).all():
        p.status, p.launched_at = "live", now
        out.append((str(p.event_id), "poll.update", poll_out(p)))
    for p in db.scalars(select(LivePoll).where(LivePoll.status == "live",
                                               LivePoll.closes_at.isnot(None),
                                               LivePoll.closes_at <= now)).all():
        p.status, p.closed_at, p.closes_at = "closed", now, None
        out.append((str(p.event_id), "poll.update", poll_out(p)))
    for a in db.scalars(select(LiveAnnouncement).where(LiveAnnouncement.sent_at.is_(None),
                                                       LiveAnnouncement.scheduled_at.isnot(None),
                                                       LiveAnnouncement.scheduled_at <= now)).all():
        a.sent_at = now
        a.delivered_to = bus.local_subscribers(str(a.event_id))
        out.append((str(a.event_id), "announcement.new", announcement_out(a)))
    return out


async def run_scheduler(interval: float = 5.0) -> None:
    """Background ticker started from the app lifespan."""
    channel_of = {"poll.update": "poll", "announcement.new": "announcement"}
    while True:
        await asyncio.sleep(interval)
        try:
            for event_id, type_, data in await tx(_due):
                await bus.publish(event_id, channel_of[type_], type_, data)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the ticker
            import logging

            logging.getLogger(__name__).exception("live scheduler tick failed")
