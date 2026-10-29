"""The Guide as an isolated sub-application.

Mounted (not include_router'd) so the Guide has its own exception handlers — problem+json
with stable codes — and its own middleware, without changing a single platform response.
The platform's CORS middleware still wraps it, so browser callers get CORS headers on Guide
errors too.

Every Guide response carries the API security posture of Security/Privacy Appendix A and §8
(SECURITY_HEADERS: no-referrer, no framing, a deny-all CSP for these JSON/SSE responses, no
browser capabilities, same-site resource policy, nosniff, no-store; HSTS over https or in
production). The request body limit is enforced on the bytes actually received, so a chunked
body without Content-Length cannot slip past it (§21 "body limits before model work").
"""
from __future__ import annotations

import logging
import secrets

from fastapi import FastAPI, Request

from . import errors
from .router import router
from .sessions import CSRF_ROTATED_HEADER, set_session_cookie
from .settings import get_settings

log = logging.getLogger(__name__)

MOUNT_PATH = "/api/v1/public-guide"
MAX_BODY_BYTES = 64 * 1024  # gateway bound; message text has its own 4,000-char limit


# Security/Privacy Appendix A + §8. The Guide API serves JSON/SSE only, so its own CSP can
# deny everything; the website shell owns the page CSP.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "same-site",
}
HSTS = "max-age=31536000; includeSubDomains"


def _rid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    # A forwarded scheme is believed only behind configured trusted proxies.
    return (get_settings().GUIDE_TRUSTED_PROXY_HOPS > 0
            and request.headers.get("x-forwarded-proto", "").split(",")[-1].strip() == "https")


class BodyLimitMiddleware:
    """Counts the request body as it streams in and answers 413 GUIDE_PAYLOAD_TOO_LARGE as
    soon as it passes `limit`, with or without Content-Length. The (small, bounded) body is
    then replayed to the app; later receive() calls — client-disconnect detection on SSE —
    still reach the server."""

    def __init__(self, app, limit: int):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":   # disconnected before the body arrived
                await self.app(scope, _replay([message], receive), send)
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                response = errors.problem_response(Request(scope), "GUIDE_PAYLOAD_TOO_LARGE")
                await response(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        await self.app(scope, _replay([{"type": "http.request", "body": b"".join(chunks),
                                        "more_body": False}], receive), send)


def _replay(messages: list[dict], receive):
    pending = list(messages)

    async def replayed():
        if pending:
            return pending.pop(0)
        return await receive()
    return replayed


def create_app() -> FastAPI:
    s = get_settings()
    # Swagger only outside production (API §22: engineering environments only).
    docs = not s.is_production()
    app = FastAPI(title="ZoikoStream Public Guide API", version="1.0",
                  docs_url="/docs" if docs else None, redoc_url=None,
                  openapi_url="/openapi.json" if docs else None)
    errors.install(app)
    # Registered BEFORE guide_envelope: Starlette makes the last-registered middleware the
    # outermost, so the envelope wraps this one and still stamps ids/headers on a 413.
    app.add_middleware(BodyLimitMiddleware, limit=MAX_BODY_BYTES)

    @app.middleware("http")
    async def guide_envelope(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        # Accept a client id only if it is short and inert; never reflect arbitrary text.
        request.state.guide_request_id = (supplied if 8 <= len(supplied) <= 64
                                          and supplied.replace("-", "").replace("_", "")
                                          .isalnum() else _rid("req"))
        request.state.guide_correlation_id = _rid("cor")
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            response = errors.problem_response(request, "GUIDE_PAYLOAD_TOO_LARGE")
        else:
            response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.guide_request_id
        response.headers["X-Correlation-Id"] = request.state.guide_correlation_id
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if s.is_production() or _is_https(request):
            response.headers["Strict-Transport-Security"] = HSTS
        response.headers["Content-Language"] = "en-US"
        response.headers.setdefault("Cache-Control", "no-store")
        rotated = getattr(request.state, "guide_rotated_capability", None)
        if rotated is not None:
            # Capability rotation (sessions.rotate_capability): the new cookie and CSRF token
            # replace the old pair, which the database no longer accepts.
            set_session_cookie(response, rotated[0])
            response.headers[CSRF_ROTATED_HEADER] = rotated[1]
        verdict = getattr(request.state, "guide_ratelimit", None)
        if verdict is not None:
            for k, v in verdict.headers().items():
                response.headers.setdefault(k, v)
        return response

    app.include_router(router)
    return app


def mount_guide(platform_app) -> bool:
    """Mount the Guide into the platform app. Never raises: any Guide fault is logged and the
    platform boots without it (PR-041, ARCH-AT-01/18)."""
    try:
        s = get_settings()
        problems = s.production_problems() if s.GUIDE_ENABLED else []
        for p in problems:
            log.error("ZoikoStream Guide disabled: %s", p)
        platform_app.mount(MOUNT_PATH, create_app(), name="public-guide")
        if not problems:
            # Hourly reconcile + retention purge (one process at a time; never raises).
            from . import scheduler
            scheduler.start()
        return True
    except Exception:
        log.exception("ZoikoStream Guide failed to mount; platform continues without it")
        return False
