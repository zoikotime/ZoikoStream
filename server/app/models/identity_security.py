"""Sign-in event history (ZST-EC-001 IDN-003, IDN-004).

One append-only row per authentication decision — succeeded, failed, or blocked. Three jobs
in one table, because they are three readings of the same fact and splitting them would mean
joining them back together on every risk evaluation:

  * IDN-003 needs the history of SUCCESSFUL sign-ins to decide whether a context is new.
  * IDN-004 needs the recent FAILED attempts to decide whether to block.
  * Both need a durable record that a notification was already sent, so a retry cannot
    produce a second email.

`user_id` is nullable on purpose: a failed attempt against an address that does not exist
still deserves a row for rate analysis, and inventing a user for it would be a lie.

Deliberately NOT stored: raw User-Agent strings, full IP addresses, precise geolocation,
device fingerprints, or anything else that would make this table a surveillance record.
What is kept is the coarsest representation that still answers "is this the same kind of
place and machine as last time".
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# What happened. Drives which template, if any, fires.
SIGN_IN_OUTCOMES = ("success", "failed", "blocked")

# Risk decision vocabulary. Deterministic and explainable — there is no scoring model here
# and none is implied. See services/identity_security.py for the rules.
ALLOW = "allow"
ALLOW_NEW_CONTEXT = "allow_new_context"
BLOCK_SUSPICIOUS = "block_suspicious"
RISK_DECISIONS = (ALLOW, ALLOW_NEW_CONTEXT, BLOCK_SUSPICIOUS)

AUTH_METHOD_PASSWORD = "password"


class SignInEvent(Base):
    __tablename__ = "sign_in_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # NULL when the attempt named an address with no account. Never a fabricated id.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)

    # Non-secret correlation id shown to the account holder and quotable to Support. It
    # authorizes nothing and is not derived from any token — see services.identity_security.
    session_reference: Mapped[str] = mapped_column(String(32), nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    authentication_method: Mapped[str] = mapped_column(String(32), nullable=False)

    # Normalized for human reading — "Chrome", "Windows". Never the raw User-Agent.
    browser_family: Mapped[str | None] = mapped_column(String(40))
    platform_family: Mapped[str | None] = mapped_column(String(40))

    # Coarse network bucket (IPv4 /24, IPv6 /48). Enough to notice "somewhere else",
    # not enough to locate anyone. Full addresses are never persisted here.
    network_context: Mapped[str | None] = mapped_column(String(64))

    # Only ever populated by a real geo provider. There is none today, so this stays NULL
    # and the templates render "Approximate location unavailable" rather than a guess.
    approximate_location: Mapped[str | None] = mapped_column(String(120))

    # True when this successful sign-in did not match any earlier successful context.
    is_new_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Why a block happened, in internal vocabulary. Never rendered into an email: the
    # baseline forbids disclosing detection logic.
    risk_decision: Mapped[str] = mapped_column(String(32), nullable=False, default=ALLOW)

    # Duplicate prevention. Set when the corresponding IDN-003/IDN-004 message is handed to
    # the provider; a retry that finds it already set sends nothing.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # The two hot reads: "recent failures for this account" and "has this context
        # succeeded before".
        Index("ix_sign_in_events_user_time", "user_id", "occurred_at"),
        Index("ix_sign_in_events_user_outcome", "user_id", "outcome"),
    )
