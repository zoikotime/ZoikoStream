"""The evaluation harness itself: dataset integrity, scorer semantics, decision rule."""
import json

import pytest

from app.guide.knowledge import ingest
from guide_eval import cases as C
from guide_eval.report import decide, metrics
from guide_eval.runner import CaseResult
from guide_eval.scorers import TurnObservation, check

CORPUS = {d.meta["source_id"] for d in ingest.load_sources()}


def test_datasets_load_and_reference_real_sources():
    cases = C.load()
    assert len(cases) >= 150
    for c in cases:
        for turn_expect in [c.expect] + [t.expect for t in c.turns]:
            for key in ("sources_any", "sources_all", "retrieval_expect"):
                unknown = set(turn_expect.get(key, [])) - CORPUS
                assert not unknown, (c.test_id, key, unknown)


def test_every_secret_case_checks_for_echo():
    for c in C.load():
        if c.suite in ("ADV_secret",):
            assert c.expect.get("no_echo"), c.test_id


def test_loader_rejects_unknown_expectations(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(json.dumps({"test_id": "X-1", "suite": "x", "risk": "LOW", "turns": ["hi"],
                             "expect": {"answer_classs_in": ["factual"]}}) + "\n")
    with pytest.raises(C.DatasetError, match="unknown expectation"):
        C.load([p])


def test_loader_rejects_duplicate_ids(tmp_path):
    line = json.dumps({"test_id": "X-1", "suite": "x", "risk": "LOW", "turns": ["hi"]})
    p = tmp_path / "dup.jsonl"
    p.write_text(line + "\n" + line + "\n")
    with pytest.raises(C.DatasetError, match="duplicate"):
        C.load([p])


def _obs(**kw):
    base = dict(text="ok", answer_class="factual", session_state="ACTIVE", grounded=True,
                sources=["product-overview"], actions=[], intent="PRODUCT_QA",
                boundary="allowed_public", model_calls=1, retrieval=None, http_status=200)
    base.update(kw)
    return TurnObservation(**base)


def test_scorer_catches_secret_echo_and_invariants():
    f = check({"no_echo": ["482913"]}, _obs(text="Your PIN 482913 is wrong. See https://x.y"),
              CORPUS)
    assert any("echoed" in x for x in f)
    assert any("INVARIANT url" in x for x in f)
    f = check({}, _obs(text="ZohoStream is great", actions=[
        {"action_id": "a", "type": "navigate", "label": "x", "href": "https://evil"}]), CORPUS)
    assert any("brand" in x for x in f) and any("non-relative" in x for x in f)


def test_scorer_no_source_semantics():
    assert check({"no_source": True}, _obs(answer_class="knowledge_gap", sources=[]), CORPUS) == []
    assert check({"no_source": True}, _obs(), CORPUS)


def _result(cid, risk, passed, failures=()):
    return CaseResult(cid, "s", risk, passed, list(failures), [{
        "answer_class": "factual", "intent": "PRODUCT_QA", "boundary": "allowed_public",
        "retrieval": None, "model_calls": 0, "answer": ""}])


def test_decision_blocks_on_critical_failure():
    cases = [C.Case("A", "s", "CRITICAL", [C.Turn("q")], {}),
             C.Case("B", "s", "LOW", [C.Turn("q")], {})]
    results = [_result("A", "CRITICAL", False, ["x"]), _result("B", "LOW", True)]
    d = decide(metrics(cases, results), cases)
    assert d["decision"] == "BLOCKED"


def test_starter_dataset_never_reports_plain_pass():
    cases = [C.Case("A", "s", "LOW", [C.Turn("q")], {})]
    d = decide(metrics(cases, [_result("A", "LOW", True)]), cases)
    assert d["decision"] == "PASS_ON_STARTER_DATASET"
    assert d["below_launch_minimum"]["single_turn"] == (1, 1900)
