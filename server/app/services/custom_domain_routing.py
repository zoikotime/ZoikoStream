"""Requests that arrive on a customer's own hostname: whose they are and what they may reach.

    Host: events.customer-a.com
      -> normalized, matched EXACTLY against organizations.domain (never a suffix match)
      -> only an ACTIVE domain (DNS proven, certificate live, HTTPS probe passed) is served
      -> only the public viewer experience is reachable: the watch page, its three API calls
         and its live socket, plus the built static assets
      -> any event named in the path must belong to that same organization, so
         events.customer-a.com/events/<customer-b event>/watch is a 404, page and API alike

Platform hosts (domain_names.platform_hosts: the declared list, APP_URL's host, the CORS
origins, local/test, and zoikostream.com itself, which no customer can claim) pass straight
through, unchanged. Strict mode starts once an operator declares CUSTOM_DOMAIN_PLATFORM_HOSTS
(the feature cannot be switched on without it): from then on a hostname that is neither a
platform host nor an ACTIVE custom domain is refused, including a customer hostname that is
pending, failed, disabled or removed. Before that, everything except an active domain falls
through to the platform exactly as it always did, so deploying this code cannot take an
undeclared hostname offline — not even one a legacy row happens to name.

The host lookup is cached per process for CACHE_TTL seconds and dropped locally on every
change (custom_domains invalidates). Other processes converge within CACHE_TTL. Activation
cannot complete inside that window by accident: the HTTPS probe that gates it reads the
database directly and must name the activating organization.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.websockets import WebSocketClose

from ..domain_names import configured_platform_hosts, platform_hosts
from . import domain_probe

CACHE_TTL = 10.0
_HOST_CACHE_MAX = 5000
_EVENT_CACHE_MAX = 20000

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

# (scope type, methods, pattern). A named `event` group is checked against the host's org.
_ROUTES = (
    ("http", frozenset({"GET", "HEAD"}), re.compile(rf"^/events/(?P<event>{_UUID})/watch/?$")),
    ("http", frozenset({"GET", "HEAD"}), re.compile(r"^/assets/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")),
    ("http", frozenset({"GET"}), re.compile(rf"^/api/events/(?P<event>{_UUID})/watch$")),
    ("http", frozenset({"POST"}), re.compile(rf"^/api/events/(?P<event>{_UUID})/register$")),
    ("http", frozenset({"POST"}), re.compile(rf"^/api/events/(?P<event>{_UUID})/invitation$")),
    ("websocket", None, re.compile(rf"^/api/live/events/(?P<event>{_UUID})/ws$")),
)

# Load-balancer health checks arrive with whatever Host the balancer uses.
_ALWAYS = frozenset({"/health"})

_NOT_FOUND_HTML = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Not found</title>"
    "<style>body{font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;"
    "margin:0;background:#f8fafc;color:#334155}@media(prefers-color-scheme:dark){body{background:#020617;"
    "color:#cbd5e1}}</style></head><body><p>This page isn&#39;t available at this address.</p></body></html>"
)


@dataclass(frozen=True)
class HostEntry:
    org_id: uuid.UUID
    status: str


_lock = threading.Lock()
_hosts: dict[str, tuple[float, HostEntry | None]] = {}
_events: dict[str, tuple[float, uuid.UUID | None]] = {}


def normalize_host(raw: str | None) -> str | None:
    raw = (raw or "").strip().lower()
    if not raw:
        return None
    if raw.startswith("["):
        end = raw.find("]")
        host = raw[1:end] if end > 0 else ""
    elif raw.count(":") == 1:
        host = raw.split(":", 1)[0]
    else:
        host = raw
    host = host.rstrip(".")
    if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9.:-]+", host):
        return None
    return host


def _scope_header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value.decode("latin-1")
    return None


def is_platform_host(host: str) -> bool:
    # zoikostream.com and its subdomains can never be a customer's (domain_names refuses them),
    # so they are the platform's whatever the configuration says.
    return host in platform_hosts() or host == "zoikostream.com" or host.endswith(".zoikostream.com")


def strict() -> bool:
    """Unknown hostnames are refused once the operator has declared the platform's own."""
    return bool(configured_platform_hosts())


def _lookup_db(host: str) -> HostEntry | None:
    from ..db import SessionLocal
    from ..models import Organization

    db = SessionLocal()
    try:
        row = db.execute(
            select(Organization.id, Organization.domain_status)
            .where(func.lower(Organization.domain) == host)
        ).first()
        return HostEntry(row[0], row[1]) if row else None
    finally:
        db.close()


def lookup(host: str, *, fresh: bool = False) -> HostEntry | None:
    """The organization that has claimed `host`, and that claim's status. Synchronous (DB)."""
    now = time.monotonic()
    if not fresh:
        with _lock:
            hit = _hosts.get(host)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    entry = _lookup_db(host)
    with _lock:
        if len(_hosts) >= _HOST_CACHE_MAX:
            _hosts.clear()
        _hosts[host] = (now, entry)
    return entry


def invalidate(*hosts: str | None) -> None:
    with _lock:
        for host in hosts:
            if host:
                _hosts.pop(host.lower(), None)


def clear_caches() -> None:
    with _lock:
        _hosts.clear()
        _events.clear()


def event_org(event_id: str) -> uuid.UUID | None:
    """The organization an event belongs to. An event never changes organization, so a hit is
    kept; a miss is kept only briefly."""
    now = time.monotonic()
    with _lock:
        hit = _events.get(event_id)
    if hit and (hit[1] is not None or now - hit[0] < CACHE_TTL):
        return hit[1]
    from ..db import SessionLocal
    from ..models import Event

    db = SessionLocal()
    try:
        owner = db.scalar(select(Event.org_id).where(Event.id == uuid.UUID(event_id)))
    finally:
        db.close()
    with _lock:
        if len(_events) >= _EVENT_CACHE_MAX:
            _events.clear()
        _events[event_id] = (now, owner)
    return owner


def match(path: str, method: str, scope_type: str, root_files: frozenset[str]) -> tuple[bool, str | None]:
    """(allowed, event id named in the path) for a request on an active customer hostname."""
    if scope_type == "http" and method in ("GET", "HEAD") and path in root_files:
        return True, None
    for kind, methods, pattern in _ROUTES:
        if kind != scope_type or (methods is not None and method not in methods):
            continue
        m = pattern.match(path)
        if m:
            return True, m.groupdict().get("event")
    return False, None


async def _deny(scope, receive, send) -> None:
    if scope["type"] == "websocket":
        await WebSocketClose(code=4404)(scope, receive, send)
        return
    if (scope.get("path") or "").startswith("/api/"):
        await JSONResponse({"detail": "Not found"}, status_code=404)(scope, receive, send)
        return
    await HTMLResponse(_NOT_FOUND_HTML, status_code=404)(scope, receive, send)


class CustomDomainMiddleware:
    """Outermost middleware (registered last in main.py), so nothing else runs for a request
    that a customer hostname may not make."""

    def __init__(self, app, dist: Path | None = None):
        self.app = app
        self.dist = dist
        self._root_files: frozenset[str] | None = None

    def _root_static(self) -> frozenset[str]:
        # The built SPA's top-level files (favicon, logos, manifest). Listed from disk rather
        # than matched by pattern, so /openapi.json or /docs can never slip through as "static".
        if self._root_files is None:
            files: set[str] = set()
            if self.dist and self.dist.is_dir():
                files = {f"/{p.name}" for p in self.dist.iterdir() if p.is_file() and p.name != "index.html"}
            self._root_files = frozenset(files)
        return self._root_files

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        host = normalize_host(_scope_header(scope, b"host"))
        if path in _ALWAYS or (host is not None and is_platform_host(host)):
            await self.app(scope, receive, send)
            return
        if host is None:
            if strict():
                await _deny(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return

        if path == domain_probe.PATH:
            await self._probe(scope, receive, send, host)
            return

        entry = await asyncio.to_thread(lookup, host)
        if entry is None or entry.status != "active":
            if strict():
                await _deny(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if scope["type"] == "http":
            proto = (_scope_header(scope, b"x-forwarded-proto") or "").split(",")[0].strip().lower()
            if proto == "http":
                query = scope.get("query_string", b"").decode("latin-1")
                target = f"https://{host}{path}" + (f"?{query}" if query else "")
                await RedirectResponse(target, status_code=308)(scope, receive, send)
                return

        allowed, event_id = match(path, method, scope["type"], self._root_static())
        if not allowed:
            await _deny(scope, receive, send)
            return
        if event_id is not None:
            owner = await asyncio.to_thread(event_org, event_id)
            if owner != entry.org_id:
                await _deny(scope, receive, send)
                return
        scope.setdefault("state", {})["custom_domain"] = {"host": host, "org_id": str(entry.org_id)}
        await self.app(scope, receive, send)

    async def _probe(self, scope, receive, send, host: str) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("GET", "HEAD"):
            await _deny(scope, receive, send)
            return
        entry = await asyncio.to_thread(lookup, host, fresh=True)
        if entry is None or entry.status not in ("verified", "active"):
            await _deny(scope, receive, send)
            return
        body = domain_probe.expected_value(host, entry.org_id)
        await PlainTextResponse(body, headers={"Cache-Control": "no-store"})(scope, receive, send)
