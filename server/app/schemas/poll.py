import uuid

from pydantic import BaseModel, ConfigDict, Field


class PollCreateIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    options: list[str] = Field(min_length=2, max_length=8)


class PollOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    votes: int


class PollOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stream_id: uuid.UUID
    question: str
    is_closed: bool
    options: list[PollOptionOut]
    # The option this viewer already voted for, if any -- same caveats as
    # QaQuestionOut.voted_by_me (schemas/qa.py): only known for logged-in/registered
    # callers, never for anonymous guests.
    voted_option_id: uuid.UUID | None = None
