"""Where a verified custom hostname gets its certificate and its route into the platform.

Production sits behind Cloudflare (zoikostream.com uses Cloudflare nameservers and
get.zoikostream.com resolves to Cloudflare's proxy), so the first provider is Cloudflare for
SaaS: each verified hostname becomes a Cloudflare *custom hostname* on the platform zone, and
Cloudflare issues and renews its certificate (HTTP domain-control validation succeeds on its
own once the customer's CNAME points at the platform target).

CUSTOM_DOMAIN_PROVIDER selects one:
  "cloudflare"  CLOUDFLARE_ZONE_ID + CLOUDFLARE_API_TOKEN (token scope: Zone > SSL and
                Certificates > Edit, on the platform zone only).
  "external"    TLS and routing are handled by infrastructure outside this app (for example a
                reverse proxy with on-demand certificates). Nothing is created here.
  anything else the feature is unavailable.

No provider's "ready" is trusted on its own: services/domain_probe.py must still reach the
hostname over HTTPS with a valid certificate before a domain becomes Active.

The API token is read from settings at call time, sent only in the Authorization header, and
never logged, stored or returned. Errors surface as short codes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..config import settings

log = logging.getLogger(__name__)

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
TIMEOUT = 10.0

# Cloudflare custom-hostname and certificate states that will not complete without a new
# request (https://developers.cloudflare.com/cloudflare-for-platforms/cloudflare-for-saas/).
_CF_HOSTNAME_FAILED = frozenset({"moved", "deleted", "blocked", "pending_blocked", "pending_deletion",
                                 "test_failed", "test_blocked"})
_CF_SSL_FAILED = frozenset({"expired", "deleted", "inactive", "deactivating", "initializing_timed_out",
                            "validation_timed_out", "issuance_timed_out", "deployment_timed_out",
                            "deletion_timed_out"})


@dataclass(frozen=True)
class ProviderState:
    hostname_id: str | None
    hostname_status: str | None
    certificate_status: str | None
    ready: bool          # hostname active AND certificate issued
    failed: bool         # the provider will not complete this without a new request


class ProviderError(Exception):
    """The provider could not be asked (network, auth, rate limit). Retry later."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ExternalProvider:
    name = "external"

    def ensure(self, hostname: str, hostname_id: str | None = None) -> ProviderState:
        return ProviderState(None, "external", "external", ready=True, failed=False)

    def status(self, hostname: str, hostname_id: str | None) -> ProviderState:
        return self.ensure(hostname, hostname_id)

    def delete(self, hostname_id: str | None) -> None:
        return None


class CloudflareProvider:
    name = "cloudflare"

    def __init__(self, zone_id: str, api_token: str, *, transport: httpx.BaseTransport | None = None):
        self._zone = zone_id
        self._token = api_token
        self._transport = transport

    def _call(self, method: str, path: str, **kw) -> dict:
        url = f"{CLOUDFLARE_API}/zones/{self._zone}{path}"
        try:
            with httpx.Client(timeout=TIMEOUT, transport=self._transport) as client:
                resp = client.request(method, url, headers={"Authorization": f"Bearer {self._token}"}, **kw)
        except httpx.HTTPError as exc:
            log.warning("cloudflare custom-hostname request failed: %s", type(exc).__name__)
            raise ProviderError("provider_unreachable") from None
        if resp.status_code in (401, 403):
            log.error("cloudflare custom-hostname request refused (HTTP %s): check the API token scope",
                      resp.status_code)
            raise ProviderError("provider_auth")
        if resp.status_code == 429:
            raise ProviderError("provider_rate_limited")
        try:
            body = resp.json()
        except ValueError:
            raise ProviderError("provider_error") from None
        body["_http_status"] = resp.status_code
        return body

    @staticmethod
    def _state(result: dict) -> ProviderState:
        hostname_status = result.get("status")
        ssl_status = (result.get("ssl") or {}).get("status")
        return ProviderState(
            hostname_id=result.get("id"),
            hostname_status=hostname_status,
            certificate_status=ssl_status,
            ready=hostname_status == "active" and ssl_status == "active",
            failed=hostname_status in _CF_HOSTNAME_FAILED or ssl_status in _CF_SSL_FAILED,
        )

    def _find(self, hostname: str) -> dict | None:
        body = self._call("GET", "/custom_hostnames", params={"hostname": hostname})
        for item in body.get("result") or []:
            if (item.get("hostname") or "").lower() == hostname:
                return item
        return None

    def status(self, hostname: str, hostname_id: str | None) -> ProviderState:
        if hostname_id:
            body = self._call("GET", f"/custom_hostnames/{hostname_id}")
            result = body.get("result")
            if body.get("success") and result and (result.get("hostname") or "").lower() == hostname:
                return self._state(result)
        found = self._find(hostname)
        if found is None:
            return ProviderState(None, None, None, ready=False, failed=False)
        return self._state(found)

    def ensure(self, hostname: str, hostname_id: str | None = None) -> ProviderState:
        """The custom hostname for `hostname`, created if it does not exist yet. Idempotent:
        an existing record (ours from an earlier attempt) is reused, never duplicated."""
        current = self.status(hostname, hostname_id)
        if current.hostname_id:
            return current
        body = self._call("POST", "/custom_hostnames", json={
            "hostname": hostname,
            "ssl": {"method": "http", "type": "dv", "settings": {"min_tls_version": "1.2"}},
        })
        if body.get("success") and body.get("result"):
            return self._state(body["result"])
        # Created concurrently by another attempt: Cloudflare refuses the duplicate.
        messages = " ".join(str(e.get("message", "")) for e in body.get("errors") or []).lower()
        if body.get("_http_status") == 409 or "duplicate" in messages or "already exists" in messages:
            found = self._find(hostname)
            if found is not None:
                return self._state(found)
        codes = [e.get("code") for e in body.get("errors") or []]
        log.warning("cloudflare refused custom hostname creation (codes=%s)", codes)
        raise ProviderError("provider_rejected")

    def delete(self, hostname_id: str | None) -> None:
        if not hostname_id:
            return
        body = self._call("DELETE", f"/custom_hostnames/{hostname_id}")
        if body.get("success") or body.get("_http_status") == 404:
            return
        raise ProviderError("provider_error")


def get_provider():
    """The configured provider, or None when the feature cannot run."""
    choice = (settings.CUSTOM_DOMAIN_PROVIDER or "").strip().lower()
    if choice == "cloudflare":
        zone, token = settings.CLOUDFLARE_ZONE_ID.strip(), settings.CLOUDFLARE_API_TOKEN.strip()
        return CloudflareProvider(zone, token) if zone and token else None
    if choice == "external":
        return ExternalProvider()
    return None
