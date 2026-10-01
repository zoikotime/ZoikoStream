"""Organization analytics over PERSISTED rows — a completed session, read back from a database.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
An event can be broadcast more than once: a rehearsal then the real run, or a stream that
dropped and was restarted. Each `_end` calls `bus.session_finalize`, which recomputes over
the event's whole viewer-session ledger and writes the result onto the session row that just
ended. That ledger is never cleared between runs — `bus.presence_clear` drops presence,
bans, state and reactions, but not the session store — so every successive summary is a
CUMULATIVE total for the event rather than a record of its own run.

`org.analytics` and `report._audience` both iterated the event's sessions and `break`-ed on
the first row carrying a summary, from a query with no ORDER BY. So a twice-broadcast event
reported whichever total the database happened to hand back first, which is usually the
earlier and smaller one — and was not even stable between two identical requests. The event
below finalizes at 600s and then at 900s; the dashboard showed 600.

`pick_event_summary` now takes the largest, which under cumulative semantics IS the event
total. It must not sum them: 600 + 900 would bill the first run twice.

── WHY SQLITE ──────────────────────────────────────────────────────────────────────────
These assertions are about aggregation over rows, so they need real rows and real SQL — the
existing analytics suites test the pure helpers only, which is how a `break` over a result
set went unnoticed. The models are portable apart from PostgreSQL's UUID, which is given a
SQLite spelling below. Nothing here touches the configured DATABASE_URL: this builds its own
engine against a temporary file.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(PGUUID, "sqlite")
def _pg_uuid_on_sqlite(type_, compiler, **kw):
    return "CHAR(36)"


from app.db import Base
import app.models  # noqa: F401  — registers every table on Base.metadata
from app.models.event import Event
from app.models.live import AnalyticsSnapshot, BroadcastSession, LiveMessage, LiveQuestion
from app.models.organization import Organization
from app.services import org as org_svc
from app.services.watch_time import pick_event_summary

UTC = timezone.utc


# ── fixtures ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real database holding the real schema, plus a bus that knows nothing.

    The bus is silenced deliberately: a COMPLETED session is one whose numbers have to
    survive in Postgres on their own. Letting a warm in-process summary answer would test
    the opposite of what these assertions claim.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'analytics.sqlite'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    from app.services import bus
    monkeypatch.setattr(bus, "summary_get_sync", lambda _e: None)
    monkeypatch.setattr(bus, "session_get_all_sync", lambda _e: [])
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def org(db):
    row = Organization(id=uuid.uuid4(), name="Acme", slug="acme")
    db.add(row)
    db.commit()
    return row


def _now():
    return datetime.now(UTC)


def make_event(db, org, title, start, status="ended", duration=timedelta(minutes=30)):
    ev = Event(id=uuid.uuid4(), org_id=org.id, created_by=uuid.uuid4(), title=title,
               start_time=start, end_time=start + duration, status=status)
    db.add(ev)
    return ev


def finalized(total_seconds, peak):
    """The shape bus.session_finalize persists onto BroadcastSession.settings."""
    return {"measured": True, "total_watch_seconds": total_seconds,
            "average_watch_seconds": total_seconds // max(peak, 1),
            "watch_hours": round(total_seconds / 3600.0, 6),
            "viewer_count": peak, "peak_concurrent": peak}


def make_session(db, ev, started, peak=0, summary=None, duration=timedelta(minutes=30)):
    row = BroadcastSession(
        id=uuid.uuid4(), event_id=ev.id, org_id=ev.org_id, status="ended",
        started_at=started, ended_at=started + duration, peak_viewers=peak,
        ended_reason="host", settings=({"analytics_summary": summary} if summary else {}))
    db.add(row)
    return row


def make_snapshots(db, ev, count, viewers, started, messages=0, questions=0, reactions=0):
    """`count` sampler ticks, 15s apart — the cadence broadcast._sample_once writes at."""
    for i in range(count):
        db.add(AnalyticsSnapshot(
            id=uuid.uuid4(), event_id=ev.id, org_id=ev.org_id,
            viewers=viewers, participants=viewers + 1, on_stage=1,
            messages=messages, questions=questions, reactions=reactions, hands=0,
            created_at=started + timedelta(seconds=15 * i)))


def summary_for(db, org, range_key="30d"):
    return org_svc.analytics(db, org, range_key=range_key)["summary"]


# ── the reported scenario: a completed session must reach the cards ─────────────────────

def test_a_completed_session_fills_all_four_cards(db, org):
    """The whole bug report in one case. 40 ticks at 10 concurrent viewers over ten
    minutes, a session peak of 12, and real chat and Q&A rows."""
    start = _now() - timedelta(days=3)
    ev = make_event(db, org, "Product Launch", start)
    make_session(db, ev, start, peak=12)
    make_snapshots(db, ev, 40, 10, start, messages=5, questions=2, reactions=8)
    for i in range(5):
        db.add(LiveMessage(id=uuid.uuid4(), event_id=ev.id, org_id=org.id,
                           user_id=uuid.uuid4(), author_name="Viewer", text=f"hi {i}",
                           status="approved", reactions={"clap": 1}))
    for i in range(2):
        db.add(LiveQuestion(id=uuid.uuid4(), event_id=ev.id, org_id=org.id,
                            user_id=uuid.uuid4(), author_name="Viewer", text=f"q {i}",
                            status="approved"))
    db.commit()

    s = summary_for(db, org)
    # Peak concurrency is the session's own recorded peak, above the sampled 10.
    assert s["peak"] == 12
    assert s["peak_viewers_summed"] == 12
    # 40 ticks x 10 viewers x 15s = 6000 viewer-seconds.
    assert s["total_watch_seconds"] == 6000
    assert s["watch_hours"] == pytest.approx(6000 / 3600, abs=1e-3)
    # Present, measured, and not the em dash the page was showing.
    assert s["engagement"] is not None


# ── multiple sessions for one event ─────────────────────────────────────────────────────

def test_a_twice_broadcast_event_reports_its_complete_total(db, org):
    """The regression. Run 1 finalizes at 600s, run 2 cumulatively at 900s. Taking the
    first row found reported 600."""
    start = _now() - timedelta(days=5)
    ev = make_event(db, org, "Two-run webinar", start, duration=timedelta(hours=3))
    make_session(db, ev, start, peak=10, summary=finalized(600, 10))
    make_session(db, ev, start + timedelta(hours=2), peak=20, summary=finalized(900, 20))
    db.commit()

    s = summary_for(db, org)
    assert s["total_watch_seconds"] == 900
    # And emphatically NOT the sum: the second summary already contains the first.
    assert s["total_watch_seconds"] != 1500


def test_the_answer_does_not_depend_on_which_row_comes_back_first(db, org):
    """The old code's result rode on unordered SQL row order. Inserting the larger summary
    first must not change the outcome."""
    start = _now() - timedelta(days=5)
    ev = make_event(db, org, "Reversed insert order", start, duration=timedelta(hours=3))
    make_session(db, ev, start + timedelta(hours=2), peak=20, summary=finalized(900, 20))
    make_session(db, ev, start, peak=10, summary=finalized(600, 10))
    db.commit()

    assert summary_for(db, org)["total_watch_seconds"] == 900


def test_peak_concurrency_is_the_highest_run_not_the_sum_of_runs(db, org):
    """Two runs of 10 and 20 never had 30 people watching at once."""
    start = _now() - timedelta(days=5)
    ev = make_event(db, org, "Two-run webinar", start, duration=timedelta(hours=3))
    make_session(db, ev, start, peak=10, summary=finalized(600, 10))
    make_session(db, ev, start + timedelta(hours=2), peak=20, summary=finalized(900, 20))
    db.commit()

    assert summary_for(db, org)["peak"] == 20


def test_peak_viewers_summed_adds_across_EVENTS(db, org):
    """The card is "sum of each event's peak", so two separate events do add — the
    distinction the rename from "Total Viewers" was making."""
    start = _now() - timedelta(days=4)
    for title, peak in (("First", 10), ("Second", 15)):
        ev = make_event(db, org, title, start)
        make_session(db, ev, start, peak=peak)
        make_snapshots(db, ev, 4, peak, start)
    db.commit()

    s = summary_for(db, org)
    assert s["peak_viewers_summed"] == 25
    assert s["peak"] == 15          # highest single event, not the total


# ── measured zero vs never measured ─────────────────────────────────────────────────────

def test_an_event_nobody_watched_reports_a_measured_zero(db, org):
    """Sampled, and the room was genuinely empty. The card must read 0, not an em dash."""
    start = _now() - timedelta(days=2)
    ev = make_event(db, org, "Empty room", start)
    make_session(db, ev, start, peak=0)
    make_snapshots(db, ev, 20, 0, start)
    db.commit()

    s = summary_for(db, org)
    assert s["total_watch_seconds"] == 0
    assert s["watch_hours"] == 0
    assert s["peak"] == 0


def test_an_event_that_was_never_sampled_reports_unavailable_not_zero(db, org):
    """No snapshots and no finalized summary: nothing was measured, so watch time is None
    and the page renders the em dash. Claiming 0 here would assert we watched and saw
    nobody, which the data cannot support."""
    start = _now() - timedelta(days=2)
    ev = make_event(db, org, "Never sampled", start)
    make_session(db, ev, start, peak=7)
    db.commit()

    s = summary_for(db, org)
    assert s["watch_hours"] is None
    assert s["total_watch_seconds"] is None
    # The peak still survives on the session row, so it is reported.
    assert s["peak"] == 7


def test_a_finalized_summary_is_used_when_no_snapshots_survive(db, org):
    """The finalization path standing alone — the sampler never wrote, but `_end` did."""
    start = _now() - timedelta(days=2)
    ev = make_event(db, org, "Finalized only", start)
    make_session(db, ev, start, peak=9, summary=finalized(5400, 9))
    db.commit()

    s = summary_for(db, org)
    assert s["total_watch_seconds"] == 5400
    assert s["watch_hours"] == pytest.approx(1.5, abs=1e-4)
    assert s["peak"] == 9


# ── date range ──────────────────────────────────────────────────────────────────────────

def test_an_event_inside_the_window_is_counted(db, org):
    start = _now() - timedelta(days=3)
    ev = make_event(db, org, "Recent", start)
    make_session(db, ev, start, peak=11)
    make_snapshots(db, ev, 8, 11, start)
    db.commit()

    assert summary_for(db, org, "7d")["peak"] == 11


def test_an_event_outside_the_window_is_excluded(db, org):
    """Twenty days old: inside 30d, outside 7d. The same rows must answer differently."""
    start = _now() - timedelta(days=20)
    ev = make_event(db, org, "Older", start)
    make_session(db, ev, start, peak=11)
    make_snapshots(db, ev, 8, 11, start)
    db.commit()

    assert summary_for(db, org, "7d")["peak"] == 0
    assert summary_for(db, org, "7d")["watch_hours"] is None
    assert summary_for(db, org, "30d")["peak"] == 11


def test_each_range_widens_to_include_the_older_event(db, org):
    start = _now() - timedelta(days=45)
    ev = make_event(db, org, "Six weeks ago", start)
    make_session(db, ev, start, peak=6)
    make_snapshots(db, ev, 8, 6, start)
    db.commit()

    assert summary_for(db, org, "30d")["peak"] == 0
    assert summary_for(db, org, "90d")["peak"] == 6
    assert summary_for(db, org, "12m")["peak"] == 6


def test_an_old_event_re_broadcast_recently_comes_back_into_range(db, org):
    """The event's own start_time is outside the window but a session ran inside it —
    analytics() reaches those through the BroadcastSession subquery."""
    old = _now() - timedelta(days=60)
    recent = _now() - timedelta(days=2)
    ev = make_event(db, org, "Old event, new run", old)
    make_session(db, ev, recent, peak=14)
    make_snapshots(db, ev, 8, 14, recent)
    db.commit()

    assert summary_for(db, org, "7d")["peak"] == 14


# ── tenant isolation ────────────────────────────────────────────────────────────────────

def test_one_organization_never_sees_another_organizations_audience(db, org):
    rival = Organization(id=uuid.uuid4(), name="Rival", slug="rival")
    db.add(rival)
    start = _now() - timedelta(days=2)

    mine = make_event(db, org, "Mine", start)
    make_session(db, mine, start, peak=5)
    make_snapshots(db, mine, 8, 5, start)

    theirs = make_event(db, rival, "Theirs", start)
    make_session(db, theirs, start, peak=999)
    make_snapshots(db, theirs, 800, 999, start)
    db.commit()

    s = summary_for(db, org)
    assert s["peak"] == 5
    assert s["peak_viewers_summed"] == 5
    reports = org_svc.analytics(db, org, range_key="30d")["reports"]
    assert [r["event"] for r in reports] == ["Mine"]

    # ...and symmetrically, so the test cannot pass by returning nothing at all.
    assert summary_for(db, rival)["peak"] == 999


def test_a_deleted_event_is_left_out(db, org):
    start = _now() - timedelta(days=2)
    ev = make_event(db, org, "Deleted", start)
    ev.deleted_at = _now()
    make_session(db, ev, start, peak=33)
    make_snapshots(db, ev, 8, 33, start)
    db.commit()

    assert summary_for(db, org)["peak"] == 0


# ── the chooser itself ──────────────────────────────────────────────────────────────────

class _Sess:
    def __init__(self, settings):
        self.settings = settings


def test_pick_event_summary_returns_none_when_there_is_nothing_to_pick():
    assert pick_event_summary([], None) is None
    assert pick_event_summary(None, None) is None


def test_pick_event_summary_ignores_unmeasured_summaries():
    """An unmeasured summary must not shadow the snapshot fallback, which has real numbers."""
    unmeasured = {"measured": False, "total_watch_seconds": None}
    assert pick_event_summary([_Sess({"analytics_summary": unmeasured})], None) is None


def test_pick_event_summary_takes_the_largest_total():
    small, large = finalized(600, 10), finalized(900, 20)
    rows = [_Sess({"analytics_summary": small}), _Sess({"analytics_summary": large})]
    assert pick_event_summary(rows, None) is large
    assert pick_event_summary(list(reversed(rows)), None) is large


def test_pick_event_summary_tolerates_rows_without_settings():
    large = finalized(900, 20)
    rows = [_Sess(None), _Sess({}), _Sess({"analytics_summary": large})]
    assert pick_event_summary(rows, None) is large


def test_pick_event_summary_lets_a_larger_bus_total_win():
    """A just-ended event whose summary is still only in the bus."""
    persisted, warm = finalized(600, 10), finalized(900, 20)
    rows = [_Sess({"analytics_summary": persisted})]
    assert pick_event_summary(rows, warm) is warm


def test_pick_event_summary_keeps_a_larger_persisted_total_over_a_stale_bus():
    persisted, stale = finalized(900, 20), finalized(120, 3)
    rows = [_Sess({"analytics_summary": persisted})]
    assert pick_event_summary(rows, stale) is persisted


def test_pick_event_summary_falls_back_to_watch_hours_when_seconds_are_absent():
    """An older build stored hours only."""
    hours_only = {"measured": True, "watch_hours": 2.0, "peak_concurrent": 4}
    seconds = finalized(600, 10)
    rows = [_Sess({"analytics_summary": seconds}), _Sess({"analytics_summary": hours_only})]
    assert pick_event_summary(rows, None) is hours_only
