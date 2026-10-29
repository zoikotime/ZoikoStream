"""Hybrid retrieval and answerability (RAG §9–18).

FILTER BEFORE RANK: publication status, public flag, effective/expiry window, unit validity
window, locale, jurisdiction, freshness of high-risk content, the index release and the
intent's allowed source classes are all WHERE clauses of the candidate queries themselves. An
ineligible unit can never enter the candidate set, however similar it is (RAG §11, RAG-AT-02/
03/17/18/22, ARCH-AT-13). The model is never handed a mixed set and asked to ignore part of it.

Candidates: up to 20 vector (pgvector cosine) + 20 lexical (Postgres FTS), unioned and capped
at 30 (RAG §12), then FUSED by the configured fusion profile (RAG §12.1):

  * "rrf-v1" (default, the RAG launch baseline): Reciprocal Rank Fusion over the two channel
    ranks (k = 60), with section specificity and authority as separate small tie-breakers —
    raw vector and lexical scores are never added together;
  * "similarity-v1": the earlier similarity-led ordering, kept selectable as a profile so the
    two can be compared on the evaluation set (the active profile is recorded in every release
    manifest and evaluation report).

There is NO cross-encoder reranker at launch (RERANKER_PROFILE = "none"). RAG §13.1 allows the
deterministic fused ranking as the fallback but requires a reduced answerability posture, so
every "supported" threshold is raised by NO_RERANKER_MARGIN while no reranker is configured
(RAG-AT-29).

Similarity is NOT confidence (RAG §17.1): answerability combines relevance, lexical support,
claim coverage, freshness of high-risk sources, conflict detection and the intent's risk
profile, with thresholds kept per embedding profile so they can be calibrated on the
evaluation set (RAG Appendix A). States: SUPPORTED, PARTIAL, LOW_CONFIDENCE, NO_SOURCE,
SOURCE_CONFLICT, STALE_HIGH_RISK (PRIVATE_REQUIRED / DYNAMIC_REQUIRED are decided upstream by
the boundary and routing stages, before retrieval is ever called — RAG §10).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import metrics
from .embeddings import HASH_PROFILE, LOCAL_PROFILE, get_embedder

# ── Source classes and the intent allowlist (RAG §3, §10.1) ───────────────────────────────

# Registry source_type -> corpus class. "status" sources are STATIC help about the public
# status page (how to read/subscribe to it): support content, K3. Current service status is the
# dynamic D1 source and never enters the static index (RAG §3.1).
SOURCE_CLASS = {
    "product": "K1", "pricing": "K2", "help": "K3", "status": "K3", "trust": "K4",
    "legal": "K5", "accessibility": "K6", "developer": "K7", "sales_help": "K8",
}

# RAG §10.1, exactly: Product Q&A = K1 + relevant K3/K4/K6/K7; Discovery = K1 + K8;
# Pricing = K2 authority, K8 only for route copy; Viewer access/playback = K3 + K6 (D2 when
# validated, outside this index); Trust/security = K4 + K5 where privacy/legal is relevant;
# Privacy/legal = K5, K4 only for approved supporting facts; Developer = K7;
# Sales/demo = K8 + K2 when pricing facts are needed.
INTENT_CLASSES = {
    "PRODUCT_QA": ("K1", "K3", "K4", "K6", "K7"),
    "DISCOVERY": ("K1", "K8"),
    "PRICING": ("K2", "K8"),
    "DEMO": ("K8", "K2"),
    "SALES": ("K8", "K2"),
    "VIEWER_ACCESS": ("K3", "K6"),
    "PLAYBACK": ("K3", "K6"),
    "AUDIO": ("K3", "K6"),
    "CAPTIONS": ("K3", "K6"),
    "REPLAY": ("K3", "K6"),
    "SECURITY_TRUST": ("K4", "K5"),
    "PRIVACY": ("K5", "K4"),
    "LEGAL": ("K5", "K4"),
    "DEVELOPER": ("K7",),
    "STATUS": ("K3",),  # static status-page help only; current status is D1 (RAG §10)
}
INTENT_SOURCES = {
    intent: tuple(t for t, k in SOURCE_CLASS.items() if k in classes)
    for intent, classes in INTENT_CLASSES.items()
}
HIGH_RISK_INTENTS = {"PRICING", "SECURITY_TRUST", "PRIVACY", "LEGAL"}

# RAG §9 "topic map": a general product question that is really about the public
# contact/sales process is routed to the Sales/demo profile (K8 + K2), the class that holds that
# content. Refinement only ever selects another §10.1 profile; it never widens one.
_SALES_PROCESS = re.compile(
    r"(?i)\b(contact|get in touch|reach (you|the team|zoikostream)|talk to (an? )?(expert|"
    r"someone|the team)|expert|sales team|enquir\w*|inquir\w*|existing customers?|"
    r"account support)\b")
# Commercial vocabulary the intent classifier may leave as a product/discovery question
# ("What are the fees for a wedding stream?") -> the Pricing profile, where K2 is authority.
_PRICING_TOPIC = re.compile(
    r"(?i)\b(fees?|billed|billing|invoic\w*|charges?|pric\w*|costs?|quotes?|quotation|"
    r"how much)\b")
# Privacy-notice vocabulary -> the Privacy/legal profile (K5 + supporting K4).
_PRIVACY_TOPIC = re.compile(
    r"(?i)\b(privacy|personal data|my data|children'?s?|gdpr|data protection|"
    r"data (subject|rights)|cookies?)\b")


def retrieval_intent(query: str, intent: str) -> str:
    if intent in ("PRODUCT_QA", "DISCOVERY") and _PRICING_TOPIC.search(query):
        return "PRICING"
    if intent == "PRODUCT_QA" and _PRIVACY_TOPIC.search(query):
        return "PRIVACY"
    if intent == "PRODUCT_QA" and _SALES_PROCESS.search(query):
        return "SALES"
    return intent


# ── Profiles and calibrated thresholds (RAG Appendix A) ───────────────────────────────────

FUSION_PROFILES = ("rrf-v1", "similarity-v1")
RERANKER_PROFILE = "none"            # no cross-encoder at launch (RAG §13.1 fallback)
ANSWERABILITY_PROFILE = "answerability-v2"
LEXICAL_PROFILE = "pg-fts-english-v1"
RRF_K = 60
NO_RERANKER_MARGIN = 0.02            # stricter "supported" gate while RERANKER_PROFILE == none

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
MAX_PER_SOURCE = 3
NEAR_DUPLICATE_JACCARD = 0.8         # RAG §14 / §6.1: collapse overlapping units

# RAG §9.1: rewrites add public synonyms; they never add identity, ids, prices or conclusions.
_SYNONYMS = [
    (r"\bfuneral(s)?\b", "memorial service livestream"),
    (r"\bmemorial(s)?\b", "memorial service livestream"),
    (r"\b(church|worship|sermon|service)\b", "church live stream worship"),
    (r"\b(freez\w*|lag\w*|stutter\w*|buffer\w*)\b", "playback buffering network browser"),
    (r"\b(how much|cost|price|pricing|fee|fees|plans?|quotes?|quotation)\b", "pricing plans"),
    (r"\b(cc|subtitles?)\b", "captions"),
    (r"\b(recording|recorded|rewatch|watch later|on demand)\b", "replay recording"),
    (r"\b(webinar|town ?hall|all-hands|conference)\b", "live events webinar"),
    (r"\b(secure|safe|safety|encrypt\w*)\b", "security"),
    (r"\b(bugs?|bounty|bounties|exploit\w*)\b", "security vulnerability report"),
    (r"\b(gdpr|data protection|my data)\b", "privacy personal data"),
    (r"\b(api|sdk|developer|integration|webhook)s?\b", "developer api"),
    (r"\b(livestream\w*|webcast\w*)\b", "live stream"),
    (r"\b(city council|council|municipal\w*|government|town meeting)\b", "civic events"),
]

# Claim-coverage vocabulary (RAG §17.2 "Coverage: evidence must answer the requested claim,
# not merely an adjacent topic"). Function words and the brand carry no claim.
_STOP = frozenset("""
a an the is are was were be been am do does did can could would should will shall may might
must i me my we our us you your yours it its this that these those there here what which who
whom whose when where why how and or but if then so of to in on at by for with about from into
over under as than too very just also any some have has had get got give tell show let make
want need like please much many more most such own same other really ok yes no not dont cant
cannot zoikostream zoiko guide
hi hello hey thanks thank honestly frustrating frustrated annoying ugh quick question explain
summarize summary overview information info details tell know learn read find look
""".split())
# General-purpose words that carry no subject of their own: a question may use them without
# the corpus ever doing so ("Is it suitable for graduations?"). They never trigger the
# "subject absent from the corpus" rule below.
_GENERIC = frozenset("""
kind kinds type types suitable handle handles feature features option options thing things
way ways work works working good best better great sure possible able available use using
used choose offer offers provide provides include includes support supports allow allows
month monthly year yearly annual annually week weekly day daily today cost costs fee fees
family families friend friends people person someone everyone travel time times start
during after before while their them they his her our new old big small first last next
right now soon still also again ever never always often help issue problem question
""".split())
# RAG §20: compliance / certification / residency / guarantee claims need EXPLICIT current
# evidence. If the query asks for one and the qualifier is absent from the evidence, the state
# is NO_SOURCE however similar the surrounding text is.
_QUALIFIER = re.compile(
    r"(?i)\b(certif\w*|complian\w*|accredit\w*|soc ?[123]|iso ?\d{4,5}|hipaa|pci(-dss)?|"
    r"fedramp|statera?mp|hitrust|tisax|nist|csa star|cyber essentials|"
    r"residen\w*|geograph\w*|data ?cent(er|re)s?|region\w*|uptime|sla|guarantee\w*|"
    # Named jurisdictions: a storage/residency/legal claim about a place needs that place in
    # the evidence ("us" is deliberately absent — it is a pronoun).
    r"eu|europe\w*|usa|united states|uk|united kingdom|canada|australia|india|germany|"
    # Corporate facts about ZoikoStream itself (founding, ownership, funding, size): never
    # inferable from product copy.
    r"founded|founders?|ceo|investors?|funding|valuation|revenue|employees|headquarter\w*|"
    r"market share|how many customers|publicly traded)\b")
# Qualifiers that may appear inflected in evidence; every other qualifier is a proper name or
# code that must appear verbatim ("StateRAMP" is not evidenced by the word "state").
_INFLECTED_QUALIFIERS = ("certif", "complian", "accredit", "residen", "geograph", "region",
                         "guarantee", "uptime")
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
# Authoritative pricing = an active K2 source at or above the canonical tier. Every approved
# canonical source carries authority_rank 90; lower tiers (e.g. 40 partner copy) never decide
# a price conflict (PRD §11.2 SOURCE PRECEDENCE, RAG §19).
AUTHORITATIVE_RANK = 80


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
    topic: str | None = None
    fused_score: float = 0.0
    # Freshness governance date of the source version (RAG §4 review_due_at). Past-due
    # HIGH-risk units never get here (filtered before ranking); for lower risk the
    # orchestrator marks the citation's freshness from it.
    review_due_at: datetime | None = None


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
    coverage: float | None = None
    uncovered_parts: int = 0
    fusion_profile: str | None = None
    reranker_profile: str = RERANKER_PROFILE
    retrieval_intent: str | None = None

    @property
    def conflict_state(self) -> str:
        return "CONFLICT" if self.conflict else "NONE"

    @property
    def freshness_state(self) -> str:
        return "STALE_BLOCKED" if self.stale_blocked else "CURRENT"

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


def fusion_profile() -> str:
    from ..settings import get_settings
    chosen = (get_settings().GUIDE_RAG_FUSION_PROFILE or "rrf-v1").strip()
    return chosen if chosen in FUSION_PROFILES else "rrf-v1"


# ── Eligibility (RAG §11) ──────────────────────────────────────────────────────────────────

_RELEASE_PINNED = "r.release_id = :release_id"

# Stale = a HIGH-risk unit/source past its review date. It is filtered BEFORE ranking (RAG §11
# "risk/freshness passes active policy"); a separate probe below only detects THAT a stale
# unit would have matched, so the state can say STALE_HIGH_RISK instead of NO_SOURCE.
_STALE = ("((u.risk_class = 'HIGH' OR s.risk_class = 'HIGH') AND v.review_due_at IS NOT NULL "
          "AND v.review_due_at < now())")


def _eligible(release_clause: str, *, stale: bool = False) -> str:
    return f"""
    FROM guide_rag.rag_knowledge_units u
    JOIN guide_rag.rag_index_releases r
      ON r.release_id = u.release_id AND {release_clause}
    JOIN guide_knowledge_source_versions v
      ON v.source_version_id = u.source_version_id
    JOIN guide_knowledge_sources s ON s.source_id = v.source_id
    WHERE v.status = 'published'
      AND v.public_retrieval
      AND (v.effective_at IS NULL OR v.effective_at <= now())
      AND (v.expires_at IS NULL OR v.expires_at > now())
      AND (u.valid_from IS NULL OR u.valid_from <= now())
      AND (u.valid_to IS NULL OR u.valid_to > now())
      AND v.locale = :locale AND u.locale = :locale
      AND (u.jurisdiction IS NULL OR u.jurisdiction = :jurisdiction)
      AND (v.jurisdiction IS NULL OR v.jurisdiction = :jurisdiction)
      AND s.source_type = ANY(:source_types)
      AND {'' if stale else 'NOT '}{_STALE}
"""


_COLUMNS = """
    u.unit_id, u.source_version_id, u.source_id, s.source_type, s.title, v.version,
    u.heading_path, u.content_text, s.canonical_path, v.published_at, s.authority_rank,
    u.risk_class, v.review_due_at, u.release_id, u.embedding_profile, u.content_digest, u.topic
"""


def active_release(db: Session) -> tuple[str, str] | None:
    row = db.execute(text("SELECT release_id, embedding_profile FROM guide_rag.rag_index_releases "
                          "WHERE status = 'active'")).first()
    return (row[0], row[1]) if row else None


def _release(db: Session, release_id: str | None) -> tuple[str, str] | None:
    if release_id is None:
        return active_release(db)
    row = db.execute(text("SELECT release_id, embedding_profile FROM guide_rag.rag_index_releases "
                          "WHERE release_id = :r"), {"r": release_id}).first()
    return (row[0], row[1]) if row else None


def retrieve(db: Session | None, query: str, intent: str, *, locale: str = "en-US",
             jurisdiction: str | None = None, release_id: str | None = None,
             refine: bool = True) -> EvidencePack:
    """Retrieve an evidence pack. `db=None` uses the retrieval store's own pool (RAG §7.1);
    a caller-supplied session is used as-is (index evaluation inside an ingest transaction).
    `release_id` pins a CANDIDATE release for offline evaluation before activation; omitted,
    only the active release is searched. `jurisdiction` None = visitor jurisdiction unknown,
    so only jurisdiction-neutral content is eligible (RAG §11 "compatible where meaning
    differs")."""
    if db is None:
        from . import store
        own = store.new_session()
        try:
            return retrieve(own, query, intent, locale=locale, jurisdiction=jurisdiction,
                            release_id=release_id, refine=refine)
        finally:
            own.close()
    started = time.perf_counter()
    pack = _retrieve(db, query, intent, locale=locale, jurisdiction=jurisdiction,
                     release_id=release_id, refine=refine)
    try:
        metrics.observe("rag_retrieval_seconds", time.perf_counter() - started,
                        intent=_metric_intent(intent))
        metrics.incr("rag_answerability_total", state=pack.answerability)
        if pack.stale_blocked:
            metrics.incr("rag_stale_blocked_total")
        if pack.conflict:
            metrics.incr("rag_conflicts_total")
    except ValueError:  # never let telemetry break retrieval
        pass
    return pack


def _metric_intent(intent: str) -> str:
    return intent if intent in INTENT_CLASSES else "other"


def _retrieve(db: Session, query: str, intent: str, *, locale: str, jurisdiction: str | None,
              release_id: str | None, refine: bool = True) -> EvidencePack:
    release = _release(db, release_id)
    if release is None:
        return EvidencePack("NO_SOURCE")
    release_id, profile = release
    r_intent = retrieval_intent(query, intent) if refine else intent
    source_types = list(INTENT_SOURCES.get(r_intent, INTENT_SOURCES["PRODUCT_QA"]))
    # RAG §9 "Normalize": misspellings are corrected against the release vocabulary first, so
    # "downlaoding an evnet" reaches the same evidence as the correctly spelled question.
    query = normalize_query(query, _release_vocabulary(db, release_id))
    expanded = rewrite_query(query)
    qvec = get_embedder(profile).embed_query(expanded)
    params = {"locale": locale, "jurisdiction": jurisdiction, "source_types": source_types,
              "k": VECTOR_K, "qvec": "[" + ",".join(f"{x:.6f}" for x in qvec) + "]",
              "q": expanded, "release_id": release_id}
    rel = _RELEASE_PINNED

    vec_rows = db.execute(text(
        f"SELECT {_COLUMNS}, 1 - (u.embedding <=> CAST(:qvec AS vector)) AS sim "
        f"{_eligible(rel)} ORDER BY u.embedding <=> CAST(:qvec AS vector) LIMIT :k"),
        params).all()
    # OR-semantics lexical query: natural questions rarely contain every term, so the AND
    # query plainto_tsquery builds is rewritten to OR before matching.
    lex_rows = db.execute(text(
        f"""WITH q AS (SELECT NULLIF(replace(plainto_tsquery('english', :q)::text, '&', '|'),
                                     '')::tsquery AS query)
            SELECT {_COLUMNS}, 1 - (u.embedding <=> CAST(:qvec AS vector)) AS sim,
                   ts_rank_cd(u.fts_document, (SELECT query FROM q)) AS lrank
            {_eligible(rel)}
              AND u.fts_document @@ (SELECT query FROM q)
            ORDER BY lrank DESC LIMIT :k"""), params).all()
    stale_top = _stale_probe(db, params, rel)

    candidates: dict[str, dict] = {}
    for rank, row in enumerate(vec_rows, 1):
        candidates[str(row.unit_id)] = {"row": row, "vrank": rank, "lrank": None}
    for rank, row in enumerate(lex_rows, 1):
        c = candidates.setdefault(str(row.unit_id), {"row": row, "vrank": None, "lrank": None})
        c["lrank"] = rank
    _observe_candidates(len(vec_rows), len(lex_rows), len(candidates))
    fusion = fusion_profile()
    if not candidates:
        return _no_evidence(release_id, stale_top, profile, intent, fusion, r_intent)
    ranked = fuse(list(candidates.values()), query, fusion)[:UNION_CAP]
    pack = _assess(db, ranked, query, r_intent, profile, release_id, stale_top)
    pack.candidates = list(dict.fromkeys(c["row"].source_id for c in ranked))
    pack.fusion_profile = fusion
    pack.retrieval_intent = r_intent
    try:
        metrics.observe("rag_candidates", len(pack.evidence), channel="final")
    except ValueError:
        pass
    return pack


def _observe_candidates(n_vec: int, n_lex: int, n_union: int) -> None:
    try:
        metrics.observe("rag_candidates", n_vec, channel="vector")
        metrics.observe("rag_candidates", n_lex, channel="lexical")
        metrics.observe("rag_candidates", n_union, channel="union")
    except ValueError:
        pass


def _stale_probe(db: Session, params: dict, rel: str) -> float | None:
    """Best similarity among STALE high-risk units that would otherwise be eligible (they are
    never ranked or returned — RAG §17 STALE_HIGH_RISK, PG-AT-24). None = no stale match."""
    sim = db.execute(text(
        f"SELECT max(1 - (u.embedding <=> CAST(:qvec AS vector))) {_eligible(rel, stale=True)}"),
        params).scalar()
    return None if sim is None else float(sim)


def _no_evidence(release_id, stale_top, profile, intent, fusion, r_intent) -> EvidencePack:
    th = _thresholds(profile, intent)
    stale = stale_top is not None and stale_top >= th["low"]
    return EvidencePack("STALE_HIGH_RISK" if stale else "NO_SOURCE", release_id=release_id,
                        stale_blocked=stale, fusion_profile=fusion, retrieval_intent=r_intent)


# ── Fusion (RAG §12.1) ─────────────────────────────────────────────────────────────────────

def fuse(cands: list[dict], query: str, profile: str = "rrf-v1") -> list[dict]:
    """Order the fused (vector ∪ lexical) candidate set.

    rrf-v1: Σ 1/(k + rank) over the channels a unit appeared in. Authority and heading
    specificity are separate, much smaller terms, so they only break near-ties and never
    replace relevance (RAG §12.1 "Authority/freshness remain separate controls").

    similarity-v1: semantic similarity first, with the same tie-breakers — measured on the
    early 50-case evaluation set it gave better top-1 evidence than plain RRF when generic
    keyword hits ("support", "events") were common; kept as a selectable profile."""
    qterms = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 3}
    for c in cands:
        row = c["row"]
        heading = row.heading_path.lower()
        specificity = sum(1 for t in qterms if t in heading)
        authority = row.authority_rank or 0
        if profile == "similarity-v1":
            c["score"] = float(row.sim) + 0.002 * specificity + 0.0001 * authority
        else:
            rrf = sum(1.0 / (RRF_K + r) for r in (c["vrank"], c["lrank"]) if r is not None)
            # Similarity enters only as a bounded tie-breaker, scaled below one RRF rank step.
            c["score"] = (rrf + 0.0004 * specificity + 0.00001 * authority
                          + 0.0002 * float(row.sim))
    return sorted(cands, key=lambda c: c["score"], reverse=True)


# ── Evidence selection and answerability (RAG §14, §17) ────────────────────────────────────

def _thresholds(profile: str, intent: str) -> dict:
    th = dict(THRESHOLDS.get(profile, THRESHOLDS[LOCAL_PROFILE]))
    margin = NO_RERANKER_MARGIN if RERANKER_PROFILE == "none" else 0.0
    bump = th["high_risk_bump"] if intent in HIGH_RISK_INTENTS else 0.0
    th["supported"] += margin + bump
    th["supported_with_lexical"] += margin + bump
    th["low"] += bump
    return th


def _shingles(s: str, n: int = 3) -> set[tuple]:
    words = re.findall(r"[a-z0-9]+", s.lower())
    return {tuple(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


def near_duplicate(a: str, b: str, threshold: float = NEAR_DUPLICATE_JACCARD) -> bool:
    """Word-trigram Jaccard similarity (RAG §14 'collapse overlapping units')."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return False
    return len(sa & sb) / len(sa | sb) >= threshold


_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied", "ers", "ed",
             "es", "er", "ly", "s", "e")


def _stem(w: str) -> str:
    """Crude root: drop one common inflectional suffix, then keep at most 5 characters, so
    "billed" ~ "billing" and "prices" ~ "pricing" match as prefixes."""
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    return w[:5]


def _terms(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) >= 3 and w not in _STOP]


def _alternatives(term: str) -> set[str]:
    alts = {term}
    for pattern, expansion in _SYNONYMS:
        if re.search(pattern, term):
            alts |= set(expansion.split())
    return alts


def _covered(term: str, blob: str) -> bool:
    return any(re.search(r"\b" + re.escape(_stem(a)), blob) for a in _alternatives(term))


_vocab_cache: dict[str, frozenset] = {}


def _release_vocabulary(db: Session, release_id: str) -> frozenset:
    """Word stems of every unit in the release (cached per immutable release)."""
    vocab = _vocab_cache.get(release_id)
    if vocab is None:
        words: set[str] = set()
        for (body,) in db.execute(text(
                "SELECT heading_path || ' ' || content_text FROM guide_rag.rag_knowledge_units "
                "WHERE release_id = :r"), {"r": release_id}):
            words.update(re.findall(r"[a-z0-9]+", body.lower()))
        vocab = frozenset(words)
        if len(_vocab_cache) > 8:
            _vocab_cache.clear()
        _vocab_cache[release_id] = vocab
    return vocab


def _in_vocabulary(term: str, vocab: frozenset) -> bool:
    stems = {_stem(a) for a in _alternatives(term)}
    return any(w.startswith(st) for st in stems for w in vocab)


def _one_edit(a: str, b: str) -> bool:
    """Damerau distance exactly 1 (substitution, insertion, deletion or transposition)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                   and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def normalize_query(query: str, vocab: frozenset) -> str:
    """Correct words of >= 5 letters that are not in the release vocabulary but are one edit
    away from exactly one vocabulary word. Unknown proper nouns stay as typed."""
    def fix(m: re.Match) -> str:
        w = m.group(0)
        low = w.lower()
        if (len(low) < 5 or low in vocab or low in _STOP or low in _GENERIC
                or not low.isalpha()):
            return w
        candidates = {v for v in vocab | _STOP | _GENERIC
                      if abs(len(v) - len(low)) <= 1 and v[:1] == low[:1] and _one_edit(low, v)}
        # A transposition ("corportae") shares its stem with the real word, so the stem check
        # alone would keep it; a single exact one-edit match is corrected first.
        swaps = {v for v in candidates if sorted(v) == sorted(low)}  # transpositions only
        if len(swaps) == 1:
            return swaps.pop()
        if _in_vocabulary(low, vocab):
            return w
        return candidates.pop() if len(candidates) == 1 else w
    return re.sub(r"[A-Za-z]+", fix, query)


def _query_parts(query: str) -> list[str]:
    """Separate requested claims: several questions, or clauses joined by 'and also'/';'."""
    parts = [p for p in re.split(r"\?|;|\band also\b|\. ", query) if _terms(p)]
    return parts if len(parts) > 1 else [query]


def _assess(db: Session, ranked: list[dict], query: str, intent: str, profile: str,
            release_id: str, stale_top: float | None) -> EvidencePack:
    th = _thresholds(profile, intent)
    evidence: list[Evidence] = []
    seen_digests: set = set()
    for c in ranked:
        row = c["row"]
        if row.content_digest in seen_digests:
            continue
        # Near-duplicate collapse: a lower-ranked unit that mostly repeats a selected one adds
        # no coverage and would displace distinct evidence (RAG §14, RAG-AT-15).
        if any(near_duplicate(row.content_text, e.text) for e in evidence):
            continue
        # Redundancy control: at most MAX_PER_SOURCE units from one source, so one long page
        # cannot crowd out the canonical unit of another (RAG §14). Not a diversity quota —
        # a single source that fully answers still yields a compact pack (RAG-AT-20).
        if sum(e.source_id == row.source_id for e in evidence) >= MAX_PER_SOURCE:
            continue
        seen_digests.add(row.content_digest)
        evidence.append(Evidence(
            unit_id=str(row.unit_id), source_version_id=str(row.source_version_id),
            source_id=row.source_id, source_type=row.source_type, title=row.title,
            version=row.version, heading_path=row.heading_path, text=row.content_text,
            canonical_path=row.canonical_path,
            updated_at=row.published_at.date().isoformat() if row.published_at else None,
            authority_rank=row.authority_rank, risk_class=row.risk_class,
            similarity=float(row.sim), lexical_rank=c["lrank"], vector_rank=c["vrank"],
            topic=row.topic, fused_score=float(c.get("score", 0.0)),
            review_due_at=row.review_due_at))
        if len(evidence) >= FINAL_MAX:
            break

    top = max(e.similarity for e in evidence)
    stale = stale_top is not None and stale_top >= th["low"]
    # The stale unit was the best match: the evidence actually required is stale, so the
    # high-risk answer fails closed rather than falling back to weaker eligible text.
    if stale and stale_top > top:
        return EvidencePack("STALE_HIGH_RISK", release_id=release_id, stale_blocked=True,
                            top_similarity=top)
    lexical_support = any(e.lexical_rank is not None for e in evidence[:3])
    if top >= th["supported"] or (lexical_support and top >= th["supported_with_lexical"]):
        state = "SUPPORTED"
    elif top >= th["low"]:
        state = "LOW_CONFIDENCE"
    else:
        return EvidencePack("NO_SOURCE", release_id=release_id, top_similarity=top,
                            stale_blocked=stale)

    # Keep only units that are themselves plausibly relevant: a weak tail dilutes the pack.
    floor = max(th["low"], top - 0.15)
    evidence = [e for e in evidence if e.similarity >= floor or e.lexical_rank is not None][
        :FINAL_MAX]

    # Claim coverage (RAG §17.2) — lexical, deterministic, applied after relevance.
    blob = " ".join(f"{e.title} {e.heading_path} {e.text}".lower() for e in evidence)
    qterms = _terms(query)
    coverage = None
    uncovered_parts = 0
    if qterms:
        covered = [t for t in qterms if _covered(t, blob)]
        coverage = len(covered) / len(qterms)
        missing = [t for t in qterms if t not in covered]
        qualifiers = {m.group(0).lower() for m in _QUALIFIER.finditer(query)}
        if qualifiers and not all(_qualifier_stated(q, blob) for q in qualifiers):
            # Compliance/residency/guarantee asked, never stated by the evidence (RAG §20).
            return _gap(release_id, top, stale, coverage)
        vocab = _release_vocabulary(db, release_id)
        absent = [t for t in missing if t not in _GENERIC and not _in_vocabulary(t, vocab)]
        if intent in ("PRICING", "SALES", "DEMO") and any(e.source_type == "pricing"
                                                          for e in evidence):
            # K2 states the pricing position for every offering ("no published prices;
            # contact sales"), so the subject of a price question need not appear in it.
            absent = []
        if absent and (coverage <= 0.5 or len(absent) >= 2):
            # The question's subject never appears anywhere in the approved corpus ("CEO",
            # "HDR", "rugby") and little else of it is covered: whatever matched is an
            # adjacent topic, not an answer.
            return _gap(release_id, top, stale, coverage)
        if not covered:
            state = "LOW_CONFIDENCE"
        parts = _query_parts(query)
        if len(parts) > 1:
            part_cov = [any(_covered(t, blob) for t in _terms(p)) for p in parts]
            uncovered_parts = part_cov.count(False)
            if state == "SUPPORTED" and 0 < uncovered_parts < len(parts):
                state = "PARTIAL"  # some requested claims supported, others not (RAG §17)

    conflict = _pricing_conflict(evidence) if intent in ("PRICING", "SALES", "DEMO") else False
    if conflict:
        state = "SOURCE_CONFLICT"
    return EvidencePack(state, evidence, release_id=release_id, conflict=conflict,
                        stale_blocked=stale, top_similarity=top, coverage=coverage,
                        uncovered_parts=uncovered_parts)


def _qualifier_stated(q: str, blob: str) -> bool:
    """A multi-word or numbered qualifier ("SOC 2", "ISO 27001", "united states") must appear
    as such; a single word may appear inflected ("compliant" ~ "compliance")."""
    if q.startswith(_INFLECTED_QUALIFIERS):
        return _covered(q, blob)
    pattern = r"\b" + r"\s?".join(re.escape(p) for p in q.split()) + r"\b"
    return re.search(pattern, blob) is not None


def _gap(release_id: str, top: float, stale: bool, coverage: float | None) -> EvidencePack:
    return EvidencePack("NO_SOURCE", release_id=release_id, top_similarity=top,
                        stale_blocked=stale, coverage=coverage)


def _pricing_conflict(evidence: list[Evidence]) -> bool:
    """Two different authoritative pricing sources quoting different amounts = conflict.
    Guide must not silently pick one (PRD §11.2 SOURCE PRECEDENCE, RAG §19, PG-AT-11)."""
    by_source: dict[str, set[str]] = {}
    for e in evidence:
        if e.source_type == "pricing" and e.authority_rank >= AUTHORITATIVE_RANK:
            by_source.setdefault(e.source_id, set()).update(
                m.replace(" ", "") for m in _MONEY.findall(e.text))
    amounts = [a for a in by_source.values() if a]
    return len(amounts) > 1 and any(a != amounts[0] for a in amounts[1:])


def is_authoritative_pricing(source_type: str, authority_rank: int) -> bool:
    return source_type == "pricing" and authority_rank >= AUTHORITATIVE_RANK


def search(query: str, intent: str, *, locale: str = "en-US",
           jurisdiction: str | None = None) -> EvidencePack:
    """Retrieval on the retrieval store's own connection pool (RAG §7.1)."""
    return retrieve(None, query, intent, locale=locale, jurisdiction=jurisdiction)
