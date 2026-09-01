"""Developer-platform operational domains (ZST-EC-001 DEV-010 / DEV-012).

Two small authoritative models, each added because the corresponding family had nothing
real to report:

  * RateLimitEvent — DEV-010. `ratelimit.py` counts in memory, per worker, and the counters
    vanish on restart. An email fired from that would be describing one process's opinion.
    This is the durable record of a governed threshold actually being crossed, which is the
    only thing a notification may be driven from.

  * DeveloperDataExport — DEV-012. The product's only export was a browser-side CSV
    (client/src/utils/export.js), which is not a governed export: nothing records who asked,
    what was produced, or who downloaded it. This is the server-side lifecycle.

Neither model stores secret material. The export never contains API key secrets or webhook
signing secrets — see services/developer_export.py, where the field selection is explicit
rather than a dump of whatever a row happens to hold.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ── DEV-010 ─────────────────────────────────────────────────────────────────────────────

# A single refusal is noise. A governed event needs sustained breaches inside one window,
# which is what makes the signal deterministic and explainable rather than a guess.
RATE_LIMIT_EVENT_THRESHOLD = 25
RATE_LIMIT_EVENT_WINDOW_SECONDS = 300

RATE_LIMIT_OPEN = "open"
RATE_LIMIT_CLEARED = "cleared"


class RateLimitEvent(Base):
    """One governed rate-limit threshold crossing, durable across restarts and workers."""

    __tablename__ = "rate_limit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # The principal the limit was applied to. An IP for unauthenticated routes, a user id
    # where one is known. Never a credential secret.
    principal: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    rule: Mapped[str] = mapped_column(String(80), nullable=False)

    observed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resets_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=RATE_LIMIT_OPEN, nullable=False)

    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


# ── DEV-012 ─────────────────────────────────────────────────────────────────────────────

EXPORT_REQUESTED = "requested"
EXPORT_PROCESSING = "processing"
EXPORT_READY = "ready"
EXPORT_EXPIRED = "expired"
EXPORT_FAILED = "failed"
EXPORT_STATUSES = (EXPORT_REQUESTED, EXPORT_PROCESSING, EXPORT_READY, EXPORT_EXPIRED,
                   EXPORT_FAILED)

EXPORT_CREDENTIALS = "api_credentials"
EXPORT_WEBHOOKS = "webhook_endpoints"
EXPORT_DELIVERIES = "webhook_deliveries"
EXPORT_TYPES = (EXPORT_CREDENTIALS, EXPORT_WEBHOOKS, EXPORT_DELIVERIES)

EXPORT_TTL_HOURS = 24


class DeveloperDataExport(Base):
    """One requested export of developer metadata."""

    __tablename__ = "developer_data_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False,
                                              index=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    requested_by_email: Mapped[str | None] = mapped_column(String(255))
    export_type: Mapped[str] = mapped_column(String(40), nullable=False)

    status: Mapped[str] = mapped_column(String(16), default=EXPORT_REQUESTED, nullable=False,
                                        index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # Where the private object lives. Never a public URL — the download is authorized per
    # request, so the object itself never becomes reachable without one.
    object_key: Mapped[str | None] = mapped_column(String(500))
    # sha256 of the one-time download token. Purpose-bound and short-lived, like every
    # other challenge in this codebase.
    download_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    row_count: Mapped[int | None] = mapped_column(Integer)
    failure_category: Mapped[str | None] = mapped_column(String(60))

    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_downloaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Append-only access log, one line per served download. Content is never recorded.
    access_log: Mapped[str | None] = mapped_column(Text)

    ready_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
