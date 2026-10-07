"""Organization branding: a light logo and a dark-theme logo.

One `logo_url` used to serve both themes, so a logo drawn for light backgrounds vanished on the
dark viewer page. `logo_url_dark` is the second, optional asset. Pinned here:

* both persist independently — a PATCH of one never touches the other;
* blank/null clears the dark logo, which restores the fallback to the light one;
* both URLs are validated the same way (https, an image), and only when sent;
* only an org admin may change branding, and only for their own organization;
* the public watch payload carries exactly this event's organization's two logos;
* an organization that only ever had `logo_url` needs no data rewrite.
"""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, Event, Organization, User
from app.security import create_access_token, hash_password
from app.services import bus, livekit

LIGHT = "https://cdn.acme.com/logo.png"
DARK = "https://cdn.acme.com/logo-dark.svg"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "LIVEKIT_URL", "")
    assert not livekit.configured()


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            self.orgs, self.users, self.events = [], [], []
            # `mine` is a pre-existing organization: one logo, never a dark one.
            self.mine = self._org(db, "Brand A", logo_url=LIGHT)
            self.other = self._org(db, "Brand B", logo_url="https://cdn.other.com/b.png",
                                   logo_url_dark="https://cdn.other.com/b-dark.png")
            self.admin = self._user(db, self.mine, "org_admin")
            self.host = self._user(db, self.mine, "host")
            self.viewer = self._user(db, self.mine, "viewer")
            self.other_admin = self._user(db, self.other, "org_admin")
            db.commit()
        finally:
            db.close()

    def _org(self, db, name, **kw):
        o = Organization(name=f"{name} {uuid.uuid4().hex[:6]}", status="active", **kw)
        db.add(o)
        db.flush()
        self.orgs.append(o.id)
        return o.id

    def _user(self, db, org_id, role):
        u = User(org_id=org_id, full_name=role.title(), role=role, is_active=True,
                 email=f"brand-{uuid.uuid4().hex[:10]}@example.com", username=f"br{uuid.uuid4().hex[:10]}",
                 password_hash=hash_password("x"), email_verified=True)
        db.add(u)
        db.flush()
        self.users.append(u.id)
        return u.id

    def client(self, user_id=None):
        c = TestClient(m.app)
        if user_id:
            db = SessionLocal()
            try:
                c.headers["Authorization"] = f"Bearer {create_access_token(db.get(User, user_id), remember=False)}"
            finally:
                db.close()
        return c

    def event(self, org_id):
        """A public, scheduled event — what an anonymous viewer can open."""
        db = SessionLocal()
        try:
            creator = self.admin if org_id == self.mine else self.other_admin
            ev = Event(org_id=org_id, created_by=creator, title=f"Brand {uuid.uuid4().hex[:5]}",
                       status="scheduled", visibility="public",
                       start_time=datetime.now(timezone.utc) + timedelta(days=2))
            db.add(ev)
            db.commit()
            self.events.append(ev.id)
            return ev.id
        finally:
            db.close()

    def org(self, org_id):
        db = SessionLocal()
        try:
            o = db.get(Organization, org_id)
            return o.logo_url, o.logo_url_dark
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(Event).filter(Event.id.in_(self.events)).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.org_id.in_(self.orgs)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(self.users)).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id.in_(self.orgs)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


# ── persistence ──────────────────────────────────────────────────────────────────────────

def test_an_existing_single_logo_organization_reads_back_unchanged(w):
    """Backward compatibility: nothing was rewritten; dark is simply null (= use the light one)."""
    body = w.client(w.admin).get("/api/organization/branding").json()
    assert body["logo_url"] == LIGHT
    assert body["logo_url_dark"] is None


def test_the_dark_logo_saves_independently_and_the_light_one_is_preserved(w):
    c = w.client(w.admin)
    r = c.patch("/api/organization/branding", json={"logo_url_dark": DARK})
    assert r.status_code == 200, r.text
    assert r.json()["logo_url"] == LIGHT and r.json()["logo_url_dark"] == DARK
    assert w.org(w.mine) == (LIGHT, DARK)
    assert c.get("/api/organization/branding").json()["logo_url_dark"] == DARK


def test_both_persist_and_a_light_only_patch_leaves_the_dark_logo_alone(w):
    c = w.client(w.admin)
    assert c.patch("/api/organization/branding",
                   json={"logo_url": "https://cdn.acme.com/new.png", "logo_url_dark": DARK}).status_code == 200
    assert w.org(w.mine) == ("https://cdn.acme.com/new.png", DARK)
    assert c.patch("/api/organization/branding", json={"logo_url": LIGHT}).status_code == 200
    assert w.org(w.mine) == (LIGHT, DARK)
    assert c.patch("/api/organization/branding", json={"primary_color": "amber"}).status_code == 200
    assert w.org(w.mine) == (LIGHT, DARK)


@pytest.mark.parametrize("cleared", [None, "", "   "])
def test_clearing_the_dark_logo_restores_the_fallback_and_keeps_the_light_one(w, cleared):
    c = w.client(w.admin)
    c.patch("/api/organization/branding", json={"logo_url_dark": DARK})
    r = c.patch("/api/organization/branding", json={"logo_url_dark": cleared})
    assert r.status_code == 200, r.text
    assert r.json()["logo_url_dark"] is None
    assert w.org(w.mine) == (LIGHT, None)


# ── validation ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "http://cdn.acme.com/logo-dark.png",        # not https
    "javascript:alert(1)",
    "cdn.acme.com/logo.png",                     # no scheme
    "https://",                                  # no host
    "https:/cdn.acme.com/logo.png",              # malformed authority
    "https://cdn.acme.com/my logo.png",          # whitespace
    "https://cdn.acme.com/brand-guide.pdf",      # not an image
    "https://cdn.acme.com/index.html",
    "https://cdn.acme.com/" + "a" * 500 + ".png",  # too long
])
@pytest.mark.parametrize("field", ["logo_url", "logo_url_dark"])
def test_an_invalid_logo_url_is_refused_on_its_own_field(w, field, bad):
    r = w.client(w.admin).patch("/api/organization/branding", json={field: bad})
    assert r.status_code == 422, r.text
    assert any(d["loc"][-1] == field for d in r.json()["detail"])
    assert w.org(w.mine) == (LIGHT, None), "nothing is written on a refused value"


@pytest.mark.parametrize("good", [
    "https://cdn.acme.com/logo.svg",
    "https://cdn.acme.com/logo.PNG",
    "https://cdn.acme.com/logo.webp?v=3",
    "https://images.acme-cdn.net/brand/7f3a9c",   # extensionless CDN key
    "HTTPS://cdn.acme.com/logo.jpg",
    "  https://cdn.acme.com/logo.gif  ",          # surrounding space is trimmed
])
def test_https_image_links_are_accepted_for_the_dark_logo(w, good):
    r = w.client(w.admin).patch("/api/organization/branding", json={"logo_url_dark": good})
    assert r.status_code == 200, r.text
    assert w.org(w.mine)[1] == good.strip()


def test_the_profile_endpoint_applies_the_same_rules_to_the_shared_logo_column(w):
    c = w.client(w.admin)
    assert c.patch("/api/organization/profile", json={"logo_url": "http://cdn.acme.com/l.png"}).status_code == 422
    assert c.patch("/api/organization/profile", json={"name": "Renamed"}).status_code == 200
    assert w.org(w.mine) == (LIGHT, None)


def test_a_stored_legacy_value_is_not_revalidated_when_other_fields_are_saved(w):
    """A logo stored before these rules must not block saving the brand color."""
    db = SessionLocal()
    try:
        db.get(Organization, w.mine).logo_url = "http://legacy.acme.com/logo.png"
        db.commit()
    finally:
        db.close()
    r = w.client(w.admin).patch("/api/organization/branding", json={"primary_color": "rose"})
    assert r.status_code == 200, r.text
    assert w.org(w.mine)[0] == "http://legacy.acme.com/logo.png"


# ── permissions and isolation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["host", "viewer"])
def test_a_non_admin_cannot_change_branding(w, role):
    who = w.host if role == "host" else w.viewer
    r = w.client(who).patch("/api/organization/branding", json={"logo_url_dark": DARK})
    assert r.status_code == 403
    assert w.org(w.mine) == (LIGHT, None)


def test_an_admin_changes_only_their_own_organization(w):
    before_other = w.org(w.other)
    r = w.client(w.other_admin).patch("/api/organization/branding", json={"logo_url_dark": DARK})
    assert r.status_code == 200
    assert w.org(w.mine) == (LIGHT, None), "the other organization's admin never reaches this row"
    assert w.org(w.other) == (before_other[0], DARK)
    # Each admin reads back only their own organization's logos.
    assert w.client(w.admin).get("/api/organization/branding").json()["logo_url_dark"] is None
    assert w.client(w.other_admin).get("/api/organization/branding").json()["logo_url_dark"] == DARK


# ── the audience-facing payload ──────────────────────────────────────────────────────────

def test_the_watch_payload_carries_this_events_organization_logos(w):
    mine, theirs = w.event(w.mine), w.event(w.other)
    anon = w.client()
    a = anon.get(f"/api/events/{mine}/watch").json()
    assert a["organization_logo_url"] == LIGHT
    assert a["organization_logo_url_dark"] is None          # client falls back to the light one
    b = anon.get(f"/api/events/{theirs}/watch").json()
    assert b["organization_logo_url"] == "https://cdn.other.com/b.png"
    assert b["organization_logo_url_dark"] == "https://cdn.other.com/b-dark.png"
    assert "cdn.other.com" not in str(a), "no other organization's branding leaks into a payload"


def test_a_saved_dark_logo_reaches_the_watch_payload_and_clearing_it_restores_the_fallback(w):
    eid = w.event(w.mine)
    admin, anon = w.client(w.admin), w.client()
    admin.patch("/api/organization/branding", json={"logo_url_dark": DARK})
    assert anon.get(f"/api/events/{eid}/watch").json()["organization_logo_url_dark"] == DARK
    admin.patch("/api/organization/branding", json={"logo_url_dark": None})
    body = anon.get(f"/api/events/{eid}/watch").json()
    assert body["organization_logo_url_dark"] is None and body["organization_logo_url"] == LIGHT


# ── migration ────────────────────────────────────────────────────────────────────────────

def test_the_migration_adds_a_nullable_column_and_rewrites_no_data():
    src = Path(__file__).with_name("create_tables.py").read_text(encoding="utf-8")
    assert '"ADD COLUMN IF NOT EXISTS logo_url_dark VARCHAR(500)"' in src
    line = next(l for l in src.splitlines() if "logo_url_dark" in l and "ADD COLUMN" in l)
    assert "NOT NULL" not in line and "DEFAULT" not in line
    assert "UPDATE organizations SET logo_url_dark" not in src
