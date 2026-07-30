import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .user import User
    from .subscription import Subscription
    from .api_key import ApiKey
    from .support_ticket import SupportTicket

ORG_STATUSES = ("trial", "active", "suspended")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # Suspended orgs keep their data but their members can't log in -- see login() in
    # auth.py. Platform superadmins are unaffected (their own org is never suspended).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Admin-facing lifecycle state ("trial" | "active" | "suspended"). Kept in sync with
    # is_active by the admin router (suspended -> is_active=False, otherwise True) rather
    # than replacing is_active outright, so the existing login gate keeps working untouched.
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    # No metering pipeline exists yet -- these are real stored numbers (not fabricated),
    # just not automatically updated from usage. Default 0 until that's wired up.
    storage_used_gb: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    bandwidth_gb: Mapped[float] = mapped_column(Float, default=0, nullable=False)

    # Org Settings page (routers/organization.py's profile/security/notifications/domain/
    # branding endpoints) -- see app/schemas/org_settings.py for the shape of security/
    # notifications when unset (None here means "use the frontend's own defaults").
    slug: Mapped[str | None] = mapped_column(String(140), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(80), nullable=True)
    company_size: Mapped[str | None] = mapped_column(String(40), nullable=True)
    support_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    primary_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notifications: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    security: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    users: Mapped[list["User"]] = relationship(
        back_populates="organization"
    )
    subscription: Mapped["Subscription | None"] = relationship(back_populates="organization", uselist=False)
    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="organization")
    support_tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="organization")