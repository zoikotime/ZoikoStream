from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Defaults mirror client/src/data/orgSettings.js's securityDefaults/notificationDefaults
# exactly, applied when an org's security/notifications JSON column is still unset.
SECURITY_DEFAULTS = {
    "require_2fa": False,
    "enforce_sso": False,
    "min_password_length": 8,
    "session_timeout": "8 hours",
    "allowed_domains": "",
}
NOTIFICATIONS_DEFAULTS = {
    "event_scheduled": True,
    "event_starting": True,
    "recording_ready": True,
    "weekly_summary": False,
    "billing": True,
    "mentions": True,
    "member_joined": False,
    "security_alerts": True,
}


class OrgProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    slug: str | None
    website: str | None
    description: str | None
    industry: str | None
    company_size: str | None
    support_email: str | None


class OrgProfileUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(None, max_length=140)
    website: str | None = None
    description: str | None = None
    industry: str | None = None
    company_size: str | None = None
    support_email: EmailStr | None = None


class OrgSecurityOut(BaseModel):
    require_2fa: bool
    enforce_sso: bool
    min_password_length: int
    session_timeout: str
    allowed_domains: str


class OrgSecurityUpdateIn(BaseModel):
    require_2fa: bool
    enforce_sso: bool
    min_password_length: int
    session_timeout: str
    allowed_domains: str


class OrgNotificationsOut(BaseModel):
    event_scheduled: bool
    event_starting: bool
    recording_ready: bool
    weekly_summary: bool
    billing: bool
    mentions: bool
    member_joined: bool
    security_alerts: bool


class OrgNotificationsUpdateIn(OrgNotificationsOut):
    pass


class OrgDomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    domain: str | None
    domain_verified: bool


class OrgDomainUpdateIn(BaseModel):
    domain: str | None = None


class OrgBrandingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    primary_color: str | None


class OrgBrandingUpdateIn(BaseModel):
    primary_color: str | None = None
