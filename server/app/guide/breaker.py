"""Per-dependency circuit breakers (ZST-ARCH-PUB-GUIDE-001 §17.1).

closed -> (N consecutive failures) -> open -> (cool-down elapsed) -> half_open
half_open: one trial call; success closes, failure re-opens.

While open, `allow()` is False and callers take their degraded path immediately instead of
spending a full timeout on a dependency that is known to be down.

    b = breaker.get("model")
    if not b.allow():
        return degraded()
    try:
        result = call()
    except Exception:
        b.failure()
        raise
    b.success()
"""
from __future__ import annotations

import threading
import time

from . import metrics

DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_COOLDOWN_SECONDS = 30.0


class CircuitOpen(Exception):
    """Raised by `guard` when the breaker refuses a call."""


class Breaker:
    def __init__(self, name: str, failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
                 cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        with self._lock:
            state = self._state_locked()
            if state == "closed":
                return True
            if state == "half_open" and not self._trial_in_flight:
                self._trial_in_flight = True
                return True
            metrics.incr("guide_breaker_rejections_total", dependency=self.name)
            return False

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._trial_in_flight = False

    def failure(self) -> None:
        with self._lock:
            self._trial_in_flight = False
            self._failures += 1
            if self._opened_at is not None or self._failures >= self.failure_threshold:
                if self._opened_at is None:
                    metrics.incr("guide_breaker_opened_total", dependency=self.name)
                self._opened_at = time.monotonic()

    def guard(self, fn, *args, **kwargs):
        """Synchronous helper: run fn under the breaker, raising CircuitOpen when refused."""
        if not self.allow():
            raise CircuitOpen(self.name)
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.failure()
            raise
        self.success()
        return result


_registry: dict[str, Breaker] = {}
_registry_lock = threading.Lock()


def get(name: str, **kwargs) -> Breaker:
    with _registry_lock:
        b = _registry.get(name)
        if b is None:
            b = _registry[name] = Breaker(name, **kwargs)
        return b


def states() -> dict[str, str]:
    with _registry_lock:
        return {name: b.state for name, b in _registry.items()}


def reset() -> None:
    """Tests only."""
    with _registry_lock:
        _registry.clear()
