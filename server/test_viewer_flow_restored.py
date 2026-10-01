"""The original viewer workflow, pinned after the ZST-SPEC-VAP-001 implementation was reverted.

What is pinned here:
  * Create Event — Public / Unlisted / Private, Save Draft, Schedule, host assignment — never
    creates an access policy and never touches the viewer-access tables. Category is metadata:
    a Funeral / Memorial event keeps the visibility and feature flags the organiser chose.
  * The viewer path for such an event is the original one: self-serve registration, emailed
    invitations, host access links, the /watch payload (flags + LiveKit token) and the live
    socket actions (chat, reactions, Q&A, polls, raise hand).
  * Readiness and analytics carry no viewer-access reasons or session terminology, and no
    /api/access or /e/{code} route exists.
  * Every server failure still reaches the browser as readable JSON with CORS headers (a
    generic fix kept from the Create Event "Network Error" work).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from starlette.testclient import TestClient

import app.main as m
from app.crud import commercial
from app.db import SessionLocal, engine
from app.models import (
    AuditLog,
    Event,
    EventAccessLink,
    EventAssignment,
    EventRegistration,
    LiveActivity,
    LiveMessage,
    LivePoll,
    LivePollVote,
    LiveQuestion,
    LiveQuestionVote,
    Organization,
    User,
)
from app.routers import events as events_router
from app.security import create_access_token, hash_password
from app.services import broadcast, bus
from app.services import moderation as mod
from app.services import org as org_svc
from app.services.moderation import Ctx

UTC = timezone.utc
ORIGIN = "http://localhost:5173"

# The tables the reverted implementation added. They may still exist in a local database
# (nothing drops them automatically); no code path may read or write them any more.
VIEWER_ACCESS_TABLES = ("event_access_policies", "invitation_credentials", "viewer_sessions",
                        "access_policy_changes", "access_throttles")


@pytest.fixture(autouse=True)
def _in_process_bus(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    yield


@pytest.fixture
def world():
    db = SessionLocal()
    org = Organization(name=f"ViewerFlow {uuid.uuid4().hex[:6]}", status="active")
    db.add(org)
    db.flush()

    def user(role, name):
        u = User(org_id=org.id, full_name=name, role=role, is_active=True,
                 email=f"vf-{uuid.uuid4().hex[:10]}@example.com", username=f"vf{uuid.uuid4().hex[:10]}",
                 password_hash=hash_password("x"), email_verified=True)
        db.add(u)
        db.flush()
        return u

    admin, host = user("org_admin", "Vihari"), user("host", "Nani")
    db.commit()
    ns = type("W", (), {"db": db, "org": org, "admin": admin, "host": host})()
    try:
        yield ns
    finally:
        db.rollback()
        ids = db.scalars(select(Event.id).where(Event.org_id == org.id)).all() or [uuid.uuid4()]
        for model in (LiveActivity, LiveMessage, LiveQuestionVote, LiveQuestion, LivePollVote, LivePoll,
                      EventRegistration, EventAccessLink, EventAssignment):
            db.query(model).filter(model.event_id.in_(ids)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.org_id == org.id).delete(synchronize_session=False)
        db.query(Event).filter(Event.id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.org_id == org.id).delete(synchronize_session=False)
        db.query(Organization).filter(Organization.id == org.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def client(user=None, **kw):
    c = TestClient(m.app, **kw)
    c.headers.update({"Origin": ORIGIN})
    if user is not None:
        c.headers.update({"Authorization": f"Bearer {create_access_token(user, remember=False)}"})
    return c


def body(category="Webinar", status="scheduled", visibility="public", title=None, **flags):
    start = datetime.now(UTC) + timedelta(days=3)
    return {"title": title or f"Event {uuid.uuid4().hex[:6]}", "description": "", "category": category,
            "timezone": "UTC", "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat(),
            "visibility": visibility, "chat_enabled": True, "polls_enabled": True, "qa_enabled": True,
            "recording_enabled": True, "status": status, **flags}


def events_titled(world, title):
    world.db.expire_all()
    return world.db.scalar(select(func.count(Event.id)).where(Event.org_id == world.org.id, Event.title == title))


def go_live_now(world, event_id):
    """Put an event on air directly — the readiness gate is not what these tests are about."""
    world.db.expire_all()
    ev = world.db.get(Event, uuid.UUID(event_id))
    now = datetime.now(UTC)
    ev.status, ev.start_time, ev.end_time = "live", now - timedelta(minutes=5), now + timedelta(hours=1)
    world.db.commit()


class refuse_viewer_access_tables:
    """While active, any SQL statement naming a viewer-access table fails exactly as it would
    on a database that never had them, and is recorded."""

    def __init__(self):
        self.touched = []

    def _refuse(self, conn, cursor, statement, parameters, context, executemany):
        if any(t in statement for t in VIEWER_ACCESS_TABLES):
            self.touched.append(statement.split()[0])
            raise schema_drift()

    def __enter__(self):
        sa_event.listen(engine, "before_cursor_execute", self._refuse)
        return self

    def __exit__(self, *exc):
        sa_event.remove(engine, "before_cursor_execute", self._refuse)
        return False


class _PgOrig(Exception):
    def __init__(self, pgcode, message):
        super().__init__(message)
        self.pgcode = pgcode


def schema_drift():
    return ProgrammingError("SELECT missing.id FROM missing", {},
                            _PgOrig("42P01", 'relation "missing" does not exist'))


# ── Create Event ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("visibility", ["public", "unlisted", "private"])
def test_create_keeps_the_chosen_visibility(world, visibility):
    r = client(world.admin).post("/api/events", json=body(visibility=visibility))
    assert r.status_code == 201, r.text
    assert r.json()["visibility"] == visibility


@pytest.mark.parametrize("category", ["Webinar", "Funeral / Memorial", "Wedding / Celebration",
                                      "Worship Service", "Graduation", "Civic Ceremony", None])
def test_no_category_makes_creation_depend_on_the_viewer_access_tables(world, category):
    """Checked at the SQL layer, not by patching one function."""
    with refuse_viewer_access_tables() as guard:
        r = client(world.admin).post("/api/events", json=body(category))
    assert r.status_code == 201, r.text
    assert guard.touched == []
    assert r.json()["category"] == category                       # stored as given: metadata only


def test_a_memorial_keeps_the_visibility_and_features_the_organiser_chose(world):
    r = client(world.admin).post("/api/events", json=body(
        "Funeral / Memorial", visibility="private", raise_hand_enabled=True, registration_required=True))
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["visibility"], out["chat_enabled"], out["qa_enabled"], out["polls_enabled"]) == \
        ("private", True, True, True)
    assert out["registration_required"] is True and out["raise_hand_enabled"] is True


def test_recategorizing_is_a_plain_update(world):
    eid = client(world.admin).post("/api/events", json=body("Webinar")).json()["id"]
    with refuse_viewer_access_tables() as guard:
        r = client(world.admin).patch(f"/api/events/{eid}", json={"category": "Funeral / Memorial"})
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "Funeral / Memorial" and guard.touched == []


def test_save_draft(world):
    r = client(world.admin).post("/api/events", json=body("Funeral / Memorial", status="draft"))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "draft"


def test_schedule_event_creates_and_assigns_the_selected_hosts(world):
    c = client(world.admin)
    r = c.post("/api/events", json=body("Funeral / Memorial", status="scheduled"))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "scheduled"
    eid = r.json()["id"]
    h = c.patch(f"/api/events/{eid}/hosts", json={"user_ids": [str(world.host.id), str(world.admin.id)]})
    assert h.status_code == 200, h.text
    assert {u["id"] for u in h.json()} == {str(world.host.id), str(world.admin.id)}
    world.db.expire_all()
    assigned = world.db.scalars(select(EventAssignment.user_id).where(
        EventAssignment.event_id == uuid.UUID(eid), EventAssignment.role == "host")).all()
    assert set(assigned) == {world.host.id, world.admin.id}


def test_a_host_from_another_organization_is_still_refused(world):
    other = Organization(name=f"Other {uuid.uuid4().hex[:6]}", status="active")
    world.db.add(other)
    world.db.flush()
    stranger = User(org_id=other.id, full_name="Stranger", role="host", is_active=True,
                    email=f"vf-{uuid.uuid4().hex[:10]}@example.com", username=f"vf{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password("x"), email_verified=True)
    world.db.add(stranger)
    world.db.commit()
    try:
        c = client(world.admin)
        eid = c.post("/api/events", json=body()).json()["id"]
        r = c.patch(f"/api/events/{eid}/hosts", json={"user_ids": [str(stranger.id)]})
        assert r.status_code == 400, r.text
        world.db.expire_all()
        assert world.db.scalar(select(EventAssignment.id).where(EventAssignment.event_id == uuid.UUID(eid))) is None
    finally:
        world.db.query(User).filter(User.id == stranger.id).delete(synchronize_session=False)
        world.db.query(Organization).filter(Organization.id == other.id).delete(synchronize_session=False)
        world.db.commit()


# ── The original viewer entry points work for a memorial ────────────────────────────────

def test_self_serve_registration_and_watch_for_a_public_memorial(world):
    eid = client(world.admin).post("/api/events", json=body(
        "Funeral / Memorial", registration_required=True)).json()["id"]
    go_live_now(world, eid)
    anon = client()
    gated = anon.get(f"/api/events/{eid}/watch").json()
    assert gated["registration_required"] is True and gated["registered"] is False
    assert gated["livekit_token"] is None                         # the registration gate holds

    reg = anon.post(f"/api/events/{eid}/register", json={"name": "Aunt May"})
    assert reg.status_code == 200, reg.text
    watch = anon.get(f"/api/events/{eid}/watch", params={"reg": reg.json()["token"]})
    assert watch.status_code == 200, watch.text
    w = watch.json()
    assert w["registered"] is True and w["livekit_token"] and w["room"]
    assert (w["chat_enabled"], w["qa_enabled"], w["polls_enabled"], w["reactions_enabled"]) == (True, True, True, True)


def test_emailed_invitation_admits_a_viewer_to_a_private_memorial(world):
    eid = client(world.admin).post("/api/events", json=body("Funeral / Memorial", visibility="private")).json()["id"]
    go_live_now(world, eid)
    r = client(world.admin).post(f"/api/events/{eid}/invite-viewers",
                                 json={"invites": [{"name": "Cousin Ray", "email": "ray@example.com"}]})
    assert r.status_code == 200, r.text
    token = r.json()[0]["token"]
    assert client().get(f"/api/events/{eid}/watch").status_code == 403        # still private
    w = client().get(f"/api/events/{eid}/watch", params={"reg": token})
    assert w.status_code == 200, w.text
    assert w.json()["visibility"] == "private" and w.json()["chat_enabled"] is True
    assert w.json()["livekit_token"]


def test_a_host_access_link_admits_a_viewer_to_a_private_memorial(world):
    eid = client(world.admin).post("/api/events", json=body("Funeral / Memorial", visibility="private")).json()["id"]
    go_live_now(world, eid)
    r = client(world.admin).post(f"/api/events/{eid}/access-links", json={"label": "Family"})
    assert r.status_code == 201, r.text
    link = r.json()["url"].split("link=")[1].split("&")[0]
    w = client().get(f"/api/events/{eid}/watch", params={"link": link})
    assert w.status_code == 200, w.text
    assert w.json()["livekit_token"] and w.json()["qa_enabled"] is True


# ── Live socket actions follow the event's own flags ─────────────────────────────────────

def _ctxs(world, event_id):
    eid = uuid.UUID(event_id)
    room = f"room-{eid}"
    host = Ctx(event_id=eid, org_id=world.org.id, room=room, user_id=world.admin.id, name="Vihari",
               identity=str(world.admin.id), role="org_admin", can_moderate=True, can_host=True)
    # Shaped like moderation.resolve_ctx_from_registration: a registered guest borrows the
    # registration's id as user_id and identity.
    reg_id = uuid.uuid4()
    viewer = Ctx(event_id=eid, org_id=world.org.id, room=room, user_id=reg_id, name="Aunt May",
                 identity=f"guest-{reg_id}", role="viewer", can_moderate=False)
    return host, viewer


@pytest.mark.asyncio
async def test_a_memorial_viewer_can_chat_react_ask_vote_and_raise_a_hand(world):
    eid = client(world.admin).post("/api/events", json=body("Funeral / Memorial", raise_hand_enabled=True)).json()["id"]
    go_live_now(world, eid)
    host, viewer = _ctxs(world, eid)

    state = await broadcast.ensure_state(host)
    # The console starts from the organiser's saved flags — no category override.
    assert state["chat_enabled"] is True and state["qa_enabled"] is True
    assert state["reactions_enabled"] is True

    async with bus.subscribe(uuid.UUID(eid)) as q:
        assert await mod.dispatch(viewer, "chat.send", {"text": "We miss you"}) is None
        msg = None
        while msg is None:
            env = await q.get()
            if (env["channel"], env["type"]) == ("chat", "message.new"):
                msg = env["data"]
        assert await mod.dispatch(viewer, "chat.react", {"id": msg["id"], "emoji": "❤️"}) is None
        assert await mod.dispatch(viewer, "reaction.add", {"key": "heart"}) is None
        assert await mod.dispatch(viewer, "qa.ask", {"text": "Will there be a replay?"}) is None
        assert await mod.dispatch(viewer, "participant.hand", {"raised": True}) is None
        assert await mod.dispatch(host, "poll.create", {"question": "Favourite hymn?",
                                                         "options": ["Abide with me", "Amazing grace"]}) is None
        world.db.expire_all()
        poll_id = world.db.scalar(select(LivePoll.id).where(LivePoll.event_id == uuid.UUID(eid)))
        assert await mod.dispatch(viewer, "poll.vote", {"id": str(poll_id), "option": 1}) is None

    world.db.expire_all()
    assert world.db.scalar(select(func.count(LiveQuestion.id)).where(LiveQuestion.event_id == uuid.UUID(eid))) == 1
    assert world.db.scalar(select(func.count(LivePollVote.id)).where(LivePollVote.poll_id == poll_id)) == 1
    reacted = world.db.get(LiveMessage, uuid.UUID(msg["id"]))
    assert reacted.reactions == {"❤️": 1}


@pytest.mark.asyncio
async def test_a_host_who_turns_chat_off_still_turns_it_off(world):
    """The flags are the only gate — and they still gate."""
    eid = client(world.admin).post("/api/events", json=body("Funeral / Memorial", chat_enabled=False)).json()["id"]
    go_live_now(world, eid)
    host, viewer = _ctxs(world, eid)
    state = await broadcast.ensure_state(host)
    assert state["chat_enabled"] is False
    assert await mod.dispatch(viewer, "chat.send", {"text": "hello"}) is not None


# ── Readiness, analytics, routes ─────────────────────────────────────────────────────────

def test_readiness_has_no_viewer_access_reasons_and_needs_no_viewer_access_tables(world):
    eid = client(world.admin).post("/api/events", json=body("Funeral / Memorial", visibility="private")).json()["id"]
    world.db.expire_all()
    ev = world.db.get(Event, uuid.UUID(eid))
    with refuse_viewer_access_tables() as guard:
        result = commercial.evaluate_readiness(world.db, ev, commercial.get_current_order(world.db, ev.id))
    assert guard.touched == []
    assert not [r for r in result["blocking_reasons"] if "viewer access" in r.lower()]


def test_org_analytics_and_sessions_speak_of_viewers_only(world):
    client(world.admin).post("/api/events", json=body("Funeral / Memorial"))
    with refuse_viewer_access_tables() as guard:
        data = org_svc.analytics(world.db, world.org, range_key="30d")
        sess = org_svc.sessions(world.db, world.org.id, datetime.now(UTC) - timedelta(days=1))
    assert guard.touched == []
    assert "audience_unit" not in data
    rows = [row for value in data.values() if isinstance(value, list) for row in value if isinstance(row, dict)]
    assert not [row for row in rows if "access_mode" in row]
    assert not [row for row in sess["items"] if "access_mode" in row]


def test_no_viewer_access_routes_remain():
    paths = {getattr(r, "path", "") for r in m.app.routes}
    assert not [p for p in paths if p.startswith(("/api/access", "/api/admin/viewer-access", "/e/"))]
    for path in ("/api/access/session", "/api/access/Ab3dEf5hIj7kLm/redeem", "/api/admin/viewer-access/policies"):
        assert client().get(path).status_code == 404, path


def test_the_registration_link_is_the_spa_with_no_special_headers():
    if not m.DIST.is_dir():
        pytest.skip("client/dist is not built")
    r = client().get(f"/e/{uuid.uuid4()}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "content-security-policy" not in r.headers and r.headers.get("referrer-policy") != "no-referrer"


# ── Every server failure is readable JSON with CORS headers (generic, kept) ─────────────

def test_an_unhandled_exception_is_json_with_cors_and_still_raised(world, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("simulated defect")
    monkeypatch.setattr(events_router.crud, "get_event_unscoped", boom)
    r = client(world.admin, raise_server_exceptions=False).get(f"/api/events/{uuid.uuid4()}/watch")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert r.json()["detail"]["code"] == "server_error"
    assert "simulated defect" not in r.text
    with pytest.raises(RuntimeError, match="simulated defect"):        # logs/tests still see it
        client(world.admin).get(f"/api/events/{uuid.uuid4()}/watch")


def test_schema_drift_is_a_readable_503(world, monkeypatch):
    def boom(*_a, **_k):
        raise schema_drift()
    monkeypatch.setattr(events_router.crud, "get_event_unscoped", boom)
    r = client(world.admin, raise_server_exceptions=False).get(f"/api/events/{uuid.uuid4()}/watch")
    assert r.status_code == 503
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert r.json()["detail"]["code"] == "schema_out_of_date"
    assert "missing" not in r.text                                    # no internals in the reply


def test_a_non_schema_programming_error_is_a_plain_json_500(world, monkeypatch):
    def boom(*_a, **_k):
        raise ProgrammingError("SELECT x", {}, _PgOrig("42601", "syntax error"))
    monkeypatch.setattr(events_router.crud, "get_event_unscoped", boom)
    r = client(world.admin, raise_server_exceptions=False).get(f"/api/events/{uuid.uuid4()}/watch")
    assert r.status_code == 500 and r.json()["detail"]["code"] == "server_error"
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_a_lost_database_connection_during_create_keeps_its_own_503(world, monkeypatch):
    def boom(*_a, **_k):
        raise OperationalError("INSERT INTO events ...", {}, Exception("connection refused"))
    monkeypatch.setattr(events_router.crud, "create_event", boom)
    title = f"Webinar {uuid.uuid4().hex[:6]}"
    r = client(world.admin, raise_server_exceptions=False).post("/api/events", json=body(title=title))
    assert r.status_code == 503 and "unreachable" in r.json()["detail"]
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert events_titled(world, title) == 0
