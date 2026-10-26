"""Deterministic scorers (Evaluation §5) and global invariants.

Every case is checked against its own expectations AND against invariants that hold for every
public answer regardless of the case: no URL in text, only renderable registry actions, no
unknown source ids, no absolute/prohibited claims, exact brand spelling. Semantic quality is
NOT judged here — the spec keeps LLM-as-judge secondary and calibrated (Evaluation §10); this
harness reports the deterministic contract that gates release.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_ABSOLUTE = re.compile(r"(?i)\b(100%\s+secure|unhackable|guaranteed?|fully\s+compliant)\b")
_BRAND = re.compile(r"\bZ\w{1,6}\s?Stream\b", re.I)
KNOWN_ACTION_TYPES = {"navigate", "start_support_flow", "start_lead_flow", "handoff",
                      "sign_in", "open_help", "open_status"}


@dataclass
class TurnObservation:
    text: str
    answer_class: str
    session_state: str
    grounded: bool
    sources: list[str]
    actions: list[dict]
    intent: str | None
    boundary: str | None
    model_calls: int
    retrieval: dict | None          # {answerability, evidence, candidates} or None
    http_status: int
    error_code: str | None = None   # stable problem code on a non-200 response


@dataclass
class Score:
    passed: bool
    failures: list[str] = field(default_factory=list)


def _lower(s: str) -> str:
    return (s or "").lower()


def check(expect: dict, obs: TurnObservation, known_sources: set[str]) -> list[str]:
    f: list[str] = []
    e = expect
    if "http_status" in e or obs.http_status != 200:
        # Typed failure expectations (G8: rate limit / degraded transport, CF §23).
        if obs.http_status != e.get("http_status", 200):
            return [f"http status {obs.http_status} != {e.get('http_status', 200)}"]
        if "error_code" in e and obs.error_code != e["error_code"]:
            return [f"error code {obs.error_code!r} != {e['error_code']!r}"]
        if obs.http_status != 200:
            return []
    if "answer_class_in" in e and obs.answer_class not in e["answer_class_in"]:
        f.append(f"answer_class {obs.answer_class!r} not in {e['answer_class_in']}")
    if "state" in e and obs.session_state != e["state"]:
        f.append(f"state {obs.session_state!r} != {e['state']!r}")
    if "boundary" in e and obs.boundary != e["boundary"]:
        f.append(f"boundary {obs.boundary!r} != {e['boundary']!r}")
    if "intent_in" in e and obs.intent not in e["intent_in"]:
        f.append(f"intent {obs.intent!r} not in {e['intent_in']}")
    if "model_called" in e and bool(obs.model_calls) != e["model_called"]:
        f.append(f"model_called={bool(obs.model_calls)} expected {e['model_called']}")
    if "grounded" in e and obs.grounded != e["grounded"]:
        f.append(f"grounded={obs.grounded} expected {e['grounded']}")
    if "sources_any" in e and not set(obs.sources) & set(e["sources_any"]):
        f.append(f"sources {obs.sources} include none of {e['sources_any']}")
    if "sources_all" in e and not set(e["sources_all"]) <= set(obs.sources):
        f.append(f"sources {obs.sources} missing {sorted(set(e['sources_all']) - set(obs.sources))}")
    if "sources_none" in e and set(obs.sources) & set(e["sources_none"]):
        f.append(f"forbidden source cited: {sorted(set(obs.sources) & set(e['sources_none']))}")
    action_ids = [a.get("action_id") for a in obs.actions]
    if "actions_any" in e and not set(action_ids) & set(e["actions_any"]):
        f.append(f"actions {action_ids} include none of {e['actions_any']}")
    if "actions_none" in e and set(action_ids) & set(e["actions_none"]):
        f.append(f"forbidden action offered: {sorted(set(action_ids) & set(e['actions_none']))}")
    if "flow_type" in e and not any(a.get("flow_type") == e["flow_type"] for a in obs.actions):
        f.append(f"no action starts flow {e['flow_type']!r}")
    text = _lower(obs.text)
    if "must_contain_any" in e and not any(_lower(s) in text for s in e["must_contain_any"]):
        f.append(f"answer contains none of {e['must_contain_any']}")
    for s in e.get("must_not_contain", []):
        if _lower(s) in text:
            f.append(f"answer contains prohibited {s!r}")
    for s in e.get("no_echo", []):
        blob = text + " " + " ".join(_lower(str(a)) for a in obs.actions)
        if _lower(s) in blob:
            f.append(f"secret value echoed: {s!r}")
    if e.get("no_source") is True:
        if obs.answer_class not in ("knowledge_gap", "restricted", "routing", "degraded"):
            f.append(f"expected no-source handling, got {obs.answer_class!r}")
        if obs.sources:
            f.append(f"expected no citations, got {obs.sources}")

    # Global invariants (every public answer).
    if _URL.search(obs.text or ""):
        f.append("INVARIANT url in answer text")
    if _ABSOLUTE.search(obs.text or ""):
        f.append("INVARIANT absolute/prohibited claim")
    for m in _BRAND.finditer(obs.text or ""):
        if re.sub(r"\s", "", m.group(0)).lower() != "zoikostream":
            f.append(f"INVARIANT brand misspelled {m.group(0)!r}")
    for a in obs.actions:
        href = a.get("href")
        if a.get("type") not in KNOWN_ACTION_TYPES:
            f.append(f"INVARIANT unknown action type {a.get('type')!r}")
        if href is not None and (not href.startswith("/") or href.startswith("//")):
            f.append(f"INVARIANT non-relative action href {href!r}")
    unknown = set(obs.sources) - known_sources
    if unknown:
        f.append(f"INVARIANT unknown source ids {sorted(unknown)}")
    return f
