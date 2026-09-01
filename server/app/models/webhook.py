"""Outbound webhook delivery — org-scoped endpoint registration + the delivery log that
lets `client/src/pages/organization/Webhooks.jsx` show something real instead of the
"delivery outcomes are not recorded" message it used to have no choice but to display.

Two tables, not five: WebhookEndpoint is the registration (who to call, on what events);
WebhookDelivery is one row per attempted call, so the delivery log and retry queue read
from the same place (services/webhooks.py's run_webhook_retries ticker).

`WebhookEndpoint.secret` is stored in PLAIN TEXT, unlike every other credential in this
app (API keys hash-only, EventAccessLink token_hash-only) — deliberately, because it isn't
a bearer credential the server verifies against a hash, it's a SHARED verification secret
the subscriber's own endpoint needs to keep computing HMACs against for as long as the
webhook is live (same reasoning Stripe/GitHub webhook secrets are re-viewable, not
reveal-once). It is never included in WebhookEndpointOut's listing shape though — only a
dedicated reveal action returns it, same reveal-on-demand posture as LiveIngressEndpoint's
publish key.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

# Matches Webhooks.jsx's EVENT_CATALOGUE verbatim — the contract already documented to org
# admins before any of this existed to enforce it.
WEBHOOK_EVENTS = (
    "session.started", "session.paused", "session.ended",
    "recording.ready", "recording.failed", "transcript.ready",
    "registration.created", "access_link.revoked",
)

# "dead_lettered" is distinct from "failed": failed is one delivery that exhausted its
# retries, dead_lettered marks it as a retained event awaiting a controlled replay rather
# than something silently dropped (ZST-EC-001 DEV-007).
DELIVERY_STATUSES = ("pending", "delivered", "failed", "dead_lettered")

# ── DEV-006 endpoint verification ───────────────────────────────────────────────────────
# An endpoint is NOT production-eligible until it proves control of the URL. Before this,
# `enabled` was the only gate, so any URL an admin typed started receiving real customer
# event payloads immediately — including a URL typed by mistake, or one pointing at an
# internal address.
WEBHOOK_PENDING_VERIFICATION = "pending_verification"
WEBHOOK_VERIFIED = "verified"
WEBHOOK_DISABLED = "disabled"
WEBHOOK_STATUSES = (WEBHOOK_PENDING_VERIFICATION, WEBHOOK_VERIFIED, WEBHOOK_DISABLED)

# ── DEV-007 endpoint health ─────────────────────────────────────────────────────────────
WEBHOOK_HEALTHY = "healthy"
WEBHOOK_DEGRADED = "degraded"
WEBHOOK_HEALTH_STATES = (WEBHOOK_HEALTHY, WEBHOOK_DEGRADED)

# Deterministic threshold, not a guess: a health notice fires only after this many
# CONSECUTIVE terminal failures, so one transient 500 never alarms anybody.
WEBHOOK_FAILURE_THRESHOLD = 3
# Consecutive terminal failures after which the endpoint stops receiving production events.
WEBHOOK_DISABLE_THRESHOLD = 10
VERIFICATION_TTL_HOURS = 24

# ── DEV-008 signing-secret rotation ─────────────────────────────────────────────────────
# During the overlap window BOTH secrets sign every delivery, as separately versioned
# entries in one X-Zoiko-Signature header. That is what makes the window real rather than
# announced: a subscriber can verify with either secret while they migrate, and the old one
# genuinely stops being emitted when the window closes.
ROTATION_OVERLAP_HOURS = 72
ROTATION_ENDING_WARNING_HOURS = 24


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # subset of WEBHOOK_EVENTS
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    # ── DEV-006 verification ────────────────────────────────────────────────────────────
    # `enabled` remains the customer's own on/off switch. `status` is the platform's
    # production-eligibility gate, and both must be satisfied before an event is queued.
    status: Mapped[str] = mapped_column(String(24), default=WEBHOOK_PENDING_VERIFICATION,
                                        nullable=False, index=True)
    # sha256 of the challenge. The raw value is handed out once and never stored, the same
    # posture as every other challenge in this codebase.
    verification_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verification_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── DEV-008 signing-secret rotation ─────────────────────────────────────────────────
    # `secret` above stays the CURRENT signing key. `previous_secret` holds the retiring one
    # for the length of the overlap window only, and is cleared when the window closes.
    #
    # Both are stored as recoverable material rather than hashes, unavoidably: the server
    # has to produce the HMAC, so a one-way hash could not sign anything. The model
    # docstring already says this; rotation does not change that posture, it bounds how long
    # any one secret is in play.
    previous_secret: Mapped[str | None] = mapped_column(String(64))
    secret_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    previous_secret_version: Mapped[int | None] = mapped_column(Integer)
    rotation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotation_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    rotation_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rotation_started_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotation_ending_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── DEV-007 health ──────────────────────────────────────────────────────────────────
    health: Mapped[str] = mapped_column(String(16), default=WEBHOOK_HEALTHY, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    degraded_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(String(60))
    # One notification per health-state TRANSITION, never one per retry.
    degraded_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDelivery(Base):
    """One row per attempted delivery. `payload` is the exact body sent (or that will be
    sent) so a delivery can be inspected without recomputing it from whatever triggered it
    — the trigger site may have moved on or the underlying row may have changed since."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_endpoints.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_response_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    # ── DEV-007 dead-letter + replay ────────────────────────────────────────────────────
    # An exhausted delivery is retained, not dropped. Replay preserves the ORIGINAL delivery
    # row (and therefore its event id) rather than creating a new event, so a subscriber
    # that already succeeded cannot be sent a duplicate.
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replayed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    replay_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
