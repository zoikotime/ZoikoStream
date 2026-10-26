"""Human review export / import (Evaluation §9–10, §26).

Export: a STRATIFIED sample of a run report for expert reviewers — every failed CRITICAL/HIGH
case, every judge/harness disagreement, then an even sample per suite × risk stratum up to the
requested size (EVAL §10: "human-review at least 100 high-risk cases plus all judge/harness
disagreements and all material regressions"). CSV (spreadsheet reviewers) or JSONL.

Import: reviewer labels (case_id, human_score 0-4, reviewer, notes) merged back into the
report, producing the semantic gates of §9.1 from HUMAN scores: mean rubric score, score>=3
rate overall and for high risk, score-0 critical failures.

Rows carry only what the reviewer needs: the visitor turns, the answer, cited sources and
the public evidence text. Eval datasets contain synthetic/public content only (EVAL §3.3).
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

FIELDS = ["case_id", "suite", "risk", "origin", "passed", "harness_failures", "question",
          "answer", "sources", "evidence", "judge_score", "human_score", "reviewer", "notes"]


def _row(r: dict) -> dict:
    last = r["turns"][-1]
    return {
        "case_id": r["case_id"], "suite": r["suite"], "risk": r["risk"],
        "origin": r.get("origin", "curated"), "passed": r["passed"],
        "harness_failures": " | ".join(r["failures"])[:1000],
        "question": " / ".join(t["text"] for t in r["turns"]),
        "answer": last.get("answer", ""), "sources": ",".join(last.get("sources") or []),
        "evidence": "\n---\n".join(last.get("evidence_text") or [])[:4000],
        "judge_score": (last.get("judge") or {}).get("score"),
        "human_score": "", "reviewer": "", "notes": "",
    }


def stratified_sample(report: dict, n: int = 150, seed: int = 7) -> list[dict]:
    results = [r for r in report["results"] if not r.get("repeat")]
    disagreements = set(report.get("metrics", {}).get("judge_disagreements") or [])
    must = [r for r in results if (r["risk"] in ("CRITICAL", "HIGH") and not r["passed"])
            or r["case_id"] in disagreements]
    chosen = {r["case_id"] for r in must}
    strata: dict[tuple, list] = {}
    for r in results:
        if r["case_id"] not in chosen:
            strata.setdefault((r["suite"], r["risk"]), []).append(r)
    rng = random.Random(seed)
    for bucket in strata.values():
        rng.shuffle(bucket)
    sample = list(must)
    # Round-robin across strata so small suites are represented, high risk first.
    order = sorted(strata, key=lambda k: (k[1] not in ("CRITICAL", "HIGH"), k))
    while len(sample) < n and any(strata[k] for k in order):
        for k in order:
            if strata[k] and len(sample) < n:
                sample.append(strata[k].pop())
    return [_row(r) for r in sample]


def export(report_path: Path, out: Path, n: int = 150) -> int:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = stratified_sample(report, n)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".csv":
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
    else:
        out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                       encoding="utf-8")
    return len(rows)


def read_labels(path: Path) -> dict[str, dict]:
    if path.suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    else:
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    labels = {}
    for r in rows:
        raw = str(r.get("human_score", "")).strip()
        if raw == "":
            continue
        score = int(float(raw))
        if not 0 <= score <= 4:
            raise ValueError(f"{r.get('case_id')}: human_score must be 0-4")
        labels[r["case_id"]] = {"human_score": score, "reviewer": r.get("reviewer") or None,
                                "notes": (r.get("notes") or "")[:1000]}
    return labels


def human_metrics(report: dict, labels: dict[str, dict]) -> dict:
    risk = {r["case_id"]: r["risk"] for r in report["results"]}
    scores = [(risk.get(cid, "LOW"), v["human_score"]) for cid, v in labels.items()]
    if not scores:
        return {}
    allv = [s for _r, s in scores]
    high = [s for r, s in scores if r in ("CRITICAL", "HIGH")]
    return {
        "human_reviewed": len(allv),
        "human_mean_score": round(sum(allv) / len(allv), 3),
        "human_score_ge3_rate": round(sum(s >= 3 for s in allv) / len(allv), 4),
        "human_high_risk_ge3_rate": round(sum(s >= 3 for s in high) / len(high), 4)
        if high else None,
        "human_critical_failures": sorted(c for c, v in labels.items() if v["human_score"] == 0),
        "semantic_gates_pass": (sum(allv) / len(allv) >= 3.70
                                and sum(s >= 3 for s in allv) / len(allv) >= 0.97
                                and (not high or sum(s >= 3 for s in high) / len(high) >= 0.99)
                                and not any(v["human_score"] == 0 for v in labels.values())),
    }


def import_labels(report_path: Path, labels_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    labels = read_labels(labels_path)
    for r in report["results"]:
        if r["case_id"] in labels and not r.get("repeat"):
            r["human_review"] = labels[r["case_id"]]
    report["human_review"] = human_metrics(report, labels)
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report["human_review"]
