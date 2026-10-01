"""Capacity protection: new viewers may wait, active viewers are never kicked.

ZST-SPEC-VAP-001 §6.3 ("Capacity protection: new sessions wait; active sessions are never
evicted") and §7.1, built on the EXISTING watch flow (services/admission.py):

  1. below the ceiling a new viewer is admitted;
  2. at the ceiling a new viewer gets admission="waiting" + retry_after_seconds, no token,
     and stays registered (so the page never discards their credential);
  3. a viewer already watching stays admitted however full the event is;
  4. nothing on this path removes anyone from LiveKit or revokes anything;
  5. a waiting viewer gets in once capacity frees up;
  6. a viewer who drops and comes back keeps their place;
  7. no commercial or plan figure caps or ejects viewers, and staff are never held.
"""
import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import AuditLog, Event, EventAccessLink, EventAssignment, EventRegistration, Organization, PlatformSetting, User
from app.security import create_access_token, hash_password
from app.services import admission, bus, livekit, platform_settings

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _in_process_bus(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    yield


@pytest.fixture
def world():
    db = SessionLocal()
    org = Organization(name=f"Admission {uuid.uuid4().hex[:6]}", status="active")
    db.add(org)
    db.flush()
    admin = User(org_id=org.id, full_name="Vihari", role="org_admin", is_active=True,
                 email=f"ad-{uuid.uuid4().hex[:10]}@example.com", username=f"ad{uuid.uuid4().hex[:10]}",
                 password_hash=hash_password("x"), email_verified=True)
    db.add(admin)
    db.flush()
    now = datetime.now(UTC)
    ev = Event(org_id=org.id, created_by=admin.id, title="Memorial", category="Funeral / Memorial",
               status="live", visibility="public", chat_enabled=True, qa_enabled=True, polls_enabled=True,
               start_time=now - timedelta(minutes=5), end_time=now + timedelta(hours=1))
    db.add(ev)
    db.commit()
    ns = type("W", (), {"db": db, "org": org, "admin": admin, "event": ev})()
    try:
        yield ns
    finally:
        db.rollback()
        bus._admitted_memory.pop(str(ev.id), None)  # noqa: SLF001
        for model in (EventRegistration, EventAccessLink, EventAssignment):
            db.query(model).filter(model.event_id == ev.id).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.org_id == org.id).delete(synchronize_session=False)
        db.query(Event).filter(Event.id == ev.id).delete(synchronize_session=False)
        db.query(User).filter(User.org_id == org.id).delete(synchronize_session=False)
        db.query(Organization).filter(Organization.id == org.id).delete(synchronize_session=False)
        db.commit()
        db.close()


@pytest.fixture
def ceiling(monkeypatch):
    """Set the infrastructure ceiling without touching the shared platform_settings row."""
    def set_to(value):
        monkeypatch.setattr(admission.platform_settings, "viewer_admission_ceiling", lambda db: value)
    return set_to


@pytest.fixture
def no_removals(monkeypatch):
    """Any attempt to take something away from a viewer fails the test outright."""
    calls = []

    def forbidden(name):
        async def _fail(*a, **k):
            calls.append(name)
            raise AssertionError(f"capacity protection called livekit.{name}")
        return _fail

    for name in ("remove_participant", "close_room", "mute_participant"):
        monkeypatch.setattr(livekit, name, forbidden(name))
    return calls


def viewer(world, name):
    """A self-registered viewer of the public event: (client, reg token)."""
    c = TestClient(m.app)
    r = c.post(f"/api/events/{world.event.id}/register", json={"name": name})
    assert r.status_code == 200, r.text
    return c, r.json()["token"]


def watch(client, world, token):
    r = client.get(f"/api/events/{world.event.id}/watch", params={"reg": token})
    assert r.status_code == 200, r.text
    return r.json()


def test_1_below_the_ceiling_a_new_viewer_is_admitted(world, ceiling):
    ceiling(2)
    c, t = viewer(world, "Aunt May")
    w = watch(c, world, t)
    assert w["admission"] == "admitted" and w["livekit_token"]


def test_2_at_the_ceiling_a_new_viewer_waits(world, ceiling, no_removals):
    ceiling(1)
    first = viewer(world, "Aunt May")
    assert watch(first[0], world, first[1])["admission"] == "admitted"
    late = viewer(world, "Cousin Ray")
    w = watch(late[0], world, late[1])
    assert w["admission"] == "waiting"
    assert w["retry_after_seconds"] == admission.RETRY_AFTER_SECONDS
    assert w["livekit_token"] is None and w["room"] is None
    # Still recognised as registered, so the page keeps the credential and simply waits.
    assert w["registered"] is True
    assert w["status"] == "live" and no_removals == []


def test_3_a_viewer_already_watching_stays_admitted_however_full(world, ceiling, no_removals):
    ceiling(1)
    first = viewer(world, "Aunt May")
    assert watch(first[0], world, first[1])["admission"] == "admitted"
    for name in ("B", "C", "D"):            # the event fills up behind them
        late = viewer(world, name)
        assert watch(late[0], world, late[1])["admission"] == "waiting"
    for _ in range(3):                       # polls / refreshes keep their token coming
        w = watch(first[0], world, first[1])
        assert w["admission"] == "admitted" and w["livekit_token"]
    assert no_removals == []


def test_4_nothing_on_the_admission_path_removes_or_revokes_anyone(world, ceiling, no_removals):
    ceiling(1)
    clients = [viewer(world, f"V{i}") for i in range(4)]
    for c, t in clients * 2:
        watch(c, world, t)
    assert no_removals == []
    # And by construction: the admission module has no way to reach LiveKit at all.
    source = inspect.getsource(admission)
    for call in ("remove_participant", "close_room", "mute_participant", "revoke", "presence_remove"):
        assert call not in source.split('"""', 2)[-1], call


def test_5_a_waiting_viewer_joins_once_capacity_frees(world, ceiling):
    ceiling(1)
    first = viewer(world, "Aunt May")
    watch(first[0], world, first[1])
    late = viewer(world, "Cousin Ray")
    assert watch(late[0], world, late[1])["admission"] == "waiting"
    # The first viewer has not been seen for longer than the hold window (they closed the tab).
    for rec in bus._admitted_memory[str(world.event.id)].values():  # noqa: SLF001
        rec["seen"] -= admission.HOLD_SECONDS + 1
    w = watch(late[0], world, late[1])
    assert w["admission"] == "admitted" and w["livekit_token"]


def test_6_a_reconnecting_viewer_keeps_their_place(world, ceiling):
    import asyncio
    ceiling(1)
    first = viewer(world, "Aunt May")
    watch(first[0], world, first[1])
    late = viewer(world, "Cousin Ray")
    assert watch(late[0], world, late[1])["admission"] == "waiting"
    # The socket drops: presence is deleted, exactly as routers/live.py does on disconnect.
    identity = f"guest-{EventRegistrationId(world, 'Aunt May')}"
    asyncio.run(bus.presence_upsert(world.event.id, identity, {"role": "viewer", "name": "Aunt May"}))
    asyncio.run(bus.presence_remove(world.event.id, identity))
    # Coming back — even after being away past the hold window — is never refused.
    for rec in bus._admitted_memory[str(world.event.id)].values():  # noqa: SLF001
        rec["seen"] -= admission.HOLD_SECONDS + 1
    w = watch(first[0], world, first[1])
    assert w["admission"] == "admitted" and w["livekit_token"]


def EventRegistrationId(world, name):  # noqa: N802 — a tiny lookup helper, named for what it returns
    world.db.expire_all()
    return world.db.scalar(select(EventRegistration.id).where(
        EventRegistration.event_id == world.event.id, EventRegistration.name == name))


def test_7_no_plan_or_commercial_figure_caps_or_ejects_viewers(world, no_removals):
    # No ceiling configured (the default): an expected audience of 1 is a readiness figure,
    # never a viewer cap.
    world.event.expected_audience = 1
    world.db.commit()
    for i in range(5):
        c, t = viewer(world, f"V{i}")
        assert watch(c, world, t)["admission"] == "admitted"
    source = inspect.getsource(admission).split('"""', 2)[-1]
    for commercial in ("plan", "subscription", "entitlement", "expected_audience", "Plan"):
        assert commercial not in source, commercial
    assert no_removals == []


def test_event_staff_are_never_held_and_take_no_slot(world, ceiling):
    ceiling(1)
    first = viewer(world, "Aunt May")
    watch(first[0], world, first[1])
    staff = TestClient(m.app)
    staff.headers["Authorization"] = f"Bearer {create_access_token(world.admin, remember=False)}"
    r = staff.get(f"/api/events/{world.event.id}/watch").json()
    assert r["admission"] == "admitted" and r["livekit_token"]
    assert str(world.admin.id) not in bus._admitted_memory[str(world.event.id)]  # noqa: SLF001


def test_a_ceiling_set_mid_event_displaces_nobody_already_watching(world, ceiling):
    ceiling(None)                       # no ceiling while the first two join
    a, b = viewer(world, "A"), viewer(world, "B")
    watch(a[0], world, a[1])
    watch(b[0], world, b[1])
    ceiling(1)                          # Operations tightens it during the event
    for c, t in (a, b):
        assert watch(c, world, t)["admission"] == "admitted"
    c3 = viewer(world, "C")
    assert watch(c3[0], world, c3[1])["admission"] == "waiting"


def test_an_unreachable_ledger_admits_rather_than_blocks(world, ceiling, monkeypatch):
    ceiling(1)

    class Down:
        def hgetall(self, *_a):
            raise ConnectionError("redis down")

    monkeypatch.setattr(bus, "sync_redis", lambda: Down())
    c, t = viewer(world, "A")
    watch(c, world, t)
    c2, t2 = viewer(world, "B")
    assert watch(c2, world, t2)["admission"] == "admitted"


def test_admission_does_not_apply_before_the_event_is_live(world, ceiling):
    ceiling(1)
    world.event.status = "scheduled"
    world.db.commit()
    c, t = viewer(world, "A")
    w = watch(c, world, t)
    assert w["admission"] is None and w["retry_after_seconds"] is None


def test_the_platform_ceiling_reads_only_a_positive_number(world):
    db = world.db
    row = db.get(PlatformSetting, "streaming_limits")
    original = dict(row.value) if row and row.value else None
    try:
        for value, expected in ((None, None), ("", None), (0, None), (-3, None), ("abc", None), (250, 250), ("40", 40)):
            if row is None:
                row = PlatformSetting(key="streaming_limits", value={})
                db.add(row)
            row.value = {**(original or {}), "viewer_admission_ceiling": value}
            db.commit()
            assert platform_settings.viewer_admission_ceiling(db) == expected, value
    finally:
        if original is None:
            db.query(PlatformSetting).filter(PlatformSetting.key == "streaming_limits").delete()
        else:
            row.value = original
        db.commit()
