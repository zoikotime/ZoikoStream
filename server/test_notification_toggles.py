"""Settings -> Notifications, end to end: toggle -> save -> stored -> real trigger -> sent or not.

Every send below goes through the product's real path (POST /api/events, the invitation
accept endpoint, recording_comms.notify_finalized, /api/auth/login) and is observed at the one
place an email leaves the platform: app/email.py's httpx.post, replaced by a capture. Nothing
reaches Resend, and RESEND_API_KEY is a dummy so the outcome does not depend on .env.

The scenario from the requirement:

    event_scheduled = OFF, member_joined = ON, recording_ready = OFF
      create event               -> no "Event created" email
      member accepts invitation  -> "joined" email sent
      recording validated        -> no "ready" email
      recording failed           -> "finalization failed" email sent (problems always send)
      new sign-in (security)     -> sent, whatever is stored
      billing receipt            -> sent, whatever is stored (test_commerce_comms.py)
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.config import settings
from app.db import SessionLocal
from app.models import (AccountRecovery, AuditLog, Event, EventAssignment, IdentityChallenge, Invitation,
                        LiveRecording, MediaAssetEvent, NotificationPreferenceEvent, Organization,
                        OrgMembershipEvent, SignInEvent, User)
from app.security import create_access_token, hash_password
from app.services import notifications as notif_svc
from app.services import recording_comms

PASSWORD = "correct-horse-battery"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64) Firefox/121"
READY = email_mod.MED_008_READY_SUBJECT
FAILED = email_mod.MED_008_FAILED_SUBJECT
SIGN_IN = email_mod.IDN_003_SUBJECT


# ── harness ─────────────────────────────────────────────────────────────────────────────

class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append(json or {})
        return _Resp()

    @property
    def subjects(self):
        return [c.get("subject") for c in self.calls]

    def to(self, subject_prefix):
        return sorted(c["to"][0] for c in self.calls if c.get("subject", "").startswith(subject_prefix))


class _Now:
    """BackgroundTasks stand-in for direct service calls: run the send immediately."""

    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


@pytest.fixture(autouse=True)
def _mail(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_dummy_not_a_key")
    cap = Captured()
    with patch.object(email_mod.httpx, "post", cap):
        yield cap


def _now():
    return datetime.now(timezone.utc)


def _email(tag):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class World:
    def __init__(self):
        db = SessionLocal()
        self.orgs, self.extra_users = [], []
        try:
            self.a, self.a_name = self._org(db, "Toggle A")
            self.b, self.b_name = self._org(db, "Toggle B")
            self.admin_a = self._user(db, self.a, "org_admin", _email("admin-a"), "Admin A")
            self.admin2_a = self._user(db, self.a, "org_admin", _email("admin2-a"), "Second Admin")
            self.host_a = self._user(db, self.a, "host", _email("host-a"), "Host A")
            self.admin_b = self._user(db, self.b, "org_admin", _email("admin-b"), "Admin B")
            db.get(Organization, self.a).owner_user_id = self.admin_a.id
            db.get(Organization, self.b).owner_user_id = self.admin_b.id
            ev = Event(org_id=self.a, created_by=self.admin_a.id, title="Recorded show",
                       status="scheduled", start_time=_now())
            db.add(ev)
            db.commit()
            self.recorded_event = ev.id
        finally:
            db.close()

    def _org(self, db, name):
        o = Organization(name=f"{name} {uuid.uuid4().hex[:6]}", status="active", timezone="UTC")
        db.add(o)
        db.flush()
        self.orgs.append(o.id)
        return o.id, o.name

    def _user(self, db, org_id, role, email, name):
        u = User(org_id=org_id, full_name=name, role=role, is_active=True, email=email.lower(),
                 username=f"u{uuid.uuid4().hex[:10]}", password_hash=hash_password(PASSWORD),
                 email_verified=True, email_verified_at=_now())
        db.add(u)
        db.flush()
        return SimpleNamespace(id=u.id, email=u.email)

    def client(self, user):
        db = SessionLocal()
        try:
            token = create_access_token(db.get(User, user.id), remember=False)
        finally:
            db.close()
        c = TestClient(m.app)
        c.headers["Authorization"] = f"Bearer {token}"
        return c

    def save(self, user, **prefs):
        r = self.client(user).patch("/api/organization/notifications", json=prefs)
        assert r.status_code == 200, r.text
        return r.json()

    def stored(self, org_id):
        db = SessionLocal()
        try:
            return db.get(Organization, org_id).notifications
        finally:
            db.close()

    def create_event(self, user, title):
        start = _now() + timedelta(days=2)
        r = self.client(user).post("/api/events", json={
            "title": title, "status": "scheduled", "visibility": "public", "timezone": "UTC",
            "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat()})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def invite_and_join(self, inviter, name):
        invitee = _email("joiner")
        ratelimit._HITS.clear()
        r = self.client(inviter).post("/api/organization/invitations", json={"email": invitee, "role": "viewer"})
        assert r.status_code == 201, r.text
        a = TestClient(m.app).post("/api/organization/invitations/accept", json={
            "token": r.json()["invite_token"], "full_name": name, "password": "brand-new-secret"})
        assert a.status_code == 200, a.text
        self.extra_users.append(invitee.lower())

    def finalize_recording(self, verdict):
        """A recording whose validation committed `verdict`, announced by the real MED-008 path."""
        db = SessionLocal()
        try:
            rec = LiveRecording(event_id=self.recorded_event, org_id=self.a, status="stopped",
                                quality="1080p", enforced=True, started_at=_now() - timedelta(minutes=30),
                                stopped_at=_now(), file_url="obj/rec.mp4", size_bytes=2048,
                                egress_id=f"EG_{uuid.uuid4().hex[:8]}", created_by=self.admin_a.id,
                                validation_status=verdict,
                                validation_evidence={"checked_at": _now().isoformat()})
            db.add(rec)
            db.commit()
            return recording_comms.notify_finalized(db, _Now(), rec)
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            events = [e for (e,) in db.query(Event.id).filter(Event.org_id.in_(self.orgs)).all()]
            db.query(MediaAssetEvent).filter(MediaAssetEvent.org_id.in_(self.orgs)).delete(synchronize_session=False)
            db.query(LiveRecording).filter(LiveRecording.event_id.in_(events)).delete(synchronize_session=False)
            db.query(EventAssignment).filter(EventAssignment.event_id.in_(events)).delete(synchronize_session=False)
            db.query(Event).filter(Event.id.in_(events)).delete(synchronize_session=False)
            for model in (NotificationPreferenceEvent, AuditLog, OrgMembershipEvent, Invitation):
                db.query(model).filter(model.org_id.in_(self.orgs)).delete(synchronize_session=False)
            for org_id in self.orgs:
                db.get(Organization, org_id).owner_user_id = None
            db.commit()
            for user in db.query(User).filter(User.org_id.in_(self.orgs)).all():
                for model in (SignInEvent, IdentityChallenge, AccountRecovery):
                    db.query(model).filter(model.user_id == user.id).delete(synchronize_session=False)
                db.delete(user)
            db.commit()
            db.query(Organization).filter(Organization.id.in_(self.orgs)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@pytest.fixture
def w():
    world = World()
    try:
        yield world
    finally:
        world.cleanup()


def created(cap, title):
    return f"Event created: {title}" in cap.subjects


def joined(cap, name, org):
    return email_mod.ORG_002_SUBJECT.format(member=name, org=org) in cap.subjects


# ── the exact scenario ──────────────────────────────────────────────────────────────────

def test_the_exact_scenario_each_toggle_governs_only_its_own_email(w, _mail):
    w.save(w.admin_a, event_scheduled=False, member_joined=True, recording_ready=False)

    # Refresh: the API and the database both hold exactly those three values.
    got = w.client(w.admin_a).get("/api/organization/notifications").json()
    assert (got["event_scheduled"], got["member_joined"], got["recording_ready"]) == (False, True, False)
    catalog = {c["key"]: c["value"] for c in w.client(w.admin_a).get("/api/organization/notifications/catalog").json()}
    assert (catalog["event_scheduled"], catalog["member_joined"], catalog["recording_ready"]) == (False, True, False)
    assert w.stored(w.a) == {"event_scheduled": False, "member_joined": True, "recording_ready": False}

    # 1. create an event -> NO event-scheduled email
    w.create_event(w.admin_a, "Quiet launch")
    assert not created(_mail, "Quiet launch"), _mail.subjects

    # 2. an invited member joins -> the joined email IS sent, to the inviter and the admins
    w.invite_and_join(w.admin_a, "Noisy Joiner")
    assert joined(_mail, "Noisy Joiner", w.a_name), _mail.subjects
    to = _mail.to("Noisy Joiner joined")
    assert w.admin_a.email in to and w.admin2_a.email in to
    assert w.admin_b.email not in to, "another organization is never told"

    # 3. a recording validates -> NO ready email
    w.finalize_recording("valid")
    assert READY not in _mail.subjects, _mail.subjects

    # 4. a recording fails -> the problem notice IS sent, even with recording_ready OFF
    w.finalize_recording("failed")
    assert FAILED in _mail.subjects, _mail.subjects


def test_the_opposite_settings_send_the_opposite_emails(w, _mail):
    w.save(w.admin_a, event_scheduled=True, member_joined=False, recording_ready=True)
    w.create_event(w.admin_a, "Loud launch")
    assert created(_mail, "Loud launch")
    w.invite_and_join(w.admin_a, "Quiet Joiner")
    assert not joined(_mail, "Quiet Joiner", w.a_name), _mail.subjects
    w.finalize_recording("valid")
    assert READY in _mail.subjects


def test_defaults_keep_every_operational_email_on_for_an_org_that_never_saved(w, _mail):
    assert not w.stored(w.a)
    w.create_event(w.admin_a, "Default launch")
    assert created(_mail, "Default launch")
    w.finalize_recording("valid")
    assert READY in _mail.subjects


# ── always-on, unavailable, and what the API accepts ────────────────────────────────────

def test_mandatory_notifications_cannot_be_switched_off(w, _mail):
    body = w.save(w.admin_a, billing=False, security_alerts=False, event_scheduled=False)
    assert body["billing"] is True and body["security_alerts"] is True and body["event_scheduled"] is False
    assert "billing" not in w.stored(w.a) and "security_alerts" not in w.stored(w.a)

    # Even a value forced into the column by hand changes nothing.
    db = SessionLocal()
    try:
        org = db.get(Organization, w.a)
        org.notifications = {"billing": False, "security_alerts": False, "event_scheduled": False,
                             "member_joined": False, "recording_ready": False}
        db.commit()
        for family in ("COM-001", "COM-006", "COM-007", "IDN-003", "SEC-001", "MED-011", "ORG-012"):
            assert notif_svc.should_send_operational_notification(family=family, org=org), family
    finally:
        db.close()

    # A real security email still goes out: a sign-in from a new device.
    ratelimit._HITS.clear()
    c = TestClient(m.app)
    assert c.post("/api/auth/login", json={"identifier": w.admin_a.email, "password": PASSWORD},
                  headers={"User-Agent": CHROME}).status_code == 200
    _mail.calls.clear()
    ratelimit._HITS.clear()
    assert c.post("/api/auth/login", json={"identifier": w.admin_a.email, "password": PASSWORD},
                  headers={"User-Agent": FIREFOX}).status_code == 200
    assert SIGN_IN in _mail.subjects, _mail.subjects


def test_unavailable_notifications_stay_off_and_are_never_stored(w, _mail):
    body = w.save(w.admin_a, event_starting=True, weekly_summary=True, mentions=True, member_joined=False)
    assert (body["event_starting"], body["weekly_summary"], body["mentions"]) == (False, False, False)
    assert set(w.stored(w.a)) == {"event_scheduled", "member_joined", "recording_ready"}
    rows = {c["key"]: c for c in w.client(w.admin_a).get("/api/organization/notifications/catalog").json()}
    for key in ("event_starting", "weekly_summary", "mentions"):
        assert rows[key]["available"] is False and rows[key]["configurable"] is False and rows[key]["value"] is False
    for key in ("billing", "security_alerts"):
        assert rows[key]["mandatory"] is True and rows[key]["configurable"] is False and rows[key]["value"] is True


@pytest.mark.parametrize("payload", [
    {"event_scheduled": "false"},          # a string is not a boolean (bool("false") is True)
    {"member_joined": 0},
    {"recording_ready": "no"},
    {"weekly_digest": True},               # not a preference at all
    {"event_scheduled": True, "anything_else": False},
])
def test_malformed_or_unknown_preferences_are_refused(w, _mail, payload):
    r = w.client(w.admin_a).patch("/api/organization/notifications", json=payload)
    assert r.status_code == 422, r.text
    assert not w.stored(w.a), "a refused update stores nothing"


def test_a_legacy_non_boolean_in_the_column_is_not_read_as_true(w, _mail):
    db = SessionLocal()
    try:
        org = db.get(Organization, w.a)
        org.notifications = {"event_scheduled": "false", "member_joined": None}
        db.commit()
        prefs = notif_svc.effective(org)
    finally:
        db.close()
    assert prefs["event_scheduled"] is True and prefs["member_joined"] is True   # defaults, not coercion


# ── independence, persistence, permission, tenants ──────────────────────────────────────

def test_changing_one_toggle_leaves_the_others_exactly_as_they_were(w, _mail):
    w.save(w.admin_a, event_scheduled=False, member_joined=True, recording_ready=False)
    body = w.save(w.admin_a, member_joined=False)
    assert (body["event_scheduled"], body["member_joined"], body["recording_ready"]) == (False, False, False)
    body = w.save(w.admin_a, recording_ready=True)
    assert (body["event_scheduled"], body["member_joined"], body["recording_ready"]) == (False, False, True)
    assert w.stored(w.a) == {"event_scheduled": False, "member_joined": False, "recording_ready": True}


def test_only_an_org_admin_changes_organization_preferences(w, _mail):
    host = w.client(w.host_a)
    r = host.patch("/api/organization/notifications", json={"event_scheduled": False})
    assert r.status_code == 403
    assert not w.stored(w.a)
    assert host.get("/api/organization/notifications").json()["event_scheduled"] is True   # may read
    w.create_event(w.admin_a, "Still announced")
    assert created(_mail, "Still announced")


def test_one_organizations_preferences_never_affect_another(w, _mail):
    w.save(w.admin_a, event_scheduled=False, member_joined=False, recording_ready=False)
    assert not w.stored(w.b)
    w.create_event(w.admin_a, "A is quiet")
    w.create_event(w.admin_b, "B is not")
    assert not created(_mail, "A is quiet")
    assert created(_mail, "B is not")
    assert _mail.to("Event created: B is not") == [w.admin_b.email]
    w.invite_and_join(w.admin_b, "B Joiner")
    assert joined(_mail, "B Joiner", w.b_name)
