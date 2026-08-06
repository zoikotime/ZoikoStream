"""Self-check for public event registration (registration_required gate).

Token round-trip is pure (SimpleNamespace stand-in, no DB). The register/watch flow is
exercised against the real DB through TestClient — /register commits real rows, so every
test that hits it cleans up what it created in a finally block.

Run with `python test_registration.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import Event, EventRegistration, Organization, User
from app.security import create_registration_token, decode_registration_token

NOW = datetime.now(timezone.utc)


# ── token round-trip (pure) ─────────────────────────────────────────────────────

def test_token_round_trips_to_the_registered_email():
    reg = SimpleNamespace(id=uuid.uuid4(), event_id=uuid.uuid4(), email="a@x.com")
    token = create_registration_token(reg)
    assert decode_registration_token(token, reg.event_id) == "a@x.com"


def test_token_rejected_for_a_different_event():
    reg = SimpleNamespace(id=uuid.uuid4(), event_id=uuid.uuid4(), email="a@x.com")
    token = create_registration_token(reg)
    assert decode_registration_token(token, uuid.uuid4()) is None


def test_garbage_token_rejected():
    assert decode_registration_token("not-a-jwt", uuid.uuid4()) is None


# ── endpoint behavior (real DB, TestClient) ─────────────────────────────────────

def _org(db):
    o = Organization(name=f"org-test-{uuid.uuid4().hex[:8]}", status="active")
    db.add(o)
    db.flush()
    return o


def _user(db, org):
    u = User(
        org_id=org.id, full_name="Test Owner", role="org_admin", is_active=True,
        email=f"t{uuid.uuid4().hex[:10]}@example.com",
        username=f"u{uuid.uuid4().hex[:10]}", password_hash="x",
    )
    db.add(u)
    db.flush()
    return u


def _event(db, org, creator, **kw):
    e = Event(org_id=org.id, created_by=creator.id, title="Registration Test Event",
              status="published", start_time=NOW + timedelta(hours=6), visibility="public", **kw)
    db.add(e)
    db.flush()
    return e


def _cleanup(db, org, user, ev):
    """The endpoints under test commit through their own (dependency-injected) session,
    so setup/teardown here must commit too — a rollback wouldn't undo rows another
    connection already committed."""
    db.query(EventRegistration).filter(EventRegistration.event_id == ev.id).delete()
    db.delete(ev)
    db.delete(user)
    db.delete(org)
    db.commit()


def test_register_400s_when_not_required():
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, registration_required=False)
    db.commit()
    try:
        resp = client.post(f"/api/events/{ev.id}/register", json={"name": "Jane", "email": "jane@example.com"})
        assert resp.status_code == 400, resp.text
    finally:
        _cleanup(db, org, user, ev)
        db.close()


def test_register_then_watch_grants_stream_access():
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, registration_required=True)
    db.commit()
    try:
        watch_before = client.get(f"/api/events/{ev.id}/watch").json()
        assert watch_before["registration_required"] is True
        assert watch_before["registered"] is False

        resp = client.post(f"/api/events/{ev.id}/register", json={"name": "Jane", "email": "jane@example.com"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["email"] == "jane@example.com"

        watch_after = client.get(f"/api/events/{ev.id}/watch", params={"reg": body["token"]}).json()
        assert watch_after["registered"] is True

        # Resubmitting the same email is idempotent, not a duplicate-key error.
        resp2 = client.post(f"/api/events/{ev.id}/register", json={"name": "Jane", "email": "jane@example.com"})
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["id"] == body["id"]
    finally:
        _cleanup(db, org, user, ev)
        db.close()


def test_register_409s_once_capacity_is_reached():
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, registration_required=True, registration_limit=1)
    db.commit()
    try:
        r1 = client.post(f"/api/events/{ev.id}/register", json={"name": "Jane", "email": "jane2@example.com"})
        assert r1.status_code == 200, r1.text

        r2 = client.post(f"/api/events/{ev.id}/register", json={"name": "Bob", "email": "bob@example.com"})
        assert r2.status_code == 409, r2.text
    finally:
        _cleanup(db, org, user, ev)
        db.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("registration self-check passed")
