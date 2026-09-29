"""Organization Settings: every section PATCHes only what it sends.

The console used to PATCH all five settings sections on every Save, so a failure in any one
surfaced against panels nobody had touched. The server side of that contract is pinned here:
an omitted field is preserved, an explicit null clears, the domain is validated only when it
is sent, and changing branding never touches the domain or its verification.
"""
import uuid

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import AuditLog, NotificationPreferenceEvent, Organization, User
from app.security import create_access_token, hash_password


@pytest.fixture
def world():
    db = SessionLocal()
    made_orgs, made_users = [], []

    def org(**kw):
        o = Organization(name=f"Settings {uuid.uuid4().hex[:6]}", status="active", **kw)
        db.add(o)
        db.flush()
        made_orgs.append(o.id)
        return o

    def user(o, role):
        u = User(org_id=o.id, full_name=role.title(), role=role, is_active=True,
                 email=f"set-{uuid.uuid4().hex[:10]}@example.com",
                 username=f"set{uuid.uuid4().hex[:10]}", password_hash=hash_password("x"),
                 email_verified=True)
        db.add(u)
        db.flush()
        made_users.append(u.id)
        return u

    mine = org(domain="events.acme.com", domain_verified=True, logo_url="https://cdn.acme.com/l.png",
               primary_color="violet")
    other = org(domain="live.other.com", domain_verified=True, primary_color="violet")
    ns = type("W", (), {})()
    ns.db, ns.org, ns.other = db, mine, other
    ns.admin, ns.host, ns.other_admin = user(mine, "org_admin"), user(mine, "host"), user(other, "org_admin")
    db.commit()
    try:
        yield ns
    finally:
        db.rollback()
        # The notifications PATCH records a preference-change event and an audit row, both of
        # which reference the admin; they go first.
        db.query(NotificationPreferenceEvent).filter(
            NotificationPreferenceEvent.org_id.in_(made_orgs)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.org_id.in_(made_orgs)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(made_users)).delete(synchronize_session=False)
        db.query(Organization).filter(Organization.id.in_(made_orgs)).delete(synchronize_session=False)
        db.commit()
        db.close()


def client_for(u):
    c = TestClient(m.app)
    c.headers["Authorization"] = f"Bearer {create_access_token(u, remember=False)}"
    return c


def fresh(w):
    w.db.expire_all()
    return w.db.get(Organization, w.org.id)


def test_branding_patch_changes_only_the_sent_field_and_never_the_domain(world):
    r = client_for(world.admin).patch("/api/organization/branding", json={"primary_color": "emerald"})
    assert r.status_code == 200, r.text
    o = fresh(world)
    assert o.primary_color == "emerald"
    assert o.logo_url == "https://cdn.acme.com/l.png", "an omitted field is preserved"
    assert (o.domain, o.domain_verified) == ("events.acme.com", True), \
        "saving branding must not touch the custom domain or its verification"


def test_explicit_null_clears_but_omission_preserves(world):
    c = client_for(world.admin)
    assert c.patch("/api/organization/branding", json={}).status_code == 200
    assert fresh(world).logo_url == "https://cdn.acme.com/l.png"
    assert c.patch("/api/organization/branding", json={"logo_url": None}).status_code == 200
    assert fresh(world).logo_url is None


def test_an_empty_domain_patch_changes_nothing(world):
    r = client_for(world.admin).patch("/api/organization/domain", json={})
    assert r.status_code == 200, r.text
    o = fresh(world)
    assert (o.domain, o.domain_verified) == ("events.acme.com", True)


def test_resaving_the_same_domain_keeps_its_verification(world):
    r = client_for(world.admin).patch("/api/organization/domain", json={"domain": "Events.ACME.com."})
    assert r.status_code == 200, r.text
    assert r.json() == {"domain": "events.acme.com", "domain_verified": True}


def test_changing_the_domain_drops_verification(world):
    r = client_for(world.admin).patch("/api/organization/domain", json={"domain": "stream.acme.com"})
    assert r.status_code == 200
    assert r.json() == {"domain": "stream.acme.com", "domain_verified": False}


@pytest.mark.parametrize("bad", [
    "https://events.acme.com", "events.acme.com/live", "events.acme.com:8443", "acme",
    "-bad.acme.com", "bad-.acme.com", "events.zoikostream.com", "a" * 64 + ".acme.com",
])
def test_an_invalid_hostname_is_refused_on_the_domain_field(world, bad):
    r = client_for(world.admin).patch("/api/organization/domain", json={"domain": bad})
    assert r.status_code == 422
    locs = [tuple(d["loc"]) for d in r.json()["detail"]]
    assert ("body", "domain") in locs, "the error must name the domain field"
    o = fresh(world)
    assert (o.domain, o.domain_verified) == ("events.acme.com", True), "a refusal changes nothing"


def test_clearing_the_domain_is_allowed(world):
    r = client_for(world.admin).patch("/api/organization/domain", json={"domain": ""})
    assert r.status_code == 200
    assert r.json()["domain"] is None


def test_notifications_patch_leaves_branding_and_domain_alone(world):
    r = client_for(world.admin).patch("/api/organization/notifications", json={"event_scheduled": False})
    assert r.status_code == 200, r.text
    o = fresh(world)
    assert o.primary_color == "violet" and o.logo_url == "https://cdn.acme.com/l.png"
    assert (o.domain, o.domain_verified) == ("events.acme.com", True)


def test_profile_patch_leaves_branding_and_domain_alone(world):
    r = client_for(world.admin).patch("/api/organization/profile", json={"description": "Hello"})
    assert r.status_code == 200, r.text
    o = fresh(world)
    assert o.description == "Hello"
    assert (o.domain, o.domain_verified, o.primary_color) == ("events.acme.com", True, "violet")


def test_only_an_org_admin_can_change_settings(world):
    host = client_for(world.host)
    for url, body in (("/api/organization/branding", {"primary_color": "emerald"}),
                      ("/api/organization/domain", {"domain": "x.acme.com"}),
                      ("/api/organization/profile", {"description": "no"})):
        assert host.patch(url, json=body).status_code == 403, url
    o = fresh(world)
    assert (o.primary_color, o.domain) == ("violet", "events.acme.com")


def test_an_admin_changes_only_their_own_organization(world):
    r = client_for(world.other_admin).patch("/api/organization/domain", json={"domain": "new.other.com"})
    assert r.status_code == 200
    o = fresh(world)
    assert (o.domain, o.domain_verified) == ("events.acme.com", True), \
        "the organization comes from the caller's session, never from the request"
