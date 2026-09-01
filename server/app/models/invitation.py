import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .organization import Organization
    from .user import User

# Lifecycle: pending -> accepted | rejected (invitee) | cancelled (admin) | expired (past expires_at).
INVITATION_STATUSES = ("pending", "accepted", "expired", "cancelled", "rejected")


class Invitation(Base):
    """A pending org-membership offer. The raw token is emailed to the invitee and NEVER
    stored — only its sha256 hash lives here (token_hash), looked up on accept/reject.
    One org can't hold two pending invites for the same email (enforced in crud)."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ZST-EC-001 ORG-001/ORG-002 notification markers. One per lifecycle transition, claimed
    # by conditional UPDATE so a page refresh, a retried request or a second worker cannot
    # send the same notice twice. They live on the invitation because the invitation IS the
    # authoritative record of the lifecycle they describe.
    #
    # `reminder_sent_at` doubles as the reminder ticker's idempotency key: at most one
    # reminder per invitation, and rotating the token on resend clears it so the new
    # deadline can be reminded about once.
    invited_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    joined_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped["Organization"] = relationship()
    inviter: Mapped["User | None"] = relationship(foreign_keys=[invited_by_id])
