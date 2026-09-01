"""Notification-preference change events (ZST-EC-001 ORG-012).

One row per committed preference change, holding both snapshots. The pair is what makes the
confirmation email describable ("Member joined: On -> Off") without ever showing the reader
raw JSON, and what gives the Organization an audit trail of who changed its routing and when.

`notified_at` is claimed by conditional UPDATE, so a retried request, a double-submit or a
second worker cannot produce a second confirmation for the same change.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Whose settings moved. Only organization-wide preferences exist today; the column is here
# so a future per-user preference set has an honest place to record its scope rather than
# being indistinguishable from an org-wide change.
SCOPE_ORGANIZATION = "organization"
SCOPE_USER = "user"


class NotificationPreferenceEvent(Base):
    __tablename__ = "notification_preference_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    # The account whose notifications these govern, and the account that changed them. They
    # are usually the same administrator, but a separate actor column is what lets the record
    # answer "who did this to my settings" rather than only "whose settings changed".
    preference_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    preference_owner_email: Mapped[str | None] = mapped_column(String(255))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_email: Mapped[str | None] = mapped_column(String(255))

    previous_preferences: Mapped[dict | None] = mapped_column(JSON)
    current_preferences: Mapped[dict | None] = mapped_column(JSON)

    scope: Mapped[str] = mapped_column(String(20), default=SCOPE_ORGANIZATION, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
