"""Event-console authorization: account role vs event assignment.

The frontend routing fix depends on this endpoint telling the truth, so this is where the
truth is asserted:

    account role "host", no assignment   -> can_host False
    assigned host for event A            -> can_host True for A, False for B
    assignment removed                   -> can_host False again
    moderator assignment                 -> can_moderate True, can_host False
    org_admin                            -> runs their own org's events, no row needed
    another organization's event         -> 404, not a probe

Run with `python test_event_assignment.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.crud import event as crud
from app.db import SessionLocal
from app.models import Event, EventAssignment, Organization, User
from app.security import hash_password

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


def _swallow(url, headers=None, json=None, timeout=None):
    return _Resp()


email_mod.httpx.post = _swallow


def _now():
    return datetime.now(timezone.utc)


def _new_email(tag="asg"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class World:
    """One org with two events, one host-persona account, and a second org to prove scoping."""

    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Asg Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="UTC")
            other = Organization(name=f"Other Co {uuid.uuid4().hex[:6]}", status="active",
                                 timezone="UTC")
            db.add_all([org, other])
            db.flush()
            self.org_id, self.other_org_id = org.id, other.id

            self.admin_email = _new_email("orgadmin")
            self.admin_id = self._u(db, org.id, "org_admin", self.admin_email)
            # The account at the centre of the bug: role "host", no assignment anywhere.
            self.host_email = _new_email("hostpersona")
            self.host_id = self._u(db, org.id, "host", self.host_email)
            self.moderator_email = _new_email("moderator")
            self.moderator_id = self._u(db, org.id, "host", self.moderator_email)
            org.owner_user_id = self.admin_id
            other.owner_user_id = self._u(db, other.id, "org_admin", _new_email("otheradmin"))
            db.flush()

            self.event_a = self._e(db, org.id, "Event A")
            self.event_b = self._e(db, org.id, "Event B")
            self.foreign_event = self._e(db, other.id, "Somebody else's event")
            db.commit()
        finally:
            db.close()

    def _u(self, db, org_id, role, email):
        user = User(org_id=org_id, full_name="Assignment Person", role=role,
                    is_active=True, email=email.lower(),
                    username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.flush()
        return user.id

    def _e(self, db, org_id, title):
        ev = Event(org_id=org_id, created_by=self.admin_id, title=title,
                   status="scheduled", visibility="private",
                   start_time=_now() + timedelta(days=2))
        db.add(ev)
        db.flush()
        return ev.id

    def assign(self, event_id, user_id, role):
        db = SessionLocal()
        try:
            db.add(EventAssignment(event_id=event_id, user_id=user_id, role=role))
            db.commit()
        finally:
            db.close()

    def unassign(self, event_id, user_id, role):
        db = SessionLocal()
        try:
            db.query(EventAssignment).filter(
                EventAssignment.event_id == event_id,
                EventAssignment.user_id == user_id,
                EventAssignment.role == role).delete()
            db.commit()
        finally:
            db.close()

    def token(self, email):
        client = TestClient(m.app)
        ratelimit._HITS.clear()
        r = client.post("/api/auth/login",
                        json={"identifier": email, "password": PASSWORD},
                        headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def cleanup(self):
        db = SessionLocal()
        try:
            for event_id in (self.event_a, self.event_b, self.foreign_event):
                db.query(EventAssignment).filter(
                    EventAssignment.event_id == event_id).delete()
            db.commit()
            for event_id in (self.event_a, self.event_b, self.foreign_event):
                ev = db.get(Event, event_id)
                if ev is not None:
                    db.delete(ev)
            db.commit()
            for org_id in (self.org_id, self.other_org_id):
                org = db.get(Organization, org_id)
                if org is not None:
                    org.owner_user_id = None
                    db.commit()
                db.query(User).filter(User.org_id == org_id).delete()
                db.commit()
                if org is not None:
                    db.delete(org)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


RESULTS = []


def run(fn):
    world = World()
    try:
        fn(world)
        RESULTS.append((fn.__name__, None))
        print(f"ok  {fn.__name__}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((fn.__name__, exc))
        print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    finally:
        world.cleanup()


def _assignment(client, headers, event_id):
    return client.get(f"/api/events/{event_id}/assignment", headers=headers)


# ── the distinction the whole fix rests on ──────────────────────────────────────────────

def test_host_account_role_alone_grants_nothing(w):
    """THE BUG, at its source.

    This account's User.role is literally "host" - and that must not, by itself, grant
    control of any event. Being a host-persona account says what somebody does, not which
    broadcast they run.
    """
    headers = w.token(w.host_email)
    client = TestClient(m.app)
    for event_id in (w.event_a, w.event_b):
        r = _assignment(client, headers, event_id)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["can_host"] is False, "an unassigned host account was granted control"
        assert body["can_moderate"] is False
        assert body["assigned_roles"] == []
        assert body["via_org_role"] is False

    # And the picker is empty, so the frontend has nothing to route them into.
    mine = client.get("/api/events/assignments/mine", headers=headers)
    assert mine.status_code == 200
    assert mine.json()["items"] == []
    # The account role is echoed, but as information - not as an entitlement.
    assert mine.json()["account_role"] == "host"


def test_assignment_is_what_grants_control(w):
    headers = w.token(w.host_email)
    client = TestClient(m.app)
    assert _assignment(client, headers, w.event_a).json()["can_host"] is False

    w.assign(w.event_a, w.host_id, "host")

    body = _assignment(client, headers, w.event_a).json()
    assert body["can_host"] is True
    assert body["can_moderate"] is True, "a host runs the room, so they can moderate it"
    assert body["assigned_roles"] == ["host"]
    assert body["via_org_role"] is False

    mine = client.get("/api/events/assignments/mine", headers=headers).json()
    assert [i["event_id"] for i in mine["items"]] == [str(w.event_a)]
    assert mine["items"][0]["roles"] == ["host"]
    assert mine["items"][0]["can_host"] is True


def test_an_assignment_for_one_event_is_not_another(w):
    w.assign(w.event_a, w.host_id, "host")
    headers = w.token(w.host_email)
    client = TestClient(m.app)
    assert _assignment(client, headers, w.event_a).json()["can_host"] is True
    assert _assignment(client, headers, w.event_b).json()["can_host"] is False, (
        "hosting one event granted control of another")


def test_revoking_an_assignment_revokes_control(w):
    w.assign(w.event_a, w.host_id, "host")
    headers = w.token(w.host_email)
    client = TestClient(m.app)
    assert _assignment(client, headers, w.event_a).json()["can_host"] is True

    w.unassign(w.event_a, w.host_id, "host")

    # Same session, same token - the answer changes because the ASSIGNMENT changed.
    assert _assignment(client, headers, w.event_a).json()["can_host"] is False
    assert client.get("/api/events/assignments/mine",
                      headers=headers).json()["items"] == []


def test_a_moderator_assignment_does_not_grant_broadcast_control(w):
    w.assign(w.event_a, w.moderator_id, "moderator")
    headers = w.token(w.moderator_email)
    client = TestClient(m.app)
    body = _assignment(client, headers, w.event_a).json()
    assert body["can_moderate"] is True
    assert body["can_host"] is False, "a moderator was given the Go Live button"
    assert body["assigned_roles"] == ["moderator"]


def test_a_speaker_assignment_grants_only_contribution(w):
    w.assign(w.event_a, w.moderator_id, "speaker")
    headers = w.token(w.moderator_email)
    client = TestClient(m.app)
    body = _assignment(client, headers, w.event_a).json()
    assert body["can_contribute"] is True
    assert body["can_host"] is False
    assert body["can_moderate"] is False


def test_org_admin_runs_their_own_events_without_a_row(w):
    """Not an exception to the rule - a different, real basis for authority."""
    headers = w.token(w.admin_email)
    client = TestClient(m.app)
    body = _assignment(client, headers, w.event_a).json()
    assert body["can_host"] is True
    assert body["assigned_roles"] == []
    # Says WHY, so a caller can tell org authority from an assignment.
    assert body["via_org_role"] is True
    # ...and their picker is still assignment-only, not every event in the org.
    assert client.get("/api/events/assignments/mine",
                      headers=headers).json()["items"] == []


# ── scoping and authentication ──────────────────────────────────────────────────────────

def test_another_organizations_event_is_not_even_visible(w):
    """404, not 403: a wrong answer here would confirm the event exists."""
    headers = w.token(w.host_email)
    client = TestClient(m.app)
    r = _assignment(client, headers, w.foreign_event)
    assert r.status_code == 404, r.text
    assert "can_host" not in r.text


def test_both_endpoints_require_authentication(w):
    client = TestClient(m.app)
    assert client.get(f"/api/events/{w.event_a}/assignment").status_code == 401
    assert client.get("/api/events/assignments/mine").status_code == 401
    assert client.get(f"/api/events/{w.event_a}/assignment",
                      headers={"Authorization": "Bearer nope"}).status_code == 401


def test_an_unknown_event_is_a_404_not_a_grant(w):
    headers = w.token(w.host_email)
    client = TestClient(m.app)
    assert _assignment(client, headers, uuid.uuid4()).status_code == 404


def test_the_route_literal_wins_over_the_uuid_parameter(w):
    """`/assignments/mine` must not be parsed as an event id."""
    headers = w.token(w.host_email)
    client = TestClient(m.app)
    r = client.get("/api/events/assignments/mine", headers=headers)
    assert r.status_code == 200, r.text
    assert "items" in r.json()


# ── the routing decision agrees with the broadcast grant ────────────────────────────────

def test_console_access_matches_the_live_socket_rule(w):
    """The routing answer and the broadcast grant must never disagree.

    If they could, the console would open for someone the socket then refuses - which is
    precisely the read-only Producer Console the user reported.
    """
    from app.services import moderation

    w.assign(w.event_a, w.host_id, "host")
    db = SessionLocal()
    try:
        event = db.get(Event, w.event_a)
        for user_id in (w.host_id, w.moderator_id, w.admin_id):
            user = db.get(User, user_id)
            routing = crud.console_access(db, event, user)
            ctx = moderation.resolve_ctx(w.event_a, user)
            assert ctx is not None
            assert routing["can_host"] == ctx.can_host, (
                f"routing and socket disagree on can_host for {user.role}")
            assert routing["can_moderate"] == ctx.can_moderate, (
                f"routing and socket disagree on can_moderate for {user.role}")
    finally:
        db.close()


TESTS = [
    test_host_account_role_alone_grants_nothing,
    test_assignment_is_what_grants_control,
    test_an_assignment_for_one_event_is_not_another,
    test_revoking_an_assignment_revokes_control,
    test_a_moderator_assignment_does_not_grant_broadcast_control,
    test_a_speaker_assignment_grants_only_contribution,
    test_org_admin_runs_their_own_events_without_a_row,
    test_another_organizations_event_is_not_even_visible,
    test_both_endpoints_require_authentication,
    test_an_unknown_event_is_a_404_not_a_grant,
    test_the_route_literal_wins_over_the_uuid_parameter,
    test_console_access_matches_the_live_socket_rule,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
