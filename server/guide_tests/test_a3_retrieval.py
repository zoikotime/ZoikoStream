"""Retrieval behaviour added for the RAG spec gaps: §10.1 allowlist, §11 filters, §12.1 RRF,
§13 no-reranker posture, §14 near-duplicates, §17 answerability incl. PARTIAL, §7.1 own pool,
§26 metrics."""
import uuid

import pytest
from sqlalchemy import text

from app.guide import metrics, orchestrator
from app.guide.knowledge import retrieval as R
from app.guide.knowledge import store
from app.guide.settings import get_settings


# ── Allowlist (RAG §10.1) ──────────────────────────────────────────────────────────────────

def test_intent_allowlist_matches_spec_exactly():
    classes = R.INTENT_CLASSES
    assert classes["PRODUCT_QA"] == ("K1", "K3", "K4", "K6", "K7")
    assert classes["DISCOVERY"] == ("K1", "K8")
    assert classes["PRICING"] == ("K2", "K8")
    assert classes["DEVELOPER"] == ("K7",)
    assert set(classes["PRIVACY"]) == {"K5", "K4"} and set(classes["SECURITY_TRUST"]) == {
        "K4", "K5"}
    for viewer in ("VIEWER_ACCESS", "PLAYBACK", "AUDIO", "CAPTIONS", "REPLAY"):
        assert set(classes[viewer]) == {"K3", "K6"}
    pq = R.INTENT_SOURCES["PRODUCT_QA"]
    assert "pricing" not in pq and "legal" not in pq and "sales_help" not in pq
    assert R.INTENT_SOURCES["DEVELOPER"] == ("developer",)


def test_topic_refinement_selects_another_spec_profile_only():
    assert R.retrieval_intent("What are the fees for a wedding stream?", "DISCOVERY") == "PRICING"
    assert R.retrieval_intent("What data do you keep about children?", "PRODUCT_QA") == "PRIVACY"
    assert R.retrieval_intent("How do I contact ZoikoStream?", "PRODUCT_QA") == "SALES"
    assert R.retrieval_intent("Do you support captions?", "PRODUCT_QA") == "PRODUCT_QA"
    assert R.retrieval_intent("contact the team", "PLAYBACK") == "PLAYBACK"  # never widened


# ── Fusion profiles (RAG §12.1) ────────────────────────────────────────────────────────────

class _Row:
    def __init__(self, sim, heading="x", rank=90):
        self.sim, self.heading_path, self.authority_rank = sim, heading, rank


def test_rrf_is_default_and_rank_based(monkeypatch):
    assert R.fusion_profile() == "rrf-v1"
    a = {"row": _Row(0.90), "vrank": 1, "lrank": None}       # vector-only, very similar
    b = {"row": _Row(0.70), "vrank": 2, "lrank": 1}          # in both channels
    assert R.fuse([a, b], "q", "rrf-v1")[0] is b              # rank fusion rewards agreement
    assert R.fuse([a, b], "q", "similarity-v1")[0] is a       # selectable legacy profile
    monkeypatch.setenv("GUIDE_RAG_FUSION_PROFILE", "similarity-v1")
    get_settings.cache_clear()
    assert R.fusion_profile() == "similarity-v1"
    monkeypatch.setenv("GUIDE_RAG_FUSION_PROFILE", "bogus")
    get_settings.cache_clear()
    assert R.fusion_profile() == "rrf-v1"


def test_pack_records_profiles(db):
    pack = R.retrieve(db, "What is the Business plan price?", "PRICING")
    assert pack.fusion_profile == "rrf-v1" and pack.reranker_profile == "none"
    assert pack.conflict_state == "NONE" and pack.freshness_state == "CURRENT"


# ── Answerability (RAG §17, §20) ───────────────────────────────────────────────────────────

def test_partial_when_one_requested_claim_is_unsupported(db):
    pack = R.retrieve(db, "Which browsers are supported? Also, can I pay with bitcoin dollars?",
                      "PLAYBACK")
    assert pack.answerability in ("PARTIAL", "NO_SOURCE")
    pack = R.retrieve(db, "Which browsers are supported? Why does the video buffer?",
                      "PLAYBACK")
    assert pack.answerability == "SUPPORTED"


def test_partial_state_machine_directly():
    assert R._query_parts("What are captions? What is replay?") == ["What are captions",
                                                                     " What is replay"]
    assert R._query_parts("just one question") == ["just one question"]


def test_subject_absent_from_corpus_is_no_source(db):
    # "pricing" retrieves the price page, but the subject (an unrelated company fact) never
    # appears in the approved corpus -> NO_SOURCE, not a confident adjacent answer.
    pack = R.retrieve(db, "Who is the CEO of the company behind live events?", "PRODUCT_QA")
    assert pack.answerability == "NO_SOURCE"


def test_compliance_qualifier_needs_explicit_evidence(db):
    pack = R.retrieve(db, "Is the Business plan SOC 2 certified?", "PRICING")
    assert pack.answerability == "NO_SOURCE"


def test_query_typos_are_normalized_against_the_release_vocabulary():
    vocab = frozenset({"browsers", "supported", "buffering"})
    assert R.normalize_query("Which brwosers are suported?", vocab) == \
        "Which browsers are supported?"
    assert R.normalize_query("Zoikostream rocks", vocab) == "Zoikostream rocks"


def test_near_duplicate_detection():
    a = "Families can stream a memorial service to relatives who cannot attend in person."
    assert R.near_duplicate(a, a + " Thanks.")
    assert not R.near_duplicate(a, "Use a current version of Chrome, Edge, Firefox or Safari.")


def test_stale_high_risk_blocked_before_ranking(db):
    db.execute(text("UPDATE guide_knowledge_source_versions SET review_due_at = now() - "
                    "interval '1 day' WHERE source_id = 'test-pricing'"))
    db.flush()
    pack = R.retrieve(db, "What is the Business plan price?", "PRICING")
    assert "test-pricing" not in pack.candidates  # never entered the ranked candidate set
    assert pack.answerability == "STALE_HIGH_RISK" and pack.stale_blocked
    db.rollback()


def test_unknown_source_class_is_never_eligible():  # RAG-AT-10/11: corpus boundary
    for intent, types in R.INTENT_SOURCES.items():
        assert "public_event" not in types, intent  # D2 never enters the static index


# ── Own pool (RAG §7.1) and metrics (RAG §26) ──────────────────────────────────────────────

def test_retrieval_store_has_its_own_pool(db):
    store.reset_engine()
    try:
        assert store.rag_database_url() == get_settings().GUIDE_DATABASE_URL
        from app.guide import db as guide_db
        assert store.get_engine() is not guide_db.get_engine()
        pack = R.search("What is the Business plan price?", "PRICING")  # db=None path
        assert pack.evidence and pack.evidence[0].source_id == "test-pricing"
    finally:
        store.reset_engine()


def test_rag_metrics_are_emitted(db):
    R.retrieve(db, "What is the Business plan price?", "PRICING")
    R.retrieve(db, "zebra quantum basketball tournament", "PRODUCT_QA")
    out = metrics.render()
    assert "rag_retrieval_seconds" in out and "rag_candidates" in out
    assert 'rag_answerability_total{state="SUPPORTED"}' in out
    assert 'rag_answerability_total{state="NO_SOURCE"}' in out


# ── Routing never reaches static retrieval (RAG-AT-07/08/09) ───────────────────────────────

@pytest.fixture
def leave_no_rows():
    """Session rows left behind would block the next run's schema downgrade."""
    yield
    from app.guide import db as guide_db
    from conftest import _LOCAL_TABLES
    with guide_db.get_engine().begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(_LOCAL_TABLES) + " CASCADE"))


@pytest.mark.parametrize("text,page", [
    ("What is the PIN for the event?", "home"),        # RAG-AT-07
    ("Is ZoikoStream down right now?", "home"),        # RAG-AT-09 -> D1
    ("What time does the event start?", "viewer"),     # RAG-AT-08 -> D2 / generic help
])
def test_dynamic_or_private_questions_bypass_static_rag(client, monkeypatch, leave_no_rows,
                                                       text, page):
    from conftest import create_session
    called = []
    real = orchestrator.retrieval.retrieve
    monkeypatch.setattr(orchestrator.retrieval, "retrieve",
                        lambda *a, **k: called.append(1) or real(*a, **k))
    g = create_session(client, page_type=page, page_id=page, path="/" if page == "home"
                       else "/events/e1/watch")
    res = g.post("/messages", {"client_message_id": str(uuid.uuid4()), "text": text})
    assert res.status_code == 200, res.text
    assert not called
