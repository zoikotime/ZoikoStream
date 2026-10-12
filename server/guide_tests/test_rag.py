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
