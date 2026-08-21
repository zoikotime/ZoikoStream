"""Public contact-form payload.

Every field here is attacker-supplied and unauthenticated, so the schema is the first line of
defence: bounded lengths, a real email parse, and — critically — `extra="forbid"`, so a request
carrying `to`, `recipient`, `bcc` or an SMTP setting is REJECTED rather than quietly ignored.
Silently dropping such a field would also be safe today, but a hard 422 makes the intent
visible the moment anyone tries it, and stops a future refactor from starting to read it.
"""
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ContactMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first: str = Field(..., min_length=1, max_length=80)
    last: str = Field(..., min_length=1, max_length=80)
    email: EmailStr = Field(..., max_length=254)          # RFC 5321 maximum
    # Optional: plenty of genuine enquiries come from individuals with no organization.
    org: str = Field("", max_length=120)
    country: str = Field(..., min_length=1, max_length=80)
    topic: str = Field(..., min_length=1, max_length=60)
    # Bounded so a single request cannot post a multi-megabyte body. Generous enough for a
    # real brief; far below anything that would strain the mail provider.
    message: str = Field(..., min_length=1, max_length=4000)

    @field_validator("first", "last", "country", "topic", "message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # str_strip_whitespace has already trimmed; a field of only spaces arrives empty and
        # must not satisfy a required field.
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("first", "last", "email", "org", "country", "topic")
    @classmethod
    def _no_header_breaks(cls, v: str) -> str:
        # These values can reach an email SUBJECT. A CR or LF inside a header is the classic
        # header-injection primitive, so it is refused at the edge as well as stripped later.
        if "\r" in v or "\n" in v:
            raise ValueError("must not contain line breaks")
        return v


class ContactMessageOut(BaseModel):
    """Deliberately says nothing about the mail transport, the destination inbox, or whether a
    provider key is configured — a public endpoint must not report internal configuration."""

    received: bool = True
