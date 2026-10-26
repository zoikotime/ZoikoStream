"""CLI.

  python -m guide_eval [--provider mock|groq|anthropic] [--suite S] [--limit N] [--pace SECONDS]
                       [--database-url URL] [--origin curated|generated] [--repeats N]
                       [--baseline REPORT.json] [--judge [--judge-model M]]
  python -m guide_eval --list                         dataset inventory (curated vs generated)
  python -m guide_eval gate [--database-url URL]      deterministic retrieval/routing gate
                                                      (no model; CI blocking job)
  python -m guide_eval calibrate                      judge vs human calibration set (§10.1)
  python -m guide_eval review-export REPORT OUT.csv|OUT.jsonl [--sample N]
  python -m guide_eval review-import REPORT LABELS.csv|LABELS.jsonl

Exit code 0 only when the decision is PASS or PASS_ON_STARTER_DATASET (or the gate passes),
so CI can gate on it. The LLM judge runs only with --judge AND ANTHROPIC_API_KEY set.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import cases as case_mod
from .runner import configure_environment

REPORTS = Path(__file__).resolve().parent / "reports"


def _inventory() -> int:
    inventory: dict[str, list[int]] = {}
    for c in case_mod.load():
        row = inventory.setdefault(c.suite, [0, 0])
        row[0 if c.origin == "curated" else 1] += 1
    print(f"{'SUITE':<26}{'curated':>9}{'generated':>11}")
    for suite, (cur, gen) in sorted(inventory.items()):
        print(f"{suite:<26}{cur:>9}{gen:>11}")
    cur = sum(v[0] for v in inventory.values())
    gen = sum(v[1] for v in inventory.values())
    print(f"{'TOTAL':<26}{cur:>9}{gen:>11}  dataset {case_mod.dataset_version()}")
    return 0


def _gate(args) -> int:
    configure_environment(None, args.database_url)
    from app.guide.db import new_session
    from app.guide.knowledge import gate, retrieval
    db = new_session()
    try:
        active = retrieval.active_release(db)
        if active is None:
            print("no active knowledge index; run: python -m app.guide.knowledge.ingest",
                  file=sys.stderr)
            return 2
        result = gate.evaluate(db, active[0], include_routing=True)
        db.rollback()
    finally:
        db.close()
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "retrieval-gate.json"
    out.write_text(json.dumps(result.summary(), indent=2), encoding="utf-8")
    print(f"Retrieval gate: {'PASS' if result.passed else 'FAIL'}  ({result.gate_set}, "
          f"release {active[0]})")
    for k, v in sorted(result.metrics.items()):
        t = gate.THRESHOLDS.get(k)
        print(f"  {k:<24}{v!s:>8}" + (f"   >= {t}" if t is not None else ""))
    for f in result.failures[:60]:
        print(f"  - {f}")
    print(f"Report: {out}")
    if result.gate_set == "not_applicable":
        print("gate set not applicable: the active release is not the approved corpus",
              file=sys.stderr)
        return 1
    return 0 if result.passed else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "gate":
        ap = argparse.ArgumentParser(prog="guide_eval gate")
        ap.add_argument("--database-url")
        return _gate(ap.parse_args(argv[1:]))
    if argv and argv[0] == "review-export":
        ap = argparse.ArgumentParser(prog="guide_eval review-export")
        ap.add_argument("report", type=Path)
        ap.add_argument("out", type=Path)
        ap.add_argument("--sample", type=int, default=150)
        a = ap.parse_args(argv[1:])
        from .review import export
        print(f"exported {export(a.report, a.out, a.sample)} rows to {a.out}")
        return 0
    if argv and argv[0] == "review-import":
        ap = argparse.ArgumentParser(prog="guide_eval review-import")
        ap.add_argument("report", type=Path)
        ap.add_argument("labels", type=Path)
        a = ap.parse_args(argv[1:])
        from .review import import_labels
        print(json.dumps(import_labels(a.report, a.labels), indent=2))
        return 0
    if argv and argv[0] == "calibrate":
        from .judge import calibrate, make_judge
        judge = make_judge()
        if judge is None:
            print("calibration needs ANTHROPIC_API_KEY (the judge is a paid model call)",
                  file=sys.stderr)
            return 2
        print(json.dumps(calibrate(judge), indent=2))
        return 0

    ap = argparse.ArgumentParser(prog="guide_eval")
    ap.add_argument("--provider", choices=["mock", "groq", "anthropic"],
                    help="override GUIDE_LLM_PROVIDER for this run")
    ap.add_argument("--suite", help="only run suites whose name starts with this")
    ap.add_argument("--origin", choices=["curated", "generated"])
    ap.add_argument("--limit", type=int, help="max cases to run")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="seconds to wait after each model call (provider rate limits)")
    ap.add_argument("--database-url", help="Guide database to run against (default: env)")
    ap.add_argument("--repeats", type=int, default=1,
                    help="stability mode: run each case N times and report variance (§22)")
    ap.add_argument("--baseline", type=Path,
                    help="last-known-good report JSON; fail on regression beyond tolerance")
    ap.add_argument("--judge", action="store_true",
                    help="LLM-as-judge rubric scoring (needs ANTHROPIC_API_KEY; paid)")
    ap.add_argument("--judge-model", help="judge model (default claude-opus-5-5)")
    ap.add_argument("--list", action="store_true", help="print dataset inventory and exit")
    args = ap.parse_args(argv)

    if args.list:
        return _inventory()
    cases = case_mod.load()
    if args.suite:
        cases = [c for c in cases if c.suite.startswith(args.suite)]
    if args.origin:
        cases = [c for c in cases if c.origin == args.origin]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2
    baseline = None
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8")).get("metrics", {})

    configure_environment(args.provider, args.database_url)
    from .report import decide, manifest, metrics, write
    from .runner import Harness
    from .scorers import check

    harness = Harness(pace=args.pace)
    if harness.rag_release is None:
        print("no active knowledge index; run: python -m app.guide.knowledge.ingest",
              file=sys.stderr)
        return 2
    results = []
    repeats = max(1, args.repeats)
    for n, case in enumerate(cases, 1):
        for rep in range(repeats):
            r = harness.run_case(case, check, harness.known_sources, repeat=rep)
            results.append(r)
            if rep:
                continue
            mark = "ok  " if r.passed else "FAIL"
            print(f"[{n:>4}/{len(cases)}] {mark} {case.test_id:<12} {case.suite}", flush=True)
            for f in r.failures:
                print(f"            - {f}", flush=True)

    m = metrics(cases, results)
    _gate_metrics(m)
    run_manifest = manifest(harness, case_mod.dataset_version(), args)
    if args.judge:
        from .judge import judge_results, make_judge
        judge = make_judge(args.judge_model)
        if judge is None:
            print("--judge ignored: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        else:
            m.update(judge_results(judge, cases, results))
            run_manifest["judge_model"] = judge.model
    d = decide(m, cases, baseline)
    run = {"manifest": run_manifest, "metrics": m, "decision": d,
           "results": [asdict(r) for r in results]}
    if args.baseline:
        run["manifest"]["baseline"] = str(args.baseline)
    jpath, mpath = write(REPORTS, run)
    print(f"\nDecision: {d['decision']}  ({m['passed']}/{m['cases']} cases passed; "
          f"{m['origin']['curated']} curated, {m['origin']['generated']} generated)")
    for b in d["blockers"]:
        print(f"  BLOCKER: {b}")
    for b in d.get("regressions", []):
        print(f"  REGRESSION: {b}")
    for g in d["gates"]:
        print(f"  {g['id']:<5} {g['gate']:<48} {str(g['value']):>8}  {g['status']}")
    print(f"Report: {mpath}")
    return 0 if d["decision"] in ("PASS", "PASS_ON_STARTER_DATASET") else 1


def _gate_metrics(m: dict) -> None:
    """Conflict / stale recall come from the deterministic index gate run against the active
    release (they need fixtures the live corpus does not contain)."""
    try:
        from app.guide.db import new_session
        from app.guide.knowledge import gate, retrieval
        db = new_session()
        try:
            active = retrieval.active_release(db)
            if active:
                sources = gate._release_sources(db, active[0])
                m["conflict_recall"], _fp = gate._conflict_selftest(sources)
                m["stale_recall"] = gate._stale_selftest(db, active[0], sources)
            db.rollback()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — reported, never fatal
        print(f"conflict/stale self-test skipped: {type(exc).__name__}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
