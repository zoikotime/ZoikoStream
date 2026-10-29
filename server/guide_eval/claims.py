"""Deterministic grounded-answer metrics (Evaluation §8).

These are PROXIES computed without a model, from the final answer and the evidence pack the
answer was generated from:

  * citation correctness — every cited source is a source of a unit in the evidence pack
    (a citation to something the model was never shown is wrong by construction);
  * grounded-claim precision — the answer is split into sentences; a "material claim" is a
    sentence with >= 3 content terms; it is supported when at least half of its content terms
    occur in the evidence text (same stem/synonym matching as retrieval coverage);
  * unsupported expansion — the answer introduces a money amount, percentage, number of
    nines, certification/compliance/residency qualifier or absolute guarantee that the
    evidence does not contain (RAG §18.1 "detect unsupported expansion").

They do not replace the calibrated semantic rubric (judge.py) or human review (review.py);
they are the always-on deterministic floor that runs in every mode, including mock.
"""
from __future__ import annotations

import re

from app.guide.knowledge.retrieval import _covered, _terms  # noqa: PLC2701 — shared matcher

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_EXPANSION = re.compile(
    r"(?i)(\$\s?\d[\d,]*(?:\.\d+)?|\b\d+(?:\.\d+)?\s?%|\b99\.\d+\b|\bsoc ?[12]\b|\biso ?\d{4,5}\b|"
    r"\bhipaa\b|\bpci\b|\bfedramp\b|\bcertified\b|\bcompliant\b|\bguarantee[ds]?\b|"
    r"\bunhackable\b|\bdata (?:is )?stored in\b)")


def claim_sentences(answer: str) -> list[str]:
    return [s for s in _SENTENCE.split(answer or "") if len(_terms(s)) >= 3]


def score_answer(answer: str, cited: list[str], evidence: list[dict]) -> dict:
    """evidence: [{"source_id", "text"}] from the pack. Returns per-turn counts."""
    blob = " ".join(f"{e.get('title', '')} {e.get('heading_path', '')} {e['text']}".lower()
                    for e in evidence)
    ev_sources = {e["source_id"] for e in evidence}
    claims = claim_sentences(answer)
    supported = 0
    for s in claims:
        terms = _terms(s)
        if terms and sum(_covered(t, blob) for t in terms) / len(terms) >= 0.5:
            supported += 1
    expansions = [m.group(0) for m in _EXPANSION.finditer(answer or "")
                  if m.group(0).lower().strip() not in blob]
    return {
        "claims": len(claims),
        "supported_claims": supported,
        "citations": len(cited),
        "correct_citations": sum(1 for c in cited if c in ev_sources),
        "unsupported_expansion": bool(expansions),
        "expansion_terms": sorted({e.lower() for e in expansions})[:5],
    }
