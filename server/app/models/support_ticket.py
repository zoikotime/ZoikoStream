import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .organization import Organization

TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")


class SupportTicket(Base):
    """A platform support request against an organization. No self-service submission
    flow exists yet (org dashboard has no "contact support" UI) — tickets are logged and
    worked from the Super Admin console."""

    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    requester_email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped["Organization"] = relationship()
