"""Support case domain (ZST-EC-001 SUP-001 -> SUP-004).

**What the audit found.** `models/support_ticket.py` carries subject, message, status,
priority, requester_email and three timestamps - and its own docstring admits "no
self-service submission flow exists yet ... tickets are logged and worked from the Super
Admin console". Every route lives on `/admin`, which is gated at ROUTER level by
`require_super_admin`, so a customer could not open, read or update a case at all.

This file adds the authoritative state those four families need, without touching the
existing table's meaning:

  * `SupportTicket` gains the columns below (all nullable / defaulted), so every row created
    by the existing Super Admin console keeps working unchanged.
  * `SupportCaseParticipant` makes recipients CASE-scoped. Without it the only options were
    "the requester" or "everyone in the organization", and SUP-002 explicitly forbids the
    second.
  * `SupportEscalation` is the escalation lifecycle, which had no representation at all.
  * `SupportNotice` is the idempotency ledger, keyed on (kind, case, CYCLE) rather than
    (kind, case). A coarse key would permanently block the legitimate
    RESOLVED -> REOPENED -> RESOLVED sequence from producing a second resolution notice.

**Internal versus customer-visible text.** The existing `message` column is the customer's
own description. There was nowhere to put staff notes, so this adds `internal_notes` - which
exists precisely so that notes have a home the sender can never reach - and
`customer_update`, which is the only free text SUP-002 ever mails.

**Incident linkage reuses `models.platform_ops.Incident`** (which already has a customer-safe
`ref` like INC-2026-0729-1420, a `kind`, and a `status`). No second incident domain is
created. `Incident.detail` and `Incident.commander` are never mailed.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# -- SUP-001 ----------------------------------------------------------------------------
# Customer-safe categories only. Nothing here is an internal routing or fraud label.
CASE_CATEGORIES = ("technical", "billing", "account_access", "live_event",
                   "developer_api", "media_recording", "other")
CASE_CATEGORY_LABELS = {
    "technical": "Technical issue",
    "billing": "Billing",
    "account_access": "Account and access",
    "live_event": "Live event",
    "developer_api": "Developer and API",
    "media_recording": "Media and recording",
    "other": "Other",
}

# Extends the existing TICKET_STATUSES ("open", "in_progress", "resolved", "closed") with the
# two states SUP-002 and SUP-004 need. The original four keep their exact meaning, so rows
# and console filters that predate this continue to work.
CASE_STATUSES = ("open", "in_progress", "waiting_for_customer", "resolved", "closed",
                 "reopened")
# Statuses in which the case is still being worked.
CASE_OPEN_STATUSES = ("open", "in_progress", "waiting_for_customer", "reopened")

# -- SUP-004 ----------------------------------------------------------------------------
# The authoritative sensitivity classification. Feedback eligibility is decided from THIS,
# never from scanning the subject or description for keywords - a customer writing "my
# account was hacked" in a billing case must not silently reclassify it, and a genuine
# bereavement case whose wording happens to be neutral must still be protected.
CASE_SENSITIVITIES = ("standard", "security", "privacy", "safety", "bereavement")
# Sensitivities for which a satisfaction survey is never appropriate.
FEEDBACK_EXCLUDED_SENSITIVITIES = ("security", "privacy", "safety", "bereavement")

# -- SUP-003 ----------------------------------------------------------------------------
ESCALATION_LEVELS = (1, 2, 3)
# Coarse, customer-safe escalation reasons. Nothing names a subsystem, a staff member or a
# security finding.
ESCALATION_REASONS = ("impact", "duration", "complexity", "customer_request",
                      "incident_linked", "other")
ESCALATION_REASON_LABELS = {
    "impact": "The impact on your service",
    "duration": "How long the case has been open",
    "complexity": "The case needs specialist input",
    "customer_request": "You asked for it to be escalated",
    "incident_linked": "It is linked to a wider incident",
    "other": "Additional review was needed",
}

# -- participants -----------------------------------------------------------------------
# `requester` is the person who opened it; `participant` is somebody explicitly added to the
# case. There is no "everyone in the org" role on purpose.
PARTICIPANT_ROLES = ("requester", "participant")

# -- notice kinds -----------------------------------------------------------------------
SUPPORT_NOTICE_KINDS = (
    "case_opened", "case_update", "action_required", "action_reminder",
    "escalated", "owner_changed", "incident_linked",
    "resolved", "closed", "reopened", "feedback_request",
)


class SupportCaseParticipant(Base):
    """Somebody explicitly authorized on one support case.

    Recipient resolution reads ONLY this table, which is what keeps a case notice from
    reaching every organization member or every admin.
    """

    __tablename__ = "support_case_participants"
    __table_args__ = (
        UniqueConstraint("ticket_id", "email", name="uq_support_participant_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"),
                                                 nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Email-keyed, so a participant does not have to hold a platform account - the same
    # reasoning as the CON-001 contributor grant.
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(20), default="participant", nullable=False)
    added_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class SupportEscalation(Base):
    """One escalation of one case."""

    __tablename__ = "support_escalations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"),
                                                 nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reason_category: Mapped[str] = mapped_column(String(40), nullable=False)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # The customer-facing owning TEAM, not a staff member's name and not an internal queue
    # id. An internal reassignment inside the same team is therefore not an owner change.
    owner_before: Mapped[str | None] = mapped_column(String(80))
    owner_after: Mapped[str | None] = mapped_column(String(80))
    # Only ever set when somebody explicitly commits to a time. Nothing derives it.
    next_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    escalated_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportNotice(Base):
    """One row per communicated support transition.

    The uniqueness key is (kind, ticket, CYCLE). `cycle` comes from
    `SupportTicket.lifecycle_cycle`, which increments on every reopen - so a case that is
    resolved, reopened and resolved again legitimately produces two resolution notices,
    while a repeated resolve inside one cycle produces one. `sequence` does the same job for
    reminders, which may fire more than once per cycle at different thresholds.
    """

    __tablename__ = "support_notices"
    __table_args__ = (
        UniqueConstraint("kind", "ticket_id", "cycle", "sequence",
                         name="uq_support_notice_cycle"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"),
                                                 nullable=False, index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    cycle: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                              server_default=func.now(), index=True)
