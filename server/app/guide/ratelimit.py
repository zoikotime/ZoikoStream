"""Guide rate limiting (API §15) with the standard RateLimit-* headers.

ponytail: per-process sliding windows, same posture as app/ratelimit.py (which is not imported
— it pulls in app.security and the platform models). With N workers the effective limit is
N x limit. Move the buckets to Redis (INCR+EXPIRE) when the Guide runs multi-instance; the
architecture allows Redis loss to degrade to exactly this behaviour.

Keys are pseudonymous: the client IP is HMAC'd before it becomes a dict key, so the limiter
never holds a raw network identifier longer than one request (Security/Privacy §21.1).
"""
from __future__ import annotations

import hashlib
import math
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request

from .errors import GuideProblem

_BUCKETS: dict[str, deque] = {}
_SWEEP_SECONDS = 300.0
_last_sweep = time.monotonic()


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


def _sweep(window: float) -> None:
    global _last_sweep
    now = time.monotonic()
    if now - _last_sweep < _SWEEP_SECONDS:
        return
    _last_sweep = now
    for key in [k for k, q in _BUCKETS.items() if not q or now - q[-1] > window * 2]:
        _BUCKETS.pop(key, None)


def hit(key: str, limit: int, window: float = 60.0) -> Verdict:
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


def reset_all() -> None:
    _BUCKETS.clear()


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
    return hashlib.sha256(f"zs-guide-rl\x00{ip}".encode()).hexdigest()[:24]


def enforce(request: Request, scope: str, key: str, limit: int, window: float = 60.0) -> Verdict:
    verdict = hit(f"{scope}:{key}", limit, window)
    request.state.guide_ratelimit = verdict
    if not verdict.allowed:
        raise GuideProblem("GUIDE_RATE_LIMITED",
                           # WF-16 / CF §23 exact copy; thresholds are never revealed.
                           "You have sent several requests in a short period. Please wait a moment and try again.",
                           headers=verdict.headers())
    return verdict
