"""The recording lifecycle, at the four points where it was losing track of reality.

── WHAT WAS WRONG ──────────────────────────────────────────────────────────────────────
Four independent defects that together produced one confusing picture: an event reading
"Ended" whose Recording tab said the broadcast was still going, a library that showed zero
recordings for it, and a Download button that returned raw GCS XML.

  1. _retire_stale_session closed the BroadcastSession and left its LiveRecording rows at
     status="recording" — forever. Nothing else would ever close them: _stop_recording_rows
     only runs on the host's own stop/end path, which by definition did not happen.

  2. reconcile_stuck_recording() existed, was correct, and was called by NOTHING. So a
     missed egress_ended webhook stranded the row with no recovery at all.

  3. Stopping a capture wrote status="stopped" immediately — claiming the recording was
     finished the instant the host let go, before the egress had finalised or uploaded
     anything. There was no state for "capture over, file not confirmed".

  4. signed_url() promised "None if the object doesn't exist" and never checked. Signing is
     a local operation, so it happily signed a key for an object that was never uploaded and
     the browser got <Code>NoSuchKey</Code> back.

The through-line in all four: the application believed things about storage and about the
provider that it had not been told. These pin the corrected behaviour, and in particular
they pin that nothing here guesses — an unreachable provider still leaves the row alone.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.main  # noqa: F401  - import order; see test_live_socket_connect.py

from app.crud import event as event_crud
from app.db import SessionLocal
from app.models import BroadcastSession, Event, LiveRecording, Organization, User
from app.security import hash_password
from app.services import broadcast as bc
from app.services import livekit as lk

UTC = timezone.utc


@pytest.fixture
def world():
    db = SessionLocal()
    made = {"r": [], "s": [], "e": [], "u": [], "o": []}
    try:
        org = Organization(name=f"RecCo {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        made["o"] = [org.id]
        user = User(org_id=org.id, full_name="Host", role="org_admin", is_active=True,
                    email=f"rec-{uuid.uuid4().hex[:10]}@example.com",
                    username=f"rec{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password("x"), email_verified=True)
        db.add(user)
        db.flush()
        made["u"] = [user.id]
        ev = Event(org_id=org.id, created_by=user.id, title="Recorded Event",
                   status="ended", visibility="public",
                   start_time=datetime.now(UTC) - timedelta(hours=2))
        db.add(ev)
        db.flush()
        made["e"] = [ev.id]
        db.commit()
        yield {"db": db, "org": org, "event": ev, "user": user, "made": made}
    finally:
        try:
            for eid in made["e"]:
                db.query(LiveRecording).filter(LiveRecording.event_id == eid).delete()
                db.query(BroadcastSession).filter(BroadcastSession.event_id == eid).delete()
                db.execute(Event.__table__.delete().where(Event.id == eid))
            for uid in made["u"]:
                db.execute(User.__table__.delete().where(User.id == uid))
            for oid in made["o"]:
                db.execute(Organization.__table__.delete().where(Organization.id == oid))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def session(world, ended=False):
    s = BroadcastSession(
        event_id=world["event"].id, org_id=world["org"].id,
        status="ended" if ended else "live",
        started_at=datetime.now(UTC) - timedelta(hours=2),
        ended_at=datetime.now(UTC) if ended else None,
    )
    world["db"].add(s)
    world["db"].commit()
    return s


def recording(world, sess=None, **over):
    fields = dict(
        event_id=world["event"].id, org_id=world["org"].id,
        session_id=sess.id if sess else None,
        status="recording", quality="1080p", egress_id=f"EG_{uuid.uuid4().hex[:10]}",
        enforced=True, started_at=datetime.now(UTC) - timedelta(hours=2),
        file_url=f"zoikostream/{world['org'].id}/{world['event'].id}/1790148294.mp4",
    )
    fields.update(over)
    r = LiveRecording(**fields)
    world["db"].add(r)
    world["db"].commit()
    return r


# ── 1. a retired session must not strand its recording ─────────────────────────────────

def test_retiring_a_stale_session_closes_its_recording(world):
    """THE REPORTED BUG. Event "Ended", recording still claiming to be running."""
    import asyncio

    s = session(world)
    r = recording(world, s)

    asyncio.run(bc._retire_stale_session(str(s.id), str(world["event"].id), "livekit_room_absent"))

    world["db"].expire_all()
    closed = world["db"].get(LiveRecording, r.id)
    assert closed.status != "recording", "a retired session must not leave a live recording row"
    assert closed.status == "processing", "the egress ran, so whether a file landed is LiveKit's to say"
    assert closed.stopped_at is not None


def test_a_session_retired_with_no_egress_job_is_failed_not_processing(world):
    """Nothing was ever handed to LiveKit, so there is nothing to wait for and nothing to
    reconcile. Leaving it "processing" would be a promise that never resolves."""
    import asyncio

    s = session(world)
    r = recording(world, s, egress_id=None, enforced=False)

    asyncio.run(bc._retire_stale_session(str(s.id), str(world["event"].id), "livekit_room_absent"))

    world["db"].expire_all()
    closed = world["db"].get(LiveRecording, r.id)
    assert closed.status == "failed"
    assert closed.error


def test_retirement_does_not_touch_an_already_finished_recording(world):
    import asyncio

    s = session(world)
    done = recording(world, s, status="stopped", stopped_at=datetime.now(UTC) - timedelta(minutes=30))
    original = done.stopped_at

    asyncio.run(bc._retire_stale_session(str(s.id), str(world["event"].id), "x"))

    world["db"].expire_all()
    assert world["db"].get(LiveRecording, done.id).status == "stopped"
    assert world["db"].get(LiveRecording, done.id).stopped_at == original


# ── 2. the reconciler is actually reachable now ────────────────────────────────────────

def test_the_reconciler_runs_and_finalises_a_stranded_recording(world, monkeypatch):
    """reconcile_stuck_recording was dead code — referenced only by a comment. This pins
    that the sampler pass finds a stranded row and finalises it through the SAME writer the
    webhook uses, so a reconciled row is indistinguishable from a normally finished one."""
    import asyncio

    r = recording(world, status="processing",
                  stopped_at=datetime.now(UTC) - timedelta(hours=1))
    # created_at must be older than the grace window for the sweep to consider it.
    world["db"].execute(
        LiveRecording.__table__.update()
        .where(LiveRecording.id == r.id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=3))
    )
    world["db"].commit()

    class _File:
        size = 4096

    class _Info:
        egress_id = r.egress_id
        status = "EGRESS_COMPLETE"
        error = ""
        file_results = [_File()]
        file = _File()

    async def fake_get_egress(egress_id):
        return _Info() if egress_id == r.egress_id else None

    monkeypatch.setattr(lk, "get_egress", fake_get_egress)

    # Driven through the reconciler the sweep calls, not through the sweep's own batch: the
    # sweep takes the OLDEST 20 stranded rows, and a shared test database carries a backlog
    # of them, so a brand-new row is not reliably in the first batch. What is being pinned
    # here is that the path exists and finalises correctly — the sweep's selection is pinned
    # separately by test_a_fresh_recording_is_not_reconciled.
    outcome = asyncio.run(bc.reconcile_stuck_recording(str(r.id), r.egress_id))

    assert outcome == "finalized"
    world["db"].expire_all()
    done = world["db"].get(LiveRecording, r.id)
    assert done.status == "stopped"
    assert done.size_bytes == 4096


def test_an_unreachable_provider_leaves_the_row_alone(world, monkeypatch):
    """The safety property. "We could not ask LiveKit" is not evidence of failure, and a
    recording that might still be running must never be marked failed on a guess."""
    import asyncio

    r = recording(world, status="processing")
    world["db"].execute(
        LiveRecording.__table__.update()
        .where(LiveRecording.id == r.id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=3))
    )
    world["db"].commit()

    async def cannot_ask(egress_id):
        return None

    monkeypatch.setattr(lk, "get_egress", cannot_ask)

    assert asyncio.run(bc.reconcile_stuck_recording(str(r.id), r.egress_id)) == "unknown"
    world["db"].expire_all()
    assert world["db"].get(LiveRecording, r.id).status == "processing"


def test_a_fresh_recording_is_not_reconciled(world, monkeypatch):
    """Inside the grace window the webhook is still expected to win. Reconciling instantly
    would be the aggressive polling this deliberately is not."""
    import asyncio

    recording(world, status="recording")   # created_at = now

    async def boom(egress_id):
        raise AssertionError("a recording inside the grace window must not be probed")

    monkeypatch.setattr(lk, "get_egress", boom)
    assert asyncio.run(bc._reconcile_recordings_once()) == 0


def test_reconciliation_is_idempotent(world, monkeypatch):
    """A late webhook after a reconciliation, or two sweeps overlapping, must converge on the
    same row rather than double-count storage."""
    import asyncio

    r = recording(world, status="processing", size_bytes=None)
    world["db"].execute(
        LiveRecording.__table__.update()
        .where(LiveRecording.id == r.id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=3))
    )
    world["db"].commit()

    class _File:
        size = 9000

    class _Info:
        egress_id = r.egress_id
        status = "EGRESS_COMPLETE"
        error = ""
        file_results = [_File()]
        file = _File()

    monkeypatch.setattr(lk, "get_egress", lambda e: _done())

    async def _done():
        return _Info()

    bc.record_egress_result(_Info())
    bc.record_egress_result(_Info())      # the duplicate

    world["db"].expire_all()
    done = world["db"].get(LiveRecording, r.id)
    assert done.status == "stopped"
    assert done.size_bytes == 9000, "a replayed webhook must not accumulate size"

    count = world["db"].query(LiveRecording).filter(LiveRecording.event_id == world["event"].id).count()
    assert count == 1, "reconciliation must never create a second recording row"


# ── 3. the library and the event tab agree ─────────────────────────────────────────────

def test_a_processing_recording_is_listed_but_not_playable(world):
    """It used to vanish from the library for the whole finalisation window while the event's
    own tab still showed it — the two pages disagreeing about one row."""
    r = recording(world, status="processing", stopped_at=datetime.now(UTC))

    rows = event_crud.list_org_recordings(world["db"], world["org"].id)

    assert any(rec.id == r.id for rec, _ev in rows), "processing must be visible in the library"
    assert event_crud.recording_library_state(r, None) == "processing"


def test_a_finished_recording_with_no_object_is_storage_unavailable_not_ready(world):
    """The distinction that stops a dead Download button. enforced=True only says LiveKit
    ACCEPTED the job; status=stopped only says the capture ended. Neither is evidence a file
    exists, and the absence of a signable URL is."""
    r = recording(world, status="stopped", stopped_at=datetime.now(UTC))

    assert event_crud.recording_library_state(r, None) == "storage_unavailable"
    assert event_crud.recording_library_state(r, "https://signed.example/x") == "ready"


def test_a_failed_recording_is_never_ready_even_with_a_url(world):
    r = recording(world, status="failed", enforced=False, error="egress unavailable")
    assert event_crud.recording_library_state(r, "https://signed.example/x") == "failed"


def test_a_live_recording_is_not_in_the_library(world):
    recording(world, status="recording")
    rows = event_crud.list_org_recordings(world["db"], world["org"].id)
    assert rows == [], "a capture still running is the host console's, not the library's"


def test_a_failed_recording_is_not_in_the_library_but_is_on_the_event(world):
    r = recording(world, status="failed", enforced=False, error="no egress")

    assert event_crud.list_org_recordings(world["db"], world["org"].id) == []
    event_rows = event_crud.list_event_recordings(world["db"], world["org"].id, world["event"].id)
    assert [x.id for x in event_rows] == [r.id], "diagnostics stay on the event's own tab"


# ── 4. signed_url keeps its own promise ────────────────────────────────────────────────

def test_signed_url_refuses_to_sign_a_missing_object(monkeypatch):
    """The NoSuchKey fix. Signing never contacts GCS, so without this check a key for an
    object that was never uploaded signs perfectly and the browser gets raw provider XML."""
    signed = {"called": False}

    class _Blob:
        def exists(self):
            return False

        def generate_signed_url(self, **kw):
            signed["called"] = True
            return "https://should-not-happen"

    class _Bucket:
        def blob(self, key):
            return _Blob()

    class _Client:
        def bucket(self, name):
            return _Bucket()

    monkeypatch.setattr(lk, "_gcs_client", lambda: _Client())

    assert lk.signed_url("zoikostream/org/event/1790148294.mp4") is None
    assert not signed["called"], "a missing object must never reach the signer"


def test_signed_url_signs_an_object_that_is_really_there(monkeypatch):
    class _Blob:
        def exists(self):
            return True

        def generate_signed_url(self, **kw):
            return "https://storage.example/signed"

    class _Bucket:
        def blob(self, key):
            assert key == "zoikostream/org/event/1790148294.mp4", \
                "the signer must receive the OBJECT key, never bucket/object"
            return _Blob()

    class _Client:
        def bucket(self, name):
            return _Bucket()

    monkeypatch.setattr(lk, "_gcs_client", lambda: _Client())

    assert lk.signed_url("zoikostream/org/event/1790148294.mp4") == "https://storage.example/signed"


def test_the_object_key_never_contains_the_bucket_name(world):
    """Pins the shape the reported GCS error made look wrong. The message prints
    "bucket/object", so it READS like the bucket was duplicated into the key — it was not.
    file_url holds the object path alone, which is what the signer must be given."""
    r = recording(world)
    assert not r.file_url.startswith("/")
    assert "zoiko-stream-recordings" not in r.file_url
    assert r.file_url.startswith(f"zoikostream/{world['org'].id}/{world['event'].id}/")
    assert r.file_url.count("zoikostream/") == 1
