"""POST /api/playback/qoe — a viewer's player reporting its own playback quality.

Public: a viewer may be anonymous. What authorizes a beacon is the signed `qoe_token`
GET /events/{id}/watch handed this playback session (services/playback_qoe.issue_token), which
also fixes the event and mode; nothing in the beacon can name an organization or an event.

The body is read as raw JSON whatever its Content-Type, so the page can send its final beacon
with fetch(keepalive) as text/plain: a "simple" request that needs no CORS preflight, which
is what lets it leave while the page is being closed.

Not org-state gated, for the same reason the live socket is not (main.py): playback telemetry
from a restricted tenant's audience is still that audience's playback.
"""
from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..db import get_db
from ..ratelimit import SlidingWindow, client_ip
from ..schemas.playback import QoeBeacon
from ..services import playback_qoe

router = APIRouter(prefix="/playback", tags=["playback"])

MAX_BODY_BYTES = 4096

# Per playback SESSION, not per IP: a corporate audience behind one NAT address is hundreds
# of honest viewers. A player sends about one beacon a minute plus one per start, failure or
# visibility change, so this is generous for a real player and tight for a loop.
#
# Per process: it only slows one session's runaway loop, which needs no global view. What
# bounds how MANY sessions an event can be sent is the shared new-session budget in
# services/playback_qoe.admit_new_session. Called only from the async `_beacon` dependency,
# i.e. on the event loop's single thread — SlidingWindow and this dict are not thread-safe,
# and the endpoint itself runs on the threadpool.
SESSION_LIMIT, SESSION_WINDOW = 30, 60.0
_session_windows: dict[str, SlidingWindow] = {}
_SWEEP_SECONDS = 300.0
_last_sweep = time.monotonic()


def _allow(session_id: str) -> bool:
    global _last_sweep
    now = time.monotonic()
    if now - _last_sweep > _SWEEP_SECONDS:
        _last_sweep = now
        stale = []
        for key, window in _session_windows.items():
            window.prune()
            if window.empty():
                stale.append(key)
        for key in stale:
            _session_windows.pop(key, None)
    window = _session_windows.get(session_id)
    if window is None:
        window = _session_windows[session_id] = SlidingWindow(SESSION_LIMIT, SESSION_WINDOW)
    return window.allow()


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail)


def _too_large() -> HTTPException:
    return HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Beacon too large")


async def _read_capped(request: Request) -> bytes:
    """The body, refusing it as soon as it passes MAX_BODY_BYTES. A declared Content-Length is
    checked first; a chunked body (no length) is read only up to the cap, never in full."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            raise _too_large()
    return bytes(raw)


async def _beacon(request: Request) -> tuple[QoeBeacon, dict]:
    """The request body as a validated beacon plus its token's claims. An async dependency
    because reading the raw body is, and so the per-session limiter runs on one thread; the
    endpoint itself stays synchronous, like every other database route here."""
    raw = await _read_capped(request)
    try:
        body = json.loads(raw or b"null")
    except (ValueError, UnicodeDecodeError, RecursionError):
        # RecursionError: 4 KB of "[[[[…" nests past the JSON decoder's recursion limit. It is
        # still just a malformed beacon — a 422, never an unhandled 500.
        raise _invalid("Beacon is not JSON") from None
    if not isinstance(body, dict):
        raise _invalid("Beacon must be a JSON object")
    try:
        beacon = QoeBeacon.model_validate(body)
    except ValidationError as e:
        # Field names only, never the submitted values, which include the token.
        fields = sorted({".".join(str(p) for p in err["loc"]) or "body" for err in e.errors()})
        raise _invalid(f"Invalid beacon: {', '.join(fields)}") from None
    claims = playback_qoe.read_token(beacon.token)
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired playback session")
    if not _allow(claims["sid"]):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many beacons for this session",
                            headers={"Retry-After": str(int(SESSION_WINDOW))})
    return beacon, claims


def _host_org(request: Request) -> uuid.UUID | None:
    """The organization whose custom domain this request arrived on (set by
    services/custom_domain_routing's middleware), or None on a platform host."""
    state = request.scope.get("state") or {}
    domain = state.get("custom_domain") if isinstance(state, dict) else None
    return uuid.UUID(domain["org_id"]) if domain and domain.get("org_id") else None


@router.post("/qoe", status_code=status.HTTP_204_NO_CONTENT)
def report_qoe(request: Request, parsed: tuple[QoeBeacon, dict] = Depends(_beacon),
               db: Session = Depends(get_db)) -> Response:
    beacon, claims = parsed
    try:
        outcome = playback_qoe.ingest(db, claims, beacon, host_org_id=_host_org(request),
                                      address=client_ip(request))
    except playback_qoe.BeaconRejected as e:
        raise _invalid(str(e)) from None
    if outcome == playback_qoe.GONE:
        raise HTTPException(status.HTTP_410_GONE, "This event no longer exists")
    if outcome == playback_qoe.WRONG_HOST:
        # Same answer the custom-domain middleware gives an event of another organization: a
        # customer's hostname carries only that customer's audience.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if outcome == playback_qoe.OVER_BUDGET:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Too many new playback sessions for this event from here",
                            headers={"Retry-After": str(playback_qoe.NEW_SESSION_WINDOW_SECONDS)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
