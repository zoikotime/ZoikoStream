"""Shared fixtures for the database-backed custom-domain tests.

The three things that would leave this machine are replaced, and nothing else:
  * DNS          domain_dns.query reads a dict the test writes records into
  * certificates domain_provider.get_provider returns FakeProvider
  * HTTPS probe  domain_probe.probe returns whatever the test sets
Everything else — validation, the unique index, the lifecycle, routing, audit rows — is the
real code against the real test database.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.db import SessionLocal
from app.models import (AuditLog, ElevationSession, Event, EventRegistration, LiveActivity, LiveMessage,
                        Organization, User)
from app.security import create_access_token, hash_password
from app.services import custom_domain_routing as routing
from app.services import custom_domains, domain_dns, domain_probe, domain_provider
from app.services.domain_provider import ProviderError, ProviderState

TARGET = "cname.zoikostream.com"


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.ready = False
        self.failed = False
        self.error = None
        self.created: list[str] = []
        self.deleted: list[str] = []
        self._ids: dict[str, str] = {}

    def ensure(self, hostname, hostname_id=None):
        if self.error:
            raise ProviderError(self.error)
        hid = self._ids.setdefault(hostname, f"ch_{uuid.uuid4().hex[:8]}")
        if hostname not in self.created:
            self.created.append(hostname)
        ssl = "validation_timed_out" if self.failed else ("active" if self.ready else "pending_validation")
        return ProviderState(hid, "active" if self.ready else "pending", ssl,
                             ready=self.ready and not self.failed, failed=self.failed)

    def status(self, hostname, hostname_id):
        return self.ensure(hostname, hostname_id)

    def delete(self, hostname_id):
        if hostname_id:
            self.deleted.append(hostname_id)
            self._ids = {h: i for h, i in self._ids.items() if i != hostname_id}


def host() -> str:
    return f"events-{uuid.uuid4().hex[:10]}.example.com"


@pytest.fixture
def feature(monkeypatch):
    """Custom domains switched on, with scripted DNS, certificates and probe."""
    monkeypatch.setattr(settings, "CUSTOM_DOMAIN_CNAME_TARGET", TARGET)
    monkeypatch.setattr(settings, "CUSTOM_DOMAIN_PLATFORM_HOSTS", "get.zoikostream.com")
    monkeypatch.setattr(settings, "CUSTOM_DOMAIN_DNS_RESOLVERS", "ns1")
    provider = FakeProvider()
    monkeypatch.setattr(domain_provider, "get_provider", lambda: provider)
    monkeypatch.setattr(custom_domains, "_target_resolves", lambda target: True)
    records: dict = {}
    monkeypatch.setattr(domain_dns, "query",
                        lambda ns, name, rdtype: records.get((name, rdtype), (domain_dns.NXDOMAIN, [])))
    probe = {"code": None, "calls": 0}

    def fake_probe(hostname, org_id, **kw):
        probe["calls"] += 1
        return probe["code"]

    monkeypatch.setattr(domain_probe, "probe", fake_probe)
    routing.clear_caches()

    def publish(hostname, token, *, cname=TARGET, txt=True):
        records.pop((hostname, "CNAME"), None)
        records.pop((f"_zoikostream.{hostname}", "TXT"), None)
        if cname:
            records[(hostname, "CNAME")] = (domain_dns.OK, [cname])
        if txt:
            value = txt if isinstance(txt, str) else domain_dns.txt_value(token)
            records[(f"_zoikostream.{hostname}", "TXT")] = (domain_dns.OK, [value])

    yield SimpleNamespace(provider=provider, records=records, probe=probe, publish=publish)
    routing.clear_caches()


@pytest.fixture
def world():
    db = SessionLocal()
    orgs, users, events = [], [], []

    def org(name):
        o = Organization(name=f"{name} {uuid.uuid4().hex[:6]}", status="active")
        db.add(o)
        db.flush()
        orgs.append(o.id)
        return o

    def user(o, role):
        u = User(org_id=o.id, full_name=role.title(), role=role, is_active=True,
                 email=f"cd-{uuid.uuid4().hex[:10]}@example.com", username=f"cd{uuid.uuid4().hex[:10]}",
                 password_hash=hash_password("x"), email_verified=True)
        db.add(u)
        db.flush()
        users.append(u.id)
        return u

    def event(o, creator, title):
        now = datetime.now(timezone.utc)
        ev = Event(org_id=o.id, created_by=creator.id, title=title, status="scheduled", visibility="public",
                   chat_enabled=True, start_time=now + timedelta(days=1), end_time=now + timedelta(days=1, hours=1))
        db.add(ev)
        db.flush()
        events.append(ev.id)
        return ev

    a, b, platform = org("Org A"), org("Org B"), org("Platform")
    ns = SimpleNamespace(db=db, a=a, b=b)
    ns.admin_a, ns.host_a, ns.admin_b = user(a, "org_admin"), user(a, "host"), user(b, "org_admin")
    ns.staff = user(platform, "super_admin")
    ns.event_a, ns.event_b = event(a, ns.admin_a, "A's event"), event(b, ns.admin_b, "B's event")
    db.commit()
    try:
        yield ns
    finally:
        db.rollback()
        for model in (LiveActivity, LiveMessage, EventRegistration):
            db.query(model).filter(model.event_id.in_(events)).delete(synchronize_session=False)
        db.query(Event).filter(Event.id.in_(events)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.org_id.in_(orgs)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.actor_id.in_(users)).delete(synchronize_session=False)
        db.query(ElevationSession).filter(ElevationSession.user_id.in_(users)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(users)).delete(synchronize_session=False)
        db.query(Organization).filter(Organization.id.in_(orgs)).delete(synchronize_session=False)
        db.commit()
        db.close()


def client_for(u=None, host_header=None):
    c = TestClient(m.app)
    if u is not None:
        c.headers["Authorization"] = f"Bearer {create_access_token(u, remember=False)}"
    if host_header:
        c.headers["Host"] = host_header
    return c


def fresh(w, org):
    w.db.expire_all()
    return w.db.get(Organization, org.id)


def elevate(w, user, scope="platform"):
    now = datetime.now(timezone.utc)
    w.db.add(ElevationSession(user_id=user.id, scope=scope, scopes=[], reason="test",
                              granted_at=now, expires_at=now + timedelta(minutes=15)))
    w.db.commit()


def audit_actions(w, org):
    w.db.expire_all()
    rows = w.db.query(AuditLog).filter(AuditLog.org_id == org.id, AuditLog.target_type == "custom_domain") \
        .order_by(AuditLog.created_at).all()
    return rows


def activate(w, feature, org, hostname):
    """Drive a domain to active through the real API: save, publish DNS, certificate, probe."""
    r = client_for(w.admin_a if org.id == w.a.id else w.admin_b).patch(
        "/api/organization/domain", json={"domain": hostname})
    assert r.status_code == 200, r.text
    token = fresh(w, org).domain_verification_token
    feature.publish(hostname, token)
    feature.provider.ready = True
    r = client_for(w.admin_a if org.id == w.a.id else w.admin_b).post("/api/organization/domain/verify")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active", r.json()
    return token
