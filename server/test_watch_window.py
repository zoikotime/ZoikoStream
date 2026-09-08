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


def test_watch_out_exposes_category_end_time_and_raise_hand():
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, start_time=NOW - timedelta(hours=1), end_time=NOW + timedelta(hours=1),
                category="Technology", raise_hand_enabled=True)
    db.commit()
    try:
        body = client.get(f"/api/events/{ev.id}/watch").json()
        assert body["category"] == "Technology", body
        assert body["end_time"] is not None, body
        assert body["raise_hand_enabled"] is True, body
    finally:
        _cleanup(db, org, user, ev)


def test_watch_out_raise_hand_forced_off_for_memorial_category():
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, category="Funeral / Memorial", raise_hand_enabled=True)
    db.commit()
    try:
        body = client.get(f"/api/events/{ev.id}/watch").json()
        assert body["raise_hand_enabled"] is False, body
        assert body["reactions_enabled"] is False, body
    finally:
        _cleanup(db, org, user, ev)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("watch-window self-check passed")


# ── watch-link authorization + room identity (live-video audit) ────────────────
# Added with the LiveKit publishing fix: the audit's "watch link authorization" and
# "room ID consistency" items. These assert at the ROUTE level what
# test_livekit_publish_flow.py asserts at the token level — that the credentials handed to a
# browser are gated, and that a viewer's room is the producer's room.

def _grants(token):
    """The `video` claim LiveKit enforces on."""
    import jwt

    from app.config import settings
    return jwt.decode(token, settings.LIVEKIT_API_SECRET, algorithms=["HS256"],
                      options={"verify_aud": False})["video"]


def test_a_private_event_refuses_an_invalid_watch_link():
    """An unrecognised ?link= on a private event must not reach the room at all — no token,
    and a 403 rather than a silently token-less 200 that reads like "not live yet"."""
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    # Built directly rather than via _event(), which pins visibility="public".
    ev = Event(org_id=org.id, created_by=user.id, title="Private Window Test Event",
               status="live", visibility="private", start_time=None, end_time=None)
    db.add(ev)
    db.flush()
    db.commit()
    try:
        r = client.get(f"/api/events/{ev.id}/watch", params={"link": uuid.uuid4().hex})
        assert r.status_code == 403, r.text
        assert "livekit_token" not in r.text
    finally:
        _cleanup(db, org, user, ev)


def test_an_unregistered_viewer_gets_no_livekit_credentials():
    """registration_required gates the media credentials themselves (can_stream), not just
    the page — otherwise the room grant is handed to anyone who loads the URL."""
    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, registration_required=True, start_time=None, end_time=None)
    db.commit()
    try:
        body = client.get(f"/api/events/{ev.id}/watch").json()
        assert body["livekit_token"] is None, body
        assert body["room"] is None, body
    finally:
        _cleanup(db, org, user, ev)


def test_a_viewers_room_is_the_producers_room_and_the_token_cannot_publish():
    """The reported bug's first two suspects, at the route level: the room the audience is
    told to join must be services.livekit.room_for_event (the exact string the producer's
    Ctx.room and its publish token use), and the audience token must be subscribe-only.

    Skipped when the environment has no LiveKit credentials — there is no token to inspect,
    which is correct behaviour, not a failure (services/livekit.configured())."""
    from app.services import livekit as lk

    db = SessionLocal()
    client = TestClient(m.app)
    org = _org(db)
    user = _user(db, org)
    ev = _event(db, org, user, start_time=None, end_time=None)
    db.commit()
    try:
        body = client.get(f"/api/events/{ev.id}/watch").json()
        if not lk.configured():
            assert body["livekit_token"] is None, body
            return
        assert body["room"] == lk.room_for_event(ev.id), body
        grants = _grants(body["livekit_token"])
        # Identical to what services/broadcast.py mints for the host, by construction.
        assert grants["room"] == lk.room_for_event(ev.id)
        assert grants["roomJoin"] is True
        assert grants["canSubscribe"] is True
        assert grants["canPublish"] is False
    finally:
        _cleanup(db, org, user, ev)
