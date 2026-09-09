"""Privacy and data-governance domain (ZST-EC-001 PRV-001 -> PRV-004).

**What the audit found.** There was no privacy-request domain of any kind. Two adjacent
things existed and are REUSED rather than duplicated:

  * `platform_ops.GovernanceRecord` already has a `privacy_request` kind with `due_at` and
    `status`. That is the internal governance-console obligation tracker; it has no
    requester, no identity verification and no customer-facing reference, so it cannot BE
    the request. `PrivacyRequest` below links to it so the two stay one record.
  * Retention authority already exists in two places - MED-011's `legal_hold` /
    `retention_expires_at` on `LiveRecording`, and `GovernanceRecord(kind="legal_hold")`.
    `RetentionException` records the privacy-side CONSEQUENCE of those; it never decides
    retention itself, so there is no second, conflicting retention system.

Also reused: the `secrets.token_urlsafe(32)` + sha256 convention from `crud/identity.py`,
and the private-object + short-lived-signed-URL pattern from `services/developer_export.py`.

**Why the verification token lives on the request row.** `IdentityChallenge` is the right
shape but its `user_id` is NOT NULL with a foreign key to `users`, and a privacy request is
frequently made by somebody with no account - or by a representative acting for someone
else. The token here is REQUEST-bound and PURPOSE-bound using the identical convention,
which is what the spec asks for; overloading the identity table would have meant either
loosening it for every existing purpose or refusing accountless requesters.

**No statutory deadline is invented.** `DEADLINE_POLICIES` below is deliberately EMPTY: this
product configures no jurisdiction rules, so `deadline_at` stays NULL and the messages say
so. Hardcoding 30 days would be a fabricated legal commitment.

**No privacy notice or Legal page exists in the client at all** (searched: there is no
Legal.jsx and no privacy page), so PRV-004 builds the notice lifecycle from nothing rather
than wrapping static content.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ── PRV-001 ─────────────────────────────────────────────────────────────────────────────
# Rights this product can actually service. `objection` and `restriction` are included
# because a request can be RECEIVED and answered (even if the answer is a documented
# limitation); nothing here promises an outcome the platform cannot deliver.
REQUEST_TYPES = ("access", "export", "deletion", "correction", "restriction", "objection",
                 "other")
REQUEST_TYPE_LABELS = {
    "access": "Access to your personal data",
    "export": "A copy of your personal data",
    "deletion": "Deletion of your personal data",
    "correction": "Correction of your personal data",
    "restriction": "Restriction of processing",
    "objection": "Objection to processing",
    "other": "Other privacy request",
}
# Purpose binding for the verification token. A token minted for this is valid for nothing
# else, and no other token type is accepted here.
PURPOSE_PRIVACY_VERIFICATION = "privacy_request_verification"
PURPOSE_PRIVACY_DOWNLOAD = "privacy_export_download"
VERIFICATION_TTL_HOURS = 72
# A download link is deliberately much shorter-lived than a verification link: it resolves
# to personal data.
DOWNLOAD_TTL_MINUTES = 60

# ── PRV-002 ─────────────────────────────────────────────────────────────────────────────
REQUEST_STATUSES = ("received", "verification_required", "verified", "in_progress",
                    "clarification_required", "extended", "completed", "denied", "limited")
# Statuses in which the requester still owes us something.
REQUEST_AWAITING_REQUESTER = ("verification_required", "clarification_required")

# Coarse, customer-safe extension reasons. Only a recorded one is ever stated - "complex
# request" is never assumed.
EXTENSION_REASONS = ("volume", "complexity", "identity_verification", "third_party_input",
                     "other")
EXTENSION_REASON_LABELS = {
    "volume": "The volume of information involved",
    "complexity": "The complexity of the request",
    "identity_verification": "Completing identity verification",
    "third_party_input": "Input needed from another party",
    "other": "Additional time was authorized",
}
# Customer-safe decision categories for a denial or limitation. Internal legal reasoning is
# never among them.
DECISION_REASONS = ("identity_not_verified", "not_a_data_subject", "legal_retention",
                    "rights_of_others", "manifestly_unfounded", "outside_scope", "other")
DECISION_REASON_LABELS = {
    "identity_not_verified": "We could not verify your identity",
    "not_a_data_subject": "We hold no personal data matching the request",
    "legal_retention": "Some records must be retained to meet a legal obligation",
    "rights_of_others": "Acting fully would affect another person's rights",
    "manifestly_unfounded": "The request falls outside what our policies cover",
    "outside_scope": "The request is outside the scope of this service",
    "other": "A documented policy exception applies",
}

# EMPTY ON PURPOSE. This product configures no jurisdiction or statutory-deadline rules, so
# no deadline is derived and none is invented. Populating this with a real, approved policy
# is what would make PRV-002 deadline handling complete - see services/privacy_comms.
# Shape, for whoever configures it: {"jurisdiction_code": {"days": int, "basis": str}}
DEADLINE_POLICIES: dict[str, dict] = {}

# ── representatives ─────────────────────────────────────────────────────────────────────
REPRESENTATIVE_STATUSES = ("claimed", "verified", "rejected", "revoked")
REPRESENTATIVE_TYPES = ("legal_guardian", "power_of_attorney", "executor",
                        "authorized_agent", "other")
# There is no document-validation process, identity-proofing vendor or legal review workflow
# in this product. A representative can therefore be CLAIMED and recorded, and verified only
# by an explicit human decision - never automatically. PRV-001 is reported PARTIAL for this.
REPRESENTATIVE_AUTOMATED_VALIDATION = False

# ── PRV-003 ─────────────────────────────────────────────────────────────────────────────
EXPORT_STATUSES = ("processing", "ready", "expired", "failed")
DELETION_STATUSES = ("pending", "in_progress", "completed", "partial",
                     "blocked_by_retention", "failed")
# Coarse categories of record that lawfully survive an erasure. Each maps to a real store in
# this codebase, so the deletion message describes actual residue rather than boilerplate.
RESIDUAL_CATEGORIES = ("audit_log", "security_event", "billing_record", "legal_hold",
                       "dispute_record")
RESIDUAL_CATEGORY_LABELS = {
    "audit_log": "Administrative audit records of actions taken on the account",
    "security_event": "Security records needed to protect the service and other users",
    "billing_record": "Invoices and payment records required for accounting",
    "legal_hold": "Records under a legal hold",
    "dispute_record": "Records relating to an open dispute",
}

# ── PRV-004 ─────────────────────────────────────────────────────────────────────────────
NOTICE_STATUSES = ("draft", "published", "superseded")
CONSENT_DECISIONS = ("accepted", "declined", "withdrawn")
SUBPROCESSOR_STATUSES = ("active", "pending", "removed")

# ── notice kinds ────────────────────────────────────────────────────────────────────────
PRIVACY_NOTICE_KINDS = (
    "request_received", "verification_required", "status_update",
    "clarification_required", "deadline_extended", "decision",
    "export_ready", "export_expired", "deletion_completed", "deletion_incomplete",
    "notice_published", "consent_required", "subprocessor_changed",
    "retention_exception",
)


class PrivacyNotice(Base):
    """One row per communicated privacy transition.

    Keyed on (kind, subject, VERSION). A request legitimately transitions many times - a
    status update, a clarification, an extension, a decision - and an export can be issued,
    expire and be regenerated, so a permanent UNIQUE(kind, subject) would swallow the second
    occurrence of each.
    """

    __tablename__ = "privacy_notices"
    __table_args__ = (
        UniqueConstraint("kind", "subject_type", "subject_id", "version",
                         name="uq_privacy_notice_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,
                                                  index=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                              server_default=func.now(), index=True)


class PrivacyRepresentative(Base):
    """Somebody claiming to act for a data subject.

    Recorded so a claim is auditable, but `verified` is reachable only through an explicit
    human decision: there is no document-validation or identity-proofing process in this
    product (REPRESENTATIVE_AUTOMATED_VALIDATION is False). Authorization EVIDENCE is
    deliberately a reference string, never the document itself - nothing here is emailed.
    """

    __tablename__ = "privacy_representatives"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    subject_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    representative_name: Mapped[str] = mapped_column(String(200), nullable=False)
    representative_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    representative_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="claimed", nullable=False)
    # A POINTER to evidence held elsewhere (a support case, a governance record). The
    # document never lives here and is never mailed.
    authorization_reference: Mapped[str | None] = mapped_column(String(200))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class PrivacyRequest(Base):
    """A durable privacy request."""

    __tablename__ = "privacy_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    # Customer-safe, immutable, quotable: PRV-2026-000123. Never a raw UUID.
    request_reference: Mapped[str | None] = mapped_column(String(40), unique=True,
                                                          index=True)
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                                index=True)
    requester_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    request_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="received", nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[str | None] = mapped_column(Text)

    # ── verification ────────────────────────────────────────────────────────────────────
    # Account ownership alone is NOT treated as sufficient for a deletion or export: those
    # carry irreversible or disclosive consequences, so services/privacy_comms requires a
    # fresh purpose-bound verification for them regardless of session state.
    verification_required: Mapped[bool] = mapped_column(Boolean, default=True,
                                                        nullable=False)
    verification_purpose: Mapped[str | None] = mapped_column(String(60))
    verification_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    verification_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    representative_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                                index=True)
    representative_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))

    # ── deadlines ───────────────────────────────────────────────────────────────────────
    # NULL unless a configured policy produced one. DEADLINE_POLICIES is empty in this
    # product, so in practice these stay NULL and the messages say no statutory deadline is
    # configured rather than quoting an invented one.
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_basis: Mapped[str | None] = mapped_column(String(120))
    extension_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extension_reason: Mapped[str | None] = mapped_column(String(40))

    clarification_needed: Mapped[str | None] = mapped_column(Text)
    clarification_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Customer-facing decision text plus a coarse category. Internal legal advice has no
    # column here at all.
    decision_reason: Mapped[str | None] = mapped_column(String(40))
    decision_summary: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    denied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    limited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The governance-console obligation this request is tracked as, so the customer-facing
    # request and the internal GovernanceRecord(kind="privacy_request") stay one record.
    governance_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Bumped per published customer-visible status change, so each is announced once and an
    # internal edit announces nothing.
    status_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class PrivacyExport(Base):
    """A generated personal-data export.

    Same posture as the DEV-012 developer export: the artifact lives in private storage and
    is reached through a single-use, short-lived, purpose-bound token. It is never attached
    to an email and never exposed as a public object URL.
    """

    __tablename__ = "privacy_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    privacy_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("privacy_requests.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="processing", nullable=False)
    # A private object key, never a URL. A signed URL is minted per authorized download.
    storage_reference: Mapped[str | None] = mapped_column(String(500))
    inventory: Mapped[dict | None] = mapped_column(JSON)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_category: Mapped[str | None] = mapped_column(String(60))

    download_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    download_purpose: Mapped[str | None] = mapped_column(String(60))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Incremented per issuance so a regenerated export is announceable again.
    issue_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class PrivacyExportAccess(Base):
    """Append-only access log for one export.

    Records WHO reached it, WHEN and the OUTCOME. Never the exported content - the whole
    point of logging access to personal data is that the log must not itself become another
    copy of it.
    """

    __tablename__ = "privacy_export_accesses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    export_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("privacy_exports.id"),
                                                 nullable=False, index=True)
    privacy_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                                 index=True)
    request_reference: Mapped[str | None] = mapped_column(String(40))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    # A coarse client hint only. No full user agent and no address.
    client_hint: Mapped[str | None] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now(), index=True)


class PrivacyDeletion(Base):
    """The erasure half of a deletion request.

    `residual_categories` is what makes the completion message truthful: it lists the
    lawful record classes that survived, so the platform never claims that everything is
    gone when audit, security, billing or legal-hold records remain.
    """

    __tablename__ = "privacy_deletions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    privacy_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("privacy_requests.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What actually happened, per store.
    removed: Mapped[list | None] = mapped_column(JSON, default=list)
    residual_categories: Mapped[list | None] = mapped_column(JSON, default=list)
    blocker_category: Mapped[str | None] = mapped_column(String(60))
    account_access_note: Mapped[str | None] = mapped_column(String(300))
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class RetentionException(Base):
    """A privacy request whose erasure scope is lawfully limited by retained records.

    Records the CONSEQUENCE of retention authority that lives elsewhere - MED-011's
    `legal_hold` / `retention_expires_at`, or `GovernanceRecord(kind="legal_hold")`. It
    never decides retention itself, which is why there is no second retention system here.
    """

    __tablename__ = "privacy_retention_exceptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    privacy_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("privacy_requests.id"), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # WHERE the authority comes from - "med_011_legal_hold", "med_011_retention_window",
    # "governance_legal_hold", "accounting". Never a freehand legal argument.
    retention_basis: Mapped[str] = mapped_column(String(60), nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authorized_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrivacyNoticeVersion(Base):
    """A versioned privacy notice.

    There is no Legal or privacy page in the client at all, so this is the first governed
    notice lifecycle rather than a wrapper over static content. A DRAFT communicates nothing.
    """

    __tablename__ = "privacy_notice_versions"
    __table_args__ = (UniqueConstraint("version", name="uq_privacy_notice_version_label"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Whether the change is material. Only a material change notifies by default - a wording
    # fix does not mail every user.
    material_change: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Whether this version genuinely requires CONSENT rather than notice. Set explicitly by
    # whoever publishes it; an acknowledgement is never relabelled as consent.
    consent_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_purpose: Mapped[str | None] = mapped_column(String(200))
    change_summary: Mapped[str | None] = mapped_column(Text)
    document_reference: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class PrivacyConsentDecision(Base):
    """One person's decision on one notice version.

    `decision` is stored exactly as made. There is no default, so a record only exists once
    somebody actually chose - which is what stops silence, or an acknowledgement, from being
    recorded as acceptance.
    """

    __tablename__ = "privacy_consent_decisions"
    __table_args__ = (
        UniqueConstraint("notice_version_id", "subject_email",
                         name="uq_privacy_consent_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    notice_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("privacy_notice_versions.id"), nullable=False, index=True)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    subject_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    consent_purpose: Mapped[str | None] = mapped_column(String(200))
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How the decision reached us, so a UI-recorded choice is distinguishable from an
    # operator-entered one.
    source: Mapped[str | None] = mapped_column(String(40))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class Subprocessor(Base):
    """A real third-party processor.

    Seeded ONLY from providers this codebase genuinely integrates - see
    services/privacy_comms.KNOWN_SUBPROCESSORS, each traceable to real configuration and
    real call sites. No vendor name is invented, and no contract terms are stored.
    """

    __tablename__ = "subprocessors"
    __table_args__ = (UniqueConstraint("name", "service", name="uq_subprocessor_service"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    service: Mapped[str] = mapped_column(String(120), nullable=False)
    processing_purpose: Mapped[str] = mapped_column(String(300), nullable=False)
    # Only stated when genuinely known. "Not stated by the provider" is an honest answer.
    region: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notice_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    change_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())
