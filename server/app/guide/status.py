"""D1 public-status adapter (Conversation Flow §17; RAG-AT-09; PG-AT-13).

"Is ZoikoStream down?" is answered from the PUBLIC status feed the status page itself renders
(GUIDE_STATUS_ENDPOINT, the platform's GET /api/status — published records only), with its
freshness. Guide never reads platform tables: it calls the same public URL a visitor's
browser would. Any failure, timeout or unexpected shape returns None and the caller says the
status cannot be verified — Guide never guesses a status and never infers an outage from a
single visitor's symptom.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .settings import get_settings

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 2.5
CACHE_SECONDS = 30.0
MAX_INCIDENTS = 2

_cache: tuple[float, "PublicStatus"] | None = None
_lock = threading.Lock()


@dataclass(frozen=True)
class PublicStatus:
    overall_label: str
    incidents: list[tuple[str, str]]       # (title, status) — published fields only
    maintenance: int                       # count of scheduled/in-progress windows
    checked_at: datetime

    def summary(self) -> str:
        when = self.checked_at.strftime("%I:%M %p UTC").lstrip("0")
        text = f"The public status page currently reports: {self.overall_label} (checked {when})."
        if self.incidents:
            items = "; ".join(f"{t} ({s.replace('_', ' ')})" for t, s in self.incidents)
            text += f" Active incident{'s' if len(self.incidents) > 1 else ''}: {items}."
        if self.maintenance:
            text += (f" {self.maintenance} maintenance window{'s are' if self.maintenance > 1 else ' is'}"
                     " scheduled or in progress.")
        return text + " The status page has the latest updates."


def _parse(data) -> PublicStatus | None:
    if not isinstance(data, dict) or not isinstance(data.get("overall_label"), str):
        return None
    incidents = []
    for inc in (data.get("active_incidents") or [])[:MAX_INCIDENTS]:
        if isinstance(inc, dict) and isinstance(inc.get("title"), str):
            incidents.append((inc["title"][:120], str(inc.get("status") or "investigating")[:24]))
    maintenance = data.get("scheduled_maintenance") or []
    return PublicStatus(data["overall_label"][:60], incidents,
                        len(maintenance) if isinstance(maintenance, list) else 0,
                        datetime.now(timezone.utc))


def current() -> PublicStatus | None:
    """The current public status (cached briefly), or None when it cannot be verified."""
    global _cache
    url = get_settings().GUIDE_STATUS_ENDPOINT
    if not url:
        return None
    with _lock:
        if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
            return _cache[1]
    try:
        import httpx
        res = httpx.get(url, timeout=TIMEOUT_SECONDS, headers={"Accept": "application/json"})
        status = _parse(res.json()) if res.status_code == 200 else None
    except Exception as exc:
        log.warning("guide public status unavailable: %s", type(exc).__name__)
        return None
    if status is not None:
        with _lock:
            _cache = (time.monotonic(), status)
    return status
