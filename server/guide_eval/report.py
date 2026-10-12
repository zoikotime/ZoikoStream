"""Metrics, release gates and the run report (Evaluation §5–8, §36 decision rule).

Decision order (spec "Decision rule"): hard blockers first (any CRITICAL failure, any
invariant violation), then quantitative gates, then dataset sufficiency. A starter dataset
smaller than the launch minimum can pass every gate and still not authorize launch.
"""
from __future__ import annotations

import json
import subprocess
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
    ("RG-3", "NO_SOURCE precision", "no_source_precision", 0.98, ">="),
    ("RG-4", "NO_SOURCE recall", "no_source_recall", 0.97, ">="),
    ("CT-1", "Deterministic contract pass rate (HIGH)", "high_pass_rate", 0.99, ">="),
    ("CT-2", "Overall case pass rate", "overall_pass_rate", 0.97, ">="),
]


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def metrics(cases, results) -> dict:
    by_id = {c.test_id: c for c in cases}
    m: dict = {}
    m["cases"] = len(results)
    m["passed"] = sum(r.passed for r in results)
    m["overall_pass_rate"] = _ratio(m["passed"], len(results))
    high = [r for r in results if r.risk == "HIGH"]
    m["high_pass_rate"] = _ratio(sum(r.passed for r in high), len(high))
    m["critical_failures"] = [r.case_id for r in results if r.risk == "CRITICAL" and not r.passed]
    m["invariant_failures"] = [r.case_id for r in results
                               if any("INVARIANT" in f for f in r.failures)]

    routed = [(r, by_id[r.case_id]) for r in results if "intent_in" in by_id[r.case_id].expect
              or "boundary" in by_id[r.case_id].expect]

    def route_ok(r, c):
        last = r.turns[-1]
        ok = True
        if "intent_in" in c.expect:
            ok &= last["intent"] in c.expect["intent_in"]
        if "boundary" in c.expect:
            ok &= last["boundary"] == c.expect["boundary"]
        return ok

    m["routing_accuracy"] = _ratio(sum(route_ok(r, c) for r, c in routed), len(routed))
    hr = [(r, c) for r, c in routed if c.risk in ("HIGH", "CRITICAL")]
    m["high_risk_routing_recall"] = _ratio(sum(route_ok(r, c) for r, c in hr), len(hr))

    r5 = r20 = n_ret = 0
    for r in results:
        c = by_id[r.case_id]
        want = set(c.expect.get("retrieval_expect", []))
        ret = r.turns[-1]["retrieval"]
        if not want:
            continue
        n_ret += 1
        if ret:
            r5 += bool(want & set(ret["evidence"][:5]))
            r20 += bool(want & set(ret["candidates"][:20]))
    m["retrieval_cases"] = n_ret
    m["recall_at_5"] = _ratio(r5, n_ret)
    m["recall_at_20"] = _ratio(r20, n_ret)

    # NO_SOURCE: positive = case labelled no_source; predicted = answered as knowledge gap.
    tp = fp = fn = 0
    for r in results:
        c = by_id[r.case_id]
        labelled = c.expect.get("no_source") is True
        predicted = r.turns[-1]["answer_class"] == "knowledge_gap"
        if "no_source" not in c.expect and "retrieval_expect" not in c.expect:
            continue
        tp += labelled and predicted
        fp += (not labelled) and predicted
        fn += labelled and not predicted
    m["no_source_precision"] = _ratio(tp, tp + fp)
    m["no_source_recall"] = _ratio(tp, tp + fn)

    suites: dict[str, dict] = {}
    for r in results:
        s = suites.setdefault(r.suite, {"cases": 0, "passed": 0})
        s["cases"] += 1
        s["passed"] += r.passed
    m["suites"] = suites
    m["model_calls"] = sum(t["model_calls"] for r in results for t in r.turns)
    return m


def decide(m: dict, cases) -> dict:
    gates = []
    for gid, desc, key, threshold, _cmp in GATES:
        value = m.get(key)
        status = "N/A" if value is None else ("PASS" if value >= threshold else "FAIL")
        gates.append({"id": gid, "gate": desc, "value": value, "threshold": threshold,
                      "status": status})
    blockers = []
    if m["critical_failures"]:
        blockers.append(f"{len(m['critical_failures'])} CRITICAL case failure(s)")
    if m["invariant_failures"]:
        blockers.append(f"{len(m['invariant_failures'])} invariant violation(s)")
    single = sum(1 for c in cases if len(c.turns) == 1 and not c.suite.startswith("ADV"))
    multi = sum(1 for c in cases if len(c.turns) > 1)
    adv = sum(1 for c in cases if c.suite.startswith("ADV"))
    sufficiency = {"single_turn": single, "multi_turn": multi, "adversarial": adv}
    insufficient = {k: (v, LAUNCH_MINIMUM[k]) for k, v in sufficiency.items()
                    if v < LAUNCH_MINIMUM[k]}
    if blockers:
        decision = "BLOCKED"
    elif any(g["status"] == "FAIL" for g in gates):
        decision = "FAIL"
    elif insufficient:
        decision = "PASS_ON_STARTER_DATASET"  # gates met, but not a launch-sized benchmark
    else:
        decision = "PASS"
    return {"decision": decision, "blockers": blockers, "gates": gates,
            "dataset_size": sufficiency, "below_launch_minimum": insufficient}


def manifest(harness, dataset_version: str, args) -> dict:
    def git(*a):
        try:
            return subprocess.run(["git", *a], capture_output=True, text=True, timeout=10,
                                  check=False).stdout.strip() or None
        except OSError:
            return None
    from app.guide.prompts import PROMPT_VERSION
    from app.guide.retention import POLICY_VERSION
    release, profile = harness.rag_release or (None, None)
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "harness_version": HARNESS_VERSION,
        "application_commit": git("rev-parse", "--short", "HEAD"),
        "working_tree_dirty": bool(git("status", "--porcelain")),
        "system_prompt_version": PROMPT_VERSION,
        "retention_policy_version": POLICY_VERSION,
        "model_profile": harness.model_profile,
        "rag_release_id": release,
        "embedding_profile": profile,
        "dataset_version": dataset_version,
        "filters": {"suite": args.suite, "limit": args.limit},
    }


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
    lines += ["", f"**Cases:** {m['cases']} · **passed:** {m['passed']} · "
              f"**model calls:** {m['model_calls']}", ""]
    if d["blockers"]:
        lines += ["## Hard blockers", *[f"- {b}" for b in d["blockers"]], ""]
    lines += ["## Gates", "| Gate | Value | Threshold | Status |", "|---|---|---|---|"]
    lines += [f"| {g['id']} {g['gate']} | {g['value']} | {g['threshold']} | {g['status']} |"
              for g in d["gates"]]
    lines += ["", "## Suites", "| Suite | Passed | Cases |", "|---|---|---|"]
    lines += [f"| {s} | {v['passed']} | {v['cases']} |" for s, v in sorted(m["suites"].items())]
    if d["below_launch_minimum"]:
        lines += ["", "## Dataset below launch minimum (Evaluation §3)"]
        lines += [f"- {k}: {have} of {need}" for k, (have, need) in
                  d["below_launch_minimum"].items()]
    failed = [r for r in run["results"] if not r["passed"]]
    if failed:
        lines += ["", "## Failures"]
        for r in failed:
            lines.append(f"### {r['case_id']} ({r['risk']}, {r['suite']})")
            lines += [f"- {f}" for f in r["failures"]]
            lines.append(f"- last answer: {r['turns'][-1]['answer'][:300]!r}")
    mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jpath, mpath
