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


class StreamBase(BaseModel):
    """Everything about a stream that is safe to hand to any authorized reader."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel_id: UUID
    title: str
    description: str | None
    category: str | None
    thumbnail_url: str | None

    is_live: bool

    started_at: datetime | None
    ended_at: datetime | None

    created_at: datetime


class StreamResponse(StreamBase):
    """Single-stream reads, which are owner-scoped. `stream_key` is a PUBLISH credential —
    it belongs only in a response the channel owner asked for by id."""

    stream_key: str


class StreamListItem(StreamBase):
    """List rows deliberately omit `stream_key`: a list is the one shape that fans a
    credential out across many rows at once."""


class StreamListResponse(BaseModel):
    page: int
    limit: int
    total: int
    items: list[StreamListItem]