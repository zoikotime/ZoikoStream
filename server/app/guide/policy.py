"""Deterministic boundary and intent classification (Conversation Flow §2, §20; Prompt §5).

Runs BEFORE retrieval or any model call, every turn. A private/restricted request never enters
public RAG or reaches the model as something to answer (CG-07): it gets a fixed boundary
template plus the closest safe route. Prior allowed turns never grant later authority — this is
re-evaluated per message.

These are pattern rules, deliberately conservative. They are the first layer of defense in
depth; the system prompt, retrieval eligibility and the output validator are the others.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .registry import INTENTS


@dataclass(frozen=True)
class Boundary:
    state: str                 # allowed_public | private_required | restricted
    reason: str | None = None  # stable reason class for audit (never the text)


def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


# Conversation Flow §20.1 restricted categories.
_RESTRICTED = [
    ("prompt_extraction", _rx(
        r"\b(system|developer|hidden|initial|original)\s+(prompt|instructions?|message|rules|policy)",
        r"\bignore\s+(all\s+|any\s+|your\s+|the\s+)?(previous|prior|above|earlier)?\s*(instructions|rules|prompts?)",
        r"\b(reveal|print|show|repeat|output|dump)\b.{0,40}\b(prompt|instructions|rules|configuration)",
        r"\bchain[- ]of[- ]thought\b", r"\byour\s+(internal\s+)?reasoning\b",
        r"\bwhat\s+(did|do)\s+you\s+retriev", r"\b(internal|private)\s+(sources?|documents?|metadata)",
        r"\b(jailbreak|DAN mode|developer mode)\b",
        r"\b(pretend|act|behave|imagine)\s+(to\s+be|as|like|you\s+are|you'?re|that\s+you\s+are)\s+"
        r"(an?\s+|the\s+)?(admin|administrator|operator|developer|root|system|staff|employee)")),
    ("event_private", _rx(
        r"\bguest\s*list\b", r"\battendee(s)?\s+(list|names|emails)\b",
        r"\bwho\s+(else\s+)?(is|are|was|were)\s+(attending|invited|registered|watching)",
        r"\b(am\s+i|is\s+\w+(\s+\w+)?)\s+(on\s+the\s+(guest\s+)?list|invited|registered)\b",
        r"\b(change|edit|update|modify)\s+(the\s+|my\s+)?event\s+(settings|access|password|pin)",
        r"\b(send|give|tell)\s+me\s+(the|my)\s+(pin|access code|event password|invite link)",
        r"\bwhat\s+is\s+(the|my)\s+(event\s+)?(pin|access code|event password)\b")),
    ("credential_request", _rx(
        r"\b(admin|root|database|db|server|internal)\s+(password|credentials?|login)",
        r"\b(your|the|zoikostream'?s?)\s+(api[_ ]?keys?|secret keys?|tokens?|private keys?)\b",
        r"\b(give|send|tell|show)\s+me\b.{0,30}\b(api[_ ]?key|password|token|credentials)")),
    ("customer_private", _rx(
        r"\b(list|name)\s+(of\s+|some\s+of\s+)?(your\s+)?(customers|clients|tenants)\b",
        r"\bwhich\s+(customers|clients|companies|organizations|churches|schools)\s+"
        r"(use|stream|are\s+using|have\s+used)\b",
        r"\bwho\s+(are\s+your\s+(customers|clients)|(else\s+)?uses\s+zoikostream)\b",
        r"\b(customer|client|user|attendee)\s+(list|emails|contact\s+details|data)\b")),
    ("operator", _rx(
        r"\bgo\s+live\b", r"\b(start|stop|end|pause)\s+(the\s+|my\s+|our\s+)?(stream|broadcast|recording|event)\b",
        r"\b(stream|ingest|encoder)\s+health\b", r"\btelemetry\b",
        r"\b(my|our)\s+(event\'?s?\s+)?readiness\b", r"\breadiness\s+((check|status|score)\s+)?(for|of)\s+(my|our)\b",
        r"\bfailover\b", r"\brun[- ]of[- ]show\b", r"\bproducer\s+(console|ready)\b",
        # Changing how an event is set up is the event team's control (PG §13, PG-AT-15).
        r"\b(change|edit|update|modify|configure|adjust)\s+(the\s+|my\s+|our\s+|this\s+)?"
        r"(event\s+)?(replay|recording|stream|streaming|broadcast|chat|caption|captions)\s+"
        r"(settings?|options?|availability|visibility|permissions?)\b",
        r"\b(enable|disable|turn\s+(on|off)|publish|unpublish|delete|remove)\s+(the\s+)?"
        r"(replay|recording)\s+(for|of)\s+(this|the|my|our)\s+(event|stream)\b",
        r"\b(our|my)\s+(viewer|audience)\s+(analytics|numbers|count)\b")),
    ("account_private", _rx(
        r"\b(my|our)\s+(account|subscription|invoice|invoices|bill|billing|payment method|"
        r"card on file|plan|organization|org|workspace|license|seats)\b",
        r"\b(cancel|downgrade|upgrade|change|renew)\s+(my|our)\b",
        r"\brefund\b", r"\breset\s+(my\s+)?password\b", r"\bcan'?t\s+(log|sign)\s*in\b",
        r"\bdelete\s+(my|our)\s+(account|event|recording|data)\b",
        r"\b(look\s*up|check|find)\s+(my|our)\b")),
    ("internal", _rx(
        r"\bpayroll\b", r"\bsalar(y|ies)\b", r"\bpayslip\b", r"\bhr\s+(policy|policies|team|portal)\b",
        r"\bemployee\s+(handbook|benefits|policy)\b", r"\bleave\s+policy\b",
        r"\binternal\s+(architecture|infrastructure|sop|security)\b",
        r"\b(pentest|penetration test)\s+(report|results|findings)\b",
        r"\bvulnerabilit(y|ies)\s+in\s+(zoikostream|your)\b")),
]

# A "my account" phrase inside a plainly public pricing/plan comparison is still public.
_PUBLIC_OVERRIDE = _rx(r"\bwhich\s+plan\b", r"\bwhat\s+plans?\b", r"\bplans?\s+(do you|are)\b")

_INTENT_RULES = [
    ("DEMO", _rx(r"\bdemo\b", r"\bdemonstration\b")),
    ("SALES", _rx(r"\b(talk|speak|chat)\s+(to|with)\s+(sales|someone in sales|a sales)",
                  r"\bcontact\s+sales\b", r"\bsales\s+team\b", r"\b(custom|enterprise)\s+quote\b")),
    ("HANDOFF", _rx(r"\b(human|real person|live agent|representative)\b",
                    r"\b(talk|speak)\s+to\s+(someone|somebody|a person|support)\b",
                    r"\bcontact\s+support\b", r"\bsupport\s+team\b")),
    ("STATUS", _rx(r"\b(is|are)\s+(zoikostream|the\s+(site|service|platform|website)|it|you|things)\s+down\b",
                   r"\boutage\b", r"\b(current|check\s+the|check)\s+(service\s+)?status\b", r"\bstatus\s+right\s+now\b",
                   r"\bdown\s+right\s+now\b")),
    ("CAPTIONS", _rx(r"\bcaptions?\b", r"\bsubtitles?\b", r"\bclosed\s+caption", r"\bcc\b")),
    ("REPLAY", _rx(r"\breplay\b", r"\bwatch\s+(it\s+)?later\b", r"\brewatch\b", r"\bon[- ]demand\b",
                   r"\brecording\s+(of|available)\b")),
    ("AUDIO", _rx(r"\bno\s+(sound|audio)\b", r"\bcan'?t\s+hear\b", r"\baudio\s+(is|isn'?t|not)\b",
                  r"\bsound\s+(is|isn'?t|not)\b")),
    ("PLAYBACK", _rx(r"\bbuffer", r"\bfreez", r"\bblack\s+screen\b", r"\bstutter", r"\blagg",
                     r"\b(video|stream|player)\s+(won'?t|isn'?t|is\s+not|not|doesn'?t|keeps)\b",
                     r"\bplayback\b", r"\bwon'?t\s+(play|load)\b", r"\bpixelat")),
    ("VIEWER_ACCESS", _rx(r"\bcan'?t\s+(get\s+in|get\s+into|access|join|open|enter)\b",
                          r"\bcannot\s+(access|join|open)\b", r"\b(join|access|enter)\s+(the|my|an)\s+event\b",
                          r"\binvitation\b", r"\binvite\s+link\b", r"\baccess\s+code\b",
                          r"\b(event\s+)?pin\b", r"\bwaiting\s+room\b", r"\blink\s+(doesn'?t|isn'?t|not)\s+work")),
    ("PRICING", _rx(r"\bpric(e|es|ing)\b", r"\bcosts?\b", r"\bhow\s+much\b", r"\b(what|which)\s+plans?\b",
                    r"\bplans?\s+(do\s+you|are|cost|and\s+pricing)\b", r"\b(subscription|pricing|paid)\s+plans?\b",
                    r"\bper\s+(month|year|event)\b", r"\bfree\s+trial\b", r"\btrial\b",
                    r"\bdiscount\b", r"\bcoupon\b", r"\bpromo\b", r"\bbilling\s+(cycle|options)\b",
                    r"\bannual(ly)?\b", r"\bmonthly\b", r"\bquote\b")),
    ("PRIVACY", _rx(r"\bprivacy\b", r"\bpersonal\s+data\b", r"\bgdpr\b", r"\bccpa\b", r"\bcookies?\b",
                    r"\bdata\s+(retention|deletion|protection|processors?|processing)\b",
                    r"\bhow\s+(is|do you use)\s+(my\s+)?(data|chat)\b", r"\b(my|your|personal)\s+(data|information)\b",
                    r"\bprocess(es|ing)?\s+(my|personal)\b", r"\bsell\s+(my|your|user)\b", r"\b(children|kids|minors)\b")),
    ("SECURITY_TRUST", _rx(r"\bsecur(e|ity)\b", r"\bencrypt", r"\bcomplian(t|ce)\b", r"\bsoc\s*2\b",
                           r"\biso\s*27001\b", r"\bhipaa\b", r"\btrust\s+center\b", r"\bdata\s+residency\b",
                           r"\bwhere\s+is\s+(my\s+|the\s+)?data\s+stored\b")),
    ("LEGAL", _rx(r"\bterms\b", r"\blegal\b", r"\bcontract\b", r"\bliabilit", r"\bagreement\b",
                  r"\bsla\b")),
    ("DEVELOPER", _rx(r"\bapi\b", r"\bsdk\b", r"\bwebhooks?\b", r"\bdeveloper", r"\bintegrat",
                      r"\bendpoint\b", r"\brtmp\b", r"\bsrt\b", r"\bembed\b")),
    ("DISCOVERY", _rx(r"\bwe\s+(need|want|are\s+looking|plan)\b", r"\blooking\s+for\b",
                      r"\b(can|could|does)\s+(you|zoikostream|it)\s+(help|stream|support|handle|work)\b",
                      r"\buse\s+case\b", r"\bfor\s+(our|my)\s+(church|school|company|event|team)\b",
                      r"\b(funeral|memorial|wedding|church|school|town\s*hall|webinar|conference)\b")),
]

EVENT_TIME = _rx(r"\bwhat\s+time\b", r"\bwhen\s+(does|will|is)\b.{0,20}\b(start|begin|live)\b",
                 r"\bstart\s+time\b")

_DISCOUNT = _rx(r"\bdiscount\b", r"\bcoupon\b", r"\bpromo(\s+code)?\b", r"\bnegotiat",
                r"\bbetter\s+(deal|price)\b", r"\bcheaper\b")


def classify_boundary(text: str) -> Boundary:
    for reason, pattern in _RESTRICTED:
        if pattern.search(text):
            if reason == "account_private" and _PUBLIC_OVERRIDE.search(text):
                continue
            state = "restricted" if reason in ("prompt_extraction", "credential_request",
                                               "operator", "internal",
                                               "customer_private") else "private_required"
            return Boundary(state, reason)
    return Boundary("allowed_public")


# "Does it support captions?" is a product question, not a viewer problem: capability questions
# about a viewer feature are answered from public product evidence, not the support flow.
_CAPABILITY = _rx(r"\b(do|does|can|will)\s+(you|it|zoikostream|the\s+(platform|product|player))\s+"
                  r"(support|offer|provide|have|include|do|allow|record)\b",
                  r"\bis\s+there\s+(a|an|any)\s+(option|feature|support)\b")
_VIEWER_INTENTS = {"CAPTIONS", "REPLAY", "AUDIO", "PLAYBACK"}


def classify_intent(text: str, intent_hint: str | None = None) -> str:
    """Free text decides; a quick-action hint only breaks ties (API §8.1: the hint is advisory
    and never bypasses routing)."""
    capability = bool(_CAPABILITY.search(text))
    for intent, pattern in _INTENT_RULES:
        if capability and intent in _VIEWER_INTENTS:
            continue
        if pattern.search(text):
            return intent
    if intent_hint and intent_hint in INTENTS:
        return INTENTS[intent_hint]
    return "PRODUCT_QA"


def is_discount_request(text: str) -> bool:
    return bool(_DISCOUNT.search(text))
