"""Sliding-window rate limiting: one algorithm, two call shapes.

`SlidingWindow` is the primitive. `rate_limit(...)` wraps it as a FastAPI dependency
keyed by client IP, for the unauthenticated endpoints where guessing is the attack
(login, password reset). `routers/live.py` uses the primitive directly, per socket.

ponytail: per-PROCESS counters, not Redis. That is the right shape for the WebSocket
limiter (the budget belongs to one socket) but it means the HTTP limits below are
per-worker: N workers = N x limit. Good enough to turn credential stuffing from
"unbounded" into "slow"; move `_HITS` into Redis (INCR + EXPIRE on the same key) when
the deployment actually runs several workers and the limit has to be global.
"""

from __future__ import annotations

import time
from collections import deque

from fastapi import Depends, HTTPException, Request, status

from . import security


class SlidingWindow:
    """Allow at most `limit` hits in any `window` seconds. Not thread-safe: callers are
    a single event loop (the dependency) or one socket task (live.py)."""

    __slots__ = ("limit", "window", "hits")

    def __init__(self, limit: int, window: float):
        self.limit, self.window, self.hits = limit, window, deque()

    def prune(self) -> None:
        """Drop timestamps that fell out of the window. Does NOT record a hit."""
        now = time.monotonic()
        while self.hits and now - self.hits[0] > self.window:
            self.hits.popleft()

    def allow(self) -> bool:
        self.prune()
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(time.monotonic())
        return True

    def empty(self) -> bool:
        """True when no hit is still inside the window — the entry can be evicted.
        Call prune() first; this only reads state."""
        return not self.hits


# key -> window. Keys are "<scope>:<ip>", so two endpoints don't share one budget.
_HITS: dict[str, SlidingWindow] = {}
# Sweep interval for keys whose window has fully drained. Without this, one request per
# unique IP grows the dict forever (a slow memory leak behind any proxy or scanner).
_SWEEP_SECONDS = 300.0
_last_sweep = time.monotonic()


def _sweep() -> None:
    global _last_sweep
    now = time.monotonic()
    if now - _last_sweep < _SWEEP_SECONDS:
        return
    _last_sweep = now
    stale = []
    for key, bucket in _HITS.items():
        bucket.prune()
        if bucket.empty():
            stale.append(key)
    for key in stale:
        _HITS.pop(key, None)


def client_ip(request: Request) -> str:
    return security.client_ip(request) or "unknown"


def rate_limit(scope: str, limit: int, window: float = 60.0):
    """Dependency factory: `Depends(rate_limit("login", 10, 60))` → 429 past the limit.

    Applied to unauthenticated endpoints only. Authenticated abuse is already bounded by
    the per-socket limiter and by needing a valid token.
    """

    def _dep(request: Request) -> None:
        _sweep()
        key = f"{scope}:{client_ip(request)}"
        bucket = _HITS.get(key)
        if bucket is None:
            bucket = _HITS[key] = SlidingWindow(limit, window)
        if not bucket.allow():
            # ZST-EC-001 DEV-010. The refusal is ALSO counted in a shared, durable counter
            # so a governed threshold can be evidenced across workers and across restarts.
            # The in-memory window above stays the hot-path enforcement; this is the
            # accounting a notification may legitimately be driven from, because an email
            # must never describe one process's private opinion.
            try:
                from .services import api_usage

                api_usage.record_refusal(client_ip(request), scope)
            except Exception:  # noqa: BLE001 — accounting must never break enforcement
                pass
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many attempts. Please wait a moment and try again.",
                headers={"Retry-After": str(int(window))},
            )

    return Depends(_dep)


if __name__ == "__main__":
    # Self-check: the window actually closes, and each scope keeps its own budget.
    w = SlidingWindow(3, 60.0)
    assert [w.allow() for _ in range(4)] == [True, True, True, False], "limit not enforced"
    assert not w.empty(), "hits inside the window must keep the entry alive"

    short = SlidingWindow(1, 0.01)
    assert short.allow() and not short.allow(), "second hit inside window must fail"
    time.sleep(0.02)
    assert short.allow(), "window did not expire"
    assert SlidingWindow(1, 0.01).empty(), "a fresh window is empty"

    # prune() must not consume budget — the bug that makes a sweep eat a user's attempts.
    p = SlidingWindow(1, 60.0)
    for _ in range(5):
        p.prune()
    assert p.allow(), "prune() wrongly recorded a hit"

    # A drained window is evictable; a live one is not.
    drained = SlidingWindow(1, 0.01)
    drained.allow()
    time.sleep(0.02)
    drained.prune()
    assert drained.empty(), "drained window should be evictable"
    print("ok")
