"""Index activation gate: retrieval smoke tests + deterministic offline evaluation of a
CANDIDATE release before it may become active (RAG §23 "lineage validation -> retrieval smoke
tests -> offline evaluation -> READY -> atomic activate", RAG-AT-21, RG-06/07/10, EVAL §7).

No model is called. Everything here is retrieval + routing over the candidate release, pinned
by release_id, so the active release keeps serving while the candidate is judged.

Checks (each a named metric with a launch threshold):
  * smoke            every source is retrievable for its own title/first section under an
                     intent whose class allowlist admits it;
  * probes           obviously off-corpus questions return NO_SOURCE / LOW_CONFIDENCE;
  * routing          gate-set intent/boundary routing accuracy (EVAL §6, RT-1);
  * recall@5 / @20   authoritative source in the evidence / candidate set (EVAL §7, RG-1/2);
  * MRR@10, nDCG@5   ranking quality of the authoritative source (EVAL §7);
  * NO_SOURCE p / r  the release declines unsupported questions and answers supported ones;
  * leak             no evidence outside the intent's allowed classes (EVAL §7, must be 0);
  * conflict recall  the conflict detector, run with the CANDIDATE's own K2 authority ranks,
                     flags disagreeing authoritative prices (100% mandatory set);
  * stale recall     every HIGH-risk source, made stale inside a rolled-back savepoint, is
                     blocked before ranking (100%).

The gate set (gate/retrieval_gate.jsonl) is exported from the guide_eval datasets by
`python -m guide_eval.build_datasets`. It references the approved public corpus, so it only
applies when the candidate contains every source it names; any other corpus (unit-test
fixtures, a partial rebuild) is judged on the corpus-agnostic checks alone, and the evaluation
record says so.
"""
from __future__ import annotations

import json
import math
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import retrieval

GATE_SET = Path(__file__).resolve().parent / "gate" / "retrieval_gate.jsonl"
GATE_VERSION = "gate-v1"
# "Declines" at the retrieval layer. LOW_CONFIDENCE is an answer with reduced posture, not a
# decline, so it counts as answered for NO_SOURCE precision/recall.
UNSUPPORTED_STATES = ("NO_SOURCE", "STALE_HIGH_RISK")

# EVAL §6/§7 launch gates. MRR@10 / nDCG@5 are gated at the EVAL values.
THRESHOLDS = {
    "smoke_pass_rate": 1.0,
    "probe_pass_rate": 1.0,
    "routing_accuracy": 0.98,
    "recall_at_20": 0.98,
    "recall_at_5": 0.95,
    "mrr_at_10": 0.92,
    "ndcg_at_5": 0.90,
    "no_source_precision": 0.98,
    "no_source_recall": 0.97,
    "conflict_recall": 1.0,
    "stale_recall": 1.0,
}
MAX_LEAKS = 0
# Routing accuracy is a property of the intent classifier (policy code), not of the index: it
# is measured and reported here, and BLOCKS in the CI evaluation gate (guide_eval gate), but an
# index activation is judged on index-dependent metrics only.
NOT_INDEX_DEPENDENT = {"routing_accuracy"}

# Off-corpus probes valid for ANY public corpus (RAG-AT-19).
PROBES = ("zebra quantum basketball tournament", "What is the capital of France?",
          "Write me a poem about the sea", "Who won the World Cup in 2022?")

# A retrieval intent that admits each class, for smoke queries.
CLASS_INTENT = {"K1": "PRODUCT_QA", "K2": "PRICING", "K3": "PLAYBACK", "K4": "SECURITY_TRUST",
                "K5": "PRIVACY", "K6": "PLAYBACK", "K7": "DEVELOPER", "K8": "SALES"}


@dataclass
class GateResult:
    evaluation_run_id: str
    release_id: str
    passed: bool
    metrics: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    gate_set: str = "not_applicable"
    gate_version: str = GATE_VERSION
    evaluated_at: str = ""

    def summary(self) -> dict:
        return asdict(self)


# ── Metric math (shared with guide_eval) ───────────────────────────────────────────────────

def reciprocal_rank(ranked: list[str], relevant: set[str], k: int = 10) -> float:
    for i, s in enumerate(ranked[:k], 1):
        if s in relevant:
            return 1.0 / i
    return 0.0


def ndcg(ranked: list[str], relevant: set[str], k: int = 5) -> float:
    """Binary-relevance nDCG over DISTINCT sources in rank order."""
    seen, gains = set(), []
    for s in ranked:
        if s in seen:
            continue
        seen.add(s)
        gains.append(1.0 if s in relevant else 0.0)
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains[:k]))
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / ideal if ideal else 0.0


def ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def load_gate_set(path: Path = GATE_SET) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            rows.append(json.loads(line))
    return rows


# ── Evaluation ─────────────────────────────────────────────────────────────────────────────

def _release_sources(db: Session, release_id: str) -> list[dict]:
    rows = db.execute(text("""
        SELECT DISTINCT ON (u.source_id) u.source_id, s.source_type, s.title, s.risk_class,
               s.authority_rank, u.heading_path, u.source_version_id, u.jurisdiction
          FROM guide_rag.rag_knowledge_units u
          JOIN guide_knowledge_sources s ON s.source_id = u.source_id
         WHERE u.release_id = :r
         ORDER BY u.source_id, u.ordinal"""), {"r": release_id}).mappings().all()
    return [dict(r) for r in rows]


def evaluate(db: Session, release_id: str, *, gate_set: list[dict] | None = None,
             include_routing: bool = False) -> GateResult:
    from .. import policy
    result = GateResult(evaluation_run_id=f"eval-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-"
                                          f"{secrets.token_hex(3)}",
                        release_id=release_id, passed=False,
                        evaluated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    m = result.metrics
    sources = _release_sources(db, release_id)
    source_ids = {s["source_id"] for s in sources}

    def run(query: str, intent: str, refine: bool = True,
            jurisdiction: str | None = None) -> retrieval.EvidencePack:
        return retrieval.retrieve(db, query, intent, release_id=release_id, refine=refine,
                                  jurisdiction=jurisdiction)

    # Smoke: each source retrievable under an intent that admits its class.
    ok = 0
    for s in sources:
        cls = retrieval.SOURCE_CLASS.get(s["source_type"])
        intent = CLASS_INTENT.get(cls)
        if intent is None:
            result.failures.append(f"smoke: {s['source_id']} has no public class")
            continue
        # Unrefined: the smoke test checks the class allowlist path itself.
        pack = run(s["heading_path"].replace(" > ", " "), intent, refine=False,
                   jurisdiction=s["jurisdiction"])
        if s["source_id"] in pack.candidates[:20]:
            ok += 1
        else:
            result.failures.append(f"smoke: {s['source_id']} not retrievable")
    m["smoke_pass_rate"] = ratio(ok, len(sources))

    ok = sum(run(q, "PRODUCT_QA").answerability in ("NO_SOURCE", "LOW_CONFIDENCE")
             for q in PROBES)
    m["probe_pass_rate"] = ratio(ok, len(PROBES))
    if ok < len(PROBES):
        result.failures.append(f"probes: {len(PROBES) - ok} off-corpus probe(s) answered")

    # Gate set (approved corpus only).
    cases = load_gate_set() if gate_set is None else gate_set
    referenced = {s for c in cases for s in c.get("expect_sources", [])}
    leaks = 0
    if cases and referenced <= source_ids:
        result.gate_set = f"{len(cases)} cases"
        routed = routed_ok = 0
        r5 = r20 = rr = nd = n_ret = 0.0
        tp = fp = fn = 0
        for c in cases:
            q = c["query"]
            boundary = policy.classify_boundary(q).state
            intent = policy.classify_intent(q)
            if c.get("intent_in") or c.get("boundary"):
                routed += 1
                routed_ok += ((not c.get("intent_in") or intent in c["intent_in"])
                              and (not c.get("boundary") or boundary == c["boundary"]))
            if boundary != "allowed_public":
                continue  # restricted before retrieval; nothing to score here
            pack = run(q, intent)
            allowed = set(retrieval.INTENT_SOURCES.get(pack.retrieval_intent or intent, ()))
            leaks += sum(e.source_type not in allowed for e in pack.evidence)
            want = set(c.get("expect_sources", []))
            ranked = [e.source_id for e in pack.evidence]
            if want:
                n_ret += 1
                r5 += bool(want & set(ranked[:5]))
                r20 += bool(want & set(pack.candidates[:20]))
                rr += reciprocal_rank(pack.candidates, want, 10)
                nd += ndcg(pack.candidates, want, 5)
            labelled = bool(c.get("no_source"))
            predicted = pack.answerability in UNSUPPORTED_STATES
            if labelled or want:
                tp += labelled and predicted
                fp += (not labelled) and predicted
                fn += labelled and not predicted
                if labelled != predicted:
                    result.failures.append(
                        f"answerability: {c['id']} {pack.answerability} "
                        f"(labelled {'no_source' if labelled else 'supported'})")
        m["routing_accuracy"] = ratio(routed_ok, routed)
        m["retrieval_cases"] = int(n_ret)
        m["recall_at_5"] = ratio(r5, n_ret)
        m["recall_at_20"] = ratio(r20, n_ret)
        m["mrr_at_10"] = ratio(rr, n_ret)
        m["ndcg_at_5"] = ratio(nd, n_ret)
        m["no_source_precision"] = ratio(tp, tp + fp)
        m["no_source_recall"] = ratio(tp, tp + fn)
    m["ineligible_leaks"] = leaks

    m["conflict_recall"], m["conflict_false_positive"] = _conflict_selftest(sources)
    m["stale_recall"] = _stale_selftest(db, release_id, sources)

    for key, threshold in THRESHOLDS.items():
        if key in NOT_INDEX_DEPENDENT and not include_routing:
            continue
        value = m.get(key)
        if value is not None and value < threshold:
            result.failures.append(f"{key} {value} < {threshold}")
    if leaks > MAX_LEAKS:
        result.failures.append(f"ineligible-source leaks: {leaks}")
    if m.get("conflict_false_positive"):
        result.failures.append("conflict detector flagged agreeing prices")
    # Per-case answerability lines are diagnostics; the thresholds above decide.
    result.passed = not [f for f in result.failures if not f.startswith("answerability:")]
    return result


def _conflict_selftest(sources: list[dict]) -> tuple[float | None, bool]:
    """Run the conflict detector on synthetic disagreeing / agreeing evidence that carries the
    CANDIDATE's real K2 sources' authority ranks. A canonical pricing source ranked below the
    detector's authority floor (the bug this gate exists for) makes recall 0."""
    k2 = [s for s in sources if s["source_type"] == "pricing"]
    if not k2:
        return None, False

    def ev(sid, rank, amount):
        return retrieval.Evidence(
            unit_id="x", source_version_id="x", source_id=sid, source_type="pricing", title=sid,
            version="x", heading_path=sid, text=f"The plan costs {amount} per month.",
            canonical_path="/", updated_at=None, authority_rank=rank, risk_class="HIGH",
            similarity=1.0, lexical_rank=1, vector_rank=1)

    detected, fp = 0, False
    for s in k2:
        rank = int(s["authority_rank"])
        other = f"{s['source_id']}::conflict-fixture"
        detected += retrieval._pricing_conflict([ev(s["source_id"], rank, "$249"),
                                                 ev(other, rank, "$199")])
        fp |= retrieval._pricing_conflict([ev(s["source_id"], rank, "$249"),
                                           ev(other, rank, "$249")])
    return ratio(detected, len(k2)), fp


def _stale_selftest(db: Session, release_id: str, sources: list[dict]) -> float | None:
    high = [s for s in sources if s["risk_class"] == "HIGH"]
    if not high:
        return None
    blocked = 0
    for s in high:
        nested = db.begin_nested()
        try:
            db.execute(text("UPDATE guide_knowledge_source_versions SET review_due_at = now() - "
                            "interval '1 day' WHERE source_version_id = :v"),
                       {"v": s["source_version_id"]})
            cls = retrieval.SOURCE_CLASS.get(s["source_type"])
            pack = retrieval.retrieve(db, s["heading_path"].replace(" > ", " "),
                                      CLASS_INTENT.get(cls, "PRODUCT_QA"), release_id=release_id,
                                      refine=False, jurisdiction=s.get("jurisdiction"))
            blocked += pack.stale_blocked and s["source_id"] not in {e.source_id
                                                                      for e in pack.evidence}
        finally:
            nested.rollback()
    return ratio(blocked, len(high))
