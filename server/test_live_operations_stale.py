"""Live Operations must report the media server, not its own database row.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
The "live" list was `BroadcastSession.status IN ("live","paused")` and nothing else, and
health was:

    "health": "ok" if s.status in ("live", "paused") else None

— the row vouching for itself. A session whose worker died never reaches "ended", so it sat
in the monitor indefinitely: the reported case showed two sessions started 15 days earlier,
"383h 58m" of duration, both reading **Operational**. A status page that calls a
fortnight-old corpse healthy is worse than none, because operators learn to discount it.

Every supposedly-live session is now reconciled against LiveKit, with three distinct
answers. The one that matters most for safety is the third: when the provider cannot be
reached the session is left ALONE and reported "unknown" — a LiveKit outage must never make
the console start closing live broadcasts, and must never let them keep claiming health
either.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.main  # noqa: F401  - import order; see test_live_socket_connect.py

from app.db import SessionLocal
from app.models import BroadcastSession, Event, Organization, User
from app.security import hash_password
from app.services import admin as svc
from app.services import livekit as lk

UTC = timezone.utc


@pytest.fixture
def world():
    db = SessionLocal()
    made = {"s": [], "e": [], "u": [], "o": []}
    try:
        org = Organization(name=f"LiveCo {uuid.uuid4().hex[:6]}", status="active", region="US East")
        db.add(org)
        db.flush()
        made["o"] = [org.id]
        user = User(org_id=org.id, full_name="Host", role="org_admin", is_active=True,
                    email=f"lo-{uuid.uuid4().hex[:10]}@example.com",
                    username=f"lo{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password("x"), email_verified=True)
        db.add(user)
        db.flush()
        made["u"] = [user.id]
        ev = Event(org_id=org.id, created_by=user.id, title="Broadcast Test Event",
                   status="live", visibility="public",
                   start_time=datetime.now(UTC) - timedelta(days=15))
        db.add(ev)
        db.flush()
        made["e"] = [ev.id]
        db.commit()
        yield {"db": db, "org": org, "event": ev, "user": user, "made": made}
    finally:
        try:
            for eid in made["e"]:
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


def session(world, status="live", days_ago=15):
    """A row exactly like the ones in the report: opened long ago, never ended."""
    s = BroadcastSession(
        event_id=world["event"].id, org_id=world["org"].id, status=status,
        started_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    world["db"].add(s)
    world["db"].commit()
    world["made"]["s"].append(s.id)
    return s


def rows(world, state="live"):
    import asyncio
    return asyncio.run(svc.live_events(world["db"], state=state))


def mine(result, sess):
    return [r for r in result if r["id"] == str(sess.id)]


@pytest.fixture
def livekit_says(monkeypatch):
    """Drive the provider's answer: True (room up), False (gone), None (cannot ask)."""
    def _set(answer, participants=None):
        async def room_is_live(room):
            return answer
        async def room_participant_count(room):
            return participants
        monkeypatch.setattr(lk, "room_is_live", room_is_live)
        monkeypatch.setattr(lk, "room_participant_count", room_participant_count)
    return _set


# ── a genuinely live session ───────────────────────────────────────────────────────────

def test_a_real_live_room_stays_live_and_healthy(world, livekit_says):
    livekit_says(True, participants=3)
    s = session(world)

    got = mine(rows(world), s)

    assert len(got) == 1
    assert got[0]["health"] == "ok"
    assert got[0]["viewers"] == 3          # the media server's real count
    world["db"].expire_all()
    assert world["db"].get(BroadcastSession, s.id).status == "live"


def test_a_paused_broadcast_is_still_reported(world, livekit_says):
    livekit_says(True, participants=1)
    s = session(world, status="paused")

    assert len(mine(rows(world), s)) == 1


# ── the reported case: a stale row with no room behind it ──────────────────────────────

def test_a_stale_session_with_no_livekit_room_is_retired(world, livekit_says):
    """THE BUG. 15 days old, no room, and it was reading Operational."""
    livekit_says(False)
    s = session(world)

    got = mine(rows(world), s)

    assert got == [], "a session with no room must not appear under Live"
    world["db"].expire_all()
    closed = world["db"].get(BroadcastSession, s.id)
    assert closed.status == "ended"
    assert closed.ended_at is not None      # closed through the real lifecycle, with a reason
    assert closed.ended_reason


def test_the_retired_session_moves_to_recently_ended(world, livekit_says):
    livekit_says(False)
    s = session(world)
    rows(world)                              # the reconciliation pass retires it

    assert len(mine(rows(world, state="recent"), s)) == 1


def test_it_is_never_reported_as_operational_on_the_way_out(world, livekit_says):
    livekit_says(False)
    s = session(world)

    assert all(r["health"] != "ok" for r in rows(world) if r["id"] == str(s.id))


# ── the provider cannot be asked ───────────────────────────────────────────────────────

def test_provider_unavailable_reports_unknown_not_operational(world, livekit_says):
    livekit_says(None)
    s = session(world)

    got = mine(rows(world), s)

    assert len(got) == 1
    assert got[0]["health"] == "unknown"
    assert got[0]["health"] != "ok"


def test_provider_unavailable_does_NOT_retire_the_session(world, livekit_says):
    """The safety property. During a LiveKit outage every live broadcast would look roomless;
    retiring on that would end real events because the monitor could not place a call."""
    livekit_says(None)
    s = session(world)
    rows(world)

    world["db"].expire_all()
    still = world["db"].get(BroadcastSession, s.id)
    assert still.status == "live"
    assert still.ended_at is None


def test_viewers_stay_unknown_rather_than_zero(world, livekit_says):
    # Missing data must not become 0 — the console prints an em dash for None.
    livekit_says(None)
    s = session(world)

    assert mine(rows(world), s)[0]["viewers"] is None


def test_a_probe_that_raises_is_treated_as_unknown(world, monkeypatch):
    async def boom(room):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(lk, "room_is_live", boom)
    s = session(world)

    got = mine(rows(world), s)

    assert len(got) == 1 and got[0]["health"] == "unknown"
    world["db"].expire_all()
    assert world["db"].get(BroadcastSession, s.id).status == "live"


# ── ended history is untouched ─────────────────────────────────────────────────────────

def test_recently_ended_is_not_probed_or_given_health(world, livekit_says):
    livekit_says(False)
    s = session(world, status="ended")
    s.ended_at = datetime.now(UTC) - timedelta(hours=2)
    world["db"].commit()

    got = mine(rows(world, state="recent"), s)

    assert len(got) == 1
    assert got[0]["health"] is None          # history has no current health to report
