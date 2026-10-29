"""Optional, privacy-preserving GeoIP enrichment for viewer audience reach.

Privacy & Security Posture:
- Never persists or logs raw IP addresses.
- Coarse geographic resolution only: ISO 3166-1 alpha-2 country codes.
- Operates disabled by default (GEOIP_ENABLED=False).
- When GEOIP_ENABLED=True, trusted reverse proxy headers (e.g. CF-IPCountry)
  are ONLY consulted if explicitly enabled (GEOIP_TRUST_PROXY_HEADERS=True),
  TRUSTED_PROXY_HOPS > 0, and the client address is an authentic non-private IP.
  Untrusted clients cannot spoof country headers.
- Graceful fallback: timeouts and provider errors fail closed (returning None)
  and never interrupt viewer join or playback.
- Private, loopback, multicast, or invalid IP addresses are immediately ignored.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Mapping

from ..config import settings
from ..countries import COUNTRY_NAMES

log = logging.getLogger(__name__)

_ISO2_RE = re.compile(r"^[A-Z]{2}$")

# Cloudflare pseudo-codes or reserved codes that do not represent countries
_RESERVED_CODES = {"XX", "T1", "A1", "A2", "O1", "ZZ"}


def _is_valid_iso2(code: str | None) -> bool:
    if not code or not isinstance(code, str):
        return False
    norm = code.strip().upper()
    if norm in _RESERVED_CODES:
        return False
    return norm in COUNTRY_NAMES


def resolve_country(
    client_ip: str | None,
    headers: Mapping[str, str] | None = None,
) -> str | None:
    """Best-effort resolution of a 2-letter ISO country code.

    Returns an uppercase 2-letter ISO country code if trustworthy, or None.
    Never throws exceptions.
    """
    try:
        # Check if GeoIP is enabled; disabled by default produces an accurate unmeasured state
        if not getattr(settings, "GEOIP_ENABLED", False):
            return None

        if not client_ip:
            return None

        # Defend against private, invalid, or unresolvable IP addresses
        try:
            parsed = ipaddress.ip_address(client_ip.strip())
            if (
                parsed.is_private
                or parsed.is_loopback
                or parsed.is_reserved
                or parsed.is_multicast
                or parsed.is_link_local
            ):
                return None
        except ValueError:
            return None

        # 1. Trusted reverse proxy headers (Cloudflare, CloudFront, Fastly)
        # ONLY trusted when explicitly enabled, behind a trusted proxy, and not direct
        trusted_hops = getattr(settings, "TRUSTED_PROXY_HOPS", 1)
        trust_proxy_headers = getattr(settings, "GEOIP_TRUST_PROXY_HEADERS", False)
        if trust_proxy_headers and trusted_hops > 0 and headers:
            h = {k.lower(): v for k, v in headers.items()}
            # Cloudflare
            cf_country = h.get("cf-ipcountry")
            if _is_valid_iso2(cf_country):
                return cf_country.strip().upper()
            # Fastly / CloudFront / Nginx
            x_country = h.get("x-country-code") or h.get("cloudfront-viewer-country")
            if _is_valid_iso2(x_country):
                return x_country.strip().upper()

        # 2. Local offline GeoIP database lookup (MaxMind mmdb)
        db_path = getattr(settings, "GEOIP_DATABASE_PATH", "").strip()
        if not db_path:
            return None

        try:
            import maxminddb  # type: ignore

            with maxminddb.open_database(db_path) as reader:
                record = reader.get(str(parsed))
                if record and isinstance(record, dict):
                    code = (
                        record.get("country", {}).get("iso_code")
                        or record.get("registered_country", {}).get("iso_code")
                    )
                    if _is_valid_iso2(code):
                        return code.strip().upper()
        except ImportError:
            log.debug("GeoIP database configured but maxminddb library not installed.")
        except Exception:
            log.warning("GeoIP lookup failed unexpectedly", exc_info=False)

        return None
    except Exception:
        return None
