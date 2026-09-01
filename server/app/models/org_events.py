"""Organization membership lifecycle events (ZST-EC-001 ORG-003 / ORG-004).

One row per committed membership-policy transition. The row is what makes the notice
repeatable-safe: a member can be promoted, demoted and removed over a lifetime, and each of
those is its own event with its own notification claim, so "already told them" is answered
by the database rather than by whichever router happened to run.

`previous_access` / `current_access` are stored as the rendered, user-facing strings rather
than raw permission internals. Two reasons: the email must never expose permission-bit
internals when role names exist, and a snapshot taken at commit time stays truthful even if
the role catalogue is renamed later.

`user_id` carries no foreign key and is nullable on purpose — the same reasoning as
AccountStateEvent. A membership removal must remain explainable after the underlying user
row is hard-deleted, and a FK would either block that delete or erase the record with it.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# What the transition was. Kept deliberately small: these are the two ORG families that
# have a real authoritative trigger in this codebase today.
ORG_ACCESS_CHANGED = "access_changed"        # ORG-003
ORG_MEMBERSHIP_REMOVED = "membership_removed"  # ORG-004

ORG_EVENT_KINDS = (ORG_ACCESS_CHANGED, ORG_MEMBERSHIP_REMOVED)


class OrgMembershipEvent(Base):
    """A committed change to one member's standing in one organization."""

    __tablename__ = "org_membership_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # No FK — see module docstring.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Rendered display strings, not permission internals.
    previous_access: Mapped[str | None] = mapped_column(Text)
    current_access: Mapped[str | None] = mapped_column(Text)

    # True when the member's Zoiko identity was ALSO restricted in the same operation.
    # ORG-004 must not imply the identity ended; IDN-008 owns that claim separately, and
    # this flag is what lets the two stay distinct instead of one message guessing.
    identity_also_restricted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # One notification set per transition, claimed by conditional UPDATE.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
