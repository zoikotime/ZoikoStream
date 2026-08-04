"""Request/response models for the Events API (/events/*). Reuses Page + AdminUserOut
from schemas.admin for listings and assignee output. Time and status-transition rules are
enforced in the router (they need merged/prior values), not duplicated here."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

Visibility = Literal["public", "private", "unlisted", "invite_only"]
CreateStatus = Literal["draft", "published", "scheduled"]  # other states only via transitions
EventStatus = Literal[
    "draft", "published", "scheduled", "live", "paused", "ended", "cancelled", "archived"
]
StreamQuality = Literal["720p", "1080p", "2k", "4k"]
# Mirrors models.event.ASSIGNMENT_ROLES. Declared as a Literal so an unknown role is a 422
# from the framework rather than a hand-written check in every handler.
TeamRole = Literal["host", "moderator", "speaker", "producer", "cohost", "panelist"]

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
    location: str | None = Field(None, max_length=200)
    start_time: datetime | None = None
    end_time: datetime | None = None
    registration_limit: int | None = Field(None, ge=0)
    max_participants: int | None = Field(None, ge=1, le=1_000_000)


class EventCreate(_EventBase):
    visibility: Visibility = "public"
    registration_required: bool = False
    captions_enabled: bool = False
    translation_enabled: bool = False
    waiting_room_enabled: bool = False
    recording_enabled: bool = False
    chat_enabled: bool = True
    qa_enabled: bool = True
    polls_enabled: bool = False
    raise_hand_enabled: bool = True
    allow_screen_share: bool = True
    auto_start_recording: bool = False
    auto_end_event: bool = False
    replay_enabled: bool = False
    stream_quality: StreamQuality = "1080p"
    status: CreateStatus = "draft"
    # Write-only. Hashed by the router before it reaches the model; the hash is never
    # serialized back out (see EventOut, which exposes only `password_protected`).
    access_password: str | None = Field(None, min_length=4, max_length=128)


class EventUpdate(_EventBase):
    visibility: Visibility | None = None
    registration_required: bool | None = None
    captions_enabled: bool | None = None
    translation_enabled: bool | None = None
    waiting_room_enabled: bool | None = None
    recording_enabled: bool | None = None
    chat_enabled: bool | None = None
    qa_enabled: bool | None = None
    polls_enabled: bool | None = None
    raise_hand_enabled: bool | None = None
    allow_screen_share: bool | None = None
    auto_start_recording: bool | None = None
    auto_end_event: bool | None = None
    replay_enabled: bool | None = None
    stream_quality: StreamQuality | None = None
    status: EventStatus | None = None
    # "" (empty string) clears the password; a value sets it; omitting the field leaves it
    # untouched. Three distinct intents need three distinct wire states, which is why this
    # is not a bare `str | None`.
    access_password: str | None = Field(None, max_length=128)


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
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    visibility: str
    registration_required: bool
    registration_limit: int | None = None
    captions_enabled: bool
    translation_enabled: bool
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
    # New in the event-management module; defaulted so existing constructors (and rows
    # created before the ALTER ran) still validate.
    replay_enabled: bool = False
    stream_quality: str = "1080p"
    max_participants: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    # ── Dashboard enrichment ──────────────────────────────────────────────────
    # Populated by the router from crud.summarize/actor_names (set-based queries), NOT by
    # the ORM — model_validate leaves them at their defaults, so any caller that skips the
    # enrichment step gets honest nulls instead of wrong numbers.
    organization_name: str | None = None
    created_by_name: str | None = None
    team_counts: dict[str, int] = Field(default_factory=dict)
    current_viewers: int | None = None
    has_recording: bool = False
    has_replay: bool = False
    access_link_count: int = 0
    # Moderation queue, for the moderator dashboard's "what needs me right now" columns.
    # raised_hands / waiting come from the newest analytics sample (so ~15s old, and None until
    # one exists — an unstarted event has no lobby to measure); the two counts are live rows.
    raised_hands: int | None = None
    waiting: int | None = None
    open_questions: int = 0
    live_polls: int = 0
    # There is no registrations table in this platform, so this is ALWAYS None. It exists so
    # the console can render "not collected" rather than a zero that reads as "nobody
    # signed up". See services/viewer.py — registration is stored config, not enforced.
    registrations: int | None = None

    @computed_field
    @property
    def duration_minutes(self) -> int | None:
        if self.start_time and self.end_time:
            return int((self.end_time - self.start_time).total_seconds() // 60)
        return None

    @computed_field
    @property
    def password_protected(self) -> bool:
        """Whether a passphrase is set — never the hash itself."""
        return bool(self.access_password_hash)

    # Excluded from the response by the router's response_model; carried here only so the
    # computed field above can read it off the ORM object.
    access_password_hash: str | None = Field(None, exclude=True)


class AssignmentUpdate(BaseModel):
    """Replace the full set of assignees for one event role. All must be org members."""
    user_ids: list[uuid.UUID]


class AssignmentSingle(BaseModel):
    """Add or remove ONE assignee, for the incremental path the team panel uses."""
    user_id: uuid.UUID


class DuplicateIn(BaseModel):
    title: str | None = Field(None, max_length=200)
    copy_team: bool = True


# Lifecycle verbs the console exposes, mapped to target statuses in the router. Verbs rather
# than raw statuses because "unpublish" and "archive" are decisions, and a bulk request that
# named statuses directly would let a client invent a transition the UI never offers.
BulkActionName = Literal[
    "publish", "unpublish", "schedule", "cancel", "archive", "delete", "end",
]


class BulkAction(BaseModel):
    action: BulkActionName
    # Capped so one request cannot ask for an unbounded transaction — the same 100-row
    # ceiling services.moderation._chat_bulk uses.
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)


class BulkResult(BaseModel):
    """Per-id outcome. A partial failure is reported, never swallowed: bulk-publishing 20
    events where 3 have no title must say which 3 and why."""
    succeeded: list[uuid.UUID] = Field(default_factory=list)
    failed: list[dict] = Field(default_factory=list)   # [{id, reason}]


class TeamMember(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str | None = None
    email: str
    role: str            # the member's ORG role, not their event role
    is_active: bool = True


class TeamOut(BaseModel):
    """All six event roles in one payload — replaces six round trips on the detail page."""
    host: list[TeamMember] = Field(default_factory=list)
    moderator: list[TeamMember] = Field(default_factory=list)
    speaker: list[TeamMember] = Field(default_factory=list)
    producer: list[TeamMember] = Field(default_factory=list)
    cohost: list[TeamMember] = Field(default_factory=list)
    panelist: list[TeamMember] = Field(default_factory=list)


class AccessLinkCreate(BaseModel):
    label: str | None = Field(None, max_length=120)
    # None = never expires. Bounded at a year so a "permanent" link is a deliberate null
    # rather than an accidental 3650.
    expires_in_days: int | None = Field(7, ge=1, le=365)


class AccessLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    uses: int = 0
    last_used_at: datetime | None = None
    created_at: datetime | None = None
    # Present ONLY in the create/rotate response — the raw token is never stored and never
    # re-served, exactly like an org invitation token.
    token: str | None = None
    url: str | None = None
