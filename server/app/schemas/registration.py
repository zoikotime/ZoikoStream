import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegistrantIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str | None = Field(None, max_length=30)
    company: str | None = Field(None, max_length=120)


class BulkRegisterIn(BaseModel):
    registrants: list[RegistrantIn] = Field(min_length=1, max_length=1000)


class RegistrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stream_id: uuid.UUID
    full_name: str
    email: EmailStr
    phone: str | None
    company: str | None
    source: str
    status: str
    created_at: datetime


class BulkRegisterResult(BaseModel):
    created: list[RegistrationOut]
    skipped: list[str]
