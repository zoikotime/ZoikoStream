"""Commerce communication state (ZST-EC-001 COM-006, COM-007, COM-008).

Every financial fact these families report already exists. What did not exist is the record
of what has been COMMUNICATED, which is what this file adds - so no billing or entitlement
rule is duplicated here, and nothing in it can change a financial outcome.

  REUSED as the authority, never re-derived:
    * `models.commercial.Invoice`      - number, issue/due date, totals, tax, state
    * `models.commercial.Payment`      - PAYMENT_STATES; the ONLY source for whether a charge
                                         occurred (see services/commerce_comms.charge_position)
    * `models.commercial.RefundCredit` - `type` is already credit | refund | fee_waiver, so a
                                         credit note is a REAL domain, not an invention
    * `models.commercial.PaymentDispute` - DISPUTE_STATES is already a full lifecycle
    * `models.commercial.FinancialPeriod` - `status` open -> closed IS the provisional /
                                         reconciled boundary; no second one is created
    * `services.org.entitlements()`    - the authoritative seat/storage/streaming figures

  GENUINELY NEW:
    * OrgEntitlementState - the durable at-limit marker. A seat-limit 409 currently repeats
      for every invitation attempt; without a stored transition the customer would either be
      told nothing (today) or told on every retry. This records the transition so the notice
      fires once per UNDER_LIMIT -> LIMIT_REACHED crossing.
    * UsageReport - a durable reporting artifact, so "your usage report is ready" can never be
      generated from a live UI counter.
    * CommerceNotice - the transition ledger + marker home for invoices, payments, refunds,
      credit notes, disputes and overdue reminders, none of which had one.

Deliberately NOT modelled:
    * payment-method expiry. There is no stored payment method anywhere in this product - no
      last4, no exp_month/exp_year, no payment-method table; `Payment.method_type` is a bare
      string like "card". COM-007's Method Expiring variant therefore has no authoritative
      trigger and is reported MISSING rather than fabricated.
    * a usage THRESHOLD percentage. No 80/90 or any other configured warning level exists in
      this repository. "Limit reached" is used because `used >= limit` is the same boundary
      the 409 itself enforces; "approaching" would require a policy that does not exist.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# -- COM-006 ----------------------------------------------------------------------------
# One notice kind per financial transition. `credit_note` is separate from `refund` because
# the underlying RefundCredit.type distinguishes them and conflating the two would tell a
# customer money is coming back when only an accounting adjustment was made.
COMMERCE_NOTICE_KINDS = (
    "invoice_available", "payment_received", "refund_issued", "credit_note",
    "payment_failed", "invoice_overdue", "billing_resolved",
    "dispute_opened", "dispute_action_required", "dispute_resolved",
)

# -- COM-007 ----------------------------------------------------------------------------
# What the platform can actually prove about a debit, derived ONLY from Payment.state.
# `no_charge` is the single case in which "no charge was recorded" may be said.
CHARGE_NO_CHARGE = "no_charge"
CHARGE_MAY_BE_AUTHORIZED = "may_be_authorized"
CHARGE_UNKNOWN = "unknown"
CHARGE_POSITIONS = (CHARGE_NO_CHARGE, CHARGE_MAY_BE_AUTHORIZED, CHARGE_UNKNOWN)

# Customer-safe failure categories. A raw processor decline code is never mailed.
PAYMENT_FAILURE_CATEGORIES = ("declined", "authentication_required", "insufficient_funds",
                              "processing_error", "unknown")
PAYMENT_FAILURE_LABELS = {
    "declined": "The payment was declined",
    "authentication_required": "Additional authentication was needed",
    "insufficient_funds": "The payment could not be taken",
    "processing_error": "A processing problem occurred",
    "unknown": "The payment could not be completed",
}

# There is no stored payment method in this product, so this stays False and the Method
# Expiring variant stays unimplemented. See the module docstring.
PAYMENT_METHOD_STORAGE_SUPPORTED = False

# Invoice states that must never receive an overdue reminder.
OVERDUE_EXCLUDED_INVOICE_STATES = ("paid", "void", "draft")

# -- COM-008 ----------------------------------------------------------------------------
# The metric labels services.org.entitlements() actually returns. Nothing else is claimed.
ENTITLEMENT_METRICS = ("Members", "Storage", "Streaming hours")
ENTITLEMENT_LIMIT_STATES = ("under_limit", "limit_reached")

# No configured warning percentage exists anywhere in this repository, so none is invented.
# When a policy is published this becomes a number and the approaching-limit notice starts
# firing; until then `approaching_supported()` is False and the gap is reported.
USAGE_WARNING_THRESHOLD_PERCENT = None

USAGE_REPORT_STATES = ("provisional", "reconciled", "corrected")


class CommerceNotice(Base):
    """One row per communicated commerce transition.

    Doubles as the idempotency marker and the audit ledger: the unique constraint on
    (kind, subject_type, subject_id) is what makes "exactly one notice per transition"
    a database guarantee rather than a convention every future caller has to remember.
    """

    __tablename__ = "commerce_notices"
    __table_args__ = (
        UniqueConstraint("kind", "subject_type", "subject_id",
                         name="uq_commerce_notice_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # The financial row this notice is about - "invoice", "payment", "refund_credit",
    # "dispute". Kept as a pair rather than five nullable FKs so one uniqueness rule covers
    # every kind.
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,
                                                  index=True)
    event_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # For an overdue reminder: which reminder in the sequence this was, so a second reminder
    # at a later threshold is possible without loosening the uniqueness rule above.
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                              server_default=func.now(), index=True)


class OrgEntitlementState(Base):
    """The last entitlement position ANNOUNCED for one organization metric.

    Holds no limits and computes nothing: `services/org.entitlements()` stays the authority.
    This exists so a repeated seat-limit 409 does not mail the customer every attempt, while
    a genuine drop below the limit and later return to it can legitimately notify again.
    """

    __tablename__ = "org_entitlement_states"
    __table_args__ = (UniqueConstraint("org_id", "metric", name="uq_org_entitlement_metric"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False,
                                              index=True)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="under_limit", nullable=False)
    last_used: Mapped[float | None] = mapped_column(Numeric(14, 2))
    last_limit: Mapped[float | None] = mapped_column(Numeric(14, 2))
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    limit_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped on each under_limit -> limit_reached crossing, so the notice can fire once per
    # crossing without the marker being nulled and losing that an earlier one was sent.
    limit_cycle: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    limit_notified_cycle: Mapped[int | None] = mapped_column(Integer)

    # COM-008 entitlement change. Stores the last ANNOUNCED plan snapshot so a change email
    # can show a real "previous" instead of re-deriving one that no longer exists.
    announced_plan: Mapped[str | None] = mapped_column(String(60))
    announced_limits: Mapped[dict | None] = mapped_column(JSON, default=dict)
    changed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageReport(Base):
    """A durable usage artifact for one organization and one period.

    `state` carries the provisional/reconciled distinction, and it is NOT decided here: it is
    read from `FinancialPeriod.status` (open -> provisional, closed -> reconciled), which is
    the platform's existing reconciliation authority. A live counter can therefore never be
    presented as a reconciled figure.
    """

    __tablename__ = "usage_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False,
                                              index=True)
    period_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    period_label: Mapped[str | None] = mapped_column(String(20))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(20), default="provisional", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # The figures as communicated. Frozen so a correction can show a real before/after rather
    # than silently overwriting a number the customer already acted on.
    figures: Mapped[dict | None] = mapped_column(JSON, default=dict)
    superseded_figures: Mapped[dict | None] = mapped_column(JSON)
    correction_reason: Mapped[str | None] = mapped_column(String(200))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())

    ready_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    corrected_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
