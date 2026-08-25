"""Account recovery and account-state lifecycle (ZST-EC-001 IDN-007, IDN-008).

Two tables, each existing because a template needs an authoritative state transition to
hang off — not because a template needs a place to write "I sent an email".

`AccountRecovery` replaces the previous recovery mechanism entirely. That mechanism was a
4-digit code in cleartext on `users.reset_token` with no attempt counter, which meant the
whole of IDN-007 rested on a credential an attacker could both guess and read out of a
database dump. The code itself now lives in `IdentityChallenge` (hashed, purpose-bound,
single-use) and this row carries the lifecycle around it.

`AccountStateEvent` records restriction and deletion transitions. One row per transition is
what makes IDN-008 safely repeatable: an account can be suspended, reactivated and suspended
again, and each is a distinct event with its own notification marker. A single timestamp on
`users` would silently swallow the second suspension.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ── Recovery lifecycle ──────────────────────────────────────────────────────────────────
# Only the states this product actually reaches. ADDITIONAL_VERIFICATION_REQUIRED is the
# state a recovery sits in while the emailed code is outstanding, which is where every
# recovery in this codebase currently begins its second step.
RECOVERY_STARTED = "started"
RECOVERY_ADDITIONAL_VERIFICATION = "additional_verification_required"
RECOVERY_VERIFIED = "verified"
RECOVERY_COMPLETED = "completed"
RECOVERY_CANCELED = "canceled"
RECOVERY_STATES = (
    RECOVERY_STARTED,
    RECOVERY_ADDITIONAL_VERIFICATION,
    RECOVERY_VERIFIED,
    RECOVERY_COMPLETED,
    RECOVERY_CANCELED,
)

# Attempts allowed against one recovery code before it is locked. Low on purpose: the code
# is short-lived and single-use, so a legitimate person needs one or two tries.
RECOVERY_MAX_ATTEMPTS = 5


class AccountRecovery(Base):
    __tablename__ = "account_recoveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Counted here rather than on the challenge so a lockout survives the challenge being
    # superseded — otherwise "request a new code" would reset the attacker's budget.
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Free-text reason category, never rendered into an email.
    reason: Mapped[str | None] = mapped_column(String(80))

    # One notification marker per transition. Each is claimed with a conditional UPDATE, so a
    # retried request or a restarted worker cannot produce a second message for the same
    # transition. Four columns rather than one because each transition is separately
    # notifiable and they are not ordered by a single clock.
    started_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Account state lifecycle ─────────────────────────────────────────────────────────────
# Only states this product can actually reach. There is deliberately no DELETION_SCHEDULED:
# no scheduling subsystem exists, so a scheduled-deletion notice would promise a
# cancellation window the platform cannot honour. Reported as a product gap instead.
STATE_RESTRICTED = "restricted"          # deactivated / suspended, reversible
STATE_REACTIVATED = "reactivated"
STATE_DELETION_COMPLETED = "deletion_completed"
ACCOUNT_STATES = (STATE_RESTRICTED, STATE_REACTIVATED, STATE_DELETION_COMPLETED)


class AccountStateEvent(Base):
    """One committed restriction or deletion transition."""

    __tablename__ = "account_state_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Nullable, and no FK: a hard delete removes the user row while this record must
    # outlive it — the same reasoning AuditLog already applies to its actor.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    # Captured at transition time because after a hard delete there is nowhere to read it.
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    state: Mapped[str] = mapped_column(String(40), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Coarse category only. Internal detection detail and admin notes must never reach the
    # recipient, so nothing more specific is stored on the row the template reads.
    reason_category: Mapped[str | None] = mapped_column(String(60))

    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
