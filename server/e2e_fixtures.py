"""Real-DB fixtures for the Playwright live-streaming E2E suite (client/e2e/).

Not a test_*.py file deliberately — pytest must not collect this, it's invoked directly by
Playwright's globalSetup/globalTeardown (client/e2e/global-setup.js) via a subprocess call.

Creates a real org, a real host user (org_admin — gets can_host/can_moderate for free, see
services/moderation.resolve_ctx) and a real contributor user (role="speaker", needs an
EventAssignment + a ContributorSession row to pass the join-window gate — see
services/contributor.join_window_error), and a real Event. Mints real access JWTs via the
same app.security.create_access_token the login endpoint uses, so the browser is handed
tokens indistinguishable from a real login — this test intentionally skips clicking through
the login FORM (not what this suite is testing) but exercises the exact same auth code path
after that point.

Writes fixture data (event id, tokens, user ids) to a JSON file the Playwright config reads
in globalSetup — never printed to stdout in a way a shared log would capture, and the output
path is gitignored (client/e2e/.auth/).

Run: `python e2e_fixtures.py create <out.json>` / `python e2e_fixtures.py cleanup <out.json>`
"""
import json
import sys
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import (
    AnalyticsSnapshot, BroadcastSession, ContributorSession, Event, EventAssignment,
    LiveActivity, LiveAnnouncement, LiveMessage, LivePoll, LivePollVote, LiveQuestion,
    LiveQuestionVote, LiveRecording, Organization, User,
)
from app.security import create_access_token


def create(out_path: str) -> None:
    db = SessionLocal()
    org = Organization(name=f"e2e-org-{uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()

    host = User(
        org_id=org.id, full_name="E2E Host", role="org_admin", is_active=True,
        email=f"e2e-host-{uuid.uuid4().hex[:10]}@example.com",
        username=f"e2ehost{uuid.uuid4().hex[:10]}", password_hash="not-used-token-auth-only",
    )
    contributor = User(
        org_id=org.id, full_name="E2E Contributor", role="speaker", is_active=True,
        email=f"e2e-contrib-{uuid.uuid4().hex[:10]}@example.com",
        username=f"e2econtrib{uuid.uuid4().hex[:10]}", password_hash="not-used-token-auth-only",
    )
    # Signed-in, not assigned to the event in any way — a plain org member. routers/events.py
    # watch_event's `mustIdentify` gate (EventWatch.jsx) only checks `user` truthiness, not
    # any assignment, so this is enough to view the public watch page without going through
    # the anonymous-visitor identify form, which is correct app behavior this suite has no
    # reason to route around (skipping the FORM, same reasoning as loginAs's own docstring).
    viewer1 = User(
        org_id=org.id, full_name="E2E Viewer One", role="viewer", is_active=True,
        email=f"e2e-viewer1-{uuid.uuid4().hex[:10]}@example.com",
        username=f"e2eviewer1{uuid.uuid4().hex[:10]}", password_hash="not-used-token-auth-only",
    )
    viewer2 = User(
        org_id=org.id, full_name="E2E Viewer Two", role="viewer", is_active=True,
        email=f"e2e-viewer2-{uuid.uuid4().hex[:10]}@example.com",
        username=f"e2eviewer2{uuid.uuid4().hex[:10]}", password_hash="not-used-token-auth-only",
    )
    db.add_all([host, contributor, viewer1, viewer2])
    db.flush()

    ev = Event(
        org_id=org.id, created_by=host.id, title="E2E Live Streaming Test Event",
        status="published", visibility="public", registration_required=False,
        # Default False on the model — WatchPanel (client/src/pages/watch/EventWatch.jsx)
        # renders no chat/Q&A/polls tab strip at all when these are off, which is correct
        # product behavior, not a bug (discovered when test 7 found an empty viewer panel).
        chat_enabled=True, qa_enabled=True, polls_enabled=True,
    )
    db.add(ev)
    db.flush()

    db.add(EventAssignment(event_id=ev.id, user_id=host.id, role="host"))
    # Deliberately NOT assigning the contributor as a speaker here — services/crud/commercial's
    # golive_readiness (called from _golive_gate) requires every ASSIGNED speaker to have
    # completed consent/preflight/rehearsal before the host can go live at all. Assigning the
    # contributor up front (discovered by actually running this suite — see invite_contributor
    # below) blocked "Go Live" outright with "Cannot go live — contributor ... has not given
    # consent...". Real usage matches this: a contributor is invited AFTER the broadcast is
    # already running just as often as before, and _golive_gate's own docstring confirms an
    # ALREADY-live event is never re-gated — so inviting post-Go-Live (invite_contributor,
    # called mid-suite once the host is live) is both realistic and avoids the gate entirely.
    db.commit()

    fixtures = {
        "org_id": str(org.id),
        "event_id": str(ev.id),
        "host": {"id": str(host.id), "token": create_access_token(host, True),
                 "full_name": host.full_name, "role": host.role},
        "contributor": {"id": str(contributor.id), "token": create_access_token(contributor, True),
                        "full_name": contributor.full_name, "role": contributor.role},
        "viewer1": {"id": str(viewer1.id), "token": create_access_token(viewer1, True)},
        "viewer2": {"id": str(viewer2.id), "token": create_access_token(viewer2, True)},
    }
    with open(out_path, "w") as f:
        json.dump(fixtures, f)
    # Never print the tokens themselves — ids only, safe for a shared CI log.
    print(f"E2E fixtures created: org={org.id} event={ev.id} host={host.id} contributor={contributor.id}")


def cleanup(out_path: str) -> None:
    try:
        with open(out_path) as f:
            fixtures = json.load(f)
    except FileNotFoundError:
        print("No fixture file to clean up — nothing to do")
        return

    db = SessionLocal()
    event_id = uuid.UUID(fixtures["event_id"])
    org_id = uuid.UUID(fixtures["org_id"])
    host_id = uuid.UUID(fixtures["host"]["id"])
    contributor_id = uuid.UUID(fixtures["contributor"]["id"])

    # Close any still-open BroadcastSession FIRST: a real dev server's ticker (services/
    # broadcast.py run_sampler) samples every session with ended_at IS NULL every
    # SAMPLE_SECONDS and keeps inserting AnalyticsSnapshot rows for it — racing a plain
    # DELETE with that ticker (discovered by actually running this suite against a live
    # local backend) means a fresh row can appear between this session's DELETE and its
    # final commit. Setting ended_at stops the sampler from matching this session at all,
    # closing the race at the source instead of retry-fighting it.
    db.query(BroadcastSession).filter(
        BroadcastSession.event_id == event_id, BroadcastSession.ended_at.is_(None)
    ).update({"ended_at": datetime.now(timezone.utc), "status": "ended"})
    db.commit()

    # FK-safe order: rows that reference the event first, then the event/users/org.
    # Votes before their parent poll/question (test 7 onward exercises chat, now that
    # chat_enabled/qa_enabled/polls_enabled are set on create() — a real LiveMessage row
    # from that test was the first thing to ever hit this gap, via a plain FK violation).
    #
    # Retried as a whole block: marking the session ended (above) closes the sampler race in
    # the common case, but a run long/slow enough (real network latency in this environment,
    # not simulated) can still let run_sampler's OWN in-flight query — already past the
    # ended_at check when our UPDATE committed — insert one more AnalyticsSnapshot row after
    # our DELETE already ran and before our final commit. Observed directly, not
    # hypothetical. A short retry loop is simpler and more honest than trying to eliminate a
    # background ticker's timing window entirely.
    last_exc = None
    for attempt in range(4):
        try:
            db.query(LivePollVote).filter(LivePollVote.event_id == event_id).delete()
            db.query(LiveQuestionVote).filter(LiveQuestionVote.event_id == event_id).delete()
            db.query(LivePoll).filter(LivePoll.event_id == event_id).delete()
            db.query(LiveQuestion).filter(LiveQuestion.event_id == event_id).delete()
            db.query(LiveMessage).filter(LiveMessage.event_id == event_id).delete()
            db.query(LiveAnnouncement).filter(LiveAnnouncement.event_id == event_id).delete()
            db.query(LiveActivity).filter(LiveActivity.event_id == event_id).delete()
            db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.event_id == event_id).delete()
            db.query(LiveRecording).filter(LiveRecording.event_id == event_id).delete()
            db.query(BroadcastSession).filter(BroadcastSession.event_id == event_id).delete()
            db.query(ContributorSession).filter(ContributorSession.event_id == event_id).delete()
            db.query(EventAssignment).filter(EventAssignment.event_id == event_id).delete()
            ev = db.get(Event, event_id)
            if ev:
                db.delete(ev)
            viewer_ids = (
                uuid.UUID(fixtures["viewer1"]["id"]) if "viewer1" in fixtures else None,
                uuid.UUID(fixtures["viewer2"]["id"]) if "viewer2" in fixtures else None,
            )
            for uid in (host_id, contributor_id, *filter(None, viewer_ids)):
                u = db.get(User, uid)
                if u:
                    db.delete(u)
            org = db.get(Organization, org_id)
            if org:
                db.delete(org)
            db.commit()
            print(f"E2E fixtures cleaned up: org={org_id} event={event_id}")
            return
        except IntegrityError as exc:
            last_exc = exc
            db.rollback()
            time.sleep(1.5)
    raise last_exc


def invite_contributor(event_id: str, contributor_id: str, host_id: str) -> None:
    """Assigns the contributor as a speaker and opens their backstage join window — called
    from the E2E spec AFTER the host is already live (see create()'s own comment for why
    upfront assignment blocks Go Live via the commercial readiness gate)."""
    db = SessionLocal()
    eid, cid, hid = uuid.UUID(event_id), uuid.UUID(contributor_id), uuid.UUID(host_id)
    ev = db.get(Event, eid)
    db.add(EventAssignment(event_id=eid, user_id=cid, role="speaker"))
    # join_window_error(session, now) passes for state != "removed", no expiry, and either no
    # join window or one that's currently open — leaving start/end unset (open-ended invite)
    # matches test_contributor.py's own test_join_window_error_no_window_at_all.
    db.add(ContributorSession(
        event_id=eid, org_id=ev.org_id, user_id=cid, identity=str(cid),
        state="waiting", invited_at=datetime.now(timezone.utc), invited_by=hid,
    ))
    db.commit()
    print(f"E2E contributor invited: event={event_id} contributor={contributor_id}")


def state(event_id: str) -> None:
    """Prints the real DB-level live state for one event as JSON — the ground truth the E2E
    spec checks against instead of scraping the DOM for something that isn't actually
    rendered anywhere (a UI element proves what a HUMAN would see, not what's really in the
    database; this is the honest, direct check for what the task calls "real recording/
    session state", not a substitute for the UI assertions the spec makes separately)."""
    db = SessionLocal()
    eid = uuid.UUID(event_id)
    ev = db.get(Event, eid)
    open_sessions = db.scalars(
        select(BroadcastSession).where(BroadcastSession.event_id == eid, BroadcastSession.ended_at.is_(None))
    ).all()
    active_recordings = db.scalars(
        select(LiveRecording).where(LiveRecording.event_id == eid, LiveRecording.status.in_(("recording", "paused")))
    ).all()
    print(json.dumps({
        "event_status": ev.status if ev else None,
        "open_broadcast_sessions": len(open_sessions),
        "broadcast_session_status": open_sessions[0].status if open_sessions else None,
        "active_recordings": len(active_recordings),
        "recording_enforced": active_recordings[0].enforced if active_recordings else None,
    }))


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "create":
        create(sys.argv[2])
    elif action == "cleanup":
        cleanup(sys.argv[2])
    elif action == "state":
        state(sys.argv[2])
    elif action == "invite-contributor":
        invite_contributor(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit(
            f"unknown action: {action!r} "
            "(expected 'create', 'cleanup', 'state', or 'invite-contributor')"
        )
