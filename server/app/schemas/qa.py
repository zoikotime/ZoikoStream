import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class QaAskIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class QaQuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stream_id: uuid.UUID
    user_id: uuid.UUID | None
    display_name: str
    text: str
    votes: int
    answered: bool
    created_at: datetime
    # Whether the requesting viewer has already upvoted this question -- only populated
    # by endpoints/socket events that know the caller's voter_key (list is public and
    # anonymous, so it's always False there).
    voted_by_me: bool = False
