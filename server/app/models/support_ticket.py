import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .organization import Organization

TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")


class SupportTicket(Base):
    """A platform support request against an organization.

    Originally Super Admin-only: every route lived on the `/admin` router, which is gated at
    router level, so a customer could not open or read a case. ZST-EC-001 SUP-001 adds
    customer-authorized creation on the ORGANIZATION router (routers/organization.py) - the
    admin routes are untouched, so Super Admin capability is unchanged rather than widened.
    """

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

    # ── ZST-EC-001 SUP-001 → SUP-004 ────────────────────────────────────────────────────
    # Every column below is nullable or defaulted, so rows created by the existing Super
    # Admin console keep working exactly as before. `message` above stays the CUSTOMER's own
    # description; see internal_notes / customer_update for the split this family needs.
    #
    # The immutable customer-facing reference. A raw UUID is never quoted to a customer, and
    # this is the value services/support_access.create_request already expects as its
    # `case_reference`.
    case_reference: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    requester_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    category: Mapped[str | None] = mapped_column(String(30))
    # The customer-facing owning TEAM. Deliberately not a staff member's name and not an
    # internal queue id, so a reassignment within one team is not an owner change.
    assigned_owner: Mapped[str | None] = mapped_column(String(80))
    # Only ever set when somebody explicitly commits to a time. Nothing derives it, which is
    # what stops an ETA being invented in the message.
    next_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    # Authoritative classification. Feedback eligibility is decided from this, never from
    # keyword-matching the subject or description.
    sensitivity: Mapped[str] = mapped_column(String(20), default="standard", nullable=False)
    feedback_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Staff-only. Exists so that notes have a home the senders can never reach; no template
    # in app/email.py accepts it, and the SUP tests assert it never appears in a message.
    internal_notes: Mapped[str | None] = mapped_column(Text)
    # The ONLY free text SUP-002 ever mails.
    customer_update: Mapped[str | None] = mapped_column(Text)
    # What the customer must do while WAITING_FOR_CUSTOMER, and when it is needed by.
    pending_action: Mapped[str | None] = mapped_column(Text)
    pending_action_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped each time a NEW action is requested, so one reminder can be sent per request
    # rather than per case.
    action_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Bumped on every reopen. SupportNotice is keyed on this, so a second resolution notice
    # after a reopen is legitimate while a repeated resolve inside one cycle is not.
    lifecycle_cycle: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resolution_summary: Mapped[str | None] = mapped_column(Text)

    organization: Mapped["Organization"] = relationship()
