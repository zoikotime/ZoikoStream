"""Custom-domain rules with no database: hostname validation (shared by the organization's
Settings and the super-admin editor), the central event-URL builder, and the routing
middleware's host and path matching."""
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import domain_names
from app.schemas.admin import OrgCreate, OrgUpdate
from app.schemas.organization import OrgDomainUpdate
from app.services import custom_domain_routing as routing
from app.services import public_urls


@pytest.fixture(autouse=True)
def platform(monkeypatch):
    s = domain_names.settings
    monkeypatch.setattr(s, "APP_URL", "https://get.zoikostream.com")
    monkeypatch.setattr(s, "CORS_ORIGINS", "https://get.zoikostream.com")
    monkeypatch.setattr(s, "CUSTOM_DOMAIN_PLATFORM_HOSTS", "get.zoikostream.com,zoikostream.com,app.zoikotech.com")
    monkeypatch.setattr(s, "CUSTOM_DOMAIN_CNAME_TARGET", "cname.zoikostream.com")


# ── hostname validation ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("events.example.com", "events.example.com"),
    ("live.example.org", "live.example.org"),
    ("Events.Example.COM.", "events.example.com"),
    ("  stream-test.example.co.uk ", "stream-test.example.co.uk"),
    ("", None),
    (None, None),
])
def test_valid_and_normalized(raw, expected):
    assert domain_names.normalize_custom_hostname(raw) == expected


@pytest.mark.parametrize("bad", [
    "https://events.example.com", "events.example.com/watch", "events.example.com?x=1",
    "events.example.com:8443", "user@events.example.com", "10.0.0.1", "[::1]", "localhost",
    "*.example.com", "a..b.com", "-bad.example.com", "x" * 250 + ".com",
    "events.example.local", "events.test", "zoikostream.com", "get.zoikostream.com",
    "cname.zoikostream.com", "anything.app.zoikotech.com", "app.zoikotech.com", "javascript:alert(1)",
])
def test_rejected(bad):
    with pytest.raises(ValueError):
        domain_names.normalize_custom_hostname(bad)


@pytest.mark.parametrize("schema", [OrgDomainUpdate, OrgUpdate])
def test_org_and_super_admin_editors_share_the_rule(schema):
    assert schema(domain="Events.Example.COM.").domain == "events.example.com"
    for bad in ("https://evil.example/path?x=1", "get.zoikostream.com", "not a domain"):
        with pytest.raises(ValidationError):
            schema(domain=bad)


def test_super_admin_create_validates_too():
    assert OrgCreate(name="Acme", domain="Live.Example.org").domain == "live.example.org"
    with pytest.raises(ValidationError):
        OrgCreate(name="Acme", domain="javascript:alert(1)")


def test_platform_hosts_are_exact_names():
    hosts = domain_names.platform_hosts()
    assert {"get.zoikostream.com", "zoikostream.com", "app.zoikotech.com", "localhost", "testserver"} <= hosts
    assert "events.example.com" not in hosts


# ── central URL builder ─────────────────────────────────────────────────────────────────

EVENT = "5e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b"


def org(status, enabled=None, domain="events.customer.com"):
    return SimpleNamespace(domain=domain, domain_status=status,
                           custom_domain_enabled=status == "active" if enabled is None else enabled)


def test_active_custom_domain_is_used():
    assert public_urls.event_watch_url(EVENT, org("active")) == f"https://events.customer.com/events/{EVENT}/watch"


@pytest.mark.parametrize("status", ["pending_dns", "verifying", "verified", "failed", "disabled", "not_configured"])
def test_every_other_state_falls_back_to_app_url(status):
    assert public_urls.event_watch_url(EVENT, org(status)) == f"https://get.zoikostream.com/events/{EVENT}/watch"


def test_no_domain_or_no_org_falls_back():
    assert public_urls.event_watch_url(EVENT, org("active", domain=None)).startswith("https://get.zoikostream.com/")
    assert public_urls.event_watch_url(EVENT, None) == f"https://get.zoikostream.com/events/{EVENT}/watch"


def test_active_status_without_the_enabled_mirror_is_not_trusted():
    assert public_urls.event_watch_url(EVENT, org("active", enabled=False)).startswith("https://get.zoikostream.com/")


def test_invitation_and_access_links_use_the_builder():
    from app.services import invitation_links
    assert invitation_links.invitation_url(EVENT, "s3cret", org("active")) == \
        f"https://events.customer.com/events/{EVENT}/watch#invite=s3cret"
    assert invitation_links.access_link_url(EVENT, "s3cret", org("pending_dns")) == \
        f"https://get.zoikostream.com/events/{EVENT}/watch#link=s3cret"


# ── routing: host normalization and the public-route allowlist ──────────────────────────

@pytest.mark.parametrize("raw,host", [
    ("Events.Example.com", "events.example.com"), ("events.example.com:443", "events.example.com"),
    ("events.example.com.", "events.example.com"), ("[::1]:8000", "::1"), ("", None),
    ("evil.com\r\nx", None), ("a b.com", None),
])
def test_normalize_host(raw, host):
    assert routing.normalize_host(raw) == host


ROOT = frozenset({"/favicon.ico", "/logo.png"})


@pytest.mark.parametrize("path,method,kind,allowed,event", [
    (f"/events/{EVENT}/watch", "GET", "http", True, EVENT),
    (f"/events/{EVENT}/watch/", "GET", "http", True, EVENT),
    (f"/api/events/{EVENT}/watch", "GET", "http", True, EVENT),
    (f"/api/events/{EVENT}/register", "POST", "http", True, EVENT),
    (f"/api/events/{EVENT}/invitation", "POST", "http", True, EVENT),
    (f"/api/live/events/{EVENT}/ws", "GET", "websocket", True, EVENT),
    ("/assets/index-abc123.js", "GET", "http", True, None),
    ("/favicon.ico", "GET", "http", True, None),
    # never reachable on a customer hostname
    ("/", "GET", "http", False, None),
    ("/login", "GET", "http", False, None),
    ("/organization/settings", "GET", "http", False, None),
    ("/admin/dashboard", "GET", "http", False, None),
    ("/api/organization/domain", "GET", "http", False, None),
    ("/api/admin/organizations", "GET", "http", False, None),
    ("/api/auth/login", "POST", "http", False, None),
    (f"/api/events/{EVENT}", "GET", "http", False, None),
    (f"/api/events/{EVENT}/registrations", "GET", "http", False, None),
    (f"/api/events/{EVENT}/watch", "DELETE", "http", False, None),
    ("/openapi.json", "GET", "http", False, None),
    ("/docs", "GET", "http", False, None),
    ("/e/abc", "GET", "http", False, None),
    (f"/events/{EVENT}/watch", "GET", "websocket", False, None),
])
def test_custom_domain_route_allowlist(path, method, kind, allowed, event):
    assert routing.match(path, method, kind, ROOT) == (allowed, event if allowed else None)


def test_strict_only_once_platform_hosts_are_declared(monkeypatch):
    assert routing.strict()
    monkeypatch.setattr(domain_names.settings, "CUSTOM_DOMAIN_PLATFORM_HOSTS", "")
    assert not routing.strict()


def test_probe_value_is_bound_to_host_and_org():
    from app.services import domain_probe
    a, b = uuid.uuid4(), uuid.uuid4()
    assert domain_probe.expected_value("events.example.com", a) != domain_probe.expected_value("events.example.com", b)
    assert domain_probe.expected_value("events.example.com", a) != domain_probe.expected_value("live.example.com", a)
