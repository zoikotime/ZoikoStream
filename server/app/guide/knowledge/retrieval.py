"""Hybrid retrieval and answerability (RAG §9–18).

FILTER BEFORE RANK: publication status, public flag, effective/expiry window, locale, active
index release and the intent's allowed source classes are all WHERE clauses of the candidate
queries themselves. An ineligible unit can never enter the candidate set, however similar it is
(RAG §11, ARCH-AT-13). The model is never handed a mixed set and asked to ignore part of it.

Candidates: up to 20 vector (pgvector cosine) + 20 lexical (Postgres FTS), unioned, then
reranked deterministically (see `_rerank`: similarity-led, chosen over the RAG spec's RRF
baseline on measured eval results). A cross-encoder can replace `_rerank` behind the same contract;
until then answerability thresholds stay conservative (RAG §13.1 fallback posture).

Similarity is NOT confidence (RAG §17.1): answerability combines relevance, lexical support,
freshness of high-risk sources and conflict detection, with thresholds kept per embedding
profile so they can be calibrated on the evaluation set.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .embeddings import HASH_PROFILE, LOCAL_PROFILE, get_embedder

# RAG §10.1 intent -> source-class allowlist.
INTENT_SOURCES = {
    "PRODUCT_QA": ("product", "help", "trust", "accessibility", "developer", "sales_help",
                   "status", "legal"),
    "DISCOVERY": ("product", "sales_help", "help"),
    "PRICING": ("pricing", "sales_help"),
    "DEMO": ("sales_help", "pricing", "product"),
    "SALES": ("sales_help", "pricing", "product"),
    "VIEWER_ACCESS": ("help", "accessibility"),
    "PLAYBACK": ("help", "accessibility"),
    "AUDIO": ("help", "accessibility"),
    "CAPTIONS": ("help", "accessibility", "product"),
    "REPLAY": ("help", "product"),
    "SECURITY_TRUST": ("trust", "legal"),
    "PRIVACY": ("legal", "trust"),
    "LEGAL": ("legal",),
    "DEVELOPER": ("developer", "product"),
    "STATUS": ("status", "help"),
}
HIGH_RISK_INTENTS = {"PRICING", "SECURITY_TRUST", "PRIVACY", "LEGAL"}

# Calibration starting points (RAG Appendix A) — configuration, tuned on the eval set.
THRESHOLDS = {
    LOCAL_PROFILE: {"supported": 0.62, "supported_with_lexical": 0.55, "low": 0.50,
                    "high_risk_bump": 0.04},
    HASH_PROFILE: {"supported": 0.35, "supported_with_lexical": 0.25, "low": 0.20,
                   "high_risk_bump": 0.0},
}

VECTOR_K = 20
LEXICAL_K = 20
UNION_CAP = 30
FINAL_MAX = 6

# RAG §9.1: rewrites add public synonyms; they never add identity, ids, prices or conclusions.
_SYNONYMS = [
    (r"\bfuneral(s)?\b", "memorial service livestream"),
    (r"\bmemorial(s)?\b", "memorial service livestream"),
    (r"\b(church|worship|sermon|service)\b", "church live stream worship"),
    (r"\b(freez\w*|lag\w*|stutter\w*|buffer\w*)\b", "playback buffering network browser"),
    (r"\b(how much|cost|price|pricing|fee|fees|plans?)\b", "pricing plans"),
    (r"\b(cc|subtitles?)\b", "captions"),
    (r"\b(recording|recorded|rewatch|watch later|on demand)\b", "replay recording"),
    (r"\b(webinar|town ?hall|all-hands|conference)\b", "live events webinar"),
    (r"\b(secure|safe|safety|encrypt\w*)\b", "security"),
    (r"\b(gdpr|data protection|my data)\b", "privacy personal data"),
    (r"\b(api|sdk|developer|integration|webhook)s?\b", "developer api"),
]


@dataclass
class Evidence:
    unit_id: str
    source_version_id: str
    source_id: str
    source_type: str
    title: str
    version: str
    heading_path: str
    text: str
    canonical_path: str | None
    updated_at: str | None
    authority_rank: int
    risk_class: str
    similarity: float
    lexical_rank: int | None
    vector_rank: int | None


@dataclass
class EvidencePack:
    answerability: str           # SUPPORTED | PARTIAL | NO_SOURCE | LOW_CONFIDENCE | ...
    evidence: list[Evidence] = field(default_factory=list)
    release_id: str | None = None
    conflict: bool = False
    stale_blocked: bool = False
    top_similarity: float = 0.0
    # Ranked source ids of the fused candidate set (internal: evaluation Recall@20 only;
    # never serialized to the public API — API §17).
    candidates: list[str] = field(default_factory=list)

    def source_refs(self) -> dict[str, Evidence]:
        """First evidence unit per source, for citation."""
        refs: dict[str, Evidence] = {}
        for e in self.evidence:
            refs.setdefault(e.source_id, e)
        return refs


def rewrite_query(query: str) -> str:
    extra = []
    low = query.lower()
    for pattern, expansion in _SYNONYMS:
        if re.search(pattern, low):
            extra.append(expansion)
    return f"{query} {' '.join(dict.fromkeys(extra))}".strip()


_ELIGIBLE = """
    FROM guide_rag.rag_knowledge_units u
    JOIN guide_rag.rag_index_releases r
      ON r.release_id = u.release_id AND r.status = 'active'
    JOIN guide_knowledge_source_versions v
      ON v.source_version_id = u.source_version_id
    JOIN guide_knowledge_sources s ON s.source_id = v.source_id
    WHERE v.status = 'published'
      AND v.public_retrieval
      AND (v.effective_at IS NULL OR v.effective_at <= now())
      AND (v.expires_at IS NULL OR v.expires_at > now())
      AND v.locale = :locale
      AND s.source_type = ANY(:source_types)
"""

_COLUMNS = """
    u.unit_id, u.source_version_id, u.source_id, s.source_type, s.title, v.version,
    u.heading_path, u.content_text, s.canonical_path, v.published_at, s.authority_rank,
    s.risk_class, v.review_due_at, u.release_id, u.embedding_profile, u.content_digest
"""


def active_release(db: Session) -> tuple[str, str] | None:
    row = db.execute(text("SELECT release_id, embedding_profile FROM guide_rag.rag_index_releases "
                          "WHERE status = 'active'")).first()
    return (row[0], row[1]) if row else None


def retrieve(db: Session, query: str, intent: str, *, locale: str = "en-US") -> EvidencePack:
    release = active_release(db)
    if release is None:
        return EvidencePack("NO_SOURCE")
    release_id, profile = release
    source_types = list(INTENT_SOURCES.get(intent, INTENT_SOURCES["PRODUCT_QA"]))
    expanded = rewrite_query(query)
    qvec = get_embedder(profile).embed_query(expanded)
    params = {"locale": locale, "source_types": source_types, "k": VECTOR_K,
              "qvec": "[" + ",".join(f"{x:.6f}" for x in qvec) + "]", "q": expanded}

    vec_rows = db.execute(text(
        f"SELECT {_COLUMNS}, 1 - (u.embedding <=> CAST(:qvec AS vector)) AS sim "
        f"{_ELIGIBLE} ORDER BY u.embedding <=> CAST(:qvec AS vector) LIMIT :k"), params).all()
    # OR-semantics lexical query: natural questions rarely contain every term, so the AND
    # query plainto_tsquery builds is rewritten to OR before matching.
    lex_rows = db.execute(text(
        f"""WITH q AS (SELECT NULLIF(replace(plainto_tsquery('english', :q)::text, '&', '|'),
                                     '')::tsquery AS query)
            SELECT {_COLUMNS}, 1 - (u.embedding <=> CAST(:qvec AS vector)) AS sim,
                   ts_rank_cd(u.fts_document, (SELECT query FROM q)) AS lrank
            {_ELIGIBLE}
              AND u.fts_document @@ (SELECT query FROM q)
            ORDER BY lrank DESC LIMIT :k"""), params).all()

    candidates: dict[str, dict] = {}
    for rank, row in enumerate(vec_rows, 1):
        candidates[str(row.unit_id)] = {"row": row, "vrank": rank, "lrank": None}
    for rank, row in enumerate(lex_rows, 1):
        c = candidates.setdefault(str(row.unit_id), {"row": row, "vrank": None, "lrank": None})
        c["lrank"] = rank
    if not candidates:
        return EvidencePack("NO_SOURCE", release_id=release_id)
    ranked = _rerank(list(candidates.values()), query)[:UNION_CAP]
    pack = _assess(ranked, intent, profile, release_id)
    pack.candidates = list(dict.fromkeys(c["row"].source_id for c in ranked))
    return pack


def _rerank(cands: list[dict], query: str) -> list[dict]:
    """Order the fused (vector ∪ lexical) candidate set.

    Calibrated on the evaluation set (guide_eval, 50 grounded cases): ordering by semantic
    similarity beat pure RRF on top-1 evidence (0.84 vs 0.76) at equal Recall@5, because
    RRF let generic keyword hits ("support", "events") outrank the right page. Lexical search
    still contributes candidates the vector path missed and counts toward answerability;
    heading specificity and authority break near-ties."""
    qterms = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 3}
    for c in cands:
        heading = c["row"].heading_path.lower()
        specificity = 0.002 * sum(1 for t in qterms if t in heading)
        authority = 0.0001 * (c["row"].authority_rank or 0)
        c["score"] = float(c["row"].sim) + specificity + authority
    return sorted(cands, key=lambda c: c["score"], reverse=True)


_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")


def _assess(ranked: list[dict], intent: str, profile: str, release_id: str) -> EvidencePack:
    th = THRESHOLDS.get(profile, THRESHOLDS[LOCAL_PROFILE])
    high_risk = intent in HIGH_RISK_INTENTS
    bump = th["high_risk_bump"] if high_risk else 0.0
    now = datetime.now(timezone.utc)

    evidence, seen_digests, stale = [], set(), False
    for c in ranked:
        row = c["row"]
        # Stale high-risk content is blocked, not down-weighted (RAG §17, PG-AT-24).
        if row.risk_class == "HIGH" and row.review_due_at and row.review_due_at < now:
            stale = True
            continue
        if row.content_digest in seen_digests:
            continue
        seen_digests.add(row.content_digest)
        evidence.append(Evidence(
            unit_id=str(row.unit_id), source_version_id=str(row.source_version_id),
            source_id=row.source_id, source_type=row.source_type, title=row.title,
            version=row.version, heading_path=row.heading_path, text=row.content_text,
            canonical_path=row.canonical_path,
            updated_at=row.published_at.date().isoformat() if row.published_at else None,
            authority_rank=row.authority_rank, risk_class=row.risk_class,
            similarity=float(row.sim), lexical_rank=c["lrank"], vector_rank=c["vrank"]))
        if len(evidence) >= FINAL_MAX:
            break

    if not evidence:
        return EvidencePack("STALE_HIGH_RISK" if stale else "NO_SOURCE", release_id=release_id,
                            stale_blocked=stale)
    top = max(e.similarity for e in evidence)
    lexical_support = any(e.lexical_rank is not None for e in evidence[:3])
    if top >= th["supported"] + bump or (lexical_support
                                          and top >= th["supported_with_lexical"] + bump):
        state = "SUPPORTED"
    elif top >= th["low"] + bump:
        state = "LOW_CONFIDENCE"
    else:
        return EvidencePack("NO_SOURCE", release_id=release_id, top_similarity=top)

    # Keep only units that are themselves plausibly relevant: a weak tail dilutes the pack.
    floor = max(th["low"], top - 0.15)
    evidence = [e for e in evidence if e.similarity >= floor or e.lexical_rank is not None][
        :FINAL_MAX]

    conflict = intent == "PRICING" and _pricing_conflict(evidence)
    if conflict:
        state = "SOURCE_CONFLICT"
    return EvidencePack(state, evidence, release_id=release_id, conflict=conflict,
                        stale_blocked=stale, top_similarity=top)


def _pricing_conflict(evidence: list[Evidence]) -> bool:
    """Two different authoritative pricing sources quoting different amounts = conflict.
    Guide must not silently pick one (PRD §11.2 SOURCE PRECEDENCE, PG-AT-11)."""
    by_source: dict[str, set[str]] = {}
    for e in evidence:
        if e.source_type == "pricing" and e.authority_rank >= 80:
            by_source.setdefault(e.source_id, set()).update(
                m.replace(" ", "") for m in _MONEY.findall(e.text))
    amounts = [a for a in by_source.values() if a]
    return len(amounts) > 1 and any(a != amounts[0] for a in amounts[1:])
