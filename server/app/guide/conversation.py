"""Conversational turns: greetings, "who are you?", "what can you do?", thanks, goodbyes.

These are questions about GUIDE, not about ZoikoStream's product, so they are answered from
fixed, approved copy rather than retrieval: no source page describes the assistant, and a
model is not needed (or allowed) to invent one. The copy states plainly that Guide is an AI
(PR-036), what it can and cannot do, and never implies account or event access (PR-038).

A greeting at the start of a real question ("hi, how much does it cost?") is stripped and
the remaining question is routed normally — small talk never swallows a real request.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

_GREETING = re.compile(
    r"^\s*(hi+|hello+|hey+|hiya|howdy|yo|greetings|good\s+(morning|afternoon|evening|day)|"
    r"namaste|hola|hai)\b[\s,!.:;-]*(there|guide|team|zoikostream|everyone|all)?[\s,!.:;-]*",
    re.I)

# Whole-message patterns, checked on the text with any greeting removed. Every pattern must
# consume the WHOLE message (only trailing punctuation or a polite tail may follow), so
# "thanks, but how much does it cost?" or "later today can I watch the replay?" are routed as
# the real questions they are.
_END = r"[\s?!.,]*$"
_TAIL = r"(\s+(today|now|then|again|exactly|please|guide|bot|there))?"


def _whole(*alternatives: str) -> re.Pattern:
    return re.compile("^(" + "|".join(alternatives) + ")" + _TAIL + _END, re.I)


_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("CAPABILITIES", _whole(
        r"how\s+(can|could|do|will|would)\s+(you|u)\s+help(\s+(me|us))?",
        r"what\s+(can|could|do)\s+(you|u)\s+(do|help\s+(me\s+|us\s+)?with|offer)(\s+for\s+(me|us))?",
        r"what\s+are\s+you\s+(able|here)\s+to\s+do", r"help(\s+me)?", r"i\s+need\s+(some\s+)?help",
        r"can\s+you\s+help(\s+(me|us))?", r"what\s+can\s+i\s+ask(\s+you)?", r"what\s+do\s+you\s+know",
        r"what\s+(are\s+)?your\s+(features|capabilities)", r"menu", r"options",
        r"(get\s+)?started")),
    ("HUMAN_CHECK", _whole(
        r"(are|r)\s+(you|u)\s+(a\s+|an\s+)?(real|human|person|bot|robot|ai|machine|chat\s*bot|"
        r"real\s+person|live\s+agent|a\s+real\s+person)",
        r"is\s+this\s+(a\s+)?(bot|human|real\s+person|ai|chat\s*bot)",
        r"am\s+i\s+(talking|speaking|chatting)\s+(to|with)\s+(a\s+)?(bot|human|real\s+person|ai)")),
    ("IDENTITY", _whole(
        r"(who|what)\s+(are|r)\s+(you|u)", r"what('?s|\s+is)\s+your\s+name",
        r"(tell\s+me\s+)?about\s+(yourself|you)", r"introduce\s+yourself",
        r"who\s+am\s+i\s+(talking|speaking|chatting)\s+(to|with)", r"what\s+is\s+guide",
        r"who\s+(made|built|created)\s+you")),
    ("HOW_ARE_YOU", _whole(
        r"how\s+(are|r)\s+(you|u)(\s+doing)?", r"how('?s|\s+is)\s+it\s+going",
        r"how\s+do\s+you\s+do", r"what'?s\s+up", r"sup", r"wassup")),
    ("THANKS", _whole(
        r"(thanks?|thank\s+(you|u))(\s+(a\s+lot|so\s+much|very\s+much|so\s+very\s+much|a\s+ton))?"
        r"(\s+for\s+(the|your)\s+help|\s+for\s+(that|everything))?",
        r"thx", r"ty", r"cheers", r"much\s+appreciated", r"appreciate\s+(it|that)")),
    ("GOODBYE", _whole(
        r"bye+", r"goodbye", r"good\s+bye", r"see\s+(you|ya)(\s+later)?", r"cya",
        r"that'?s\s+all", r"that\s+is\s+all", r"i'?m\s+done", r"nothing\s+else",
        r"no\s+thanks?(\s+you)?", r"have\s+a\s+(good|great|nice)\s+(day|one|evening)")),
    ("NEGATIVE", _whole(
        r"you('?re|\s+are)\s+(useless|stupid|dumb|not\s+helpful|unhelpful|bad|wrong)",
        r"(this|that|it)\s+(is|was)\s+(useless|not\s+helpful|unhelpful|wrong|bad)",
        r"not\s+helpful", r"useless", r"that\s+didn'?t\s+help", r"you\s+didn'?t\s+help")),
    ("POSITIVE", _whole(
        r"great", r"awesome", r"perfect", r"cool", r"nice", r"excellent", r"amazing", r"wonderful",
        r"good\s+job", r"well\s+done", r"that\s+(helped|helps|was\s+helpful)", r"very\s+helpful",
        r"helpful")),
    ("CONFUSED", _whole(
        r"what", r"huh", r"i\s+don'?t\s+(understand|get\s+it)", r"i'?m\s+(confused|lost)",
        r"i\s+don'?t\s+know(\s+what\s+to\s+ask)?", r"not\s+sure")),
    ("ACK", _whole(
        r"ok(ay)?", r"k", r"sure", r"alright", r"all\s+right", r"got\s+it", r"i\s+see",
        r"understood", r"fine", r"yes", r"yeah", r"yep", r"no", r"nope", r"hmm+")),
]

CAPABILITIES_LIST = (
    "- Explaining what ZoikoStream does and the kinds of events it supports\n"
    "- Pricing questions, requesting a demo, or reaching the sales team\n"
    "- Getting into an event, and fixing video, audio, captions or replay problems\n"
    "- Privacy, security, the status page, and finding the right person to talk to"
)

COPY: dict[str, list[str]] = {
    # Calm, short and direct (Conversation Flow §28.1, Engineering Wireframe §8.1): no social
    # filler or exclamations before the answer, no exaggerated AI personality.
    "GREETING": [
        "Hello. I'm Guide, ZoikoStream's AI assistant. I can answer questions about "
        "ZoikoStream, help you request a demo, or help you get into and watch an event. "
        "What would you like to know?",
        "Hello. I'm Guide, the AI assistant for ZoikoStream. Ask a question about ZoikoStream, "
        "or choose a topic below.",
    ],
    "GREETING_AGAIN": [
        "Hello again. What else would you like to know?",
        "What would you like to know next?",
    ],
    "IDENTITY": [
        "I'm Guide, the AI assistant on the ZoikoStream website. I answer questions using "
        "ZoikoStream's public information, help you request a demo or reach the team, and walk "
        "viewers through access and playback problems step by step.\n\n"
        "I'm an AI, not a person, and I can't see your account or any private event details.",
    ],
    "HUMAN_CHECK": [
        "I'm an AI assistant, not a person. I answer from ZoikoStream's public information and "
        "can guide you through common viewer issues. If you'd like to talk to someone on the "
        "ZoikoStream team, I can point you to the right place.",
    ],
    "CAPABILITIES": [
        "Here's what I can help with:\n\n" + CAPABILITIES_LIST + "\n\n"
        "Ask in your own words, or choose a topic below.",
    ],
    "HOW_ARE_YOU": [
        "I'm ready to help. What would you like to know about ZoikoStream?",
        "What can I help you with?",
    ],
    "THANKS": [
        "You're welcome. Is there anything else I can help with?",
        "You're welcome. Let me know if you have another question.",
    ],
    "GOODBYE": [
        "Thanks for visiting. You can open Guide again anytime you have a question.",
        "You can open Guide again anytime you have a question.",
    ],
    "NEGATIVE": [
        "I'm sorry I wasn't more helpful. I can only answer from ZoikoStream's public "
        "information, so some questions are better handled by the team directly. You can "
        "contact them below, or try asking your question a different way.",
    ],
    "POSITIVE": [
        "Good to hear. Is there anything else you'd like to know?",
        "Let me know if there's anything else you'd like to know.",
    ],
    "CONFUSED": [
        "You can ask a question in your own words, or choose one of the topics below.",
    ],
    "ACK": [
        "What would you like to do next? You can ask about ZoikoStream or choose a topic below.",
    ],
}

# Registry destinations offered with some replies (only ids that exist in the registry render).
ACTIONS = {
    "HUMAN_CHECK": ["contact-support", "contact-sales"],
    "NEGATIVE": ["contact-support", "contact-sales"],
}

GAP_FOLLOW_UP = ("I can help with ZoikoStream's product, pricing and demos, or viewer support. "
                 "You can also reach the team directly below.")


@dataclass
class Conversational:
    kind: str           # GREETING, IDENTITY, ...
    text: str


def strip_greeting(text: str) -> tuple[bool, str]:
    """-> (had_greeting, remaining text)."""
    m = _GREETING.match(text)
    if not m:
        return False, text
    return True, text[m.end():].strip(" ,!.:;-")


def classify(text: str, *, returning: bool = False) -> Conversational | None:
    """A conversational reply for `text`, or None when it is a real request."""
    greeted, rest = strip_greeting(text.strip())
    if greeted and not rest:
        kind = "GREETING_AGAIN" if returning else "GREETING"
        return Conversational(kind, random.choice(COPY[kind]))
    candidate = rest if greeted else text.strip()
    candidate = candidate.strip()
    if len(candidate.split()) > 14:
        return None  # long messages are real requests, even if they open politely
    for kind, pattern in _PATTERNS:
        if pattern.match(candidate):
            return Conversational(kind, random.choice(COPY[kind]))
    return None


def routable_text(text: str) -> str:
    """The message with any leading greeting removed, for intent routing and retrieval."""
    greeted, rest = strip_greeting(text.strip())
    return rest if greeted and rest else text
