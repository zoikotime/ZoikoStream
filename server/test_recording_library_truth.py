"""Organization Recordings: what happened to every attempt, said truthfully.

  * A recording is made only when the host presses Record (auto-start is deliberately absent,
    see test_storage_config). The library lists captured recordings only, and that stays.
  * What it no longer does is say "0" while an attempt failed or is still running:
    /organization/recordings/summary counts every attempt in SQL and names the latest
    failure, so an empty library is never mistaken for "nothing was attempted".
  * A CONFIGURED storage credential that cannot be used means no file can reach the bucket,
    so Record refuses with that reason instead of recording the event into nothing. An
    UNSET credential still reaches egress, which may carry its own storage.
  * The event's Recording tab and the library give one row one verdict.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, Event, LiveActivity, LiveRecording, Organization, User
from app.security import create_access_token, hash_password
from app.services import bus
from app.services import livekit as lk
from app.services import moderation as mod

UTC = timezone.utc
NOW = datetime.now(UTC)
CONSOLE_URL = "https://console.cloud.google.com/iam-admin/serviceaccounts/details/123"


@pytest.fixture(autouse=True)
def in_process_bus_and_clean_gcs_cache():
    url = bus.settings.REDIS_URL
    bus.settings.REDIS_URL = ""
    lk._gcs_credentials_json.cache_clear()  # noqa: SLF001
    lk._gcs_client.cache_clear()            # noqa: SLF001
    try:
        yield
    finally:
        bus.settings.REDIS_URL = url
        lk._gcs_credentials_json.cache_clear()  # noqa: SLF001
        lk._gcs_client.cache_clear()            # noqa: SLF001


@pytest.fixture
def world():
    db = SessionLocal()
    orgs, users, events = [], [], []

    def org_with_admin(name):
        o = Organization(name=f"{name} {uuid.uuid4().hex[:6]}", status="active")
        db.add(o)
        db.flush()
        u = User(org_id=o.id, full_name="Host", role="org_admin", is_active=True,
                 email=f"lib-{uuid.uuid4().hex[:10]}@example.com",
                 username=f"lib{uuid.uuid4().hex[:10]}", password_hash=hash_password("x"),
                 email_verified=True)
        db.add(u)
        db.flush()
        orgs.append(o.id)
        users.append(u.id)
        return o, u

    def event(o, u, title="Town hall", status="ended"):
        e = Event(org_id=o.id, created_by=u.id, title=title, status=status, visibility="public",
                  recording_enabled=True, start_time=NOW - timedelta(hours=2))
        db.add(e)
        db.flush()
        events.append(e.id)
        return e

    def rec(e, **kw):
        kw.setdefault("status", "stopped")
        kw.setdefault("enforced", True)
        r = LiveRecording(event_id=e.id, org_id=e.org_id, quality="1080p",
                          started_at=kw.pop("started_at", NOW - timedelta(hours=2)),
                          stopped_at=kw.pop("stopped_at", NOW - timedelta(hours=1)), **kw)
        db.add(r)
        db.flush()
        return r

    ns = type("W", (), {})()
    ns.db, ns.org_with_admin, ns.event, ns.rec = db, org_with_admin, event, rec
    try:
        yield ns
    finally:
        db.rollback()
        # recording.start also writes the console's activity entry and an audit row.
        db.query(LiveActivity).filter(LiveActivity.event_id.in_(events or [uuid.uuid4()])).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.org_id.in_(orgs or [uuid.uuid4()])).delete(synchronize_session=False)
        db.query(LiveRecording).filter(LiveRecording.event_id.in_(events or [uuid.uuid4()])).delete(synchronize_session=False)
        db.query(Event).filter(Event.id.in_(events or [uuid.uuid4()])).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(users or [uuid.uuid4()])).delete(synchronize_session=False)
        db.query(Organization).filter(Organization.id.in_(orgs or [uuid.uuid4()])).delete(synchronize_session=False)
        db.commit()
        db.close()


def client_for(u):
    c = TestClient(m.app)
    c.headers["Authorization"] = f"Bearer {create_access_token(u, remember=False)}"
    return c


# ── the storage gate on Record ─────────────────────────────────────────────────────────

def test_an_unset_credential_path_is_not_blocked(monkeypatch):
    monkeypatch.setattr(settings, "GCS_CREDENTIALS_PATH", "")
    monkeypatch.setattr(settings, "GCS_BUCKET", "zoiko-stream-recordings")
    assert lk.gcs_upload_blocked() is None, "egress may carry its own storage"


@pytest.mark.parametrize("path,needle", [
    (CONSOLE_URL, "URL"),
    ("/definitely/not/here/gcs-key.json", "could not be read"),
])
def test_a_configured_but_unusable_credential_blocks_uploads(monkeypatch, path, needle):
    monkeypatch.setattr(settings, "GCS_CREDENTIALS_PATH", path)
    monkeypatch.setattr(settings, "GCS_BUCKET", "zoiko-stream-recordings")
    blocked = lk.gcs_upload_blocked()
    assert blocked and needle in blocked


def _record(world, monkeypatch, *, credential_path):
    o, u = world.org_with_admin("RecGate")
    e = world.event(o, u, status="live")
    world.db.commit()
    monkeypatch.setattr(settings, "GCS_CREDENTIALS_PATH", credential_path)
    monkeypatch.setattr(settings, "GCS_BUCKET", "zoiko-stream-recordings")
    calls = []

    async def fake_start(room, quality, filepath):
        calls.append(filepath)
        return "EG_test", None

    monkeypatch.setattr(lk, "start_recording", fake_start)
    ctx = mod.Ctx(event_id=e.id, org_id=o.id, room=f"event_{e.id}", user_id=u.id, name="Host",
                  identity=str(u.id), role="org_admin", can_moderate=True, can_host=True)
    asyncio.run(mod.dispatch(ctx, "recording.start", {}))
    world.db.expire_all()
    return calls, world.db.query(LiveRecording).filter(LiveRecording.event_id == e.id).all()


def test_record_refuses_to_start_egress_when_the_credential_is_broken(world, monkeypatch):
    calls, rows = _record(world, monkeypatch, credential_path=CONSOLE_URL)
    assert calls == [], "no egress may be started into a bucket it cannot reach"
    (row,) = rows
    assert (row.status, row.enforced, row.egress_id, row.file_url) == ("failed", False, None, None)
    assert "URL" in (row.error or ""), "the host is told the real cause"


def test_record_still_reaches_egress_when_no_credential_is_configured(world, monkeypatch):
    calls, rows = _record(world, monkeypatch, credential_path="")
    assert len(calls) == 1
    (row,) = rows
    assert (row.status, row.enforced, row.egress_id) == ("recording", True, "EG_test")
    assert row.file_url == calls[0], "the stored key is the path egress was given"


# ── the summary: every attempt, counted over the whole org ─────────────────────────────

def test_the_summary_counts_every_attempt_and_names_the_latest_failure(world):
    o, u = world.org_with_admin("Summary")
    ev = world.event(o, u, title="Board meeting")
    world.rec(ev, size_bytes=1024)                                          # captured
    world.rec(ev, status="processing", stopped_at=None)                     # finalising
    world.rec(ev, status="recording", stopped_at=None)                      # running
    world.rec(ev, status="failed", enforced=False, error="Egress unavailable",
              stopped_at=NOW - timedelta(minutes=30))                        # never captured
    world.rec(ev, status="failed", enforced=True, error="Upload failed",
              stopped_at=NOW - timedelta(minutes=5))                         # latest failure
    other_o, other_u = world.org_with_admin("Other")
    world.rec(world.event(other_o, other_u), status="failed", enforced=False, error="theirs")
    world.db.commit()

    body = client_for(u).get("/api/organization/recordings/summary").json()
    assert body["library_total"] == 2, "stopped + processing, captured"
    assert body["in_progress"] == 1
    assert body["failed_attempts"] == 2, "another organization's failures are not counted"
    assert body["latest_failure"]["event_title"] == "Board meeting"
    assert body["latest_failure"]["error"] == "Upload failed"


def test_an_empty_organization_reports_zeros_and_no_failure(world):
    _o, u = world.org_with_admin("Empty")
    world.db.commit()
    body = client_for(u).get("/api/organization/recordings/summary").json()
    assert body == {"library_total": 0, "in_progress": 0, "failed_attempts": 0, "latest_failure": None}


def test_the_summary_requires_membership(world):
    r = TestClient(m.app).get("/api/organization/recordings/summary")
    assert r.status_code == 401


# ── one row, one verdict, on both pages ────────────────────────────────────────────────

@pytest.mark.parametrize("signed,expected", [("https://signed.example/obj", "ready"), (None, "storage_unavailable")])
def test_the_event_tab_and_the_library_agree(world, monkeypatch, signed, expected):
    o, u = world.org_with_admin("Verdict")
    ev = world.event(o, u)
    r = world.rec(ev, file_url=f"zoikostream/{o.id}/{ev.id}/1.mp4", size_bytes=0)
    world.db.commit()
    monkeypatch.setattr(lk, "signed_url", lambda key, *a, **k: signed)
    c = client_for(u)
    library = {x["id"]: x for x in c.get("/api/organization/recordings").json()}
    tab = {x["id"]: x for x in c.get(f"/api/organization/events/{ev.id}/recordings").json()}
    assert library[str(r.id)]["state"] == tab[str(r.id)]["state"] == expected
    assert library[str(r.id)]["size_bytes"] == 0, "a zero-byte capture reports 0, not null"
    assert (library[str(r.id)]["url"] is None) == (expected != "ready"), "no link without a file"


def test_a_live_row_is_in_progress_on_the_event_tab_not_a_missing_file(world, monkeypatch):
    o, u = world.org_with_admin("Live")
    ev = world.event(o, u, status="live")
    r = world.rec(ev, status="recording", stopped_at=None, file_url="zoikostream/x/y/1.mp4")
    world.db.commit()
    monkeypatch.setattr(lk, "signed_url", lambda key, *a, **k: None)
    tab = {x["id"]: x for x in client_for(u).get(f"/api/organization/events/{ev.id}/recordings").json()}
    assert tab[str(r.id)]["state"] == "in_progress"
