from uuid import UUID
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class StreamCreate(BaseModel):
    # channel_id is optional — omit it and the org's default channel is used
    # (created automatically the first time an org creates an event).
    channel_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    category: str | None = None
    thumbnail_url: str | None = None
    host_id: UUID | None = None
    moderator_id: UUID | None = None
    visibility: str = Field("public", pattern=r"^(public|private|unlisted)$")
    registration_required: bool = False
    scheduled_date: date | None = None
    start_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    end_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    timezone: str = "UTC"
    # "draft" holds it back from the org's published events list; "scheduled" publishes it.
    status: str = Field("draft", pattern=r"^(draft|scheduled)$")


class StreamUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    thumbnail_url: str | None = None
    host_id: UUID | None = None
    moderator_id: UUID | None = None
    visibility: str | None = Field(None, pattern=r"^(public|private|unlisted)$")
    registration_required: bool | None = None
    scheduled_date: date | None = None
    start_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    end_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    timezone: str | None = None
    status: str | None = Field(None, pattern=r"^(draft|scheduled|canceled)$")


class StreamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel_id: UUID
    org_id: UUID
    host_id: UUID | None
    moderator_id: UUID | None
    title: str
    description: str | None
    category: str | None
    thumbnail_url: str | None

    stream_key: str

    is_live: bool
    status: str
    visibility: str
    registration_required: bool

    scheduled_date: date | None
    start_time: str | None
    end_time: str | None
    timezone: str

    started_at: datetime | None
    ended_at: datetime | None

    created_at: datetime