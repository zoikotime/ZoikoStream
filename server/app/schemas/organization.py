"""Request/response models for the org self-service API (/organization/*).

Distinct from schemas/admin.py (that's the cross-org super-admin view with computed
counts/plan). These describe an org managing *itself*. Update schemas use exclude_unset
in the router so only supplied fields are patched.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ── Profile ──────────────────────────────────────────────────────────────────

class OrgProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    slug: str | None = None
    website: str | None = None
    description: str | None = None
    industry: str | None = None
    company_size: str | None = None
    support_email: EmailStr | None = None
    logo_url: str | None = None
    timezone: str | None = None
    country: str | None = None


class OrgProfileUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    slug: str | None = Field(None, min_length=3, max_length=140, pattern=r"^[a-z0-9][a-z0-9-]*$")
    website: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=2000)
    industry: str | None = Field(None, max_length=80)
    company_size: str | None = Field(None, max_length=40)
    support_email: EmailStr | None = None
    logo_url: str | None = Field(None, max_length=500)
    timezone: str | None = Field(None, max_length=60)
    country: str | None = Field(None, max_length=80)


class OrgMeOut(OrgProfileOut):
    """The logged-in organization: profile plus identity/account fields."""
    id: uuid.UUID
    status: str
    domain: str | None = None
    created_at: datetime | None = None


# ── Branding ─────────────────────────────────────────────────────────────────

class OrgBrandingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    theme: str | None = None


class OrgBrandingUpdate(BaseModel):
    logo_url: str | None = Field(None, max_length=500)
    primary_color: str | None = Field(None, max_length=20)
    secondary_color: str | None = Field(None, max_length=20)
    theme: str | None = Field(None, pattern=r"^(light|dark|system)$")


# ── Developer (read-only this phase) ──────────────────────────────────────────

class WebhookEndpointOut(BaseModel):
    """One registered endpoint. Never carries `secret` — see WebhookEndpointCreated for
    the one moment it's visible, and GET .../webhooks/{id}/secret for re-viewing it later
    (models/webhook.py's docstring explains why this secret, unlike an API key, has to
    stay re-viewable)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    label: str | None = None
    events: list[str] = []
    enabled: bool = True
    created_at: datetime


class WebhookEndpointCreated(WebhookEndpointOut):
    secret: str


class WebhookEndpointCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    label: str | None = Field(None, max_length=120)
    events: list[str] = Field(..., min_length=1)


class WebhookEndpointUpdate(BaseModel):
    url: str | None = Field(None, min_length=1, max_length=2000)
    label: str | None = Field(None, max_length=120)
    events: list[str] | None = None
    enabled: bool | None = None


class WebhookSecretOut(BaseModel):
    secret: str


class WebhookDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    status: str
    attempt_count: int
    last_response_code: int | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime


class OrgDeveloperOut(BaseModel):
    """API keys and registered webhook endpoints."""
    api_keys: list[dict] = []
    webhooks: list[WebhookEndpointOut] = []


# ── Notifications ─────────────────────────────────────────────────────────────
# All fields defaulted → GET fills missing keys; PATCH merges only supplied keys.

class OrgNotifications(BaseModel):
    event_scheduled: bool = True
    event_starting: bool = True
    recording_ready: bool = True
    weekly_summary: bool = False
    billing: bool = True
    mentions: bool = True
    member_joined: bool = False
    security_alerts: bool = True


# ── Security ──────────────────────────────────────────────────────────────────

class OrgSecurity(BaseModel):
    require_2fa: bool = False
    enforce_sso: bool = False
    min_password_length: int = Field(8, ge=8, le=64)
    session_timeout: str = "8 hours"
    allowed_domains: str = ""  # comma-separated; empty = no restriction


# ── Domain ────────────────────────────────────────────────────────────────────

class OrgDomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    domain: str | None = None
    domain_verified: bool = False


class OrgDomainUpdate(BaseModel):
    # Setting/changing the domain resets verification (handled in the router).
    domain: str | None = Field(None, max_length=255)


# ── Invitations ───────────────────────────────────────────────────────────────
# User management endpoints reuse Page / AdminUserOut / UserUpdate from schemas.admin.

class InvitationCreate(BaseModel):
    email: EmailStr
    role: str  # validated against the org-assignable set in the router


class InvitationOut(BaseModel):
    """Admin-facing invitation view. `invite_token`/`invite_url` are populated ONLY in the
    create + resend responses (delivered once); they are null everywhere else. token_hash
    is never exposed."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: str
    status: str
    invited_by: str | None = None  # inviter email
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime | None = None
    invite_token: str | None = None
    invite_url: str | None = None


class InvitationAction(BaseModel):
    """Admin lifecycle actions on an existing invitation. Accept/reject are NOT here — the
    invitee performs those via the public token endpoints."""
    action: Literal["resend", "cancel", "expire"]


class InvitationAccept(BaseModel):
    token: str = Field(min_length=16)
    full_name: str = Field(min_length=1, max_length=120)
    username: str | None = Field(None, min_length=3, max_length=60, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=72)  # bcrypt caps at 72 bytes


class InvitationReject(BaseModel):
    token: str = Field(min_length=16)


class InvitationPreview(BaseModel):
    """Public, pre-accept view so the invitee's page can show who invited them before
    asking for a password."""
    email: EmailStr
    role: str
    organization_name: str


# ── Recordings ───────────────────────────────────────────────────────────────

class RecordingOut(BaseModel):
    """One captured recording, org-wide (GET /organization/recordings). `url` is a
    time-limited signed link generated per-request — never persisted, so it can't go
    stale in a cached response."""
    id: uuid.UUID
    event_id: uuid.UUID
    title: str | None = None
    category: str | None = None
    started_at: datetime | None = None
    duration_seconds: int | None = None
    size_bytes: int | None = None
    # True on the recording's own column, or when its event has an open legal-hold
    # governance record (crud.admin.event_under_legal_hold) — either blocks deletion.
    legal_hold: bool = False
    url: str | None = None


# ── Live Inputs (LiveKit Ingress) ─────────────────────────────────────────────

class LiveInputCreate(BaseModel):
    event_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    # "rtmp" | "whip" — no "srt" until the installed livekit-api SDK adds SRT_INPUT
    # (see services/livekit.py's INGRESS_TYPES).
    input_type: str = Field(pattern="^(rtmp|whip)$")


class LiveInputOut(BaseModel):
    """One live input, org-wide (GET /organization/live-inputs). Never carries the raw
    publish key — that's fetched on demand via GET .../live-inputs/{id}/key, and only
    while the detail sheet is open (see LiveIngressEndpoint's model docstring)."""
    id: uuid.UUID
    event_id: uuid.UUID
    event_title: str | None = None
    title: str
    description: str | None = None
    input_type: str
    state: str
    enforced: bool
    error: str | None = None
    created_at: datetime


class LiveInputKeyOut(BaseModel):
    ingest_url: str | None = None
    stream_key: str | None = None
