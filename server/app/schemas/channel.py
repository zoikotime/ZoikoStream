from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChannelCreate(BaseModel):
    name: str
    slug: str
    description: str | None = None
    category: str | None = None


class ChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    slug: str
    description: str | None
    avatar_url: str | None
    banner_url: str | None
    category: str | None
    created_at: datetime