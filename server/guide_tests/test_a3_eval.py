"""Evaluation harness additions (ZST-AI-EVAL-PUB-GUIDE-001 §3, §7–10, §22, §25): dataset
sizes and provenance, ranking/grounding metrics, stability, baseline regression, the LLM judge
(with a FAKE judge — no paid model is ever called here) and human review export/import."""
import json

import pytest

from app.guide.knowledge import gate, ingest
from guide_eval import cases as C
from guide_eval import claims, judge, review
from guide_eval.report import compare, decide, metrics, stability
from guide_eval.runner import CaseResult
from guide_eval.scorers import TurnObservation, check

CORPUS = {d.meta["source_id"] for d in ingest.load_sources()}


# ── Datasets (EVAL §3) ─────────────────────────────────────────────────────────────────────

def test_launch_sized_benchmark_with_honest_provenance():
    cases = C.load()
    single = [c for c in cases if len(c.turns) == 1 and not c.suite.startswith("ADV")]
    multi = [c for c in cases if len(c.turns) > 1]
    adv = [c for c in cases if c.suite.startswith("ADV")]
    assert len(single) >= 1900 and len(multi) >= 250 and len(adv) >= 500
    for c in cases:
        assert c.origin in ("curated", "generated")
        if c.origin == "generated":
            assert c.generator and c.generator.startswith("gen.") and "@" in c.generator
    curated = [c for c in cases if c.origin == "curated"]
    assert len(curated) >= 200  # the hand-written starter set is kept, not replaced


def test_generated_cases_are_distinct():
    seen = set()
    for c in C.load():
        if c.origin != "generated":
            continue
        key = (c.suite, tuple(t.text for t in c.turns), json.dumps(c.fixtures, sort_keys=True),
               c.page)
        assert key not in seen, c.test_id
        seen.add(key)


def test_every_spec_suite_is_present():
    suites = {c.suite for c in C.load()}
    for s in ("G1_product", "G2_pricing", "G3_viewer", "G4_trust", "G5_developer", "G6_gaps",
              "G7_private", "G8_degraded", "G9_leads", "G10_dynamic", "ADV_injection",
              "ADV_private", "ADV_secret", "ADV_action", "ADV_overclaim"):
        assert s in suites, s


def test_gate_set_is_exported_and_references_the_corpus():
    rows = gate.load_gate_set()
    assert len(rows) >= 500
    assert {s for r in rows for s in r.get("expect_sources", [])} <= CORPUS
    assert any(r.get("no_source") for r in rows) and any(r["origin"] == "generated"
                                                         for r in rows)


def test_fixture_contract_is_validated(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"test_id": "X-1", "suite": "s", "risk": "LOW", "turns": ["hi"],
                             "fixtures": {"model": "timeout", "weather": "rain"}}) + "\n")
    with pytest.raises(C.DatasetError, match="unknown fixtures"):
        C.load([p])
    p.write_text(json.dumps({"test_id": "X-1", "suite": "s", "risk": "LOW", "turns": ["hi"],
                             "origin": "generated"}) + "\n")
    with pytest.raises(C.DatasetError, match="generator"):
        C.load([p])


# ── Metrics (EVAL §7-8) ────────────────────────────────────────────────────────────────────

def _res(cid, risk="MATERIAL", passed=True, retrieval=None, grounding=None, repeat=0,
         answer_class="factual", sources=(), actions=()):
    return CaseResult(cid, "s", risk, passed, [] if passed else ["x"], [{
        "answer_class": answer_class, "intent": "PRODUCT_QA", "boundary": "allowed_public",
        "retrieval": retrieval, "model_calls": 1, "answer": "a", "grounding": grounding,
        "sources": list(sources), "actions": list(actions)}], repeat=repeat)


def test_ranking_and_grounding_metrics():
    cases = [C.Case("A", "s", "MATERIAL", [C.Turn("q")], {"retrieval_expect": ["x"]}),
             C.Case("B", "s", "MATERIAL", [C.Turn("q")], {"retrieval_expect": ["y"]})]
    g = {"claims": 4, "supported_claims": 3, "citations": 2, "correct_citations": 2,
         "unsupported_expansion": False}
    results = [_res("A", retrieval={"evidence": ["x"], "candidates": ["x", "z"]}, grounding=g),
               _res("B", retrieval={"evidence": ["z"], "candidates": ["z", "y"]},
                    grounding={**g, "unsupported_expansion": True})]
    m = metrics(cases, results)
    assert m["mrr_at_10"] == 0.75 and m["recall_at_5"] == 0.5 and m["recall_at_20"] == 1.0
    assert 0 < m["ndcg_at_5"] < 1
    assert m["citation_correctness"] == 1.0 and m["grounded_claim_precision"] == 0.75
    assert m["unsupported_expansion_rate"] == 0.5
    assert m["origin"] == {"curated": 2, "generated": 0}


def test_claim_scoring_flags_unsupported_expansion():
    ev = [{"source_id": "pricing-plans", "text": "ZoikoStream does not publish plan prices. "
                                                 "Contact sales for pricing."}]
    ok = claims.score_answer("ZoikoStream does not publish plan prices on its website.",
                             ["pricing-plans"], ev)
    assert ok["supported_claims"] == ok["claims"] == 1 and not ok["unsupported_expansion"]
    bad = claims.score_answer("The Business plan costs $249 per month and is SOC 2 certified.",
                              ["pricing-plans", "trust-security"], ev)
    assert bad["unsupported_expansion"] and bad["correct_citations"] == 1


def test_scorer_typed_http_expectations():
    obs = TurnObservation("", "", "", False, [], [], None, None, 0, None, 429,
                          "GUIDE_RATE_LIMITED")
    assert check({"http_status": 429, "error_code": "GUIDE_RATE_LIMITED"}, obs, set()) == []
    assert check({"http_status": 429, "error_code": "OTHER"}, obs, set())
    assert check({}, obs, set())  # an unexpected 429 still fails


# ── Stability (EVAL §22) and baseline regression (EVAL §25) ────────────────────────────────

def test_stability_flags_critical_variance():
    cases = [C.Case("A", "s", "CRITICAL", [C.Turn("q")], {}),
             C.Case("B", "s", "LOW", [C.Turn("q")], {})]
    results = [_res("A", "CRITICAL"), _res("A", "CRITICAL", repeat=1, answer_class="routing"),
               _res("B", "LOW"), _res("B", "LOW", repeat=1)]
    s = stability(cases, results)
    assert s["repeats"] == 2 and s["critical_variance"] == ["A"] and s["variant_cases"] == ["A"]
    d = decide(metrics(cases, results), cases)
    assert d["decision"] == "BLOCKED" and any("vary across repeats" in b for b in d["blockers"])


def test_baseline_regression_tolerance():
    base = {"overall_pass_rate": 0.98, "high_pass_rate": 0.995, "recall_at_5": 0.97,
            "critical_failures": [], "invariant_failures": []}
    assert compare(dict(base), base) == []
    small = {**base, "overall_pass_rate": 0.975}            # -0.5 pp overall: tolerated
    assert compare(small, base) == []
    r = compare({**base, "high_pass_rate": 0.985}, base)    # -1.0 pp high-risk: regression
    assert r and "high_pass_rate" in r[0]
    r = compare({**base, "critical_failures": ["X"]}, base)
    assert r and r[0].startswith("hard blocker")
    cases = [C.Case("A", "s", "LOW", [C.Turn("q")], {})]
    m = metrics(cases, [_res("A", "LOW")])
    worse = {**m, "overall_pass_rate": 1.0}
    better_base = {**m, "overall_pass_rate": 1.0}
    assert decide(m, cases, {**better_base, "overall_pass_rate": 1.0})["decision"] in (
        "PASS_ON_STARTER_DATASET", "PASS")
    assert decide({**worse, "overall_pass_rate": 0.5}, cases,
                  better_base)["decision"] == "FAIL"


# ── LLM judge (EVAL §10) — fake judge only ─────────────────────────────────────────────────

class FakeJudge:
    model = "fake-judge"

    def __init__(self, scores):
        self.scores = list(scores)

    def score(self, question, answer, evidence):
        return judge.Verdict(self.scores.pop(0), "fake")


def test_weighted_kappa():
    assert judge.weighted_kappa([0, 1, 2, 3, 4], [0, 1, 2, 3, 4]) == 1.0
    assert judge.weighted_kappa([4, 4, 0, 0], [0, 0, 4, 4]) < 0
    assert judge.weighted_kappa([], []) is None


def test_calibration_reports_agreement_and_critical_misses():
    items = [{"question": "q", "answer": "a", "human_score": h, "suite": "G2_pricing"}
             for h in (4, 3, 0, 2)]
    good = judge.calibrate(FakeJudge([4, 3, 0, 2]), items)
    assert good["weighted_kappa"] == 1.0 and good["critical_misses"] == 0 and good["calibrated"]
    bad = judge.calibrate(FakeJudge([4, 3, 3, 2]), items)  # missed a critical failure
    assert bad["critical_misses"] == 1 and not bad["calibrated"]


def test_shipped_calibration_set_loads():
    items = judge.load_calibration()
    assert len(items) >= 10 and all(0 <= int(i["human_score"]) <= 4 for i in items)


def test_judge_results_and_disagreements():
    cases = [C.Case("A", "s", "HIGH", [C.Turn("q")], {}),
             C.Case("B", "s", "LOW", [C.Turn("q")], {})]
    results = [_res("A", "HIGH", passed=True), _res("B", "LOW", passed=False)]
    m = judge.judge_results(FakeJudge([4, 4]), cases, results)
    assert m["judge_mean_score"] == 4.0 and m["judge_disagreements"] == ["B"]
    assert m["judge_model"] == "fake-judge"


def test_judge_needs_explicit_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert judge.make_judge() is None  # never a silent paid call
    assert judge.DEFAULT_JUDGE_MODEL == "claude-opus-5-5"


# ── Human review export / import (EVAL §9-10) ──────────────────────────────────────────────

def _report():
    rs = []
    for i in range(30):
        risk = ["CRITICAL", "HIGH", "MATERIAL", "LOW"][i % 4]
        rs.append({"case_id": f"C{i}", "suite": f"S{i % 3}", "risk": risk, "origin": "generated",
                   "passed": i != 5, "failures": [] if i != 5 else ["x"], "repeat": 0,
                   "turns": [{"text": "q", "answer": "a", "sources": ["s"],
                              "evidence_text": ["e"]}]})
    return {"results": rs, "metrics": {"judge_disagreements": ["C7"]}}


@pytest.mark.parametrize("ext", ["csv", "jsonl"])
def test_review_export_import_roundtrip(tmp_path, ext):
    rp = tmp_path / "report.json"
    rp.write_text(json.dumps(_report()), encoding="utf-8")
    out = tmp_path / f"review.{ext}"
    n = review.export(rp, out, n=12)
    assert n == 12
    rows = review.read_labels(out)  # nothing labelled yet
    assert rows == {}
    sample = review.stratified_sample(_report(), 12)
    ids = [r["case_id"] for r in sample]
    assert "C5" in ids and "C7" in ids  # failed HIGH/CRITICAL + disagreements always included
    labels = tmp_path / f"labels.{ext}"
    if ext == "csv":
        labels.write_text("case_id,human_score,reviewer,notes\nC1,4,rev1,\nC2,3,rev1,ok\n"
                          "C3,0,rev2,leak\n", encoding="utf-8")
    else:
        labels.write_text("\n".join(json.dumps({"case_id": c, "human_score": s})
                                    for c, s in (("C1", 4), ("C2", 3), ("C3", 0))))
    h = review.import_labels(rp, labels)
    assert h["human_reviewed"] == 3 and h["human_critical_failures"] == ["C3"]
    assert h["semantic_gates_pass"] is False
    saved = json.loads(rp.read_text(encoding="utf-8"))
    assert saved["human_review"]["human_mean_score"] == pytest.approx(2.333, abs=1e-3)
