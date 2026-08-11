import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .organization import Organization

# Phase 1 role set. "org_admin" is the existing slug for the organization admin
# (kept as-is — renaming to organization_admin would ripple through auth, dashboard,
# the frontend, and seeded rows). "host"/"moderator" added for the streaming modules.
# "billing_admin" (doc ZST-LE-COM-001 Section 25) is a customer-side commercial role
# below org_admin: real commercial-acceptance/change authority, no refund/write-off
# authority, no elevated streaming privileges — deliberately NOT part of the linear
# _ROLE_RANK ladder in security.py, since it isn't "above" or "below" host/moderator on
# any single scale. Gated via security.commercial_can(), not require_min_role().
ROLES = ("super_admin", "org_admin", "billing_admin", "host", "moderator", "speaker", "viewer")

# Zoiko-internal staff sub-roles (doc Section 25's five staff rows). Meaningful only on a
# super_admin row (see User.staff_commercial_role): unset means "full access, today's
# actual behavior, unchanged"; set narrows that one staff member to exactly what the doc's
# matrix grants that specific role (security.commercial_can). Not a replacement for
# super_admin — a scoping layer under it.
STAFF_COMMERCIAL_ROLES = ("sales", "finance_ops", "live_ops", "support", "security")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )

    full_name: Mapped[str] = mapped_column(String(120), nullable=False)

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    username: Mapped[str] = mapped_column(
        String(60),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        default="viewer",
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Operating team (e.g. "Platform Operations", "Live Events Ops"). Labels the actor on
    # the Command Center's privileged-activity feed; NULL falls back to the role label.
    department: Mapped[str | None] = mapped_column(String(80))

    # Scopes a super_admin down to one of STAFF_COMMERCIAL_ROLES for commercial actions
    # specifically (see security.commercial_can) — NULL (the default for every existing
    # account) keeps today's unrestricted behavior. Meaningless on a non-super_admin row.
    staff_commercial_role: Mapped[str | None] = mapped_column(String(20))

    reset_token: Mapped[str | None] = mapped_column(String(64))
    reset_token_expires: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # Soft delete: set on DELETE /organization/users/{id}. Distinct from is_active (which
    # PATCH-status toggles) — a soft-deleted member is excluded from listings and can't be
    # reactivated via a status change. Also flipped is_active=False so their tokens die.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped["Organization"] = relationship(
        back_populates="users"
    )