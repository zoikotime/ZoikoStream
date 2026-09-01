import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
    otp: str = Field(pattern=r"^\d{6}$")


class ResetPasswordIn(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")
    password: str = Field(min_length=8, max_length=72)


# ── IDN-001 email verification ──────────────────────────────────────────────────────────

class VerifyEmailIn(BaseModel):
    # Opaque, single-use token from the emailed link. Bounded so a huge body can't be
    # pushed through the hash path; secrets.token_urlsafe(32) renders to 43 characters.
    token: str = Field(min_length=16, max_length=512)


class ResendVerificationIn(BaseModel):
    email: EmailStr


class RegistrationPendingOut(BaseModel):
    """Registration no longer returns a session. The account exists but is unverified,
    so the only thing handed back is what the client needs to render "check your email"."""

    status: str = "EMAIL_VERIFICATION_REQUIRED"
    email: str                      # masked, never the full address
    expires_in_minutes: int
    message: str


class VerificationResultOut(BaseModel):
    status: str                     # "verified"
    message: str


class ChangeRecoveryContactIn(BaseModel):
    """Nominate a recovery address. Not honoured until the address proves control."""
    recovery_email: EmailStr


class ConfirmRecoveryContactIn(BaseModel):
    token: str = Field(min_length=16, max_length=512)


class StepUpIn(BaseModel):
    """Re-verify the holder's password for one high-risk purpose."""
    password: str = Field(min_length=1, max_length=72)
    purpose: str


class StepUpOut(BaseModel):
    """The opaque reference is returned exactly once and never recoverable afterwards."""
    reference: str
    purpose: str
    expires_in_minutes: int


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