import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .user import User
    from .subscription import Subscription

# Account states the super admin can set. `suspended` blocks the org platform-wide.
# "restricted" added for ZST-EC-001 ORG-010: the platform previously collapsed every
# non-active operational posture into "suspended", which made a partial restriction and a
# full suspension indistinguishable to the customer being told about it.
ORG_STATUSES = ("active", "trial", "restricted", "suspended")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Admin-managed account fields. Added via create_tables.py ALTERs on existing DBs.
    domain: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    region: Mapped[str | None] = mapped_column(String(40), default="US East")
    # Usage metrics — persisted here until a real metering pipeline populates them.
    storage_used_gb: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    bandwidth_gb: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    # Test accounts are excluded from the Command Center's readiness, badge and attention
    # counts unless the console's "Include test mode" toggle is on.
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Org self-service settings (/organization/*). Added via create_tables.py ALTERs. ──
    # Profile
    slug: Mapped[str | None] = mapped_column(String(140), index=True)  # app-level uniqueness (crud)
    website: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(String(80))
    company_size: Mapped[str | None] = mapped_column(String(40))
    support_email: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str | None] = mapped_column(String(60))
    country: Mapped[str | None] = mapped_column(String(80))
    logo_url: Mapped[str | None] = mapped_column(String(500))  # shared by Profile + Branding
    # The logo for dark backgrounds. Optional: when empty, every surface falls back to
    # `logo_url`, so an organization that only ever set one logo keeps it in both themes.
    logo_url_dark: Mapped[str | None] = mapped_column(String(500))
    # Branding
    primary_color: Mapped[str | None] = mapped_column(String(20))
    secondary_color: Mapped[str | None] = mapped_column(String(20))
    theme: Mapped[str | None] = mapped_column(String(20))
    # ── Custom domain lifecycle (services/custom_domains.py owns every write) ──────────────
    # `domain` above is the requested hostname, stored normalized and unique across
    # organizations (uq_organizations_domain on lower(domain), create_tables.py).
    # `domain_status` is the authoritative state; `domain_verified` and `custom_domain_enabled`
    # are kept in step with it by the service so older readers stay correct:
    #   domain_verified        True only while ownership is proven (verified / active)
    #   custom_domain_enabled  True only while the hostname may serve traffic (active)
    domain_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    domain_status: Mapped[str] = mapped_column(String(20), default="not_configured", nullable=False)
    # Unique per organization AND per hostname: regenerated whenever the hostname changes,
    # so a TXT record published for one claim can never prove another.
    domain_verification_token: Mapped[str | None] = mapped_column(String(64))
    domain_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain_status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set while a check runs (status "verifying"), so a crashed check is recognisably stale.
    domain_check_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # First failed re-check of an ACTIVE domain; deactivation happens after the grace period.
    domain_failing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A code from custom_domains.ERROR_MESSAGES, never a raw resolver/provider exception.
    domain_error: Mapped[str | None] = mapped_column(String(40))
    # Last DNS result, for the Settings panel and the support console:
    # {"cname_ok", "cname_found", "txt_ok", "txt_present"}.
    domain_check: Mapped[dict | None] = mapped_column(JSON)
    custom_domain_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Certificate provider references (Cloudflare custom hostname id and its states).
    custom_hostname_id: Mapped[str | None] = mapped_column(String(64))
    custom_hostname_status: Mapped[str | None] = mapped_column(String(30))
    certificate_status: Mapped[str | None] = mapped_column(String(30))

    # ZST-EC-001 ORG-008. Before this there was no owner concept at all - an organization had
    # a set of interchangeable org_admins, so there was nothing for an ownership transfer to
    # move. Nullable because existing organizations genuinely have no recorded owner, and
    # inventing one by picking an arbitrary admin would misattribute accountability.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))
    # Grouped settings as JSON blobs (matches PlatformSetting.value). Shape enforced by the
    # Pydantic schemas, not the column, so toggles can evolve without a migration.
    notifications: Mapped[dict | None] = mapped_column(JSON, default=dict)
    security: Mapped[dict | None] = mapped_column(JSON, default=dict)
    # Developer: read-only in this phase (no key-management endpoints yet). Empty until a
    # later phase writes them.
    api_keys: Mapped[list | None] = mapped_column(JSON, default=list)
    webhook_urls: Mapped[list | None] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # foreign_keys is required now that ORG-008 added organizations.owner_user_id: there
    # are two FK paths between these tables, and membership is the one on User.org_id.
    # Without this SQLAlchemy cannot tell "the org's members" from "the org's owner".
    users: Mapped[list["User"]] = relationship(
        back_populates="organization",
        foreign_keys="User.org_id",
    )

    owner: Mapped["User | None"] = relationship(
        foreign_keys=[owner_user_id],
        post_update=True,   # owner is set after both rows exist; avoids a cyclic flush
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="organization",
        # A Subscription cannot exist without its Organization — `subscriptions.org_id` is NOT
        # NULL — so orphaning one is invalid by definition, and this cascade says so.
        #
        # Without it SQLAlchemy's default on `db.delete(org)` is to NULL the child's foreign
        # key, which the NOT NULL constraint then rejects with
        # `NotNullViolation: null value in column "org_id"`. That was latent for as long as
        # almost no organization had a subscription: the one production path that hard-deletes
        # an organization (crud.admin.delete_organization) hand-rolls the child delete first,
        # so it never hit this. It surfaced the moment every organization began receiving one
        # at creation.
        #
        # `delete_organization`'s explicit delete is now redundant but harmless, and is left
        # alone: it also covers rows this relationship would not have loaded.
        cascade="all, delete-orphan",
    )


# One organization per hostname, case-insensitively. create_tables.py creates the same index
# on existing databases, after refusing to proceed past any hostname claimed twice.
Index("uq_organizations_domain", func.lower(Organization.domain), unique=True)
