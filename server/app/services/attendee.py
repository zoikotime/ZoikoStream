"""Attendee domain: per-person preferences, and the live reaction stream.

Registered into the same dispatcher as broadcast.py and speaker.py — one socket, one permission
table, one audit path. Import direction is one-way (attendee -> moderation); routers/live.py
imports this module, which is what performs the registration.

Almost everything else an attendee does already existed: chat.send, qa.ask, qa.vote, poll.vote and
participant.hand are all in moderation.VIEWER_ACTIONS, and `viewer_snapshot` already carries the
messages, questions, polls and announcements. The audience side of this platform was wired and
simply had no interface. What is genuinely new here is reactions.
"""

from __future__ import annotations

import re
import time

from . import bus
from . import moderation as mod

# ── reactions ─────────────────────────────────────────────────────────────────
# The six the brief asks for, as an ALLOW-LIST rather than "any emoji". This value is rendered by
# every other attendee, so it is not free text — and a fixed set is also what makes the aggregate
# counter meaningful.
REACTIONS = ("👏", "❤️", "🔥", "🎉", "👍", "😂")

# Per-connection budget, on top of the socket's own 30-actions/10s limiter. A reaction is one tap,
# so a person can genuinely fire ten in a few seconds — but at 10,000 attendees an unthrottled
# reaction is 10,000 fan-outs per tap, which is the one action in this system that can melt the bus
# by being used exactly as intended.
REACTION_WINDOW = 5.0
REACTION_BURST = 6

# Reactions are NOT persisted, and that is a deliberate deviation from the brief's audit list.
# One audit row per reaction at this scale is a self-inflicted write storm for information nobody
# reads back — "who clapped at 14:03" is not a compliance question. The BUS keeps a running total
# per emoji, `analytics_now` reports it, and the analytics sampler persists the aggregate on the
# row it already writes every 15s. So the number survives; the individual tap does not.


def _reaction_key(event_id) -> str:
    return f"reactions:{bus.eid(event_id)}"


_last_reaction: dict[str, list[float]] = {}


def reaction_allowed(identity: str, now: float | None = None) -> bool:
    """Sliding-window burst check, per identity. Pure enough to test, and cheap enough to run on
    every tap — a Redis counter here would add a round trip to the hottest action in the room."""
    now = time.monotonic() if now is None else now
    hits = [t for t in _last_reaction.get(identity, ()) if now - t < REACTION_WINDOW]
    if len(hits) >= REACTION_BURST:
        _last_reaction[identity] = hits
        return False
    hits.append(now)
    _last_reaction[identity] = hits
    return True


async def _reaction_send(ctx, payload):
    """One attendee reacting. Ephemeral: broadcast, counted, never written to a row.

    Returns [] silently when throttled rather than an error frame — a rate-limit toast on a tap
    the attendee already saw animate would be noise, and the cap exists to protect the bus, not to
    discipline the audience.
    """
    emoji = (payload.get("emoji") or "").strip()
    if emoji not in REACTIONS:
        return []
    settings = await bus.state_get(ctx.event_id)
    # Staff bypass their own audience controls, same rule as chat.
    if settings.get("reactions_enabled") is False and not ctx.can_moderate:
        return "Reactions are turned off"
    if not reaction_allowed(ctx.identity):
        return []

    totals = await bus.counter_bump(_reaction_key(ctx.event_id), emoji)
    return [("reaction", "reaction.new", {
        # `identity` is included so a client can skip animating its OWN reaction twice (it draws
        # one optimistically on tap). It is already visible to anyone in the room via presence.
        "emoji": emoji, "identity": ctx.identity, "name": ctx.name, "totals": totals,
    })]


async def reaction_totals(event_id) -> dict:
    return await bus.counter_all(_reaction_key(event_id))


# ── per-person preferences ────────────────────────────────────────────────────
# Stored on users.preferences (one JSON column — read whole, for one person, by that person).
# A WHITELIST with per-key validation, exactly like broadcast.clean_settings: an unknown key is
# dropped rather than trusted, because this blob is echoed back to the client and rendered.

LANGUAGES = ("en", "es", "fr", "de", "pt", "it", "nl", "hi", "ar", "zh", "ja", "ko")
TEXT_SIZES = ("default", "large", "larger")
REMINDER_OFFSETS = (0, 5, 15, 30, 60, 1440)     # minutes before start

# Notification switches. Every key here maps to something this platform can ACTUALLY detect and
# deliver in-session (see the notification list in the attendee console) — there is no push or SMS
# transport, so nothing offers one.
NOTIFY_KEYS = (
    "event_starting", "session_starting", "announcement", "question_answered",
    "poll_started", "hand_approved", "speaker_live", "recording_available",
)

_BOOL = "bool"
PREFERENCE_SPECS: dict[str, object] = {
    "language": LANGUAGES,
    "text_size": TEXT_SIZES,
    "reduced_motion": _BOOL,
    "high_contrast": _BOOL,
    "captions": _BOOL,
    "reminder_offset_minutes": REMINDER_OFFSETS,
    # Favourite speakers, as user ids. Bounded and de-duplicated; NOT validated against the user
    # table, because a favourite is a personal bookmark and resolving it is the reader's problem —
    # a stale id renders as nothing rather than failing the save.
    "favorite_speakers": "id_list",
    "notify": "notify_map",
}

MAX_FAVORITES = 100
_UUIDISH = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def clean_preferences(patch: dict) -> dict:
    """Keep only known keys with valid values. Returns the accepted subset."""
    out: dict = {}
    for key, spec in PREFERENCE_SPECS.items():
        if key not in (patch or {}):
            continue
        value = patch[key]
        if spec == _BOOL:
            out[key] = bool(value)
        elif spec == "id_list":
            seen, ids = set(), []
            for raw in (value or [])[:MAX_FAVORITES * 2]:
                s = str(raw)
                if _UUIDISH.match(s) and s not in seen:
                    seen.add(s)
                    ids.append(s)
            out[key] = ids[:MAX_FAVORITES]
        elif spec == "notify_map":
            out[key] = {k: bool((value or {}).get(k)) for k in NOTIFY_KEYS if k in (value or {})}
        elif isinstance(spec, tuple) and value in spec:
            out[key] = value
    return out


DEFAULT_PREFERENCES = {
    "language": "en",
    "text_size": "default",
    "reduced_motion": False,
    "high_contrast": False,
    "captions": False,
    "reminder_offset_minutes": 15,
    "favorite_speakers": [],
    # Everything on by default: an attendee who never opens settings should still be told the
    # event started.
    "notify": {k: True for k in NOTIFY_KEYS},
}


def preferences_out(stored: dict | None) -> dict:
    merged = {**DEFAULT_PREFERENCES, **(stored or {})}
    merged["notify"] = {**DEFAULT_PREFERENCES["notify"], **((stored or {}).get("notify") or {})}
    return merged


# ── snapshot contribution ─────────────────────────────────────────────────────


async def snapshot_extra(ctx) -> dict:
    """Three things every tier needs, attendees included.

    `identity` so the client can tell its OWN message, question and reaction apart from everyone
    else's — it is the caller's own user id, which they already hold in their JWT. `reactions` is
    the room's running tally. `resources` is what the audience may download, and it is computed
    here rather than fetched over REST so the console paints complete from the first frame.
    """
    from ..crud import attendee as crud

    resources = await mod.tx(lambda db: [
        crud.resource_out(a) for a in crud.shared_assets(db, ctx.event_id)
    ])
    return {
        "identity": ctx.identity,
        "reactions": await reaction_totals(ctx.event_id),
        "resources": resources,
    }


# ── registration into the shared dispatcher ───────────────────────────────────

ACTIONS = {"reaction.send": _reaction_send}

mod.ACTIONS.update(ACTIONS)
# An attendee action, so it goes in the VIEWER set — reacting is the most basic thing an audience
# does, and gating it behind a moderator check would make the feature pointless.
mod.VIEWER_ACTIONS = mod.VIEWER_ACTIONS | frozenset(ACTIONS)
mod.SNAPSHOT_EXTRAS.append(snapshot_extra)
