"""Request/response models for the org self-service API (/organization/*).

Distinct from schemas/admin.py (that's the cross-org super-admin view with computed
counts/plan). These describe an org managing *itself*. Update schemas use exclude_unset
in the router so only supplied fields are patched.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..config import BILLING_INTERVALS, MONTHLY


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


class WebhookVerificationOut(BaseModel):
    """The challenge is returned ONCE, here. Only its hash is stored."""
    endpoint_id: uuid.UUID
    status: str
    challenge: str
    challenge_header: str
    expires_at: datetime | None = None


class DeveloperExportCreate(BaseModel):
    export_type: str


class RetentionExtensionCreate(BaseModel):
    """`reason_category` is a closed list (models.media_governance.RETENTION_REASON_CATEGORIES)
    rather than free text, because it is rendered into a notification sent to data-governance
    recipients — a free-text field there is an invitation to paste confidential detail."""

    reason_category: str
    requested_until: datetime


class RetentionStateOut(BaseModel):
    recording_id: uuid.UUID
    retention_policy_version: str
    retention_expires_at: datetime | None = None
    legal_hold: bool = False
    # Safe hold metadata only. The hold's actual subject matter is never stored, so it
    # cannot be served here.
    hold_reference: str | None = None
    hold_category: str | None = None
    deletion_eligible: bool = True
    deletion_blocked_reason: str | None = None


class WebhookVerifyIn(BaseModel):
    challenge: str = Field(min_length=8, max_length=256)


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


from .admin import ApiKeyOut  # noqa: E402  (shared credential shape)


class OrgDeveloperOut(BaseModel):
    """API keys and registered webhook endpoints.

    `api_keys` is typed as ApiKeyOut, not list[dict]. It used to be the latter, and the
    router's own comment said records were "shaped through ApiKeyOut" so `key_hash` could
    not reach a browser - but list[dict] passes every key through untouched, so the stored
    sha256 verifier for every credential was being returned to the client. Typing the field
    is what actually enforces the redaction the comment describes.
    """
    api_keys: list[ApiKeyOut] = []
    webhooks: list[WebhookEndpointOut] = []


# ── Notifications ─────────────────────────────────────────────────────────────
# All fields defaulted → GET fills missing keys; PATCH merges only supplied keys.

class OrgNotifications(BaseModel):
    """Stored operational preferences.

    Every field is kept for backward compatibility with values already in the database and
    with existing clients, but the server normalizes on read AND on write
    (services/notifications.normalize): mandatory keys are pinned True and keys with no send
    path are pinned False. A client may still POST `security_alerts: false`; it simply has
    no effect, which is the safe way to deprecate a switch that was never honoured.
    """
    event_scheduled: bool = True
    event_starting: bool = False
    recording_ready: bool = False
    weekly_summary: bool = False
    billing: bool = True
    mentions: bool = False
    # Opt-out: ORG-002 sent unconditionally before preferences were enforced.
    member_joined: bool = True
    # Class A. Always true on read and on write; see NotificationCatalogItem.mandatory.
    security_alerts: bool = True


class NotificationCatalogItem(BaseModel):
    """What one preference actually controls, so the UI can stop guessing.

    `mandatory` means no preference can suppress the family. `available` means a send path
    for it exists in this codebase at all — four of the historical eight name emails that
    are never sent, and the settings page must not render those as working switches.
    """
    key: str
    label: str
    description: str
    message_class: str
    mandatory: bool
    configurable: bool
    available: bool
    scope: str
    channel: str
    value: bool


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
    # captured|validating|valid|degraded|failed, or None — nothing in this stack sets this
    # yet (no dual-recording comparison pipeline exists), so it's shown as an honest
    # "Validation pending" label, not used to gate anything.
    validation_status: str | None = None
    url: str | None = None


# ── Live Inputs (LiveKit Ingress) ─────────────────────────────────────────────

# ── ORG-007 / ORG-008 / ORG-009 customer-side governance (ZST-EC-001) ───────

class SupportAccessDecision(BaseModel):
    """An Organization approver's decision on a support-access request."""
    approve: bool


class SupportAccessCustomerOut(BaseModel):
    """What the tenant is allowed to see about a support request.

    Deliberately omits internal engineer ids, elevation ids and reason internals - the
    customer needs to know who was authorized to do what, for how long, on whose approval.
    """
    id: uuid.UUID
    case_reference: str
    engineer_display: str
    requested_scope: str
    allowed_actions: list[str]
    requested_minutes: int
    status: str
    emergency: bool
    requested_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by_email: str | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    ended_at: datetime | None = None


class OwnershipTransferCreate(BaseModel):
    proposed_owner_email: EmailStr


class OwnershipTransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    current_owner_email: str
    proposed_owner_email: str
    expires_at: datetime
    current_owner_confirmed_at: datetime | None = None
    proposed_owner_confirmed_at: datetime | None = None
    completed_at: datetime | None = None
    canceled_at: datetime | None = None


class AccessReviewCreate(BaseModel):
    due_in_days: int = Field(14, ge=1, le=180)
    review_period: str | None = Field(None, max_length=60)


class AccessReviewDecisionIn(BaseModel):
    decision: str            # approved | change_required | remove | exception
    reason: str | None = Field(None, max_length=300)
    exception_owner_email: EmailStr | None = None


class AccessReviewAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_email: str
    member_name: str | None = None
    access_snapshot: str | None = None
    decision: str
    decision_reason: str | None = None
    decided_at: datetime | None = None
    exception_owner_email: str | None = None
    high_risk: bool = False


class AccessReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    review_period: str | None = None
    opened_at: datetime | None = None
    due_at: datetime
    completed_at: datetime | None = None
    outstanding: int = 0


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


# ── Customer export & post-event reports ──────────────────────────────────────
# CustomerDelivery is the shared mechanism for both (models/live.py's docstring explains
# why). Never carries `token_hash`, and the raw token itself only ever appears once, in
# DeliveryCreated below — see EventAccessLink's reveal-once precedent.

class DeliveryCreate(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=200)
    recipient_email: EmailStr
    expires_in_days: int | None = Field(14, ge=1, le=365)


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    recipient_name: str
    recipient_email: str
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    delivered_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int
    created_at: datetime
    # kind="export" only — pending|ready|failed (models.live.WATERMARK_STATUSES).
    watermark_status: str | None = None


class DeliveryCreated(DeliveryOut):
    """Returned once, at creation — the raw /deliveries/{token} URL. Never reconstructable
    afterward, same posture as an API key or an event access link."""
    delivery_url: str


class RecordingExportEligibility(BaseModel):
    eligible: bool
    reason: str | None = None
    validation_status: str | None = None


class EventReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_id: uuid.UUID
    version: int
    data: dict
    created_at: datetime
    released_at: datetime | None = None


class SubscriptionCheckoutCreate(BaseModel):
    """Body for POST /organization/billing/checkout-session.

    Carries ONLY two CANONICAL IDENTIFIERS: which plan, and which published billing cadence.
    No amount, currency, price or Stripe Price ID is accepted — the server resolves the
    approved price itself from operator configuration, so there is no field here through which
    a browser could influence what it is charged.

    `billing_interval` is a closed vocabulary enforced by the pattern below, so a tampered
    value is a 422 at the edge rather than something the price resolver has to defend against.
    It selects BETWEEN approved prices; it can never introduce one.
    """
    plan_slug: str = Field(..., min_length=1, max_length=60)
    billing_interval: str = Field(MONTHLY, pattern=f"^({'|'.join(BILLING_INTERVALS)})$")


# -- Support cases (ZST-EC-001 SUP-001 .. SUP-004) ---------------------------------------
# Deliberately NO org_id field: the organization comes from the caller's own token via
# get_my_org, so there is nothing here a customer could point at another tenant.

class SupportCaseCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)
    category: str
    priority: str = "normal"
    # The authoritative classification feedback eligibility is decided from. Defaults to
    # standard; an operator raises it, and it is never guessed from the wording.
    sensitivity: str = "standard"


class SupportParticipantAdd(BaseModel):
    email: EmailStr
    display_name: str | None = Field(None, max_length=200)


class SupportReopen(BaseModel):
    reason: str | None = Field(None, max_length=1000)


# -- Security contacts and abuse reports (ZST-EC-001 SEC-006 / SEC-004) ------------------

class SecurityContactCreate(BaseModel):
    email: EmailStr
    display_name: str | None = Field(None, max_length=200)


class SecurityContactVerify(BaseModel):
    token: str = Field(..., min_length=16, max_length=200)


class AbuseReportCreate(BaseModel):
    category: str
    subject_type: str
    subject_id: uuid.UUID | None = None
    description: str | None = Field(None, max_length=4000)
    # The organization the report is ABOUT, not the reporter's own.
    org_id: uuid.UUID | None = None
