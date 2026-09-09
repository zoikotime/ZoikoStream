"""Trust Center: security advisories, evidence access, vulnerability disclosure.

ZST-EC-001 TRU-001 -> TRU-003. Three registers that did not exist before:

    SecurityAdvisory + SecurityAdvisoryVersion + AdvisoryImpact
    TrustEvidenceDocument + TrustEvidenceRequest + TrustEvidenceAccess
    VulnerabilityReport + VulnerabilityReportUpdate

── WHY THIS IS NOT THE STATUS PAGE, AND NOT MARKETING ────────────────────────────────────
Three communication domains are kept apart on purpose, and this module is the first:

    SECURITY / TRUST   this file. Class A. A marketing unsubscribe cannot touch it.
    OPERATIONAL STATUS models/status_page.py. Opt-in, suppressible, public.
    MARKETING          models/marketing.py. Opt-in with a lawful basis, always suppressible.

An advisory subscriber is not a status subscriber and neither is a marketing subscriber.
Nothing in this file writes to `marketing_subscriptions`, and the regression suite asserts
that a vulnerability reporter never appears in it.

── VERSIONING ────────────────────────────────────────────────────────────────────────────
A published advisory is a public commitment. `SecurityAdvisoryVersion` is append-only for
the same reason `PublicStatusIncidentUpdate` is: a customer who acted on version 1 must be
able to see what version 1 said. Nothing here ever UPDATEs a published version row.
"""

import uuid
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, JSON, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ── TRU-001 vocabulary ────────────────────────────────────────────────────────────────────

# Approved severity categories. There is no authoritative CVSS source in this platform - no
# scanner feed, no NVD sync, no scoring workflow - so a score is NEVER computed here.
# `cvss_vector` below exists for the case where a real one is recorded by a human; when it is
# null (the normal case today) no score is quoted anywhere.
ADVISORY_SEVERITIES = ("low", "medium", "high", "critical")

SEVERITY_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "critical": "Critical",
}

ADVISORY_STATUSES = ("draft", "published", "updated", "remediation_available",
                     "remediated", "closed")

# Which transitions may be announced. "draft" is absent by construction: an unpublished
# advisory has no audience.
ADVISORY_NOTIFIABLE = ("published", "updated", "remediation_available", "closed")

ADVISORY_VERSION_TYPES = ("publication", "update", "remediation", "closure")

# The customer-facing components an advisory can name. Reuses the public status taxonomy so a
# customer reading an advisory and a status notice sees the same words for the same thing.
# (models/status_page.py owns the list; importing the constant would create a cycle at import
# time for no benefit, so the advisory validates against it lazily in the service.)

# Why an organization is considered affected. Every basis is a RECORDED fact about that
# tenant, never an inference from the tenant merely existing.
IMPACT_BASES = (
    "configuration_observed",   # the affected setting/feature is recorded as in use
    "version_observed",         # the affected version is recorded against the tenant
    "component_in_use",         # the tenant has records proving use of the component
    "operator_confirmed",       # a named operator confirmed impact and is recorded as such
)

# ── TRU-002 vocabulary ────────────────────────────────────────────────────────────────────

EVIDENCE_DOCUMENT_TYPES = (
    "soc2_type2", "iso27001_certificate", "penetration_test_summary",
    "security_whitepaper", "subprocessor_list", "dpa_template",
    "architecture_overview", "questionnaire_response",
)

# How restricted a document is. `public` documents are the only ones that may be linked
# without an approved request; everything else needs one.
EVIDENCE_CLASSIFICATIONS = ("public", "customer_confidential", "nda_required")

EVIDENCE_DOCUMENT_STATUSES = ("draft", "available", "superseded", "withdrawn")

# Purposes a requester may declare. Access is bound to ONE of these, and a token issued for
# one purpose is not valid for another.
EVIDENCE_PURPOSES = (
    "vendor_security_review",
    "procurement_due_diligence",
    "customer_audit",
    "regulatory_compliance",
    "contract_negotiation",
)

# Scope of the review the evidence is for. Bound the same way as purpose.
EVIDENCE_SCOPES = ("organization", "single_project", "annual_review")

EVIDENCE_REQUEST_STATES = ("requested", "under_review", "approved", "denied", "expired",
                           "revoked")

# What makes a requester qualified. Only the first two are IMPLEMENTED, because only they
# can be checked against something this platform actually records:
#
#   customer_organization  the requester is an active member of an organization here
#   operator_vouched       a named platform operator recorded the qualification
#
# `authorized_auditor` and `approved_prospect` are DECLARED but not automatically checkable:
# there is no auditor register and no commercial-process record to read. A request claiming
# either is accepted and left for human review - never auto-qualified. See the family report.
QUALIFICATION_BASES = ("customer_organization", "operator_vouched", "authorized_auditor",
                       "approved_prospect")

AUTO_QUALIFIABLE_BASES = ("customer_organization",)

# Evidence access is short-lived by default. Long enough for a security reviewer to read a
# report properly, short enough that a forwarded link is worthless within days.
EVIDENCE_ACCESS_TTL_HOURS = 72

# ── TRU-003 vocabulary ───────────────────────────────────────────────────────────────────

VULN_CATEGORIES = (
    "authentication", "authorization", "injection", "data_exposure",
    "denial_of_service", "cryptography", "configuration", "media_pipeline",
    "supply_chain", "other",
)

VULN_STATES = ("received", "acknowledged", "triaged", "validating", "coordinating",
               "remediated", "closed")

# The lifecycle points a researcher may be told about. Deliberately a SUBSET of the internal
# states: "validating" and internal triage detail are not researcher-facing events, and
# nothing here carries detection logic.
VULN_REPORTER_NOTIFIABLE = ("acknowledged", "clarification_needed", "coordinating",
                            "remediated", "closed")

# How a report was resolved. "not_applicable"/"duplicate" are outcomes, not judgements about
# the researcher, and none of them is communicated as a promise before it is recorded.
VULN_RESOLUTIONS = ("remediated", "mitigated", "not_reproducible", "not_applicable",
                    "duplicate", "accepted_risk", "withdrawn")

# Whether the reporter wants to be named IF a public credit programme ever exists. Recording
# the preference is not the same as promising credit - there is no bounty or credit policy in
# this platform, and no message ever implies one.
IDENTITY_VISIBILITY = ("private", "credit_if_published", "anonymous")


class SecurityAdvisory(Base):
    """One security advisory. The register that TRU-001 needs and did not have.

    `severity` is a recorded category from ADVISORY_SEVERITIES. It is set by whoever files the
    advisory and is never derived - not from the report, not from the affected component, and
    certainly not in email code.
    """

    __tablename__ = "security_advisories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    public_reference: Mapped[str] = mapped_column(String(40), unique=True, index=True,
                                                  nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False,
                                        index=True)

    # Customer-safe summary and impact. Separate from any internal analysis, which is not
    # stored on this row at all - there is nowhere here for exploit detail to live.
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    customer_impact: Mapped[str | None] = mapped_column(Text)
    immediate_mitigation: Mapped[str | None] = mapped_column(Text)

    affected_components: Mapped[list | None] = mapped_column(JSON, default=list)
    # Free-form because versions are product-specific ("API v1 before 2026-09-01"). Rendered
    # only when a human filled it in; never guessed from a release table.
    affected_versions: Mapped[str | None] = mapped_column(String(300))
    affected_scope_note: Mapped[str | None] = mapped_column(Text)

    # Only ever populated by a person from an authoritative scoring exercise. Null today.
    cvss_vector: Mapped[str | None] = mapped_column(String(120))

    remediation_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fixed_version: Mapped[str | None] = mapped_column(String(120))
    remediation_steps: Mapped[str | None] = mapped_column(Text)
    # A deadline is only ever a real policy deadline. Null means "no deadline exists", and
    # the email says nothing about timing rather than inventing urgency.
    remediation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set only when a policy marks the customer action mandatory. Gates the one piece of
    # imperative wording ("upgrade immediately") that would otherwise be editorialising.
    action_mandatory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    workaround_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    workaround_summary: Mapped[str | None] = mapped_column(Text)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remediated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_note: Mapped[str | None] = mapped_column(Text)

    # Publication requires a named approver distinct from the drafter where policy demands
    # it; the service refuses to publish without one.
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Correlates to an internal record WITHOUT exposing it: this id never crosses into a
    # customer-facing projection or an email.
    internal_incident_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vulnerability_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class SecurityAdvisoryVersion(Base):
    """Append-only published history for one advisory.

    Every publication, material update, remediation notice and closure appends a row. Nothing
    updates one. A customer can therefore always see what an advisory said at the moment they
    acted on it, which is the whole point of versioning a public security statement.
    """

    __tablename__ = "security_advisory_versions"
    __table_args__ = (
        UniqueConstraint("advisory_id", "version", name="uq_advisory_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    advisory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("security_advisories.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    version_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # A snapshot of the customer-facing statement AS PUBLISHED, so later edits to the parent
    # row cannot rewrite history.
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    customer_impact: Mapped[str | None] = mapped_column(Text)
    affected_components: Mapped[list | None] = mapped_column(JSON, default=list)
    affected_versions: Mapped[str | None] = mapped_column(String(300))

    # What actually changed, for an update. Only meaningful differences are recorded, so the
    # "Update" message can show the change rather than restating the whole advisory.
    change_summary: Mapped[str | None] = mapped_column(Text)
    changed_fields: Mapped[list | None] = mapped_column(JSON, default=list)

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class AdvisoryImpact(Base):
    """Authoritative "this tenant is affected" mapping.

    The ONLY thing that makes an organization an affected customer. Each row records WHY,
    from IMPACT_BASES, plus the evidence a person or a query relied on. An organization that
    merely exists is never notified: without a row here, an advisory reaches subscribed
    verified security contacts and nobody else.
    """

    __tablename__ = "advisory_impacts"
    __table_args__ = (
        UniqueConstraint("advisory_id", "org_id", name="uq_advisory_impact_org"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    advisory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("security_advisories.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"),
                                              nullable=False, index=True)
    basis: Mapped[str] = mapped_column(String(30), nullable=False)
    # A short, non-sensitive note naming the evidence ("recording retention enabled"), never
    # the evidence itself and never anything exploitable.
    evidence_note: Mapped[str | None] = mapped_column(String(400))
    affected_versions: Mapped[str | None] = mapped_column(String(300))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrustEvidenceDocument(Base):
    """A trust/compliance document. Contents live in private storage, never in a column.

    `storage_reference` is an object key resolved through the same private-storage path
    developer exports use (services/developer_export._store): a bucket when one is
    configured, a private local directory otherwise. Neither is publicly reachable, and every
    read is authorized per request.
    """

    __tablename__ = "trust_evidence_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    document_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    classification: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    # Deliberately NOT a URL. A permanent public link is exactly what TRU-002 forbids.
    storage_reference: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(120), default="application/pdf",
                                              nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer)

    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # An approved request must match one of each, or access is refused. Empty means
    # "no purpose/scope is allowed", not "all are" - the default fails closed.
    allowed_purposes: Mapped[list | None] = mapped_column(JSON, default=list)
    allowed_scopes: Mapped[list | None] = mapped_column(JSON, default=list)

    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class TrustEvidenceRequest(Base):
    """A request for one document, for one purpose, at one scope, by one requester.

    Every dimension is bound: the approval is for THIS requester, THIS document, THIS purpose
    and THIS scope, and it expires. Changing any of them requires a new request.
    """

    __tablename__ = "trust_evidence_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True,
                                           nullable=False)
    # The bound recipient. Stored as the address the approval was issued for, so an approval
    # cannot be handed to a colleague by forwarding it.
    requester_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    requester_name: Mapped[str | None] = mapped_column(String(200))
    requester_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    company_name: Mapped[str | None] = mapped_column(String(200))

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trust_evidence_documents.id"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose_note: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False,
                                        index=True)
    # Which QUALIFICATION_BASES the requester claimed, and whether it was CHECKED against
    # something real or merely asserted and left for a human.
    qualification_basis: Mapped[str | None] = mapped_column(String(40))
    qualification_verified: Mapped[bool] = mapped_column(Boolean, default=False,
                                                          nullable=False)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    denied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    denied_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # A customer-safe reason. Never the internal assessment.
    decision_note: Mapped[str | None] = mapped_column(Text)

    # sha256 at rest, like every other token in this codebase. Purpose-bound to
    # PURPOSE_TRUST_EVIDENCE so it cannot be replayed against another flow, single-use in the
    # sense that it is cleared on expiry/revocation.
    access_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class TrustEvidenceAccess(Base):
    """One authorization decision on one document read. Append-only.

    Written for BOTH outcomes: a refused attempt is the interesting half of an access log.
    Deliberately records no document content and no token - only who, what, when and the
    outcome.
    """

    __tablename__ = "trust_evidence_accesses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trust_evidence_requests.id"), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trust_evidence_documents.id"), nullable=False, index=True)
    requester_email: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    purpose: Mapped[str | None] = mapped_column(String(60))
    scope: Mapped[str | None] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    client: Mapped[str | None] = mapped_column(String(160))
    ip: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                         index=True)


class VulnerabilityReport(Base):
    """A security report from a researcher, through a channel that is not a support ticket.

    ── WHY NOT SupportTicket ────────────────────────────────────────────────────────────────
    Support tickets are readable by org admins and support staff by design (SUP-002), and a
    vulnerability report must not be. This register is separate so reporter identity and
    reproduction detail sit behind a security role from the first write, not after a
    reclassification that might never happen.
    """

    __tablename__ = "vulnerability_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True,
                                           nullable=False)

    # Identity. Restricted: services/vuln_disclosure.reporter_identity() is the only reader,
    # and it requires a security role. No advisory, no customer-facing projection and no
    # email template takes these as a parameter.
    reporter_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reporter_name: Mapped[str | None] = mapped_column(String(200))
    reporter_identity_visibility: Mapped[str] = mapped_column(String(30), default="private",
                                                              nullable=False)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    affected_service: Mapped[str | None] = mapped_column(String(200))
    # Reproduction detail. Held here rather than in mail, and never rendered to a customer.
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reproduction: Mapped[str | None] = mapped_column(Text)
    # Object key in private storage for a researcher's attachment. Same private-storage path
    # as evidence documents; authorization-protected, never emailed.
    evidence_reference: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[str] = mapped_column(String(30), default="received", nullable=False,
                                        index=True)
    # The one field a researcher-facing message may quote. Written deliberately as
    # researcher-safe prose; internal analysis has no field on this row.
    safe_reporter_update: Mapped[str | None] = mapped_column(Text)

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validating_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coordinated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remediated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(30))

    # ── coordinated disclosure ───────────────────────────────────────────────────────────
    # Populated ONLY when a real coordination agreement exists. There is no coordinated
    # disclosure policy configured in this platform, so these stay null and no message
    # mentions embargoes, disclosure dates or credit. See the family report.
    disclosure_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embargo_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remediation_target: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    public_credit_preference: Mapped[str | None] = mapped_column(String(30))

    advisory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    triaged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # The reporter portal handle. Hashed at rest and purpose-bound: it lets a researcher with
    # no account read their own report's safe status, and grants nothing else anywhere.
    portal_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    portal_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class VulnerabilityReportUpdate(Base):
    """Append-only researcher-visible history for one report.

    Only researcher-safe text lands here, because this is what the reporter portal renders.
    Internal triage notes have no row and no column in this table.
    """

    __tablename__ = "vulnerability_report_updates"
    __table_args__ = (
        UniqueConstraint("report_id", "version", name="uq_vuln_report_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vulnerability_reports.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class TrustNotice(Base):
    """Durable idempotency ledger for TRU-001 -> TRU-003.

    Version-keyed, not subject-keyed: an advisory that is published, updated twice and then
    closed must send four notices, and a coarse UNIQUE(kind, subject_id) would swallow three
    of them. `recipient` is part of the key because an advisory fans out to many people and
    each one is a separate obligation.
    """

    __tablename__ = "trust_notices"
    __table_args__ = (
        UniqueConstraint("kind", "subject_type", "subject_id", "version", "recipient",
                         name="uq_trust_notice"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,
                                                  index=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Lower-cased address, or "" for a notice that is not per-recipient.
    recipient: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                              server_default=func.now())
