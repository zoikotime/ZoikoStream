from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PlatformSetting(Base):
    """Key-value config rows (key in {"brand", "storage_limits", "streaming_limits",
    "global"}), read/written wholesale by GET/PATCH /admin/settings."""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
