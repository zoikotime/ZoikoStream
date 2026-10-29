"""External adapter layer (ZST-ARCH-PUB-GUIDE-001 §10–11).

Leads go to the platform's EXISTING public contact flow — POST /api/contact, which emails the
configured sales inbox — over HTTP, not by importing platform code. That keeps the Guide's
trust boundary intact: it can do exactly what any anonymous visitor's browser can do, nothing
more. That route is served by this same process (main.py mounts the Guide into the platform
app), so unless GUIDE_CONTACT_ENDPOINT says otherwise a lead is posted over loopback to the port
its own request arrived on (own_contact_endpoint).

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

from . import breaker
from .settings import get_settings

log = logging.getLogger(__name__)


@dataclass
class AdapterOutcome:
    outcome: str            # confirmed | failed | unknown
    reason: str | None = None


CONTACT_PATH = "/api/contact"


def own_contact_endpoint(server) -> str | None:
    """The platform's contact route on this same server, over loopback.

    `server` is the ASGI scope's (host, port): the local end of the socket the visitor's request
    arrived on, so its port is the one this process is listening on — $PORT on Cloud Run, the
    API's own port behind a VM's reverse proxy, whatever port it was started on locally. No port
    is assumed anywhere.

    The host is always loopback, never the socket's own address: custom-domain routing serves the
    platform for loopback hosts (domain_names._ALWAYS_PLATFORM), whereas a container's own IP
    would be looked up as a customer's domain. None when the socket has no port (a Unix socket),
    which needs GUIDE_CONTACT_ENDPOINT.
    """
    if not server or len(server) != 2 or not server[1]:
        return None
    host = "[::1]" if ":" in str(server[0]) else "127.0.0.1"
    return f"http://{host}:{int(server[1])}{CONTACT_PATH}"


class ContactFlowAdapter:
    def submit(self, payload: dict, *, visitor_ip: str | None,
               server: tuple | None = None) -> AdapterOutcome:
        s = get_settings()
        endpoint = s.GUIDE_CONTACT_ENDPOINT.strip() or own_contact_endpoint(server)
        if not endpoint:
            # Nothing was sent, so this is a definite failure (retryable), not an unknown.
            log.error("guide lead adapter: no contact endpoint (GUIDE_CONTACT_ENDPOINT is unset "
                      "and the API is not listening on a TCP port)")
            return AdapterOutcome("failed", "unconfigured")
        headers = {"Content-Type": "application/json", "User-Agent": "zoikostream-guide/1.0"}
        # The contact route rate-limits per client IP. Forwarding the visitor's address keeps
        # that a per-visitor limit instead of one budget shared by every Guide lead.
        if visitor_ip:
            headers["X-Forwarded-For"] = visitor_ip
        # Architecture §17.1: an open breaker means nothing is sent, so the outcome is a
        # positive "failed" (never "unknown") and the visitor gets the direct contact route.
        b = breaker.get("contact")
        if not b.allow():
            log.warning("guide lead adapter: contact breaker open; not sending")
            return AdapterOutcome("failed", "circuit_open")
        try:
            res = httpx.post(endpoint, json=payload, headers=headers,
                             timeout=httpx.Timeout(s.GUIDE_CONTACT_TIMEOUT_SECONDS, connect=3.0))
        except httpx.ConnectError:
            log.error("guide lead adapter: contact endpoint unreachable")
            b.failure()
            return AdapterOutcome("failed", "unreachable")
        except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError):
            log.error("guide lead adapter: outcome unknown (timeout after send)")
            b.failure()
            return AdapterOutcome("unknown", "timeout")
        # A 4xx is the endpoint working and declining; only 5xx counts against the breaker.
        if res.status_code >= 500:
            b.failure()
        else:
            b.success()
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
