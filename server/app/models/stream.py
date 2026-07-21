import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, func, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Stream(Base):

    __tablename__ = "streams"


    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )


    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id"),
        nullable=False
    )


    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False
    )


    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )


    category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )


    thumbnail_url: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )


    stream_key: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False
    )

    livekit_room: Mapped[str | None] = mapped_column(
    String(120),
    unique=True
)


    is_live: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )


    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )


    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )


    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )


    channel = relationship("Channel")