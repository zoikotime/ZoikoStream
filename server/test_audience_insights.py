"""Audience insights: geography, device and player mix, registration -> attendance and blocked
join attempts (models/audience.py, services/audience.py, GET /organization/audience-insights).

What is pinned here:
  * Country / Region is optional, ISO-coded, refused when unknown, updatable by a returning
    viewer, and offered once to a host-invited viewer (who never sees the registration form).
  * Device, browser, OS and client are detected from the request and normalized to closed
    classes; the user agent itself is never stored.
  * One audience row per event and viewer: reconnects and repeat /watch calls never inflate the
    audience, staff and the anonymous disposable identity are never recorded.
  * Aggregates count unique viewers, are scoped to the caller's organization, the date window
    and (optionally) one event, and say "not applicable" rather than 0% where nothing applies.
  * Every refused join is recorded with a normalized reason only: no token, secret, address or
    user agent, and a retried refusal is one attempt.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

import app.main as m
from app.db import Base, SessionLocal
from app.models import (AuditLog, AudienceSession, BroadcastSession, Event, EventRegistration, JoinDenial,
                        Organization, User)
from app.routers import events as events_router
from app.security import create_access_token, create_registration_token, hash_password
from app.services import audience as audience_svc
from app.services import bus

UTC = timezone.utc

UA_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1")
UA_WINDOWS_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0.0.0 Safari/537.36")
UA_WINDOWS_EDGE = UA_WINDOWS_CHROME + " Edg/126.0.0.0"
UA_MAC_FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0"
UA_IPAD = ("Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
           "Version/17.4 Mobile/15E148 Safari/604.1")
UA_ANDROID_TABLET = ("Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0.0.0 Safari/537.36")
UA_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """In-process bus, a LiveKit that answers without a server, and a clean in-memory denial
    de-duplication window for every test."""
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    monkeypatch.setattr(events_router.livekit, "configured", lambda: True)
    monkeypatch.setattr(events_router.livekit, "create_stream_token", lambda *a, **k: "stream-token")
    audience_svc._recent_denials.clear()
    yield
    audience_svc._recent_denials.clear()


class World:
    def __init__(self):
        self.db = SessionLocal()
        self.orgs, self.users, self.events = [], [], []
        self.org, self.admin = self._org("Audience")
        self.other_org, self.other_admin = self._org("Elsewhere")

    def _org(self, label):
        org = Organization(name=f"{label} {uuid.uuid4().hex[:6]}", status="active")
        self.db.add(org)
        self.db.flush()
        admin = User(org_id=org.id, full_name=f"{label} Admin", role="org_admin", is_active=True,
                     email=f"aud-{uuid.uuid4().hex[:10]}@example.com", username=f"aud{uuid.uuid4().hex[:10]}",
                     password_hash=hash_password("x"), email_verified=True)
        self.db.add(admin)
        self.db.commit()
        self.orgs.append(org.id)
        self.users.append(admin.id)
        return org, admin

    def event(self, *, org=None, visibility="public", required=False, status="live", limit=None,
              start=None):
        org = org or self.org
        creator = self.admin if org.id == self.org.id else self.other_admin
        now = datetime.now(UTC)
        ev = Event(org_id=org.id, created_by=creator.id, title=f"Event {uuid.uuid4().hex[:6]}",
                   status=status, visibility=visibility, registration_required=required,
                   registration_limit=limit, chat_enabled=True,
                   start_time=start or now - timedelta(minutes=30), end_time=now + timedelta(hours=2))
        self.db.add(ev)
        self.db.commit()
        self.events.append(ev.id)
        return ev

    def registration(self, ev, *, invited=False, country=None, email=None):
        reg = EventRegistration(event_id=ev.id, name="Guest", email=email or f"g-{uuid.uuid4().hex[:10]}@example.com",
                                invited_by=self.admin.id if invited else None, country_code=country,
                                created_at=datetime.now(UTC))
        self.db.add(reg)
        self.db.commit()
        return reg

    def session_row(self, ev, *, country=None, device="desktop", browser="chrome", os="windows",
                    client="web", seen=None, registration=None):
        seen = seen or datetime.now(UTC)
        row = AudienceSession(org_id=ev.org_id, event_id=ev.id, viewer_key=uuid.uuid4().hex + uuid.uuid4().hex,
                              registration_id=registration.id if registration else None, country_code=country,
                              device_type=device, browser=browser, os=os, client_type=client,
                              joined_at=seen, last_seen_at=seen, join_count=1, watch_seconds=0)
        self.db.add(row)
        self.db.commit()
        return row

    def admin_client(self, user=None):
        c = TestClient(m.app)
        c.headers["Authorization"] = f"Bearer {create_access_token(user or self.admin, remember=False)}"
        return c

    def rows(self, ev):
        self.db.expire_all()
        return list(self.db.scalars(select(AudienceSession).where(AudienceSession.event_id == ev.id)))

    def denials(self, ev):
        self.db.expire_all()
        return list(self.db.scalars(select(JoinDenial).where(JoinDenial.event_id == ev.id)))

    def insights(self, org=None, **kw):
        self.db.expire_all()
        return audience_svc.insights(self.db, org or self.org, **kw)

    def cleanup(self):
        db = self.db
        try:
            db.rollback()
            ids = self.events
            if ids:
                for table in reversed(Base.metadata.sorted_tables):
                    for fk in table.foreign_keys:
                        if fk.column.table.name == "events" and table.name != "events":
                            db.execute(table.delete().where(fk.parent.in_(ids)))
                db.query(Event).filter(Event.id.in_(ids)).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.org_id.in_(self.orgs)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(self.users)).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id.in_(self.orgs)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


def watch(ev, *, reg=None, ua=UA_WINDOWS_CHROME, client=None, http=None):
    params = {k: v for k, v in (("reg", reg), ("client", client)) if v}
    return (http or TestClient(m.app)).get(f"/api/events/{ev.id}/watch", params=params,
                                            headers={"User-Agent": ua})


def register(ev, **body):
    return TestClient(m.app).post(f"/api/events/{ev.id}/register", json={"name": "Asha", **body})


# ── 1-4: the Country / Region field ─────────────────────────────────────────────────────────

def test_1_registration_saves_an_iso_country(w):
    ev = w.event(required=True)
    r = register(ev, country="in")
    assert r.status_code == 200, r.text
    assert r.json()["country_code"] == "IN"
    reg = w.db.get(EventRegistration, uuid.UUID(r.json()["id"]))
    assert reg.country_code == "IN"


def test_2_country_is_optional(w):
    ev = w.event(required=True)
    r = register(ev)
    assert r.status_code == 200, r.text
    assert r.json()["country_code"] is None
    r = register(ev, country="")
    assert r.status_code == 200 and r.json()["country_code"] is None


def test_3_an_unknown_country_is_refused_and_nothing_is_registered(w):
    ev = w.event(required=True)
    for bad in ("ZZ", "XX", "United States", "U"):
        r = register(ev, country=bad)
        assert r.status_code == 422, (bad, r.text)
    w.db.expire_all()
    assert w.db.scalar(select(EventRegistration).where(EventRegistration.event_id == ev.id)) is None


def test_4_a_returning_viewer_updates_their_country_and_their_audience_row_follows(w):
    ev = w.event(required=True)
    first = register(ev, email="returning@example.com", country="GB").json()
    watch(ev, reg=first["token"])
    assert [row.country_code for row in w.rows(ev)] == ["GB"]

    again = register(ev, email="returning@example.com", country="IE")
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert again.json()["country_code"] == "IE"
    assert [row.country_code for row in w.rows(ev)] == ["IE"]
    # Leaving it blank on a later visit keeps what they chose.
    assert register(ev, email="returning@example.com").json()["country_code"] == "IE"


def test_4b_an_invited_viewer_is_offered_the_field_once_and_can_set_it(w):
    ev = w.event(visibility="private")
    reg = w.registration(ev, invited=True)
    token = create_registration_token(reg)
    viewer = TestClient(m.app)
    assert watch(ev, reg=token, http=viewer).json()["country_prompt"] is True

    r = viewer.put(f"/api/events/{ev.id}/registration/country", json={"token": token, "country": "ng"})
    assert r.status_code == 200, r.text
    assert r.json() == {"country_code": "NG"}
    assert watch(ev, reg=token, http=viewer).json()["country_prompt"] is False
    assert [row.country_code for row in w.rows(ev)] == ["NG"]


def test_4c_the_country_update_needs_this_events_credential(w):
    ev, other = w.event(visibility="private"), w.event(visibility="private")
    reg = w.registration(ev, invited=True)
    viewer = TestClient(m.app)
    watch(ev, reg=create_registration_token(reg), http=viewer)      # claims the invitation
    for event_id, token, client in (
        (other.id, create_registration_token(reg), viewer),          # another event's token
        (ev.id, "not-a-real-registration-token", viewer),            # forged
        (ev.id, create_registration_token(reg), TestClient(m.app)),  # another browser
    ):
        r = client.put(f"/api/events/{event_id}/registration/country", json={"token": token, "country": "FR"})
        assert r.status_code == 404, r.text
    w.db.expire_all()
    assert w.db.get(EventRegistration, reg.id).country_code is None


def test_4d_self_registered_viewers_are_not_prompted_again(w):
    ev = w.event(required=True)
    token = register(ev).json()["token"]
    assert watch(ev, reg=token).json()["country_prompt"] is False


# ── 5-7: device detection, staff, reconnects ────────────────────────────────────────────────

@pytest.mark.parametrize("ua, expected", [
    (UA_IPHONE, ("mobile", "safari", "ios")),
    (UA_IPAD, ("tablet", "safari", "ios")),
    (UA_ANDROID_TABLET, ("tablet", "chrome", "android")),
    (UA_WINDOWS_CHROME, ("desktop", "chrome", "windows")),
    (UA_WINDOWS_EDGE, ("desktop", "edge", "windows")),
    (UA_MAC_FIREFOX, ("desktop", "firefox", "macos")),
    (UA_LINUX, ("desktop", "firefox", "linux")),
    ("", ("other", "other", "other")),
])
def test_5_device_browser_and_os_are_normalized(ua, expected):
    got = audience_svc.normalize_agent(ua)
    assert (got["device_type"], got["browser"], got["os"]) == expected
    assert got["client_type"] == "web"


def test_5b_watch_records_detected_classes_and_never_the_user_agent(w):
    ev = w.event(required=True)
    token = register(ev).json()["token"]
    assert watch(ev, reg=token, ua=UA_IPHONE, client="embedded").status_code == 200
    (row,) = w.rows(ev)
    assert (row.device_type, row.browser, row.os, row.client_type) == ("mobile", "safari", "ios", "embedded")
    stored = " ".join(str(getattr(row, c.name)) for c in AudienceSession.__table__.columns)
    assert "iPhone" not in stored and "Mozilla" not in stored


def test_5c_an_unknown_client_hint_reads_as_web(w):
    assert audience_svc.normalize_agent(UA_WINDOWS_CHROME, "smart-fridge")["client_type"] == "web"
    assert audience_svc.normalize_agent(UA_WINDOWS_CHROME, "MOBILE")["client_type"] == "mobile"


def test_6_staff_and_anonymous_disposable_viewers_are_not_recorded(w):
    ev = w.event()
    # The organizing organization's own admin watching their event gets the stream...
    assert watch(ev, http=w.admin_client()).json()["livekit_token"] == "stream-token"
    # ...and a visitor with no credential is held at the registration gate.
    assert watch(ev).json()["livekit_token"] is None
    assert w.rows(ev) == []
    # The disposable `viewer-<uuid>` identity cannot be told apart from the next request's,
    # so it is never recorded even when handed to the recorder directly.
    audience_svc.record_join(w.db, ev, f"viewer-{uuid.uuid4()}", user_agent=UA_WINDOWS_CHROME)
    assert w.rows(ev) == []


def test_7_reconnects_never_inflate_unique_viewers(w):
    ev = w.event(required=True)
    token = register(ev).json()["token"]
    for _ in range(4):
        assert watch(ev, reg=token).status_code == 200
    (row,) = w.rows(ev)
    assert row.join_count == 1
    assert w.insights(event_id=ev.id)["viewers"] == 1


def test_7b_a_real_return_after_leaving_counts_as_another_join_not_another_viewer(w):
    ev = w.event(required=True)
    token = register(ev).json()["token"]
    watch(ev, reg=token)
    identity = f"guest-{w.rows(ev)[0].registration_id}"
    audience_svc.record_leave(w.db, ev.id, identity, datetime.now(UTC) - timedelta(minutes=1))
    row = w.rows(ev)[0]
    row.left_at = datetime.now(UTC) - timedelta(minutes=30)
    w.db.commit()
    watch(ev, reg=token)
    (row,) = w.rows(ev)
    assert row.join_count == 2 and row.left_at is None


def test_7c_watch_time_counts_only_time_admitted_while_live(w):
    ev = w.event(required=True)
    token = register(ev).json()["token"]
    watch(ev, reg=token)
    row = w.rows(ev)[0]
    identity = f"guest-{row.registration_id}"
    admitted = datetime.now(UTC) - timedelta(minutes=20)
    row.last_seen_at = admitted
    w.db.commit()
    # Socket opened 5 minutes BEFORE admission (pre-show page): only the 20 admitted minutes count.
    audience_svc.record_leave(w.db, ev.id, identity, admitted - timedelta(minutes=5))
    first = w.rows(ev)[0].watch_seconds
    assert 20 * 60 - 5 <= first <= 20 * 60 + 5
    # A later visit to the ended event's page earns nothing.
    ev.status = "ended"
    w.db.add(BroadcastSession(event_id=ev.id, org_id=ev.org_id, status="ended",
                              started_at=admitted, ended_at=datetime.now(UTC) - timedelta(minutes=1)))
    w.db.commit()
    audience_svc.record_leave(w.db, ev.id, identity, datetime.now(UTC))
    assert w.rows(ev)[0].watch_seconds == first


def _wait_for(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_7d_closing_the_live_socket_records_the_leave_and_the_time_watched(w):
    ev = w.event(required=True)
    token = register(ev).json()["token"]
    watch(ev, reg=token)
    with TestClient(m.app).websocket_connect(f"/api/live/events/{ev.id}/ws?reg={token}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        time.sleep(1.2)
    assert _wait_for(lambda: w.rows(ev)[0].left_at is not None)
    (row,) = w.rows(ev)
    assert row.watch_seconds >= 1 and row.join_count == 1


def test_7e_a_staff_socket_never_creates_or_touches_an_audience_row(w):
    ev = w.event()
    host_token = create_access_token(w.admin, remember=False)
    with TestClient(m.app).websocket_connect(f"/api/live/events/{ev.id}/ws?token={host_token}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
    time.sleep(0.3)
    assert w.rows(ev) == []


# ── 8-11: aggregation ───────────────────────────────────────────────────────────────────────

def test_8_geography_counts_unique_viewers_and_reports_the_unknown(w):
    ev = w.event()
    for country in ("IN", "IN", "IN", "US", None):
        w.session_row(ev, country=country)
    out = w.insights()
    assert out["geography"] == [
        {"country_code": "IN", "country_name": "India", "viewers": 3, "percentage": 75},
        {"country_code": "US", "country_name": "United States", "viewers": 1, "percentage": 25},
    ]
    assert (out["geography_known"], out["geography_unknown"], out["viewers"]) == (4, 1, 5)


def test_8b_no_geography_is_an_empty_list_not_an_invented_one(w):
    w.session_row(w.event(), country=None)
    out = w.insights()
    assert out["geography"] == [] and out["geography_unknown"] == 1


def test_9_device_mix_is_real_aggregates(w):
    ev = w.event()
    for device, browser, os in (("desktop", "chrome", "windows"), ("desktop", "edge", "windows"),
                                ("mobile", "safari", "ios"), ("tablet", "chrome", "android")):
        w.session_row(ev, device=device, browser=browser, os=os)
    out = w.insights()
    assert [(d["device_type"], d["viewers"], d["percentage"]) for d in out["device_mix"]] == [
        ("desktop", 2, 50), ("mobile", 1, 25), ("tablet", 1, 25)]
    assert {b["browser"]: b["viewers"] for b in out["device_breakdown"]["browsers"]} == {
        "chrome": 2, "edge": 1, "safari": 1}
    assert {o["label"] for o in out["device_breakdown"]["operating_systems"]} == {"Windows", "iOS", "Android"}


def test_10_registration_to_attendance_counts_registrants_who_joined(w):
    ev = w.event(required=True)
    tokens = [register(ev, email=f"r{i}-{uuid.uuid4().hex[:6]}@example.com").json()["token"] for i in range(4)]
    watch(ev, reg=tokens[0])
    watch(ev, reg=tokens[1])
    watch(ev, reg=tokens[1])                       # the same registrant twice is one attendee
    # A registration under one of the organization's own staff addresses is not audience.
    w.registration(ev, email=w.admin.email)
    ra = w.insights()["registration_attendance"]
    assert ra == {"applicable": True, "registered": 4, "attended": 2, "rate": 50}


def test_10b_zero_attendance_is_a_counted_zero(w):
    ev = w.event(required=True)
    register(ev)
    register(ev)
    assert w.insights()["registration_attendance"] == {"applicable": True, "registered": 2, "attended": 0, "rate": 0}


def test_11_no_registrations_is_not_applicable_never_zero_percent(w):
    ev = w.event()                                  # public, nobody has registered
    w.session_row(ev)                               # an access-link viewer watched
    ra = w.insights()["registration_attendance"]
    assert ra == {"applicable": False, "registered": 0, "attended": 0, "rate": None}


# ── 12-14: blocked join attempts ────────────────────────────────────────────────────────────

def test_12_an_invalid_invitation_is_one_blocked_attempt_with_no_secret_stored(w):
    ev = w.event(visibility="private")
    secret = "x" * 40 + uuid.uuid4().hex
    viewer = TestClient(m.app)
    for _ in range(3):                              # a page retrying is one attempt
        r = viewer.post(f"/api/events/{ev.id}/invitation", json={"kind": "invite", "secret": secret})
        assert r.status_code == 404
    (denial,) = w.denials(ev)
    assert denial.reason == "invalid_invite"
    stored = " ".join(str(getattr(denial, c.name)) for c in JoinDenial.__table__.columns)
    assert secret not in stored and "testclient" not in stored


def test_13_private_event_refusals_are_recorded_by_reason(w):
    ev = w.event(visibility="private")
    assert watch(ev).status_code == 403                                   # no credential
    assert watch(ev).status_code == 403                                   # ...retried: same attempt
    assert watch(ev, reg="forged.registration.token").status_code == 403  # a bad one
    reg = w.registration(ev, invited=True)
    token = create_registration_token(reg)
    assert watch(ev, reg=token, http=TestClient(m.app)).status_code == 200      # claims it
    assert watch(ev, reg=token, http=TestClient(m.app)).status_code == 403      # another device
    # Self-serve registration on a private event, from a different browser: another attempt.
    assert register(ev).status_code == 403
    reasons = sorted(d.reason for d in w.denials(ev))
    assert reasons == ["invalid_invite", "invite_used_elsewhere", "not_invited", "not_invited"]
    blocked = w.insights()["blocked_join_attempts"]
    assert blocked["total"] == 4
    assert {r["reason"]: r["count"] for r in blocked["reasons"]} == {
        "invalid_invite": 1, "invite_used_elsewhere": 1, "not_invited": 2}
    assert blocked["reasons"][0] == {"reason": "not_invited", "label": "Private event, not invited", "count": 2}


def test_14_full_and_unpublished_events_record_their_refusals(w):
    full = w.event(required=True, limit=1)
    assert register(full).status_code == 200
    assert register(full).status_code == 409
    draft = w.event(status="draft")
    assert watch(draft).status_code == 404
    assert [d.reason for d in w.denials(full)] == ["event_full"]
    assert [d.reason for d in w.denials(draft)] == ["event_unavailable"]


def test_14b_a_removed_viewer_rejoining_is_recorded(w):
    ev = w.event()
    audience_svc.record_denial_by_id(ev.id, "removed_by_host", "guest-someone")
    audience_svc.record_denial_by_id(ev.id, "removed_by_host", "guest-someone")
    assert [d.reason for d in w.denials(ev)] == ["removed_by_host"]


# ── 15-17: date, organization and event filters ─────────────────────────────────────────────

def test_15_the_date_window_applies_to_every_figure(w):
    ev = w.event()
    old = datetime.now(UTC) - timedelta(days=40)
    w.session_row(ev, country="IN", seen=old)
    w.session_row(ev, country="US")
    w.db.add(JoinDenial(org_id=w.org.id, event_id=ev.id, reason="not_invited", created_at=old))
    w.db.add(JoinDenial(org_id=w.org.id, event_id=ev.id, reason="event_full", created_at=datetime.now(UTC)))
    w.db.commit()
    thirty, ninety = w.insights(range_key="30d"), w.insights(range_key="90d")
    assert [g["country_code"] for g in thirty["geography"]] == ["US"]
    assert {g["country_code"] for g in ninety["geography"]} == {"IN", "US"}
    assert thirty["blocked_join_attempts"]["total"] == 1 and ninety["blocked_join_attempts"]["total"] == 2
    assert (thirty["viewers"], ninety["viewers"]) == (1, 2)


def test_16_another_organizations_audience_is_never_counted(w):
    mine, theirs = w.event(), w.event(org=w.other_org)
    w.session_row(mine, country="IN")
    w.session_row(theirs, country="BR")
    w.session_row(theirs, country="BR")
    w.db.add(JoinDenial(org_id=w.other_org.id, event_id=theirs.id, reason="not_invited", created_at=datetime.now(UTC)))
    w.db.commit()
    out = w.insights()
    assert [g["country_code"] for g in out["geography"]] == ["IN"]
    assert out["blocked_join_attempts"]["total"] == 0
    # Over HTTP the organization comes from the token, and another org's event is not found.
    r = w.admin_client().get("/api/organization/audience-insights", params={"event_id": str(theirs.id)})
    assert r.status_code == 404


def test_17_one_event_can_be_isolated(w):
    a, b = w.event(), w.event()
    w.session_row(a, country="IN", device="mobile")
    w.session_row(b, country="US")
    w.session_row(b, country="US")
    out = w.insights(event_id=a.id)
    assert out["viewers"] == 1
    assert [g["country_code"] for g in out["geography"]] == ["IN"]
    assert [d["device_type"] for d in out["device_mix"]] == ["mobile"]


def test_17b_the_endpoint_returns_the_documented_shape(w):
    ev = w.event(required=True)
    w.session_row(ev, country="IN")
    c = w.admin_client()
    r = c.get("/api/organization/audience-insights", params={"range": "7d", "event_id": str(ev.id)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["geography"][0]) == {"country_code", "country_name", "viewers", "percentage"}
    assert {"device_type", "viewers", "percentage"} <= set(body["device_mix"][0])
    assert set(body["registration_attendance"]) == {"registered", "attended", "rate", "applicable"}
    assert set(body["blocked_join_attempts"]) == {"total", "reasons"}
    assert c.get("/api/organization/audience-insights", params={"range": "1y"}).status_code == 422
    assert TestClient(m.app).get("/api/organization/audience-insights").status_code in (401, 403)


# ── 18-19: privacy ─────────────────────────────────────────────────────────────────────────

def test_18_the_audience_tables_hold_no_address_agent_token_or_identity(w):
    assert {c.name for c in AudienceSession.__table__.columns} == {
        "id", "org_id", "event_id", "viewer_key", "registration_id", "country_code", "device_type",
        "browser", "os", "client_type", "joined_at", "last_seen_at", "left_at", "join_count", "watch_seconds"}
    assert {c.name for c in JoinDenial.__table__.columns} == {
        "id", "org_id", "event_id", "reason", "registration_id", "created_at"}


def test_19_the_viewer_key_is_a_hash_not_the_identity(w):
    ev = w.event(required=True)
    reg_id = register(ev).json()["id"]
    token = create_registration_token(w.db.get(EventRegistration, uuid.UUID(reg_id)))
    watch(ev, reg=token)
    (row,) = w.rows(ev)
    identity = f"guest-{reg_id}"
    assert row.viewer_key == audience_svc.viewer_key(identity) and len(row.viewer_key) == 64
    assert identity not in row.viewer_key
