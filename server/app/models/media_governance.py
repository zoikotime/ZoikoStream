"""Media asset governance — retention, legal hold and deletion (ZST-EC-001 MED-011).

Retention and legal hold were previously *half* present: `LiveRecording` already carried
`retention_policy_version`, `retention_expires_at` and `legal_hold` columns, and
`routers/organization.py::delete_recording` already refused to delete a held recording. What
did not exist was anything that SET a retention date, any way to extend one, and any record
of what a deletion actually did. This module supplies those three.

The design rule throughout: a retention deadline that only exists in an email is not a
retention policy. Every date this module reports is committed to a row first, and the policy
those dates are derived from is a stored `PlatformSetting` (see services/media_retention.py)
rather than a constant buried in a template — so an operator can answer "under what policy?"
with a version string, not a guess.

`RetentionExtension` is a maker-checker workflow on purpose. An ordinary org admin can ASK
for a longer retention; only a super_admin can grant one, matching who can place or release a
legal hold today. Letting the asset owner extend their own retention unilaterally would make
the retention policy advisory, which is the opposite of what it is for.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ── governed recording-health states (MED-007) ──────────────────────────────────────────
# Derived ONLY from committed LiveRecording rows. There is no separate health telemetry
# stream for recordings, so these are the states the platform can actually prove.
REC_HEALTH_RECORDING = "recording"
REC_HEALTH_DEGRADED = "degraded"
REC_HEALTH_RECOVERED = "recovered"
REC_HEALTH_STOPPED = "stopped"
REC_HEALTH_FAILED = "failed"
RECORDING_HEALTH_STATES = (
    REC_HEALTH_RECORDING, REC_HEALTH_DEGRADED, REC_HEALTH_RECOVERED,
    REC_HEALTH_STOPPED, REC_HEALTH_FAILED,
)

# ── finalization states (MED-008) ───────────────────────────────────────────────────────
# These are NOT a new column. They are the reading of the EXISTING
# `LiveRecording.validation_status` that services/validation.py already writes
# (captured|validating|valid|degraded|failed), mapped to the vocabulary MED-008 speaks.
# Introducing a parallel status column would create two answers to one question.
FINALIZATION_READY = "ready"
FINALIZATION_PARTIAL = "partial"
FINALIZATION_FAILED = "failed"
FINALIZATION_RECOVERED = "recovered"
FINALIZATION_STATES = (
    FINALIZATION_READY, FINALIZATION_PARTIAL, FINALIZATION_FAILED, FINALIZATION_RECOVERED,
)

VALIDATION_TO_FINALIZATION = {
    "valid": FINALIZATION_READY,
    "degraded": FINALIZATION_PARTIAL,
    "failed": FINALIZATION_FAILED,
}

# ── deletion states (MED-011) ───────────────────────────────────────────────────────────
# `deleting` exists because storage deletion and row deletion are two separate operations
# that can disagree. Before this, delete_recording called livekit.delete_object() and threw
# the result away, then deleted the row unconditionally — so a failed storage delete left an
# orphaned object with nothing recording that it was supposed to be gone.
DELETION_PENDING = "pending"
DELETION_DELETING = "deleting"
DELETION_COMPLETED = "completed"
DELETION_FAILED = "failed"
DELETION_STATES = (DELETION_PENDING, DELETION_DELETING, DELETION_COMPLETED, DELETION_FAILED)

EXTENSION_STATES = ("pending", "approved", "declined")

# Free-text reasons invite confidential detail into a field that is emailed. A closed list
# keeps the notification safe to send to a data-governance mailbox.
RETENTION_REASON_CATEGORIES = (
    "legal_or_regulatory", "dispute_or_claim", "customer_request",
    "internal_review", "operational_need",
)

# Same reasoning for hold categories: the notification says WHY a hold exists in
# non-confidential terms and never carries counsel's actual instructions.
LEGAL_HOLD_CATEGORIES = (
    "litigation", "regulatory_inquiry", "internal_investigation", "preservation_request",
)


class RetentionExtension(Base):
    """One request to keep a recording past its committed retention date.

    Nothing here changes `LiveRecording.retention_expires_at` — only an approval does, in
    services/media_retention.py::approve_extension, which writes the new date and the
    evidence of who granted it in the same transaction.
    """

    __tablename__ = "retention_extensions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    recording_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,
                                                    index=True)

    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    reason_category: Mapped[str] = mapped_column(String(40), nullable=False)
    # What the requester asked for, and what was actually granted — kept separately so an
    # approval that shortens the request is still an honest record of both.
    requested_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Nulled rather than cascaded when a user is deleted: the governance record must outlive
    # the person, the same rule crud/admin.py already applies to support access and
    # ownership transfers.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)

    requested_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)


class MediaAssetEvent(Base):
    """Append-only ledger of governed media-asset lifecycle transitions.

    Exists for the same reason `org_events` does for the ORG families: a notification claim
    marker on the asset row answers "was this announced?", but not "what happened, in what
    order, to an asset that has since been deleted". A deletion removes the LiveRecording
    row; this ledger is what survives it, which is exactly the record a retention audit
    needs.

    Deliberately NOT the audit log: audit_logs is actor-centric ("who did what"), this is
    asset-centric ("what happened to this asset"), and a retention review reads the second.
    """

    __tablename__ = "media_asset_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # Not a ForeignKey: the row it points at is routinely deleted, and that deletion is
    # precisely the transition this ledger must keep.
    recording_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    replay_entitlement_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                                    index=True)

    family: Mapped[str] = mapped_column(String(10), nullable=False)   # MED-007 | MED-008 | ...
    transition: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)


class LegalHoldContact(Base):
    """An address that must be told when a legal hold is placed or released.

    Legal-hold notification has a recipient the product otherwise has no way to name: the
    person who asked for the hold is frequently outside the tenant (counsel, a compliance
    officer) and is not a Zoiko Steam user at all. Without this table the only truthful
    options were to email org admins and call them "legal contacts", or to skip the
    notification. This is a real, stored, super_admin-managed list instead.
    """

    __tablename__ = "legal_hold_contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))
    # "legal" contacts hear about holds; "governance" contacts also hear about retention
    # and deletion. Kept as one table because the recipient lists overlap heavily.
    kind: Mapped[str] = mapped_column(String(20), default="legal", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


CONTACT_KINDS = ("legal", "governance")
