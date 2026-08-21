import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, JSON, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .subscription import Subscription


class Plan(Base):
    """A billing plan orgs subscribe to. Limits are None = unlimited.

    This is Ledger 1 (platform commercial account) in ZST-LE-COM-001's three-ledger
    doctrine — kept strictly separate from the Live Event order ledger in
    models/commercial.py, which has its own versioned CatalogVersion/CatalogLine price book.
    """

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    # NULL = no approved price published for this plan yet, which is NOT the same as free
    # (doc Section 26: no hard-coded fallback price may exist; doc B2: a missing price must
    # fail closed rather than be assumed). Previously `default=0, nullable=False`, which made
    # an unpriced plan indistinguishable from a $0 plan. Every reader must handle None:
    # PlanOut exposes it as optional, the MRR aggregates skip it (SQL SUM ignores NULL), and
    # the UI renders "Not published" instead of a price.
    price_monthly: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # Three distinct commercial states, never collapsed into a number:
    #   price_monthly set                        -> PUBLISHED  (show the price)
    #   price_monthly NULL, custom_pricing False -> NOT_PUBLISHED ("not currently published")
    #   price_monthly NULL, custom_pricing True  -> CUSTOM ("contact sales" / quote required)
    # Defaults False so nothing is silently advertised as bespoke; marking a plan custom is a
    # Commercial decision. See schemas.admin.PlanOut.pricing_state.
    custom_pricing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    max_users: Mapped[int | None] = mapped_column(Integer)
    max_storage_gb: Mapped[int | None] = mapped_column(Integer)
    max_streaming_hours: Mapped[int | None] = mapped_column(Integer)
    features: Mapped[list | None] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="plan")
