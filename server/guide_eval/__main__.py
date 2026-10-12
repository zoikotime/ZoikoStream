"""CLI: python -m guide_eval [--provider mock|groq|anthropic] [--suite S] [--limit N]
[--pace SECONDS] [--database-url URL] [--list]

Exit code 0 only when the decision is PASS or PASS_ON_STARTER_DATASET, so CI can gate on it.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from . import cases as case_mod
from .runner import configure_environment

REPORTS = Path(__file__).resolve().parent / "reports"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="guide_eval")
    ap.add_argument("--provider", choices=["mock", "groq", "anthropic"],
                    help="override GUIDE_LLM_PROVIDER for this run")
    ap.add_argument("--suite", help="only run suites whose name starts with this")
    ap.add_argument("--limit", type=int, help="max cases to run")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="seconds to wait after each model call (provider rate limits)")
    ap.add_argument("--database-url", help="Guide database to run against (default: env)")
    ap.add_argument("--list", action="store_true", help="print dataset inventory and exit")
    args = ap.parse_args(argv)

    cases = case_mod.load()
    if args.suite:
        cases = [c for c in cases if c.suite.startswith(args.suite)]
    if args.limit:
        cases = cases[: args.limit]
    if args.list:
        inventory: dict[str, int] = {}
        for c in case_mod.load():
            inventory[c.suite] = inventory.get(c.suite, 0) + 1
        for suite, n in sorted(inventory.items()):
            print(f"{suite:<28}{n:>5}")
        print(f"{'TOTAL':<28}{sum(inventory.values()):>5}  dataset {case_mod.dataset_version()}")
        return 0
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2

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
    for n, case in enumerate(cases, 1):
        r = harness.run_case(case, check, harness.known_sources)
        results.append(r)
        mark = "ok  " if r.passed else "FAIL"
        print(f"[{n:>4}/{len(cases)}] {mark} {case.test_id:<10} {case.suite}", flush=True)
        for f in r.failures:
            print(f"            - {f}", flush=True)

    m = metrics(cases, results)
    d = decide(m, cases)
    run = {"manifest": manifest(harness, case_mod.dataset_version(), args), "metrics": m,
           "decision": d, "results": [asdict(r) for r in results]}
    jpath, mpath = write(REPORTS, run)
    print(f"\nDecision: {d['decision']}  ({m['passed']}/{m['cases']} cases passed)")
    for b in d["blockers"]:
        print(f"  BLOCKER: {b}")
    for g in d["gates"]:
        print(f"  {g['id']:<5} {g['gate']:<42} {str(g['value']):>8}  {g['status']}")
    print(f"Report: {mpath}")
    return 0 if d["decision"] in ("PASS", "PASS_ON_STARTER_DATASET") else 1


if __name__ == "__main__":
    raise SystemExit(main())
