"""The recording lifecycle, and the endpoint an event's Recording tab reads.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
Event Details -> Recording printed "No recording available" for every event, always. The tab
was hardcoded markup with NO data source: no fetch, no endpoint, nothing. A host who recorded
a broadcast and ended it saw the same sentence as a host who never pressed Record, and so did
an event whose file was captured and sitting in storage.

There was also no per-event endpoint to call. /organization/recordings is the playable
LIBRARY and deliberately filters to status=stopped + enforced=True, so a run where LiveKit
egress never started — which is exactly what a bad GCS credential produces — was invisible
everywhere, with its real reason sitting unread in LiveRecording.error.

These pin the lifecycle end to end and, above all, that a failed capture REPORTS ITSELF
rather than rendering as an absence.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.crud import event as event_crud
from app.db import SessionLocal
from app.models import Event, LiveRecording, Organization, User
from app.security import hash_password

UTC = timezone.utc


@pytest.fixture
def world():
    """One org with an event, plus a second org that must never see it."""
    db = SessionLocal()
    made = {"orgs": [], "events": [], "users": [], "recs": []}
    try:
        org = Organization(name=f"RecCo {uuid.uuid4().hex[:6]}", status="active")
        other = Organization(name=f"OtherRec {uuid.uuid4().hex[:6]}", status="active")
        db.add_all([org, other])
        db.flush()
        made["orgs"] = [org.id, other.id]

        user = User(org_id=org.id, full_name="Rec Host", role="org_admin", is_active=True,
                    email=f"rec-{uuid.uuid4().hex[:10]}@example.com",
                    username=f"rec{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password("x"), email_verified=True)
        db.add(user)
        db.flush()
        made["users"] = [user.id]

        ev = Event(org_id=org.id, created_by=user.id, title="Recorded event",
                   status="ended", visibility="public", recording_enabled=True,
                   start_time=datetime.now(UTC) - timedelta(hours=1))
        foreign = Event(org_id=other.id, created_by=user.id, title="Someone else's",
                        status="ended", visibility="public",
                        start_time=datetime.now(UTC) - timedelta(hours=1))
        db.add_all([ev, foreign])
        db.flush()
        made["events"] = [ev.id, foreign.id]
        db.commit()
        yield {"db": db, "org": org, "other": other, "event": ev, "foreign": foreign,
               "user": user, "made": made}
    finally:
        try:
            for eid in made["events"]:
                db.query(LiveRecording).filter(LiveRecording.event_id == eid).delete()
                db.query(Event).filter(Event.id == eid).delete()
            for uid in made["users"]:
                db.query(User).filter(User.id == uid).delete()
            for oid in made["orgs"]:
                db.query(Organization).filter(Organization.id == oid).delete()
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def _rec(db, ev, **over):
    """A recording row. Defaults describe a SUCCESSFUL capture."""
    fields = dict(
        event_id=ev.id, org_id=ev.org_id, status="stopped", enforced=True,
        quality="1080p", egress_id=f"EG_{uuid.uuid4().hex[:8]}",
        started_at=datetime.now(UTC) - timedelta(minutes=30),
        stopped_at=datetime.now(UTC) - timedelta(minutes=8),
        file_url=f"zoikostream/{ev.org_id}/{ev.id}/file.mp4",
        size_bytes=1024 * 1024 * 40, paused_ms=0,
    )
    fields.update(over)
    r = LiveRecording(**fields)
    db.add(r)
    db.flush()
    return r


# ── 1. start: a row is created even when egress refuses ────────────────────────────────

def test_a_started_recording_is_visible_while_it_runs(world):
    db, ev = world["db"], world["event"]
    _rec(db, ev, status="recording", stopped_at=None, size_bytes=None)
    db.commit()

    rows = event_crud.list_event_recordings(db, ev.org_id, ev.id)
    assert len(rows) == 1
    assert rows[0].status == "recording"


def test_an_unenforced_attempt_is_still_returned(world):
    """The heart of the bug: egress never accepted the job, so there is no file — but there
    IS a row, and it carries the reason. It must not be filtered out."""
    db, ev = world["db"], world["event"]
    _rec(db, ev, status="stopped", enforced=False, egress_id=None, size_bytes=None,
         error="GCS_CREDENTIALS_PATH is a URL, not a path to a service-account key file.")
    db.commit()

    rows = event_crud.list_event_recordings(db, ev.org_id, ev.id)
    assert len(rows) == 1
    assert rows[0].enforced is False
    assert "service-account" in rows[0].error


# ── 2/3/4. stop, egress completion, status update ──────────────────────────────────────

def test_a_completed_recording_carries_a_file_and_a_size(world):
    db, ev = world["db"], world["event"]
    r = _rec(db, ev)
    db.commit()

    got = event_crud.list_event_recordings(db, ev.org_id, ev.id)[0]
    assert got.status == "stopped" and got.enforced is True
    assert got.file_url and got.size_bytes == r.size_bytes
    assert got.stopped_at is not None


def test_duration_excludes_paused_time(world):
    """The endpoint computes duration as stopped-started minus paused_ms; a paused stretch
    is not recorded footage and must not be billed as it."""
    db, ev = world["db"], world["event"]
    start = datetime.now(UTC) - timedelta(minutes=10)
    r = _rec(db, ev, started_at=start, stopped_at=start + timedelta(minutes=10),
             paused_ms=120_000)
    db.commit()

    raw = int((r.stopped_at - r.started_at).total_seconds() - r.paused_ms / 1000)
    assert raw == 480          # 10 minutes wall clock, 2 paused


def test_a_failed_egress_is_recorded_as_failed_not_absent(world):
    db, ev = world["db"], world["event"]
    _rec(db, ev, status="failed", enforced=False, file_url=None, size_bytes=None,
         error="Egress aborted by the media server")
    db.commit()

    rows = event_crud.list_event_recordings(db, ev.org_id, ev.id)
    assert len(rows) == 1, "a failed attempt must not vanish"
    assert rows[0].status == "failed"
    assert rows[0].error


# ── 5. the recording belongs to the right event, and only that event ───────────────────

def test_recordings_are_scoped_to_their_event(world):
    db, ev, foreign = world["db"], world["event"], world["foreign"]
    _rec(db, ev)
    _rec(db, foreign, org_id=foreign.org_id)
    db.commit()

    mine = event_crud.list_event_recordings(db, ev.org_id, ev.id)
    assert len(mine) == 1
    assert all(r.event_id == ev.id for r in mine)


def test_one_organization_never_reads_another_recording(world):
    """Scoped through Event.org_id, so a stale org_id copied onto the recording row cannot
    leak it — the row below deliberately carries the WRONG org_id."""
    db, ev, other = world["db"], world["event"], world["other"]
    _rec(db, ev, org_id=other.id)          # mislabelled on the recording row itself
    db.commit()

    assert event_crud.list_event_recordings(db, other.id, ev.id) == []
    assert len(event_crud.list_event_recordings(db, ev.org_id, ev.id)) == 1


def test_newest_first(world):
    db, ev = world["db"], world["event"]
    old = _rec(db, ev, started_at=datetime.now(UTC) - timedelta(hours=3))
    new = _rec(db, ev, started_at=datetime.now(UTC) - timedelta(minutes=5))
    db.commit()

    rows = event_crud.list_event_recordings(db, ev.org_id, ev.id)
    assert [r.id for r in rows] == [new.id, old.id]


def test_both_paths_of_a_dual_recording_are_listed(world):
    db, ev = world["db"], world["event"]
    _rec(db, ev, role="primary")
    _rec(db, ev, role="secondary")
    db.commit()

    roles = {r.role for r in event_crud.list_event_recordings(db, ev.org_id, ev.id)}
    assert roles == {"primary", "secondary"}


# ── 6. the library stays a library ─────────────────────────────────────────────────────

def test_the_org_library_still_hides_uncaptured_attempts(world):
    """/organization/recordings must keep filtering to playable files — a dead Watch link in
    the library is worse than an absent row. The EVENT view is the one that explains."""
    db, ev = world["db"], world["event"]
    _rec(db, ev, status="stopped", enforced=False, error="not captured", file_url=None)
    db.commit()

    library = event_crud.list_org_recordings(db, ev.org_id)
    assert all(r.event_id != ev.id for r, _e in library)
    # ...while the event's own view shows it.
    assert len(event_crud.list_event_recordings(db, ev.org_id, ev.id)) == 1


def test_a_captured_recording_appears_in_BOTH_views(world):
    """The success path the reporter expected: record, end, and it shows up in the event tab
    AND in the organization Recordings page."""
    db, ev = world["db"], world["event"]
    r = _rec(db, ev)
    db.commit()

    assert r.id in {x.id for x in event_crud.list_event_recordings(db, ev.org_id, ev.id)}
    assert r.id in {rec.id for rec, _e in event_crud.list_org_recordings(db, ev.org_id)}


def test_an_event_with_no_attempt_returns_an_empty_list(world):
    """Genuinely nothing recorded — an empty list, which the client renders as the real
    "no recording" state. Distinct from a request that failed."""
    db, ev = world["db"], world["event"]
    assert event_crud.list_event_recordings(db, ev.org_id, ev.id) == []


# ── 3/4 again, this time through the real webhook path ─────────────────────────────────
#
# The tests above describe rows; these drive services.broadcast.record_egress_result, which
# is what the egress_ended webhook calls. It opens its OWN session, so the fixture's rows
# have to be committed — they are.

class _FileResult:
    def __init__(self, size):
        self.size = size


class _EgressInfo:
    """The shape record_egress_result reads off a LiveKit EgressInfo."""

    def __init__(self, egress_id, size=None, error=""):
        self.egress_id = egress_id
        self.error = error
        self.file_results = [_FileResult(size)] if size is not None else []
        self.file = None


def test_egress_completion_marks_the_row_stopped_and_writes_the_real_size(world):
    from app.services import broadcast

    db, ev = world["db"], world["event"]
    r = _rec(db, ev, status="recording", stopped_at=None, size_bytes=None)
    db.commit()

    out = broadcast.record_egress_result(_EgressInfo(r.egress_id, size=987_654_321))

    assert out is not None
    db.expire_all()
    fresh = db.get(LiveRecording, r.id)
    assert fresh.status == "stopped"
    assert fresh.size_bytes == 987_654_321     # the size LiveKit reported, not our estimate
    assert fresh.stopped_at is not None
    # And that is precisely the row the event tab now reads.
    assert fresh.id in {x.id for x in event_crud.list_event_recordings(db, ev.org_id, ev.id)}


def test_egress_failure_is_written_as_failed_with_its_reason(world):
    """The path that produced the report. LiveKit says it went wrong, so the row says so —
    and because the event view does not filter on enforced/status, the operator reads the
    reason instead of "No recording available"."""
    from app.services import broadcast

    db, ev = world["db"], world["event"]
    r = _rec(db, ev, status="recording", stopped_at=None, size_bytes=None)
    db.commit()

    broadcast.record_egress_result(_EgressInfo(r.egress_id, error="upload failed: 403"))

    db.expire_all()
    fresh = db.get(LiveRecording, r.id)
    assert fresh.status == "failed"
    assert "403" in fresh.error

    shown = event_crud.list_event_recordings(db, ev.org_id, ev.id)
    assert [x.id for x in shown] == [r.id], "the failure must be VISIBLE, not filtered away"


def test_an_unknown_egress_id_is_ignored(world):
    """A webhook for a recording this deployment has no row for must not raise or invent
    one — the handler returns None and the event tab is unaffected."""
    from app.services import broadcast

    assert broadcast.record_egress_result(_EgressInfo("EG_does_not_exist", size=1)) is None
