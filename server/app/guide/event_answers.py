"""Questions about the event being watched (Conversation Flow §16, §21.1; PRD PR-020/PR-023).

On a viewer page Guide may hold a signed set of PUBLIC event facts (event_context.py): the
event's public name, start and end time and zone, status, host, description, which
interactive features are on, caption facts and replay state — what the watch page already
shows a viewer — nothing else. Questions those facts answer are answered deterministically from them,
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
                  r"webinar|meeting|call|conference)\b")

# A named zone a viewer may ask for ("in Indian time", "in EST"). The event context carries its
# own zone and UTC; any other IANA zone converts from the same instant, so nothing new is exposed.
_NAMED_ZONES = [
    (r"\b(ist|indian?(\s+standard)?\s+time|india\s+time)\b", "Asia/Kolkata"),
    (r"\b(est|edt|eastern(\s+time)?|us\s+eastern)\b", "America/New_York"),
    (r"\b(cst|cdt|central\s+time|us\s+central)\b", "America/Chicago"),
    (r"\b(mst|mdt|mountain\s+time)\b", "America/Denver"),
    (r"\b(pst|pdt|pacific(\s+time)?|us\s+pacific)\b", "America/Los_Angeles"),
    (r"\b(bst|london\s+time|uk\s+time)\b", "Europe/London"),
    (r"\b(cet|cest|central\s+european(\s+time)?)\b", "Europe/Paris"),
    (r"\b(jst|japan(ese)?\s+time)\b", "Asia/Tokyo"),
    (r"\b(sgt|singapore\s+time)\b", "Asia/Singapore"),
    (r"\b(gst|dubai\s+time|uae\s+time)\b", "Asia/Dubai"),
    (r"\b(aest|aedt|sydney\s+time|australia(n)?\s+eastern)\b", "Australia/Sydney"),
]
_CITY_ALIASES = {"mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata",
                 "bangalore": "Asia/Kolkata", "bengaluru": "Asia/Kolkata",
                 "hyderabad": "Asia/Kolkata", "chennai": "Asia/Kolkata", "india": "Asia/Kolkata",
                 "new york": "America/New_York", "san francisco": "America/Los_Angeles",
                 "los angeles": "America/Los_Angeles", "hong kong": "Asia/Hong_Kong",
                 "beijing": "Asia/Shanghai", "uk": "Europe/London", "berlin": "Europe/Berlin"}


def _city_zones() -> dict[str, str]:
    """"tokyo" -> "Asia/Tokyo" for every IANA city, plus the aliases people actually type."""
    from zoneinfo import available_timezones
    cities = {}
    for name in sorted(available_timezones()):
        if "/" in name and not name.startswith(("Etc/", "SystemV/", "US/")):
            city = name.rsplit("/", 1)[1].replace("_", " ").lower()
            if len(city) >= 4:
                cities.setdefault(city, name)
    return {**cities, **_CITY_ALIASES}


_CITIES = _city_zones()
_CITY_WORDS = "|".join(sorted(map(re.escape, _CITIES), key=len, reverse=True))
# "in Tokyo", "for London", "Paris time" — a city only counts next to a time/zone cue so a
# question about where the event is held is not read as a time-zone request.
_CITY_ASK = (rf"\b(time|start\w*|end\w*|begin\w*|when|schedule\w*)\b.{{0,40}}\b(in|for|at)\s+(the\s+)?"
             rf"({_CITY_WORDS})\b|\b({_CITY_WORDS})\s+(local\s+)?time\b")
_ZONE_WORDS = "|".join([rx for rx, _ in _NAMED_ZONES] + [_CITY_ASK])


def _asked_zone(text: str) -> str | None:
    if re.search(r"\b(utc|gmt|zulu)\b", text, re.I):
        return "UTC"
    for rx, name in _NAMED_ZONES:
        if re.search(rx, text, re.I):
            return name
    m = re.search(_CITY_ASK, text, re.I)
    if m:
        city = next((g for g in reversed(m.groups()) if g and g.lower() in _CITIES), None)
        return _CITIES.get((city or "").lower())
    return None


# Facts public Guide may state (answered from the context). Order matters: the first match
# wins, so the narrower questions (time left, end, duration) come before "start" and "about".
_ANSWERABLE = [
    ("zone", _rx(r"\b(utc|gmt|zulu)\b", r"\btime\s*zones?\b", r"\bwhich\s+zone\b", _ZONE_WORDS)),
    ("both", _rx(r"\bstart\w*\b.{0,40}\b(end|ends|ending|finish)\w*\b",
                 r"\b(end|ends|ending|finish)\w*\b.{0,40}\bstart\w*\b")),
    ("left", _rx(r"\b(time|how\s+much\s+(time|longer))\s+(is\s+)?(left|remaining)\b",
                 r"\bhow\s+much\s+longer\b", r"\bhow\s+long\s+(is\s+)?(left|remaining|more)\b",
                 r"\btime\s+remaining\b")),
    ("end", _rx(r"\b(what|which)\s+time\b.{0,40}\b(end|finish|over|close|wrap)",
                r"\bwhen\b.{0,30}\b(end|ends|ending|finish|finishes|over|wrap|wraps|close|closes)\b",
                r"\bend(ing)?\s+(time|date)\b", r"\bfinish(ing)?\s+time\b")),
    ("duration", _rx(r"\bdur[a-z]*\b", r"\bhow\s+long\b", r"\blength\s+of\b",
                     r"\bhow\s+many\s+(hours|minutes)\b")),
    ("start", _rx(r"\bwhen\s+(does|will|is|did|was)\b.{0,30}\b(start|begin|began|live|happen)",
                  r"\bstart(ing|ed)?\s+(time|date)\b",
                  r"\b(what|which)\s+time\b(?!.{0,40}\b(end|finish|over|close|wrap))",
                  r"\bwhen\s+is\s+(it|the\s+event|this\s+event)\b", r"\bwhat\s+date\b")),
    ("status", _rx(r"\bis\s+(it|this|(the|this)\s+(event|meeting|session|stream))\s+(live|on|over|started|"
                   r"finished|ended|cancel+ed)\b", r"\bhas\s+(it|the\s+event|this\s+event)\s+"
                   r"(started|ended|begun|finished)\b", r"\b(is\s+it\s+)?live\s+yet\b",
                   r"\bevent\s+status\b", r"\b(cancel+ed|postponed|rescheduled)\b")),
    ("replay", _rx(r"\breplay\b", r"\bwatch\s+(it\s+)?later\b")),
    ("captions", _rx(r"\bcaptions?\b", r"\bsubtitles?\b")),
    ("host", _rx(r"\bwho\b.{0,20}\b(host|hosting|hosted|organi[sz]|running|presenting)",
                 r"\bhost(ed)?\s+(name|by)\b", r"\bwho\s+is\s+the\s+host\b")),
    ("features", _rx(r"\b(chat|q\s*&\s*a|qna|polls?|questions?)\b.{0,25}\b(on|enabled|available|allowed|"
                     r"open|turned)\b", r"\b(is|are|does|do|can|will)\b.{0,30}\b(chat|q\s*&\s*a|qna|polls?)\b",
                     r"\bfeatures?\b")),
    ("summary", _rx(r"\bdescri(be|ption)\b", r"\bagenda\b", r"\btopics?\b", r"\bsummary\b",
                    r"\boverview\b")),
    ("category", _rx(r"\bcategory\b", r"\bwhat\s+(type|kind)\b")),
    ("about", _rx(r"\b(what|tell\s+me)\b.{0,20}\babout\b", r"\bwhat\s+is\s+(this|the)\s+(event|meeting|session)\b",
                  r"\b(event|this)\s+(details|info|information)\b", r"\bwhat\s+event\b")),
]
# Details even the event context does not hold (speaker lineup, who is on stage): always the
# gap reply on a viewer page.
_PRIVATE_DETAIL = _rx(
    r"\bspeakers?\b", r"\bwho\b.{0,20}\b(speak|talking)",
    r"\b(host|speaker|presenter)\b.{0,30}\b(speaking|talking|on\s+stage|present)\b",
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


def _status_note(c, text: str) -> str:
    """"Is it cancelled?" gets a direct yes/no before the status line."""
    for word, state in (("cancel", "cancelled"), ("postpone", "postponed")):
        if re.search(word, text, re.I) and c.get("public_status"):
            return (f"Yes. " if c["public_status"] == state else f"No, {_name(c)} is not {state}. ")
    return ""


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


def _dt(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


def _span(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts)


def _end(c):
    end = when(c.get("public_end_at"), c.get("public_timezone"))
    if not end:
        return None
    ended = c.get("public_status") in ("ended", "replay_available")
    return f"{_name(c)} {'ended at' if ended else 'is scheduled to end at'} {end}."


def _duration(c):
    start, end = _dt(c.get("public_start_at")), _dt(c.get("public_end_at"))
    if not start or not end or end <= start:
        return None
    return f"{_name(c)} is scheduled to run for {_span((end - start).total_seconds())}."


def _left(c):
    end = _dt(c.get("public_end_at"))
    if c.get("public_status") in ("ended", "replay_available"):
        return f"{_name(c)} has ended."
    start = _dt(c.get("public_start_at"))
    if c.get("public_status") in ("scheduled", "starting_soon") and start is not None:
        until = (start - datetime.now(start.tzinfo)).total_seconds()
        if until > 0:
            return (f"{_name(c)} hasn't started yet. It begins in about {_span(until)}, at "
                    f"{when(c.get('public_start_at'), c.get('public_timezone'))}.")
    if c.get("public_status") != "live" or end is None:
        return _end(c)
    remaining = (end - datetime.now(end.tzinfo)).total_seconds()
    if remaining <= 0:
        return (f"{_name(c)} has passed its scheduled end time of "
                f"{when(c.get('public_end_at'), c.get('public_timezone'))}.")
    return f"About {_span(remaining)} remain until {_name(c)}'s scheduled end."


def _times(c, text, zone):
    """Start and/or end as asked, in `zone` (the event's own zone when None)."""
    asks_end = re.search(r"\b(end|ends|ending|finish)\w*\b", text, re.I)
    asks_start = re.search(r"\bstart\w*\b", text, re.I)
    both = not asks_end and not asks_start or (asks_end and asks_start)
    start = when(c.get("public_start_at"), zone or c.get("public_timezone"))
    end = when(c.get("public_end_at"), zone or c.get("public_timezone"))
    parts = []
    if start and (both or asks_start):
        parts.append(f"{_name(c)} starts at {start}")
    if end and (both or asks_end):
        parts.append(f"{'ends' if parts else _name(c) + ' ends'} at {end}")
    return (" and ".join(parts) + ".") if parts else None


def _zone(c, text=""):
    zone = _asked_zone(text)
    if zone:
        return _times(c, text, zone)
    tz = c.get("public_timezone")
    return f"{_name(c)}'s published time zone is {tz}." if tz else None


def _both(c, text=""):
    return _times(c, text, None)


def _host(c):
    host = c.get("host_display_name")
    return f"{_name(c)} is hosted by {host}." if host else None


_FEATURES = (("chat_enabled", "Chat", r"\bchat\b"),
             ("qa_enabled", "Q&A", r"\b(q\s*&\s*a|qna|questions?)\b"),
             ("polls_enabled", "Polls", r"\bpolls?\b"))


def _features(c, text=""):
    asked = [f for f in _FEATURES if re.search(f[2], text, re.I)] or list(_FEATURES)
    known = [(label, c[key]) for key, label, _ in asked if key in c]
    if not known:
        return None
    return " ".join(f"{label} is {'on' if on else 'off'} for this event." for label, on in known)


def _summary(c):
    summary = c.get("event_summary")
    if not summary:
        return None
    return f"About {_name(c)}: {summary}" + ("" if summary[-1] in ".!?" else ".")


def _category(c):
    cat = c.get("event_category")
    return f"{_name(c)} is listed under the category: {cat}." if cat else None


def _about(c):
    parts = [p for p in (_summary(c), _status(c), _start(c), _host(c)) if p]
    return " ".join(parts) or None


_ANSWER = {"start": _start, "status": _status, "replay": _replay, "captions": _captions,
           "about": _about, "end": _end, "duration": _duration, "left": _left, "host": _host,
           "summary": _summary, "category": _category}
_DETAIL_KINDS = {"end", "duration", "left", "host", "features", "summary", "category"}
# Kinds where a missing fact means "the event details don't say", not "ask another flow".
_GAP_WHEN_MISSING = {"zone", "both", "end", "duration", "left", "host", "features", "summary", "category",
                     "about", "start"}


def answer(text: str, claims: dict | None) -> EventAnswer | None:
    """The reply to a question about the event being watched, or None for normal routing."""
    if _PRODUCT.search(text) or _PROBLEM.search(text):
        return None
    if claims:
        for kind, pattern in _ANSWERABLE:
            if pattern.search(text):
                reply = (_features(claims, text) if kind == "features" else
                         _zone(claims, text) if kind == "zone" else
                         _both(claims, text) if kind == "both" else _ANSWER[kind](claims))
                if reply and kind == "status":
                    reply = _status_note(claims, text) + reply
                if reply:
                    return EventAnswer(reply, True)
                return EventAnswer(GAP, False) if kind in _GAP_WHEN_MISSING else None
    # Details public Guide never holds: the gap reply on a viewer page (claims present) or
    # whenever the question is explicitly about "this event".
    if _PRIVATE_DETAIL.search(text) and (claims or _THIS_EVENT.search(text)):
        return EventAnswer(GAP, False)
    # No event context at all, but the question is explicitly about "this event" and asks for a
    # detail only that context could hold: the gap reply, never a guess from product docs.
    if not claims and _THIS_EVENT.search(text) and any(
            p.search(text) for kind, p in _ANSWERABLE if kind in _DETAIL_KINDS):
        return EventAnswer(GAP, False)
    return None
