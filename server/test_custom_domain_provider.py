"""Certificate provider (services/domain_provider.py) and HTTPS probe (services/domain_probe.py),
against scripted HTTP. No network, no database, and the Cloudflare token never leaves the
Authorization header."""
import json
import ssl

import httpx
import pytest

from app.services import domain_probe, domain_provider
from app.services.domain_provider import CloudflareProvider, ProviderError

HOST = "events.example.com"
TOKEN = "cf-secret-token-value"


def cf(handler):
    return CloudflareProvider("zone123", TOKEN, transport=httpx.MockTransport(handler))


def hostname(id_="ch_1", status="pending", ssl_status="pending_validation", host=HOST):
    return {"id": id_, "hostname": host, "status": status, "ssl": {"status": ssl_status}}


def test_ensure_creates_a_custom_hostname_with_http_dv():
    seen = []

    def handler(req):
        seen.append(req)
        assert req.headers["Authorization"] == f"Bearer {TOKEN}"
        if req.method == "GET":
            return httpx.Response(200, json={"success": True, "result": []})
        body = json.loads(req.content)
        assert body["hostname"] == HOST and body["ssl"]["method"] == "http" and body["ssl"]["type"] == "dv"
        return httpx.Response(200, json={"success": True, "result": hostname()})

    state = cf(handler).ensure(HOST)
    assert (state.hostname_id, state.ready, state.failed) == ("ch_1", False, False)
    assert [r.method for r in seen] == ["GET", "POST"]
    assert seen[1].url.path == "/client/v4/zones/zone123/custom_hostnames"


def test_ensure_reuses_an_existing_hostname_instead_of_duplicating():
    def handler(req):
        assert req.method == "GET"
        return httpx.Response(200, json={"success": True, "result": hostname(status="active", ssl_status="active")})

    state = cf(handler).ensure(HOST, "ch_1")
    assert state.ready and state.hostname_id == "ch_1"


def test_a_concurrent_duplicate_is_resolved_by_lookup():
    calls = {"get": 0}

    def handler(req):
        if req.method == "GET":
            calls["get"] += 1
            result = [] if calls["get"] == 1 else [hostname()]
            return httpx.Response(200, json={"success": True, "result": result})
        return httpx.Response(409, json={"success": False, "errors": [{"code": 1406, "message": "Duplicate custom hostname found."}]})

    assert cf(handler).ensure(HOST).hostname_id == "ch_1"


@pytest.mark.parametrize("status,ssl_status,ready,failed", [
    ("active", "active", True, False),
    ("pending", "pending_validation", False, False),
    ("active", "pending_deployment", False, False),
    ("active", "validation_timed_out", False, True),
    ("blocked", "active", False, True),
    ("moved", "pending_validation", False, True),
])
def test_state_mapping(status, ssl_status, ready, failed):
    state = CloudflareProvider._state(hostname(status=status, ssl_status=ssl_status))
    assert (state.ready, state.failed) == (ready, failed)


def test_auth_failure_is_a_code_and_never_carries_the_token():
    def handler(req):
        return httpx.Response(403, json={"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]})

    with pytest.raises(ProviderError) as exc:
        cf(handler).ensure(HOST)
    assert exc.value.code == "provider_auth"
    assert TOKEN not in str(exc.value)


def test_network_failure_is_retryable():
    def handler(req):
        raise httpx.ConnectError("boom")

    with pytest.raises(ProviderError) as exc:
        cf(handler).status(HOST, "ch_1")
    assert exc.value.code == "provider_unreachable"


def test_delete_tolerates_already_gone():
    def handler(req):
        assert req.method == "DELETE"
        return httpx.Response(404, json={"success": False, "errors": [{"code": 1436, "message": "not found"}]})

    cf(handler).delete("ch_1")
    cf(handler).delete(None)          # nothing to delete, no request


def test_get_provider_fails_closed(monkeypatch):
    s = domain_provider.settings
    monkeypatch.setattr(s, "CUSTOM_DOMAIN_PROVIDER", "")
    assert domain_provider.get_provider() is None
    monkeypatch.setattr(s, "CUSTOM_DOMAIN_PROVIDER", "cloudflare")
    monkeypatch.setattr(s, "CLOUDFLARE_ZONE_ID", "")
    monkeypatch.setattr(s, "CLOUDFLARE_API_TOKEN", "t")
    assert domain_provider.get_provider() is None
    monkeypatch.setattr(s, "CLOUDFLARE_ZONE_ID", "z")
    assert isinstance(domain_provider.get_provider(), CloudflareProvider)
    monkeypatch.setattr(s, "CUSTOM_DOMAIN_PROVIDER", "external")
    assert domain_provider.get_provider().ensure(HOST).ready


# ── HTTPS probe ─────────────────────────────────────────────────────────────────────────

ORG = "6f1c2a3b-0000-4000-8000-000000000001"
PUBLIC = lambda host: ["104.21.1.39"]          # noqa: E731


def test_probe_passes_only_with_this_orgs_value():
    good = domain_probe.expected_value(HOST, ORG)

    def handler(req):
        assert str(req.url) == f"https://{HOST}{domain_probe.PATH}"
        return httpx.Response(200, text=good)

    assert domain_probe.probe(HOST, ORG, transport=httpx.MockTransport(handler), resolve=PUBLIC) is None


def test_probe_rejects_another_orgs_value():
    other = domain_probe.expected_value(HOST, "6f1c2a3b-0000-4000-8000-000000000002")
    t = httpx.MockTransport(lambda req: httpx.Response(200, text=other))
    assert domain_probe.probe(HOST, ORG, transport=t, resolve=PUBLIC) == domain_probe.NOT_ROUTED


def test_probe_rejects_a_redirect_or_error_page():
    t = httpx.MockTransport(lambda req: httpx.Response(301, headers={"Location": "https://elsewhere/"}))
    assert domain_probe.probe(HOST, ORG, transport=t, resolve=PUBLIC) == domain_probe.NOT_ROUTED


def test_probe_reports_an_invalid_certificate():
    def handler(req):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed") from ssl.SSLError()

    t = httpx.MockTransport(handler)
    assert domain_probe.probe(HOST, ORG, transport=t, resolve=PUBLIC) == domain_probe.CERTIFICATE_INVALID


def test_probe_never_connects_to_a_private_address():
    def handler(req):
        raise AssertionError("must not connect")

    t = httpx.MockTransport(handler)
    assert domain_probe.probe(HOST, ORG, transport=t, resolve=lambda h: ["10.0.0.7"]) == domain_probe.FORBIDDEN
    assert domain_probe.probe(HOST, ORG, transport=t, resolve=lambda h: ["169.254.169.254"]) == domain_probe.FORBIDDEN
    assert domain_probe.probe(HOST, ORG, transport=t, resolve=lambda h: []) == domain_probe.UNRESOLVED
