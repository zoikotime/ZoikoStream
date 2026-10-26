"""rag unit routing/authorization metadata and release governance

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-23

1. Knowledge-unit schema completed to RAG §7: topic, intent_tags, unit-level locale and
   jurisdiction (pre-retrieval authorization filters, RAG §11 / RAG-AT-17/18), risk_class
   (answerability/freshness policy) and optional valid_from / valid_to temporal eligibility.
   Existing units are backfilled from their source version so a pre-0006 release keeps
   answering identically until the next ingest replaces it.
2. Release manifest governance (RAG Appendix B, EVAL §2.1): evaluation_run_id, approved_by and
   the fusion / reranker / answerability profile ids become columns so "which evaluation
   authorized this release" is queryable without parsing JSON. The rest of the manifest stays
   in the JSONB column.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

S = "guide_rag"


def upgrade() -> None:
    op.add_column("rag_knowledge_units", sa.Column("topic", sa.String(96), nullable=True),
                  schema=S)
    op.add_column("rag_knowledge_units",
                  sa.Column("intent_tags", postgresql.ARRAY(sa.String(32)), nullable=False,
                            server_default=sa.text("'{}'::varchar[]")), schema=S)
    op.add_column("rag_knowledge_units",
                  sa.Column("locale", sa.String(16), nullable=False,
                            server_default="en-US"), schema=S)
    op.add_column("rag_knowledge_units", sa.Column("jurisdiction", sa.String(16), nullable=True),
                  schema=S)
    op.add_column("rag_knowledge_units",
                  sa.Column("risk_class", sa.String(16), nullable=False, server_default="LOW"),
                  schema=S)
    op.add_column("rag_knowledge_units",
                  sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True), schema=S)
    op.add_column("rag_knowledge_units",
                  sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True), schema=S)
    op.create_check_constraint("ck_rag_unit_risk_class", "rag_knowledge_units",
                               "risk_class IN ('LOW', 'MATERIAL', 'HIGH')", schema=S)
    op.create_index("ix_rag_units_eligibility", "rag_knowledge_units",
                    ["release_id", "locale", "jurisdiction"], schema=S)
    # Backfill from the source version / source rows the units already reference.
    op.execute(f"""
        UPDATE {S}.rag_knowledge_units u
           SET locale = v.locale, jurisdiction = v.jurisdiction, risk_class = s.risk_class
          FROM guide_knowledge_source_versions v
          JOIN guide_knowledge_sources s ON s.source_id = v.source_id
         WHERE v.source_version_id = u.source_version_id
    """)

    for name, length in (("evaluation_run_id", 64), ("approved_by", 96),
                         ("fusion_profile", 32), ("reranker_profile", 32),
                         ("answerability_profile", 32)):
        op.add_column("rag_index_releases", sa.Column(name, sa.String(length), nullable=True),
                      schema=S)


def downgrade() -> None:
    for name in ("answerability_profile", "reranker_profile", "fusion_profile", "approved_by",
                 "evaluation_run_id"):
        op.drop_column("rag_index_releases", name, schema=S)
    op.drop_index("ix_rag_units_eligibility", table_name="rag_knowledge_units", schema=S)
    op.drop_constraint("ck_rag_unit_risk_class", "rag_knowledge_units", schema=S)
    for name in ("valid_to", "valid_from", "risk_class", "jurisdiction", "locale", "intent_tags",
                 "topic"):
        op.drop_column("rag_knowledge_units", name, schema=S)
