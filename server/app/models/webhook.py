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

DELIVERY_STATUSES = ("pending", "delivered", "failed")


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
