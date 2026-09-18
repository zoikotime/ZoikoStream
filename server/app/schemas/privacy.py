"""Request/response shapes for the public privacy surface (routers/privacy.py).

A different trust boundary from schemas/organization.py's admin shapes, for the same
reason schemas/delivery.py exists separately: these are what a requester sees, and a
requester is frequently somebody with no account at all.

The response shapes carry only what the emails already disclose — reference, type, status,
deadline note. The request's free-text `details` and any internal decision reasoning are
read by NO route here, so a future field added to a response is a deliberate disclosure
rather than an accident of a shared model.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from ..models.privacy import CONSENT_DECISIONS, REQUEST_TYPES


class PrivacyRequestIn(BaseModel):
    """Intake from the Privacy Center form.

    `details` is bounded like contact.py's message: an unbounded free-text field on an
    unauthenticated surface is both an abuse vector and a place where people paste things
    a privacy request does not need (credentials, other people's data).
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    request_type: str
    details: str | None = Field(default=None, max_length=4000)

    @field_validator("request_type")
    @classmethod
    def known_type(cls, v: str) -> str:
        if v not in REQUEST_TYPES:
            raise ValueError(f"request_type must be one of: {', '.join(REQUEST_TYPES)}")
        return v


class PrivacyRequestOut(BaseModel):
    """The identity block, and nothing more.

    Mirrors email._request_rows exactly: reference, type, status. A response that carried
    the request's own free-text details back over an unauthenticated endpoint would let
    anyone who guessed a reference read the request contents.
    """

    model_config = ConfigDict(from_attributes=True)

    reference: str
    request_type: str
    status: str
    deadline_note: str


class PrivacyVerifyIn(BaseModel):
    """Redeem a verification token. Purpose is implied by the route, never the body."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=10, max_length=200)


class PrivacyDecisionIn(BaseModel):
    """A consent decision recorded exactly as made. No default; both are equal peers."""

    model_config = ConfigDict(extra="forbid")

    version_id: str
    email: EmailStr
    decision: str

    @field_validator("decision")
    @classmethod
    def known_decision(cls, v: str) -> str:
        if v not in CONSENT_DECISIONS:
            raise ValueError(f"decision must be one of: {', '.join(CONSENT_DECISIONS)}")
        return v


class PrivacyConsentOut(BaseModel):
    decision: str
    decided_at: datetime | None = None
    source: str


class PublicNoticeOut(BaseModel):
    """A PUBLISHED notice version as the Privacy Center renders it.

    A DRAFT communicates nothing — `status` is checked in the router, not trusted to the
    caller, so this shape is only ever filled from a published row.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    version: str
    effective_at: datetime | None = None
    published_at: datetime | None = None
    material_change: bool
    consent_required: bool
    consent_purpose: str | None = None
    change_summary: str | None = None


class SubprocessorOut(BaseModel):
    """What PRV-004 publishes about a processor — and no contract terms."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    service: str
    processing_purpose: str
    region: str | None = None
    status: str
    effective_from: datetime | None = None


class ExportDownloadOut(BaseModel):
    """The one thing an export link can do: resolve once, to a fresh signed URL."""

    download_url: str
