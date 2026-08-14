"""Request/response models for the Super Admin platform API (/admin/*)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ── Generic ────────────────────────────────────────────────────────────────

class Page(BaseModel):
    """Envelope for list endpoints so the client gets totals for pagination."""
    items: list[Any]
    total: int
    page: int
    page_size: int


class SeriesPoint(BaseModel):
    label: str
    value: float


# ── Organizations ────────────────────────────────────────────────────────────

class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    domain: str | None = Field(None, max_length=255)
    region: str | None = Field(None, max_length=40)
    status: str = "active"
    plan_slug: str | None = None  # optional: create a trial subscription to this plan


class OrgUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    domain: str | None = Field(None, max_length=255)
    region: str | None = Field(None, max_length=40)
    status: str | None = None
    storage_used_gb: float | None = None
    bandwidth_gb: float | None = None
    plan_slug: str | None = None  # switch the org's active subscription plan


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    domain: str | None = None
    status: str
    region: str | None = None
    storage_used_gb: float
    bandwidth_gb: float
    created_at: datetime | None = None
    # Computed / joined:
    users_count: int = 0
    events_count: int = 0
    plan: str | None = None
    subscription_status: str | None = None


# ── Users ──────────────────────────────────────────────────────────────────

class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: EmailStr
    username: str
    role: str
    is_active: bool
    org_id: uuid.UUID
    organization_name: str | None = None
    created_at: datetime | None = None
    # Commercial RBAC (ZST-LE-COM-001 Section 25) — meaningful only when role=='super_admin'.
    staff_commercial_role: str | None = None


class UserUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    full_name: str | None = Field(None, min_length=1, max_length=120)
    # One of models.user.STAFF_COMMERCIAL_ROLES, or "" to clear it back to unscoped
    # full-access super_admin. Validated against role in routers/admin.update_user (needs
    # the target user's row, not just this payload, to check "role is/stays super_admin").
    staff_commercial_role: str | None = None


# ── Plans & Subscriptions ────────────────────────────────────────────────────

class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    price_monthly: float
    currency: str
    max_users: int | None = None
    max_storage_gb: int | None = None
    max_streaming_hours: int | None = None
    features: list | None = None
    is_active: bool


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    organization_name: str | None = None
    plan: str | None = None
    price_monthly: float | None = None
    status: str
    seats: int
    started_at: datetime | None = None
    current_period_end: datetime | None = None
    trial_ends_at: datetime | None = None


class SubscriptionUpdate(BaseModel):
    status: str | None = None
    seats: int | None = Field(None, ge=1)
    plan_slug: str | None = None
    current_period_end: datetime | None = None


# ── Audit logs ───────────────────────────────────────────────────────────────

class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_email: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    org_id: uuid.UUID | None = None
    meta: dict | None = None
    ip: str | None = None
    created_at: datetime | None = None


# ── Settings ─────────────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    """Partial patch of platform settings, keyed by setting name -> arbitrary JSON blob."""
    values: dict[str, Any]


# ── Feature flags ────────────────────────────────────────────────────────────

class FeatureFlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    description: str | None = None
    enabled: bool
    updated_at: datetime | None = None
    updated_by: str | None = None


class FeatureFlagCreate(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    enabled: bool = False


class FeatureFlagUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    enabled: bool | None = None


# ── Release center ───────────────────────────────────────────────────────────

class ReleaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: str
    title: str
    notes: str | None = None
    channel: str
    released_by: str | None = None
    released_at: datetime | None = None


class ReleaseCreate(BaseModel):
    version: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    notes: str | None = None
    channel: str = "production"


# ── Support tickets ──────────────────────────────────────────────────────────

class SupportTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    organization_name: str | None = None
    subject: str
    message: str
    status: str
    priority: str
    requester_email: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    resolved_at: datetime | None = None


class SupportTicketCreate(BaseModel):
    org_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1)
    priority: str = "normal"
    requester_email: EmailStr | None = None


class SupportTicketUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None


# ── Incidents (Trust & Safety console) ───────────────────────────────────────
# Same `incidents` table the Command Center's own Incidents panel and action queues
# already read (services/ops.py) — these are the write side that table never had.

class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ref: str
    title: str
    detail: str | None = None
    severity: str
    kind: str
    stage: str | None = None
    region: str | None = None
    org_id: uuid.UUID | None = None
    organization_name: str | None = None
    status: str
    commander: str | None = None
    started_at: datetime | None = None
    resolved_at: datetime | None = None


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    detail: str | None = None
    severity: str = "sev3"
    kind: str = "security"
    org_id: uuid.UUID | None = None
    commander: str | None = None


class IncidentUpdate(BaseModel):
    status: str | None = None
    severity: str | None = None
    commander: str | None = None


# ── Governance records (Governance console) ──────────────────────────────────
# One generic obligation-tracking table already read by services/ops.py for two kinds
# ("single_path_override", "break_glass") — this is the write side + the other kinds
# (dpia, legal_hold, privacy_request, access_review, exception, obligation) the Governance
# console needs. `kind` is free-text on the model; GOVERNANCE_KINDS below is this API's own
# closed list, not a DB constraint, so the two ops.py-owned kinds keep working unmodified.

GOVERNANCE_KINDS = (
    "dpia", "legal_hold", "privacy_request", "access_review", "exception", "obligation",
    "single_path_override", "break_glass",
)


class GovernanceRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    org_id: uuid.UUID | None = None
    organization_name: str | None = None
    event_id: uuid.UUID | None = None
    status: str
    detail: str | None = None
    opened_at: datetime | None = None
    due_at: datetime | None = None
    resolved_at: datetime | None = None


class GovernanceRecordCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=30)
    org_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    detail: str | None = Field(None, max_length=300)
    due_at: datetime | None = None


class GovernanceRecordUpdate(BaseModel):
    status: str | None = None
    detail: str | None = Field(None, max_length=300)
    due_at: datetime | None = None


# ── Developer / API keys ─────────────────────────────────────────────────────

class ApiKeyOut(BaseModel):
    id: str
    label: str
    prefix: str
    created_at: datetime
    # None = non-expiring. Keys minted before expiry existed keep None rather than being
    # retroactively given a deadline.
    expires_at: datetime | None = None
    revoked: bool = False


class ApiKeyCreated(ApiKeyOut):
    """Returned once, at creation time — the only moment the raw key is visible."""
    key: str


class ApiKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    # Optional lifetime. Omit for a non-expiring key.
    expires_in_days: int | None = Field(None, ge=1, le=730)


# ── Command Center ───────────────────────────────────────────────────────────
# The console payloads are deep, heterogeneous and assembled in services/ops.py, so they
# are returned as-is rather than mirrored into a parallel model tree that would have to be
# edited twice on every change. Only the REQUEST bodies are modelled — those are the ones
# that need validating.

class ElevationRequest(BaseModel):
    """Step-up privilege request. `minutes` is capped so an elevation can't be permanent."""
    scope: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=3, max_length=300)
    minutes: int = Field(15, ge=1, le=240)
