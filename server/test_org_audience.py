"""Organization audience attendance: the analytics contract it consumes, and null telemetry.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
analytics()'s summary key was renamed `viewers` -> `peak_viewers_summed`, because the figure
is the SUM OF EACH EVENT'S PEAK concurrency and cannot support a "viewers" claim.
audience_attendance() is a second consumer of analytics() and was missed in that rename, so
`base["summary"]["viewers"]` raised KeyError on every request — every organization, every
role, every range. /organization/audience was HTTP 500 platform-wide and nothing caught it,
because the analytics tests only ever called analytics() directly.

Sitting immediately behind it was `watch_hours * 60`, where watch_hours is now legitimately
None for a window with no AnalyticsSnapshot rows. Fixing only the KeyError would have turned a
guaranteed 500 into an intermittent TypeError, so both are pinned here.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import SessionLocal
from app.models import AnalyticsSnapshot, BroadcastSession, Event, Organization, User
from app.security import hash_password
from app.services import org as org_svc

UTC = timezone.utc
RANGES = ["7d", "30d", "90d", "12m"]


def _org(db, label):
    o = Organization(name=f"{label} {uuid.uuid4().hex[:6]}", status="active")
    db.add(o)
    db.flush()
    return o


def _user(db, org):
    # Event.created_by is NOT NULL.
    u = User(org_id=org.id, full_name="Audience Fixture", role="org_admin", is_active=True,
             email=f"au-{uuid.uuid4().hex[:10]}@example.com",
             username=f"au{uuid.uuid4().hex[:10]}",
             password_hash=hash_password("x"), email_verified=True)
    db.add(u)
    db.flush()
    return u


def _event(db, org, creator, days_ago=2):
    ev = Event(org_id=org.id, created_by=creator.id, title="Measured event",
               status="ended", visibility="public",
               start_time=datetime.now(UTC) - timedelta(days=days_ago))
    db.add(ev)
    db.flush()
    return ev


@pytest.fixture
def world():
    """Three organizations: one with telemetry, one with none, one to prove scoping."""
    db = SessionLocal()
    made = {"orgs": [], "events": [], "users": []}
    try:
        rich, bare, other = _org(db, "AudRich"), _org(db, "AudBare"), _org(db, "AudOther")
        made["orgs"] = [rich.id, bare.id, other.id]

        creator = _user(db, rich)
        made["users"] = [creator.id]
        ev = _event(db, rich, creator)
        made["events"] = [ev.id]

        db.add(BroadcastSession(event_id=ev.id, org_id=rich.id, status="ended", peak_viewers=4))
        # Cumulative counters exactly as the sampler writes them.
        for _ in range(3):
            db.add(AnalyticsSnapshot(event_id=ev.id, org_id=rich.id, viewers=2, participants=2,
                                     on_stage=1, messages=1, questions=0, reactions=0, hands=0))
        db.commit()
        yield {"rich": rich, "bare": bare, "other": other, "event_id": ev.id}
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


def _call(org, range_key="30d"):
    db = SessionLocal()
    try:
        return org_svc.audience_attendance(db, org, range_key=range_key)
    finally:
        db.close()


def _with_watch_hours(monkeypatch, value):
    """Force analytics() to report a specific watch_hours at the seam audience reads it from.

    Done here rather than by deleting rows so the attendee count stays NON-ZERO — otherwise
    the `if attendees` guard masks the very arithmetic under test.
    """
    real = org_svc.analytics

    def patched(db, org, range_key="30d", **kw):
        out = real(db, org, range_key, **kw)
        out["summary"]["watch_hours"] = value
        return out

    monkeypatch.setattr(org_svc, "analytics", patched)


# ── the regression, stated as plainly as it can be ─────────────────────────────────────

def test_audience_does_not_raise_key_error_viewers(world):
    """THE test for this audit. Before the fix this raised KeyError: 'viewers' — not an
    assertion failure, an unhandled exception that FastAPI served as 500."""
    try:
        _call(world["rich"])
    except KeyError as e:
        pytest.fail(f"audience_attendance still reads a stale analytics key: KeyError({e})")


def test_it_reads_the_current_analytics_contract(world):
    """Pins the coupling itself: what audience consumes must be a key analytics EMITS.
    Renaming a summary key again without updating this consumer fails here."""
    db = SessionLocal()
    try:
        summary = org_svc.analytics(db, world["rich"], range_key="30d")["summary"]
    finally:
        db.close()

    assert "peak_viewers_summed" in summary
    # The misleading name must not return as a compatibility alias — that would re-create the
    # very claim ("viewers") the rename existed to retire.
    assert "viewers" not in summary


@pytest.mark.parametrize("range_key", RANGES)
def test_an_org_with_telemetry_returns_a_payload(world, range_key):
    out = _call(world["rich"], range_key)
    assert out["range"] == range_key
    assert isinstance(out["unique_attendees"], int)


@pytest.mark.parametrize("range_key", RANGES)
def test_an_org_without_telemetry_returns_a_payload(world, range_key):
    """No snapshots, no registrations — still a 200-shaped answer, not an exception."""
    out = _call(world["bare"], range_key)
    assert out["range"] == range_key
    assert out["unique_attendees"] == 0


# ── null watch hours ───────────────────────────────────────────────────────────────────

def test_null_watch_hours_never_reaches_the_arithmetic(world, monkeypatch):
    """The defect hiding behind the KeyError: `None * 60`. A real state — a private
    registration was claimed, but no AnalyticsSnapshot was ever written."""
    _with_watch_hours(monkeypatch, None)
    out = _call(world["rich"])

    assert out["unique_attendees"] > 0, "the guard would mask the bug if this were 0"
    assert out["avg_watch_minutes"] is None


def test_unmeasured_watch_time_stays_unavailable_not_zero(world, monkeypatch):
    """None must not be quietly coerced. 0 would claim people watched for no time at all;
    None says nobody measured. The client renders the first as "0 min", the second as "—"."""
    _with_watch_hours(monkeypatch, None)
    avg = _call(world["rich"])["avg_watch_minutes"]

    assert avg is None
    assert avg != 0 and avg is not False


def test_a_measured_zero_is_still_reported_as_zero(world, monkeypatch):
    """The other side of the same rule: sampled, and the answer really was zero."""
    _with_watch_hours(monkeypatch, 0.0)
    out = _call(world["rich"])

    assert out["unique_attendees"] > 0
    assert out["avg_watch_minutes"] == 0          # a measurement, so it gets a figure
    assert out["avg_watch_minutes"] is not None


# ── the estimate is labelled for what it is ────────────────────────────────────────────

def test_the_attendee_figure_is_flagged_as_an_estimate(world):
    """unique_attendees is claimed-registration emails PLUS summed per-event peak concurrency,
    so somebody at two events is counted twice. It is not a distinct-person count, and this
    flag is what the UI keys its "Estimated attendees" label and caveat off."""
    out = _call(world["rich"])
    assert out["unique_attendees_estimated"] is True
    assert out["avg_watch_minutes_estimated"] is True
    # The show rate is real, but only for private invited events — and says so.
    assert out["show_rate_basis"] == "private_invited_events_only"


def test_returning_attendees_is_a_real_count_not_an_estimate(world):
    """Distinct claimed emails seen at more than one event. No peak-concurrency guesswork is
    added to it, so it carries no _estimated flag and must stay that way."""
    out = _call(world["rich"])
    assert out["returning"] == 0
    assert "returning_estimated" not in out


# ── scoping ────────────────────────────────────────────────────────────────────────────

def test_one_organization_never_sees_another(world):
    """The rich org's snapshots must not leak into an unrelated org's attendance."""
    assert _call(world["other"])["unique_attendees"] == 0
    assert _call(world["rich"])["unique_attendees"] > 0
