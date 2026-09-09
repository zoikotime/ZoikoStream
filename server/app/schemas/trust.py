"""Trust Center and marketing request models (ZST-EC-001 TRU-002/003, MKT-000/004).

Vocabulary is validated in the services against the real constant tuples, so a bad value is
refused rather than stored - these models only bound shape and size.
"""

from pydantic import BaseModel, EmailStr, Field


# ── TRU-002 evidence requests ────────────────────────────────────────────────────────────

class EvidenceRequestIn(BaseModel):
    requester_email: EmailStr
    document_id: str
    purpose: str = Field(..., min_length=3, max_length=60)
    scope: str = Field(..., min_length=3, max_length=40)
    requester_name: str | None = Field(None, max_length=200)
    company_name: str | None = Field(None, max_length=200)
    purpose_note: str | None = Field(None, max_length=2000)
    # What the requester CLAIMS. `trust_center.qualify` decides whether it can be checked
    # against anything real; nothing is auto-approved either way.
    qualification_basis: str = Field("customer_organization", max_length=40)


# ── TRU-003 vulnerability reports ────────────────────────────────────────────────────────

class VulnerabilityReportIn(BaseModel):
    """The protected report form.

    Note what is absent: there is no field for a password, an API key, a private key or a
    token. The form cannot ask for a credential because there is nowhere to put one, and
    the router additionally warns when free text looks like a leaked secret.
    """

    reporter_email: EmailStr
    title: str = Field(..., min_length=4, max_length=300)
    category: str = Field(..., min_length=3, max_length=40)
    description: str = Field(..., min_length=20, max_length=20000)
    reproduction: str | None = Field(None, max_length=20000)
    affected_service: str | None = Field(None, max_length=200)
    reporter_name: str | None = Field(None, max_length=200)
    # Recording a preference is not a promise of credit: there is no public credit
    # programme, and no message implies one.
    identity_visibility: str = Field("private", max_length=30)
    evidence: str | None = Field(None, max_length=200000)


# ── MKT consent ──────────────────────────────────────────────────────────────────────────

class MarketingSubscribeIn(BaseModel):
    email: EmailStr
    # Required and non-empty: there is no "subscribe to everything" default, because
    # asking for one topic is not consent to four.
    topics: list[str] = Field(..., min_length=1, max_length=8)
    source: str = Field("preference_center", max_length=40)


class MarketingTokenIn(BaseModel):
    token: str = Field(..., min_length=16, max_length=200)


class MarketingPreferencesIn(BaseModel):
    # An empty list is a valid choice and means "stop everything".
    topics: list[str] = Field(..., max_length=8)


class GuideRequestIn(BaseModel):
    email: EmailStr
    guide: str = Field(..., min_length=3, max_length=60)
    name: str | None = Field(None, max_length=200)
    # Defaults False. Asking for a guide is not consent to a campaign, so the opt-in has to
    # be affirmative and is recorded separately.
    marketing_opt_in: bool = False


class WebinarRegistrationIn(BaseModel):
    email: EmailStr
    name: str | None = Field(None, max_length=200)
