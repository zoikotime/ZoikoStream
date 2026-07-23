import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stream_id: uuid.UUID
    status: str
    file_url: str | None
    duration_seconds: int | None
    created_at: datetime
