import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .organization import Organization
    from .channel import Channel

# Phase 1 role set. "org_admin" is the existing slug for the organization admin
# (kept as-is — renaming to organization_admin would ripple through auth, dashboard,
# the frontend, and seeded rows). "host"/"moderator" added for the streaming modules.
ROLES = ("super_admin", "org_admin", "host", "moderator", "speaker", "viewer")


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

    # Per-PERSON settings that span every event: language, accessibility switches, favourite
    # speakers, notification opt-outs. A JSON column rather than four tables (or four columns)
    # because none of it is ever queried across users — it is read whole, for one person, by that
    # person. Validated by services.attendee.clean_preferences, which is a whitelist, so an
    # unknown key cannot be written.
    preferences: Mapped[dict | None] = mapped_column(JSON)

    reset_token: Mapped[str | None] = mapped_column(String(64))
    reset_token_expires: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # Soft delete: set on DELETE /organization/users/{id}. Distinct from is_active (which
    # PATCH-status toggles) — a soft-deleted member is excluded from listings and can't be
    # reactivated via a status change. Also flipped is_active=False so their tokens die.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # lazy="joined": org arrives in the same SELECT as the user. Every authenticated request
    # loads the caller (security.get_current_user) and most serializers then read org.name —
    # as a lazy load that was a second round trip per user row (10 extra queries on a 10-row
    # /admin/users page). Many-to-one on an indexed FK, so the LEFT JOIN is near-free and one
    # round trip beats N.
    organization: Mapped["Organization"] = relationship(
        back_populates="users", lazy="joined"
    )

    channels: Mapped[list["Channel"]] = relationship(
        back_populates="owner"
    )