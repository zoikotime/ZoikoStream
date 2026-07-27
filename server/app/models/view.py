import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StreamView(Base):
    """One row per unique viewer of a stream, for the org/platform dashboards'
    total_viewers count. See services/views.py for how viewer_key is derived and why
    it's an approximation for anonymous guests."""

    __tablename__ = "stream_views"
    __table_args__ = (UniqueConstraint("stream_id", "viewer_key", name="uq_stream_view_viewer"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("streams.id"), nullable=False, index=True)
    viewer_key: Mapped[str] = mapped_column(String(160), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
