import uuid
from datetime import datetime

from sqlalchemy import DateTime, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditLog(Base):
    """Append-only record of every platform action. Actor is kept by id AND email so the
    entry survives a user deletion (no FK cascade — the log must outlive the actor)."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(80), nullable=False)          # e.g. "organization.update"
    target_type: Mapped[str | None] = mapped_column(String(40))             # organization | user | subscription | setting
    target_id: Mapped[str | None] = mapped_column(String(64))
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meta: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
    # Ties one financial event's whole chain together — provider event -> payment -> invoice
    # -> order -> event -> these audit rows (ZST-LE-COM-001 Section 30 "Event correlation":
    # "A single correlation chain must link event_id, event_order_id, capacity reservation,
    # payment/invoice, service profile, media session, recording and incident"). Indexed
    # because the operational question is always "show me everything for this correlation id".
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
