"""Physical schema invariants (ZST-DB-PUB-GUIDE-001 §13–16, DB-AT-01/09/10)."""
from sqlalchemy import inspect

from app.guide import db as guide_db
from app.guide.models import CIPHERTEXT_COLUMNS, GuideBase

EXPECTED_TABLES = {
    "guide_sessions", "guide_public_event_contexts", "guide_session_context_versions",
    "guide_knowledge_sources", "guide_knowledge_source_versions", "guide_destination_registry",
    "guide_messages", "guide_message_sources", "guide_message_actions", "guide_support_flows",
    "guide_support_step_events", "guide_consents", "guide_lead_flows",
    "guide_lead_field_values", "guide_handoffs", "guide_external_operations", "guide_feedback",
    "guide_runtime_config", "guide_audit_events",
}
CONFIG_TABLES = {"guide_knowledge_sources", "guide_knowledge_source_versions",
                 "guide_destination_registry", "guide_runtime_config",
                 "guide_message_sources", "guide_message_actions"}
FORBIDDEN_COLUMN_WORDS = ("password", "pin", "token", "secret", "card", "email", "ip_addr",
                          "api_key", "capability", "csrf", "idempotency_key")


def test_all_19_tables_exist():
    tables = set(inspect(guide_db.get_engine()).get_table_names())
    assert EXPECTED_TABLES <= tables
    assert {t.name for t in GuideBase.metadata.sorted_tables if t.schema is None} == EXPECTED_TABLES
    rag = set(inspect(guide_db.get_engine()).get_table_names(schema="guide_rag"))
    assert rag == {"rag_index_releases", "rag_knowledge_units"}


def test_no_plaintext_secret_columns():
    insp = inspect(guide_db.get_engine())
    for table in EXPECTED_TABLES:
        for col in insp.get_columns(table):
            name = col["name"]
            if name.endswith("_hash") or name.endswith("_ciphertext") \
                    or name.endswith("_fingerprint") or name == "token_expires_at":
                continue
            assert not any(w in name for w in FORBIDDEN_COLUMN_WORDS), f"{table}.{name}"


def test_ciphertext_columns_are_never_indexed():
    insp = inspect(guide_db.get_engine())
    for table, cols in CIPHERTEXT_COLUMNS.items():
        for index in insp.get_indexes(table):
            assert not set(index["column_names"] or []) & set(cols), (table, index["name"])
        for col in insp.get_columns(table):
            if col["name"] in cols:
                assert "BYTEA" in str(col["type"]).upper()


def test_session_local_rows_require_purge_after():
    insp = inspect(guide_db.get_engine())
    for table in EXPECTED_TABLES - CONFIG_TABLES:
        cols = {c["name"]: c for c in insp.get_columns(table)}
        assert "purge_after" in cols, table
        assert cols["purge_after"]["nullable"] is False, table


def test_lead_field_allowlist_is_enforced_by_database(db):
    import uuid
    from sqlalchemy.exc import IntegrityError
    from datetime import datetime, timezone
    import pytest
    from app.guide.models import GuideLeadFieldValue
    db.add(GuideLeadFieldValue(flow_id=uuid.uuid4(), field_id="ssn", value_ciphertext=b"x",
                               data_class="PII", purge_after=datetime.now(timezone.utc)))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
