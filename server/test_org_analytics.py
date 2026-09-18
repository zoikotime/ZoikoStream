"""Organization analytics aggregation: the arithmetic, not the plumbing.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
AnalyticsSnapshot's interaction columns are CUMULATIVE — broadcast._counts recomputes the
running event total on every 15-second tick, so three ticks after one message stored
messages=1 three times. analytics() summed them, turning one message into three. A
ten-minute event has ~40 ticks, so engagement inflated ~40x and pegged at the formula's cap
of 100. `viewers` on the same rows is INSTANTANEOUS, so watch-hours was and remains a
correct integral — one column per-tick, three cumulative, all four treated alike.

These test the pure helpers and the aggregation shape. The full service needs a database;
see test_org_analytics_db-style suites for that.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.org import _bucket_label, _bucket_timeline

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


# ── the cumulative-vs-instantaneous distinction ────────────────────────────────────────

class Snap:
    """Only the four columns analytics() reads."""

    def __init__(self, viewers=0, messages=0, questions=0, reactions=0):
        self.viewers = viewers
        self.messages = messages
        self.questions = questions
        self.reactions = reactions


def totals(snaps):
    """Exactly what analytics() now computes per event."""
    return {
        "messages": max((s.messages for s in snaps), default=0),
        "questions": max((s.questions for s in snaps), default=0),
        "reactions": max((s.reactions for s in snaps), default=0),
        "watch_hours": (sum(s.viewers for s in snaps) * (15 / 3600)) if snaps else None,
    }


def test_one_message_across_three_ticks_counts_once():
    """The reported defect, exactly: [1, 1, 1] is ONE message seen three times."""
    snaps = [Snap(messages=1), Snap(messages=1), Snap(messages=1)]
    assert totals(snaps)["messages"] == 1
    # The old behaviour, for contrast — this is what inflated the score.
    assert sum(s.messages for s in snaps) == 3


def test_a_growing_counter_reports_its_final_value():
    snaps = [Snap(messages=1), Snap(messages=1), Snap(messages=4), Snap(messages=7)]
    assert totals(snaps)["messages"] == 7


def test_questions_and_reactions_behave_the_same_way():
    snaps = [Snap(questions=2, reactions=5), Snap(questions=2, reactions=9)]
    out = totals(snaps)
    assert out["questions"] == 2
    assert out["reactions"] == 9


def test_engagement_no_longer_scales_with_event_length():
    """Same interactions, ten times the ticks: the totals must not move."""
    short = [Snap(messages=3, questions=1, reactions=2)] * 4
    long_ = [Snap(messages=3, questions=1, reactions=2)] * 40
    assert totals(short)["messages"] == totals(long_)["messages"] == 3
    assert totals(short)["reactions"] == totals(long_)["reactions"] == 2


def test_separate_events_are_summed_after_each_is_maxed():
    """max() is per event; across events the per-event totals add up. Using max across
    unrelated events would report 3 where the answer is 5."""
    event_a = totals([Snap(messages=1), Snap(messages=2)])["messages"]      # 2
    event_b = totals([Snap(messages=3), Snap(messages=3)])["messages"]      # 3
    assert event_a + event_b == 5


# ── watch hours: the one column that really is per-tick ────────────────────────────────

def test_watch_hours_stays_an_integral_of_the_viewer_curve():
    # Two viewers for four ticks = 8 viewer-samples x 15s.
    snaps = [Snap(viewers=2)] * 4
    assert totals(snaps)["watch_hours"] == pytest.approx(8 * 15 / 3600)


def test_watch_hours_is_none_when_nothing_was_sampled():
    """Not 0.0: nothing was measured, and a figure would be a claim."""
    assert totals([])["watch_hours"] is None


def test_watch_hours_is_zero_when_sampled_with_nobody_watching():
    """Measured, and the measurement is zero — a different fact from the case above."""
    assert totals([Snap(viewers=0)] * 3)["watch_hours"] == 0


# ── timezone bucketing ─────────────────────────────────────────────────────────────────

def test_a_late_evening_utc_timestamp_buckets_on_the_local_next_day():
    """The exact case from the brief: 20:45 UTC is 02:15 the NEXT day in IST, and the chart
    must agree with the Events list rather than with the storage zone."""
    moment = datetime(2026, 9, 16, 20, 45, tzinfo=UTC)
    assert _bucket_label(moment, "30d", UTC) == "Sep 16"
    assert _bucket_label(moment, "30d", IST) == "Sep 17"


def test_a_midday_timestamp_does_not_cross_the_boundary():
    moment = datetime(2026, 9, 16, 6, 41, tzinfo=UTC)     # 12:11 IST
    assert _bucket_label(moment, "30d", IST) == "Sep 16"


def test_twelve_month_range_buckets_by_month():
    moment = datetime(2026, 9, 16, 20, 45, tzinfo=UTC)
    assert _bucket_label(moment, "12m", IST) == "Sep 2026"


# ── zero-filled timeline ───────────────────────────────────────────────────────────────

def test_every_day_in_the_window_gets_a_bucket():
    """Only days containing an event used to appear, so two events three days apart drew as
    two isolated bars with nothing between them."""
    since = datetime(2026, 9, 10, tzinfo=UTC)
    until = datetime(2026, 9, 16, tzinfo=UTC)
    labels = _bucket_timeline(since, until, "7d", UTC)

    assert labels[0] == "Sep 10" and labels[-1] == "Sep 16"
    assert len(labels) == 7
    assert labels == sorted(set(labels), key=labels.index)   # ordered, no duplicates


def test_the_timeline_is_built_in_the_requested_zone():
    since = datetime(2026, 9, 10, 20, 0, tzinfo=UTC)   # Sep 11 in IST
    until = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    assert _bucket_timeline(since, until, "7d", IST)[0] == "Sep 11"


def test_month_timeline_walks_months_not_days():
    since = datetime(2025, 11, 5, tzinfo=UTC)
    until = datetime(2026, 2, 5, tzinfo=UTC)
    labels = _bucket_timeline(since, until, "12m", UTC)

    assert labels == ["Nov 2025", "Dec 2025", "Jan 2026", "Feb 2026"]


def test_a_single_day_window_yields_one_bucket():
    day = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
    assert _bucket_timeline(day, day, "7d", UTC) == ["Sep 16"]


# ── the service itself, end to end ─────────────────────────────────────────────────────
#
# The helpers above are pure and were green while /organization/analytics returned 500:
# `zone, _zone_name = event_zone(...)` was never written to the file, so the payload builder
# referenced an undefined name. Nothing exercised analytics() as a whole, so a NameError on
# the happy path shipped. These call it for real — an organization WITH telemetry and one
# WITHOUT, across every supported range.

import uuid

from app.db import SessionLocal
from app.models import AnalyticsSnapshot, BroadcastSession, Event, Organization, User
from app.security import hash_password
from app.services import org as org_svc

RANGES = ["7d", "30d", "90d", "12m"]


@pytest.fixture
def world():
    """One org with an event, a session and snapshots; one org with nothing."""
    db = SessionLocal()
    made = {"orgs": [], "events": [], "users": []}
    try:
        rich = Organization(name=f"AnalyticsRich {uuid.uuid4().hex[:6]}", status="active")
        bare = Organization(name=f"AnalyticsBare {uuid.uuid4().hex[:6]}", status="active")
        db.add_all([rich, bare])
        db.flush()
        made["orgs"] = [rich.id, bare.id]

        # Event.created_by is NOT NULL.
        creator = User(org_id=rich.id, full_name="Analytics Fixture", role="org_admin",
                       is_active=True, email=f"an-{uuid.uuid4().hex[:10]}@example.com",
                       username=f"an{uuid.uuid4().hex[:10]}",
                       password_hash=hash_password("x"), email_verified=True)
        db.add(creator)
        db.flush()
        made["users"] = [creator.id]

        ev = Event(org_id=rich.id, created_by=creator.id, title="Measured event",
                   status="ended", visibility="public",
                   start_time=datetime.now(UTC) - timedelta(days=2))
        db.add(ev)
        db.flush()
        made["events"] = [ev.id]

        db.add(BroadcastSession(event_id=ev.id, org_id=rich.id, status="ended", peak_viewers=4))
        # Cumulative counters, exactly as the sampler writes them: one message seen 3 times.
        for _ in range(3):
            db.add(AnalyticsSnapshot(event_id=ev.id, org_id=rich.id, viewers=2,
                                     participants=2, on_stage=1, messages=1,
                                     questions=0, reactions=0, hands=0))
        db.commit()
        yield {"rich": rich, "bare": bare, "event_id": ev.id}
    finally:
        try:
            for eid in made["events"]:
                db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.event_id == eid).delete()
                db.query(BroadcastSession).filter(BroadcastSession.event_id == eid).delete()
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


@pytest.mark.parametrize("range_key", RANGES)
def test_every_range_returns_a_payload_for_an_org_with_telemetry(world, range_key):
    """The regression: this raised NameError for every range, which FastAPI served as 500."""
    db = SessionLocal()
    try:
        out = org_svc.analytics(db, world["rich"], range_key=range_key)
    finally:
        db.close()

    assert out["range"] == range_key
    assert out["timezone"]                       # the zone actually resolved
    assert out["trends"]["viewership"]           # continuous, zero-filled timeline
    assert len(out["trends"]["viewership"]) == len(out["trends"]["watch_time"])


@pytest.mark.parametrize("range_key", RANGES)
def test_every_range_returns_a_payload_for_an_org_with_no_telemetry(world, range_key):
    db = SessionLocal()
    try:
        out = org_svc.analytics(db, world["bare"], range_key=range_key)
    finally:
        db.close()

    s = out["summary"]
    # Nothing measured: honest nulls, not zeros.
    assert s["watch_hours"] is None
    assert s["engagement"] is None
    # Counts that ARE knowable stay numeric.
    assert s["peak_viewers_summed"] == 0
    assert s["peak"] == 0


def test_cumulative_snapshots_are_not_summed_end_to_end(world):
    """Three ticks each carrying the cumulative messages=1 is ONE message, so the score is
    computed from 1 — not 3. Peak 4 => 100*1/(5*4) = 5."""
    db = SessionLocal()
    try:
        out = org_svc.analytics(db, world["rich"], range_key="30d")
    finally:
        db.close()

    assert out["summary"]["engagement"] == 5
    # Watch hours: 3 ticks x 2 viewers x 15s.
    assert out["summary"]["watch_hours"] == pytest.approx(6 * 15 / 3600, rel=1e-3)
    assert out["summary"]["peak"] == 4
    assert out["summary"]["peak_viewers_summed"] == 4


def test_one_organization_never_sees_another(world):
    """Scoping is unchanged by this work, and stays asserted."""
    db = SessionLocal()
    try:
        bare = org_svc.analytics(db, world["bare"], range_key="30d")
    finally:
        db.close()
    assert bare["reports"] == []
    assert bare["summary"]["peak_viewers_summed"] == 0
