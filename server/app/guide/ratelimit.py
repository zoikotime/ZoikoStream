"""Guide rate limiting (API §15; ARCH §6.1; Security/Privacy §21) with RateLimit-* headers.

Backends:
  * Redis (GUIDE_REDIS_URL, else the platform's REDIS_URL): fixed windows via INCR+EXPIRE,
    shared by every worker and instance, under a per-environment key namespace (ARCH §14.1).
    The Guide talks to Redis with redis-py directly and its own short timeouts — it never
    imports the platform's bus.
  * In-memory sliding windows: the automatic fallback when Redis is unset, unreachable or
    slow (ARCH §6.1 lets Redis loss degrade to exactly this). Each fallback is counted in
    guide_ratelimit_fallback_total, and Redis is retried after REDIS_RETRY_AFTER seconds.

Limits are configuration twice over: env settings give the defaults, and the runtime config
row `guide.rate_limits` ({"<scope>": <limit>, ...}) overrides them within registry.CONFIG_TTL
on every instance, without a deploy (Security/Privacy §33 S5 "tighten rate limits").

Keys are pseudonymous: the client IP is hashed before it becomes a key, so the limiter never
holds a raw network identifier (Security/Privacy §21.1). The client IP itself comes from
client_ip(), which trusts X-Forwarded-For only as far as GUIDE_TRUSTED_PROXY_HOPS says.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request

from . import metrics
from .errors import GuideProblem
from .settings import get_settings

log = logging.getLogger(__name__)

_BUCKETS: dict[str, deque] = {}
_SWEEP_SECONDS = 300.0
_last_sweep = time.monotonic()
_mem_lock = threading.Lock()

RUNTIME_CONFIG_KEY = "guide.rate_limits"
REDIS_RETRY_AFTER = 30.0

_redis_lock = threading.Lock()
_redis_client = None
_redis_url_in_use: str | None = None
_redis_down_until = 0.0


@dataclass
class Verdict:
    allowed: bool
    limit: int
    remaining: int
    reset_after: int

    def headers(self) -> dict[str, str]:
        h = {"RateLimit-Limit": str(self.limit), "RateLimit-Remaining": str(self.remaining),
             "RateLimit-Reset": str(int(time.time()) + self.reset_after)}
        if not self.allowed:
            h["Retry-After"] = str(self.reset_after)
        return h


# ── Client identity ────────────────────────────────────────────────────────────────────────

def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.startswith("[") and "]" in value:      # "[2001:db8::1]:443"
        value = value[1:value.index("]")]
    elif value.count(":") == 1:                       # "203.0.113.7:443"
        value = value.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    """The visitor's IP, or None when it cannot be established.

    GUIDE_TRUSTED_PROXY_HOPS = 0: the socket peer only; X-Forwarded-For is ignored because
    any client can write it. N > 0: the Nth entry from the RIGHT of X-Forwarded-For — the
    address the outermost of N trusted proxies saw. A header with fewer than N entries did not
    pass through all of them, so the socket peer is used instead. The result is always a
    syntactically valid IP address (never arbitrary header text)."""
    peer = _valid_ip(request.client.host if request.client else None)
    hops = get_settings().GUIDE_TRUSTED_PROXY_HOPS
    if hops <= 0:
        return peer
    entries = [e.strip() for h in request.headers.getlist("x-forwarded-for")
               for e in h.split(",") if e.strip()]
    if len(entries) < hops:
        return peer
    return _valid_ip(entries[-hops]) or peer


def client_key(request: Request) -> str:
    ip = client_ip(request) or "?"
    return hashlib.sha256(f"zs-guide-rl\x00{ip}".encode()).hexdigest()[:24]


# ── Backends ───────────────────────────────────────────────────────────────────────────────

def _sweep(window: float) -> None:
    global _last_sweep
    now = time.monotonic()
    if now - _last_sweep < _SWEEP_SECONDS:
        return
    _last_sweep = now
    for key in [k for k, q in _BUCKETS.items() if not q or now - q[-1] > window * 2]:
        _BUCKETS.pop(key, None)


def _memory_hit(key: str, limit: int, window: float) -> Verdict:
    with _mem_lock:
        _sweep(window)
        now = time.monotonic()
        q = _BUCKETS.setdefault(key, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            reset = max(1, math.ceil(window - (now - q[0])))
            return Verdict(False, limit, 0, reset)
        q.append(now)
        reset = max(1, math.ceil(window - (now - q[0])))
        return Verdict(True, limit, limit - len(q), reset)


def redis_url() -> str:
    s = get_settings()
    return (s.GUIDE_REDIS_URL or s.REDIS_URL).strip()


def _redis():
    """The shared client, or None while Redis is unset or in its post-failure back-off."""
    global _redis_client, _redis_url_in_use
    url = redis_url()
    if not url or time.monotonic() < _redis_down_until:
        return None
    with _redis_lock:
        if _redis_client is None or _redis_url_in_use != url:
            import redis  # deferred: only multi-instance deployments need it

            t = get_settings().GUIDE_REDIS_TIMEOUT_SECONDS
            _redis_client = redis.Redis.from_url(
                url, socket_connect_timeout=t, socket_timeout=t, retry_on_timeout=False,
                health_check_interval=30,
                # RESP2: INCR/EXPIRE need nothing newer, and redis-py 8's default RESP3
                # HELLO handshake fails against older servers (Redis < 6).
                protocol=2)
            _redis_url_in_use = url
        return _redis_client


def _namespace() -> str:
    env = get_settings().ENVIRONMENT.strip().lower() or "unknown"
    return f"zsg:{env}:rl:"


def _redis_hit(client, key: str, limit: int, window: float) -> Verdict:
    w = max(1, int(window))
    now = time.time()
    slot = int(now // w)
    rkey = f"{_namespace()}{key}:{slot}"
    pipe = client.pipeline(transaction=True)
    pipe.incr(rkey)
    pipe.expire(rkey, w + 1)
    count = int(pipe.execute()[0])
    reset = max(1, math.ceil((slot + 1) * w - now))
    if count > limit:
        return Verdict(False, limit, 0, reset)
    return Verdict(True, limit, max(0, limit - count), reset)


def _redis_failed(exc: Exception) -> None:
    global _redis_down_until
    _redis_down_until = time.monotonic() + REDIS_RETRY_AFTER
    metrics.incr("guide_ratelimit_fallback_total", reason="redis_error")
    # Class name only: a connection error string can carry the URL with its password.
    log.warning("guide rate limiter: Redis unavailable (%s); using in-process windows for "
                "%ss", type(exc).__name__, int(REDIS_RETRY_AFTER))


def hit(key: str, limit: int, window: float = 60.0) -> Verdict:
    client = _redis()
    if client is not None:
        try:
            verdict = _redis_hit(client, key, limit, window)
            metrics.incr("guide_ratelimit_checks_total", backend="redis",
                         outcome="allowed" if verdict.allowed else "limited")
            return verdict
        except Exception as exc:  # noqa: BLE001 — any Redis fault degrades, never fails
            _redis_failed(exc)
    elif redis_url():
        metrics.incr("guide_ratelimit_fallback_total", reason="backoff")
    verdict = _memory_hit(key, limit, window)
    metrics.incr("guide_ratelimit_checks_total", backend="memory",
                 outcome="allowed" if verdict.allowed else "limited")
    return verdict


def backend() -> str:
    """"redis" or "memory" — what the next check would use (health/readiness)."""
    return "redis" if _redis() is not None else "memory"


def reset_all() -> None:
    global _redis_client, _redis_url_in_use, _redis_down_until
    with _mem_lock:
        _BUCKETS.clear()
    with _redis_lock:
        _redis_client, _redis_url_in_use, _redis_down_until = None, None, 0.0


# ── Runtime-changeable limits ──────────────────────────────────────────────────────────────

def effective_limit(scope: str, default: int) -> int:
    """The runtime override for `scope` from guide_runtime_config, else the env default.
    Reads through registry's TTL cache; an unreachable database keeps the env default."""
    try:
        from . import registry
        from .db import new_session

        db = new_session()
        try:
            overrides = registry.runtime_config(db).get(RUNTIME_CONFIG_KEY)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — config lookup must never break a request
        return default
    value = overrides.get(scope) if isinstance(overrides, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def enforce(request: Request, scope: str, key: str, limit: int, window: float = 60.0) -> Verdict:
    limit = effective_limit(scope, limit)
    verdict = hit(f"{scope}:{key}", limit, window)
    request.state.guide_ratelimit = verdict
    if not verdict.allowed:
        metrics.incr("guide_rate_limited_total", scope=scope)
        _mark_session_limited(request, key)
        raise GuideProblem("GUIDE_RATE_LIMITED",
                           # WF-16 / CF §23 exact copy; thresholds are never revealed.
                           "You have sent several requests in a short period. Please wait a moment and try again.",
                           headers=verdict.headers())
    return verdict


def _mark_session_limited(request: Request, key: str) -> None:
    """CF §23: a session-scoped limit puts the session in RATE_LIMITED. sessions.load_session
    registers the authenticated session on the request; any limit keyed by that session id
    (the per-session request limit, the messages limit, lead submit) marks it. The state
    returns to ACTIVE (or the paused flow's state) on the first request the window allows."""
    bound = getattr(request.state, "guide_session_binding", None)
    if not bound:
        return
    db, session = bound
    if str(session.session_id) != key:
        return
    from .sessions import mark_rate_limited

    try:
        mark_rate_limited(db, request, session)
    except Exception:  # noqa: BLE001 — bookkeeping must not mask the 429
        log.exception("guide rate limiter: could not record RATE_LIMITED state")
        db.rollback()
