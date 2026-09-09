"""Security, abuse and content-restriction domain (ZST-EC-001 SEC-001 -> SEC-006).

**What the audit found already built, and is REUSED rather than duplicated:**

  * `platform_ops.ElevationSession` - break-glass grants, with expiry genuinely enforced:
    `services/ops.current_elevation` selects on `expires_at > now()` and `ended_at IS NULL`.
  * `services/support_access.countersign_emergency` - the independent second authorization,
    which already refuses `authorizer.id == req.engineer_id`. SEC-002 therefore builds NO
    parallel emergency-access subsystem; it adds the customer-facing disclosure and the
    post-access review deadline that were missing.
  * `support_access.SupportAccessRequest` - carries `emergency`, `emergency_authorizer_id`,
    `post_use_review_at` and `elevation_session_id`. SEC-002 adds only `review_due_at`,
    `reviewed_by_id` and `review_outcome`.
  * `platform_ops.Incident` - the internal record, with a customer-safe `ref` and a `status`.
    SEC-003 adds a DISCLOSURE layer beside it rather than exposing `detail` or `commander`.
  * MED-011's `legal_hold` / `retention_expires_at` on `LiveRecording` - SEC-005 reads them
    and never claims a deletion they forbid.
  * `GovernanceRecord` (kind `break_glass`) and `AuditLog` - the existing evidence trail.

**Why the verification token lives here rather than on IdentityChallenge.**
`IdentityChallenge` is the right shape (purpose, token_hash, expires_at, consumed_at,
superseded_at) but its `user_id` is NOT NULL with a foreign key to `users`. A security
contact is frequently a dedicated address - `security@customer.example` - with no platform
account, and making that column nullable would loosen the identity core for every existing
purpose. The token below therefore uses the IDENTICAL convention
(`secrets.token_urlsafe(32)` + sha256 at rest, purpose-bound, single-use, expiring,
superseded on resend) on this row instead. That is a deliberate reuse of the pattern, not a
bespoke weaker token.

**Detection internals are structurally absent.** No model here has a column for a rule id, a
threshold, a signature, a score, an IP, a payload or a log line. `safe_summary` fields are
operator-authored customer-facing text; anything investigative belongs in `AuditLog` or the
incident's own internal `detail`, neither of which any SEC sender can reach.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ── SEC-006 security contacts ───────────────────────────────────────────────────────────
CONTACT_STATUSES = ("pending", "verified", "revoked", "expired")
# Purpose binding for the verification token. A token minted for this can be used for
# nothing else, and nothing else can be redeemed here.
PURPOSE_SECURITY_CONTACT = "security_contact_verification"
CONTACT_TOKEN_TTL_HOURS = 72

# ── SEC-006 access-policy violations ────────────────────────────────────────────────────
# A denied request is NOT a violation. These are the categories a violation may be
# CONFIRMED under, each requiring an authoritative control or an approved deterministic
# rule to assert it - never a bare 403.
VIOLATION_CATEGORIES = ("credential_sharing", "prohibited_location", "device_policy",
                        "privileged_misuse", "policy_bypass_attempt", "other")
VIOLATION_CATEGORY_LABELS = {
    "credential_sharing": "Account credentials appear to be shared",
    "prohibited_location": "Access from a location your policy does not permit",
    "device_policy": "Access from a device your policy does not permit",
    "privileged_misuse": "Privileged access used outside its approved purpose",
    "policy_bypass_attempt": "An attempt to work around a security control",
    "other": "A security policy condition was met",
}
VIOLATION_STATUSES = ("confirmed", "remediated", "dismissed")
# Only a control that genuinely establishes the fact may confirm one. `authorization_denial`
# is deliberately NOT a member: that is the ordinary-403 case this list exists to exclude.
VIOLATION_SOURCES = ("security_control", "approved_rule", "manual_review")

# ── SEC-001 security events ─────────────────────────────────────────────────────────────
# The confirmation ladder. SEC-001 urgent alerts fire only from `confirmed` or later - an
# unverified detector score sits at `detected` and communicates nothing.
EVENT_STATUSES = ("detected", "under_review", "confirmed", "contained", "resolved",
                  "dismissed")
EVENT_ALERTABLE = ("confirmed", "contained", "resolved")
SEC_EVENT_CATEGORIES = ("suspicious_access", "credential_compromise", "privileged_misuse",
                        "account_takeover", "policy_violation", "other")
SEC_EVENT_CATEGORY_LABELS = {
    "suspicious_access": "Suspicious access confirmed",
    "credential_compromise": "Credential compromise",
    "privileged_misuse": "Privileged-access misuse",
    "account_takeover": "Account takeover",
    "policy_violation": "Security policy violation",
    "other": "Confirmed high-risk security event",
}

# ── SEC-003 incident disclosure ─────────────────────────────────────────────────────────
# Mirrors platform_ops.INCIDENT_STATUSES ("open", "monitoring", "resolved") plus the
# containment step the customer disclosure needs.
DISCLOSURE_STATUSES = ("open", "investigating", "contained", "resolved")
DISCLOSURE_STATUS_LABELS = {
    "open": "Open",
    "investigating": "Under investigation",
    "contained": "Contained",
    "resolved": "Resolved",
}

# ── SEC-004 abuse reports ───────────────────────────────────────────────────────────────
ABUSE_STATUSES = ("received", "triaged", "under_review", "closed")
ABUSE_CATEGORIES = ("harassment", "harmful_content", "impersonation", "spam",
                    "intellectual_property", "privacy", "other")
ABUSE_SUBJECT_TYPES = ("event", "recording", "user", "organization", "message", "other")
# What a reporter may be told at closure. Deliberately outcome-free: the platform does not
# publish enforcement decisions, so no member of this set names one.
ABUSE_CLOSURE_NOTES = ("reviewed_no_action_disclosed", "reviewed_action_taken_undisclosed",
                       "insufficient_information", "outside_policy_scope")
ABUSE_CLOSURE_LABELS = {
    "reviewed_no_action_disclosed": "We reviewed the report under our policies.",
    "reviewed_action_taken_undisclosed": "We reviewed the report and have taken the steps we "
                                          "consider appropriate under our policies.",
    "insufficient_information": "We could not review the report with the information "
                                 "available.",
    "outside_policy_scope": "The report falls outside what our policies cover.",
}

# ── SEC-005 content restriction ─────────────────────────────────────────────────────────
RESTRICTION_STATUSES = ("proposed", "active", "removed")
RESTRICTION_TYPES = ("visibility_limited", "access_suspended", "download_disabled",
                     "content_removed")
RESTRICTION_TYPE_LABELS = {
    "visibility_limited": "Visibility limited",
    "access_suspended": "Access suspended",
    "download_disabled": "Download disabled",
    "content_removed": "Content removed",
}
RESTRICTED_CONTENT_TYPES = ("event", "recording", "replay", "message", "other")
APPEAL_STATUSES = ("submitted", "under_review", "upheld", "granted")

# ── notice kinds ────────────────────────────────────────────────────────────────────────
SECURITY_NOTICE_KINDS = (
    "contact_verification", "violation_confirmed",
    "urgent_alert", "event_contained", "event_resolved",
    "breakglass_started", "breakglass_ended", "breakglass_review_overdue",
    "incident_opened", "incident_update", "incident_contained", "incident_resolved",
    "abuse_received", "abuse_triaged", "abuse_closed",
    "restriction_active", "restriction_removed",
    "appeal_received", "appeal_upheld", "appeal_granted",
)


class SecurityNotice(Base):
    """One row per communicated security transition.

    Keyed on (kind, subject, VERSION). A version rather than a bare subject id because the
    same transition can legitimately recur: an incident issues many updates, a restriction
    can be applied, removed and applied again, and a break-glass session recurs per request.
    A permanent UNIQUE(kind, subject_id) would silently swallow the second occurrence.
    """

    __tablename__ = "security_notices"
    __table_args__ = (
        UniqueConstraint("kind", "subject_type", "subject_id", "version",
                         name="uq_security_notice_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,
                                                  index=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Coarse, customer-safe. Never an investigative note.
    detail: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                              server_default=func.now(), index=True)


class OrganizationSecurityContact(Base):
    """A verified destination for high-risk organization security mail.

    Exists so security notices are not simply blasted at every org admin. Only a VERIFIED
    contact receives SEC-001 and SEC-003; a pending, expired or revoked one is excluded.
    """

    __tablename__ = "organization_security_contacts"
    __table_args__ = (
        UniqueConstraint("org_id", "email", name="uq_org_security_contact_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"),
                                              nullable=False, index=True)
    # Nullable: a security contact is often a dedicated address with no platform account.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)

    # ── TRU-001 advisory routing ────────────────────────────────────────────────────────
    # Verifying a security contact IS the opt-in for security mail, so this defaults True.
    # The flag exists so an organization with several verified contacts can route security
    # ADVISORIES to the subset that handles patching, without weakening anything: SEC-001
    # and SEC-003 ignore it, because an incident notice is not optional. It is also not a
    # marketing preference and lives nowhere near one - a marketing unsubscribe can never
    # reach this column.
    advisory_subscribed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Same convention as crud/identity.py: only the sha256 hash is stored, the raw token
    # exists once in the verification message. Purpose-bound, single-use, expiring, and
    # superseded when a fresh one is issued.
    verification_purpose: Mapped[str | None] = mapped_column(String(60))
    verification_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())
    # Bumped on each re-issue, so a resent verification can be announced once per issue.
    verification_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AccessPolicyViolation(Base):
    """A CONFIRMED security-policy violation.

    Deliberately not created by the authorization layer. A 403 means "you may not do that",
    which is an ordinary and expected outcome - an expired role, a stale session, a wrong
    page. Only `services/security_comms.confirm_violation()` writes one, and only from a
    `VIOLATION_SOURCES` member; there is no code path from a permission denial to this table.
    """

    __tablename__ = "access_policy_violations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    affected_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="confirmed", nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # WHICH authoritative control established this. Never a rule id or a signature.
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Operator-authored, customer-facing. Detector internals have no column here at all.
    safe_summary: Mapped[str | None] = mapped_column(String(300))
    remediation_required: Mapped[str | None] = mapped_column(String(300))
    access_restricted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)

    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityEvent(Base):
    """A high-risk security event on its confirmation ladder.

    SEC-001 fires from `confirmed` or later only. A detector that has merely fired sits at
    `detected` and communicates nothing, which is the difference between an alert a customer
    can act on and a false positive delivered at 3am.
    """

    __tablename__ = "security_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    affected_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    reference: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), default="high", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="detected", nullable=False)

    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # CONTAINED is a distinct fact from RESOLVED and from "we revoked a token". Only an
    # operator setting this permits the containment sentence in the message.
    contained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer_safe_summary: Mapped[str | None] = mapped_column(String(500))
    remediation_required: Mapped[str | None] = mapped_column(String(300))
    # Current access posture as an operator recorded it, so the message reports state rather
    # than inferring it from a password change.
    access_state: Mapped[str | None] = mapped_column(String(120))
    audit_reference: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class SecurityIncidentDisclosure(Base):
    """The CUSTOMER-SAFE face of an internal `platform_ops.Incident`.

    A separate row rather than new columns on Incident, because the two have different
    audiences and different review paths: an operator edits the incident freely, and only a
    deliberate write here changes what a customer has been told. `Incident.detail`,
    `Incident.commander` and its severity are never copied in.
    """

    __tablename__ = "security_incident_disclosures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"),
                                                   nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"),
                                              nullable=False, index=True)
    # Copied from Incident.ref, the already customer-safe reference.
    reference: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    affected_service: Mapped[str | None] = mapped_column(String(120))
    customer_impact: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    resolution_summary: Mapped[str | None] = mapped_column(Text)
    # Only ever set when an operator commits to a time. Nothing derives it.
    next_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Whether an approved customer-facing report artifact actually exists. The resolved
    # message mentions a report only when this is true.
    report_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Bumped per published disclosure change, so each meaningful update is announced once
    # and an internal incident edit announces nothing.
    disclosure_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class AbuseReport(Base):
    """A durable abuse report. Distinct from in-event moderation, which is ephemeral."""

    __tablename__ = "abuse_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    reference: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    # Reporter identity. Held here and NEVER projected into any customer-visible read of the
    # reported party's own records - see services/security_comms.reported_party_view().
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    reporter_contact: Mapped[str | None] = mapped_column(String(255))
    reporter_name: Mapped[str | None] = mapped_column(String(200))
    # The organization the report is ABOUT, not the reporter's.
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="received", nullable=False)
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # One of ABUSE_CLOSURE_NOTES. Deliberately outcome-free: none of them names an
    # enforcement action taken against anybody.
    closure_note: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)

    received_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triaged_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContentRestriction(Base):
    """A committed restriction on one piece of content.

    The restriction dialogs in the client are UI; this row is the authoritative state, and
    no notice is sent until it is `active`.
    """

    __tablename__ = "content_restrictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    reference: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"),
                                              nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    content_label: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="proposed", nullable=False)
    restriction_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # Operator-authored and customer-facing. The complainant is never named here, and
    # `AbuseReport.reporter_*` is never copied into it.
    customer_safe_reason: Mapped[str | None] = mapped_column(String(500))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    appeal_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    appeal_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_reason: Mapped[str | None] = mapped_column(String(300))
    # Set only when a real storage removal completed. Never used to claim that every copy
    # everywhere is gone - see services/security_comms.removal_position().
    content_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped per apply/remove cycle, so a re-restriction is announceable again.
    cycle: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class RestrictionAppeal(Base):
    """An appeal against a content restriction."""

    __tablename__ = "restriction_appeals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    restriction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_restrictions.id"),
                                                      nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    submitted_by_email: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grounds: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="submitted", nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    customer_safe_decision: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    received_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
