"""Intent routing (ZST-UX-GUIDE-001 §12): deterministic rules, evaluated in a fixed order.

Order matters and encodes policy:
  1. blocked  — R5 critical live controls and R6 destructive / secret operations are
                recognised FIRST, so no phrasing can reach an action or a tool with them.
                A how-to QUESTION about one ("how do I delete an event?") is answered from
                documentation instead, with a note that Guide cannot do it (§25).
  2. action   — R3/R4 governed changes, recognised only in imperative form.
  3. draft    — R2 draft generation (never applied without a separate confirmation).
     escalate — a request for human help; Guide offers routes, it never contacts anyone.
  4. chat     — greeting, identity, capabilities, thanks.
  5. read     — R0/R1 tools chosen by topic vocabulary; definitional phrasing adds docs.
  6. docs     — anything else is a documentation question (or an honest gap).

The model never routes, never picks a tool and never decides an action's parameters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Intent:
    kind: str                    # blocked | action | draft | escalate | chat | read | docs
    name: str
    tools: list[str] = field(default_factory=list)
    docs: bool = False
    action: str | None = None                   # actions.CATALOG id
    params: dict = field(default_factory=dict)
    risk: str | None = None                     # for blocked: R5 | R6


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


# ── 1. Blocked operations ────────────────────────────────────────────────────────────
# (name, risk, pattern). R5 routes to the dedicated control; R6 is refused outright.
BLOCKED = [
    ("go_live", "R5", _rx(r"\bgo(ing)?\s+live\b|\bstart(ing)?\s+(the\s+|my\s+|our\s+)?"
                          r"(broadcast|stream(ing)?|show|event)\b|\bbegin\s+streaming\b|"
                          r"\barm\s+(the\s+|this\s+|my\s+)?event\b")),
    ("end_broadcast", "R5", _rx(r"\b(end|stop|kill|terminate|finish)\s+(the\s+|my\s+|our\s+|this\s+)?"
                                r"(broadcast|stream(ing)?|live\s*stream|show)\b|\bgo\s+off\s+air\b")),
    ("recording_control", "R5", _rx(r"\b(start|stop|pause|resume)\s+(the\s+)?recording\b")),
    ("failover", "R5", _rx(r"\bfail\s*-?\s*over\b|\bswitch\s+to\s+(the\s+)?(backup|secondary|"
                           r"recovery)\b|\brecovery\s+output\b")),
    ("override_gate", "R5", _rx(r"\b(override|bypass|skip|ignore)\s+(the\s+|a\s+)?"
                                r"(readiness|gate|gates|check|checks|blocker)\b|"
                                r"\b(mark|set|make|force)\s+(it\s+|this\s+|the\s+event\s+)?"
                                r"(as\s+)?(ready|live)\b")),
    ("cancel_event", "R5", _rx(r"\bcancel\s+(the\s+|this\s+|my\s+|our\s+)?event\b")),
    ("delete", "R6", _rx(r"\b(delete|erase|purge|destroy|wipe)\b")),
    ("secret", "R6", _rx(r"(\b(show|reveal|give|send|tell|display|copy)\b|\bwhat\s*('?s|\s+is)\s+"
                         r"(the|my|our)\b).{0,30}"
                         r"\b(stream\s*key|api\s*key|secret|password|token|ingest\s*url|"
                         r"access\s*link\s*url|signing\s*key)\b|"
                         r"\b(rotate|regenerate|reset)\s+(the\s+|my\s+|an?\s+)?(api\s*)?"
                         r"(key|keys|secret|stream\s*key|token)\b")),
    ("roles", "R6", _rx(r"\b(make|promote|demote)\b.{0,40}\b(admin|owner|host)\b|"
                        r"\bchange\s+.{0,20}\brole\b|\bgrant\s+.{0,20}\baccess\b|"
                        r"\btransfer\s+ownership\b")),
    ("billing", "R6", _rx(r"\b(refund|charge|cancel\s+(my\s+|our\s+|the\s+)?(subscription|plan)|"
                          r"change\s+(my\s+|our\s+|the\s+)?plan)\b")),
]

# A question about whether the event CAN go live is a readiness read, never a request to.
READINESS_QUESTION = _rx(r"\b(ready|good|clear|ok(ay)?|safe)\s+(to|for)\s+(go(ing)?\s+live|start|"
                         r"launch|broadcast)\b|\bcan\s+(we|i)\s+go\s+live\b|\bbefore\s+(we\s+)?"
                         r"go(ing)?\s+live\b|\bwhat('s|\s+is)\s+(blocking|stopping)\b")

# A question about HOW to do something is never a request to do it. Imperatives ("delete the
# recording", "can you rotate the key") do not match and stay blocked.
HOW_TO = _rx(r"^\s*(?:(?:and|so|ok(?:ay)?)\s*,?\s+)?(?:how\s+(?:do|can|should|would|could)\s+"
             r"(?:i|we|one|someone)\b|how\s+to\b|where\s+(?:do|can)\s+(?:i|we)\b|"
             r"what(?:'s|\s+is)\s+the\s+(?:way|process|procedure|workflow)\s+(?:to|for)\b|"
             r"is\s+(?:it|there\s+a\s+way)\s+(?:possible\s+)?to\b|can\s+(?:i|we)\b)")

# ── 2. Governed actions (imperative only) ────────────────────────────────────────────
_LEAD = r"^\s*(?:please\s+|pls\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|" \
        r"(?:i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to)\s+|let'?s\s+|go\s+ahead\s+and\s+)?"
_ON = r"(?:turn\s+on|switch\s+on|enable|allow|open|unmute|re-?enable|activate)"
_OFF = r"(?:turn\s+off|switch\s+off|disable|close|mute|block|deactivate|stop|hide)"
FEATURES = {
    "chat": r"(?:the\s+)?(?:live\s+)?chat",
    "qa": r"(?:the\s+)?(?:q\s*&\s*a|q\s*and\s*a|qa|questions)",
    "polls": r"(?:the\s+)?polls?",
    "waiting_room": r"(?:the\s+)?waiting\s*room",
}


def _toggle(enabled: bool, noun: str) -> re.Pattern:
    """"turn off chat" and "turn chat off" alike."""
    verb, word = (_ON, "on") if enabled else (_OFF, "off")
    return _rx(_LEAD + rf"(?:{verb}\s+{noun}|(?:turn|switch)\s+{noun}\s+{word})\b")


ACTIONS: list[tuple[re.Pattern, str, dict]] = []
for _key, _noun in FEATURES.items():
    ACTIONS.append((_toggle(True, _noun), "event.set_feature", {"feature": _key, "enabled": True}))
    ACTIONS.append((_toggle(False, _noun), "event.set_feature", {"feature": _key, "enabled": False}))
for _vis in ("private", "public", "unlisted"):
    ACTIONS.append((_rx(_LEAD + rf"(?:make|set|change|switch|turn)\s+(?:it|this|this\s+event|the\s+"
                                rf"event|(?:the\s+)?(?:visibility|access)(?:\s+profile)?)\s+"
                                rf"(?:to\s+|as\s+|into\s+)?{_vis}\b"),
                    "event.set_visibility", {"visibility": _vis}))
ACTIONS += [
    (_rx(_LEAD + r"(?:require|turn\s+on|enable|switch\s+on)\s+(?:the\s+)?registration\b|"
         + _LEAD + r"make\s+registration\s+required\b"),
     "event.set_registration", {"required": True}),
    (_rx(_LEAD + r"(?:turn\s+off|disable|remove|switch\s+off|don'?t\s+require|stop\s+requiring)"
                 r"\s+(?:the\s+)?registration\b|" + _LEAD + r"make\s+registration\s+optional\b"),
     "event.set_registration", {"required": False}),
    (_rx(_LEAD + r"(?:enable|turn\s+on|switch\s+on)\s+(?:the\s+)?recording\b"),
     "event.set_recording", {"enabled": True}),
    (_rx(_LEAD + r"(?:disable|turn\s+off|switch\s+off)\s+(?:the\s+)?recording\b"),
     "event.set_recording", {"enabled": False}),
    (_rx(_LEAD + r"revoke\s+(?:the\s+|an?\s+|that\s+|this\s+)?(?:access\s+)?links?\b"),
     "access_link.revoke", {}),
    (_rx(_LEAD + r"(?:open|create|raise|file|submit|log|start)\s+(?:a\s+|an\s+)?(?:new\s+)?"
                 r"(?:support\s+)?(?:case|ticket)\b"),
     "support.open_case", {}),
    (_rx(_LEAD + r"(?:set|change|update|replace)\s+(?:the\s+)?(?:event\s+)?description\s+"
                 r"(?:to|with)\s*:?\s*"),
     "event.update_description", {}),
]

# ── 3. Drafts ────────────────────────────────────────────────────────────────────────
DRAFT = _rx(r"\b(draft|write|compose|prepare|suggest|create)\b.{0,40}\b(description|summary|"
            r"announcement|reminder|email|message|invitation|invite|update|post|follow[- ]?up|"
            r"thank[- ]?you|notice|blurb)\b")
DRAFT_KINDS = [
    ("incident_update", _rx(r"\b(incident|outage|delay|problem|issue)\b")),
    ("reminder", _rx(r"\breminder\b")),
    ("follow_up", _rx(r"\b(follow[- ]?up|thank[- ]?you|after\s+the\s+event|post[- ]event|recap)\b")),
    ("announcement", _rx(r"\b(announce|announcement|invitation|invite|promo)\w*\b")),
    ("description", _rx(r"\b(description|summary|blurb)\b")),
]

ESCALATE = _rx(r"\b(talk|speak|chat)\s+(to|with)\s+(a\s+|an\s+|someone\s+(in|from)\s+)?"
               r"(human|person|people|someone|support|agent|engineer|real\s+person)\b|"
               r"\b(contact|reach|call|email|get\s+hold\s+of)\s+(zoiko\s*stream\s+)?"
               r"(support|a\s+human|someone)\b|\bescalate\b|\bneed\s+(a\s+)?human\b|"
               r"\bhuman\s+(help|support)\b|\bget\s+help\s+from\s+(a\s+)?(person|human|"
               r"support)\b")

# ── 4. Conversation ──────────────────────────────────────────────────────────────────
CHAT = [
    ("greeting", _rx(r"^\s*(hi|hello|hey|hiya|yo|good\s+(morning|afternoon|evening)|greetings)"
                     r"\b[\s!.,]*(there|guide)?[\s!.]*$")),
    ("thanks", _rx(r"^\s*(thanks|thank\s+you|thx|cheers|great|perfect|ok(ay)?|got\s+it)"
                   r"\b[\s\w!.,]{0,20}$")),
    ("identity", _rx(r"\bwho\s+are\s+you\b|\bwhat\s+are\s+you\b|\byour\s+name\b|"
                     r"\bare\s+you\s+(an?\s+)?(ai|bot|human|robot)\b")),
    ("capabilities", _rx(r"\bwhat\s+can\s+you\s+do\b|\bhow\s+can\s+you\s+help\b|"
                         r"\bwhat\s+do\s+you\s+do\b|^\s*help\s*[!?.]*$|\bwhat\s+can\s+i\s+ask\b")),
]
ATTENTION = _rx(r"\b(what\s+needs\s+(my\s+)?attention|anything\s+(i\s+should|to)\s+(know|worry)|"
                r"what('s|\s+is)\s+(wrong|happening|up)(?!\s+(with|to|on|in|at)\b)|"
                r"where\s+should\s+i\s+(start|look)|"
                r"what\s+should\s+i\s+(do|look\s+at|check))\b")

# ── 5. Reads ─────────────────────────────────────────────────────────────────────────
TOPICS: list[tuple[str, re.Pattern, int]] = [
    ("event.readiness", _rx(r"\b(ready|readiness|blockers?|blocked|gates?|conditional|"
                            r"go[- ]live\s+check|preflight|prepared|good\s+to\s+go)\b"), 3),
    ("event.live", _rx(r"\b(health|healthy|on\s+air|live\s+now|broadcast\s+(ok|status)|ingest|"
                       r"encoder|signal|bitrate|stream\s+(ok|status|quality)|how('s|\s+is)\s+"
                       r"(the\s+)?(broadcast|stream|event)\s+(going|doing))\b"), 3),
    ("event.incidents", _rx(r"\b(incidents?|wrong|problems?|issues?|drop(ped|ping)?|failing|"
                            r"failed|broken|glitch|lag(ging)?|buffer(ing)?|outage|down)\b"), 2),
    ("event.access", _rx(r"\b(access|private|public|unlisted|invit\w*|access\s+links?|links?|"
                         r"registration|registrations|register(ed)?|sign[- ]?ups?|who\s+can\s+"
                         r"(watch|join|see)|denied|refused|can'?t\s+join|capacity|admission)\b"), 2),
    ("event.replay", _rx(r"\b(replay|recordings?|recorded|vod|on[- ]demand|download|retention|"
                         r"legal\s+hold)\b"), 3),
    ("org.analytics", _rx(r"\b(analytics|viewers?|audience|attendance|attended|watch\s+time|"
                          r"engagement|peak|metrics?|stats|statistics|perform(ed|ing|ance)?|numbers|"
                          r"how\s+(many|well)|show\s+rate|devices?|countries|geography)\b"), 2),
    ("event.planning", _rx(r"\b(plan(ning)?|rehearsals?|run[- ]of[- ]show|intake|requirements?|"
                           r"cue|checklist|prep(aration)?)\b"), 2),
    ("event.summary", _rx(r"\b(config(uration)?|settings|set\s?up|details|summary|overview\s+of\s+"
                          r"(this|the)\s+event|tell\s+me\s+about|about\s+(this|the)\s+event|"
                          r"chat\s+(on|enabled)|q&a|polls?|when\s+(does|is)\s+.{0,30}\b(start|"
                          r"begin))\b"), 1),
    ("org.developer", _rx(r"\b(api|apis|api\s+keys?|webhooks?|developer|sdk|integration|"
                          r"endpoints?)\b"), 3),
    ("org.support", _rx(r"\b(support\s+(case|ticket)s?|my\s+(cases|tickets)|open\s+(cases|tickets)|"
                        r"support)\b"), 2),
    ("platform.status", _rx(r"\b(status\s+page|platform\s+status|service\s+status|maintenance|"
                            r"is\s+zoiko\s*stream\s+(down|up|ok)|zoiko\s*stream\s+(outage|down))\b"), 3),
    ("org.overview", _rx(r"\b(upcoming|overview|today|tomorrow|this\s+week|next\s+week|"
                         r"my\s+events|our\s+events|all\s+events|schedule[d]?)\b"), 2),
]
DEFINITIONAL = _rx(r"^\s*(what\s+(does|do|is|are)\b(?!.{0,40}\b(my|our|this|current|now)\b)|"
                   r"how\s+(do|does|can|should)\s+(i|we|you|it)\b|explain\b|define\b|"
                   r"what('s|\s+is)\s+the\s+difference|why\s+(does|do|would|is)\b|"
                   r"how\s+(do|does)\b.{0,40}\bwork\b|what\s+does\b.{0,40}\bmean\b)")
STATEFUL = _rx(r"\b(my|our|this|current(ly)?|now|right\s+now|today|is\s+(it|the\s+event)|"
               r"are\s+we)\b")
EVENT_TOOLS = {"event.readiness", "event.live", "event.incidents", "event.access",
               "event.replay", "event.planning", "event.summary"}


def classify(text: str) -> Intent:
    t = " ".join((text or "").split())
    if not t:
        return Intent("chat", "empty")

    if READINESS_QUESTION.search(t):
        return Intent("read", "event.readiness", tools=["event.readiness"])
    for name, risk, pattern in BLOCKED:
        if pattern.search(t):
            if HOW_TO.search(t):
                return Intent("docs", "docs.how_to", docs=True, params={"blocked": name})
            return Intent("blocked", name, risk=risk)

    for pattern, action, params in ACTIONS:
        m = pattern.search(t)
        if m:
            extra = dict(params)
            if action in ("support.open_case", "event.update_description"):
                extra["text"] = t[m.end():].strip(" :-") if action == "event.update_description" \
                    else t
            return Intent("action", action, action=action, params=extra)

    if DRAFT.search(t):
        kind = next((k for k, p in DRAFT_KINDS if p.search(t)), "description")
        return Intent("draft", f"draft.{kind}", params={"kind": kind})

    if ESCALATE.search(t):
        return Intent("escalate", "escalate")

    for name, pattern in CHAT:
        if pattern.search(t):
            return Intent("chat", name)
    if ATTENTION.search(t):
        return Intent("read", "attention", tools=["org.overview"])

    scores: dict[str, int] = {}
    for tool, pattern, weight in TOPICS:
        hits = len(pattern.findall(t))
        if hits:
            scores[tool] = weight * hits
    definitional = bool(DEFINITIONAL.search(t))
    if scores and (not definitional or STATEFUL.search(t)):
        ranked = sorted(scores, key=lambda k: -scores[k])[:2]
        # Live health and incident triage answer the same question from two sides.
        if ranked[0] == "event.incidents" and "event.live" not in ranked:
            ranked.append("event.live")
        return Intent("read", ranked[0], tools=ranked, docs=definitional)
    return Intent("docs", "docs", docs=True)


# ── Follow-ups (§13 conversation memory) ─────────────────────────────────────────────
# "and the replay?", "what about access?", "why?" — a turn that leans on the previous one.
FOLLOW_UP = _rx(r"^\s*(?:and|also|plus|then|so|ok(?:ay)?\s+and)\b|^\s*(?:what|how)\s+about\b|"
                r"^\s*(?:what|how)\s+(?:else|now)\b|^\s*(?:and\s+)?(?:why|how\s+come|really|"
                r"since\s+when|anything\s+else)\s*\??\s*$")


def is_follow_up(text: str) -> bool:
    t = " ".join((text or "").split())
    return bool(t) and len(t.split()) <= 8 and bool(FOLLOW_UP.search(t))
