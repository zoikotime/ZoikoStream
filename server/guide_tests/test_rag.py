"""Ingestion, index lifecycle and retrieval (ZST-AI-RAG-PUB-GUIDE-001; RAG-AT-*)."""
import shutil

import pytest
from sqlalchemy import func, select, text

from app.guide.knowledge import ingest
from app.guide.knowledge.models import RagIndexRelease, RagKnowledgeUnit
from app.guide.knowledge.retrieval import retrieve
from app.guide.models import GuideKnowledgeSourceVersion

from conftest import FIXTURES, ingest_corpus, reset_knowledge


@pytest.fixture
def fresh_corpus():
    """Tests that change the corpus get a clean registry and restore the default after."""
    reset_knowledge()
    yield
    reset_knowledge()
    ingest_corpus()


def _write(tmp_path, name, body):
    (tmp_path / name).write_text(body, encoding="utf-8")


# ── Segmentation ───────────────────────────────────────────────────────────────────────────

def test_real_site_corpus_parses():
    docs = ingest.load_sources()  # the approved public pages
    assert len(docs) >= 5
    for d in docs:
        assert d.meta["canonical_path"].startswith("/")
        assert all(u.text.strip() for u in d.units)
        assert not any(u.text.startswith("# ") for u in d.units)  # H1 never a unit


def test_faq_is_one_unit_per_question():
    doc = ingest.parse_file(FIXTURES / "knowledge" / "help.md")
    faqs = [u for u in doc.units if u.unit_type == "faq"]
    assert len(faqs) == 2
    assert faqs[0].text.startswith("Q: Which browsers are supported?\nA: Use a current")


def test_pricing_section_stays_whole():  # RAG-AT-27
    doc = ingest.parse_file(FIXTURES / "knowledge" / "pricing.md")
    (unit,) = doc.units
    assert unit.unit_type == "pricing"
    assert "$249 per month" in unit.text and "$2,490 per year" in unit.text


def test_front_matter_is_required(tmp_path):
    _write(tmp_path, "bad.md", "no front matter\n")
    with pytest.raises(ingest.IngestError):
        ingest.load_sources(tmp_path)


def test_relative_canonical_path_required(tmp_path):
    body = (FIXTURES / "knowledge" / "product.md").read_text().replace(
        'canonical_path: "/"', 'canonical_path: "https://evil.example"')
    _write(tmp_path, "p.md", body)
    with pytest.raises(ingest.IngestError):
        ingest.load_sources(tmp_path)


# ── Lifecycle ──────────────────────────────────────────────────────────────────────────────

def test_one_active_release_and_lineage(db):
    active = db.scalars(select(RagIndexRelease).where(RagIndexRelease.status == "active")).all()
    assert len(active) == 1
    orphan = db.scalar(
        select(func.count()).select_from(RagKnowledgeUnit)
        .join(GuideKnowledgeSourceVersion, GuideKnowledgeSourceVersion.source_version_id
              == RagKnowledgeUnit.source_version_id)
        .where(RagKnowledgeUnit.release_id == active[0].release_id,
               GuideKnowledgeSourceVersion.status != "published"))
    assert orphan == 0  # RAG-AT-25


def test_reingest_supersedes_atomically(fresh_corpus, db):
    first = ingest_corpus()
    second = ingest_corpus()
    db.expire_all()
    assert db.get(RagIndexRelease, first.release_id).status == "superseded"
    assert db.get(RagIndexRelease, second.release_id).status == "active"


def test_changed_content_without_version_bump_is_refused(fresh_corpus, db, tmp_path):
    for f in (FIXTURES / "knowledge").glob("*.md"):
        shutil.copy(f, tmp_path / f.name)
    ingest_corpus(tmp_path)
    p = tmp_path / "product.md"
    p.write_text(p.read_text().replace("invited audiences", "everyone"), encoding="utf-8")
    with pytest.raises(ingest.IngestError, match="not bumped"):
        ingest_corpus(tmp_path)


def test_removed_source_is_retired_and_unretrievable(fresh_corpus, db, tmp_path):
    for f in (FIXTURES / "knowledge").glob("*.md"):
        shutil.copy(f, tmp_path / f.name)
    ingest_corpus(tmp_path)
    assert retrieve(db, "Which browsers are supported?", "PLAYBACK").evidence
    (tmp_path / "help.md").unlink()
    ingest_corpus(tmp_path)
    status = db.scalar(select(GuideKnowledgeSourceVersion.status).where(
        GuideKnowledgeSourceVersion.source_id == "test-help"))
    assert status == "retired"
    pack = retrieve(db, "Which browsers are supported?", "PLAYBACK")
    assert all(e.source_id != "test-help" for e in pack.evidence)  # RAG-AT-03


# ── Retrieval ──────────────────────────────────────────────────────────────────────────────

def test_product_question_retrieves_canonical_source(db):  # RAG-AT-01/12
    pack = retrieve(db, "Can you stream a memorial service for family?", "DISCOVERY")
    assert pack.answerability == "SUPPORTED"
    assert pack.evidence[0].source_id == "test-product"


def test_pricing_intent_filters_before_rank(db):  # RAG-AT-04, ARCH-AT-13/14
    pack = retrieve(db, "What is the Business plan price?", "PRICING")
    assert {e.source_id for e in pack.evidence} == {"test-pricing"}
    # The injection note mentions pricing but is a product source: ineligible for PRICING.
    assert all("evil.example" not in e.text for e in pack.evidence)


def test_quarantined_version_is_ineligible_immediately(db):  # RAG-AT-22
    db.execute(text("UPDATE guide_knowledge_source_versions SET status = 'quarantined' "
                    "WHERE source_id = 'test-pricing'"))
    db.flush()
    pack = retrieve(db, "What is the Business plan price?", "PRICING")
    assert pack.answerability == "NO_SOURCE"
    db.rollback()


def test_stale_high_risk_source_is_blocked(db):  # RAG-AT-06
    db.execute(text("UPDATE guide_knowledge_source_versions SET review_due_at = now() - "
                    "interval '1 day' WHERE source_id = 'test-pricing'"))
    db.flush()
    pack = retrieve(db, "What is the Business plan price?", "PRICING")
    assert pack.answerability == "STALE_HIGH_RISK"
    db.rollback()


def test_unrelated_question_has_no_source(db):  # RAG-AT-19
    assert retrieve(db, "zebra quantum basketball tournament", "PRODUCT_QA"
                    ).answerability == "NO_SOURCE"


def test_wrong_locale_is_filtered(db):  # RAG-AT-18
    pack = retrieve(db, "memorial service", "DISCOVERY", locale="fr-FR")
    assert pack.answerability == "NO_SOURCE"


def test_conflicting_pricing_sources(fresh_corpus, db):  # RAG-AT-05
    ingest_corpus(FIXTURES / "knowledge_conflict")
    pack = retrieve(db, "How much is the Business plan per month?", "PRICING")
    assert pack.answerability == "SOURCE_CONFLICT"


def _real_pricing_front_matter(source_id: str, title: str) -> str:
    """The REAL approved pricing source's front matter (authority rank, type, risk), with a
    test identity — so conflict detection is proven against the production rank, not a
    fixture value chosen to pass."""
    raw = (ingest.SOURCES_DIR / "pricing-plans.md").read_text(encoding="utf-8")
    meta = raw.replace("\r\n", "\n").split("---\n")[1]
    meta = meta.replace("source_id: pricing-plans", f"source_id: {source_id}")
    meta = meta.replace("title: ZoikoStream Pricing and Plans", f"title: {title}")
    return meta


def test_conflict_detection_uses_real_pricing_authority(fresh_corpus, db, tmp_path):
    # RAG-AT-05 with the production front matter: before the fix the real source carried
    # authority_rank 70, below the detector's authoritative floor, so this conflict was missed.
    for sid, title, amount in (("pricing-a", "Pricing A", "$249"),
                               ("pricing-b", "Pricing B", "$199")):
        meta = _real_pricing_front_matter(sid, title)
        assert "authority_rank: 90" in meta
        (tmp_path / f"{sid}.md").write_text(
            f"---\n{meta}---\n\n## Business plan\n\nThe Business plan costs {amount} per month, "
            "billed monthly.\n", encoding="utf-8")
    ingest_corpus(tmp_path)
    pack = retrieve(db, "How much is the Business plan per month?", "PRICING")
    assert pack.answerability == "SOURCE_CONFLICT" and pack.conflict


def test_deploy_skips_reindexing_unchanged_knowledge(db):
    """Deploy-time refresh (app/guide/deploy.py) rebuilds only when the sources changed."""
    from conftest import FIXTURES
    from app.guide.knowledge import ingest
    from app.guide.settings import get_settings
    docs = ingest.load_sources(FIXTURES / "knowledge")
    profile = get_settings().GUIDE_EMBEDDING_PROFILE
    assert ingest.is_current(db, docs, profile)          # the session fixture already built it
    assert not ingest.is_current(db, docs[:-1], profile)  # a removed source changes the digest
    assert not ingest.is_current(db, docs, "fastembed:other-model")


# ── RAG §30 acceptance tests (RG-12) ───────────────────────────────────────────────────────
# Covered elsewhere: RAG-AT-07 (PIN -> restricted before retrieval) test_messages.py
# parametrized restricted routes + test_a3_retrieval.py; RAG-AT-08/09 (event/outage questions
# route to D2/D1) test_event_answers.py / test_a3_retrieval.py; RAG-AT-30 (retrieval down)
# test_messages.py::test_retrieval_outage_never_answers_from_memory.

def _copy_fixtures(tmp_path):
    for f in (FIXTURES / "knowledge").glob("*.md"):
        shutil.copy(f, tmp_path / f.name)


def _source(tmp_path, name, sid, stype, body, *, risk="LOW", extra=""):
    domain = {"pricing": "commercial", "legal": "legal", "trust": "security"}.get(stype)
    overlay = (f"domain_approvals:\n  - {{domain: {domain}, approved_by: test-domain-approver, "
               f"approved_at: 2026-10-01}}\n") if domain else ""
    (tmp_path / name).write_text(
        f"---\nsource_id: {sid}\ntitle: {sid}\nsource_type: {stype}\ncanonical_path: \"/\"\n"
        f"risk_class: {risk}\nauthority_rank: 90\nversion: \"t1\"\nauthor: test-author\n"
        f"owner_role: product\napproved_by: test-kb-approver\napproved_at: 2026-10-01\n"
        f"{overlay}{extra}---\n\n{body}\n", encoding="utf-8")


def test_draft_version_excluded_before_ranking(db):  # RAG-AT-02
    db.execute(text("UPDATE guide_knowledge_source_versions SET status = 'draft' "
                    "WHERE source_id = 'test-product'"))
    db.flush()
    pack = retrieve(db, "Can you stream a memorial service for family?", "DISCOVERY")
    assert all(e.source_id != "test-product" for e in pack.evidence)
    assert "test-product" not in pack.candidates
    db.rollback()


def test_not_public_or_expired_versions_excluded(db):  # RAG §11 public flag / time window
    db.execute(text("UPDATE guide_knowledge_source_versions SET public_retrieval = false "
                    "WHERE source_id = 'test-product'"))
    db.execute(text("UPDATE guide_knowledge_source_versions SET expires_at = now() - "
                    "interval '1 hour' WHERE source_id = 'test-help'"))
    db.flush()
    assert "test-product" not in retrieve(db, "memorial service", "DISCOVERY").candidates
    assert "test-help" not in retrieve(db, "Which browsers are supported?",
                                       "PLAYBACK").candidates
    db.rollback()


def test_unit_validity_window_filters(db):  # RAG §7 valid_from / valid_to
    db.execute(text("UPDATE guide_rag.rag_knowledge_units SET valid_to = now() - "
                    "interval '1 minute' WHERE source_id = 'test-help'"))
    db.flush()
    assert "test-help" not in retrieve(db, "Which browsers are supported?",
                                       "PLAYBACK").candidates
    db.rollback()


def test_exact_developer_endpoint_query(fresh_corpus, db, tmp_path):  # RAG-AT-13
    _copy_fixtures(tmp_path)
    _source(tmp_path, "dev.md", "test-developer", "developer",
            "## Events API v2\n\nPOST /v2/events creates an event. Requires the events:write "
            "scope.\n\n```\ncurl -X POST /v2/events\n```")
    ingest_corpus(tmp_path)
    pack = retrieve(db, "POST /v2/events endpoint", "DEVELOPER")
    assert pack.evidence and pack.evidence[0].source_id == "test-developer"
    assert pack.evidence[0].lexical_rank is not None  # the lexical channel found it
    # Developer intent is K7 only (RAG §10.1): product copy is never developer evidence.
    assert {e.source_type for e in pack.evidence} == {"developer"}


def test_vector_channel_contributes(db):  # RAG-AT-14 (vector path recovers evidence)
    pack = retrieve(db, "memorial service relatives attend", "DISCOVERY")
    assert any(e.vector_rank is not None for e in pack.evidence)


def test_near_duplicate_units_collapse(fresh_corpus, db, tmp_path):  # RAG-AT-15
    _copy_fixtures(tmp_path)
    para = ("Families can stream a memorial service to relatives who cannot attend in person, "
            "with access limited to invited viewers and a private link for each guest.")
    _source(tmp_path, "dup.md", "test-dup", "product",
            f"## Memorial streams\n\n{para}\n\n## Memorial streaming\n\n{para} Thank you.")
    ingest_corpus(tmp_path)
    pack = retrieve(db, "memorial service relatives private link", "DISCOVERY")
    dup_units = [e for e in pack.evidence if e.source_id == "test-dup"]
    assert len(dup_units) == 1


def test_wrong_jurisdiction_legal_source_filtered(fresh_corpus, db, tmp_path):  # RAG-AT-17
    _copy_fixtures(tmp_path)
    _source(tmp_path, "eu.md", "test-legal-eu", "legal",
            "## EU data rights\n\nEU residents may request erasure of personal data.",
            risk="HIGH", extra="jurisdiction: EU\n")
    ingest_corpus(tmp_path)
    q = "Can EU residents request erasure of personal data?"
    assert "test-legal-eu" not in retrieve(db, q, "PRIVACY").candidates          # unknown
    assert "test-legal-eu" not in retrieve(db, q, "PRIVACY", jurisdiction="US").candidates
    assert "test-legal-eu" in retrieve(db, q, "PRIVACY", jurisdiction="EU").candidates


def test_one_canonical_source_gives_compact_pack(db):  # RAG-AT-20
    pack = retrieve(db, "What is the Business plan price?", "PRICING")
    assert 1 <= len(pack.evidence) <= 6
    assert {e.source_id for e in pack.evidence} == {"test-pricing"}


def test_long_legal_section_keeps_qualifier(tmp_path):  # RAG-AT-26
    body = ("## Retention\n\n" + "Personal data is kept for the period described here. " * 60
            + "\n\nExcept where law requires longer retention, data is deleted after 30 days.")
    _source(tmp_path, "legal.md", "test-legal", "legal", body, risk="HIGH")
    (unit,) = ingest.parse_file(tmp_path / "legal.md").units
    assert unit.unit_type == "legal" and "Except where law requires" in unit.text


def test_failed_regression_blocks_promotion(fresh_corpus, db, monkeypatch):  # RAG-AT-21
    first = ingest_corpus()
    from app.guide.knowledge import gate
    real = gate.evaluate

    def failing(db_, release_id, **kw):
        result = real(db_, release_id, **kw)
        result.passed, result.failures = False, ["recall_at_5 0.5 < 0.95"]
        return result
    monkeypatch.setattr(gate, "evaluate", failing)
    with pytest.raises(ingest.IngestError, match="activation gate failed"):
        ingest_corpus()
    db.expire_all()
    assert db.get(RagIndexRelease, first.release_id).status == "active"  # unchanged
    failed = db.scalars(select(RagIndexRelease).where(RagIndexRelease.status == "failed")).all()
    assert failed and failed[0].manifest["evaluation"]["passed"] is False


def test_rollback_restores_prior_release_without_quarantined_source(
        fresh_corpus, db, tmp_path):  # RAG-AT-24
    _copy_fixtures(tmp_path)
    a = ingest_corpus(tmp_path)
    p = tmp_path / "product.md"
    p.write_text(p.read_text().replace('version: "t1"', 'version: "t2"')
                 .replace("invited audiences", "invited audiences worldwide"), encoding="utf-8")
    b = ingest_corpus(tmp_path)
    # Superseding did NOT retire A's product version: it is A's lineage for rollback.
    statuses = dict(db.execute(text(
        "SELECT version, status FROM guide_knowledge_source_versions "
        "WHERE source_id = 'test-product'")).all())
    assert statuses == {"t1": "published", "t2": "published"}
    # Only the ACTIVE release's version is eligible.
    q = "stream scheduled live events to invited audiences"
    pack = retrieve(db, q, "PRODUCT_QA")
    assert {e.version for e in pack.evidence if e.source_id == "test-product"} == {"t2"}
    ingest.break_glass_quarantine(db, "test-help", reason_code="incident",
                                  incident="INC-1234", actor="sre-oncall")
    target = ingest.rollback(db, actor="sre-oncall")
    db.expire_all()
    assert target.release_id == a.release_id
    assert db.get(RagIndexRelease, a.release_id).status == "active"
    assert db.get(RagIndexRelease, b.release_id).status == "superseded"
    pack = retrieve(db, q, "PRODUCT_QA")
    assert {e.version for e in pack.evidence if e.source_id == "test-product"} == {"t1"}
    assert "test-help" not in retrieve(db, "Which browsers are supported?",
                                       "PLAYBACK").candidates
    assert ingest.excluded_sources(db, a.release_id) == ["test-help"]
    events = db.execute(text("SELECT event_type FROM guide_audit_events")).scalars().all()
    assert "index_rolled_back" in events and "break_glass" in events


def test_reranker_absent_raises_answerability_bar():  # RAG-AT-29
    from app.guide.knowledge import retrieval as R
    assert R.RERANKER_PROFILE == "none"
    th = R._thresholds(R.LOCAL_PROFILE, "PRODUCT_QA")
    assert th["supported"] == pytest.approx(
        R.THRESHOLDS[R.LOCAL_PROFILE]["supported"] + R.NO_RERANKER_MARGIN)
