"""Every status the Events page offers is a real, reachable, persisted and filterable state.

Driven through the product's own paths against the real database: the Events API (create,
PATCH lifecycle, archive/unarchive, End), the host console's Go Live
(services/broadcast._golive, the same coroutine the socket calls), and the media sampler's
degraded/recovered writers. Two things are switched off because they would leave the
machine or are tested elsewhere: LiveKit (unconfigured, so no room calls) and the commercial
readiness evaluation (stubbed "ready"; its own rules are pinned in test_payment_path.py and
test_events.py checks that a refusal blocks arming).

    draft -> scheduled -> ready_to_arm -> armed -> (disarm, re-arm) -> live -> degraded ->
    live -> ended -> archived -> unarchive -> ended            (the normal event)
    scheduled -> cancelled -> archived -> unarchive             (cancelled before air)
    draft -> published -> scheduled                             (publish, then schedule)
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.crud import commercial as commercial_crud
from app.db import Base, SessionLocal
from app.models import (AuditLog, BroadcastSession, Event, EventRegistration, MediaAssetEvent, Organization,
                        User)
from app.models.event import EVENT_STATUSES
from app.security import create_access_token, hash_password
from app.services import broadcast, bus, livekit
from app.services import moderation as mod

FUTURE = lambda days=3: (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()   # noqa: E731


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "LIVEKIT_URL", "")            # livekit.configured() -> False
    assert not livekit.configured()
    readiness = {"ready": True, "blocking_reasons": []}
    monkeypatch.setattr(commercial_crud, "golive_readiness", lambda db, ev: dict(readiness))
    monkeypatch.setattr(commercial_crud, "audit_golive_decision", lambda *a, **k: None)
    return readiness


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            self.orgs, self.users = [], []
            self.a = self._org(db, "Lifecycle A")
            self.b = self._org(db, "Lifecycle B")
            self.admin_a = self._user(db, self.a, "org_admin")
            self.admin_b = self._user(db, self.b, "org_admin")
            db.commit()
        finally:
            db.close()

    def _org(self, db, name):
        o = Organization(name=f"{name} {uuid.uuid4().hex[:6]}", status="active")
        db.add(o)
        db.flush()
        self.orgs.append(o.id)
        return o.id

    def _user(self, db, org_id, role):
        u = User(org_id=org_id, full_name=role.title(), role=role, is_active=True,
                 email=f"lc-{uuid.uuid4().hex[:10]}@example.com", username=f"lc{uuid.uuid4().hex[:10]}",
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

    def event(self, org_id, status, **kw):
        """A row written directly - for filter/count datasets, never for lifecycle moves."""
        db = SessionLocal()
        try:
            creator = self.admin_a if org_id == self.a else self.admin_b
            ev = Event(org_id=org_id, created_by=creator, title=kw.pop("title", f"E {uuid.uuid4().hex[:5]}"),
                       status=status, **kw)
            db.add(ev)
            db.commit()
            return ev.id
        finally:
            db.close()

    def status(self, event_id):
        db = SessionLocal()
        try:
            ev = db.get(Event, event_id)
            return ev.status, ev.previous_status, ev.end_time
        finally:
            db.close()

    def go_live(self, event_id):
        """The host console's Go Live, exactly as routers/live.py dispatches it."""
        ctx = mod.Ctx(event_id=event_id, org_id=self.a, room=livekit.room_for_event(event_id),
                      user_id=self.admin_a, name="Admin", identity=f"host-{self.admin_a}",
                      role="org_admin", can_moderate=True, can_host=True)
        return asyncio.run(broadcast._golive(ctx, {}))

    def cleanup(self):
        db = SessionLocal()
        try:
            ids = [e for (e,) in db.query(Event.id).filter(Event.org_id.in_(self.orgs)).all()]
            # Every table with a foreign key to events: the lifecycle writes rows into several of
            # them (readiness state, schedule changes, sessions, ...), and Postgres refuses the
            # event delete below while any remain (SQLite never enforced those keys). Reverse
            # dependency order, so a child table is emptied before anything it points at.
            for table in reversed(Base.metadata.sorted_tables):
                for fk in table.foreign_keys:
                    if fk.column.table.name == "events" and table.name != "events":
                        db.execute(table.delete().where(fk.parent.in_(ids)))
            db.query(MediaAssetEvent).filter(MediaAssetEvent.org_id.in_(self.orgs)).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.org_id.in_(self.orgs)).delete(synchronize_session=False)
            db.query(Event).filter(Event.id.in_(ids)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(self.users)).delete(synchronize_session=False)
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


def ids(resp):
    assert resp.status_code == 200, resp.text
    return [e["id"] for e in resp.json()["items"]]


def counts(c):
    r = c.get("/api/events/status-counts")
    assert r.status_code == 200, r.text
    return r.json()["counts"]


# ── the normal event, start to finish ───────────────────────────────────────────────────

def test_the_normal_lifecycle_end_to_end(w):
    admin, anon = w.client(w.admin_a), w.client()

    r = admin.post("/api/events", json={"title": "Launch", "status": "draft"})
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    assert w.status(eid)[0] == "draft"
    # A draft does not exist outside its organization.
    assert anon.get(f"/api/events/{eid}/watch").status_code == 404
    assert anon.post(f"/api/events/{eid}/register", json={"name": "Early"}).status_code == 404
    assert admin.get(f"/api/events/{eid}/watch").json()["status"] == "draft"      # preview
    assert counts(admin)["draft"] == 1
    assert ids(admin.get("/api/events", params={"status": "draft"})) == [eid]

    # Scheduled needs a real future start.
    r = admin.patch(f"/api/events/{eid}", json={"status": "scheduled"})
    assert r.status_code == 400 and "start date" in r.json()["detail"]
    r = admin.patch(f"/api/events/{eid}", json={"status": "scheduled", "start_time": FUTURE()})
    assert r.status_code == 200 and r.json()["status"] == "scheduled"
    assert anon.get(f"/api/events/{eid}/watch").json()["status"] == "scheduled"
    assert anon.post(f"/api/events/{eid}/register", json={"name": "Guest"}).status_code == 200

    # Ready -> armed -> disarm -> re-arm.
    for target in ("ready_to_arm", "armed", "ready_to_arm", "armed"):
        r = admin.patch(f"/api/events/{eid}", json={"status": target})
        assert r.status_code == 200 and r.json()["status"] == target, r.text
    assert ids(admin.get("/api/events", params={"status": "armed"})) == [eid]

    # A status write can never claim a broadcast.
    r = admin.patch(f"/api/events/{eid}", json={"status": "live"})
    assert r.status_code == 400 and "Go Live" in r.json()["detail"]
    assert w.status(eid)[0] == "armed"

    # Go Live (the real host-console path) -> live, persisted.
    frames = w.go_live(eid)
    assert frames[0][1] == "broadcast.update", frames
    assert w.status(eid)[0] == "live"
    assert counts(admin)["live"] == 1
    assert ids(admin.get("/api/events", params={"status": "live"})) == [eid]
    assert anon.get(f"/api/events/{eid}/watch").json()["status"] == "live"

    # The media sampler: degraded, then recovered.
    assert asyncio.run(broadcast.mark_degraded(str(eid), str(w.a), "producer stopped publishing"))
    assert w.status(eid)[0] == "degraded"
    assert ids(admin.get("/api/events", params={"status": "degraded"})) == [eid]
    assert counts(admin)["degraded"] == 1
    assert asyncio.run(broadcast.mark_recovered(str(eid), str(w.a)))
    assert w.status(eid)[0] == "live"

    # Live can't be cancelled; End is the way out.
    r = admin.patch(f"/api/events/{eid}", json={"status": "cancelled"})
    assert r.status_code == 400 and "End the broadcast" in r.json()["detail"]
    r = admin.post(f"/api/events/{eid}/end")
    assert r.status_code == 200 and r.json()["status"] == "ended"
    status, _, end_time = w.status(eid)
    assert status == "ended" and end_time is not None
    assert counts(admin)["live"] == 0 and counts(admin)["ended"] == 1
    assert anon.get(f"/api/events/{eid}/watch").json()["status"] == "ended"

    # Ended is final: neither a PATCH nor Go Live brings it back.
    assert admin.patch(f"/api/events/{eid}", json={"status": "scheduled", "start_time": FUTURE()}).status_code == 400
    refused = w.go_live(eid)
    assert refused[0][1] == "broadcast.error", refused
    assert w.status(eid)[0] == "ended"

    # Archive: out of the working list, nothing lost, viewers still see an ended event.
    r = admin.post(f"/api/events/{eid}/archive")
    assert r.status_code == 200 and r.json()["status"] == "archived"
    assert w.status(eid)[:2] == ("archived", "ended")
    assert eid not in ids(admin.get("/api/events"))
    assert ids(admin.get("/api/events", params={"status": "archived"})) == [eid]
    assert counts(admin)["archived"] == 1 and counts(admin)["ended"] == 0
    assert anon.get(f"/api/events/{eid}/watch").json()["status"] == "ended"
    assert admin.post(f"/api/events/{eid}/archive").json()["status"] == "archived"     # twice: no change

    r = admin.post(f"/api/events/{eid}/unarchive")
    assert r.status_code == 200 and r.json()["status"] == "ended"
    assert w.status(eid)[:2] == ("ended", None)
    assert admin.post(f"/api/events/{eid}/unarchive").json()["status"] == "ended"      # twice: no change


def test_cancelled_before_air(w, offline):
    admin, anon = w.client(w.admin_a), w.client()
    eid = admin.post("/api/events", json={"title": "Wedding", "status": "scheduled",
                                         "start_time": FUTURE()}).json()["id"]
    assert anon.post(f"/api/events/{eid}/register", json={"name": "Guest"}).status_code == 200

    r = admin.patch(f"/api/events/{eid}", json={"status": "cancelled"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert admin.patch(f"/api/events/{eid}", json={"status": "cancelled"}).status_code == 200   # twice
    assert counts(admin)["cancelled"] == 1 and counts(admin)["ended"] == 0
    assert ids(admin.get("/api/events", params={"status": "cancelled"})) == [eid]

    watch = anon.get(f"/api/events/{eid}/watch")
    assert watch.status_code == 200 and watch.json()["status"] == "cancelled"
    assert watch.json()["livekit_token"] is None
    r = anon.post(f"/api/events/{eid}/register", json={"name": "Late"})
    assert r.status_code == 409 and "cancelled" in r.json()["detail"]
    assert w.go_live(eid)[0][1] == "broadcast.error"
    assert w.status(eid)[0] == "cancelled"
    r = admin.patch(f"/api/events/{eid}", json={"status": "scheduled", "start_time": FUTURE()})
    assert r.status_code == 400 and "can't be reopened" in r.json()["detail"]

    assert admin.post(f"/api/events/{eid}/archive").json()["status"] == "archived"
    assert anon.get(f"/api/events/{eid}/watch").json()["status"] == "cancelled"
    assert admin.post(f"/api/events/{eid}/unarchive").json()["status"] == "cancelled"


def test_publish_without_a_time_then_schedule(w):
    admin = w.client(w.admin_a)
    eid = admin.post("/api/events", json={"title": "TBD", "status": "draft"}).json()["id"]
    assert admin.patch(f"/api/events/{eid}", json={"status": "published"}).json()["status"] == "published"
    assert w.client().get(f"/api/events/{eid}/watch").json()["status"] == "published"
    r = admin.patch(f"/api/events/{eid}", json={"status": "scheduled",
                                               "start_time": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    assert r.status_code == 400 and "past" in r.json()["detail"]
    r = admin.patch(f"/api/events/{eid}", json={"status": "scheduled", "start_time": FUTURE()})
    assert r.json()["status"] == "scheduled"
    # Rescheduling a scheduled event into the past is refused too.
    r = admin.patch(f"/api/events/{eid}", json={"start_time": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()})
    assert r.status_code == 400


def test_arming_needs_readiness(w, offline):
    admin = w.client(w.admin_a)
    eid = admin.post("/api/events", json={"title": "Gated", "status": "scheduled", "start_time": FUTURE()}).json()["id"]
    admin.patch(f"/api/events/{eid}", json={"status": "ready_to_arm"})
    offline["ready"], offline["blocking_reasons"] = False, ["capacity not reserved"]
    r = admin.patch(f"/api/events/{eid}", json={"status": "armed"})
    assert r.status_code == 400 and "capacity not reserved" in r.json()["detail"]
    assert w.status(eid)[0] == "ready_to_arm"


# ── invalid moves, retired values, double clicks ────────────────────────────────────────

@pytest.mark.parametrize("start,target,needle", [
    ("draft", "armed", "ready_to_arm"),
    ("draft", "ready_to_arm", "published or scheduled"),
    ("scheduled", "ended", "not live"),
    ("scheduled", "degraded", "Only a live event"),
    ("scheduled", "live", "Go Live"),
    ("ended", "live", "can't go live again"),
    ("ended", "scheduled", "can't be moved back"),
    ("cancelled", "published", "can't be reopened"),
    ("ended", "archived", "Use Archive"),
    ("archived", "ended", "Use Unarchive"),
])
def test_invalid_transitions_are_refused_with_a_reason(w, start, target, needle):
    eid = w.event(w.a, start, start_time=datetime.now(timezone.utc) + timedelta(days=2),
                  previous_status="ended" if start == "archived" else None)
    r = w.client(w.admin_a).patch(f"/api/events/{eid}", json={"status": target})
    assert r.status_code == 400, r.text
    assert needle in r.json()["detail"]
    assert w.status(eid)[0] == start


@pytest.mark.parametrize("retired", ["rehearsal", "ending", "processing", "replay_ready", "blocked"])
def test_retired_statuses_are_refused_everywhere(w, retired):
    eid = w.event(w.a, "scheduled", start_time=datetime.now(timezone.utc) + timedelta(days=2))
    c = w.client(w.admin_a)
    assert c.patch(f"/api/events/{eid}", json={"status": retired}).status_code == 422
    assert c.get("/api/events", params={"status": retired}).status_code == 422
    assert retired not in counts(c)
    # And the database itself refuses to hold one (ck_events_status).
    db = SessionLocal()
    try:
        db.get(Event, eid).status = retired
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_go_live_twice_opens_one_broadcast(w):
    admin = w.client(w.admin_a)
    eid = admin.post("/api/events", json={"title": "Twice", "status": "scheduled", "start_time": FUTURE()}).json()["id"]
    w.go_live(eid)
    w.go_live(eid)
    db = SessionLocal()
    try:
        assert db.query(BroadcastSession).filter(BroadcastSession.event_id == eid).count() == 1
    finally:
        db.close()
    assert w.status(eid)[0] == "live"
    assert admin.post(f"/api/events/{eid}/end").json()["status"] == "ended"
    second = admin.post(f"/api/events/{eid}/end")
    assert second.status_code == 400 and w.status(eid)[0] == "ended"


# ── filter and counts: the whole organization, and only it ──────────────────────────────

def test_filter_and_counts_cover_every_event_and_only_this_organization(w):
    future = datetime.now(timezone.utc) + timedelta(days=2)
    made = {"draft": 12, "scheduled": 8, "ended": 3, "cancelled": 2, "archived": 2, "live": 1}
    by_status = {s: [w.event(w.a, s, start_time=future, previous_status="ended" if s == "archived" else None)
                     for _ in range(n)] for s, n in made.items()}
    for _ in range(5):
        w.event(w.b, "scheduled", start_time=future)

    a = w.client(w.admin_a)
    got = counts(a)
    assert set(got) == set(EVENT_STATUSES)
    assert {s: got[s] for s in made} == made
    assert got["published"] == 0

    # Server-side: every page of a filter holds only that status, and the total is exact.
    seen = []
    for page in (1, 2):
        r = a.get("/api/events", params={"status": "draft", "page": page, "page_size": 10})
        assert r.json()["total"] == 12
        seen += ids(r)
    assert sorted(seen) == sorted(str(i) for i in by_status["draft"])
    for status, n in made.items():
        r = a.get("/api/events", params={"status": status, "page_size": 100})
        assert r.json()["total"] == n and {e["status"] for e in r.json()["items"]} <= {status}
    # "All" is every status except archived.
    assert a.get("/api/events", params={"page_size": 100}).json()["total"] == sum(made.values()) - 2

    # Organization B sees only its own.
    b = w.client(w.admin_b)
    assert counts(b)["scheduled"] == 5 and counts(b)["draft"] == 0
    assert b.get("/api/events", params={"page_size": 100}).json()["total"] == 5
    eid = by_status["scheduled"][0]
    assert b.patch(f"/api/events/{eid}", json={"status": "cancelled"}).status_code == 404
    assert b.post(f"/api/events/{eid}/archive").status_code == 404
    assert w.status(eid)[0] == "scheduled"


def test_completed_count_excludes_archived_cancellations(w):
    w.event(w.a, "ended")
    w.event(w.a, "archived", previous_status="ended")
    w.event(w.a, "archived", previous_status="cancelled")
    w.event(w.a, "cancelled")
    stats = w.client(w.admin_a).get("/api/dashboard/org/stats").json()
    assert stats["completed_events"] == 2


def test_guests_cannot_join_a_drafts_or_cancelled_events_live_socket(w):
    for status in ("draft", "cancelled"):
        eid = w.event(w.a, status)
        db = SessionLocal()
        try:
            reg = EventRegistration(event_id=eid, name="Guest", email=f"{uuid.uuid4().hex[:8]}@example.com")
            db.add(reg)
            db.commit()
            db.refresh(reg)
        finally:
            db.close()
        assert mod.resolve_ctx_from_registration(eid, reg) is None, status
    eid = w.event(w.a, "scheduled", start_time=datetime.now(timezone.utc) + timedelta(days=1))
    db = SessionLocal()
    try:
        reg = EventRegistration(event_id=eid, name="Guest", email=f"{uuid.uuid4().hex[:8]}@example.com")
        db.add(reg)
        db.commit()
        db.refresh(reg)
    finally:
        db.close()
    assert mod.resolve_ctx_from_registration(eid, reg) is not None
