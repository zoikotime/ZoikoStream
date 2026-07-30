import uuid
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from datetime import datetime

class RegisterIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    organization_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    username: str | None = Field(None, min_length=3, max_length=60, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=72)  # bcrypt caps at 72 bytes


class LoginIn(BaseModel):
    identifier: str  # username or email
    password: str
    remember: bool = False


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class VerifyOtpIn(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{4}$")


class ResetPasswordIn(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{4}$")
    password: str = Field(min_length=8, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: EmailStr
    username: str
    role: str
    org_id: uuid.UUID
    organization_name: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_id: uuid.UUID
    organization_name: str
    role: str
    is_active: bool


class SwitchOrgIn(BaseModel):
    org_id: uuid.UUID


class UpdateProfileIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


_MEMBER_ROLES = r"^(org_admin|host|moderator|speaker|viewer)$"


class OrgUserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: EmailStr
    role: str
    is_active: bool
    created_at: datetime


class OrgUserUpdateIn(BaseModel):
    role: str = Field(pattern=_MEMBER_ROLES)


class InvitationCreateIn(BaseModel):
    email: EmailStr
    role: str = Field(pattern=_MEMBER_ROLES)


class InvitationActionIn(BaseModel):
    action: str = Field(pattern=r"^resend$")


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: str
    status: str
    invited_by: str | None = None
    expires_at: datetime
    created_at: datetime


class AcceptInvitationIn(BaseModel):
    token: str
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=72)

class ChannelCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=120)
    slug: str = Field(..., min_length=3, max_length=120)
    description: str | None = None
    category: str | None = None


class ChannelUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    avatar_url: str | None = None
    banner_url: str | None = None


class ChannelOut(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    avatar_url: str | None
    banner_url: str | None
    category: str | None
    created_at: datetime

    model_config = {
        "from_attributes": True
    }