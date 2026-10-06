"""Custom-domain hostname rules, in one place.

Both ways a hostname can be written — the organization's own Settings (PATCH
/organization/domain) and the super-admin organization editor — validate through
`normalize_custom_hostname`, so neither can store something the other would refuse. The
super-admin editor used to accept any text at all ("https://x/path", "javascript:...").

Also the one definition of the platform's own hostnames, which the routing middleware and
the validator must agree on exactly.

No database, no network: importable from schemas and from the ASGI middleware alike.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .config import settings

# A fully-qualified hostname: dot-separated labels of 1-63 letters, digits or hyphens (no
# leading/trailing hyphen), ending in an alphabetic TLD, 253 characters at most. IP addresses,
# single labels ("localhost") and wildcards cannot match.
_HOSTNAME = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

# Special-use names (RFC 6761 / 6762 / 7686 and friends) that can never be a customer's public
# event domain.
_RESERVED_TLDS = frozenset({"localhost", "local", "internal", "invalid", "test", "example", "onion", "arpa"})

_ZOIKO = "zoikostream.com"

# Hosts that always serve the full platform: local development and Starlette's TestClient.
_ALWAYS_PLATFORM = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})


def _host_of(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = "//" + value
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def configured_platform_hosts() -> frozenset[str]:
    """Hostnames an operator explicitly declared as serving the full platform."""
    return frozenset(
        h for h in (_host_of(x) for x in settings.CUSTOM_DOMAIN_PLATFORM_HOSTS.split(",")) if h
    )


def platform_hosts() -> frozenset[str]:
    """Every hostname that serves the full platform: the declared list, APP_URL's host, the
    CORS origins' hosts, and local/test hosts. Exact names only — never suffix matching."""
    hosts = set(_ALWAYS_PLATFORM) | configured_platform_hosts()
    for raw in [settings.APP_URL, *settings.CORS_ORIGINS.split(",")]:
        host = _host_of(raw)
        if host:
            hosts.add(host)
    return frozenset(hosts)


def cname_target() -> str | None:
    target = (settings.CUSTOM_DOMAIN_CNAME_TARGET or "").strip().lower().rstrip(".")
    return target or None


def _is_same_or_under(host: str, parent: str) -> bool:
    return host == parent or host.endswith("." + parent)


def normalize_custom_hostname(value) -> str | None:
    """Return the normalized hostname, None for "no domain", or raise ValueError.

    Format only. Nothing here resolves DNS: ownership is proven later by the TXT record
    (services/custom_domains.py).
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Domain must be text.")
    host = value.strip().lower().rstrip(".")
    if not host:
        return None
    if "://" in host or any(ch in host for ch in "/:@?# \\"):
        raise ValueError("Enter the hostname only, like events.yourcompany.com — no https://, path or port.")
    if "*" in host:
        raise ValueError("Wildcard domains aren't supported. Enter one hostname, like events.yourcompany.com.")
    if not _HOSTNAME.match(host):
        raise ValueError("Enter a valid hostname, like events.yourcompany.com.")
    if host.rsplit(".", 1)[-1] in _RESERVED_TLDS:
        raise ValueError("That domain is reserved and can't be used. Enter a domain your organization owns.")
    reserved = [_ZOIKO, *platform_hosts()]
    target = cname_target()
    if target:
        reserved.append(target)
    if any(_is_same_or_under(host, r) for r in reserved if "." in r):
        raise ValueError("Use a domain your organization owns, not a ZoikoStream address.")
    return host
