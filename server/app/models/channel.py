import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .user import User

class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    slug: Mapped[str] = mapped_column(
        String(120),
        unique=True,
        index=True,
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(Text)

    avatar_url: Mapped[str | None] = mapped_column(String(255))

    banner_url: Mapped[str | None] = mapped_column(String(255))

    category: Mapped[str | None] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    owner: Mapped["User"] = relationship(
        back_populates="channels"
    )