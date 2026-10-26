"""Content governance, release manifest and index lifecycle (RBAC §5–8, §17, §22; RAG §23,
Appendix B; EVAL §2.1)."""
import shutil
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from app.guide.knowledge import gate, governance, ingest, retrieval
from app.guide.knowledge.models import RagIndexRelease, RagKnowledgeUnit
from app.guide.settings import get_settings

from conftest import FIXTURES, ingest_corpus, reset_knowledge

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


@pytest.fixture
def fresh_corpus():
    reset_knowledge()
    yield
    reset_knowledge()
    ingest_corpus()


def _meta(**over):
    m = {"source_id": "s", "version": "1", "source_type": "product", "risk_class": "LOW",
         "author": "alice", "owner_role": "product", "approved_by": "bob",
         "approved_at": "2026-10-01"}
    m.update(over)
    return m


def _check(**over):
    m = _meta(**over)
    return governance.check_approval(m, review_days=ingest.REVIEW_DAYS[m["risk_class"]],
                                     now=NOW)


# ── Approval rules (RBAC §5-8) ─────────────────────────────────────────────────────────────

def test_approved_source_passes():
    assert _check().state == "approved"


def test_author_cannot_approve_own_content():  # SOD-01
    c = _check(approved_by="Alice")
    assert c.state == "rejected" and any("separation of duties" in p for p in c.problems)


def test_pricing_needs_commercial_overlay():  # SOD-03
    c = _check(source_type="pricing", risk_class="HIGH", owner_role="commercial")
    assert any("domain approval overlay" in p for p in c.problems)
    ok = _check(source_type="pricing", risk_class="HIGH", owner_role="commercial",
                domain_approvals=[{"domain": "commercial", "approved_by": "carol",
                                   "approved_at": "2026-10-02"}])
    assert ok.state == "approved"
    # Platform admin is not business authority: a security overlay does not approve pricing.
    wrong = _check(source_type="pricing", risk_class="HIGH", owner_role="commercial",
                   domain_approvals=[{"domain": "security", "approved_by": "carol",
                                      "approved_at": "2026-10-02"}])
    assert wrong.state == "rejected"


@pytest.mark.parametrize("stype,domain", [("legal", "privacy"), ("legal", "legal"),
                                          ("trust", "security")])
def test_legal_privacy_security_overlays(stype, domain):  # SOD-04 / SOD-05
    assert _check(source_type=stype, risk_class="HIGH").state == "rejected"
    assert _check(source_type=stype, risk_class="HIGH", domain_approvals=[
        {"domain": domain, "approved_by": "dan", "approved_at": "2026-10-02"}]).state == \
        "approved"


def test_domain_approver_cannot_be_author():
    c = _check(source_type="trust", risk_class="HIGH", domain_approvals=[
        {"domain": "security", "approved_by": "alice", "approved_at": "2026-10-02"}])
    assert c.state == "rejected"


def test_high_risk_author_cannot_publish():  # SOD-01 publisher
    c = governance.check_approval(_meta(risk_class="HIGH", source_type="product"),
                                  review_days=90, now=NOW, publisher="alice")
    assert any("publisher" in p for p in c.problems)


def test_expired_and_pending_approvals_are_pending():
    old = _check(approved_at="2025-01-01")
    assert old.state == "pending" and "expired" in old.pending[0]
    assert _check(approved_by="pending-approval", approved_at=None).state == "pending"


def test_unknown_owner_role_rejected():
    assert _check(owner_role="ceo").state == "rejected"


def test_every_real_source_carries_approval_metadata():
    for d in ingest.load_sources():
        m = d.meta
        assert m.get("owner_role") in governance.OWNER_ROLES, m["source_id"]
        assert "approved_by" in m and "approved_at" in m, m["source_id"]
        required = governance.REQUIRED_DOMAINS.get(m["source_type"])
        if required:
            assert {o["domain"] for o in m["domain_approvals"]} & required, m["source_id"]


def test_pending_refused_in_production_allowed_in_test(monkeypatch, db):
    docs = ingest.load_sources()  # real sources: approval not yet recorded
    _snap, warnings = ingest.check_governance(docs)
    assert warnings  # allowed outside production, with warnings
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(ingest.IngestError, match="content approval refused"):
            ingest.check_governance(docs)
    finally:
        monkeypatch.setenv("ENVIRONMENT", "test")
        get_settings.cache_clear()
    rows = db.execute(text("SELECT metadata FROM guide_audit_events WHERE event_type = "
                           "'content_rejected'")).scalars().all()
    assert {r["source_id"] for r in rows} == {d.meta["source_id"] for d in docs}
    assert all(r["reason"] == "approval_pending_or_expired" for r in rows)


def test_setting_can_refuse_pending_outside_production(monkeypatch):
    monkeypatch.setenv("GUIDE_KB_ALLOW_PENDING_APPROVAL", "false")
    get_settings.cache_clear()
    with pytest.raises(ingest.IngestError):
        ingest.check_governance(ingest.load_sources())


def test_self_approved_fixture_refused_everywhere(fresh_corpus, tmp_path):
    for f in (FIXTURES / "knowledge").glob("*.md"):
        shutil.copy(f, tmp_path / f.name)
    p = tmp_path / "product.md"
    p.write_text(p.read_text().replace("approved_by: test-kb-approver",
                                       "approved_by: test-author"), encoding="utf-8")
    with pytest.raises(ingest.IngestError, match="separation of duties"):
        ingest_corpus(tmp_path)


# ── Manifest, unit metadata, audit (RAG App. B, §7; C9) ────────────────────────────────────

def test_release_manifest_is_complete(db):
    rel = db.scalar(select(RagIndexRelease).where(RagIndexRelease.status == "active"))
    m = rel.manifest
    for key in ("rag_release_id", "index_version", "source_registry_snapshot",
                "embedding_profile", "chunking_profile", "lexical_profile", "fusion_profile",
                "reranker_profile", "answerability_policy_version", "active_source_count",
                "active_unit_count", "build_digest", "evaluation_run_id", "approved_by",
                "activated_at", "previous_release_id", "system_prompt_version",
                "policy_version", "conversation_flow_version", "destination_registry_version",
                "retention_policy_version", "application_commit"):
        assert key in m, key
    assert m["application_commit"] and m["application_commit"] != "None"
    assert m["evaluation"]["passed"] is True
    assert rel.evaluation_run_id == m["evaluation_run_id"]
    assert rel.fusion_profile == "rrf-v1" and rel.reranker_profile == "none"
    snap = m["source_registry_snapshot"]["test-pricing"]
    assert snap["approved_by"] == "test-kb-approver"
    assert snap["domain_approvals"][0]["domain"] == "commercial"


def test_application_commit_prefers_env(monkeypatch):
    monkeypatch.setenv("GUIDE_APP_COMMIT", "abc1234def")
    get_settings.cache_clear()
    assert ingest.application_commit() == "abc1234def"


def test_units_carry_routing_and_authorization_metadata(db):
    u = db.scalar(select(RagKnowledgeUnit).where(RagKnowledgeUnit.source_id == "test-pricing"))
    assert u.topic == "business-plan"
    assert "PRICING" in u.intent_tags and "PRODUCT_QA" not in u.intent_tags
    assert u.locale == "en-US" and u.risk_class == "HIGH" and u.jurisdiction is None


def test_embedding_input_has_topic_line():
    from app.guide.knowledge.embeddings import document_input
    s = document_input("T", "T > Section", "body", "topic-x")
    assert s.splitlines()[:3] == ["Title: T", "Section: T > Section", "Topic: topic-x"]


def test_publish_and_activation_are_audited(fresh_corpus, db):
    ingest_corpus()
    events = db.execute(text("SELECT event_type, metadata FROM guide_audit_events")).all()
    kinds = [e for e, _m in events]
    assert kinds.count("content_published") == 4 and "index_activated" in kinds
    for _e, meta in events:
        assert set(meta) <= {"source_id", "version", "source_version_id", "release_id",
                             "approval_state", "approved_by", "previous_release_id",
                             "evaluation_run_id", "embedding_profile", "fusion_profile",
                             "retention_policy"}


def test_new_release_without_passing_evaluation_cannot_activate(fresh_corpus, db):
    rel = ingest_corpus()
    rel = db.get(RagIndexRelease, rel.release_id)
    rel.status = "ready"
    rel.activated_at = None
    rel.manifest = {**rel.manifest, "evaluation": {"passed": False}}
    db.flush()
    with pytest.raises(ingest.IngestError, match="no passing evaluation"):
        ingest.activate_release(db, rel.release_id)
    db.rollback()


# ── Break-glass (RBAC §17) ─────────────────────────────────────────────────────────────────

def test_break_glass_requires_reason_and_incident(db):
    with pytest.raises(ingest.IngestError, match="reason-code"):
        ingest.break_glass_quarantine(db, "test-help", reason_code="because",
                                      incident="INC-1", actor="x")
    with pytest.raises(ingest.IngestError, match="incident"):
        ingest.break_glass_quarantine(db, "test-help", reason_code="incident", incident="",
                                      actor="x")


def test_break_glass_disable_retrieval_fails_closed(fresh_corpus, db):
    rel = ingest_corpus()
    stood_down = ingest.break_glass_disable_retrieval(
        db, reason_code="security_exposure", incident="INC-77", actor="secops")
    assert stood_down == rel.release_id
    assert retrieval.retrieve(db, "What is the Business plan price?",
                              "PRICING").answerability == "NO_SOURCE"
    meta = db.execute(text("SELECT metadata FROM guide_audit_events WHERE event_type = "
                           "'break_glass'")).scalar_one()
    assert meta["action"] == "disable_retrieval" and meta["reason_code"] == "security_exposure"
    ingest.rollback(db, to=rel.release_id, actor="secops")
    assert retrieval.retrieve(db, "What is the Business plan price?",
                              "PRICING").answerability != "NO_SOURCE"


def test_cli_break_glass_and_releases(fresh_corpus, capsys):
    ingest_corpus()
    assert ingest.main(["releases"]) == 0
    assert ingest.main(["break-glass", "quarantine", "--source", "test-help", "--reason-code",
                        "content_error", "--incident", "CHG-9", "--actor", "kb-oncall"]) == 0
    assert "quarantined" in capsys.readouterr().out
    with pytest.raises(SystemExit):  # --reason-code is mandatory
        ingest.main(["break-glass", "quarantine", "--source", "test-help",
                     "--incident", "CHG-9", "--actor", "kb-oncall"])


# ── Gate internals ─────────────────────────────────────────────────────────────────────────

def test_gate_conflict_selftest_catches_low_authority_canonical_source():
    good = [{"source_id": "p", "source_type": "pricing", "authority_rank": 90}]
    bad = [{"source_id": "p", "source_type": "pricing", "authority_rank": 70}]
    assert gate._conflict_selftest(good) == (1.0, False)
    assert gate._conflict_selftest(bad)[0] == 0.0


def test_gate_metric_math():
    assert gate.reciprocal_rank(["a", "b", "c"], {"b"}) == 0.5
    assert gate.ndcg(["a", "a", "b"], {"a"}) == 1.0
    assert gate.ndcg(["x", "a"], {"a"}) < 1.0
    assert gate.ratio(1, 0) is None


def test_gate_set_applies_only_to_the_approved_corpus(db):
    rid = retrieval.active_release(db)[0]
    result = gate.evaluate(db, rid)  # fixture corpus: the approved-corpus gate set is n/a
    db.rollback()
    assert result.passed and result.gate_set == "not_applicable"
    rows = [{"id": "x", "query": "What is the Business plan price?",
             "expect_sources": ["test-pricing"], "intent_in": ["PRICING"]},
            {"id": "y", "query": "zebra quantum basketball tournament", "no_source": True}]
    result = gate.evaluate(db, rid, gate_set=rows, include_routing=True)
    db.rollback()
    assert result.gate_set == "2 cases" and result.metrics["recall_at_5"] == 1.0
    assert result.metrics["no_source_recall"] == 1.0 and result.passed
