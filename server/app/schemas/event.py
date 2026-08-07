"""Request/response models for the Events API (/events/*). Reuses Page + AdminUserOut
from schemas.admin for listings and assignee output. Time and status-transition rules are
enforced in the router (they need merged/prior values), not duplicated here."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

Visibility = Literal["public", "private", "unlisted"]
CreateStatus = Literal["draft", "published", "scheduled"]  # other states only via transitions
EventStatus = Literal["draft", "published", "scheduled", "live", "ended", "cancelled", "archived"]

_SLUG = r"^[a-z0-9][a-z0-9-]*$"


class _EventBase(BaseModel):
    """Shared, all-optional field set for create/update (defaults live on Create only)."""
    title: str | None = Field(None, max_length=200)
    slug: str | None = Field(None, min_length=3, max_length=220, pattern=_SLUG)
    description: str | None = None
    short_description: str | None = Field(None, max_length=300)
    banner_image: str | None = Field(None, max_length=500)
    thumbnail: str | None = Field(None, max_length=500)
    category: str | None = Field(None, max_length=100)
    tags: list[str] | None = None
    language: str | None = Field(None, max_length=40)
    timezone: str | None = Field(None, max_length=60)
    start_time: datetime | None = None
    end_time: datetime | None = None
    registration_limit: int | None = Field(None, ge=0)


class EventCreate(_EventBase):
    visibility: Visibility = "public"
    registration_required: bool = False
    waiting_room_enabled: bool = False
    recording_enabled: bool = False
    chat_enabled: bool = True
    qa_enabled: bool = True
    polls_enabled: bool = False
    raise_hand_enabled: bool = True
    allow_screen_share: bool = True
    auto_start_recording: bool = False
    auto_end_event: bool = False
    status: CreateStatus = "draft"


class EventUpdate(_EventBase):
    visibility: Visibility | None = None
    registration_required: bool | None = None
    waiting_room_enabled: bool | None = None
    recording_enabled: bool | None = None
    chat_enabled: bool | None = None
    qa_enabled: bool | None = None
    polls_enabled: bool | None = None
    raise_hand_enabled: bool | None = None
    allow_screen_share: bool | None = None
    auto_start_recording: bool | None = None
    auto_end_event: bool | None = None
    status: EventStatus | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_by: uuid.UUID
    title: str | None = None
    slug: str | None = None
    description: str | None = None
    short_description: str | None = None
    banner_image: str | None = None
    thumbnail: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    language: str | None = None
    timezone: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    visibility: str
    registration_required: bool
    registration_limit: int | None = None
    waiting_room_enabled: bool
    recording_enabled: bool
    chat_enabled: bool
    qa_enabled: bool
    polls_enabled: bool
    raise_hand_enabled: bool
    allow_screen_share: bool
    auto_start_recording: bool
    auto_end_event: bool
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    @computed_field
    @property
    def duration_minutes(self) -> int | None:
        if self.start_time and self.end_time:
            return int((self.end_time - self.start_time).total_seconds() // 60)
        return None


class AssignmentUpdate(BaseModel):
    """Replace the full set of assignees for one event role. All must be org members."""
    user_ids: list[uuid.UUID]


class WatchOut(BaseModel):
    """Public-facing view for GET /events/{id}/watch — deliberately thin: no org_id,
    no created_by, nothing an anonymous visitor shouldn't see. A subscribe-only LiveKit
    token is included only while the event is actually live (and, if registration is
    required, only once the caller is registered)."""
    id: uuid.UUID
    title: str | None = None
    description: str | None = None
    status: str
    visibility: str
    start_time: datetime | None = None
    organization_name: str | None = None
    host_name: str | None = None
    chat_enabled: bool
    qa_enabled: bool
    polls_enabled: bool
    registration_required: bool = False
    registered: bool = True
    not_started: bool = False
    expired: bool = False
    livekit_url: str | None = None
    livekit_token: str | None = None
    room: str | None = None
    # Populated once the event has ended and a recording actually captured something
    # (enforced=True) — same registration/private-event gate as the live token above.
    recording_url: str | None = None
    recording_duration_seconds: int | None = None


class RegistrationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr


class RegistrationOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    token: str


class RegistrantOut(BaseModel):
    """Admin-facing view of a registrant — no access token in here."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    invited_by: uuid.UUID | None = None
    created_at: datetime | None = None


class ViewerInvite(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr


class ViewerInviteCreate(BaseModel):
    invites: list[ViewerInvite] = Field(..., min_length=1, max_length=100)
