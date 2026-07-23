import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("streams.id"), nullable=False, index=True)

    # Null for anonymous/guest viewers -- events can be watched (and chatted in) without
    # an account. display_name is a snapshot at send time so a message still shows a
    # name after a guest leaves or a user is later removed from the org.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)

    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Soft delete: moderators hide messages rather than erase them outright.
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
