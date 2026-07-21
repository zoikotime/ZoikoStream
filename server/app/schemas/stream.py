from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StreamCreate(BaseModel):
    channel_id: UUID
    title: str
    description: str | None = None
    category: str | None = None


class StreamUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    thumbnail_url: str | None = None


class StreamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel_id: UUID
    title: str
    description: str | None
    category: str | None
    thumbnail_url: str | None

    stream_key: str

    is_live: bool

    started_at: datetime | None
    ended_at: datetime | None

    created_at: datetime