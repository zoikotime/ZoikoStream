"""Session-aware viewer analytics — additional metrics, existing ones untouched.

ZST-SPEC-VAP-001 §9, built on the existing session ledger (services/bus.py) by
services/viewing_sessions.py:
  * each viewing session gets an opaque random id and its coarse browser/device class;
  * sessions admitted, peak concurrent sessions, median watch, join-time distribution,
    rejoin rate and playback QoE, with None (never 0) when nothing was measured;
  * the rejoin rate never merges identities that do not stand for one person;
  * playback reports land on the viewer's open session only, from a closed vocabulary;
  * org analytics, event reports and the finalized summary carry the block, and the existing
    Peak Viewers / watch-time fields are unchanged.
"""
import asyncio
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.main  # noqa: F401 — import order
from app.db import SessionLocal
from app.models import AnalyticsSnapshot, BroadcastSession, Event, Organization, User
from app.security import hash_password
from app.services import bus
from app.services import moderation as mod
from app.services import org as org_svc
from app.services import report as report_svc
from app.services.moderation import Ctx
from app.services.viewing_sessions import REBUFFER_NOTE, combine, summarize_sessions

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _in_process_bus(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    yield


def row(identity, joined, left, role="viewer", **extra):
    return {"identity": identity, "role": role, "joined_at": joined, "last_seen": left, "left_at": left, **extra}


# ── the pure metrics ─────────────────────────────────────────────────────────────────────

def test_nothing_measured_reports_none_never_zero():
    out = summarize_sessions([], broadcast_start_ts=0)
    assert out["measured"] is False
    for key in ("sessions_admitted", "peak_concurrent_sessions", "median_watch_seconds",
                "join_time_distribution", "rejoin_rate", "qoe"):
        assert out[key] is None, key
    # Staff alone are not an audience.
    assert summarize_sessions([row("host-1", 0, 600, role="host")])["measured"] is False


def test_sessions_peak_median_and_join_distribution():
    start = 1_000_000.0
    rows = [
        row("guest-a", start - 60, start + 600),          # joined before start, 11 min
        row("guest-b", start + 60, start + 360),          # 0-5m, 5 min
        row("guest-c", start + 400, start + 700),         # 5-15m, 5 min
        row("guest-d", start + 4000, start + 4100),       # 60m+, ~2 min, alone
        row("host-x", start, start + 5000, role="host"),  # staff: excluded everywhere
    ]
    out = summarize_sessions(rows, broadcast_start_ts=start)
    assert out["measured"] is True
    assert out["sessions_admitted"] == 4
    assert out["peak_concurrent_sessions"] == 2                        # a+b, then a+c
    assert out["median_watch_seconds"] == 300.0                        # [100, 300, 300, 660]
    dist = {b["bucket"]: b["sessions"] for b in out["join_time_distribution"]}
    assert dist == {"before_start": 1, "0-5m": 1, "5-15m": 1, "15-30m": 0, "30-60m": 0, "60m+": 1}


def test_join_distribution_is_none_without_a_known_start():
    assert summarize_sessions([row("guest-a", 0, 60)])["join_time_distribution"] is None


def test_rejoin_rate_never_merges_people():
    start = 0.0
    link = str(uuid.uuid4())
    rows = [
        row("guest-reg1", 0, 100), row("guest-reg1", 200, 300),       # one registrant, rejoined once
        row("guest-reg2", 0, 300),
        # The legacy shared link identity is many people: never counted as rejoins.
        row(f"guest-link-{link}", 0, 50), row(f"guest-link-{link}", 60, 90), row(f"guest-link-{link}", 95, 99),
        # A per-browser pass identity IS one browser, so it counts.
        row(f"guest-link-{link}-ab12cd34ef56ab78", 0, 10), row(f"guest-link-{link}-ab12cd34ef56ab78", 20, 30),
    ]
    out = summarize_sessions(rows, broadcast_start_ts=start)
    assert out["rejoin_basis"] == {"sessions": 5, "excluded_shared_sessions": 3}
    assert out["rejoins"] == 2 and out["rejoin_rate"] == 0.4
    assert out["sessions_admitted"] == 8                              # every session still counts


def test_open_sessions_end_at_their_last_heartbeat():
    out = summarize_sessions([{"identity": "guest-a", "role": "viewer", "joined_at": 0,
                               "last_seen": 120, "left_at": None}], now_ts=10_000)
    assert out["median_watch_seconds"] == 120.0


def test_qoe_reports_only_what_players_sent():
    rows = [
        row("guest-a", 0, 100, startup_ms=800, browser="Chrome", device="Desktop"),
        row("guest-b", 0, 100, startup_ms=1600, browser="Safari", device="Mobile",
            failures=1, failure_kinds={"connect": 1}),
        row("guest-c", 0, 100),                                       # reported nothing
    ]
    qoe = summarize_sessions(rows)["qoe"]
    assert qoe["sessions_reporting"] == 2
    assert qoe["startup_ms_median"] == 1200
    assert qoe["failed_sessions"] == 1
    assert qoe["failures_by_browser"] == {"Safari": 1} and qoe["failures_by_device"] == {"Mobile": 1}
    assert qoe["rebuffer_ratio"] is None and qoe["rebuffer_note"] == REBUFFER_NOTE
    none_reported = summarize_sessions([row("guest-c", 0, 100)])["qoe"]
    assert none_reported["startup_ms_median"] is None and none_reported["failed_sessions"] is None


def test_combine_adds_what_adds_and_keeps_the_median_per_event():
    a = summarize_sessions([row("guest-a", 0, 100), row("guest-a", 200, 300)], broadcast_start_ts=0)
    b = summarize_sessions([row("guest-b", 0, 50), row("guest-c", 10, 60)], broadcast_start_ts=0)
    out = combine([a, b, summarize_sessions([])])
    assert out["events_measured"] == 2 and out["sessions_admitted"] == 4
    assert out["peak_concurrent_sessions"] == 2
    assert out["rejoin_rate"] == 0.25
    assert "median_watch_seconds" not in out
    assert combine([])["measured"] is False


# ── the ledger: session ids, device class, playback reports ─────────────────────────────

def test_each_session_gets_an_opaque_id_and_browser_class():
    event_id = uuid.uuid4()

    async def flow():
        await bus.presence_upsert(event_id, "guest-a", {"role": "viewer", "name": "A",
                                                       "device": "Mobile", "browser": "Safari",
                                                       "platform": "iOS"})
        await bus.presence_remove(event_id, "guest-a")
        await bus.presence_upsert(event_id, "guest-a", {"role": "viewer", "name": "A",
                                                       "device": "Mobile", "browser": "Safari"})
        return await bus.session_get_all(event_id)

    rows = [r for r in asyncio.run(flow()) if r.get("sid")]
    assert len(rows) == 2
    assert all(re.fullmatch(r"[0-9a-f]{16}", r["sid"]) for r in rows)
    assert rows[0]["sid"] != rows[1]["sid"]
    assert all(r["browser"] == "Safari" and r["device"] == "Mobile" for r in rows)
    assert all("platform" not in r and "name" in r for r in rows)     # class only, no UA string


def _viewer(event_id, identity="guest-v"):
    return Ctx(event_id=event_id, org_id=uuid.uuid4(), room=f"event_{event_id}", user_id=uuid.uuid4(),
               name="V", identity=identity, role="viewer", can_moderate=False)


def test_playback_reports_attach_to_the_open_session_only():
    event_id = uuid.uuid4()
    v = _viewer(event_id)

    async def flow():
        assert await mod.dispatch(v, "playback.report", {"startup_ms": 900}) is None   # no session yet
        await bus.presence_upsert(event_id, v.identity, {"role": "viewer", "name": "V"})
        await mod.dispatch(v, "playback.report", {"startup_ms": 1200})
        await mod.dispatch(v, "playback.report", {"startup_ms": 50})                   # first one wins
        await mod.dispatch(v, "playback.report", {"failure": "connect"})
        await mod.dispatch(v, "playback.report", {"failure": "<script>"})              # not a kind
        await mod.dispatch(v, "playback.report", {"startup_ms": 10 ** 9})              # out of range
        host = Ctx(event_id=event_id, org_id=v.org_id, room=v.room, user_id=uuid.uuid4(), name="H",
                   identity="host-h", role="host", can_moderate=True, can_host=True)
        await mod.dispatch(host, "playback.report", {"startup_ms": 5})                 # staff ignored
        return await bus.session_get_all(event_id)

    rows = [r for r in asyncio.run(flow()) if r["identity"] == "guest-v"]
    assert len(rows) == 1
    assert rows[0]["startup_ms"] == 1200
    assert rows[0]["failures"] == 1 and rows[0]["failure_kinds"] == {"connect": 1}


# ── durable, org analytics and the event report ──────────────────────────────────────────

@pytest.fixture
def ended_event():
    db = SessionLocal()
    org = Organization(name=f"Sessions {uuid.uuid4().hex[:6]}", status="active")
    db.add(org)
    db.flush()
    u = User(org_id=org.id, full_name="Fixture", role="org_admin", is_active=True,
             email=f"vs-{uuid.uuid4().hex[:10]}@example.com", username=f"vs{uuid.uuid4().hex[:10]}",
             password_hash=hash_password("x"), email_verified=True)
    db.add(u)
    db.flush()
    started = datetime.now(UTC) - timedelta(hours=2)
    ev = Event(org_id=org.id, created_by=u.id, title="Measured", status="ended", visibility="public",
               start_time=started, end_time=started + timedelta(hours=1))
    db.add(ev)
    db.flush()
    bs = BroadcastSession(event_id=ev.id, org_id=org.id, status="ended", peak_viewers=3,
                          started_at=started, ended_at=started + timedelta(hours=1))
    db.add(bs)
    db.add(AnalyticsSnapshot(event_id=ev.id, org_id=org.id, viewers=2, participants=3, on_stage=1,
                             messages=0, questions=0, reactions=0, hands=0))
    db.commit()
    try:
        yield type("E", (), {"db": db, "org": org, "event": ev, "bs": bs, "started": started})()
    finally:
        db.rollback()
        bus._sessions_memory.pop(str(ev.id), None)  # noqa: SLF001
        bus._summary_memory.pop(str(ev.id), None)  # noqa: SLF001
        db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.event_id == ev.id).delete()
        db.query(BroadcastSession).filter(BroadcastSession.event_id == ev.id).delete()
        db.query(Event).filter(Event.id == ev.id).delete()
        db.query(User).filter(User.id == u.id).delete()
        db.query(Organization).filter(Organization.id == org.id).delete()
        db.commit()
        db.close()


def _seed_ledger(event_id, start):
    async def flow():
        for ident, offset, length in (("guest-a", 0, 1800), ("guest-b", 600, 600), ("guest-a", 2000, 300)):
            await bus.session_record_join(event_id, ident, role="viewer", joined_at=start + offset)
            await bus.session_record_heartbeat(event_id, ident, start + offset + length)
            await bus.session_record_leave(event_id, ident, left_at=start + offset + length)
    asyncio.run(flow())


def test_finalize_stores_the_session_block_so_it_outlives_the_ledger(ended_event):
    start = ended_event.started.timestamp()
    _seed_ledger(ended_event.event.id, start)
    summary = asyncio.run(bus.session_finalize(ended_event.event.id, end_time=start + 3600,
                                               broadcast_start_ts=start))
    sessions = summary["sessions"]
    assert sessions["measured"] and sessions["sessions_admitted"] == 3
    assert sessions["rejoins"] == 1
    assert {b["bucket"]: b["sessions"] for b in sessions["join_time_distribution"]}["0-5m"] == 1
    # Existing watch-time keys are still there, unchanged in meaning.
    assert summary["measured"] is True and summary["total_watch_seconds"] is not None


def test_org_analytics_adds_sessions_and_leaves_existing_fields_alone(ended_event):
    start = ended_event.started.timestamp()
    _seed_ledger(ended_event.event.id, start)
    out = org_svc.analytics(ended_event.db, ended_event.org, range_key="30d")
    (report,) = [r for r in out["reports"] if r["id"] == str(ended_event.event.id)]
    assert report["viewers"] == 3                                      # Peak Viewers, as before
    assert report["watch_hours"] is not None and report["measurement_state"] == "measured"
    s = report["sessions"]
    assert s["sessions_admitted"] == 3 and s["peak_concurrent_sessions"] == 2
    assert s["median_watch_seconds"] == 600.0
    assert out["session_summary"]["sessions_admitted"] == 3
    assert out["summary"]["peak_viewers_summed"] == 3                  # existing summary untouched


def test_an_event_with_no_sessions_reports_none_not_zero(ended_event):
    out = org_svc.analytics(ended_event.db, ended_event.org, range_key="30d")
    (report,) = [r for r in out["reports"] if r["id"] == str(ended_event.event.id)]
    assert report["sessions"]["measured"] is False and report["sessions"]["sessions_admitted"] is None
    assert report["viewers"] == 3                                      # the card keeps its real value
    assert out["session_summary"]["measured"] is False


def test_the_event_report_carries_the_session_block(ended_event):
    start = ended_event.started.timestamp()
    _seed_ledger(ended_event.event.id, start)
    audience = report_svc._audience(ended_event.db, ended_event.event)  # noqa: SLF001
    assert audience["sessions"]["sessions_admitted"] == 3
    assert audience["peak_viewers"] >= 3                               # existing figure intact


def test_the_session_ids_are_random_not_derived_from_the_person():
    ids = set()

    async def flow():
        for _ in range(20):
            e = uuid.uuid4()
            await bus.session_record_join(e, "guest-same", joined_at=time.time())
            ids.add((await bus.session_get_all(e))[0]["sid"])
    asyncio.run(flow())
    assert len(ids) == 20
