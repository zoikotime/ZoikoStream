"""What a viewer's player may send to POST /api/playback/qoe (services/playback_qoe.py).

Strict on purpose: unknown fields are refused rather than ignored, so nothing beyond these
numbers and reason codes — no error text, URL, token or device detail — can ride along into
storage. Every figure is a cumulative total for the playback session, so a later beacon
replaces an earlier one instead of adding to it.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.playback import PLAYBACK_FAILURE_REASONS

FailureReason = Literal[PLAYBACK_FAILURE_REASONS]  # type: ignore[valid-type]

# A session that plays for longer than a week is not a viewer; it is a bug or a forgery.
MAX_SESSION_MS = 7 * 24 * 3600 * 1000


class QoeBeacon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=2048)
    seq: int = Field(ge=1, le=1_000_000_000)
    started: bool = False
    startup_ms: int | None = Field(None, ge=0)
    playback_ms: int = Field(0, ge=0, le=MAX_SESSION_MS)
    rebuffer_count: int = Field(0, ge=0, le=100_000)
    rebuffer_ms: int = Field(0, ge=0, le=MAX_SESSION_MS)
    failures: list[FailureReason] = Field(default_factory=list, max_length=len(PLAYBACK_FAILURE_REASONS))
    ended: bool = False

    @model_validator(mode="after")
    def _consistent(self):
        # Stall time is part of watch time, and a stall needs at least one stall to have it.
        if self.rebuffer_ms > self.playback_ms:
            raise ValueError("rebuffer_ms cannot exceed playback_ms")
        if self.rebuffer_ms and not self.rebuffer_count:
            raise ValueError("rebuffer_ms reported without a rebuffer")
        # Nothing about playback can be measured before playback started.
        if not self.started and (self.playback_ms or self.rebuffer_count or self.startup_ms is not None):
            raise ValueError("playback figures reported before playback started")
        return self
