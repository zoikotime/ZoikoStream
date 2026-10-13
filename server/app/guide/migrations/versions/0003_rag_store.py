"""retrieval store: pgvector + full-text in schema guide_rag

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

S = "guide_rag"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    op.create_table(
        "rag_index_releases",
        sa.Column("release_id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("embedding_profile", sa.String(64), nullable=False),
        sa.Column("chunking_profile", sa.String(32), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("unit_count", sa.Integer(), nullable=False),
        sa.Column("build_digest", sa.LargeBinary(), nullable=True),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("previous_release_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('building', 'ready', 'active', 'superseded', 'failed')",
                           name="ck_rag_release_status"),
        schema=S,
    )
    op.create_index("uq_rag_one_active_release", "rag_index_releases", ["status"], unique=True,
                    schema=S, postgresql_where=sa.text("status = 'active'"))
    op.create_table(
        "rag_knowledge_units",
        sa.Column("unit_id", sa.Uuid(), primary_key=True),
        sa.Column("release_id", sa.String(64),
                  sa.ForeignKey(f"{S}.rag_index_releases.release_id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source_version_id", sa.Uuid(),
                  sa.ForeignKey("guide_knowledge_source_versions.source_version_id",
                                ondelete="RESTRICT"), nullable=False),
        sa.Column("source_id", sa.String(96), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("heading_path", sa.String(400), nullable=False),
        sa.Column("unit_type", sa.String(16), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.LargeBinary(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding_profile", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("fts_document", postgresql.TSVECTOR(), sa.Computed(
            "setweight(to_tsvector('english', coalesce(heading_path, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(content_text, '')), 'B')",
            persisted=True)),
        sa.CheckConstraint(
            "unit_type IN ('prose', 'faq', 'table', 'procedure', 'legal', 'pricing', 'code', "
            "'notice')", name="ck_rag_unit_type"),
        schema=S,
    )
    op.create_index("ix_rag_units_release", "rag_knowledge_units", ["release_id"], schema=S)
    op.create_index("ix_rag_units_fts", "rag_knowledge_units", ["fts_document"], schema=S,
                    postgresql_using="gin")
    op.create_index("ix_rag_units_embedding", "rag_knowledge_units", ["embedding"], schema=S,
                    postgresql_using="hnsw",
                    postgresql_ops={"embedding": "vector_cosine_ops"})


def downgrade() -> None:
    op.drop_table("rag_knowledge_units", schema=S)
    op.drop_table("rag_index_releases", schema=S)
    op.execute(f"DROP SCHEMA IF EXISTS {S}")
