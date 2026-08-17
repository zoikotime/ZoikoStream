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

from sqlalchemy import delete, select

from ..crud.admin import create_audit_log
from ..db import SessionLocal
from ..models import (
    Event,
    EventAssignment,
    EventFeedback,
    EventRegistration,
    LiveActivity,
    LiveAnnouncement,
    LiveMessage,
    LivePoll,
    LivePollVote,
    LiveQuestion,
    LiveQuestionVote,
    User,
)
from . import bus, livekit

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

    @property
    def actor(self):
        """Minimal stand-in for the User row that create_audit_log needs (id + email)."""
        return SimpleNamespace(id=self.user_id, email=self.name)


def resolve_ctx(event_id: uuid.UUID, user: User) -> Ctx | None:
    """Load the event with org isolation and work out whether this user may moderate it.
    Returns None when the event isn't visible to the user's org -> socket is refused."""
    db = SessionLocal()
    try:
        stmt = select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
        if user.role != "super_admin":
            stmt = stmt.where(Event.org_id == user.org_id)
        ev = db.scalar(stmt)
        if ev is None:
            return None

        # Org admins and above moderate any event in their org. A moderator/host/speaker
        # must be ASSIGNED to this specific event — an org's moderator is not automatically
        # a moderator of every event in it.
        can = can_host = False
        if user.role in ("org_admin", "super_admin"):
            can = can_host = True
        elif user.role in ("moderator", "host"):
            roles = set(db.scalars(
                select(EventAssignment.role).where(
                    EventAssignment.event_id == ev.id,
                    EventAssignment.user_id == user.id,
                )
            ).all())
            can = bool(roles & {"moderator", "host"})
            # Only an assigned HOST gets broadcast control — being the org's host role is
            # not enough, and a moderator assignment never grants it.
            can_host = "host" in roles

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
        )
    finally:
        db.close()


def resolve_ctx_from_registration(event_id: uuid.UUID, registration: EventRegistration) -> Ctx | None:
    """The anonymous-viewer counterpart to resolve_ctx: a self-serve name+email
    registration (routers/events.py register_for_event) takes the place of a User login for
    chat/Q&A/polls, so a public visitor never has to sign in to say something.

    `user_id` borrows the registration's own id rather than staying None — LiveMessage.
    user_id and friends (models/live.py) are bare UUID columns with no FK to `users`, so this
    is safe, and it means per-author scoping (slow mode, duplicate detection in
    _chat_send) still works per guest instead of every anonymous visitor sharing one bucket."""
    db = SessionLocal()
    try:
        ev = db.scalar(select(Event).where(Event.id == event_id, Event.deleted_at.is_(None)))
        if ev is None:
            return None
        return Ctx(
            event_id=ev.id,
            org_id=ev.org_id,
            room=f"event_{ev.id}",
            user_id=registration.id,
            name=registration.name,
            identity=f"guest-{registration.id}",
            role="viewer",
            can_moderate=False,
            can_host=False,
        )
    finally:
        db.close()




def resolve_ctx_from_access_link(event_id: uuid.UUID, raw_token: str) -> Ctx | None:
    """Resolve a host-issued private-event access link into an anonymous viewer context.

    The token is validated against BOTH the requested event id and the stored hash, and
    revoked/expired links are rejected by ``find_access_link``.  This mirrors the HTTP
    ``/events/{id}/watch?link=...`` gate so the WebSocket cannot become a side door into a
    private event.

    ``user_id`` deliberately stays as the access-link row's id rather than using a shared
    anonymous identity: it keeps presence, slow-mode and moderation state isolated per
    shared-link viewer without creating a User account.
    """
    if not raw_token:
        return None
    from ..crud import event as event_crud

    db = SessionLocal()
    try:
        ev = db.scalar(select(Event).where(Event.id == event_id, Event.deleted_at.is_(None)))
        if ev is None or ev.visibility != "private":
            return None
        link = event_crud.find_access_link(db, ev.id, raw_token)
        if link is None:
            return None
        identity = f"guest-link-{link.id}"
        return Ctx(
            event_id=ev.id,
            org_id=ev.org_id,
            room=f"event_{ev.id}",
            user_id=link.id,
            name=link.label or "Viewer",
            identity=identity,
            role="viewer",
            can_moderate=False,
            can_host=False,
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
        "text": m.text, "status": m.status, "pinned": m.pinned,
        "flags": m.flags or [], "flagged": bool(m.flags), "reactions": m.reactions or {},
        "reply_to": str(m.reply_to) if m.reply_to else None, "note": m.note,
        "created_at": _iso(m.created_at),
    }


def question_out(q: LiveQuestion) -> dict:
    return {
        "id": str(q.id), "name": q.author_name, "text": q.text, "votes": q.votes,
        "status": q.status, "pinned": q.pinned, "assigned_name": q.assigned_name,
        "flags": q.flags or [], "created_at": _iso(q.created_at),
    }


def poll_out(p: LivePoll, your_vote: int | None = None) -> dict:
    """`your_vote` is the CALLER's own option index (or None if they haven't voted) —
    always per-connection, never broadcast-derived, since it would leak one viewer's
    ballot to every other viewer if it were. Callers that build a public broadcast
    (poll.new/update/delete) simply omit it and every viewer gets `your_vote: None`,
    which is correct for them. Only `_snapshot` (below), which runs once per socket
    with that socket's own Ctx, passes a real value — that's what lets a refreshed
    page know it already voted instead of showing the vote buttons again."""
    return {
        "id": str(p.id), "question": p.question, "options": p.options or [], "status": p.status,
        "scheduled_at": _iso(p.scheduled_at), "closes_at": _iso(p.closes_at),
        "launched_at": _iso(p.launched_at), "closed_at": _iso(p.closed_at),
        "votes": sum(o.get("votes", 0) for o in (p.options or [])),
        "your_vote": your_vote,
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

    # Newest-first, same as the old inline `reversed(recent(LivePoll, 50))` — kept as its
    # own list so the vote lookup below can reuse it instead of querying twice.
    polls = list(reversed(recent(LivePoll, 50)))
    # This viewer's own ballots, so a refreshed/reconnected page can show "you voted for
    # X" and disable the buttons instead of re-offering a vote the server will just no-op
    # (see _poll_vote's already_voted check) — without this the poll LOOKED like it reset.
    my_poll_votes: dict = {}
    if ctx.user_id and polls:
        rows = db.scalars(
            select(LivePollVote).where(
                LivePollVote.poll_id.in_([p.id for p in polls]),
                LivePollVote.user_id == ctx.user_id,
            )
        ).all()
        my_poll_votes = {r.poll_id: r.option for r in rows}

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
        "polls": [poll_out(p, my_poll_votes.get(p.id)) for p in polls],
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
    snap["reactions"] = _reaction_snapshot(await bus.reaction_all(ctx.event_id))
    for extra in SNAPSHOT_EXTRAS:
        snap.update(await extra(ctx))
    return snap


# ── actions ───────────────────────────────────────────────────────────────────
# Each handler is `async (ctx, payload) -> list[(channel, type, data)]`; the dispatcher
# publishes whatever comes back. Handlers that touch the DB wrap it in tx().

# Actions any authenticated attendee may perform. Everything else needs can_moderate.
VIEWER_ACTIONS = frozenset({
    "chat.send", "chat.typing", "chat.react", "qa.ask", "qa.vote", "poll.vote",
    "participant.hand", "participant.state", "reaction.add", "feedback.submit",
})

# The viewer reaction bar under the player (components/watch/ReactionBar.jsx) — a fixed,
# whole-event tap counter per emoji, distinct from chat.react above (which tags one chat
# message). Keys match the frontend's REACTIONS list 1:1 so no mapping layer is needed.
REACTION_KEYS = ("like", "heart", "clap", "fire", "party")


def _reaction_snapshot(counts: dict) -> dict:
    """Zero-fill every known key so the envelope is always the complete state (a viewer
    who has never seen a `fire` tap this session still needs to know it's 0, not missing)."""
    return {k: counts.get(k, 0) for k in REACTION_KEYS}


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

def chat_gate(settings: dict, ctx: Ctx, text: str) -> str | None:
    """Reason to reject this message outright, or None to allow it."""
    if ctx.can_moderate:
        return None
    if settings.get("chat_enabled") is False:
        return "Chat is turned off"
    if settings.get("emoji_only") and not _EMOJI_ONLY.match(text):
        return "Emoji-only mode is on"
    if settings.get("subscriber_only") and ctx.role == "viewer":
        return "Chat is limited to members right now"
    return None


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

    # Read from the bus, not the DB — this runs once per message.
    settings = await bus.state_get(ctx.event_id)
    blocked_reason = chat_gate(settings, ctx, text)
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
    """approve | pin | unpin | delete | note — one body, since they differ only in the
    field they set and the sentence they log."""

    def work(db):
        m = _row(db, LiveMessage, ctx, payload.get("id"))
        if not m:
            return None
        if op == "approve":
            m.status, m.flags = "approved", []
            text = f"Approved a message from {m.author_name}"
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
    """Upvote is a toggle (see WatchPanel.jsx's toggleVote), enforced server-side via a
    (question, voter) ledger row rather than trusting the client's local `voted` state —
    that state is only ever in memory, so a page refresh reset it to "not voted" with
    nothing stopping a repeat upvote from being counted again. `down` here means "remove
    my upvote", not "downvote"; it's a no-op if this voter never had one."""
    down = bool(payload.get("down"))

    def work(db):
        q = _row(db, LiveQuestion, ctx, payload.get("id"))
        if not q:
            return None
        existing = db.scalar(
            select(LiveQuestionVote).where(
                LiveQuestionVote.question_id == q.id, LiveQuestionVote.user_id == ctx.user_id,
            )
        )
        if down:
            if existing is not None:
                db.delete(existing)
                q.votes = max(0, q.votes - 1)
        elif existing is None:
            db.add(LiveQuestionVote(
                event_id=ctx.event_id, org_id=ctx.org_id, question_id=q.id, user_id=ctx.user_id,
            ))
            q.votes += 1
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
    data, act, deleted = out
    return [("qa", "question.delete" if deleted else "question.update", data),
            ("activity", "activity.new", act)]


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
            # LivePollVote rows FK onto live_polls.id with no ON DELETE CASCADE (see
            # models/live.py), so deleting a poll that already has votes would otherwise
            # hit a foreign-key violation and silently fail the whole action. Clear the
            # ledger first so a poll with votes can still be deleted.
            db.execute(delete(LivePollVote).where(LivePollVote.poll_id == p.id))
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
    """A (poll, voter) ledger row is how a page refresh knows this voter already has a
    ballot in (see poll_out's `your_vote`, filled from the snapshot's per-viewer lookup) —
    without it the option index alone gated nothing, and a refresh reset the client's
    local `voted` flag with nothing server-side remembering the vote.

    A voter CAN change their mind while the poll is still live: re-voting moves their
    existing ledger row to the new option (decrementing the old tally, incrementing the
    new one) instead of being rejected as a second vote. Once the poll closes the ledger
    row — and so the tally — is frozen, same as before."""
    index = payload.get("option")

    def work(db):
        p = _row(db, LivePoll, ctx, payload.get("id"))
        if not p or p.status != "live":
            return None
        options = [dict(o) for o in (p.options or [])]
        if not isinstance(index, int) or not 0 <= index < len(options):
            return None
        already_voted = db.scalar(
            select(LivePollVote).where(LivePollVote.poll_id == p.id, LivePollVote.user_id == ctx.user_id)
        )
        if already_voted is not None:
            if already_voted.option == index:
                return poll_out(p)  # no-op: re-picking the same option
            if 0 <= already_voted.option < len(options):
                options[already_voted.option]["votes"] = max(0, options[already_voted.option].get("votes", 0) - 1)
            already_voted.option = index
        else:
            db.add(LivePollVote(
                event_id=ctx.event_id, org_id=ctx.org_id, poll_id=p.id, user_id=ctx.user_id, option=index,
            ))
        options[index]["votes"] = options[index].get("votes", 0) + 1
        p.options = options  # reassign: JSON columns don't track in-place mutation
        # `your_vote` is intentionally omitted here (see poll_out's docstring) — this
        # dict is broadcast to EVERY viewer, and it must not leak this voter's ballot
        # to the rest of the room. The voter's own UI already updated optimistically
        # in WatchPanel.jsx's Poll before this round trip returned.
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

async def _participant_hand(ctx, payload):
    raised = bool(payload.get("raised", True))
    rec = await bus.presence_upsert(ctx.event_id, ctx.identity, {"hand": raised})
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
    if "speaking" in payload:
        patch["speaking"] = bool(payload["speaking"])
    if payload.get("quality") in _QUALITY:
        patch["quality"] = payload["quality"]
    if not patch:
        return []
    rec = await bus.presence_upsert(ctx.event_id, ctx.identity, patch)
    return [("participants", "participant.update", rec)]


# reactions ---------------------------------------------------------------------

async def _reaction_add(ctx, payload):
    """One tap on the viewer reaction bar. `key` is validated against the fixed set the
    frontend renders (never trust a wire value into a dict key that gets broadcast).
    The increment itself is bus.reaction_incr, which is atomic (Redis HINCRBY / a bare
    dict bump with no intervening await) — concurrent taps from different viewers never
    clobber each other the way a read-count/add-one/save-count round trip would."""
    key = payload.get("key")
    if key not in REACTION_KEYS:
        return "Unsupported reaction"

    # Same toggle the host console already exposes (data/host.js "Reactions"); chat_gate
    # reads the equivalent chat_enabled flag the same way, from the bus's hot settings
    # copy rather than a query per tap.
    settings = await bus.state_get(ctx.event_id)
    if settings.get("reactions_enabled") is False:
        return "Reactions are turned off for this event"

    counts = await bus.reaction_incr(ctx.event_id, key)
    return [("reactions", "reaction.update", {
        "event_id": str(ctx.event_id), "reactions": _reaction_snapshot(counts),
    })]


async def _participant_action(ctx, payload, op: str):
    identity = str(payload.get("identity") or "")
    if not identity:
        return []
    now = datetime.now(timezone.utc)
    patch: dict = {}
    enforced = True

    if op == "mute":
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
        patch = {"on_stage": on, "role": "speaker" if on else "viewer"}
        enforced = await livekit.set_stage(ctx.room, identity, on)
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


# feedback ----------------------------------------------------------------------

async def _feedback_submit(ctx, payload):
    """Sent once, by the modal shown when a viewer leaves the event — right before the
    socket disconnects, which is why this is a socket action rather than a REST call: the
    connection (and the ctx/identity it carries) is still open at that moment, and
    everything else the console does already goes through here.

    Feedback is a viewer-only signal: it's what the organization dashboard's event detail
    page averages to show how the audience felt about the event, not how the host felt
    running it. The host console no longer shows the modal at all (see
    pages/host/Dashboard.jsx), but this guard keeps a stray/legacy `feedback.submit` from
    a host or moderator connection from landing in that same average.

    Both fields are optional on their own (a submitter can rate without commenting, or
    comment without rating), but a submission with neither is a no-op, not an empty row —
    that's what lets the modal's "Skip" button just close without a network call."""

    if ctx.can_moderate:
        return []

    rating = payload.get("rating")
    try:
        rating = int(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None
    if rating is not None:
        rating = max(1, min(rating, 5))
    comment = _text(payload, "comment", 2000) or None
    if rating is None and not comment:
        return []

    role = "host" if ctx.can_host else "viewer"

    def work(db):
        db.add(EventFeedback(
            event_id=ctx.event_id, org_id=ctx.org_id, role=role,
            user_id=ctx.user_id, identity=ctx.identity, name=ctx.name,
            rating=rating, comment=comment,
        ))

    await tx(work)
    return []


# dispatcher ------------------------------------------------------------------

ACTIONS: dict[str, callable] = {
    "chat.send": _chat_send,
    "chat.typing": _chat_typing,
    "chat.react": _chat_react,
    "chat.approve": lambda c, p: _chat_moderate(c, p, "approve"),
    "chat.pin": lambda c, p: _chat_moderate(c, p, "pin"),
    "chat.delete": lambda c, p: _chat_moderate(c, p, "delete"),
    "chat.note": lambda c, p: _chat_moderate(c, p, "note"),
    "chat.bulk": _chat_bulk,
    "qa.ask": _qa_ask,
    "qa.vote": _qa_vote,
    "qa.approve": lambda c, p: _qa_moderate(c, p, "approve"),
    "qa.answer": lambda c, p: _qa_moderate(c, p, "answer"),
    "qa.dismiss": lambda c, p: _qa_moderate(c, p, "dismiss"),
    "qa.pin": lambda c, p: _qa_moderate(c, p, "pin"),
    "qa.assign": lambda c, p: _qa_moderate(c, p, "assign"),
    "qa.delete": lambda c, p: _qa_moderate(c, p, "delete"),
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
    "participant.mute": lambda c, p: _participant_action(c, p, "mute"),
    "participant.timeout": lambda c, p: _participant_action(c, p, "timeout"),
    "participant.stage": lambda c, p: _participant_action(c, p, "stage"),
    "participant.role": lambda c, p: _participant_action(c, p, "role"),
    "participant.ban": lambda c, p: _participant_action(c, p, "ban"),
    "participant.remove": lambda c, p: _participant_action(c, p, "remove"),
    "reaction.add": _reaction_add,
    "feedback.submit": _feedback_submit,
}

# Broadcast-control actions, filled in by services/broadcast.py at import (which is
# imported by routers/live.py). They live in the same registry so the host and moderator
# consoles share ONE socket, one permission gate and one audit path — but they are gated
# on can_host, so a moderator cannot end the stream or stop the recording.
HOST_ONLY: set[str] = set()


async def dispatch(ctx: Ctx, action: str, payload: dict) -> str | None:
    """Run an action and broadcast its envelopes. Returns an error string for the caller
    to send back on its own socket, or None on success.

    A handler may also RETURN a string to reject the action (chat controls do this), which
    reaches the sender as an error frame without touching anybody else's console."""
    handler = ACTIONS.get(action)
    if handler is None:
        return f"Unknown action: {action}"
    if action in HOST_ONLY:
        if not ctx.can_host:
            return "Only the event host can control the broadcast"
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
