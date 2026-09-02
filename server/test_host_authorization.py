"""Host authorization resolution — the exact production complaint.

REPORTED: "I assigned myself as the host of the event" and the console still said
"No host assigned" / "View only" / "You're not signed in as a host of this event".

resolve_ctx is the single authority for can_host (routers/live.py builds the moderator
snapshot from it, and the snapshot's can_host is the ONLY thing that sets state.canHost in
useLiveEvent.js). These lock down its contract so a regression there is caught here rather
than in a producer console.
"""
import uuid

import pytest

from app.db import SessionLocal
from app.models import Event, EventAssignment, Organization, User
from app.services import moderation as mod


@pytest.fixture(scope="module")
def world():
    """One real org + users + event. Module-scoped: resolve_ctx opens its own session, so
    these must genuinely exist in the database, and this DB is remote enough that creating
    them per-test dominates the runtime."""
    db = SessionLocal()
    org = Organization(name=f"authz-{uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()

    def mk(role):
        return User(org_id=org.id, full_name=f"authz {role}", role=role, is_active=True,
                    email=f"authz-{role}-{uuid.uuid4().hex[:8]}@example.com",
                    username=f"authz{role}{uuid.uuid4().hex[:8]}", password_hash="unused")

    host, viewer, admin, speaker = mk("host"), mk("viewer"), mk("org_admin"), mk("speaker")
    # One at a time rather than add_all. This was an attempt to work around a Supabase /
    # Supavisor transaction-pooler fault under which ORM INSERTs hang indefinitely from a
    # fresh process while the long-lived dev server keeps writing fine; splitting the batch
    # did NOT resolve it, so it is kept only because per-row flushes fail more legibly (you
    # see which row wedged). The fault is environmental and intermittent — the same fixture
    # ran repeatedly earlier the same day — and is NOT application behaviour.
    for _u in (host, viewer, admin, speaker):
        db.add(_u)
        db.flush()
    # created_by is the ADMIN, not the assigned host — so nothing here can pass by
    # accidentally treating event ownership as hosting.
    ev = Event(org_id=org.id, created_by=admin.id, title="authz", status="published",
               visibility="public", registration_required=False)
    db.add(ev)
    db.flush()
    db.add(EventAssignment(event_id=ev.id, user_id=host.id, role="host"))
    db.add(EventAssignment(event_id=ev.id, user_id=speaker.id, role="speaker"))
    db.commit()

    # IDs only. Yielding live ORM objects hands every test a instance bound to THIS session;
    # once it is no longer the active session, touching an unloaded attribute emits a lazy
    # load against a connection the test does not control, which is how this fixture wedged
    # against a degraded pooler. It also mirrors production: resolve_ctx is always called with
    # a User loaded by the request's own session, never one carried across sessions.
    org_id = org.id
    ids = {"event": ev.id, "host": host.id, "viewer": viewer.id,
           "admin": admin.id, "speaker": speaker.id}
    db.close()
    yield ids
    db = SessionLocal()

    db.query(EventAssignment).filter(EventAssignment.event_id == ids["event"]).delete()
    row = db.get(Event, ids["event"])
    if row:
        db.delete(row)
    for uid in (ids["host"], ids["viewer"], ids["admin"], ids["speaker"]):
        got = db.get(User, uid)
        if got:
            db.delete(got)
    got_org = db.get(Organization, org_id)
    if got_org:
        db.delete(got_org)
    db.commit()
    db.close()


def _resolve(event_id, user_id):
    """Resolve exactly as a request does: fetch the User in a fresh session, then hand it to
    resolve_ctx (which opens its own session internally)."""
    db = SessionLocal()
    try:
        return mod.resolve_ctx(event_id, db.get(User, user_id))
    finally:
        db.close()

def test_explicitly_assigned_host_is_recognised(world):
    """THE reported bug. A plain platform-role "host" with EventAssignment(role="host") —
    exactly what "I assigned myself as host" persists — must resolve can_host True."""
    ctx = _resolve(world["event"], world["host"])
    assert ctx is not None, "resolve_ctx refused the event outright"
    assert ctx.can_host is True, "an explicitly assigned host was not recognised as host"
    assert ctx.can_moderate is True, "a host must also be able to moderate their own event"


def test_assignment_is_matched_on_the_authenticated_user_id(world):
    """Guards the ID-comparison failure mode: the assignment must be selected by the
    authenticated user's own id, not by event ownership or org membership."""
    ctx = _resolve(world["event"], world["host"])
    assert str(ctx.user_id) == str(world["host"])
    assert ctx.identity == str(world["host"])


def test_event_creator_is_not_silently_treated_as_host(world):
    """created_by here is the org_admin. They get can_host from their PLATFORM role, which is
    the documented model — this asserts the model, so a change to it is deliberate."""
    ctx = _resolve(world["event"], world["admin"])
    assert ctx.can_host is True


def test_plain_viewer_in_the_same_org_stays_view_only(world):
    """The other half of the fix: nothing above may hand can_host to someone unassigned."""
    ctx = _resolve(world["event"], world["viewer"])
    assert ctx is not None
    assert ctx.can_host is False
    assert ctx.can_moderate is False


def test_assigned_speaker_is_a_contributor_not_a_host(world):
    ctx = _resolve(world["event"], world["speaker"])
    assert ctx.can_contribute is True
    assert ctx.can_host is False


def test_snapshot_carries_can_host_to_the_console(world):
    """state.canHost in useLiveEvent.js comes from exactly one field. If snapshot_extra ever
    stops emitting it, the console silently falls back to its unauthorised default — which is
    precisely how a correctly-assigned host got told they were not the host."""
    import asyncio

    from app.services import bus, broadcast

    bus._redis = None   # avoid a stale loop from other asyncio.run() calls in this process
    ctx = _resolve(world["event"], world["host"])
    extra = asyncio.run(broadcast.snapshot_extra(ctx))
    assert "can_host" in extra, "snapshot no longer carries can_host at all"
    assert extra["can_host"] is True
