"""Super Admin on the Command Center: platform-wide answers and links this role can open."""
from datetime import timedelta

from app.models.live import BroadcastSession
from app.operator_guide import platform_scope
from app.operator_guide.sources import now
from conftest import blocks_of

ADMIN_ROUTE = "/admin/dashboard"


def _super_admin(world):
    return world.user(world.org(), role="super_admin")


def _hrefs(reply: dict) -> set[str]:
    return {a["href"] for a in reply["message"]["payload"]["actions"]}


def _metric(reply: dict, label: str) -> str:
    card = blocks_of(reply, "metric")[0]
    return next(m["value"] for m in card["metrics"] if m["label"] == label)


def test_analytics_reads_the_whole_platform_not_the_admins_own_org(world, ask, db):
    other = world.org()
    host = world.user(other)
    ev = world.event(other, host, status="ended")
    db.add(BroadcastSession(event_id=ev.id, org_id=other.id, status="ended",
                            started_at=now() - timedelta(hours=3),
                            ended_at=now() - timedelta(hours=1)))
    db.commit()
    reply = ask(world.headers(_super_admin(world)), "How did our events perform?",
                route=ADMIN_ROUTE)
    assert blocks_of(reply, "metric")[0]["scope"].startswith("Platform-wide")
    assert int(_metric(reply, "Completed broadcasts")) >= 1
    assert float(_metric(reply, "Streaming hours").split()[0]) >= 2.0
    assert "/admin/analytics" in _hrefs(reply)


def test_upcoming_events_lists_other_organizations_events(world, ask):
    other = world.org()
    # The list shows the 8 soonest events and the test database is shared: start before the rest.
    ev = world.event(other, world.user(other), title="Platform Gala Night",
                     start_time=now() + timedelta(minutes=1))
    reply = ask(world.headers(_super_admin(world)), "Show upcoming events", route=ADMIN_ROUTE)
    rows = [r["label"] for b in blocks_of(reply, "status") for r in b["rows"]]
    assert ev.title in rows
    assert "/admin/live-events" in _hrefs(reply)


def test_support_cases_are_counted_platform_wide_and_link_to_admin_support(world, ask):
    reply = ask(world.headers(_super_admin(world)), "Show open support cases", route=ADMIN_ROUTE)
    assert "organization(s)" in reply["message"]["text"]
    assert _hrefs(reply) == {"/admin/support"}


def test_attention_includes_the_platform_health_limitations(world, ask):
    reply = ask(world.headers(_super_admin(world)), "What needs attention?", route=ADMIN_ROUTE)
    items = blocks_of(reply, "attention")[0]["items"]
    limits = [i for i in items if i["kind"] == "platform_limits"]
    assert limits and "not integrated" in limits[0]["detail"]
    assert limits[0]["href"] == "/admin/status"


def test_organization_admins_keep_organization_scope_and_links(world, ask):
    org = world.org()
    admin = world.user(org)
    reply = ask(world.headers(admin), "How did our events perform?",
                route="/organization/analytics")
    assert blocks_of(reply, "metric")[0]["scope"].startswith("Your organization")
    assert "/organization/analytics" in _hrefs(reply)
    support = ask(world.headers(admin), "Show open support cases", route="/organization/support")
    assert _hrefs(support) == {"/organization/support"}


def test_super_admin_links_never_point_at_organization_console_routes(world, ask):
    admin = _super_admin(world)
    for question in ("How did our events perform?", "Show open support cases",
                     "Show our API keys and webhooks", "Show upcoming events"):
        reply = ask(world.headers(admin), question, route=ADMIN_ROUTE)
        assert not [h for h in _hrefs(reply) if h.startswith("/organization")], question


def test_console_href_maps_only_super_admins(world):
    sa = type("A", (), {"role": "super_admin"})()
    org = type("A", (), {"role": "org_admin"})()
    assert platform_scope.console_href(sa, "/organization/events/abc") == "/admin/live-events/abc"
    assert platform_scope.console_href(sa, "/organization/recordings") == "/admin/media"
    assert platform_scope.console_href(sa, "/status") == "/status"
    assert platform_scope.console_href(org, "/organization/analytics") == "/organization/analytics"


def test_super_admin_welcome_gets_platform_tiles_and_others_do_not(world, client, ask):
    from conftest import BASE
    sa = client.get(f"{BASE}/bootstrap", headers=world.headers(_super_admin(world)),
                    params={"route": ADMIN_ROUTE}).json()
    tiles = {t["id"]: t for t in sa["platform"]}
    assert set(tiles) == {"health", "live", "support"}
    assert tiles["health"]["ask"] == "Is ZoikoStream down?"
    assert tiles["support"]["value"].isdigit()
    org_admin = client.get(f"{BASE}/bootstrap", headers=world.headers(world.user(world.org()))).json()
    assert org_admin["platform"] is None
