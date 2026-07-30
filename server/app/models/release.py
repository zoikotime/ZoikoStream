import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Release(Base):
    """Admin-authored changelog entry. There's no CI/CD integration to source this
    from, so every row is manually published through /admin/releases."""

    __tablename__ = "releases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    version: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="production", nullable=False)
    released_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
