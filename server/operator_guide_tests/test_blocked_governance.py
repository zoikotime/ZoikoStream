"""R5/R6 are never executed from chat (§10), and organization governance is enforced by the
server (§6.17): availability, capability switches, the Super Admin kill switch."""
from sqlalchemy import func, select

from app.models.feature_flag import FeatureFlag
from app.operator_guide.schema import Proposal
from conftest import BASE, blocks_of


def test_go_live_routes_to_console_with_readiness_snapshot(world, ask, db):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin)
    reply = ask(world.headers(admin), "go live now", event_id=str(ev.id))
    text = reply["message"]["text"]
    assert text.startswith("I can't do that from Guide. Starting a broadcast is done in")
    assert blocks_of(reply, "blocker") and blocks_of(reply, "readiness")
    assert {"type": "navigate", "label": "Open host console",
            "href": f"/host/dashboard?event={ev.id}"} in reply["message"]["payload"]["actions"]
    assert db.scalar(select(func.count(Proposal.id)).where(Proposal.org_id == org.id)) == 0


def test_destructive_and_secret_requests_are_refused(world, ask, db):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin)
    h = world.headers(admin)
    for text in ("delete the recording", "show me the stream key", "rotate the api key",
                 "end the broadcast", "make Sam an admin"):
        reply = ask(h, text, event_id=str(ev.id))
        assert reply["message"]["text"].startswith("I can't do that from Guide."), text
        assert not blocks_of(reply, "proposal")
    assert db.scalar(select(func.count(Proposal.id)).where(Proposal.org_id == org.id)) == 0


def test_capability_switch_is_enforced_server_side(client, world, ask):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin)
    h = world.headers(admin)
    res = client.put(f"{BASE}/settings", headers=h, json={"capabilities": {"actions": False}})
    assert res.status_code == 200
    reply = ask(h, "turn off chat", event_id=str(ev.id))
    assert not blocks_of(reply, "proposal")
    assert "turned off for this organization" in reply["message"]["text"]
    client.put(f"{BASE}/settings", headers=h, json={"capabilities": {"actions": True}})
    assert blocks_of(ask(h, "turn off chat", event_id=str(ev.id)), "proposal")


def test_capability_turned_off_after_proposal_blocks_confirmation(client, world, ask):
    org = world.org()
    admin = world.user(org)
    ev = world.event(org, admin)
    h = world.headers(admin)
    p = blocks_of(ask(h, "turn off chat", event_id=str(ev.id)), "proposal")[0]
    client.put(f"{BASE}/settings", headers=h, json={"capabilities": {"actions": False}})
    res = client.post(f"{BASE}/proposals/{p['id']}/confirm",
                      headers={**h, "Idempotency-Key": "cap-off-key-1"})
    assert res.status_code == 403 and res.json()["code"] == "OPGUIDE_ENTITLEMENT_BLOCKED"


def test_org_can_turn_guide_off_and_admin_can_turn_it_back_on(client, world, ask):
    org = world.org()
    admin = world.user(org)
    host = world.user(org, "host")
    h = world.headers(admin)
    assert client.put(f"{BASE}/settings", headers=h, json={"enabled": False}).status_code == 200
    ask(h, "hello", status=503)
    assert client.get(f"{BASE}/bootstrap", headers=world.headers(host)).status_code == 503
    settings = client.get(f"{BASE}/settings", headers=h).json()
    assert settings["enabled"] is False and settings["editable"] is True
    assert client.put(f"{BASE}/settings", headers=h, json={"enabled": True}).status_code == 200
    ask(h, "hello")


def test_only_org_admins_change_settings_or_read_audit(client, world):
    org = world.org()
    h = world.headers(world.user(org, "host"))
    res = client.put(f"{BASE}/settings", headers=h, json={"enabled": False})
    assert res.status_code == 403
    assert client.get(f"{BASE}/audit", headers=h).status_code == 403
    assert client.get(f"{BASE}/settings", headers=h).json()["editable"] is False


def test_unknown_capability_is_rejected(client, world):
    h = world.headers(world.user(world.org()))
    res = client.put(f"{BASE}/settings", headers=h, json={"capabilities": {"go_live": True}})
    assert res.status_code == 422


def test_settings_are_per_organization(client, world, ask):
    a, b = world.org(), world.org()
    ha, hb = world.headers(world.user(a)), world.headers(world.user(b))
    client.put(f"{BASE}/settings", headers=ha, json={"enabled": False})
    ask(hb, "hello")
    client.put(f"{BASE}/settings", headers=ha, json={"enabled": True})


def test_super_admin_kill_switch(client, world, ask, db):
    h = world.headers(world.user(world.org()))
    flag = FeatureFlag(key="operator_guide", name="Operator Guide", enabled=False)
    db.add(flag)
    db.commit()
    try:
        res = client.get(f"{BASE}/bootstrap", headers=h)
        assert res.status_code == 503
        assert res.json()["message"] == ("Guide is temporarily unavailable. Core ZoikoStream "
                                         "controls remain available.")
    finally:
        db.delete(flag)
        db.commit()


def test_audit_log_is_metadata_only(client, world, ask):
    org = world.org()
    h = world.headers(world.user(org))
    ask(h, "my secret plan is to say hello")
    items = client.get(f"{BASE}/audit", headers=h).json()["items"]
    assert items and "secret plan" not in str(items)
    assert {"kind", "intent", "outcome", "tools", "correlation_id"} <= set(items[0])
