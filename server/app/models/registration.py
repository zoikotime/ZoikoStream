import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (UniqueConstraint("stream_id", "email", name="uq_registration_stream_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("streams.id"), nullable=False, index=True)

    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    company: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # "self" = registered themselves via the public event page; "bulk" = pre-registered by
    # an org admin/host importing a list.
    source: Mapped[str] = mapped_column(String(10), default="self", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="confirmed", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
