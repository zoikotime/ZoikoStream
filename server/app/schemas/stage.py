from pydantic import BaseModel, EmailStr, Field, model_validator


class StagePromoteIn(BaseModel):
    # Either identity (promoting someone already in the raised-hand queue) or email
    # (host inviting anyone straight from the Registrations list) must be given.
    identity: str | None = None
    email: EmailStr | None = None
    display_name: str = Field("Guest", max_length=120)

    @model_validator(mode="after")
    def _one_of(self):
        if not self.identity and not self.email:
            raise ValueError("identity or email is required")
        return self


class StageDemoteIn(BaseModel):
    identity: str = Field(min_length=1)


class StageEntry(BaseModel):
    identity: str
    display_name: str


class StageOut(BaseModel):
    hands: list[StageEntry]
    roster: list[StageEntry]
