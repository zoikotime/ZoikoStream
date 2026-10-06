"""Requests on a customer hostname, through the real app (services/custom_domain_routing.py).

    events.customer-a.com/api/events/<A's event>/watch     -> served
    events.customer-a.com/api/events/<B's event>/watch     -> 404 (page, API and socket alike)
    events.customer-a.com/api/organization/...  /admin  /login  /api/auth  -> 404
    a saved-but-not-active hostname                          -> 404
    an unknown hostname, once platform hosts are declared    -> 404
    the platform's own hostnames                             -> unchanged
"""
import pytest
from starlette.websockets import WebSocketDisconnect

from app.main import DIST
from app.services import custom_domain_routing as routing
from app.services import custom_domains, domain_probe
from custom_domain_support import activate, client_for, feature, fresh, host, world  # noqa: F401


@pytest.fixture
def live(world, feature):
    """A and B each with an ACTIVE custom domain."""
    world.host_a, world.host_b = host(), host()
    activate(world, feature, world.a, world.host_a)
    activate(world, feature, world.b, world.host_b)
    return world


def on(hostname, user=None):
    return client_for(user, host_header=hostname)


def register(c, event_id):
    return c.post(f"/api/events/{event_id}/register", json={"name": "Visitor"})


def test_platform_hosts_are_unchanged(live):
    c = client_for()                                          # Host: testserver
    assert c.get(f"/api/events/{live.event_a.id}/watch").status_code == 200
    assert c.get(f"/api/events/{live.event_b.id}/watch").status_code == 200
    assert on("get.zoikostream.com").get(f"/api/events/{live.event_b.id}/watch").status_code == 200


def test_an_active_domain_serves_its_own_events(live):
    c = on(live.host_a)
    assert c.get(f"/api/events/{live.event_a.id}/watch").status_code == 200
    r = register(c, live.event_a.id)
    assert r.status_code == 200 and r.json()["token"]


def test_another_organizations_event_is_not_found_on_this_domain(live):
    c = on(live.host_a)
    assert c.get(f"/api/events/{live.event_b.id}/watch").status_code == 404
    assert register(c, live.event_b.id).status_code == 404
    assert c.post(f"/api/events/{live.event_b.id}/invitation", json={"kind": "invite", "secret": "x"}).status_code == 404
    # ...and B's own domain serves it.
    assert on(live.host_b).get(f"/api/events/{live.event_b.id}/watch").status_code == 200


def test_the_live_socket_is_refused_for_another_organizations_event(live):
    with pytest.raises(WebSocketDisconnect) as exc:
        with on(live.host_a).websocket_connect(f"/api/live/events/{live.event_b.id}/ws"):
            pass
    assert exc.value.code == 4404


def test_the_live_socket_works_for_its_own_event(live):
    token = register(on(live.host_a), live.event_a.id).json()["token"]
    with on(live.host_a).websocket_connect(f"/api/live/events/{live.event_a.id}/ws?reg={token}") as ws:
        first = ws.receive_json()
        assert first.get("type") != "error"


@pytest.mark.parametrize("method,path", [
    ("get", "/api/organization/domain"),
    ("get", "/api/organization/overview"),
    ("get", "/api/admin/organizations"),
    ("get", "/api/events"),
    ("post", "/api/auth/login"),
    ("get", "/login"),
    ("get", "/organization/settings"),
    ("get", "/admin/dashboard"),
    ("get", "/"),
    ("get", "/openapi.json"),
    ("get", "/docs"),
])
def test_management_surfaces_are_not_reachable_on_a_customer_domain(live, method, path):
    c = on(live.host_a, live.admin_a)                         # even with a valid admin token
    assert getattr(c, method)(path).status_code == 404


def test_event_management_api_is_not_reachable_even_for_own_events(live):
    c = on(live.host_a, live.admin_a)
    assert c.get(f"/api/events/{live.event_a.id}").status_code == 404
    assert c.get(f"/api/events/{live.event_a.id}/registrations").status_code == 404
    assert c.delete(f"/api/events/{live.event_a.id}").status_code == 404


@pytest.mark.skipif(not DIST.is_dir(), reason="needs the built SPA (client/dist)")
def test_the_viewer_page_is_served_in_custom_domain_mode(live):
    r = on(live.host_a).get(f"/events/{live.event_a.id}/watch")
    assert r.status_code == 200
    assert '<meta name="zk-host-mode" content="custom-domain">' in r.text
    assert 'name="zk-platform-origin"' in r.text
    assert on(live.host_a).get(f"/events/{live.event_b.id}/watch").status_code == 404
    # The platform's own page is untouched.
    assert "zk-host-mode" not in client_for().get(f"/events/{live.event_a.id}/watch").text


def test_a_saved_but_not_active_hostname_serves_nothing(world, feature):
    h = host()
    client_for(world.admin_a).patch("/api/organization/domain", json={"domain": h})
    assert on(h).get(f"/api/events/{world.event_a.id}/watch").status_code == 404
    feature.publish(h, fresh(world, world.a).domain_verification_token)
    client_for(world.admin_a).post("/api/organization/domain/verify")      # verified, cert pending
    assert fresh(world, world.a).domain_status == "verified"
    assert on(h).get(f"/api/events/{world.event_a.id}/watch").status_code == 404


def test_unknown_hostnames_are_refused_once_platform_hosts_are_declared(live, monkeypatch):
    stranger = host()
    assert on(stranger).get(f"/api/events/{live.event_a.id}/watch").status_code == 404
    monkeypatch.setattr(custom_domains.settings, "CUSTOM_DOMAIN_PLATFORM_HOSTS", "")
    assert on(stranger).get(f"/api/events/{live.event_a.id}/watch").status_code == 200


def test_before_the_feature_is_configured_nothing_changes_for_non_active_rows(world, feature, monkeypatch):
    """Production today: no CUSTOM_DOMAIN_PLATFORM_HOSTS. A legacy row that names some hostname
    (the old super-admin editor accepted anything) must not take that hostname offline."""
    h = host()
    client_for(world.admin_a).patch("/api/organization/domain", json={"domain": h})   # pending_dns
    monkeypatch.setattr(custom_domains.settings, "CUSTOM_DOMAIN_PLATFORM_HOSTS", "")
    routing.clear_caches()
    assert on(h).get(f"/api/events/{world.event_a.id}/watch").status_code == 200


def test_zoikostream_hostnames_are_always_the_platform(live):
    for name in ("zoikostream.com", "www.zoikostream.com", "cname.zoikostream.com"):
        assert on(name).get(f"/api/events/{live.event_b.id}/watch").status_code == 200, name


@pytest.mark.parametrize("make", [
    lambda h: f"x.{h}",                 # a subdomain of an active domain is not that domain
    lambda h: f"{h}.evil.com",          # nor is a name that merely starts with it
    lambda h: h.replace("events-", "events-x"),
])
def test_matching_is_exact_never_by_suffix(live, make):
    assert on(make(live.host_a)).get(f"/api/events/{live.event_a.id}/watch").status_code == 404


def test_host_header_case_port_and_trailing_dot_still_match(live):
    for variant in (live.host_a.upper(), f"{live.host_a}:443", f"{live.host_a}."):
        assert on(variant).get(f"/api/events/{live.event_a.id}/watch").status_code == 200


def test_plain_http_is_redirected_to_https(live):
    r = on(live.host_a).get(f"/api/events/{live.event_a.id}/watch?x=1",
                            headers={"X-Forwarded-Proto": "http"}, follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == f"https://{live.host_a}/api/events/{live.event_a.id}/watch?x=1"


def test_health_checks_answer_on_any_host(live):
    assert on("10.0.0.5").get("/health").status_code == 200


def test_the_probe_answers_only_for_its_own_organization(live, feature):
    r = on(live.host_a).get(domain_probe.PATH)
    assert r.status_code == 200 and r.text == domain_probe.expected_value(live.host_a, live.a.id)
    assert r.text != domain_probe.expected_value(live.host_a, live.b.id)
    pending = host()
    client_for(live.admin_a).patch("/api/organization/domain", json={"domain": pending})
    assert on(pending).get(domain_probe.PATH).status_code == 404


def test_removal_stops_serving_immediately(live):
    assert on(live.host_a).get(f"/api/events/{live.event_a.id}/watch").status_code == 200
    client_for(live.admin_a).delete("/api/organization/domain")
    assert on(live.host_a).get(f"/api/events/{live.event_a.id}/watch").status_code == 404


def test_a_reassigned_hostname_maps_to_its_new_owner(live, feature):
    """A removes its hostname, B claims and activates it: B's events are served, A's are not,
    with no stale cache entry pointing the hostname at A."""
    h = live.host_a
    assert routing.lookup(h).org_id == live.a.id
    client_for(live.admin_a).delete("/api/organization/domain")
    client_for(live.admin_b).delete("/api/organization/domain")
    activate(live, feature, live.b, h)
    assert on(h).get(f"/api/events/{live.event_b.id}/watch").status_code == 200
    assert on(h).get(f"/api/events/{live.event_a.id}/watch").status_code == 404
