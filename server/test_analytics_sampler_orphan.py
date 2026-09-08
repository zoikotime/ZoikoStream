"""Regression cover for the Cloud Run production error:

    sqlalchemy.exc.IntegrityError (psycopg2.errors.ForeignKeyViolation)
    insert or update on table "analytics_snapshots"
    violates foreign key constraint "analytics_snapshots_event_id_fkey"
    services/broadcast.py run_sampler -> _sample_once -> moderation.tx(write) -> commit

`event_id` reaches AnalyticsSnapshot from exactly ONE place: the BroadcastSession rows with
ended_at IS NULL. The sampler read them in one short-lived transaction and inserted the
snapshot in a LATER, separate one, with no existence check in between.

What must stay true, and is asserted below:

  * a snapshot is NEVER written for an event_id with no events row — validated inside the
    SAME transaction as the insert, not in the earlier read;
  * the FK stays enabled and no placeholder Event is ever invented to satisfy it;
  * ONE stale event does not stop the other sessions in the same tick from being sampled;
  * a session that can never be sampled again (event gone / soft-deleted / terminal status)
    is RETIRED once — not skipped forever, which is what generated ticks and snapshot rows
    for up to 29 days per abandoned session in production;
  * only a ForeignKeyViolation naming THIS constraint is swallowed. Every other
    IntegrityError still propagates to run_sampler's ERROR log with its traceback.

Most tests here are deterministic and touch no database: they drive the real _sample_once
against stubbed seams (mod.tx / bus), which is the only way to script "the event disappears
between the lookup and the commit" reliably. The invariant checks at the end DO use the real
database, because "the FK is still enabled" is not a fact about Python.

Run: `venv/Scripts/python -m pytest test_analytics_sampler_orphan.py`
"""
import asyncio
import logging
import uuid

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.main  # noqa: F401 — import first: app/services/org.py <-> broadcast circular import
from app.db import SessionLocal
from app.models import AnalyticsSnapshot, BroadcastSession, Event
from app.models.event import EVENT_STATUSES
from app.services import broadcast as bc
from app.services import moderation as m


# ── fake psycopg2 error shapes ────────────────────────────────────────────────

class _Diag:
    def __init__(self, constraint_name=None, table_name=None):
        self.constraint_name = constraint_name
        self.table_name = table_name


class _PgError(Exception):
    """Shaped like a psycopg2 error: SQLSTATE in .pgcode, constraint in .diag."""

    def __init__(self, pgcode, constraint_name=None, table_name=None, msg="pg error"):
        super().__init__(msg)
        self.pgcode = pgcode
        self.diag = _Diag(constraint_name, table_name)


def _integrity_error(pgcode="23503", constraint="analytics_snapshots_event_id_fkey",
                     table="analytics_snapshots", msg=None):
    msg = msg or ('insert or update on table "analytics_snapshots" violates foreign key '
                  'constraint "analytics_snapshots_event_id_fkey"')
    return IntegrityError("INSERT INTO analytics_snapshots ...", {},
                          _PgError(pgcode, constraint, table, msg))


# ── the classifier: narrow by construction ────────────────────────────────────

def test_the_production_error_is_recognised_as_a_missing_event():
    assert bc._is_missing_event_fk(_integrity_error()) is True


def test_a_missing_constraint_name_still_resolves_via_the_table():
    """Some psycopg2 builds leave diag.constraint_name empty; SQLSTATE 23503 plus the table
    is enough to identify which foreign key without matching English message text."""
    assert bc._is_missing_event_fk(_integrity_error(constraint=None)) is True


@pytest.mark.parametrize("exc, why", [
    (_integrity_error(pgcode="23505", constraint="broadcast_sessions_one_open_per_event"),
     "a unique-constraint regression is a real defect"),
    (_integrity_error(constraint="analytics_snapshots_org_id_fkey", table="analytics_snapshots"),
     "a DIFFERENT foreign key on the same table"),
    (_integrity_error(constraint="live_messages_event_id_fkey", table="live_messages"),
     "the same kind of violation on another table"),
    (IntegrityError("INSERT ...", {}, Exception("no pgcode at all")),
     "a driver error with no SQLSTATE must never be guessed at"),
])
def test_unexpected_integrity_errors_are_not_swallowed(exc, why):
    assert bc._is_missing_event_fk(exc) is False, why


# ── _sample_once orchestration, against stubbed seams ─────────────────────────

class _FakeDB:
    """Just enough Session surface for the real write()/work() closures."""

    def __init__(self, existing_events, sessions=None):
        self.existing = {str(e) for e in existing_events}
        self.sessions = sessions or {}
        self.added = []

    def get(self, model, pk):
        if model is Event:
            return object() if str(pk) in self.existing else None
        if model is BroadcastSession:
            return self.sessions.get(str(pk))
        return None

    def add(self, row):
        self.added.append(row)


class _Harness:
    """Drives the REAL bc._sample_once with mod.tx and bus replaced.

    `existing_events` is the authoritative "does the events row exist" answer, and it is
    consulted separately for the initial read and for the write transaction — which is how
    "deleted between lookup and insert" is scripted.
    """

    def __init__(self, monkeypatch, sessions, existing_events, write_raises=None,
                 existing_at_write=None):
        self.sessions = sessions
        self.existing_events = existing_events
        self.existing_at_write = (existing_events if existing_at_write is None
                                  else existing_at_write)
        self.write_raises = write_raises
        self.snapshots = []
        self.retired = []
        self.presence_cleared = []
        self.state_writes = []

        async def fake_tx(fn):
            name = getattr(fn, "__name__", "")
            if name == "_open_sessions":
                return list(self.sessions)
            if name == "write":
                if self.write_raises is not None:
                    raise self.write_raises
                db = _FakeDB(self.existing_at_write)
                out = fn(db)
                self.snapshots.extend(db.added)
                return out
            raise AssertionError(f"unexpected tx callable in this test: {name!r}")

        async def fake_retire(session_id, event_id, reason):
            self.retired.append((session_id, event_id, reason))
            return True

        monkeypatch.setattr(bc.mod, "tx", fake_tx)
        monkeypatch.setattr(bc, "_retire_stale_session", fake_retire)
        monkeypatch.setattr(bc, "_counts", lambda db, e, o: {
            "messages": 1, "questions": 2, "reactions": 3, "poll_votes": 0, "polls": 0})
        monkeypatch.setattr(bc.bus, "presence_all", self._presence_all)
        monkeypatch.setattr(bc.bus, "state_set", self._state_set)
        monkeypatch.setattr(bc.bus, "presence_clear", self._presence_clear)
        monkeypatch.setattr(bc, "media_publishing_get", self._media)
        monkeypatch.setattr(bc, "mark_degraded", self._noop3)
        monkeypatch.setattr(bc, "mark_recovered", self._noop2)

    async def _presence_all(self, event_id):
        return [{"identity": "v1", "role": "viewer", "joined_at": 0.0}]

    async def _state_set(self, event_id, patch):
        self.state_writes.append(str(event_id))
        return {}

    async def _presence_clear(self, event_id):
        self.presence_cleared.append(str(event_id))

    async def _media(self, event_id):
        return True

    async def _noop3(self, *a, **k):
        return False

    async def _noop2(self, *a, **k):
        return False

    def run(self):
        return asyncio.run(bc._sample_once())


def _session(event_id, *, exists=True, deleted=False, status="live", ev_status="live"):
    return {"event_id": str(event_id), "org_id": str(uuid.uuid4()), "id": str(uuid.uuid4()),
            "status": status, "peak": 0, "started_at": None,
            "event_exists": exists, "event_status": ev_status, "event_deleted": deleted}


def test_a_valid_event_is_sampled_and_the_snapshot_is_written(monkeypatch):
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], existing_events=[ev])
    out = h.run()
    assert len(h.snapshots) == 1
    assert str(h.snapshots[0].event_id) == str(ev)
    assert h.retired == []
    assert [e for e, _ in out] == [str(ev)]


def test_a_session_whose_event_row_is_gone_writes_no_snapshot(monkeypatch):
    """The reported crash condition. No snapshot, no FK error, and the session is retired so
    it stops being a tick source."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev, exists=False)], existing_events=[])
    out = h.run()
    assert h.snapshots == []
    assert out == []
    assert [r[2] for r in h.retired] == ["event_row_missing"]


def test_a_soft_deleted_event_is_retired_not_sampled_forever(monkeypatch):
    """THE LEAK: 6 of production's 11 open sessions belonged to soft-deleted events, the
    oldest 29 days old. The events row still exists, so there is no FK error — the sessions
    were simply sampled every 15s indefinitely."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev, deleted=True)], existing_events=[ev])
    h.run()
    assert h.snapshots == []
    assert [r[2] for r in h.retired] == ["event_soft_deleted"]


@pytest.mark.parametrize("ev_status", ["ended", "cancelled", "archived"])
def test_a_terminal_event_status_retires_its_leaked_session(monkeypatch, ev_status):
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev, ev_status=ev_status)], existing_events=[ev])
    h.run()
    assert h.snapshots == []
    assert h.retired[0][2] == f"event_status_{ev_status}"


def test_a_degraded_event_is_still_sampled(monkeypatch):
    """Guard against over-reach: "degraded" means live-but-unhealthy (mark_degraded), which
    is exactly when analytics matter most. It must NOT be treated as terminal."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev, ev_status="degraded")], existing_events=[ev])
    h.run()
    assert len(h.snapshots) == 1
    assert h.retired == []


def test_one_missing_event_does_not_stop_the_other_sessions(monkeypatch):
    """A valid / missing / valid ordering: both valid events must still be sampled in the
    SAME tick, and both must still get their analytics.tick frame."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    h = _Harness(
        monkeypatch,
        [_session(a), _session(b, exists=False), _session(c)],
        existing_events=[a, c],
    )
    out = h.run()
    assert sorted(str(s.event_id) for s in h.snapshots) == sorted([str(a), str(c)])
    assert [r[2] for r in h.retired] == ["event_row_missing"]
    assert sorted(e for e, _ in out) == sorted([str(a), str(c)])


def test_event_deleted_between_the_lookup_and_the_insert(monkeypatch):
    """THE RACE. The initial read saw the event; the write transaction does not. write()
    re-checks in its own transaction and returns the sentinel, so nothing is inserted."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], existing_events=[ev], existing_at_write=[])
    out = h.run()
    assert h.snapshots == []
    assert out == []
    assert [r[2] for r in h.retired] == ["event_deleted_before_insert"]


def test_the_fk_violation_race_is_caught_and_the_tick_continues(monkeypatch):
    """The residual window: deleted between write()'s own db.get and the COMMIT, so only
    PostgreSQL can catch it. The specific ForeignKeyViolation is absorbed and the session
    retired; no traceback, no crash."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], existing_events=[ev],
                 write_raises=_integrity_error())
    out = h.run()
    assert h.snapshots == []
    assert out == []
    assert [r[2] for r in h.retired] == ["event_deleted_during_insert"]


def test_an_unrelated_integrity_error_still_propagates(monkeypatch):
    """Explicitly NOT swallowed: a unique-constraint violation is a real defect and must
    reach run_sampler's ERROR log rather than be filed as a stale event."""
    ev = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ev)], existing_events=[ev],
                 write_raises=_integrity_error(
                     pgcode="23505", constraint="broadcast_sessions_one_open_per_event"))
    with pytest.raises(IntegrityError):
        h.run()
    assert h.retired == []


def test_the_next_iteration_still_works_after_a_stale_event(monkeypatch):
    """Requirement 7: the sampler is not left in a poisoned state. The stale session is gone
    from the second read (it was retired), and the valid event samples normally."""
    stale, good = uuid.uuid4(), uuid.uuid4()
    h = _Harness(monkeypatch, [_session(stale, exists=False), _session(good)],
                 existing_events=[good])
    h.run()
    assert len(h.snapshots) == 1

    h.sessions = [_session(good)]          # retired session no longer returned
    h.snapshots.clear()
    h.retired.clear()
    h.run()
    assert len(h.snapshots) == 1
    assert h.retired == []


def test_run_sampler_survives_an_unexpected_error_and_keeps_ticking(monkeypatch, caplog):
    """One bad tick must not kill the loop, and it must be logged at ERROR with a traceback
    (this is the path an unexpected DB failure takes)."""
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT ...", {}, _PgError("23505", "something_else"))
        raise asyncio.CancelledError

    async def no_sleep(_):
        return None

    monkeypatch.setattr(bc, "_sample_once", boom)
    monkeypatch.setattr(bc.asyncio, "sleep", no_sleep)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bc.run_sampler(interval=0))
    assert calls["n"] == 2, "the loop did not continue past the failing tick"
    assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records), \
        "an unexpected sampler failure must log ERROR with a traceback"


# ── the orphan log line ───────────────────────────────────────────────────────

def test_the_orphan_warning_is_structured_and_carries_no_traceback(caplog):
    """Structured for log-based alerting, WARNING (not ERROR — it is a known stale-state
    condition), and no stack: this used to be a full IntegrityError traceback every 15s."""
    ev, sess = str(uuid.uuid4()), str(uuid.uuid4())

    async def fake_tx(fn):
        return False          # nothing to close

    async def fake_clear(event_id):
        return None

    orig_tx, orig_clear = bc.mod.tx, bc.bus.presence_clear
    bc.mod.tx, bc.bus.presence_clear = fake_tx, fake_clear
    try:
        with caplog.at_level(logging.WARNING):
            asyncio.run(bc._retire_stale_session(sess, ev, "event_row_missing"))
    finally:
        bc.mod.tx, bc.bus.presence_clear = orig_tx, orig_clear

    hits = [r for r in caplog.records if "analytics_sampler_orphan_event" in r.getMessage()]
    assert hits, caplog.text
    rec = hits[0]
    assert rec.levelno == logging.WARNING
    assert rec.exc_info is None, "a known stale event must not emit a traceback"
    msg = rec.getMessage()
    assert f"event_id={ev}" in msg
    assert "action=skip_snapshot" in msg
    assert "reason=event_row_missing" in msg


def test_terminal_statuses_are_real_event_statuses():
    """A typo here would silently disable the leak fix."""
    for status in bc._TERMINAL_EVENT_STATUSES:
        assert status in EVENT_STATUSES, status
    # "degraded"/"live" must never be terminal — that would retire healthy broadcasts.
    assert "live" not in bc._TERMINAL_EVENT_STATUSES
    assert "degraded" not in bc._TERMINAL_EVENT_STATUSES


# ── moderation.tx transaction safety ──────────────────────────────────────────

def test_tx_rolls_back_and_closes_on_failure(monkeypatch):
    """Requirement 6. A failed analytics insert must not leave a Session in a failed
    transaction state — and because tx() opens a SHORT-LIVED session per call, a failure
    cannot leak into the next one either."""
    events = []

    class FakeSession:
        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")

    monkeypatch.setattr(m, "SessionLocal", lambda: FakeSession())

    def boom(db):
        raise IntegrityError("INSERT ...", {}, _PgError("23503", "analytics_snapshots_event_id_fkey"))

    with pytest.raises(IntegrityError):
        m._run(boom)
    assert events == ["rollback", "close"], events

    events.clear()
    assert m._run(lambda db: "ok") == "ok"
    assert events == ["commit", "close"], events


# ── database invariants (these need the real schema) ──────────────────────────

def test_the_foreign_key_is_still_declared_on_the_model():
    """Requirement 9: the fix must not have removed the constraint that exposed the bug."""
    fks = sa_inspect(AnalyticsSnapshot).local_table.c.event_id.foreign_keys
    assert fks, "analytics_snapshots.event_id lost its ForeignKey"
    assert any(fk.target_fullname == "events.id" for fk in fks)


def test_the_foreign_key_is_still_enabled_and_validated_in_the_database():
    """The constraint named in the production traceback must still exist, still be validated,
    and still be NO ACTION — nothing here weakened it to make the error go away."""
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT convalidated, confdeltype
            FROM pg_constraint
            WHERE conname = 'analytics_snapshots_event_id_fkey' AND contype = 'f'
        """)).first()
        assert row is not None, "analytics_snapshots_event_id_fkey is missing from the database"
        assert row[0] is True, "the FK was left NOT VALID"
        assert row[1] == "a", "the FK delete rule was changed away from NO ACTION"
    finally:
        db.close()


def test_sampling_an_orphan_invents_no_event_and_writes_no_snapshot(monkeypatch):
    """Requirement 10, against the real database: after a tick that hit a missing event, no
    placeholder parent exists and no snapshot was written FOR THAT event_id.

    Scoped per event_id rather than to global row counts on purpose. A deployed sampler runs
    against this same database (that is how the reported production error reached a
    developer's event in the first place), so it can legitimately insert a snapshot for one
    of ITS live events between two counts here — an absolute count is a test of who else is
    running, not of this code.
    """
    ghost = uuid.uuid4()
    h = _Harness(monkeypatch, [_session(ghost, exists=False)], existing_events=[])
    h.run()
    assert h.snapshots == []

    db = SessionLocal()
    try:
        assert db.get(Event, ghost) is None, "a placeholder Event was created"
        wrote = db.execute(
            text("SELECT count(*) FROM analytics_snapshots WHERE event_id = :e"),
            {"e": str(ghost)},
        ).scalar()
        assert wrote == 0, "a snapshot was written for an event that does not exist"
    finally:
        db.close()


def test_no_orphaned_snapshots_exist_in_the_database():
    """The invariant the FK is there to guarantee, checked end-to-end. If this ever fails the
    constraint was dropped or bypassed."""
    db = SessionLocal()
    try:
        orphans = db.execute(text("""
            SELECT count(*) FROM analytics_snapshots a
            LEFT JOIN events e ON e.id = a.event_id WHERE e.id IS NULL
        """)).scalar()
        assert orphans == 0, f"{orphans} analytics_snapshots rows have no parent event"
    finally:
        db.close()
