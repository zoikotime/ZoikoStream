"""External sign-in identities linked to a ZoikoStream account (Google today).

WHY THIS TABLE EXISTS
Google sign-in originally matched an account by email address alone. An email address is not
a stable identity: a Google Workspace administrator can reassign `alex@company.com` to a
different person, and the new holder would then have signed in to the old holder's account.
Google's stable, never-reassigned account id is the ID token's `sub` claim. This table pins a
ZoikoStream account to exactly one `sub`, and Google sign-in authenticates ONLY through it
(routers/auth.py google_callback): no row, no sign-in — an email match is never enough.

HOW A ROW IS CREATED
Only by the account holder, signed in, after a fresh password step-up
(POST /api/auth/google/link/start -> Google -> POST /api/auth/google/link/complete). Never
automatically, and never because an email address happens to match.

CONSTRAINTS (all enforced by the database, not only by the code that writes rows)
  * (provider, subject) unique — one Google account can be linked to one ZoikoStream account.
  * (user_id, provider) unique — one ZoikoStream account holds at most one Google account; a
    different Google account cannot be linked on top of an existing link (it must be unlinked
    first, deliberately).
  * provider restricted to the supported providers (CHECK).
  * user_id cascades on delete — a removed account takes its links with it, and can never
    leave a `sub` pointing at nothing.

DEPLOYMENT
A brand-new table: `server/create_tables.py` (the deploy pipeline's `migrate` step) builds it
with create_all(), including the constraints above. No existing table changes.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

IDENTITY_PROVIDER_GOOGLE = "google"
# Adding a provider means adding it here AND altering ck_user_identities_provider on every
# existing database (create_all never alters an existing table's constraints).
IDENTITY_PROVIDERS = (IDENTITY_PROVIDER_GOOGLE,)


class UserIdentity(Base):
    __tablename__ = "user_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    # Google's `sub`: an opaque, case-sensitive string of at most 255 ASCII characters (OpenID
    # Connect Core 1.0 §2). Stored exactly as Google sends it — never normalised.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The verified Google email when the link was made. Evidence for support and audit only:
    # sign-in never reads it.
    email_at_link_time: Mapped[str] = mapped_column(String(255), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
        UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
        CheckConstraint(
            "provider IN (" + ", ".join(f"'{p}'" for p in IDENTITY_PROVIDERS) + ")",
            name="ck_user_identities_provider",
        ),
    )
