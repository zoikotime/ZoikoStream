"""The last gate before a custom domain is Active: fetch it over HTTPS, for real.

DNS records and a provider's "certificate active" are each only part of the path. This makes
one request to https://<hostname>/.well-known/zoikostream-domain-check and requires:

  * the name resolves to a public address (never a private, loopback or metadata one), and
  * the TLS handshake succeeds with a certificate valid for that hostname (httpx verifies
    by default; nothing here turns that off), and
  * the answer is the HMAC of (hostname, organization) that only this platform can compute,
    which proves the request reached THIS app and that its host lookup maps the hostname to
    the organization being activated (services/custom_domain_routing.py serves it).

A plain-HTTP-only hostname, a certificate for another name, or a proxy that routes the name
somewhere else all fail here, so none of them can become Active.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import ssl

import httpx

from ..config import settings
from .webhook_security import _is_forbidden_address, resolve_addresses

log = logging.getLogger(__name__)

PATH = "/.well-known/zoikostream-domain-check"
TIMEOUT = 8.0

UNRESOLVED = "probe_unresolved"
FORBIDDEN = "probe_forbidden_address"
CERTIFICATE_INVALID = "certificate_invalid"
UNREACHABLE = "probe_unreachable"
NOT_ROUTED = "probe_not_routed"


def expected_value(hostname: str, org_id) -> str:
    msg = f"custom-domain-probe:{hostname.lower()}:{org_id}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def _is_cert_error(exc: BaseException) -> bool:
    seen = exc
    for _ in range(5):
        if seen is None:
            break
        if isinstance(seen, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(seen):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def probe(hostname: str, org_id, *, transport: httpx.BaseTransport | None = None,
          resolve=resolve_addresses) -> str | None:
    """None when the hostname serves this organization over valid HTTPS, else a code."""
    addresses = resolve(hostname)
    if not addresses:
        return UNRESOLVED
    for address in addresses:
        try:
            if _is_forbidden_address(ipaddress.ip_address(address)):
                return FORBIDDEN
        except ValueError:
            continue
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=transport) as client:
            resp = client.get(f"https://{hostname}{PATH}", headers={"Cache-Control": "no-cache"})
    except httpx.HTTPError as exc:
        if _is_cert_error(exc):
            return CERTIFICATE_INVALID
        log.info("custom-domain probe failed: %s", type(exc).__name__)
        return UNREACHABLE
    if resp.status_code != 200:
        return NOT_ROUTED
    body = resp.text[:200].strip()
    if not hmac.compare_digest(body.encode(), expected_value(hostname, org_id).encode()):
        return NOT_ROUTED
    return None
