import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

RELEASE_CHANNELS = ("production", "staging", "beta")


class Release(Base):
    """An admin-authored changelog entry. There's no CI/CD integration to source this
    from automatically, so it's a manual log super admins publish — real entries, not a
    fabricated deploy history."""

    __tablename__ = "releases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(20), default="production", nullable=False)
    released_by: Mapped[str | None] = mapped_column(String(255))
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ── ZST-EC-001 MKT-001 ──────────────────────────────────────────────────────────────
    # `notes` above is INTERNAL admin prose and is never mailed to a customer. A release
    # becomes distributable only when a person writes a customer-facing summary and another
    # explicitly approves it. Nothing derives approval from `released_at` existing: a logged
    # release is a changelog entry, not permission to email anyone.
    customer_summary: Mapped[str | None] = mapped_column(Text)
    customer_visible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    documentation_path: Mapped[str | None] = mapped_column(String(300))
    # Where the rollout has actually reached, when that is a real fact worth stating.
    rollout_status: Mapped[str | None] = mapped_column(String(60))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
