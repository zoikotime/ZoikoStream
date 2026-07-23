import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Text, DateTime, Date, ForeignKey, func, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .user import User

# draft -> scheduled -> live -> completed (or draft/scheduled -> canceled)
STREAM_STATUSES = ("draft", "scheduled", "live", "completed", "canceled")
STREAM_VISIBILITIES = ("public", "private", "unlisted")


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

    # Denormalized for cheap org-scoped listing/authorization without joining through
    # channels -> users every time (events are managed by any org_admin/host in the org,
    # not just the channel's specific owner).
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )

    host_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    moderator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


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

    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), default="public", nullable=False)
    registration_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    scheduled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM", local to `timezone`
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)


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

    def visible_to(self, user: "User | None") -> bool:
        """Whether `user` (None = anonymous/guest) may read this event.

        Draft events and private events are org-only; everything else (public/unlisted,
        any non-draft status) is readable by anyone, including guests -- that's what
        shareable event links and the public watch page rely on.
        """
        if self.status == "draft" or self.visibility == "private":
            return user is not None and user.org_id == self.org_id
        return True