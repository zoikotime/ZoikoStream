"""Missed: the derived display status for a Scheduled event that never went live.

The Events page used to show "Scheduled" with a "Start time passed" note under it for an event
whose start had long gone by without a broadcast — two competing statuses, and a Scheduled
counter that counted it as upcoming. The API now derives ONE display status
(crud/event.display_states): a Scheduled event past start + grace (EVENT_START_GRACE_MINUTES)
with no broadcast that ever started is "missed". Nothing is stored — status stays "scheduled",
so reschedule, cancel and starting late all still work.

Pinned here through the real routes: the derivation, the grace period, what can never be
Missed (draft, published, live, ended, cancelled, anything that actually broadcast), the
upcoming-only Scheduled counter and filter, the Missed filter, rescheduling back to
Scheduled, and that timezones are compared as instants.
"""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.crud import event as crud
from app.db import Base, SessionLocal
from app.models import AuditLog, BroadcastSession, Event, Organization, User
from app.security import create_access_token, hash_password

UTC = timezone.utc


@pytest.fixture(autouse=True)
def grace(monkeypatch):
    monkeypatch.setattr(settings, "EVENT_START_GRACE_MINUTES", 15)


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Missed {uuid.uuid4().hex[:6]}", status="active")
            db.add(org)
            db.flush()
            admin = User(org_id=org.id, full_name="Ops Admin", role="org_admin", is_active=True,
                         email=f"ms-{uuid.uuid4().hex[:10]}@example.com", username=f"ms{uuid.uuid4().hex[:10]}",
                         password_hash=hash_password("x"), email_verified=True)
            db.add(admin)
            db.commit()
            self.org, self.admin = org.id, admin.id
            self.events = []
        finally:
            db.close()

    def event(self, status, start_in: timedelta | None, *, aired=False, title=None, **kw):
        db = SessionLocal()
        try:
            now = datetime.now(UTC)
            start = now + start_in if start_in is not None else None
            ev = Event(org_id=self.org, created_by=self.admin, title=title or f"E {uuid.uuid4().hex[:5]}",
                       status=status, visibility="public", start_time=start,
                       end_time=(start + timedelta(hours=1)) if start else None, **kw)
            db.add(ev)
            db.flush()
            if aired:
                db.add(BroadcastSession(event_id=ev.id, org_id=self.org, status="ended",
                                        started_at=(start or now), ended_at=(start or now) + timedelta(minutes=10)))
            db.commit()
            self.events.append(ev.id)
            return ev.id
        finally:
            db.close()

    def client(self):
        db = SessionLocal()
        try:
            token = create_access_token(db.get(User, self.admin), remember=False, db=db)
        finally:
            db.close()
        c = TestClient(m.app)
        c.headers["Authorization"] = f"Bearer {token}"
        return c

    def cleanup(self):
        db = SessionLocal()
        try:
            ids = self.events
            if ids:
                for table in reversed(Base.metadata.sorted_tables):
                    for fk in table.foreign_keys:
                        if fk.column.table.name == "events" and table.name != "events":
                            db.execute(table.delete().where(fk.parent.in_(ids)))
                db.query(Event).filter(Event.id.in_(ids)).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.org_id == self.org).delete(synchronize_session=False)
            db.query(User).filter(User.id == self.admin).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id == self.org).delete(synchronize_session=False)
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


def listed(c, **params):
    r = c.get("/api/events", params={"page_size": 100, **params})
    assert r.status_code == 200, r.text
    return {row["id"]: row for row in r.json()["items"]}


def shown(c, event_id):
    r = c.get(f"/api/events/{event_id}")
    assert r.status_code == 200, r.text
    return r.json()


H, M = timedelta(hours=1), timedelta(minutes=1)


# ── 1-9: the derivation ───────────────────────────────────────────────────────────────────

def test_1_a_future_scheduled_event_is_scheduled(w):
    eid = w.event("scheduled", 2 * H)
    row = shown(w.client(), eid)
    assert (row["status"], row["display_status"], row["start_overdue"]) == ("scheduled", "scheduled", False)


def test_2_a_past_scheduled_event_that_never_went_live_is_missed(w):
    eid = w.event("scheduled", -3 * H)
    row = shown(w.client(), eid)
    # Derived only: the stored lifecycle is untouched.
    assert (row["status"], row["display_status"], row["start_overdue"]) == ("scheduled", "missed", True)


def test_3_inside_the_grace_period_it_is_still_scheduled(w):
    eid = w.event("scheduled", -5 * M)                 # 5 min late, grace is 15
    assert shown(w.client(), eid)["display_status"] == "scheduled"


def test_4_after_the_grace_period_it_is_missed(w):
    eid = w.event("scheduled", -20 * M)
    assert shown(w.client(), eid)["display_status"] == "missed"


def test_4b_the_grace_period_is_configurable(w, monkeypatch):
    eid = w.event("scheduled", -5 * M)
    monkeypatch.setattr(settings, "EVENT_START_GRACE_MINUTES", 0)
    assert shown(w.client(), eid)["display_status"] == "missed"
    monkeypatch.setattr(settings, "EVENT_START_GRACE_MINUTES", 60)
    assert shown(w.client(), eid)["display_status"] == "scheduled"


@pytest.mark.parametrize("status", ["draft", "cancelled", "ended", "live", "degraded", "archived"])
def test_5_to_8_other_statuses_are_never_missed(w, status):
    eid = w.event(status, -3 * H, **({"previous_status": "ended"} if status == "archived" else {}))
    row = shown(w.client(), eid)
    assert row["display_status"] == status and row["start_overdue"] is False


def test_5b_a_draft_with_an_old_tentative_time_stays_draft(w):
    eid = w.event("draft", -48 * H)
    assert shown(w.client(), eid)["display_status"] == "draft"


@pytest.mark.parametrize("status", ["published", "ready_to_arm", "armed"])
def test_8b_other_pre_live_statuses_keep_their_label_and_note_the_late_start(w, status):
    eid = w.event(status, -3 * H)
    row = shown(w.client(), eid)
    assert (row["display_status"], row["start_overdue"]) == (status, True)


def test_9_an_event_that_actually_broadcast_is_never_missed(w):
    # Evidence over status: a broadcast session that really started outranks the label.
    eid = w.event("scheduled", -3 * H, aired=True)
    row = shown(w.client(), eid)
    assert (row["display_status"], row["start_overdue"]) == ("scheduled", False)
    ended = w.event("ended", -3 * H, aired=True)
    assert shown(w.client(), ended)["display_status"] == "ended"


# ── 10: recovery ──────────────────────────────────────────────────────────────────────────

def test_10_rescheduling_a_missed_event_to_the_future_makes_it_scheduled_at_once(w):
    eid = w.event("scheduled", -3 * H)
    c = w.client()
    assert shown(c, eid)["display_status"] == "missed"
    start = datetime.now(UTC) + 2 * H
    r = c.patch(f"/api/events/{eid}", json={"start_time": start.isoformat(),
                                             "end_time": (start + H).isoformat()})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["display_status"]) == ("scheduled", "scheduled")
    assert shown(c, eid)["display_status"] == "scheduled"


def test_10b_a_missed_event_can_still_be_cancelled(w):
    eid = w.event("scheduled", -3 * H)
    r = w.client().patch(f"/api/events/{eid}", json={"status": "cancelled"})
    assert r.status_code == 200, r.text
    assert r.json()["display_status"] == "cancelled"


# ── 11-12: counters and filters ───────────────────────────────────────────────────────────

def test_11_the_scheduled_counter_counts_upcoming_events_only(w):
    w.event("scheduled", 2 * H)
    w.event("scheduled", 3 * H)
    w.event("scheduled", -3 * H)
    w.event("scheduled", -5 * H)
    w.event("scheduled", -2 * H, aired=True)            # broadcast happened: not missed
    body = w.client().get("/api/events/status-counts").json()
    assert body["counts"]["scheduled"] == 5               # canonical lifecycle, unchanged
    assert body["display_counts"]["scheduled"] == 3       # upcoming (incl. the aired one)
    assert body["display_counts"]["missed"] == 2


def test_12_the_missed_and_scheduled_filters_split_cleanly(w):
    upcoming, missed, aired = w.event("scheduled", 2 * H), w.event("scheduled", -3 * H), \
        w.event("scheduled", -2 * H, aired=True)
    late_published = w.event("published", -3 * H)
    c = w.client()
    ids = lambda *xs: {str(x) for x in xs}  # noqa: E731
    assert set(listed(c, display_status="missed")) == ids(missed)
    assert set(listed(c, display_status="scheduled")) == ids(upcoming, aired)
    assert set(listed(c, display_status="published")) == ids(late_published)
    # The canonical filter keeps its meaning for every existing caller.
    assert set(listed(c, status="scheduled")) == ids(upcoming, missed, aired)
    # "missed" is a display filter only: it is never a status anyone can write.
    assert c.get("/api/events", params={"status": "missed"}).status_code == 422
    assert c.patch(f"/api/events/{upcoming}", json={"status": "missed"}).status_code == 422


def test_12b_the_unfiltered_list_labels_every_row(w):
    future, past = w.event("scheduled", 2 * H), w.event("scheduled", -3 * H)
    rows = listed(w.client())
    assert rows[str(future)]["display_status"] == "scheduled"
    assert rows[str(past)]["display_status"] == "missed"


def test_12c_another_organizations_missed_events_are_not_counted(w):
    other = World()
    try:
        other.event("scheduled", -3 * H)
        assert w.client().get("/api/events/status-counts").json()["display_counts"]["missed"] == 0
    finally:
        other.cleanup()


# ── 13-14: time ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("zone", ["Asia/Kolkata", "America/New_York", "UTC"])
def test_13_instants_are_compared_whatever_the_event_timezone(w, zone):
    """The stored UTC instant decides; the event's display timezone never does."""
    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        local = ZoneInfo(zone)
        missed = Event(org_id=w.org, created_by=w.admin, title="tz past", status="scheduled",
                       visibility="public", timezone=zone,
                       start_time=(now - 30 * M).astimezone(local))
        upcoming = Event(org_id=w.org, created_by=w.admin, title="tz future", status="scheduled",
                         visibility="public", timezone=zone,
                         start_time=(now + 30 * M).astimezone(local))
        db.add_all([missed, upcoming])
        db.commit()
        w.events += [missed.id, upcoming.id]
        states = crud.display_states(db, [missed, upcoming], now=now)
        assert states[missed.id] == ("missed", True)
        assert states[upcoming.id] == ("scheduled", False)
    finally:
        db.close()


def test_14_the_label_moves_from_scheduled_to_missed_as_time_passes(w):
    """What the Events page's poll sees: the same row, re-read later, changes label."""
    eid = w.event("scheduled", 10 * M)
    db = SessionLocal()
    try:
        ev = db.get(Event, eid)
        now = datetime.now(UTC)
        assert crud.display_states(db, [ev], now=now)[eid][0] == "scheduled"
        assert crud.display_states(db, [ev], now=now + 20 * M)[eid][0] == "scheduled"   # in grace
        assert crud.display_states(db, [ev], now=now + 30 * M)[eid][0] == "missed"
        # The SQL filter agrees with the row-level rule at every one of those instants.
        for later, expect in ((now, False), (now + 20 * M, False), (now + 30 * M, True)):
            ids, _ = crud.list_events(db, w.org, display_status="missed", now=later, page_size=100)
            assert (eid in {e.id for e in ids}) is expect
    finally:
        db.close()
