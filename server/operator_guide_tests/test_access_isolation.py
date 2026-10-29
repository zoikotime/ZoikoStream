"""Authority and tenant isolation (§9, §16): Guide never shows a user more than the platform
would, and pins every read to the caller's own organization — super_admin included."""
from conftest import BASE, blocks_of


def test_requires_platform_authentication(client):
    assert client.get(f"{BASE}/bootstrap").status_code in (401, 403)
    res = client.get(f"{BASE}/bootstrap", headers={"Authorization": "Bearer not-a-token"})
    assert res.status_code == 401


def test_viewers_and_speakers_are_refused(client, world):
    org = world.org()
    for role in ("viewer", "speaker"):
        res = client.get(f"{BASE}/bootstrap", headers=world.headers(world.user(org, role)))
        assert res.status_code == 403
        assert res.json()["code"] == "OPGUIDE_FORBIDDEN"


def test_hosts_and_admins_are_admitted(client, world):
    org = world.org()
    for role in ("host", "org_admin"):
        res = client.get(f"{BASE}/bootstrap", headers=world.headers(world.user(org, role)))
        assert res.status_code == 200, res.text
        assert res.json()["context"]["organization"]["id"] == str(org.id)


def test_other_tenants_event_is_indistinguishable_from_missing(client, world, ask):
    mine, theirs = world.org(), world.org()
    admin = world.user(mine)
    other_ev = world.event(theirs, world.user(theirs), title="Their Secret Launch")
    reply = ask(world.headers(admin), "Is this event ready?", event_id=str(other_ev.id),
                status=404)
    assert reply == {"code": "OPGUIDE_NOT_FOUND",
                     "message": "I can't find that event in your organization."}


def test_super_admin_is_pinned_to_own_organization(client, world, ask):
    home, tenant = world.org(), world.org()
    staff = world.user(home, "super_admin")
    tenant_ev = world.event(tenant, world.user(tenant), title="Tenant Board Meeting")
    ask(world.headers(staff), "Is this event ready?", event_id=str(tenant_ev.id), status=404)
    # Naming the other tenant's event in text does not resolve it either.
    reply = ask(world.headers(staff), "Is Tenant Board Meeting ready?")
    assert "Tenant Board Meeting" not in str(reply["message"]["payload"]["blocks"])


def test_route_context_from_another_tenant_is_silently_dropped(client, world):
    mine, theirs = world.org(), world.org()
    other_ev = world.event(theirs, world.user(theirs))
    res = client.get(f"{BASE}/bootstrap", params={"route": f"/organization/events/{other_ev.id}"},
                     headers=world.headers(world.user(mine)))
    assert res.status_code == 200 and res.json()["context"]["event"] is None


def test_context_event_list_is_tenant_scoped(client, world):
    mine, theirs = world.org(), world.org()
    admin = world.user(mine)
    world.event(mine, admin, title="Ours")
    world.event(theirs, world.user(theirs), title="Theirs")
    items = client.get(f"{BASE}/context/events", headers=world.headers(admin)).json()["items"]
    assert [i["title"] for i in items] == ["Ours"]


def test_conversations_are_private_to_their_owner(client, world, ask):
    org = world.org()
    alice, bob = world.user(org), world.user(org)
    first = ask(world.headers(alice), "hello")
    cid = first["conversation_id"]
    hb = world.headers(bob)
    assert client.get(f"{BASE}/conversations/{cid}", headers=hb).status_code == 404
    assert client.get(f"{BASE}/conversations", headers=hb).json()["items"] == []
    assert client.delete(f"{BASE}/conversations/{cid}", headers=hb).status_code == 404
    ask(hb, "hello", conversation_id=cid, status=404)


def test_host_reads_but_cannot_change_events_they_do_not_run(client, world, ask):
    org = world.org()
    admin = world.user(org)
    host = world.user(org, "host")
    ev = world.event(org, admin, title="Admin Event")
    h = world.headers(host)
    reply = ask(h, "Is this event ready?", event_id=str(ev.id))
    assert blocks_of(reply, "readiness")
    reply = ask(h, "turn off chat", event_id=str(ev.id))
    assert not blocks_of(reply, "proposal")
    assert "only events you created or are assigned to host" in reply["message"]["text"]


def test_assigned_host_can_propose_changes(client, world, ask):
    org = world.org()
    admin = world.user(org)
    host = world.user(org, "host")
    ev = world.event(org, admin)
    world.assign(ev, host)
    reply = ask(world.headers(host), "turn off chat", event_id=str(ev.id))
    assert blocks_of(reply, "proposal")


def test_developer_details_are_admin_only(client, world, ask):
    org = world.org()
    reply = ask(world.headers(world.user(org, "host")), "show our api keys and webhooks")
    assert reply["message"]["text"] == ("You do not have permission to view API credentials "
                                        "and webhooks.")
