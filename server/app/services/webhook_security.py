"""Webhook endpoint verification and outbound-request safety (ZST-EC-001 DEV-006).

Two problems this module exists to fix, both found in the audit:

  * **Unverified endpoints received production events.** `enqueue` gated only on `enabled`,
    so any URL an administrator typed started receiving real customer payloads immediately —
    a typo, a stale address, or a URL pointing somewhere it should not. Verification is now
    a purpose-bound challenge the endpoint has to echo back, and the delivery gate checks
    it.

  * **No SSRF protection at all.** The URL column was `min_length=1` with no scheme check.
    An org admin could register `http://169.254.169.254/...` and have the platform sign and
    POST to cloud metadata on their behalf.

The address check runs TWICE, deliberately: once at registration for fast feedback, and
again immediately before each delivery. Validating once and trusting forever is exactly what
DNS rebinding defeats — a hostname that resolved publicly at registration can resolve to a
private address later.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import secrets
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from ..config import settings
from ..models import VERIFICATION_TTL_HOURS

log = logging.getLogger(__name__)

# Attempts allowed against one challenge before it must be reset.
MAX_VERIFICATION_ATTEMPTS = 10

# Header the endpoint must echo, and the header carrying the challenge on the probe.
CHALLENGE_HEADER = "X-Zoiko-Webhook-Challenge"

ALLOWED_SCHEMES = ("https", "http")


class UnsafeWebhookUrl(ValueError):
    """The URL is not a permissible outbound destination."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_challenge(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_challenge() -> tuple[str, str, datetime]:
    """(raw, hash, expires_at). The raw value is returned once and never stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_challenge(raw), _now() + timedelta(hours=VERIFICATION_TTL_HOURS)


# ── outbound address safety ─────────────────────────────────────────────────────────────

def _is_forbidden_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Every range an outbound webhook must never reach.

    `is_private` alone is not enough - it misses link-local (which is where cloud metadata
    lives at 169.254.169.254), and misses IPv6 unique-local and the v4-mapped forms an
    attacker can use to smuggle a private v4 address through a v6 literal.
    """
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_private          # RFC1918 + IPv6 unique-local
        or ip.is_loopback
        or ip.is_link_local    # includes 169.254.169.254 cloud metadata
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolve_addresses(host: str) -> list[str]:
    """Every address a hostname currently resolves to. Empty when it does not resolve."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return []
    return sorted({info[4][0] for info in infos})


def validate_webhook_url(url: str, *, require_https: bool | None = None) -> str:
    """Return the normalized URL, or raise UnsafeWebhookUrl.

    Called at registration AND again before each delivery attempt - see module docstring on
    DNS rebinding.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeWebhookUrl("A webhook URL is required.")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeWebhookUrl("Webhook URLs must use https (http is permitted only outside "
                               "production).")

    # HTTPS is mandatory in production: a signed payload sent over plaintext is still a
    # customer payload sent over plaintext.
    if require_https is None:
        require_https = settings.ENVIRONMENT == "production"
    if require_https and scheme != "https":
        raise UnsafeWebhookUrl("Webhook URLs must use https.")

    host = parsed.hostname
    if not host:
        raise UnsafeWebhookUrl("The webhook URL has no host.")
    if parsed.username or parsed.password:
        raise UnsafeWebhookUrl("Webhook URLs must not embed credentials.")

    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost") or lowered.endswith(".local"):
        raise UnsafeWebhookUrl("Webhook URLs must not point at the local machine.")

    # A literal address is checked directly; a hostname is checked against every address it
    # currently resolves to, so a name that resolves to a private address is refused too.
    try:
        literal = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        literal = None

    candidates = [literal] if literal is not None else []
    if literal is None:
        resolved = resolve_addresses(host)
        if not resolved:
            raise UnsafeWebhookUrl("The webhook host could not be resolved.")
        for address in resolved:
            try:
                candidates.append(ipaddress.ip_address(address))
            except ValueError:
                continue

    for candidate in candidates:
        if _is_forbidden_address(candidate):
            # The specific address is deliberately not echoed back - it is an internal
            # detail of the platform's network, and naming it turns this into a scanner.
            raise UnsafeWebhookUrl(
                "Webhook URLs must point at a public internet address. Private, loopback, "
                "link-local and metadata addresses are not permitted.")
    return raw


def is_deliverable(endpoint) -> tuple[bool, str | None]:
    """Whether a production event may be delivered to this endpoint right now.

    The single boundary every sender consults. Returns (ok, reason) so a caller can record
    WHY something was withheld rather than silently doing nothing.
    """
    from ..models import WEBHOOK_DISABLED, WEBHOOK_VERIFIED

    if endpoint is None:
        return False, "endpoint_missing"
    if not endpoint.enabled:
        return False, "endpoint_disabled_by_customer"
    if endpoint.status == WEBHOOK_DISABLED:
        return False, "endpoint_disabled_for_failures"
    if endpoint.status != WEBHOOK_VERIFIED:
        return False, "endpoint_not_verified"
    return True, None
