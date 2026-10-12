"""External adapter layer (ZST-ARCH-PUB-GUIDE-001 §10–11).

Leads go to the platform's EXISTING public contact flow — POST /api/contact, which emails the
configured sales inbox — over HTTP, not by importing platform code. That keeps the Guide's
trust boundary intact: it can do exactly what any anonymous visitor's browser can do, nothing
more.

Outcome mapping (the whole point of this module — never report a success that was not
confirmed, PR-027):
  202 from /api/contact            -> confirmed  (the platform accepted the enquiry)
  4xx/5xx response                 -> failed     (positively not accepted)
  connect error (nothing was sent) -> failed
  timeout / connection lost AFTER the request was written -> unknown (may have arrived)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .settings import get_settings

log = logging.getLogger(__name__)


@dataclass
class AdapterOutcome:
    outcome: str            # confirmed | failed | unknown
    reason: str | None = None


class ContactFlowAdapter:
    def submit(self, payload: dict, *, visitor_ip: str | None) -> AdapterOutcome:
        s = get_settings()
        headers = {"Content-Type": "application/json", "User-Agent": "zoikostream-guide/1.0"}
        # The contact route rate-limits per client IP. Forwarding the visitor's address keeps
        # that a per-visitor limit instead of one budget shared by every Guide lead.
        if visitor_ip:
            headers["X-Forwarded-For"] = visitor_ip
        try:
            res = httpx.post(s.GUIDE_CONTACT_ENDPOINT, json=payload, headers=headers,
                             timeout=httpx.Timeout(s.GUIDE_CONTACT_TIMEOUT_SECONDS, connect=3.0))
        except httpx.ConnectError:
            log.error("guide lead adapter: contact endpoint unreachable")
            return AdapterOutcome("failed", "unreachable")
        except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError):
            log.error("guide lead adapter: outcome unknown (timeout after send)")
            return AdapterOutcome("unknown", "timeout")
        if res.status_code == 202:
            return AdapterOutcome("confirmed")
        log.warning("guide lead adapter: contact endpoint answered %s", res.status_code)
        return AdapterOutcome("failed", "rate_limited" if res.status_code == 429
                              else f"http_{res.status_code}")


_adapter: ContactFlowAdapter | None = None


def lead_adapter() -> ContactFlowAdapter:
    global _adapter
    if _adapter is None:
        _adapter = ContactFlowAdapter()
    return _adapter


def set_lead_adapter(adapter) -> None:
    """Test seam: swap in a fake adapter."""
    global _adapter
    _adapter = adapter
