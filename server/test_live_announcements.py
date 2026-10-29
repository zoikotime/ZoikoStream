"""Live Producer Console announcements: one submission, one record, one broadcast per change.

Production report: posting one scheduled announcement showed two entries in the host's queue,
"Sent 10:20 · 1 connected" beside an identical one still showing the pending clock. The
backend wrote ONE record. It broadcast it as `announcement.new` twice — at creation (no
sent_at) and again when the scheduler sent it — and the console prepended both (fixed in
client/src/hooks/useLiveEvent.js, tested in liveAnnouncements.test.js). Separately, every
worker process runs the scheduler ticker and the bus fans each publish out to every worker,
so two tickers could both claim the same due row and broadcast it twice; the scheduler now
claims due rows with FOR UPDATE SKIP LOCKED.

These drive the real handlers (moderation._announce_send, moderation._due) against the
isolated test database. Nothing in sys.modules is replaced.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import app.main  # noqa: F401 - import order
from app.db import SessionLocal
from app.models import Event, Organization, User
from app.models.audit_log import AuditLog
from app.models.live import LiveActivity, LiveAnnouncement
from app.services import bus
from app.services import moderation as m
from app.services.moderation import Ctx


@pytest.fixture(autouse=True)
def in_process_bus():
    url = bus.settings.REDIS_URL
    bus.settings.REDIS_URL = ""
    try:
        yield
    finally:
        bus.settings.REDIS_URL = url


@pytest.fixture
def host():
    db = SessionLocal()
    made = {}
    try:
        org = Organization(name=f"ann-org-{uuid.uuid4().hex[:8]}", status="active")
        db.add(org)
        db.flush()
        user = User(org_id=org.id, full_name="Ann Host", role="org_admin", is_active=True,
                    email=f"ann-{uuid.uuid4().hex[:10]}@example.com",
                    username=f"ann{uuid.uuid4().hex[:10]}", password_hash="x", email_verified=True)
        db.add(user)
        db.flush()
        ev = Event(org_id=org.id, created_by=user.id, title="Announcements", status="live",
                   visibility="public")
        db.add(ev)
        db.commit()
        made = {"org": org.id, "user": user.id, "event": ev.id}
        yield Ctx(event_id=ev.id, org_id=org.id, room=f"room-{ev.id}", user_id=user.id,
                  name="Ann Host", identity=f"host-{user.id}", role="host",
                  can_moderate=True, can_host=True)
    finally:
        if made:
            db.rollback()
            for model in (LiveAnnouncement, LiveActivity):
                db.execute(model.__table__.delete().where(model.event_id == made["event"]))
            db.execute(AuditLog.__table__.delete().where(AuditLog.org_id == made["org"]))
            db.execute(Event.__table__.delete().where(Event.id == made["event"]))
            db.execute(User.__table__.delete().where(User.id == made["user"]))
            db.execute(Organization.__table__.delete().where(Organization.id == made["org"]))
            db.commit()
        db.close()


def send(ctx, **payload):
    return asyncio.run(m._announce_send(ctx, payload))


def rows(event_id):
    with SessionLocal() as db:
        return db.scalars(select(LiveAnnouncement).where(LiveAnnouncement.event_id == event_id)).all()


def run_due():
    """One scheduler tick, as run_scheduler does it: _due inside a committed transaction."""
    return [e for e in m._run(m._due) if e[1] == "announcement.new"]


def make_due(announcement_id):
    with SessionLocal() as db:
        a = db.get(LiveAnnouncement, uuid.UUID(announcement_id))
        a.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()


def test_one_immediate_submission_is_one_record_and_one_broadcast(host):
    out = send(host, text="We're starting in 5 minutes", priority="important")
    news = [d for ch, t, d in out if t == "announcement.new"]
    assert len(news) == 1 and news[0]["sent_at"] and news[0]["status"] == "sent"
    assert len(rows(host.event_id)) == 1


def test_a_scheduled_announcement_is_one_record_sent_once(host):
    out = send(host, text="Q&A next", priority="important",
               scheduled_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    created = [d for ch, t, d in out if t == "announcement.new"]
    assert len(created) == 1 and created[0]["sent_at"] is None and created[0]["status"] == "scheduled"

    make_due(created[0]["id"])
    first = [e for e in run_due() if e[2]["id"] == created[0]["id"]]
    assert len(first) == 1 and first[0][2]["sent_at"]          # the SAME record, now sent
    assert [e for e in run_due() if e[2]["id"] == created[0]["id"]] == []   # never twice
    assert len(rows(host.event_id)) == 1                         # still one record
    assert rows(host.event_id)[0].sent_at is not None


def test_a_row_another_ticker_holds_is_skipped_not_double_sent(host):
    out = send(host, text="Slides", priority="normal",
               scheduled_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    aid = [d for ch, t, d in out if t == "announcement.new"][0]["id"]
    make_due(aid)

    other = SessionLocal()                     # a second worker's ticker, mid-claim
    try:
        other.execute(select(LiveAnnouncement).where(LiveAnnouncement.id == uuid.UUID(aid))
                      .with_for_update()).all()
        assert [e for e in run_due() if e[2]["id"] == aid] == []   # skipped, not blocked
    finally:
        other.rollback()
        other.close()
    assert len([e for e in run_due() if e[2]["id"] == aid]) == 1   # claimed once released


def test_separate_identical_submissions_stay_separate(host):
    send(host, text="Wrapping up", priority="normal")
    send(host, text="Wrapping up", priority="normal")
    with SessionLocal() as db:
        n = db.scalar(select(func.count()).select_from(LiveAnnouncement)
                      .where(LiveAnnouncement.event_id == host.event_id))
    assert n == 2      # two deliberate posts are two announcements, not a duplicate to hide
