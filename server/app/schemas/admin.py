import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.organization import ORG_STATUSES
from app.models.subscription import SUBSCRIPTION_STATUSES


# ── Organizations ─────────────────────────────────────────────────────────────────
class OrgCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    domain: str | None = Field(None, max_length=120)
    region: str | None = Field(None, max_length=60)
    status: str = Field("trial", pattern=r"^(" + "|".join(ORG_STATUSES) + ")$")
    plan_slug: str | None = None


class OrgUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    domain: str | None = None
    region: str | None = None
    status: str | None = Field(None, pattern=r"^(" + "|".join(ORG_STATUSES) + ")$")
    plan_slug: str | None = None


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    domain: str | None
    region: str | None
    status: str
    plan: str | None = None
    subscription_status: str | None = None
    users_count: int
    events_count: int
    bandwidth_gb: float
    storage_used_gb: float
    created_at: datetime


class OrgAdminOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: EmailStr
    role: str
    is_active: bool


class OrgDetailOut(OrgOut):
    admins: list[OrgAdminOut]


# ── Users ─────────────────────────────────────────────────────────────────────────
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
    created_at: datetime


class UserUpdateIn(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=120)
    role: str | None = Field(None, pattern=r"^(super_admin|org_admin|host|moderator|speaker|viewer)$")
    is_active: bool | None = None


# ── Events (platform-wide, read-only) ───────────────────────────────────────────────
class AdminEventOut(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    visibility: str
    scheduled_date: str | None = None
    org_id: uuid.UUID
    organization_name: str | None = None
    host_name: str | None = None
    created_at: datetime


# ── Plans ─────────────────────────────────────────────────────────────────────────
class PlanOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    price_monthly: float
    currency: str
    max_users: int | None
    max_storage_gb: int | None
    max_streaming_hours: int | None


# ── Subscriptions ─────────────────────────────────────────────────────────────────
class SubscriptionOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    organization_name: str | None = None
    plan: str | None = None
    price_monthly: float | None = None
    status: str
    seats: int
    started_at: datetime
    current_period_end: datetime | None = None


class SubscriptionUpdateIn(BaseModel):
    status: str | None = Field(None, pattern=r"^(" + "|".join(SUBSCRIPTION_STATUSES) + ")$")
    seats: int | None = Field(None, ge=1)
    plan_slug: str | None = None
    current_period_end: datetime | None = None


# ── Feature flags ─────────────────────────────────────────────────────────────────
class FeatureFlagOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    enabled: bool
    created_at: datetime


class FeatureFlagCreateIn(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    enabled: bool = False


class FeatureFlagUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None


# ── Audit logs ────────────────────────────────────────────────────────────────────
class AuditLogOut(BaseModel):
    id: uuid.UUID
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    meta: dict | None
    created_at: datetime


# ── Releases ──────────────────────────────────────────────────────────────────────
class ReleaseOut(BaseModel):
    id: uuid.UUID
    version: str
    title: str
    notes: str | None
    channel: str
    released_by: str | None
    released_at: datetime


class ReleaseCreateIn(BaseModel):
    version: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    notes: str | None = None
    channel: str = Field("production", pattern=r"^(production|staging|beta)$")


# ── Support tickets ───────────────────────────────────────────────────────────────
class SupportTicketOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    organization_name: str | None = None
    subject: str
    message: str
    priority: str
    status: str
    requester_email: str | None
    created_at: datetime


class SupportTicketCreateIn(BaseModel):
    org_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1)
    priority: str = Field("normal", pattern=r"^(low|normal|high|urgent)$")
    requester_email: EmailStr | None = None


class SupportTicketUpdateIn(BaseModel):
    status: str = Field(pattern=r"^(open|in_progress|resolved|closed)$")


# ── API keys ──────────────────────────────────────────────────────────────────────
class ApiKeyOut(BaseModel):
    id: uuid.UUID
    label: str
    prefix: str
    revoked: bool
    created_at: datetime


class ApiKeyCreateIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)


class ApiKeyCreateOut(ApiKeyOut):
    key: str  # only ever present on this one response


# ── Roles (static reference) ─────────────────────────────────────────────────────
class RoleOut(BaseModel):
    role: str
    rank: int
    label: str
    description: str
    capabilities: list[str]


# ── Platform settings ─────────────────────────────────────────────────────────────
class PlatformSettingsUpdateIn(BaseModel):
    values: dict[str, dict]


# ── Platform health ───────────────────────────────────────────────────────────────
class ServiceHealthOut(BaseModel):
    id: str
    name: str
    note: str
    status: str  # ok | warn | down | not_configured
    latency_ms: int | None = None


class PlatformHealthOut(BaseModel):
    overall: str  # ok | warn | down
    services: list[ServiceHealthOut]


# ── Live events ───────────────────────────────────────────────────────────────────
class LiveEventOut(BaseModel):
    id: uuid.UUID
    organization: str | None
    title: str
    channel: str | None
    region: str | None = None
    started_at: datetime | None
    ended_at: datetime | None = None
    viewers: int | None = None
    health: str | None = None
    bitrate_kbps: int | None = None
