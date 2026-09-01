"""Step-up (recent-auth) grants (ZST-EC-001 ORG-003 / ORG-008).

ORG-003 and ORG-008 both audited PARTIAL for the same reason: the canonical control list
requires step-up authentication for high-risk grants and ownership transfer, and this
codebase had none — no re-authentication, no MFA, and a JWT carrying only `sub`, `role` and
`exp`, so recent-auth could not even be derived from the session.

This is the missing mechanism. A grant is server-side state, not a claim in a token:

  * issued only after the holder re-verifies their password in the same request
  * purpose-bound — a grant minted for a role change cannot authorize a transfer
  * short-lived, and single-use for the operation it authorizes
  * the reference handed to the client is opaque and stored only as a sha256 hash, so a
    leaked database row cannot be replayed

Deliberately NOT a boolean. `step_up_verified = true` on a request would assert that
re-authentication happened without anything having verified it, which is the exact failure
this table exists to avoid.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Purposes. Kept small and explicit — a grant is only ever valid for the operation it names.
STEP_UP_HIGH_RISK_ROLE_GRANT = "high_risk_role_grant"
STEP_UP_OWNERSHIP_TRANSFER = "ownership_transfer"
STEP_UP_SUPPORT_EMERGENCY_APPROVAL = "support_emergency_approval"

STEP_UP_PURPOSES = (
    STEP_UP_HIGH_RISK_ROLE_GRANT,
    STEP_UP_OWNERSHIP_TRANSFER,
    STEP_UP_SUPPORT_EMERGENCY_APPROVAL,
)

# Short by design. Long enough to complete the operation the user just re-authenticated for,
# short enough that a grant left lying around is not a standing privilege.
STEP_UP_TTL_MINUTES = 5


class StepUpGrant(Base):
    """One completed re-authentication, valid for one purpose, for a few minutes."""

    __tablename__ = "step_up_grants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False,
                                               index=True)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    # sha256 of the opaque reference handed to the client. The raw value is never stored,
    # for the same reason a password never is.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True,
                                            nullable=False)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # What the grant was actually spent on, for the audit trail.
    consumed_for: Mapped[str | None] = mapped_column(String(120))
    requested_ip: Mapped[str | None] = mapped_column(String(64))
