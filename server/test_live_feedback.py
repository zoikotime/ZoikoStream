"""Viewer feedback after a live event: stored once per viewer, and acknowledged truthfully.

Production report: viewers saw no feedback request when an event ended. The watch page only
offered it from "Leave Event"; it now also asks once when the event ends (EventWatch.jsx).
That makes a second submission from one viewer likely, so the handler keeps ONE row per
viewer per event (a later submission updates it, keeping any field it leaves blank), and it
acknowledges a committed row to the submitter (session/feedback.saved) so the dialog says
"saved" only when it was — or returns an error the dialog shows, if it could not be stored.

Real handler (moderation._feedback_submit) against the isolated test database. Nothing in
sys.modules is replaced.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select

import app.main  # noqa: F401 - import order
from app.db import SessionLocal
from app.models import Event, Organization, User
from app.models.live import EventFeedback
from app.services import moderation as m
from app.services.moderation import Ctx


@pytest.fixture
def world():
    db = SessionLocal()
    made = {}
    try:
        org = Organization(name=f"fb-live-{uuid.uuid4().hex[:8]}", status="active")
        db.add(org)
        db.flush()
        host = User(org_id=org.id, full_name="Host", role="org_admin", is_active=True,
                    email=f"fbl-{uuid.uuid4().hex[:10]}@example.com",
                    username=f"fbl{uuid.uuid4().hex[:10]}", password_hash="x", email_verified=True)
        db.add(host)
        db.flush()
        ev = Event(org_id=org.id, created_by=host.id, title="Ended", status="ended")
        other = Event(org_id=org.id, created_by=host.id, title="Other", status="ended")
        db.add_all([ev, other])
        db.commit()
        made = {"org": org.id, "user": host.id, "events": [ev.id, other.id]}

        def viewer(event_id, identity="viewer-1", **kw):
            return Ctx(event_id=event_id, org_id=org.id, room=f"room-{event_id}", user_id=None,
                       name="Viewer One", identity=identity, role="viewer",
                       can_moderate=False, **kw)

        yield {"ev": ev.id, "other": other.id, "viewer": viewer,
               "staff": Ctx(event_id=ev.id, org_id=org.id, room="r", user_id=host.id, name="Host",
                            identity="host-1", role="host", can_moderate=True, can_host=True)}
    finally:
        if made:
            for eid in made["events"]:
                db.execute(EventFeedback.__table__.delete().where(EventFeedback.event_id == eid))
                db.execute(Event.__table__.delete().where(Event.id == eid))
            db.execute(User.__table__.delete().where(User.id == made["user"]))
            db.execute(Organization.__table__.delete().where(Organization.id == made["org"]))
            db.commit()
        db.close()


def submit(ctx, **payload):
    return asyncio.run(m._feedback_submit(ctx, payload))


def rows(event_id):
    with SessionLocal() as db:
        return db.scalars(select(EventFeedback).where(EventFeedback.event_id == event_id)).all()


def test_a_submission_is_stored_and_acknowledged_to_the_submitter(world):
    out = submit(world["viewer"](world["ev"]), rating=5, comment="Great session")
    assert out == [("session", "feedback.saved", {"identity": "viewer-1"})]
    [row] = rows(world["ev"])
    assert (row.rating, row.comment, row.role, row.identity) == (5, "Great session", "viewer", "viewer-1")


def test_a_second_submission_updates_the_first_rather_than_adding_a_row(world):
    v = world["viewer"](world["ev"])
    submit(v, rating=4)
    submit(v, comment="Audio was clear")          # rating left blank: kept
    submit(v, rating=5)                            # comment left blank: kept
    [row] = rows(world["ev"])
    assert (row.rating, row.comment) == (5, "Audio was clear")


def test_different_viewers_and_different_events_stay_separate(world):
    submit(world["viewer"](world["ev"]), rating=4)
    submit(world["viewer"](world["ev"], identity="viewer-2"), rating=2)
    submit(world["viewer"](world["other"]), rating=1)
    assert sorted(r.rating for r in rows(world["ev"])) == [2, 4]
    assert [r.rating for r in rows(world["other"])] == [1]


def test_an_empty_submission_stores_nothing_and_claims_nothing(world):
    assert submit(world["viewer"](world["ev"]), rating=None, comment="   ") == []
    assert rows(world["ev"]) == []


def test_staff_consoles_do_not_add_to_the_audience_average(world):
    assert submit(world["staff"], rating=5) == []
    assert rows(world["ev"]) == []


def test_a_storage_failure_is_reported_not_acknowledged(world, monkeypatch):
    async def broken(fn):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(m, "tx", broken)
    out = submit(world["viewer"](world["ev"]), rating=3)
    assert isinstance(out, str) and "couldn't be saved" in out   # dispatch sends it as an error
    assert rows(world["ev"]) == []
