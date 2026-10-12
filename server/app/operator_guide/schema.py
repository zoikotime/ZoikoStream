"""Operator Guide persistence: its OWN tables (prefix opguide_) in the platform database.

Kept on a separate SQLAlchemy metadata so the platform's create_tables.py never sees them and
they never alter a platform table. Platform rows are referenced by id only (no cross-metadata
foreign keys): Guide data is purgeable on its own schedule without touching platform data.

Apply / update (idempotent, additive):
    cd server && venv/Scripts/python -m app.operator_guide.schema

What is stored (§13, §16):
  * conversations + messages — scoped to (org, user); history is never shared across users.
    Text is redacted for credentials before it is stored.
  * proposals — governed write requests with their confirmation, idempotency and receipt.
  * audit — the §16.1 minimum audit event for every interaction and action.
  * settings — per-organization availability and capability controls (§6.17).
  * feedback — answer ratings.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Identity, Index, Integer, String, Text, Uuid,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class OpGuideBase(DeclarativeBase):
    pass


def _now_col():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Conversation(OpGuideBase):
    __tablename__ = "opguide_conversations"
    __table_args__ = (
        Index("ix_opguide_conv_owner", "org_id", "user_id", text("updated_at DESC")),
        Index("ix_opguide_conv_purge", "purge_after"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="New conversation")
    # Last resolved context (route/workspace/event) — display only, never authority.
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Message(OpGuideBase):
    __tablename__ = "opguide_messages"
    __table_args__ = (Index("ix_opguide_msg_conv", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opguide_conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)          # user | guide
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Guide responses: blocks/sources/actions exactly as rendered (tenant data, no secrets).
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    intent: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = _now_col()


class Proposal(OpGuideBase):
    """A governed write (R2–R4). Nothing executes until an explicit confirmation by an actor
    whose permission is re-checked by the platform endpoint at execution time."""
    __tablename__ = "opguide_proposals"
    __table_args__ = (
        Index("ix_opguide_prop_owner", "org_id", "actor_id", text("created_at DESC")),
        Index("uq_opguide_prop_idem", "org_id", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_class: Mapped[str] = mapped_column(String(4), nullable=False)    # R2 | R3 | R4
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # proposed -> executing -> succeeded | failed | unknown ; or cancelled | expired
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt: Mapped[dict | None] = mapped_column(JSONB)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _now_col()


class AuditEvent(OpGuideBase):
    """§16.1 minimum audit event. Holds references and metadata — never raw credentials."""
    __tablename__ = "opguide_audit"
    __table_args__ = (
        Index("ix_opguide_audit_org", "org_id", text("created_at DESC")),
        Index("ix_opguide_audit_corr", "correlation_id"),
        Index("ix_opguide_audit_purge", "purge_after"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    interaction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role_context: Mapped[str] = mapped_column(String(32), nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)        # message | proposal | action ...
    user_request_hash: Mapped[str | None] = mapped_column(String(64))
    intent: Mapped[str | None] = mapped_column(String(48))
    model_release: Mapped[str | None] = mapped_column(String(96))
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    retrieval_source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tool_plan: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    authorization_result: Mapped[str | None] = mapped_column(String(32))
    tool_execution_receipts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    confirmation_actor: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    final_response_ref: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now_col()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrgSettings(OpGuideBase):
    """Per-organization Guide governance (§6.17). The server enforces these; the UI only
    reflects them. Absent row = defaults (enabled, every capability on)."""
    __tablename__ = "opguide_settings"

    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = _now_col()


class Feedback(OpGuideBase):
    __tablename__ = "opguide_feedback"
    __table_args__ = (Index("uq_opguide_feedback", "message_id", "user_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opguide_messages.id", ondelete="CASCADE"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    rating: Mapped[str] = mapped_column(String(8), nullable=False)     # up | down
    reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _now_col()


TABLES = [t for t in OpGuideBase.metadata.sorted_tables]


def create_schema(engine=None) -> list[str]:
    """Create any missing opguide_* tables (additive, idempotent)."""
    from sqlalchemy import inspect
    if engine is None:
        from app.db import engine as platform_engine
        engine = platform_engine
    existing = set(inspect(engine).get_table_names())
    OpGuideBase.metadata.create_all(engine, checkfirst=True)
    return [t.name for t in TABLES if t.name not in existing]


def schema_ready(db) -> bool:
    from sqlalchemy import inspect
    names = set(inspect(db.get_bind()).get_table_names())
    return all(t.name in names for t in TABLES)


if __name__ == "__main__":
    created = create_schema()
    print("created:", ", ".join(created) if created else "nothing (already up to date)")
