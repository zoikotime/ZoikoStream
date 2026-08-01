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

class OrgDeveloperOut(BaseModel):
    """API keys and webhook URLs. No management endpoints yet, so these are empty
    until a later phase writes them. list[dict] keeps key shape open until then."""
    api_keys: list[dict] = []
    webhook_urls: list[str] = []


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
