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


class UserUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    full_name: str | None = Field(None, min_length=1, max_length=120)


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
