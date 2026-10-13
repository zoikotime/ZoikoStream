"""Typed Guide errors (ZST-UX-GUIDE-001 §14: errors distinguish unauthorized, forbidden,
entitlement blocked, validation failed, conflict, service unavailable, timeout and unknown
outcome). Every error carries the §15 user-facing wording, never internals."""
from __future__ import annotations


class GuideError(Exception):
    status = 500
    code = "OPGUIDE_ERROR"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def body(self) -> dict:
        return {"code": self.code, "message": self.message, **self.detail}


class Unauthorized(GuideError):
    status, code = 401, "OPGUIDE_UNAUTHORIZED"


class Forbidden(GuideError):
    status, code = 403, "OPGUIDE_FORBIDDEN"


class EntitlementBlocked(GuideError):
    status, code = 403, "OPGUIDE_ENTITLEMENT_BLOCKED"


class NotFound(GuideError):
    status, code = 404, "OPGUIDE_NOT_FOUND"


class ValidationFailed(GuideError):
    status, code = 422, "OPGUIDE_VALIDATION_FAILED"


class Conflict(GuideError):
    status, code = 409, "OPGUIDE_CONFLICT"


class RateLimited(GuideError):
    status, code = 429, "OPGUIDE_RATE_LIMITED"


class Unavailable(GuideError):
    status, code = 503, "OPGUIDE_UNAVAILABLE"


class OutcomeUnknown(GuideError):
    status, code = 504, "OPGUIDE_OUTCOME_UNKNOWN"


# §15 failure-state wording.
GUIDE_UNAVAILABLE = "Guide is temporarily unavailable. Core ZoikoStream controls remain available."
TOOL_FAILED = "The service did not confirm the change. No successful change has been recorded."
OUTCOME_UNKNOWN = ("The request was submitted, but completion is not confirmed. Check current "
                   "state before retrying.")


def no_data(data: str, obj: str) -> str:
    return f"I do not have current {data} for this {obj}."


def permission_denied(resource: str) -> str:
    return f"You do not have permission to view {resource}."
