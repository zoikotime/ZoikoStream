"""Physical schema for the public Guide (ZST-DB-PUB-GUIDE-001 §4–10, all 19 tables).

Conventions taken from the DDL characteristics table (§21):
  * UUID keys for application-facing entities; bigint identity for append-only event tables.
  * timestamptz everywhere.
  * Enumerations are CHECK-constrained varchar, not PostgreSQL ENUM, so they can evolve.
  * Conversation text, lead values, handoff summaries, feedback comments and sensitive
    downstream refs are bytea CIPHERTEXT only (app/guide/crypto.py). No plaintext column,
    index, trigger or generated column exists for any of them (§13, §16.1).
  * Raw session capabilities, CSRF tokens and Idempotency-Keys are never stored — only
    keyed hashes (§13).
  * purge_after is NOT NULL on every session-local / sensitive row (DB-INV-10); the value is
    computed from the retention policy at write time (app/guide/retention.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer,
    LargeBinary, SmallInteger, String, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import GuideBase


def _check_in(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


def _now():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ── Vocabularies (shared with schemas/services so a value cannot drift) ────────────────────

SESSION_STATES = (
    "ACTIVE", "LEAD_FLOW", "VIEWER_FLOW", "SUPPORT_HANDOFF", "SALES_HANDOFF",
    "KNOWLEDGE_GAP", "RESTRICTED", "DEGRADED", "RATE_LIMITED", "ENDED",
)
PAGE_TYPES = (
    "home", "product", "solutions", "pricing", "help", "viewer", "event", "contact",
    "trust", "privacy", "status", "developer", "other",
)
DEVICE_CLASSES = ("desktop", "tablet", "mobile", "unknown")
SOURCE_TYPES = ("product", "pricing", "help", "trust", "legal", "status", "developer",
                "public_event", "accessibility", "sales_help")
SOURCE_VERSION_STATUSES = ("draft", "published", "retired", "quarantined")
HREF_KINDS = ("relative", "external_allowlisted", "none")
MESSAGE_ROLES = ("user", "assistant", "system_event")
MESSAGE_STATUSES = ("complete", "incomplete", "failed")
ACTION_TYPES = ("navigate", "start_support_flow", "start_lead_flow", "handoff", "sign_in",
                "open_help", "open_status")
SUPPORT_FLOW_TYPES = ("viewer_access", "playback", "audio", "captions", "replay",
                      "viewer_other")
SUPPORT_FLOW_STATUSES = ("in_progress", "resolved", "escalated", "cancelled")
CONSENT_PURPOSES = ("sales_contact", "optional_analytics")
CONSENT_STATUSES = ("granted", "denied", "withdrawn")
LEAD_FLOW_KINDS = ("demo", "sales")
LEAD_FLOW_STATUSES = ("collecting", "ready_to_submit", "submitting", "submitted", "failed",
                      "unknown", "cancelled")
LEAD_FIELDS = ("full_name", "work_email", "organization", "role", "country_code", "use_case",
               "event_timing", "notes")
LEAD_DATA_CLASSES = ("PII", "BUSINESS_CONTACT", "BUSINESS_CONTEXT")
HANDOFF_TARGETS = ("sales", "support", "organizer", "sign_in", "help_center", "status",
                   "privacy")
HANDOFF_OUTCOMES = ("pending", "route_available", "confirmed_submission", "failed", "unknown")
OPERATION_TYPES = ("lead_submit", "handoff_submit")
# dead_letter: an `unknown` outcome the reconciler gave up on (ARCH §12.2; migration 0005).
OPERATION_OUTCOMES = ("in_progress", "confirmed", "failed", "unknown", "dead_letter")
FEEDBACK_TARGETS = ("answer", "session")
FEEDBACK_RATINGS = ("up", "down")


# ── Session & context ──────────────────────────────────────────────────────────────────────

class GuideSession(GuideBase):
    __tablename__ = "guide_sessions"
    __table_args__ = (
        _check_in("state", SESSION_STATES, "ck_guide_sessions_state"),
        _check_in("device_class", DEVICE_CLASSES, "ck_guide_sessions_device_class"),
        Index("ix_guide_sessions_expires_active", "expires_at",
              postgresql_where=text("state <> 'ENDED'")),
        Index("ix_guide_sessions_purge_after", "purge_after"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    capability_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    csrf_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Keyed hash of client_session_nonce: dedupes accidental create replays (API §7.1).
    client_nonce_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-US")
    device_class: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    current_context_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    analytics_consent: Mapped[str] = mapped_column(String(16), nullable=False,
                                                   default="unknown")
    # Paused-flow bookkeeping for interruption/resume (ZST-CONV-PUB-GUIDE-001 §3).
    active_flow_kind: Mapped[str | None] = mapped_column(String(16))
    active_flow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = _now()
    last_activity_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(32))
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuidePublicEventContext(GuideBase):
    __tablename__ = "guide_public_event_contexts"
    __table_args__ = (Index("ix_guide_event_ctx_purge_after", "purge_after"),)

    event_context_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True,
                                                        default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    # Allowlisted claims only (DB-INV-09); the raw token is never stored.
    safe_claims: Mapped[dict] = mapped_column(JSONB, nullable=False)
    token_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuideSessionContextVersion(GuideBase):
    __tablename__ = "guide_session_context_versions"
    __table_args__ = (
        _check_in("page_type", PAGE_TYPES, "ck_guide_ctx_page_type"),
        Index("ix_guide_ctx_latest", "session_id", text("context_version DESC")),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="CASCADE"), primary_key=True)
    context_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    page_type: Mapped[str] = mapped_column(String(32), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64))
    device_class: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_point: Mapped[str | None] = mapped_column(String(32))
    event_context_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_public_event_contexts.event_context_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Public knowledge metadata & destinations ───────────────────────────────────────────────

class GuideKnowledgeSource(GuideBase):
    __tablename__ = "guide_knowledge_sources"
    __table_args__ = (_check_in("source_type", SOURCE_TYPES, "ck_guide_ks_source_type"),)

    source_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(96))
    authority_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)
    risk_class: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW")
    canonical_path: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = _now()


class GuideKnowledgeSourceVersion(GuideBase):
    __tablename__ = "guide_knowledge_source_versions"
    __table_args__ = (
        _check_in("status", SOURCE_VERSION_STATUSES, "ck_guide_ksv_status"),
        UniqueConstraint("source_id", "version", name="uq_guide_ksv_source_version"),
        Index("ix_guide_ksv_published", "source_id", text("published_at DESC"),
              postgresql_where=text("status = 'published'")),
    )

    source_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True,
                                                         default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("guide_knowledge_sources.source_id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    content_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-US")
    jurisdiction: Mapped[str | None] = mapped_column(String(16))
    public_retrieval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _now()
    created_at: Mapped[datetime] = _now()


class GuideDestination(GuideBase):
    """Trusted CTA registry. href values are controlled configuration, never model-authored
    (DB-INV-08)."""
    __tablename__ = "guide_destination_registry"
    __table_args__ = (_check_in("href_kind", HREF_KINDS, "ck_guide_dest_href_kind"),)

    destination_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    href_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    href_value: Mapped[str | None] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = _now()


# ── Conversation & evidence ────────────────────────────────────────────────────────────────

class GuideMessage(GuideBase):
    __tablename__ = "guide_messages"
    __table_args__ = (
        _check_in("role", MESSAGE_ROLES, "ck_guide_msg_role"),
        _check_in("status", MESSAGE_STATUSES, "ck_guide_msg_status"),
        Index("ix_guide_msg_session_created", "session_id", "created_at"),
        Index("uq_guide_msg_client_id", "session_id", "client_message_id", unique=True,
              postgresql_where=text("client_message_id IS NOT NULL")),
        Index("ix_guide_msg_purge_after", "purge_after"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="CASCADE"), nullable=False)
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    in_reply_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_messages.message_id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="complete")
    content_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    content_digest: Mapped[bytes | None] = mapped_column(LargeBinary)
    intent_class: Mapped[str | None] = mapped_column(String(32))
    answer_class: Mapped[str | None] = mapped_column(String(32))
    boundary_state: Mapped[str | None] = mapped_column(String(32))
    grounded: Mapped[bool | None] = mapped_column(Boolean)
    # Detector classes only (e.g. ["event_pin"]) — never the detected value.
    security_flags: Mapped[list | None] = mapped_column(JSONB)
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    model_profile: Mapped[str | None] = mapped_column(String(32))
    rag_release_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuideMessageSource(GuideBase):
    __tablename__ = "guide_message_sources"

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_messages.message_id", ondelete="CASCADE"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_knowledge_source_versions.source_version_id", ondelete="RESTRICT"),
        nullable=False)


class GuideMessageAction(GuideBase):
    __tablename__ = "guide_message_actions"
    __table_args__ = (_check_in("action_type", ACTION_TYPES, "ck_guide_msg_action_type"),)

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_messages.message_id", ondelete="CASCADE"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_id: Mapped[str | None] = mapped_column(
        ForeignKey("guide_destination_registry.destination_id", ondelete="RESTRICT"))


# ── Viewer support ─────────────────────────────────────────────────────────────────────────

class GuideSupportFlow(GuideBase):
    __tablename__ = "guide_support_flows"
    __table_args__ = (
        _check_in("flow_type", SUPPORT_FLOW_TYPES, "ck_guide_sf_flow_type"),
        _check_in("status", SUPPORT_FLOW_STATUSES, "ck_guide_sf_status"),
        Index("ix_guide_sf_session_started", "session_id", text("started_at DESC")),
        Index("ix_guide_sf_purge_after", "purge_after"),
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="CASCADE"), nullable=False)
    flow_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress")
    current_step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Step history for Back (server-owned; the client cannot rewrite it).
    step_path: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    resolution_code: Mapped[str | None] = mapped_column(String(48))
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuideSupportStepEvent(GuideBase):
    __tablename__ = "guide_support_step_events"
    __table_args__ = (
        UniqueConstraint("flow_id", "client_step_id", name="uq_guide_sse_client_step"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    flow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_support_flows.flow_id", ondelete="CASCADE"), nullable=False)
    client_step_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Allowlisted option/action ids only — never free text.
    answer_structured: Mapped[dict] = mapped_column(JSONB, nullable=False)
    from_state: Mapped[str] = mapped_column(String(64), nullable=False)
    to_state: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome_code: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Consent & lead ─────────────────────────────────────────────────────────────────────────

class GuideConsent(GuideBase):
    """Immutable receipts; a change of mind supersedes, never mutates (DB-AT-08)."""
    __tablename__ = "guide_consents"
    __table_args__ = (
        _check_in("purpose", CONSENT_PURPOSES, "ck_guide_consent_purpose"),
        _check_in("status", CONSENT_STATUSES, "ck_guide_consent_status"),
        Index("ix_guide_consent_current", "session_id", "purpose", text("recorded_at DESC")),
        Index("ix_guide_consent_purge_after", "purge_after"),
    )

    consent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # SET NULL (migration 0005): a receipt is evidence of a purpose decision and is kept for
    # its own 24-month baseline (Security/Privacy §16), long after the 30-day session row.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="SET NULL",
                   name="fk_guide_consents_session"))
    receipt_ref: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    notice_version: Mapped[str] = mapped_column(String(48), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="guide")
    supersedes_consent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_consents.consent_id", ondelete="SET NULL"))
    recorded_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuideLeadFlow(GuideBase):
    __tablename__ = "guide_lead_flows"
    __table_args__ = (
        _check_in("flow_kind", LEAD_FLOW_KINDS, "ck_guide_lead_kind"),
        _check_in("status", LEAD_FLOW_STATUSES, "ck_guide_lead_status"),
        Index("ix_guide_lead_session_started", "session_id", text("started_at DESC")),
        Index("ix_guide_lead_purge_after", "purge_after"),
        Index("ix_guide_lead_email_lookup", "email_lookup_hash",
              postgresql_where=text("email_lookup_hash IS NOT NULL")),
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="CASCADE"), nullable=False)
    flow_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="collecting")
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    required_fields: Mapped[list] = mapped_column(JSONB, nullable=False)
    optional_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    notice_version: Mapped[str] = mapped_column(String(48), nullable=False)
    consent_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    consent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_consents.consent_id", ondelete="SET NULL"))
    source_intent: Mapped[str | None] = mapped_column(String(32))
    # HMAC of the normalized work email (crypto.keyed_hash, purpose LEAD_EMAIL_LOOKUP): lets a
    # verified privacy-rights request find this lead without decrypting (Security/Privacy §20).
    email_lookup_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuideLeadFieldValue(GuideBase):
    __tablename__ = "guide_lead_field_values"
    __table_args__ = (
        _check_in("field_id", LEAD_FIELDS, "ck_guide_lead_field_id"),
        _check_in("data_class", LEAD_DATA_CLASSES, "ck_guide_lead_data_class"),
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_lead_flows.flow_id", ondelete="CASCADE"), primary_key=True)
    field_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    value_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    data_class: Mapped[str] = mapped_column(String(24), nullable=False)
    updated_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Handoffs & external operations ─────────────────────────────────────────────────────────

class GuideExternalOperation(GuideBase):
    __tablename__ = "guide_external_operations"
    __table_args__ = (
        _check_in("operation_type", OPERATION_TYPES, "ck_guide_extop_type"),
        _check_in("outcome", OPERATION_OUTCOMES, "ck_guide_extop_outcome"),
        UniqueConstraint("operation_type", "idempotency_key_hash", name="uq_guide_extop_idem"),
        Index("ix_guide_extop_unknown", "last_attempt_at",
              postgresql_where=text("outcome = 'unknown'")),
    )

    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Recovery-critical: survives session purge (SET NULL) so reconciliation still works.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="SET NULL"))
    operation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downstream_ref_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    public_reference: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = _now()
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuideHandoff(GuideBase):
    __tablename__ = "guide_handoffs"
    __table_args__ = (
        _check_in("target", HANDOFF_TARGETS, "ck_guide_handoff_target"),
        _check_in("outcome", HANDOFF_OUTCOMES, "ck_guide_handoff_outcome"),
        Index("ix_guide_handoff_purge_after", "purge_after"),
    )

    handoff_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="CASCADE"), nullable=False)
    target: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(48))
    destination_id: Mapped[str | None] = mapped_column(
        ForeignKey("guide_destination_registry.destination_id", ondelete="RESTRICT"))
    summary_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    external_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_external_operations.operation_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Feedback, config & audit ───────────────────────────────────────────────────────────────

class GuideFeedback(GuideBase):
    __tablename__ = "guide_feedback"
    __table_args__ = (
        _check_in("target_type", FEEDBACK_TARGETS, "ck_guide_fb_target"),
        _check_in("rating", FEEDBACK_RATINGS, "ck_guide_fb_rating"),
        UniqueConstraint("session_id", "client_feedback_id", name="uq_guide_fb_client_id"),
        Index("ix_guide_fb_purge_after", "purge_after"),
    )

    feedback_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # SET NULL: structured feedback outlives the session (12-month baseline), the comment
    # does not (comment_purge_after).
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="SET NULL"))
    client_feedback_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64))
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    comment_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    comment_purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuideRuntimeConfig(GuideBase):
    __tablename__ = "guide_runtime_config"

    config_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    config_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(96))
    updated_at: Mapped[datetime] = _now()


class GuideAuditEvent(GuideBase):
    """Structured non-content facts only. Never raw chat, lead values or secrets."""
    __tablename__ = "guide_audit_events"
    __table_args__ = (
        Index("ix_guide_audit_session_created", "session_id", "created_at"),
        Index("ix_guide_audit_created", "created_at"),
        Index("ix_guide_audit_request", "request_id"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("guide_sessions.session_id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _now()
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Free text never gets an index; this list is what test_schema asserts against.
CIPHERTEXT_COLUMNS = {
    "guide_messages": ("content_ciphertext",),
    "guide_lead_field_values": ("value_ciphertext",),
    "guide_handoffs": ("summary_ciphertext",),
    "guide_external_operations": ("downstream_ref_ciphertext",),
    "guide_feedback": ("comment_ciphertext",),
}
