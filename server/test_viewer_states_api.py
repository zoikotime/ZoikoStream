"""What GET /events/{id}/watch tells the viewer page about replay (WatchOut.replay_state).

The ended page used to say "No recording is available" for every case it could not play —
never recorded, still being prepared, withheld, expired. ZST-SPEC-VAP-001 §6.3 asks for
distinct Processing / Available / Expired states, so the endpoint now names which one is TRUE,
from the same ReplayEntitlement the playback gate already reads:

  available   -> recording_url is set (and until when, if the entitlement says)
  processing  -> published and the watermarked copy is being made, or under validation
  expired     -> the replay window has closed
  unavailable -> anything else, including a recording waiting on an operator's decision
                 (it may never be published, so "processing" would be a promise)
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import Event, Organization, ReplayEntitlement, User
from app.security import hash_password
from app.services import bus, livekit

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _in_process_bus(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    yield


@pytest.fixture
def ended():
    db = SessionLocal()
    org = Organization(name=f"Replay {uuid.uuid4().hex[:6]}", status="active")
    db.add(org)
    db.flush()
    u = User(org_id=org.id, full_name="Fixture", role="org_admin", is_active=True,
             email=f"rp-{uuid.uuid4().hex[:10]}@example.com", username=f"rp{uuid.uuid4().hex[:10]}",
             password_hash=hash_password("x"), email_verified=True)
    db.add(u)
    db.flush()
    start = datetime.now(UTC) - timedelta(hours=3)
    ev = Event(org_id=org.id, created_by=u.id, title="Service", status="ended", visibility="private",
               start_time=start, end_time=start + timedelta(hours=1))
    db.add(ev)
    db.commit()
    try:
        yield type("W", (), {"db": db, "event": ev, "user": u})()
    finally:
        db.rollback()
        db.query(ReplayEntitlement).filter(ReplayEntitlement.event_id == ev.id).delete()
        db.query(Event).filter(Event.id == ev.id).delete()
        db.query(User).filter(User.id == u.id).delete()
        db.query(Organization).filter(Organization.id == org.id).delete()
        db.commit()
        db.close()


def watch(w):
    from app.security import create_access_token
    c = TestClient(m.app)
    c.headers["Authorization"] = f"Bearer {create_access_token(w.user, remember=False)}"
    r = c.get(f"/api/events/{w.event.id}/watch")
    assert r.status_code == 200, r.text
    return r.json()


def entitle(w, **fields):
    ent = ReplayEntitlement(event_id=w.event.id, scope="audience", **fields)
    w.db.add(ent)
    w.db.commit()
    return ent


def test_no_entitlement_is_unavailable(ended):
    out = watch(ended)
    assert out["replay_state"] == "unavailable" and out["recording_url"] is None


@pytest.mark.parametrize("fields", [
    {"publish_state": "published", "watermark_status": "pending"},
    {"publish_state": "validating"},
])
def test_a_replay_on_its_way_is_processing(ended, fields):
    entitle(ended, **fields)
    assert watch(ended)["replay_state"] == "processing"


@pytest.mark.parametrize("fields", [
    {"publish_state": "expired"},
    {"publish_state": "published", "watermark_status": "ready",
     "expires_at": datetime.now(UTC) - timedelta(days=1)},
])
def test_a_closed_window_is_expired(ended, fields):
    entitle(ended, **fields)
    out = watch(ended)
    assert out["replay_state"] == "expired" and out["recording_url"] is None


@pytest.mark.parametrize("state", ["ready_for_review", "withheld", "not_available"])
def test_a_replay_nobody_has_published_is_not_promised(ended, state):
    entitle(ended, publish_state=state)
    assert watch(ended)["replay_state"] == "unavailable"


def test_a_served_replay_is_available_with_its_end_date(ended, monkeypatch):
    until = datetime.now(UTC) + timedelta(days=30)
    entitle(ended, publish_state="published", watermark_status="ready",
            watermarked_file_key="replays/x.mp4", expires_at=until)
    monkeypatch.setattr(livekit, "object_exists", lambda key: True)
    monkeypatch.setattr(livekit, "signed_url", lambda key: "https://storage.example.invalid/signed")
    out = watch(ended)
    assert out["replay_state"] == "available" and out["recording_url"]
    assert datetime.fromisoformat(out["replay_available_until"]).timestamp() == pytest.approx(until.timestamp(), abs=1)


def test_a_missing_file_is_unavailable_not_available(ended, monkeypatch):
    entitle(ended, publish_state="published", watermark_status="ready", watermarked_file_key="replays/gone.mp4")
    monkeypatch.setattr(livekit, "object_exists", lambda key: False)
    out = watch(ended)
    assert out["replay_state"] == "unavailable" and out["recording_url"] is None


def test_replay_state_is_only_set_once_the_event_has_ended(ended):
    ended.event.status = "live"
    ended.event.end_time = datetime.now(UTC) + timedelta(hours=1)
    ended.db.commit()
    assert watch(ended)["replay_state"] is None
