import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatSendIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stream_id: uuid.UUID
    user_id: uuid.UUID | None
    display_name: str
    text: str
    pinned: bool
    flagged: bool
    created_at: datetime
