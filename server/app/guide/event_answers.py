"""Questions about the event being watched (Conversation Flow §16, §21.1; PRD PR-020/PR-023).

On a viewer page Guide may hold a signed set of PUBLIC event facts (event_context.py): the
event's public name, public start time and zone, public status, caption facts and replay
state — nothing else. Questions those facts answer are answered deterministically from them,
citing the event context; nothing is generated, so nothing can be invented.

Every other question about the event — how long it runs, when it ends, who is hosting or
speaking, what it covers, which features are on — asks for facts public Guide does not hold,
and gets the spec's exact gap reply (CF §21.1) pointing to the invitation or organizer.

Product questions ("how long can a ZoikoStream event be?") and viewer problems ("captions
aren't working") are left to the normal routing and the deterministic support flows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SOURCE = {"source_id": "event-context", "title": "This event's public details",
          "source_type": "event", "version": None, "updated_at": None, "canonical_path": None}

# CF §21.1 / CF-AT-24 exact copy, plus where the viewer can find the detail.
GAP = ("I don't have public event information that confirms that. The event invitation or "
       "the organizer has those details.")


@dataclass(frozen=True)
class EventAnswer:
    text: str
    grounded: bool          # True: stated from the verified event context (cite SOURCE)


def _rx(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts), re.I)


# Not about this event, or a problem report: never answered here.
_PRODUCT = _rx(r"\bzoiko\s*stream\b", r"\bplatform\b", r"\bpric", r"\bplans?\b", r"\bdemo\b")
_PROBLEM = _rx(r"\bnot\s+working\b", r"\b(isn'?t|doesn'?t|won'?t|can'?t|cannot)\b", r"\bbroken\b",
               r"\berror\b", r"\bstuck\b", r"\bbuffer", r"\bfrozen\b", r"\bfreez", r"\blag")
# An explicit reference to the event being watched.
_THIS_EVENT = _rx(r"\b(this|the|today'?s|tonight'?s)\s+(event|stream|session|broadcast|show|"
                  r"webinar)\b")

# Facts public Guide may state (answered from the context).
_ANSWERABLE = [
    ("start", _rx(r"\bwhen\s+(does|will|is|did)\b.{0,30}\b(start|begin|live|happen)",
                  r"\bstart(ing)?\s+time\b", r"\bwhat\s+time\b(?!.{0,30}\b(end|finish))",
                  r"\bwhen\s+is\s+(it|the\s+event|this\s+event)\b", r"\bwhat\s+date\b")),
    ("status", _rx(r"\bis\s+(it|this|the\s+event|this\s+event)\s+(live|on|over|started|"
                   r"finished|ended|cancel+ed)\b", r"\bhas\s+(it|the\s+event|this\s+event)\s+"
                   r"(started|ended|begun|finished)\b", r"\b(is\s+it\s+)?live\s+yet\b",
                   r"\bevent\s+status\b")),
    ("replay", _rx(r"\breplay\b", r"\bwatch\s+(it\s+)?later\b")),
    ("captions", _rx(r"\bcaptions?\b", r"\bsubtitles?\b")),
    ("about", _rx(r"\b(what|tell\s+me)\b.{0,20}\babout\b", r"\bwhat\s+is\s+(this|the)\s+event\b",
                  r"\b(event|this)\s+(details|info|information)\b", r"\bwhat\s+event\b")),
]
# Event details public Guide does not hold (always the gap reply).
_PRIVATE_DETAIL = _rx(
    r"\bdur[a-z]*\b", r"\bhow\s+long\b", r"\blength\s+of\b", r"\bhow\s+many\s+(hours|minutes)\b",
    r"\b(time|how\s+much\s+(time|longer))\s+(is\s+)?(left|remaining)\b", r"\bhow\s+much\s+longer\b",
    r"\bwhen\s+(does|will|is)\b.{0,30}\b(end|finish|over|close|wrap)", r"\bend(ing)?\s+time\b",
    r"\bwho\b.{0,20}\b(host|hosting|hosted|organi[sz]|running|presenting|speak)",
    r"\b(host|speaker|presenter)\b.{0,30}\b(speaking|talking|on\s+stage|present)\b",
    r"\bagenda\b", r"\bdescri(be|ption)\b",
    r"\b(is|are|does|do)\b.{0,30}\b(chat|q\s*&\s*a|polls?)\b.{0,15}\b(on|enabled|available)\b",
)

_STATUS_TEXT = {"live": "is live now", "starting_soon": "is starting soon",
                "scheduled": "hasn't started yet", "ended": "has ended",
                "cancelled": "has been cancelled", "postponed": "has been postponed",
                "replay_available": "has ended, and a replay is available"}


def when(iso: str | None, tz_name: str | None) -> str | None:
    """American English, as the rest of Guide: "Monday, October 12 at 11:35 AM IST"."""
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    try:
        if tz_name:
            dt = dt.astimezone(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        tz_name = None
    hour = dt.strftime("%I").lstrip("0") or "12"
    zone = dt.strftime("%Z") if tz_name else "UTC"
    return f"{dt.strftime('%A, %B')} {dt.day} at {hour}:{dt.strftime('%M %p')} {zone}".strip()


def _name(c: dict) -> str:
    return c.get("event_display_name") or "This event"


def _start(c):
    start = when(c.get("public_start_at"), c.get("public_timezone"))
    return f"{_name(c)}'s published start time is {start}." if start else None


def _status(c):
    text = _STATUS_TEXT.get(c.get("public_status"))
    return f"{_name(c)} {text}." if text else None


def _replay(c):
    # CF §15: state only the public replay fact; "unknown"/"pending" go to the replay flow.
    state = c.get("replay_public_state")
    if state == "available":
        return "The public event information shows a replay is available for this event."
    if state == "unavailable":
        return "The public event context does not show replay availability for this event."
    return None


def _captions(c):
    # CF §14 CC-01: only the published caption facts; otherwise the captions flow.
    langs = c.get("caption_languages")
    if langs:
        return f"This event lists captions in: {', '.join(langs)}."
    if c.get("captions_available") is False:
        return "The public event information doesn't list captions for this event."
    return None


def _about(c):
    parts = [p for p in (_status(c), _start(c)) if p]
    return " ".join(parts) or None


_ANSWER = {"start": _start, "status": _status, "replay": _replay, "captions": _captions,
           "about": _about}


def answer(text: str, claims: dict | None) -> EventAnswer | None:
    """The reply to a question about the event being watched, or None for normal routing."""
    if _PRODUCT.search(text) or _PROBLEM.search(text):
        return None
    if claims:
        for kind, pattern in _ANSWERABLE:
            if pattern.search(text):
                reply = _ANSWER[kind](claims)
                return EventAnswer(reply, True) if reply else None
    # Details public Guide never holds: the gap reply on a viewer page (claims present) or
    # whenever the question is explicitly about "this event".
    if _PRIVATE_DETAIL.search(text) and (claims or _THIS_EVENT.search(text)):
        return EventAnswer(GAP, False)
    return None
