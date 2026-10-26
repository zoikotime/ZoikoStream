"""Output validation, V-01..V-10 (ZST-AI-PROMPT-PUB-GUIDE-001 §17).

The model's reply is CANDIDATE content. This module decides whether it may be shown, and which
actions/sources survive. Fallback order (§17.1): safe structural repair -> one regeneration
with the failure summary -> approved template. Unvalidated output is never exposed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .knowledge.retrieval import EvidencePack
from .prompts import ANSWER_CLASSES, all_system_prompts
from .secrets_detect import detect

MAX_ANSWER_CHARS = 1500
# Prompt §15 "At most two prominent actions" and §28.1 "One material clarification at a
# time" — the same limits the policy envelope states to the model (pub-policy max_actions /
# max_clarifying_questions), so prompt and validator cannot disagree.
MAX_ACTIONS = 2
MAX_QUESTIONS = 1

_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HTML = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")

# V-02 boundary: claims of private access or completed private actions.
_PRIVATE_CLAIMS = re.compile(
    r"(?i)\b(i|we)\s+(have\s+)?(checked|looked\s+up|verified|accessed|reviewed|changed|updated|"
    r"reset|deleted|cancel+ed|refunded)\s+(your|the\s+event'?s?|their)\b"
    r"|\bi\s+can\s+see\s+(your|that\s+your)\b|\bin\s+your\s+account\b")
# V-06 commercial commitments.
_COMMERCIAL = re.compile(
    r"(?i)\b(i|we)\s+(can|will|could)\s+(offer|give|apply|extend)\b.{0,30}\b(discount|deal|"
    r"refund|credit|free\s+month|coupon)|\b(your|a)\s+discount\s+(has\s+been|is)\s+applied\b")
# V-07 status: no outage/incident claim without a typed public-status result.
_STATUS_CLAIMS = re.compile(
    r"(?i)\b(there\s+is|there'?s|we\s+are\s+experiencing|currently\s+experiencing)\s+(an?\s+)?"
    r"(outage|incident|disruption|degradation)\b|\b(zoikostream|the\s+(service|platform))\s+is\s+"
    r"(currently\s+)?(down|experiencing)\b|\ball\s+systems\s+(are\s+)?operational\b")
# V-09 hidden content markers.
_HIDDEN_MARKERS = [m.lower() for m in (
    "SYSTEM: ZOIKOSTREAM GUIDE", "<public_evidence", "<policy>", "boundary_state",
    "allowed_actions", "response_profile", "answerability_state", "RETRIEVED CONTENT\n",
    "validation_feedback", "profile_guidance")]
_SYSTEM_LINES = sorted({ln.strip().lower() for prompt in all_system_prompts()
                        for ln in prompt.splitlines() if len(ln.strip()) > 40})
# V-10 / do-not-say lexicon (§15.2).
_DO_NOT_SAY = re.compile(
    r"(?i)\b(100%\s+secure|completely\s+secure|unhackable|guaranteed?|fully\s+compliant|"
    r"always\s+available|never\s+goes?\s+down|i\s+am\s+(a\s+)?human|as\s+a\s+human)\b")
# The product name must be spelled exactly; a near-miss ("ZohoStream") reads as a different
# company and is rejected like any other unsupported claim.
_BRAND_VARIANT = re.compile(r"\bZ\w{1,6}\s?Stream\b", re.I)
_NUMBER = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?%|\b\d{2,}[\d,]*(?:\.\d+)?\b")


# ── V-05 claim-to-evidence (RAG §18.1) ─────────────────────────────────────────────────────
# Each material sentence must be lexically supported by the evidence it cites: most of its
# content words (6-char stems, so "streams"/"streaming" match) must occur in that evidence.
# A sentence that mostly says things the evidence does not is "unsupported expansion" and
# fails like an unsupported number: one regeneration, then the gap template.
CLAIM_MIN_TERMS = 4          # shorter sentences carry too little to judge
CLAIM_MIN_COVERAGE = 0.5     # share of a sentence's content terms found in cited evidence
_WORD = re.compile(r"[a-z0-9]+")
# Function words and assistant/routing vocabulary: overlap on these proves nothing, and
# lacking them in the evidence is not an unsupported claim.
_CLAIM_STOP = set("""
about above after again also although always another anyone anything around because been
before being below between both cannot could does doing done down during each either else
enough even every from further have having here into itself just like make many more most
much must need only other otherwise over own same should since some such than that their
them then there these they thing things this those though through under until upon very
want were what whatever when where whether which while will with within without would your
yours yourself you're it's that's there's don't can't isn't aren't won't doesn't
zoikostream guide help helps helpful information details detail page pages approved public
source sources visit check request demo sales team contact support link links route
please happy glad sure question questions answer offer option options next step steps
learn find mention mentioned confirm confirms provide provides available currently today
""".split())
# Sentences that make no factual claim about ZoikoStream: offers, routing, hedges, questions.
_NON_CLAIM = re.compile(
    r"(?i)^\s*(i\s+can|i\s+could|i'?d|i\s+don'?t|i\s+do\s+not|i\s+can'?t|i\s+cannot|you\s+can|"
    r"you\s+could|you\s+may|you\s+might|if\s+you|let\s+me|would\s+you|feel\s+free|for\s+more|"
    r"to\s+(learn|find|get|see|request)|the\s+approved\s+(public\s+)?sources?|please)\b")


def _stems(text: str) -> set[str]:
    return {w[:6] for w in _WORD.findall(text.lower().replace("'", ""))
            if len(w) > 3 and w not in _CLAIM_STOP}


def unsupported_claims(text: str, evidence_text: str) -> list[str]:
    """Sentences of `text` whose content is mostly absent from `evidence_text`."""
    support = _stems(evidence_text)
    bad = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = sentence.strip()
        if not s or s.endswith("?") or _NON_CLAIM.match(s):
            continue
        terms = _stems(s)
        if len(terms) < CLAIM_MIN_TERMS:
            continue
        if len(terms & support) / len(terms) < CLAIM_MIN_COVERAGE:
            bad.append(s)
    return bad


def question_count(text: str) -> int:
    return len(re.findall(r"\?(?=\s|$)", text))


@dataclass
class Validated:
    ok: bool
    answer_text: str = ""
    answer_class: str = "knowledge_gap"
    source_ids: list[str] = field(default_factory=list)
    action_ids: list[str] = field(default_factory=list)
    uncertainty: str = "gap"
    failures: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return bool(self.source_ids) and self.uncertainty != "gap"


def _norm_num(s: str) -> str:
    return re.sub(r"[\s,$%]", "", s)


def validate(data: dict | None, *, pack: EvidencePack, allowed_action_ids: list[str],
             high_risk: bool, max_actions: int = MAX_ACTIONS,
             max_questions: int = MAX_QUESTIONS) -> Validated:
    failures: list[str] = []
    # V-01 schema
    if not isinstance(data, dict):
        return Validated(False, failures=["V-01 response did not match the schema"])
    text = data.get("answer_text")
    cls = data.get("answer_class")
    sources = data.get("used_source_ids")
    actions = data.get("action_ids")
    unc = data.get("uncertainty")
    if not isinstance(text, str) or not text.strip() or cls not in ANSWER_CLASSES or \
            not isinstance(sources, list) or not isinstance(actions, list) or \
            unc not in ("none", "partial", "gap"):
        return Validated(False, failures=["V-01 response did not match the schema"])

    # V-04 URLs: structural repair — links only ever come from the destination registry.
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _HTML.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    # V-10 brand spelling: structural repair. Models tokenize "Zoiko" poorly and drift to
    # near-misses ("ZohoStream"); a "Z…Stream" compound can only mean this product here, so it
    # is corrected rather than failing an otherwise valid grounded answer.
    text = _BRAND_VARIANT.sub(
        lambda m: m.group(0) if re.sub(r"\s", "", m.group(0)).lower() == "zoikostream"
        else "ZoikoStream", text)

    # V-03 actions
    action_ids = [a for a in dict.fromkeys(actions) if a in allowed_action_ids][:max_actions]
    # Source ids must be ones actually supplied this turn.
    refs = pack.source_refs()
    source_ids = [s for s in dict.fromkeys(sources) if s in refs]

    low = text.lower()
    if _PRIVATE_CLAIMS.search(text):
        failures.append("V-02 claimed access to private account or event state")
    if _COMMERCIAL.search(text):
        failures.append("V-06 offered a commercial commitment")
    if _STATUS_CLAIMS.search(text):
        failures.append("V-07 asserted current service status without a status result")
    if detect(text).suspected:
        failures.append("V-08 output contains a credential-like value")
    if any(m in low for m in _HIDDEN_MARKERS) or any(ln in low for ln in _SYSTEM_LINES):
        failures.append("V-09 output exposes hidden instructions or internal metadata")
    if _DO_NOT_SAY.search(text):
        failures.append("V-10 output uses an absolute or prohibited claim")
    if len(text) > MAX_ANSWER_CHARS:
        failures.append("V-10 answer is too long")
    if question_count(text) > max_questions:
        failures.append("V-10 asks more than one clarifying question")

    # V-05 grounding: a factual answer must cite, and every number it states must appear in
    # the evidence it cites (prices, durations, counts, percentages — V-06 for amounts).
    if unc != "gap" and cls in ("factual", "pricing", "privacy", "discovery"):
        if not source_ids:
            failures.append("V-05 factual answer cites no supplied source")
        evidence_text = " ".join(e.text for e in pack.evidence if e.source_id in source_ids)
        evidence_nums = {_norm_num(n) for n in _NUMBER.findall(evidence_text)}
        unsupported = [n for n in _NUMBER.findall(text) if _norm_num(n) not in evidence_nums]
        if unsupported:
            failures.append("V-05 states numbers not present in the cited evidence")
        if source_ids:
            cited = " ".join(f"{e.title} {e.heading_path} {e.text}" for e in pack.evidence
                             if e.source_id in source_ids)
            if unsupported_claims(text, cited):
                failures.append("V-05 makes claims the cited evidence does not support")
    if high_risk and unc == "none" and not source_ids:
        failures.append("V-05 high-risk answer without evidence")

    if failures:
        return Validated(False, failures=failures)
    return Validated(True, text, cls, source_ids if unc != "gap" else [], action_ids, unc)
