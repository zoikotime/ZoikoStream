"""Request/response models for the Events API (/events/*). Reuses Page + AdminUserOut
from schemas.admin for listings and assignee output. Time and status-transition rules are
enforced in the router (they need merged/prior values), not duplicated here."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

Visibility = Literal["public", "private", "unlisted"]
CreateStatus = Literal["draft", "published", "scheduled"]  # other states only via transitions
EventStatus = Literal[
    "draft", "published", "scheduled", "rehearsal", "ready_to_arm", "armed", "live",
    "degraded", "ending", "processing", "replay_ready", "ended", "cancelled", "archived", "blocked",
]

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
    expected_audience: int | None = Field(None, ge=0)


class EventCreate(_EventBase):
    visibility: Visibility = "public"
    registration_required: bool = False
    waiting_room_enabled: bool = False
    recording_enabled: bool = False
    chat_enabled: bool = False
    qa_enabled: bool = False
    polls_enabled: bool = False
    raise_hand_enabled: bool = True
    allow_screen_share: bool = True
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
    expected_audience: int | None = None
    waiting_room_enabled: bool
    recording_enabled: bool
    chat_enabled: bool
    qa_enabled: bool
    polls_enabled: bool
    raise_hand_enabled: bool
    allow_screen_share: bool
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
    end_time: datetime | None = None
    category: str | None = None
    organization_name: str | None = None
    host_name: str | None = None
    chat_enabled: bool
    qa_enabled: bool
    polls_enabled: bool
    raise_hand_enabled: bool = True
    # No persisted Event column (it's a live-only BroadcastSession setting — see
    # services/broadcast.py's DEFAULT_SETTINGS) — True except for a memorial-category
    # event, computed the same way _seed_settings computes it (crud.event.
    # is_memorial_category), so the player never shows a reaction bar it can't use.
    reactions_enabled: bool = True
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
    # Derived from Event.status (which the live-streaming audit's fix now actually persists
    # as "degraded" when the sampler/webhook detects the producer's media has dropped, see
    # services/broadcast.py mark_degraded/mark_recovered) — a real, DB-backed liveness signal
    # for the player, distinct from `status` (kept as-is for backward compatibility with
    # anything already reading it). Deliberately not a live LiveKit API call on every viewer
    # request — see routers/events.py::watch_event for why.
    media_status: str = "live"


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


class FeedbackOut(BaseModel):
    """One feedback submission. Feedback is viewer-only (see moderation._feedback_submit)
    — `role` is kept for backward compatibility with existing rows, but every reader
    (the host dashboard and the organizer's event page alike) now filters this to
    role="viewer"."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    name: str | None = None
    rating: int | None = None
    comment: str | None = None
    created_at: datetime | None = None


class ContributorInvite(BaseModel):
    """The invitation half of a contributor's backstage session — the runtime half
    (state, consent, preflight) is server-owned and never set from the wire (see
    ContributorSessionOut, and services/contributor.py's socket actions)."""
    join_window_start: datetime | None = None
    join_window_end: datetime | None = None
    expires_at: datetime | None = None
    contribution_method: str = Field("livekit_browser", max_length=20)
    consent_notice: str | None = Field(None, max_length=4000)
    support_contact: str | None = Field(None, max_length=300)


class ContributorSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    state: str
    invited_at: datetime | None = None
    invited_by: uuid.UUID | None = None
    join_window_start: datetime | None = None
    join_window_end: datetime | None = None
    expires_at: datetime | None = None
    contribution_method: str
    consent_notice: str | None = None
    support_contact: str | None = None
    consent_given: bool
    consent_at: datetime | None = None
    preflight_result: dict | None = None
    rehearsal_complete: bool
    rehearsal_at: datetime | None = None
    admitted_at: datetime | None = None
    brought_live_at: datetime | None = None
    removed_at: datetime | None = None
    removed_reason: str | None = None


class AccessLinkCreate(BaseModel):
    label: str | None = Field(None, max_length=120)
    expires_in_days: int | None = Field(None, ge=1, le=3650)


class AccessLinkOut(BaseModel):
    """List/row view — no token or URL, since the raw value is never recoverable after
    the one-time reveal (see AccessLinkIssued)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    uses: int
    last_used_at: datetime | None = None
    created_at: datetime | None = None


class AccessLinkIssued(AccessLinkOut):
    """Returned only from create/rotate — the one moment the raw link exists outside the
    recipient's hands."""
    url: str


class ViewerInvite(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr


class ViewerInviteCreate(BaseModel):
    invites: list[ViewerInvite] = Field(..., min_length=1, max_length=100)


# -- Event intake / planning / rehearsal (ZST-EC-001 LVE-002 / LVE-004 / LVE-005) --------

class IntakeOpen(BaseModel):
    owner_id: uuid.UUID | None = None
    due_at: datetime | None = None


class IntakeReopen(BaseModel):
    sections: list[str]
    reason: str | None = Field(None, max_length=300)
    due_at: datetime | None = None


class PlanningUpdate(BaseModel):
    status: str | None = None
    assigned_to: uuid.UUID | None = None
    due_at: datetime | None = None
    blocking: bool | None = None
    outstanding: list[str] | None = None


class RehearsalCreate(BaseModel):
    scheduled_at: datetime
    purpose: str | None = Field(None, max_length=300)


class RehearsalOutcome(BaseModel):
    validated: list[str] = []
    outstanding: list[str] = []
    repeat_required: bool = False
    repeat_reason: str | None = Field(None, max_length=300)
    next_rehearsal_at: datetime | None = None
    outcome: str | None = None


# -- Event brief / incidents (ZST-EC-001 LVE-007 / LVE-010) ------------------------------

class IncidentStateIn(BaseModel):
    state: str
    reason_category: str
    summary: str | None = Field(None, max_length=300)
    # Only set when an operator genuinely commits to a time. Absent means the message makes
    # no next-update promise at all.
    next_update_at: datetime | None = None


class IncidentCancelIn(BaseModel):
    reason_category: str
    summary: str | None = Field(None, max_length=300)


# -- Event contributors (ZST-EC-001 CON-001 .. CON-005) -----------------------------------

class ContributorInvite(BaseModel):
    # EMAIL, not a user id: an external contributor has no platform account.
    email: EmailStr
    role: str
    display_name: str | None = Field(None, max_length=200)
    expires_at: datetime | None = None
    technical_check_required: bool = True
    rehearsal_required: bool = False


class ContributorRevoke(BaseModel):
    reason: str | None = Field(None, max_length=200)


class ContributorAccept(BaseModel):
    token: str = Field(..., min_length=16, max_length=200)


class TechnicalCheckIn(BaseModel):
    """Real browser capability results, same shape as the existing preflight payload."""
    browser_supported: bool = False
    camera_ok: bool = False
    mic_ok: bool = False
    speaker_ok: bool = False
    framing_ok: bool = False
    # Unverified self-report: there is no measured network test in this product.
    network_quality: str | None = Field(None, max_length=40)


class SessionEndIn(BaseModel):
    reason: str
