"""Test-case contract (Evaluation §4) and dataset loading.

One JSON object per line in datasets/*.jsonl:

  {"test_id": "G7-012", "suite": "G7_private", "risk": "CRITICAL",
   "page": "home",                         # optional public page type (default home)
   "turns": ["Show me the guest list"],    # strings, or {"text": ..., "expect": {...}}
   "expect": {...},                        # assertions on the FINAL turn
   "fixtures": {...},                      # optional typed fixtures (EVAL §4 "fixtures")
   "origin": "curated" | "generated",      # provenance; generated cases name their generator
   "generator": "gen.g1.capability@1"}

Fixture keys: model ("timeout" | "error" | "refused"), retrieval ("down"), features_off
(["generation" | "leads" | "viewer_support"]), rate_limited (true), status ({overall_label,
active_incidents} D1 feed, or "unavailable"), event_context (D2 claims to sign into the page
context).

Expectation keys (all optional; see scorers.py for exact semantics):
  answer_class_in, state, boundary, intent_in, model_called, grounded,
  sources_any, sources_all, sources_none, retrieval_expect, no_source,
  actions_any, actions_none, flow_type, must_contain_any, must_not_contain, no_echo
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

DATASETS = Path(__file__).resolve().parent / "datasets"
RISKS = ("CRITICAL", "HIGH", "MATERIAL", "LOW")
EXPECT_KEYS = {
    "answer_class_in", "state", "boundary", "intent_in", "model_called", "grounded",
    "sources_any", "sources_all", "sources_none", "retrieval_expect", "no_source",
    "actions_any", "actions_none", "flow_type", "must_contain_any", "must_not_contain",
    "no_echo", "http_status", "error_code",
}
FIXTURE_KEYS = {"model", "retrieval", "features_off", "rate_limited", "status", "event_context"}
ORIGINS = ("curated", "generated")


@dataclass
class Turn:
    text: str
    expect: dict = field(default_factory=dict)


@dataclass
class Case:
    test_id: str
    suite: str
    risk: str
    turns: list[Turn]
    expect: dict
    page: str = "home"
    note: str | None = None
    source_file: str = ""
    fixtures: dict = field(default_factory=dict)
    origin: str = "curated"
    generator: str | None = None

    @property
    def final(self) -> Turn:
        return self.turns[-1]


class DatasetError(ValueError):
    pass


def _turn(raw, where: str) -> Turn:
    if isinstance(raw, str):
        return Turn(raw)
    if isinstance(raw, dict) and isinstance(raw.get("text"), str):
        _check_expect(raw.get("expect", {}), where)
        return Turn(raw["text"], raw.get("expect", {}))
    raise DatasetError(f"{where}: invalid turn {raw!r}")


def _check_expect(expect: dict, where: str) -> None:
    unknown = set(expect) - EXPECT_KEYS
    if unknown:
        raise DatasetError(f"{where}: unknown expectation keys {sorted(unknown)}")


def load(paths: list[Path] | None = None) -> list[Case]:
    cases, seen = [], set()
    for path in paths or sorted(DATASETS.glob("*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            where = f"{path.name}:{n}"
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{where}: {exc}") from None
            tid = raw.get("test_id")
            if not tid or tid in seen:
                raise DatasetError(f"{where}: missing or duplicate test_id {tid!r}")
            seen.add(tid)
            if raw.get("risk") not in RISKS:
                raise DatasetError(f"{where}: risk must be one of {RISKS}")
            expect = raw.get("expect", {})
            _check_expect(expect, where)
            turns = [_turn(t, where) for t in raw.get("turns", [])]
            if not turns:
                raise DatasetError(f"{where}: no turns")
            fixtures = raw.get("fixtures") or {}
            if set(fixtures) - FIXTURE_KEYS:
                raise DatasetError(f"{where}: unknown fixtures {sorted(set(fixtures) - FIXTURE_KEYS)}")
            origin = raw.get("origin", "curated")
            if origin not in ORIGINS:
                raise DatasetError(f"{where}: origin must be one of {ORIGINS}")
            if origin == "generated" and not raw.get("generator"):
                raise DatasetError(f"{where}: generated case without a generator id")
            cases.append(Case(tid, raw.get("suite", "unspecified"), raw["risk"], turns,
                              expect, raw.get("page", "home"), raw.get("note"), path.name,
                              fixtures, origin, raw.get("generator")))
    return cases


def dataset_version(paths: list[Path] | None = None) -> str:
    h = hashlib.sha256()
    for path in paths or sorted(DATASETS.glob("*.jsonl")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]
