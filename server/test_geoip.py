import pytest
from app.config import settings
from app.services import geoip


def test_geoip_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "GEOIP_ENABLED", False)
    # Even if client sends proxy country headers, they must be ignored when GeoIP is disabled
    assert geoip.resolve_country("203.0.113.1", {"cf-ipcountry": "US"}) is None
    assert geoip.resolve_country("203.0.113.1") is None
    assert geoip.resolve_country(None) is None


def test_spoofed_headers_rejected_when_proxy_headers_untrusted(monkeypatch):
    monkeypatch.setattr(settings, "GEOIP_ENABLED", True)
    monkeypatch.setattr(settings, "GEOIP_TRUST_PROXY_HEADERS", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)

    # Must reject spoofed headers since GEOIP_TRUST_PROXY_HEADERS is False
    assert geoip.resolve_country("203.0.113.1", {"cf-ipcountry": "US"}) is None
    assert geoip.resolve_country("203.0.113.1", {"x-country-code": "GB"}) is None


def test_spoofed_headers_rejected_when_hops_zero(monkeypatch):
    monkeypatch.setattr(settings, "GEOIP_ENABLED", True)
    monkeypatch.setattr(settings, "GEOIP_TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)  # Direct connection

    # When connecting directly, headers cannot be trusted
    assert geoip.resolve_country("203.0.113.1", {"cf-ipcountry": "US"}) is None


def test_spoofed_headers_rejected_with_private_ip(monkeypatch):
    monkeypatch.setattr(settings, "GEOIP_ENABLED", True)
    monkeypatch.setattr(settings, "GEOIP_TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)

    # Request from private/loopback peer must not trust client headers
    assert geoip.resolve_country("127.0.0.1", {"cf-ipcountry": "US"}) is None
    assert geoip.resolve_country("192.168.1.5", {"cf-ipcountry": "US"}) is None
    assert geoip.resolve_country(None, {"cf-ipcountry": "US"}) is None


def test_trusted_proxy_headers_accepted_when_fully_configured(monkeypatch):
    monkeypatch.setattr(settings, "GEOIP_ENABLED", True)
    monkeypatch.setattr(settings, "GEOIP_TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)

    # Valid public IP + trusted proxy configuration
    public_ip = "8.8.8.8"
    assert geoip.resolve_country(public_ip, {"cf-ipcountry": "US"}) == "US"
    assert geoip.resolve_country(public_ip, {"cf-ipcountry": "gb"}) == "GB"
    assert geoip.resolve_country(public_ip, {"x-country-code": "DE"}) == "DE"
    assert geoip.resolve_country(public_ip, {"cloudfront-viewer-country": "FR"}) == "FR"

    # Reserved or invalid country codes
    assert geoip.resolve_country(public_ip, {"cf-ipcountry": "XX"}) is None
    assert geoip.resolve_country(public_ip, {"cf-ipcountry": "T1"}) is None
    assert geoip.resolve_country(public_ip, {"cf-ipcountry": "ZZ"}) is None
    assert geoip.resolve_country(public_ip, {"cf-ipcountry": "INVALID"}) is None
    assert geoip.resolve_country(public_ip, {"cf-ipcountry": ""}) is None


def test_private_and_loopback_ips_ignored(monkeypatch):
    monkeypatch.setattr(settings, "GEOIP_ENABLED", True)
    monkeypatch.setattr(settings, "GEOIP_DATABASE_PATH", "/nonexistent/path.mmdb")

    for private_ip in [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.100",
        "172.16.0.1",
        "169.254.1.1",
        "::1",
        "fc00::1",
        "not-an-ip",
        None,
    ]:
        assert geoip.resolve_country(private_ip) is None


def test_missing_database_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "GEOIP_ENABLED", True)
    monkeypatch.setattr(settings, "GEOIP_DATABASE_PATH", "/missing/file.mmdb")
    # Must fail safely and return None without raising
    assert geoip.resolve_country("203.0.113.1") is None
