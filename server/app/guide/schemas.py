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
    client_request_id: uuid.UUID | None = None


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
    reason_code: Literal["not_helpful", "incorrect", "unclear", "too_long", "other",
                         "helpful"] | None = None
    comment: str | None = Field(default=None, max_length=500)
