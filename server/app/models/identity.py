"""Identity challenges (ZST-EC-001 IDN-001).

One row per issued email-verification challenge. The raw token is emailed and NEVER
stored — only its sha256 hash lives here, exactly as `Invitation.token_hash` already
works (crud/organization.py::_hash_token). Same reasoning: the token is 256-bit random
from `secrets.token_urlsafe(32)`, so a plain sha256 is sufficient and no salt/bcrypt
work factor is needed.

Deliberately NOT reusing `users.reset_token`: that column is the password-reset OTP, it
stores its value in cleartext, and a shared credential would break purpose-binding — a
reset code must never satisfy an email-verification check or vice versa. `purpose` is
carried on the row and asserted on every lookup so the two can never cross over.

Lifecycle of a challenge: issued -> (consumed | superseded | expired). All three end
states are terminal and are represented by timestamps rather than a status enum, so the
row remains an evidence record of when each transition happened.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Purpose-bound: a challenge is only ever valid for the job it was issued for. IDN-001 is
# the only purpose in scope; IDN-003..IDN-008 are explicitly out of scope for this change.
CHALLENGE_PURPOSES = ("email_verification",)

EMAIL_VERIFICATION = "email_verification"


class IdentityChallenge(Base):
    """A single-use, short-lived, purpose-bound identity challenge."""

    __tablename__ = "identity_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # Asserted on every lookup — see crud/identity.py::find_active_challenge.
    purpose: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    # sha256 of the raw token. Unique so a hash collision or a double-issue is a hard error
    # rather than an ambiguous lookup.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Single-use marker. Set inside the same transaction that flips the user's verified
    # state, so a token can never be spent twice even under concurrent requests.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Set when a newer challenge replaces this one (resend). Keeps the audit trail instead
    # of deleting the row, and makes the old link stop working immediately.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Attempt metadata. `attempts` counts redemption attempts against this specific row;
    # requested_ip is the issuing caller's address (never the redeemer's, which would make
    # the row a movement log). Both exist for abuse investigation, not for authorization.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_ip: Mapped[str | None] = mapped_column(String(64))

    user: Mapped["User"] = relationship()  # noqa: F821
