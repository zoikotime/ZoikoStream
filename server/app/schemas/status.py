"""Public status page request models (ZST-EC-001 STS-001).

Component and region keys are validated against the real taxonomy in
services/status_publication._clean_selection, which drops anything unknown rather than
storing a preference that points at a component the platform does not have.
"""

from pydantic import BaseModel, EmailStr, Field


class StatusSubscribeIn(BaseModel):
    email: EmailStr
    # Empty means "all" - which is the only way a subscriber receives everything.
    components: list[str] = []
    regions: list[str] = []
    notify_kinds: list[str] = []


class StatusTokenIn(BaseModel):
    token: str = Field(..., min_length=16, max_length=200)


class StatusPreferencesIn(BaseModel):
    components: list[str] | None = None
    regions: list[str] | None = None
    notify_kinds: list[str] | None = None
