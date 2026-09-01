"""Organization governance domains (ZST-EC-001 ORG-007, ORG-008, ORG-010).

Three small authoritative domains, each added because the corresponding email family would
otherwise have had nothing real to report:

  * AccessReview / AccessReviewAssignment - ORG-007. The decision column defaults to
    PENDING and there is no code path that turns silence into APPROVED. Implicit approval is
    the single most dangerous thing an access review can do, so it is designed out rather
    than merely avoided.
  * OwnershipTransfer - ORG-008. Ownership moves only after BOTH parties confirm, in the
    authenticated console, before a real expiry. An email link click is never sufficient.
  * OrgOperationalEvent - ORG-010. One row per committed organization state transition,
    carrying the coarse reason category the customer is allowed to see.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# ── ORG-007 access review ───────────────────────────────────────────────────────────────

REVIEW_OPEN = "open"
REVIEW_OVERDUE = "overdue"
REVIEW_COMPLETED = "completed"
REVIEW_STATUSES = (REVIEW_OPEN, REVIEW_OVERDUE, REVIEW_COMPLETED)

# PENDING is the default and the only state a reviewer's silence can leave a row in.
DECISION_PENDING = "pending"
DECISION_APPROVED = "approved"
DECISION_CHANGE_REQUIRED = "change_required"
DECISION_REMOVE = "remove"
DECISION_EXCEPTION = "exception"
REVIEW_DECISIONS = (DECISION_PENDING, DECISION_APPROVED, DECISION_CHANGE_REQUIRED,
                    DECISION_REMOVE, DECISION_EXCEPTION)

REVIEW_REMINDER_BEFORE_DUE_HOURS = 48

# How a reviewer was designated, most-authoritative first. Deterministic and explicit: the
# owner reviews administrators, an administrator reviews everyone else, and the fallback is
# named rather than being "whoever came back first".
REVIEWER_OWNER = "organization_owner"
REVIEWER_SECURITY_ADMIN = "organization_admin"
REVIEWER_FALLBACK_CREATOR = "review_creator"
REVIEWER_UNASSIGNED = "unassigned"


class AccessReview(Base):
    """A periodic confirmation that each member's access is still necessary."""

    __tablename__ = "access_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False,
                                              index=True)
    status: Mapped[str] = mapped_column(String(20), default=REVIEW_OPEN, nullable=False,
                                        index=True)
    review_period: Mapped[str | None] = mapped_column(String(60))

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    # Recorded when the review passes its due date. There is no escalation SUBSYSTEM in this
    # codebase, so this marks the policy state and the audit trail; it does not claim an
    # escalation workflow ran. See the reported gap.
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    opened_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overdue_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization = relationship("Organization")
    assignments = relationship("AccessReviewAssignment", back_populates="review",
                               cascade="all, delete-orphan")


class AccessReviewAssignment(Base):
    """One member's access, put to one reviewer.

    The role/workspace snapshot is stored as rendered text taken when the review opened, so
    the reviewer decides on what access looked like at that moment rather than on whatever
    it has since become.
    """

    __tablename__ = "access_review_assignments"
    __table_args__ = (
        UniqueConstraint("review_id", "member_id", name="uq_review_member"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("access_reviews.id"), nullable=False,
                                                 index=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    reviewer_email: Mapped[str | None] = mapped_column(String(255))

    member_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    member_email: Mapped[str] = mapped_column(String(255), nullable=False)
    member_name: Mapped[str | None] = mapped_column(String(120))
    access_snapshot: Mapped[str | None] = mapped_column(Text)

    # How this reviewer was chosen. Recorded so a review can be audited for WHO was asked,
    # not just what they answered — the previous implementation silently handed every
    # assignment to whichever admin the query returned first.
    reviewer_source: Mapped[str | None] = mapped_column(String(30))

    decision: Mapped[str] = mapped_column(String(24), default=DECISION_PENDING, nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(String(300))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    # An EXCEPTION decision must name an accountable owner - an exception nobody owns is an
    # exception nobody will revisit.
    exception_owner_email: Mapped[str | None] = mapped_column(String(255))
    # True when the exception keeps an administrator-level grant in place.
    high_risk: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    review = relationship("AccessReview", back_populates="assignments")


class AccessReviewEscalation(Base):
    """A durable record that an overdue item was escalated (ZST-EC-001 ORG-007).

    The previous implementation marked `AccessReview.escalated_at` and said, honestly, that
    no escalation subsystem stood behind it. This is that subsystem's minimum viable form:
    one row per escalated assignment, naming who it went to and why, and carrying its own
    resolution. It does not invent an approval workflow — an escalation is a recorded,
    addressable obligation, and `resolved_at` is set only when someone actually decides the
    underlying assignment.
    """

    __tablename__ = "access_review_escalations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("access_reviews.id"),
                                                 nullable=False, index=True)
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("access_review_assignments.id"), index=True)

    escalated_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    escalated_to_email: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    # True when the pending item would leave an administrative grant unreviewed.
    high_risk: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    escalated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── ORG-008 ownership transfer ──────────────────────────────────────────────────────────

TRANSFER_INITIATED = "initiated"
TRANSFER_CURRENT_CONFIRMED = "current_owner_confirmed"
TRANSFER_PROPOSED_CONFIRMED = "proposed_owner_confirmed"
TRANSFER_READY = "ready"
TRANSFER_COMPLETED = "completed"
TRANSFER_EXPIRED = "expired"
TRANSFER_CANCELED = "canceled"
TRANSFER_STATUSES = (TRANSFER_INITIATED, TRANSFER_CURRENT_CONFIRMED,
                     TRANSFER_PROPOSED_CONFIRMED, TRANSFER_READY, TRANSFER_COMPLETED,
                     TRANSFER_EXPIRED, TRANSFER_CANCELED)

TRANSFER_TTL_HOURS = 72


class OwnershipTransfer(Base):
    """A proposed move of Organization ownership, pending dual confirmation.

    Both confirmations are stored as durable timestamps on this row rather than inferred
    from anything transient, and completion reads them. That is what makes "dual
    confirmation" a property of the record instead of a property of one request handler.
    """

    __tablename__ = "ownership_transfers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False,
                                              index=True)

    current_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    current_owner_email: Mapped[str] = mapped_column(String(255), nullable=False)
    proposed_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    proposed_owner_email: Mapped[str] = mapped_column(String(255), nullable=False)

    initiated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 index=True)

    status: Mapped[str] = mapped_column(String(30), default=TRANSFER_INITIATED, nullable=False,
                                        index=True)
    current_owner_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proposed_owner_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_by_email: Mapped[str | None] = mapped_column(String(255))

    # What the outgoing owner keeps. Stored so the completion email can state the resulting
    # permissions as recorded fact rather than as a guess about product policy.
    previous_owner_retained_role: Mapped[str | None] = mapped_column(String(20))

    # Deliberately NOT a step_up_verified boolean. No step-up mechanism exists in this
    # codebase, and a column asserting one had happened would be a lie in schema form. What
    # IS recorded is that each confirmation arrived from an authenticated session.
    current_owner_confirmed_session: Mapped[str | None] = mapped_column(String(64))
    proposed_owner_confirmed_session: Mapped[str | None] = mapped_column(String(64))

    initiated_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization = relationship("Organization")


# ── ORG-010 organization operational restriction ────────────────────────────────────────

ORG_STATE_ACTIVE = "active"
ORG_STATE_RESTRICTED = "restricted"
ORG_STATE_SUSPENDED = "suspended"
ORG_STATE_DELETED = "deleted"
ORG_OPERATIONAL_STATES = (ORG_STATE_ACTIVE, ORG_STATE_RESTRICTED, ORG_STATE_SUSPENDED,
                          ORG_STATE_DELETED)

# Coarse categories only. Nothing here reveals fraud rules, detection logic, risk scores,
# employee notes, investigation detail or any other customer.
ORG_REASON_BILLING = "billing_commercial_requirement"
ORG_REASON_SECURITY = "security_requirement"
ORG_REASON_POLICY = "policy_compliance_requirement"
ORG_REASON_ADMINISTRATIVE = "administrative_restriction"
ORG_REASON_CATEGORIES = (ORG_REASON_BILLING, ORG_REASON_SECURITY, ORG_REASON_POLICY,
                         ORG_REASON_ADMINISTRATIVE)


class OrgOperationalEvent(Base):
    """One committed change to an Organization's operational state."""

    __tablename__ = "org_operational_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No FK: a deletion event must outlive the organization row it describes.
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    org_name: Mapped[str] = mapped_column(String(200), nullable=False)

    previous_state: Mapped[str | None] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason_category: Mapped[str | None] = mapped_column(String(40))

    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
