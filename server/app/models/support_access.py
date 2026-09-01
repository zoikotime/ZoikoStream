"""Authorized support access (ZST-EC-001 ORG-009).

This exists because privileged staff access was previously self-approved and invisible to
the customer: `POST /admin/elevation` opened an ElevationSession immediately, with no
organization scope, no case reference, no customer approval and no notification.

The lifecycle here is the authoritative record of who was allowed to do what, to which
tenant, for how long, and on whose approval:

    REQUESTED -> APPROVED -> ACTIVE -> ENDED
              \\-> DENIED        \\-> EXPIRED

`approval_fingerprint` is the control that makes the approval mean something. It is a hash
of the exact terms the customer approved (case, engineer, scope, allowed actions, duration).
A session can only start when the live terms still hash to the approved value, so widening
scope or extending duration after approval invalidates it and forces a new request. Without
that, "approved" would only mean "approved something, once".

Nothing secret is stored here. No customer credentials, no tokens, no session material -
the record describes authorization, and the privileged actions themselves are attributed
through the existing AuditLog (reused deliberately, so there is no parallel hidden trail).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Lifecycle states. "EXPIRING" from the canonical contract is a notification moment, not a
# stored state - a session is still ACTIVE while it is expiring, and inventing a separate
# row state would let a session sit in EXPIRING forever if a ticker missed it.
SUPPORT_REQUESTED = "requested"
SUPPORT_APPROVED = "approved"
SUPPORT_ACTIVE = "active"
SUPPORT_ENDED = "ended"
SUPPORT_EXPIRED = "expired"
SUPPORT_DENIED = "denied"

SUPPORT_STATUSES = (
    SUPPORT_REQUESTED, SUPPORT_APPROVED, SUPPORT_ACTIVE,
    SUPPORT_ENDED, SUPPORT_EXPIRED, SUPPORT_DENIED,
)

# Coarse, customer-safe reason categories. Nothing here reveals detection logic.
SUPPORT_REASONS = (
    "customer_reported_issue",
    "billing_investigation",
    "platform_incident",
    "security_review",
)

# Hard ceiling. The canonical contract forbids open-ended support access, so the duration is
# bounded in the domain rather than trusted to the requester.
SUPPORT_MAX_MINUTES = 480
SUPPORT_EXPIRING_WARNING_MINUTES = 15


class SupportAccessRequest(Base):
    """One scoped, time-limited, customer-approved support session."""

    __tablename__ = "support_access_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # The tenant whose data is in scope. Required: an org-unscoped support session is exactly
    # the thing this model exists to prevent.
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False,
                                              index=True)

    case_reference: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    reason_category: Mapped[str] = mapped_column(String(40), nullable=False)

    # The engineer, by internal id (for attribution) and by an approved professional display
    # identity (for the customer). The customer must be able to tell WHO was authorized
    # without being handed internal account detail.
    engineer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    engineer_display: Mapped[str] = mapped_column(String(120), nullable=False)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    requested_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    allowed_actions: Mapped[list | None] = mapped_column(Text)   # newline-joined, see service
    requested_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default=SUPPORT_REQUESTED, nullable=False,
                                        index=True)

    # Hash of the exact approved terms. Recomputed at activation; a mismatch means the terms
    # moved after approval and the approval no longer applies.
    approval_fingerprint: Mapped[str | None] = mapped_column(String(64))

    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_by_email: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    denied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Emergency / break-glass. Kept because operational safety can genuinely require access
    # before a customer approver is reachable - but it is exceptional, it is announced, and
    # it carries a post-use review obligation.
    emergency: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    emergency_reason: Mapped[str | None] = mapped_column(String(300))
    emergency_authorizer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    emergency_authorizer_email: Mapped[str | None] = mapped_column(String(255))
    post_use_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The elevation actually opened for this request, so the session and the authorization
    # are one linked record rather than two hopeful halves.
    elevation_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # One notification per lifecycle transition, each claimed by conditional UPDATE.
    requested_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiring_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    emergency_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization = relationship("Organization")
    engineer = relationship("User", foreign_keys=[engineer_id])
