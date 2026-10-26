"""Request/response contracts for the public Guide API (ZST-API-PUB-GUIDE-001).

Security-sensitive request objects use extra="forbid": an unknown property (e.g. an attempt to
smuggle `org_id` or `operator_mode` into page context) is a 400, never silently accepted
(API §4, API-AT-03).
"""
from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import CONSENT_PURPOSES, DEVICE_CLASSES, PAGE_TYPES, SUPPORT_FLOW_TYPES

PageType = Literal[PAGE_TYPES]  # type: ignore[valid-type]
DeviceClass = Literal[DEVICE_CLASSES]  # type: ignore[valid-type]
EntryPoint = Literal["launcher", "inline", "viewer_help", "contact", "pricing_cta", "other"]
FlowType = Literal[SUPPORT_FLOW_TYPES]  # type: ignore[valid-type]

_LOCALE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
_PAGE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PageContext(Strict):
    page_id: str = Field(max_length=64)
    page_type: PageType
    path: str = Field(max_length=512)
    locale: str = Field(default="en-US", max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    device_class: DeviceClass = "unknown"
    entry_point: EntryPoint = "launcher"
    public_event_context_token: str | None = Field(default=None, max_length=4096)

    @field_validator("page_id")
    @classmethod
    def _page_id(cls, v: str) -> str:
        if not _PAGE_ID.match(v):
            raise ValueError("invalid page_id")
        return v

    @field_validator("path")
    @classmethod
    def _relative_path(cls, v: str) -> str:
        # Relative public path only (API §7.1). Absolute/protocol-relative URLs are rejected,
        # and the query/fragment is dropped: a viewer URL's query may carry an invitation
        # credential, which must never reach Guide storage.
        if not v.startswith("/") or v.startswith("//") or "\\" in v or "://" in v:
            raise ValueError("path must be a relative public path")
        return v.split("?", 1)[0].split("#", 1)[0] or "/"

    @field_validator("locale")
    @classmethod
    def _locale(cls, v: str) -> str:
        if not _LOCALE.match(v):
            raise ValueError("invalid locale")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}$", v):
            raise ValueError("invalid timezone")
        return v


class ConsentSnapshot(Strict):
    analytics: Literal["granted", "denied", "unknown"] = "unknown"
    consent_version: str | None = Field(default=None, max_length=48)


class SessionCreate(Strict):
    client_session_nonce: uuid.UUID
    page_context: PageContext
    consent_snapshot: ConsentSnapshot = ConsentSnapshot()


class ContextUpdate(Strict):
    page_context: PageContext


class SupportFlowStart(Strict):
    flow_type: FlowType
    # Idempotent start (API §16): a replay with the same id returns the same flow.
    client_request_id: uuid.UUID | None = None
    # C3 / Conversation Flow §11: replacing an active flow that has progress needs the
    # visitor's explicit confirmation; without it the server answers 409 confirm_required.
    confirm_replace: bool = False


class SupportStepSubmit(Strict):
    client_step_id: uuid.UUID
    step_id: str = Field(max_length=64)
    # Exactly one of: an allowlisted option, or a navigation command.
    option_id: str | None = Field(default=None, max_length=64)
    command: Literal["back", "restart", "cancel"] | None = None


class MessageSubmit(Strict):
    client_message_id: uuid.UUID
    # Hard ceiling well above the product limit: the product limit (4,000) is enforced in the
    # router as 413 GUIDE_PAYLOAD_TOO_LARGE, which the UI treats as "please shorten".
    text: str = Field(min_length=1, max_length=20000)
    intent_hint: str | None = Field(default=None, pattern=r"^I-[A-Z-]{2,24}$")


ConsentPurpose = Literal[CONSENT_PURPOSES]  # type: ignore[valid-type]


class ConsentPut(Strict):
    status: Literal["granted", "denied", "withdrawn"]
    notice_version: str = Field(max_length=48)
    source: Literal["guide"] = "guide"


class LeadFlowStart(Strict):
    flow_kind: Literal["demo", "sales"] = "demo"
    source_intent: str | None = Field(default=None, pattern=r"^I-[A-Z-]{2,24}$")


class LeadFlowUpdate(Strict):
    # Values are validated per field against the approved schema in leads.FIELDS.
    fields: dict[str, str] = Field(default_factory=dict, max_length=8)
    cancel: bool = False


class LeadSubmit(Strict):
    consent_receipt_id: str | None = Field(default=None, max_length=48)


class HandoffRequest(Strict):
    target: Literal["support", "sales", "organizer", "sign_in", "help_center", "status",
                    "privacy"]
    reason_code: str | None = Field(default=None, pattern=r"^[a-z_]{2,48}$")


class FeedbackSubmit(Strict):
    client_feedback_id: uuid.UUID
    target_type: Literal["answer", "session"]
    target_id: str | None = Field(default=None, max_length=64)
    rating: Literal["up", "down"]
    reason_code: Literal["not_helpful", "incorrect", "unclear", "out_of_date", "too_long",
                         "other", "helpful"] | None = None
    comment: str | None = Field(default=None, max_length=500)


# ── Response contracts (documentation; API §8, §10, §13; C4, C5, C7) ──────────────────────
# Routes return plain dicts. These models describe those bodies in the OpenAPI document
# (API §22) and are deliberately NOT used as response_model, which would silently drop any
# field the model does not list.

class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class FieldError(Open):
    field: str
    code: str
    message: str


class Problem(Open):
    """RFC 9457 problem details with a stable machine code (API §5)."""
    type: str
    title: str
    status: int
    detail: str
    code: str
    request_id: str | None = None
    correlation_id: str | None = None
    retryable: bool
    field_errors: list[FieldError] | None = None


class Action(Open):
    action_id: str
    type: str
    label: str
    href: str | None = None


class Source(Open):
    source_id: str
    title: str
    source_type: str | None = None
    version: str | None = None
    updated_at: str | None = None
    canonical_path: str | None = None
    freshness: Literal["current", "stale"] | None = None
    stale: bool | None = None
    external: bool | None = None
    external_url: str | None = Field(default=None, pattern=r"^https://")


class Uncertainty(Open):
    state: Literal["none", "partial", "gap"]
    message: str | None = None


class ClarificationOption(Open):
    id: str
    label: str


class Clarification(Open):
    """C4: at most one clarifying question with 2–4 chips (Conversation Flow §6.1, CF-AT-04).
    A chip sends its label as the visitor's next message."""
    question: str
    options: list[ClarificationOption] = Field(min_length=2, max_length=4)


class Paused(Open):
    """C7: a capability switched off at runtime (G3) and the direct routes that still work."""
    feature: Literal["leads", "viewer_support"]
    message: str
    routes: list[Action]


class AssistantMessage(Open):
    message_id: str
    text: str
    answer_class: str
    grounded: bool
    sources: list[Source]
    actions: list[Action]
    uncertainty: Uncertainty
    notice: str | None = None
    resume: dict | None = None
    suggestions: list[dict] | None = None
    clarification: Clarification | None = None
    paused: Paused | None = None
    missing_topic_code: str | None = None
    intent_class: str | None = None
    # CF §10.1: free text that is a viewer issue starts that flow; this is its first step
    # (same shape as the POST /support-flows response).
    support_flow: dict | None = None


class MessageResult(Open):
    request_id: str
    session_id: str
    user_message_id: str
    assistant_message: AssistantMessage
    session_state: str
    replayed: bool | None = None


class StatusCard(Open):
    """C5: the D1 public status, shown at the playback flow's public-status step (PB-06)."""
    state: Literal["operational", "degraded", "outage", "unknown"]
    summary: str
    checked_at: str
    status_url: str


class SupportStep(Open):
    step_id: str
    prompt: str
    input_type: str
    options: list[dict]
    allow_skip: bool
    allow_back: bool


class SupportFlow(Open):
    flow_id: str
    flow_type: str
    title: str
    status: str
    resolution_code: str | None
    step: SupportStep | None
    message: str | None
    notice: str | None
    actions: list[Action]
    status_card: StatusCard | None = None
    next_flow: dict | None = None


class HandoffResult(Open):
    handoff_id: str
    target: str
    outcome: Literal["route_available", "confirmed_submission", "failed", "unknown"]
    message: str
    actions: list[Action]
    replayed: bool | None = None


class SessionBody(Open):
    session_id: str
    session_state: str
    expires_at: str
    idle_timeout_seconds: int
    context_version: int
    event_context: dict | None


class SessionCreated(SessionBody):
    csrf_token: str
    assistant: dict
    welcome: str
    suggested_intents: list[dict]
    privacy: dict


class Bootstrap(Open):
    api_version: str
    assistant: dict
    enabled: bool
    locale: str
    welcome: str | None
    suggested_intents: list[dict]
    limits: dict
    privacy: dict
    direct_routes: list[Action]
    unavailable_reason: Literal["disabled", "degraded"] | None = None


class Consent(Open):
    purpose: str
    status: str
    notice_version: str
    recorded_at: str
    receipt_id: str


class LeadFlow(Open):
    flow_id: str
    flow_kind: str
    status: str
    schema_version: str | None = None
    purpose_notice: dict | None = None
    consent: dict | None = None
    fields: list[dict] | None = None
    missing_required: list[str] | None = None


class LeadOutcome(Open):
    flow_id: str
    outcome: Literal["confirmed", "failed", "unknown"]
    message: str
    submission_reference: str | None
    replayed: bool
    actions: list[Action]
