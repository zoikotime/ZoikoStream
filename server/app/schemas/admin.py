"""Request/response models for the Super Admin platform API (/admin/*)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

from ..config import BILLING_INTERVALS

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
    # ZST-EC-001 ORG-010. Coarse, customer-safe category recorded with an operational-state
    # change so the notice can state a reason without exposing enforcement logic. Ignored
    # for edits that do not change state.
    reason_category: str | None = None
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
    # None = no approved price published yet (models/plan.py) — distinct from 0.00.
    price_monthly: float | None = None
    custom_pricing: bool = False
    currency: str
    max_users: int | None = None
    max_storage_gb: int | None = None
    max_streaming_hours: int | None = None
    features: list | None = None
    is_active: bool

    @computed_field
    @property
    def pricing_state(self) -> str:
        """PUBLISHED | CUSTOM | NOT_PUBLISHED — the one place the three states are derived,
        so the console and the Billing page cannot disagree, and so no caller has to infer
        "unpriced" from a null and risk rendering it as 0 (doc Section 26: no invented
        price). This is a PLATFORM subscription price (Ledger 1); it is never a Live Event
        price, which comes only from a published CatalogVersion (Ledger 2)."""
        if self.price_monthly is not None:
            return "PUBLISHED"
        return "CUSTOM" if self.custom_pricing else "NOT_PUBLISHED"


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
    # Deliberately still a plain `str`, not a Literal: the authority for a valid value is the
    # §12 state machine in models/subscription.py, and duplicating the vocabulary here would be
    # two places to keep in sync (same convention as schemas/commercial.py's server-computed
    # workflow states). crud.update_subscription validates the TRANSITION, which a Literal
    # could not do anyway — it would accept `canceled -> active`.
    status: str | None = None
    seats: int | None = Field(None, ge=1)
    plan_slug: str | None = None
    # Optional cadence for an administrative plan change. Omitted keeps the subscription's
    # current interval — support changing the PLAN must not silently also re-cadence the
    # customer. Same closed vocabulary as the customer-facing request; the price for the pair
    # is still resolved server-side from approved configuration.
    billing_interval: str | None = Field(
        None, pattern=f"^({'|'.join(BILLING_INTERVALS)})$")
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

class LegalHoldIn(BaseModel):
    """A hold's actual legal instruction is privileged and has no field here — only a closed
    category and an opaque reference, both of which are safe to render into a notification
    sent to a data-governance mailbox."""

    category: str
    hold_reference: str = Field(..., max_length=80)


class RetentionDecisionIn(BaseModel):
    approve: bool
    # An approver may grant less than was asked for. Null means "grant exactly the request".
    granted_until: datetime | None = None
    note: str | None = Field(None, max_length=2000)


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
    # Non-secret, hash-derived short identifier (ZST-EC-001 DEV-002). Shown in the console
    # AND in the credential-created email so a security administrator can match the two
    # without either surface ever carrying key material.
    fingerprint: str | None = None
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

# ── ORG-009 authorized support access (ZST-EC-001) ──────────────────────────
# Staff-side request bodies. Nothing here can grant access: a request is a request, and a
# session only starts through the approval gate in services/support_access.py.

class SupportAccessCreate(BaseModel):
    """Ask an Organization for scoped, time-limited access."""
    org_id: uuid.UUID
    case_reference: str = Field(min_length=3, max_length=60)
    reason_category: str
    engineer_display: str = Field(min_length=2, max_length=120)
    requested_scope: str = Field(min_length=3, max_length=200)
    allowed_actions: list[str] = Field(min_length=1)
    # Capped in the schema AND clamped in the domain. No open-ended support access.
    minutes: int = Field(60, ge=1, le=480)
    emergency: bool = False
    emergency_reason: str | None = Field(None, max_length=300)


class SupportAccessAmend(BaseModel):
    """Change the terms. Any change invalidates an existing approval."""
    case_reference: str | None = Field(None, min_length=3, max_length=60)
    requested_scope: str | None = Field(None, min_length=3, max_length=200)
    allowed_actions: list[str] | None = None
    minutes: int | None = Field(None, ge=1, le=480)


class SupportAccessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    case_reference: str
    reason_category: str
    engineer_display: str
    requested_scope: str
    requested_minutes: int
    status: str
    requested_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by_email: str | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    ended_at: datetime | None = None
    emergency: bool = False
    post_use_review_at: datetime | None = None

    @computed_field
    @property
    def allowed_action_list(self) -> list[str]:
        return []


class ElevationRequest(BaseModel):
    """Step-up privilege request. `minutes` is capped so an elevation can't be permanent."""
    scope: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=3, max_length=300)
    minutes: int = Field(15, ge=1, le=240)
