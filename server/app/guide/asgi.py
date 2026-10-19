"""The Guide as an isolated sub-application.

Mounted (not include_router'd) so the Guide has its own exception handlers — problem+json
with stable codes — and its own middleware, without changing a single platform response.
The platform's CORS middleware still wraps it, so browser callers get CORS headers on Guide
errors too.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import FastAPI, Request

from . import errors
from .router import router
from .settings import get_settings

log = logging.getLogger(__name__)

MOUNT_PATH = "/api/v1/public-guide"
MAX_BODY_BYTES = 64 * 1024  # gateway bound; message text has its own 4,000-char limit


def _rid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def create_app() -> FastAPI:
    s = get_settings()
    # Swagger only outside production (API §22: engineering environments only).
    docs = not s.is_production()
    app = FastAPI(title="ZoikoStream Public Guide API", version="1.0",
                  docs_url="/docs" if docs else None, redoc_url=None,
                  openapi_url="/openapi.json" if docs else None)
    errors.install(app)

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
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Language"] = "en-US"
        response.headers.setdefault("Cache-Control", "no-store")
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
