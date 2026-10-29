"""Retrieval store (RAG §7): knowledge units with lexical + vector representations, grouped
into versioned index releases. Lives in its own schema, guide_rag, so the operational Guide
tables and the derived index never mix (RAG §7.1)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint, Computed, DateTime, ForeignKey, Index, Integer, LargeBinary, SmallInteger,
    String, Text, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from ..db import GuideBase

SCHEMA = "guide_rag"
EMBEDDING_DIM = 384
RELEASE_STATUSES = ("building", "ready", "active", "superseded", "failed")
UNIT_TYPES = ("prose", "faq", "table", "procedure", "legal", "pricing", "code", "notice")
RISK_CLASSES = ("LOW", "MATERIAL", "HIGH")


class RagIndexRelease(GuideBase):
    """Release manifest (RAG Appendix B). Exactly one release is active at a time; activation
    is a single transaction, so queries never see a half-built index (RAG §23.1)."""
    __tablename__ = "rag_index_releases"
    __table_args__ = (
        CheckConstraint("status IN ('building', 'ready', 'active', 'superseded', 'failed')",
                        name="ck_rag_release_status"),
        Index("uq_rag_one_active_release", "status", unique=True,
              postgresql_where=text("status = 'active'")),
        {"schema": SCHEMA},
    )

    release_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    embedding_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    chunking_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    build_digest: Mapped[bytes | None] = mapped_column(LargeBinary)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    previous_release_id: Mapped[str | None] = mapped_column(String(64))
    # Governance (RAG Appendix B; migration 0006): the offline evaluation that authorized
    # activation, who approved it, and the retrieval-config profiles it was evaluated with.
    evaluation_run_id: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(96))
    fusion_profile: Mapped[str | None] = mapped_column(String(32))
    reranker_profile: Mapped[str | None] = mapped_column(String(32))
    answerability_profile: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RagKnowledgeUnit(GuideBase):
    __tablename__ = "rag_knowledge_units"
    __table_args__ = (
        CheckConstraint(
            "unit_type IN ('prose', 'faq', 'table', 'procedure', 'legal', 'pricing', 'code', "
            "'notice')", name="ck_rag_unit_type"),
        CheckConstraint("risk_class IN ('LOW', 'MATERIAL', 'HIGH')", name="ck_rag_unit_risk_class"),
        Index("ix_rag_units_release", "release_id"),
        Index("ix_rag_units_eligibility", "release_id", "locale", "jurisdiction"),
        Index("ix_rag_units_fts", "fts_document", postgresql_using="gin"),
        Index("ix_rag_units_embedding", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
        {"schema": SCHEMA},
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    release_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.rag_index_releases.release_id", ondelete="CASCADE"),
        nullable=False)
    # Exact lineage (RAG §4, RG-02). RESTRICT: a version cannot vanish under a live unit.
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_knowledge_source_versions.source_version_id", ondelete="RESTRICT"),
        nullable=False)
    source_id: Mapped[str] = mapped_column(String(96), nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    heading_path: Mapped[str] = mapped_column(String(400), nullable=False)
    unit_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Retrieval routing + authorization metadata (RAG §7, migration 0006).
    topic: Mapped[str | None] = mapped_column(String(96))
    intent_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list,
        server_default=text("'{}'::varchar[]"))
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-US",
                                        server_default="en-US")
    jurisdiction: Mapped[str | None] = mapped_column(String(16))
    risk_class: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW",
                                            server_default="LOW")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    # Title + heading + text, weighted so a heading match outranks a passing mention.
    fts_document = mapped_column(
        TSVECTOR,
        Computed("setweight(to_tsvector('english', coalesce(heading_path, '')), 'A') || "
                 "setweight(to_tsvector('english', coalesce(content_text, '')), 'B')",
                 persisted=True))
