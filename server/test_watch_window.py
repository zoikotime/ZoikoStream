"""Self-check for the scheduled start_time/end_time viewer access window on GET
/events/{id}/watch. Real DB via TestClient — setup/teardown commit for real (a second
connection needs to see the rows), so every test cleans up what it created.

Run with `python test_watch_window.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import Event, Organization, User

NOW = datetime.now(timezone.utc)


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
    e = Event(org_id=org.id, created_by=creator.id, title="Window Test Event",
              status="live", visibility="public", **kw)
    db.add(e)
    db.flush()
    return e


def _cleanup(db, org, user, ev):
    db.delete(ev)
    db.delete(user)
    db.delete(org)
    db.commit()


def test_before_start_time_is_not_started_not_expired():
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, start_time=NOW + timedelta(hours=1), end_time=NOW + timedelta(hours=2))
    db.commit()
    try:
        body = client.get(f"/api/events/{ev.id}/watch").json()
        assert body["not_started"] is True, body
        assert body["expired"] is False, body
        assert body["livekit_token"] is None
    finally:
        _cleanup(db, org, user, ev)


def test_after_end_time_is_expired_even_while_live():
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, start_time=NOW - timedelta(hours=2), end_time=NOW - timedelta(hours=1))
    db.commit()
    try:
        body = client.get(f"/api/events/{ev.id}/watch").json()
        assert body["expired"] is True, body
        assert body["not_started"] is False, body
        assert body["livekit_token"] is None
        # status is untouched by the time check — the host's own broadcast isn't force-ended.
        assert body["status"] == "live", body
    finally:
        _cleanup(db, org, user, ev)


def test_within_window_is_neither_gated():
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, start_time=NOW - timedelta(hours=1), end_time=NOW + timedelta(hours=1))
    db.commit()
    try:
        body = client.get(f"/api/events/{ev.id}/watch").json()
        assert body["not_started"] is False, body
        assert body["expired"] is False, body
    finally:
        _cleanup(db, org, user, ev)


def test_no_schedule_set_is_never_time_gated():
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, start_time=None, end_time=None)
    db.commit()
    try:
        body = client.get(f"/api/events/{ev.id}/watch").json()
        assert body["not_started"] is False, body
        assert body["expired"] is False, body
    finally:
        _cleanup(db, org, user, ev)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("watch-window self-check passed")
