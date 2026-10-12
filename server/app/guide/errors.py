"""RFC 9457 problem details with stable machine codes (API §5).

Clients branch on `code`, never on title/detail prose. Public errors carry no stack trace,
internal hostname, provider name, database identifier or token content (API-AT-20).
"""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .crypto import CryptoUnavailable

log = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"

# code -> (status, slug, title, retryable)
CODES: dict[str, tuple[int, str, str, bool]] = {
    "GUIDE_BAD_REQUEST": (400, "bad-request", "Malformed request", False),
    "GUIDE_SESSION_INVALID": (401, "session-invalid", "Guide session is not valid", False),
    "GUIDE_PUBLIC_BOUNDARY_DENIED": (403, "public-boundary-denied",
                                     "Request is outside the public Guide", False),
    "GUIDE_RESOURCE_NOT_FOUND": (404, "resource-not-found", "Resource not found", False),
    "GUIDE_STATE_CONFLICT": (409, "state-conflict", "Request conflicts with current state",
                             False),
    "GUIDE_PAYLOAD_TOO_LARGE": (413, "payload-too-large", "Request is too large", False),
    "GUIDE_VALIDATION_ERROR": (422, "validation-error", "Request validation failed", False),
    "GUIDE_RATE_LIMITED": (429, "rate-limited", "Too many requests", True),
    "GUIDE_UNAVAILABLE": (503, "unavailable", "Guide is temporarily unavailable", True),
    "GUIDE_OUTCOME_UNKNOWN": (504, "outcome-unknown", "Outcome could not be confirmed", False),
    "GUIDE_INTERNAL_ERROR": (500, "internal-error", "Guide could not complete the request",
                             False),
}


class GuideProblem(Exception):
    def __init__(self, code: str, detail: str | None = None, *,
                 field_errors: list[dict] | None = None, headers: dict | None = None,
                 extra: dict | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.field_errors = field_errors
        self.headers = headers or {}
        self.extra = extra or {}


def problem_response(request: Request, code: str, detail: str | None = None, *,
                     field_errors: list[dict] | None = None, headers: dict | None = None,
                     extra: dict | None = None) -> JSONResponse:
    status, slug, title, retryable = CODES[code]
    body = {
        "type": f"/problems/{slug}",
        "title": title,
        "status": status,
        "detail": detail or title + ".",
        "code": code,
        "request_id": getattr(request.state, "guide_request_id", None),
        "correlation_id": getattr(request.state, "guide_correlation_id", None),
        "retryable": retryable,
    }
    if field_errors:
        body["field_errors"] = field_errors
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON,
                        headers=headers or None)


_STATUS_TO_CODE = {400: "GUIDE_BAD_REQUEST", 401: "GUIDE_SESSION_INVALID",
                   403: "GUIDE_PUBLIC_BOUNDARY_DENIED", 404: "GUIDE_RESOURCE_NOT_FOUND",
                   405: "GUIDE_BAD_REQUEST", 409: "GUIDE_STATE_CONFLICT",
                   413: "GUIDE_PAYLOAD_TOO_LARGE", 415: "GUIDE_BAD_REQUEST",
                   422: "GUIDE_VALIDATION_ERROR", 429: "GUIDE_RATE_LIMITED"}

# Pydantic error types that mean "schema violation" (400) rather than "a field's value is
# wrong" (422). Unknown properties land here: security-sensitive schemas reject them.
_SCHEMA_ERRORS = {"extra_forbidden", "json_invalid", "model_type", "model_attributes_type",
                  "missing", "dict_type", "list_type"}


def install(app) -> None:
    @app.exception_handler(GuideProblem)
    async def _guide_problem(request: Request, exc: GuideProblem):
        return problem_response(request, exc.code, exc.detail, field_errors=exc.field_errors,
                                headers=exc.headers, extra=exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        field_errors = []
        for err in errors:
            loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path",
                                                                     "header")]
            # The submitted value is deliberately NOT echoed: it may be a secret or PII.
            field_errors.append({"field": ".".join(loc) or "body",
                                 "code": err.get("type", "invalid"),
                                 "message": _safe_message(err)})
        code = ("GUIDE_BAD_REQUEST" if any(e.get("type") in _SCHEMA_ERRORS for e in errors)
                else "GUIDE_VALIDATION_ERROR")
        detail = ("The request does not match the expected schema."
                  if code == "GUIDE_BAD_REQUEST" else "One or more submitted fields are invalid.")
        return problem_response(request, code, detail, field_errors=field_errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = _STATUS_TO_CODE.get(exc.status_code, "GUIDE_INTERNAL_ERROR")
        return problem_response(request, code)

    @app.exception_handler(OperationalError)
    async def _db_down(request: Request, exc: OperationalError):
        log.error("guide database unavailable on %s: %s", request.url.path,
                  type(exc.orig).__name__ if exc.orig else type(exc).__name__)
        return problem_response(request, "GUIDE_UNAVAILABLE")

    @app.exception_handler(CryptoUnavailable)
    async def _crypto(request: Request, exc: CryptoUnavailable):
        log.error("guide encryption unavailable on %s: %s", request.url.path, exc)
        return problem_response(request, "GUIDE_UNAVAILABLE")

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(request: Request, exc: SQLAlchemyError):
        log.error("guide database error on %s", request.url.path, exc_info=exc)
        return problem_response(request, "GUIDE_INTERNAL_ERROR")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error("guide unhandled error on %s", request.url.path, exc_info=exc)
        return problem_response(request, "GUIDE_INTERNAL_ERROR")


def _safe_message(err: dict) -> str:
    kind = err.get("type", "")
    if kind == "extra_forbidden":
        return "This field is not allowed."
    if kind == "missing":
        return "This field is required."
    if kind.startswith("string_too_long"):
        return "Value is too long."
    if kind.startswith("string_too_short"):
        return "Value is too short."
    return "Value is invalid."
