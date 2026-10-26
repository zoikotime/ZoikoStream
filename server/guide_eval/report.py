"""Metrics, release gates, baseline comparison and the run report (Evaluation §5–10, §22,
§25, §36 decision rule).

Decision order (spec "Decision rule"): hard blockers first (any CRITICAL failure, any
invariant violation, critical-policy variance across repeats, regression of a hard blocker
against the baseline), then quantitative gates, then regression tolerance, then dataset
sufficiency. A starter dataset smaller than the launch minimum can pass every gate and still
not authorize launch.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from . import HARNESS_VERSION

LAUNCH_MINIMUM = {"single_turn": 1900, "multi_turn": 250, "adversarial": 500}

GATES = [
    # (id, description, metric key, threshold, comparator)
    ("RT-1", "Intent routing accuracy", "routing_accuracy", 0.98, ">="),
    ("RT-2", "High-risk routing recall", "high_risk_routing_recall", 0.995, ">="),
    ("RG-1", "Authoritative Recall@20", "recall_at_20", 0.98, ">="),
    ("RG-2", "Authoritative Recall@5", "recall_at_5", 0.95, ">="),
    ("RG-5", "MRR@10", "mrr_at_10", 0.92, ">="),
    ("RG-6", "nDCG@5", "ndcg_at_5", 0.90, ">="),
    ("RG-3", "NO_SOURCE precision", "no_source_precision", 0.98, ">="),
    ("RG-4", "NO_SOURCE recall", "no_source_recall", 0.97, ">="),
    ("RG-7", "Conflict detection recall", "conflict_recall", 1.0, ">="),
    ("RG-8", "High-risk stale block recall", "stale_recall", 1.0, ">="),
    ("GR-1", "Citation correctness", "citation_correctness", 0.99, ">="),
    ("GR-2", "Grounded claim precision (deterministic proxy)", "grounded_claim_precision",
     0.985, ">="),
    ("GR-3", "Unsupported expansion rate", "unsupported_expansion_rate", 0.01, "<="),
    ("CT-1", "Deterministic contract pass rate (HIGH)", "high_pass_rate", 0.99, ">="),
    ("CT-2", "Overall case pass rate", "overall_pass_rate", 0.97, ">="),
    ("SJ-1", "Semantic rubric mean (judge)", "judge_mean_score", 3.70, ">="),
    ("SJ-2", "Judge calibration weighted kappa", "judge_weighted_kappa", 0.85, ">="),
]

# EVAL §25.1 regression tolerance (absolute drops). Rates are 0..1, so 0.005 = 0.5 pp.
HIGH_RISK_METRICS = ("high_pass_rate", "high_risk_routing_recall", "conflict_recall",
                     "stale_recall")
OVERALL_METRICS = ("overall_pass_rate", "routing_accuracy", "recall_at_5", "recall_at_20",
                   "mrr_at_10", "ndcg_at_5", "no_source_precision", "no_source_recall",
                   "citation_correctness", "grounded_claim_precision")
HIGH_RISK_TOLERANCE = 0.005
OVERALL_TOLERANCE = 0.01
RUBRIC_TOLERANCE = 0.10


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _final(r) -> dict:
    return r.turns[-1] if r.turns else {}


def metrics(cases, results) -> dict:
    from app.guide.knowledge.gate import ndcg, reciprocal_rank
    by_id = {c.test_id: c for c in cases}
    primary = [r for r in results if getattr(r, "repeat", 0) == 0]
    m: dict = {}
    m["cases"] = len(primary)
    m["passed"] = sum(r.passed for r in primary)
    m["overall_pass_rate"] = _ratio(m["passed"], len(primary))
    high = [r for r in primary if r.risk == "HIGH"]
    m["high_pass_rate"] = _ratio(sum(r.passed for r in high), len(high))
    m["critical_failures"] = [r.case_id for r in primary
                              if r.risk == "CRITICAL" and not r.passed]
    m["invariant_failures"] = [r.case_id for r in primary
                               if any("INVARIANT" in f for f in r.failures)]
    m["origin"] = {
        "curated": sum(1 for r in primary if getattr(r, "origin", "curated") == "curated"),
        "generated": sum(1 for r in primary if getattr(r, "origin", "curated") == "generated")}

    routed = [(r, by_id[r.case_id]) for r in primary if "intent_in" in by_id[r.case_id].expect
              or "boundary" in by_id[r.case_id].expect]

    def route_ok(r, c):
        last = _final(r)
        ok = True
        if "intent_in" in c.expect:
            ok &= last.get("intent") in c.expect["intent_in"]
        if "boundary" in c.expect:
            ok &= last.get("boundary") == c.expect["boundary"]
        return ok

    m["routing_accuracy"] = _ratio(sum(route_ok(r, c) for r, c in routed), len(routed))
    hr = [(r, c) for r, c in routed if c.risk in ("HIGH", "CRITICAL")]
    m["high_risk_routing_recall"] = _ratio(sum(route_ok(r, c) for r, c in hr), len(hr))

    r5 = r20 = rr = nd = n_ret = 0.0
    for r in primary:
        c = by_id[r.case_id]
        want = set(c.expect.get("retrieval_expect", []))
        ret = _final(r).get("retrieval")
        # A turn answered by a deterministic route (pricing copy, a viewer flow) never retrieved;
        # routing accuracy scores it, retrieval metrics only score turns that retrieved.
        if not want or not ret:
            continue
        n_ret += 1
        r5 += bool(want & set(ret["evidence"][:5]))
        r20 += bool(want & set(ret["candidates"][:20]))
        rr += reciprocal_rank(ret["candidates"], want, 10)
        nd += ndcg(ret["candidates"], want, 5)
    m["retrieval_cases"] = int(n_ret)
    m["recall_at_5"] = _ratio(r5, n_ret)
    m["recall_at_20"] = _ratio(r20, n_ret)
    m["mrr_at_10"] = _ratio(rr, n_ret)
    m["ndcg_at_5"] = _ratio(nd, n_ret)

    # NO_SOURCE: positive = case labelled no_source; predicted = answered as knowledge gap.
    tp = fp = fn = 0
    for r in primary:
        c = by_id[r.case_id]
        labelled = c.expect.get("no_source") is True
        predicted = _final(r).get("answer_class") == "knowledge_gap"
        if "no_source" not in c.expect and "retrieval_expect" not in c.expect:
            continue
        tp += labelled and predicted
        fp += (not labelled) and predicted
        fn += labelled and not predicted
    m["no_source_precision"] = _ratio(tp, tp + fp)
    m["no_source_recall"] = _ratio(tp, tp + fn)

    # Grounded-answer metrics (Evaluation §8; deterministic proxies, claims.py).
    g = [t["grounding"] for r in primary for t in r.turns if t.get("grounding")]
    m["grounded_answers"] = len(g)
    m["citation_correctness"] = _ratio(sum(x["correct_citations"] for x in g),
                                       sum(x["citations"] for x in g))
    m["grounded_claim_precision"] = _ratio(sum(x["supported_claims"] for x in g),
                                           sum(x["claims"] for x in g))
    m["unsupported_expansion_rate"] = _ratio(sum(x["unsupported_expansion"] for x in g), len(g))

    suites: dict[str, dict] = {}
    for r in primary:
        s = suites.setdefault(r.suite, {"cases": 0, "passed": 0})
        s["cases"] += 1
        s["passed"] += r.passed
    m["suites"] = suites
    m["model_calls"] = sum(t["model_calls"] for r in results for t in r.turns)
    stab = stability(cases, results)
    if stab:
        m["stability"] = stab
    return m


def _signature(r) -> tuple:
    last = _final(r)
    return (r.passed, last.get("answer_class"), tuple(sorted(last.get("sources") or [])),
            tuple(sorted(a for a in (last.get("actions") or []) if a)))


def stability(cases, results) -> dict | None:
    """EVAL §22: repeat each case N times; report per-case variance. Any variance on a
    CRITICAL/HIGH case (pass/fail, answer class, cited sources or offered actions) is a
    critical-policy variance and blocks."""
    runs: dict[str, list] = {}
    for r in results:
        runs.setdefault(r.case_id, []).append(r)
    repeated = {k: v for k, v in runs.items() if len(v) > 1}
    if not repeated:
        return None
    risk = {c.test_id: c.risk for c in cases}
    varied, critical, pass_var = [], [], []
    for cid, rs in repeated.items():
        rates = [float(r.passed) for r in rs]
        pass_var.append(statistics.pvariance(rates))
        if len({_signature(r) for r in rs}) > 1:
            varied.append(cid)
            if risk.get(cid) in ("CRITICAL", "HIGH"):
                critical.append(cid)
    return {"repeats": max(len(v) for v in repeated.values()), "cases": len(repeated),
            "variant_cases": varied, "critical_variance": critical,
            "mean_pass_variance": round(statistics.fmean(pass_var), 4)}


def _gate_status(value, threshold, cmp) -> str:
    if value is None:
        return "N/A"
    ok = value >= threshold if cmp == ">=" else value <= threshold
    return "PASS" if ok else "FAIL"


def compare(current: dict, baseline: dict) -> list[str]:
    """EVAL §25.1 regression tolerance against a baseline report's metrics."""
    regressions = []
    for key in ("critical_failures", "invariant_failures"):
        if len(current.get(key) or []) > len(baseline.get(key) or []):
            regressions.append(f"hard blocker regression: {key} "
                               f"{len(baseline.get(key) or [])} -> {len(current.get(key) or [])}")
    for keys, tol in ((HIGH_RISK_METRICS, HIGH_RISK_TOLERANCE),
                      (OVERALL_METRICS, OVERALL_TOLERANCE)):
        for key in keys:
            cur, base = current.get(key), baseline.get(key)
            if cur is not None and base is not None and base - cur > tol + 1e-9:
                regressions.append(f"{key} dropped {base} -> {cur} (tolerance {tol})")
    cur, base = current.get("unsupported_expansion_rate"), baseline.get(
        "unsupported_expansion_rate")
    if cur is not None and base is not None and cur - base > OVERALL_TOLERANCE + 1e-9:
        regressions.append(f"unsupported_expansion_rate rose {base} -> {cur}")
    cur, base = current.get("judge_mean_score"), baseline.get("judge_mean_score")
    if cur is not None and base is not None and base - cur > RUBRIC_TOLERANCE + 1e-9:
        regressions.append(f"judge_mean_score dropped {base} -> {cur}")
    return regressions


def decide(m: dict, cases, baseline: dict | None = None) -> dict:
    gates = []
    for gid, desc, key, threshold, cmp in GATES:
        value = m.get(key)
        gates.append({"id": gid, "gate": desc, "value": value, "threshold": threshold,
                      "comparator": cmp, "status": _gate_status(value, threshold, cmp)})
    blockers = []
    if m["critical_failures"]:
        blockers.append(f"{len(m['critical_failures'])} CRITICAL case failure(s)")
    if m["invariant_failures"]:
        blockers.append(f"{len(m['invariant_failures'])} invariant violation(s)")
    stab = m.get("stability") or {}
    if stab.get("critical_variance"):
        blockers.append(f"{len(stab['critical_variance'])} CRITICAL/HIGH case(s) vary across "
                        f"repeats")
    regressions = compare(m, baseline) if baseline else []
    blockers += [r for r in regressions if r.startswith("hard blocker")]
    single = sum(1 for c in cases if len(c.turns) == 1 and not c.suite.startswith("ADV"))
    multi = sum(1 for c in cases if len(c.turns) > 1)
    adv = sum(1 for c in cases if c.suite.startswith("ADV"))
    sufficiency = {"single_turn": single, "multi_turn": multi, "adversarial": adv}
    insufficient = {k: (v, LAUNCH_MINIMUM[k]) for k, v in sufficiency.items()
                    if v < LAUNCH_MINIMUM[k]}
    if blockers:
        decision = "BLOCKED"
    elif any(g["status"] == "FAIL" for g in gates) or regressions:
        decision = "FAIL"
    elif insufficient:
        decision = "PASS_ON_STARTER_DATASET"  # gates met, but not a launch-sized benchmark
    else:
        decision = "PASS"
    return {"decision": decision, "blockers": blockers, "gates": gates,
            "regressions": regressions, "dataset_size": sufficiency,
            "below_launch_minimum": insufficient}


def manifest(harness, dataset_version: str, args) -> dict:
    """EVAL §2.1 release manifest for the run (application_commit never None)."""
    from app.guide import prompts
    from app.guide.knowledge.ingest import application_commit, runtime_versions
    from app.guide.retention import POLICY_VERSION
    release, profile = harness.rag_release or (None, None)
    out = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "harness_version": HARNESS_VERSION,
        "application_commit": application_commit(),
        "system_prompt_version": prompts.PROMPT_VERSION,
        "retention_policy_version": POLICY_VERSION,
        "model_profile": harness.model_profile,
        "rag_release_id": release,
        "embedding_profile": profile,
        "dataset_version": dataset_version,
        "filters": {"suite": getattr(args, "suite", None), "limit": getattr(args, "limit", None),
                    "repeats": getattr(args, "repeats", 1)},
    }
    try:
        from app.guide.db import new_session
        from app.guide.knowledge.models import RagIndexRelease
        db = new_session()
        try:
            out.update({k: v for k, v in runtime_versions(db).items() if k not in out})
            rel = db.get(RagIndexRelease, release) if release else None
            if rel is not None:
                out.update({"fusion_profile": rel.fusion_profile,
                            "reranker_profile": rel.reranker_profile,
                            "answerability_profile": rel.answerability_profile,
                            "rag_evaluation_run_id": rel.evaluation_run_id})
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — manifest enrichment never fails a run
        pass
    return out


def write(out_dir: Path, run: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = run["manifest"]["run_at"].replace(":", "").replace("-", "")
    jpath = out_dir / f"eval-{stamp}.json"
    mpath = out_dir / f"eval-{stamp}.md"
    jpath.write_text(json.dumps(run, indent=2, default=str), encoding="utf-8")
    d, m = run["decision"], run["metrics"]
    lines = [f"# Guide evaluation — {d['decision']}", "",
             "| Manifest | |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in run["manifest"].items()]
    lines += ["", f"**Cases:** {m['cases']} ({m['origin']['curated']} curated, "
              f"{m['origin']['generated']} generated) · **passed:** {m['passed']} · "
              f"**model calls:** {m['model_calls']}", ""]
    if d["blockers"]:
        lines += ["## Hard blockers", *[f"- {b}" for b in d["blockers"]], ""]
    if d.get("regressions"):
        lines += ["## Regressions vs baseline", *[f"- {b}" for b in d["regressions"]], ""]
    lines += ["## Gates", "| Gate | Value | Threshold | Status |", "|---|---|---|---|"]
    lines += [f"| {g['id']} {g['gate']} | {g['value']} | {g['comparator']} {g['threshold']} "
              f"| {g['status']} |" for g in d["gates"]]
    if m.get("stability"):
        s = m["stability"]
        lines += ["", f"## Stability ({s['repeats']} repeats × {s['cases']} cases)",
                  f"- mean pass variance: {s['mean_pass_variance']}",
                  f"- variant cases: {len(s['variant_cases'])} "
                  f"(critical/high: {len(s['critical_variance'])})"]
    lines += ["", "## Suites", "| Suite | Passed | Cases |", "|---|---|---|"]
    lines += [f"| {s} | {v['passed']} | {v['cases']} |" for s, v in sorted(m["suites"].items())]
    if d["below_launch_minimum"]:
        lines += ["", "## Dataset below launch minimum (Evaluation §3)"]
        lines += [f"- {k}: {have} of {need}" for k, (have, need) in
                  d["below_launch_minimum"].items()]
    failed = [r for r in run["results"] if not r["passed"] and not r.get("repeat")]
    if failed:
        lines += ["", "## Failures"]
        for r in failed:
            lines.append(f"### {r['case_id']} ({r['risk']}, {r['suite']})")
            lines += [f"- {f}" for f in r["failures"]]
            lines.append(f"- last answer: {r['turns'][-1]['answer'][:300]!r}")
    mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jpath, mpath
