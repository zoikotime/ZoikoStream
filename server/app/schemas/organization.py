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

# Mirrors models.event.ASSIGNMENT_ROLES. A Literal so an unknown role is a framework 422
# rather than a string written straight into the column.
EventRole = Literal["host", "moderator", "speaker", "producer", "cohost", "panelist"]


class InvitationCreate(BaseModel):
    """One shape for both invitation kinds. `event_id` present = an event invitation, which
    also creates an EventAssignment on accept."""
    email: EmailStr
    # Platform role. Optional for an event invitation: the server derives a conservative
    # default from event_role rather than letting the choice of "cohost" silently inflate it.
    role: str | None = None
    event_id: uuid.UUID | None = None
    event_role: EventRole | None = None
    message: str | None = Field(None, max_length=1000)
    # False = "manual invitation": create the row, send no email, hand the link over out of
    # band. sent_at stays NULL, which is what distinguishes the two.
    send_email: bool = True


class InvitationBulkCreate(BaseModel):
    """Same settings applied to many addresses. Capped so one request cannot ask for an
    unbounded transaction — the same 100 ceiling the events bulk endpoint uses."""
    emails: list[EmailStr] = Field(..., min_length=1, max_length=100)
    role: str | None = None
    event_id: uuid.UUID | None = None
    event_role: EventRole | None = None
    message: str | None = Field(None, max_length=1000)
    send_email: bool = True


class InvitationBulkAction(BaseModel):
    action: Literal["resend", "cancel", "delete"]
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)


class InvitationBulkResult(BaseModel):
    """Per-id outcome, never all-or-nothing: bulk-resending 20 invitations where 3 were
    already accepted must say WHICH 3 and why."""
    succeeded: list[uuid.UUID] = Field(default_factory=list)
    failed: list[dict] = Field(default_factory=list)   # [{id|email, reason}]


class InvitationOut(BaseModel):
    """Admin-facing invitation view. `invite_token`/`invite_url` are populated ONLY in the
    create + resend responses (delivered once); they are null everywhere else. token_hash is
    never exposed."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: str
    status: str
    # "Declined" for the stored `rejected`, resolved server-side so no client re-derives the
    # vocabulary from string literals.
    status_label: str
    invited_by: str | None = None       # inviter display name
    invited_by_email: str | None = None  # admin surface only; omitted from the public preview
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime | None = None

    # Event scope
    event_id: uuid.UUID | None = None
    event_title: str | None = None
    event_role: str | None = None
    message: str | None = None

    # Delivery, reported as facts. `delivered` is only ever true when the Resend webhook has
    # confirmed it — absent a configured webhook it stays false rather than being assumed.
    sent_at: datetime | None = None
    last_sent_at: datetime | None = None
    delivered_at: datetime | None = None
    send_error: str | None = None
    send_attempts: int = 0
    resend_count: int = 0
    delivery: str = "not_sent"   # not_sent | sent | delivered | failed

    # Which buttons the console may show. Computed from the transition table, so the client
    # never re-implements the state machine.
    can_resend: bool = False
    can_cancel: bool = False
    can_revoke: bool = False

    declined_at: datetime | None = None
    revoked_at: datetime | None = None

    invite_token: str | None = None
    invite_url: str | None = None


class InvitationAction(BaseModel):
    """Admin lifecycle actions on an existing invitation.

    `expire` is deliberately ABSENT: expiry is a fact about the clock, not an admin verb, and
    an admin-triggered expire was a laundering path (expire a declined row, then resend it).
    Accept/decline are the invitee's, via the public token endpoints.
    """
    action: Literal["resend", "cancel", "revoke"]


class InvitationAccept(BaseModel):
    token: str = Field(min_length=16)
    # Both optional: an EXISTING account supplies neither (its credentials are untouched —
    # see the router), a brand-new account must supply both. The router enforces which.
    full_name: str | None = Field(None, min_length=1, max_length=120)
    username: str | None = Field(None, min_length=3, max_length=60, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str | None = Field(None, min_length=8, max_length=72)  # bcrypt caps at 72 bytes


class InvitationReject(BaseModel):
    token: str = Field(min_length=16)


class InvitationPreview(BaseModel):
    """What the invitee is shown BEFORE deciding — the only public read of an invitation.

    A narrow projection on purpose. It must not carry the invitation id, the token hash, the
    inviter's email address, the org id/slug, or any event field beyond title and start time
    (never EventOut, which carries the organizer's operational config).
    """
    org_name: str
    org_logo_url: str | None = None
    inviter_name: str | None = None
    role_label: str
    event_title: str | None = None
    event_starts_at: datetime | None = None
    event_role_label: str | None = None
    message: str | None = None
    expires_at: datetime
    # Masked so the page can confirm WHICH address was invited without printing it in full
    # for anyone looking over the invitee's shoulder.
    email_hint: str
    # True = brand-new account, so the page collects a name and password. False = the address
    # already has an account, so the page sends them to sign in instead.
    needs_account: bool


class InvitationAcceptResult(BaseModel):
    """Accept returns a session ONLY for a brand-new account. For an address that already has
    credentials, `login_required` is true and no token is issued — an emailed link must never
    be exchangeable for a session on an existing account."""
    login_required: bool = False
    access_token: str | None = None
    user: dict | None = None
    event_id: uuid.UUID | None = None
    message: str
