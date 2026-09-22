"""Answering a Q&A question with actual words.

── WHAT WAS MISSING ────────────────────────────────────────────────────────────────────
The console could approve, pin, route to a speaker, dismiss, delete and tick "Answered",
but there was no way to type a reply and nowhere to put one: LiveQuestion had no answer
column and the socket had no action that wrote text. `qa.answer` set `status` and nothing
else, so a question could read "answered" to everyone — the asker included — with no
answer existing anywhere in the system.

`qa.respond` adds the reply. The two invariants worth the most here are:

  1. status and answer are DIFFERENT THINGS, and status is never set on its own by this
     path. A refused or failed respond leaves the question exactly as it was, and a
     question ticked with `qa.answer` still has answer=None, so no surface can mistake the
     tick for a written reply.

  2. the answer goes out on the SAME question.update broadcast every other Q&A change uses
     and comes back in the reconnect snapshot, so viewers get it live and after a refresh
     without a second delivery path.

The bus runs in-process here (REDIS_URL blanked, same reasoning as test_viewer_reactions)
and publish is captured, so the assertions are about what actually reached the wire.
"""
import asyncio
import uuid

import pytest

import app.main  # noqa: F401  - import order; see test_live_socket_connect.py

from app.db import SessionLocal
from app.models import AuditLog, Event, LiveActivity, LiveQuestion, Organization, User
from app.security import hash_password
from app.services import bus
from app.services import moderation as m


@pytest.fixture(autouse=True)
def in_process_bus():
    url = bus.settings.REDIS_URL
    bus.settings.REDIS_URL = ""
    try:
        yield
    finally:
        bus.settings.REDIS_URL = url


@pytest.fixture
def sent(monkeypatch):
    """Every envelope dispatch broadcast, in order."""
    out = []
    real = bus.publish

    async def spy(event_id, channel, type_, data=None):
        out.append((channel, type_, data))
        return await real(event_id, channel, type_, data)

    monkeypatch.setattr(bus, "publish", spy)
    return out


@pytest.fixture
def world():
    """A real event with a real question — _qa_respond writes to the database, so none of
    this can be faked with a bare Ctx the way the reaction tests can."""
    db = SessionLocal()
    made = {"q": [], "ev": [], "u": [], "o": []}
    try:
        org = Organization(name=f"QaCo {uuid.uuid4().hex[:6]}", status="active")
        other = Organization(name=f"QaOther {uuid.uuid4().hex[:6]}", status="active")
        db.add_all([org, other])
        db.flush()
        made["o"] = [org.id, other.id]

        host = User(org_id=org.id, full_name="Vihari Host", role="org_admin", is_active=True,
                    email=f"qa-{uuid.uuid4().hex[:10]}@example.com",
                    username=f"qa{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password("x"), email_verified=True)
        db.add(host)
        db.flush()
        made["u"] = [host.id]

        ev = Event(org_id=org.id, created_by=host.id, title="TEST", status="live",
                   visibility="public", qa_enabled=True)
        db.add(ev)
        db.flush()
        made["ev"] = [ev.id]

        q = LiveQuestion(event_id=ev.id, org_id=org.id, author_name="NAVEEN",
                         text="hey hai", status="approved")
        db.add(q)
        db.flush()
        made["q"] = [q.id]
        db.commit()
        yield {"db": db, "org": org, "other": other, "event": ev, "host": host, "q": q}
    finally:
        try:
            for qid in made["q"]:
                db.execute(LiveQuestion.__table__.delete().where(LiveQuestion.id == qid))
            for eid in made["ev"]:
                db.execute(LiveActivity.__table__.delete().where(LiveActivity.event_id == eid))
                db.execute(LiveQuestion.__table__.delete().where(LiveQuestion.event_id == eid))
                db.execute(AuditLog.__table__.delete().where(AuditLog.target_type == "live_question"))
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


def _ctx(world, *, moderate=False, contribute=False, org=None, name="Vihari Host"):
    return m.Ctx(
        event_id=world["event"].id,
        org_id=(org or world["org"]).id,
        room=f"event_{world['event'].id}",
        user_id=world["host"].id,
        name=name,
        identity=str(world["host"].id),
        role="org_admin" if moderate else "viewer",
        can_moderate=moderate,
        can_host=moderate,
        can_contribute=contribute,
    )


def run(ctx, action, payload):
    return asyncio.run(m.dispatch(ctx, action, payload))


def reload(world):
    world["db"].expire_all()
    return world["db"].get(LiveQuestion, world["q"].id)


HOST = "The room opens at nine, and the recording goes out on Friday."


# ── 2 & 3. a host can submit an answer, and it persists ────────────────────────────────

def test_a_host_answer_is_written_to_the_question(world, sent):
    assert run(_ctx(world, moderate=True), "qa.respond",
               {"id": str(world["q"].id), "answer": HOST}) is None

    q = reload(world)
    assert q.answer_text == HOST
    assert q.answered_at is not None
    assert q.answered_by == world["host"].id
    assert q.answered_by_name == "Vihari Host"


def test_answering_marks_the_question_answered(world, sent):
    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": HOST})
    assert reload(world).status == "answered"


def test_the_answer_is_trimmed_but_otherwise_verbatim(world, sent):
    run(_ctx(world, moderate=True), "qa.respond",
        {"id": str(world["q"].id), "answer": f"  {HOST}\n"})
    assert reload(world).answer_text == HOST


def test_an_overlong_answer_is_capped_rather_than_rejected(world, sent):
    run(_ctx(world, moderate=True), "qa.respond",
        {"id": str(world["q"].id), "answer": "x" * (m.ANSWER_LIMIT + 500)})
    assert len(reload(world).answer_text) == m.ANSWER_LIMIT


# ── 4. the viewer's side: it goes out on the wire and survives a reconnect ─────────────

def test_the_answer_is_broadcast_to_everyone_on_the_event(world, sent):
    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": HOST})

    qa = [e for e in sent if e[0] == "qa"]
    assert len(qa) == 1
    channel, type_, data = qa[0]
    assert type_ == "question.update"        # the envelope both clients already reduce
    assert data["answer"] == HOST
    assert data["status"] == "answered"
    assert data["answered_by_name"] == "Vihari Host"


def test_a_reconnecting_viewer_still_sees_the_answer(world, sent):
    """The snapshot is what a refreshed page gets, so an answer that only ever existed in a
    live broadcast would vanish on reload."""
    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": HOST})

    snap = asyncio.run(m.tx(lambda db: m._snapshot(db, _ctx(world, moderate=False))))
    mine = [q for q in snap["questions"] if q["id"] == str(world["q"].id)]
    assert mine and mine[0]["answer"] == HOST


def test_the_serializer_reports_no_answer_as_none_not_empty_string(world):
    out = m.question_out(world["q"])
    assert out["answer"] is None
    assert out["answered_at"] is None


# ── 5. nothing is marked answered unless an answer was actually saved ──────────────────

def test_an_empty_answer_is_refused_and_changes_nothing(world, sent):
    """Pressing send on a blank box must not become a second, quieter "mark answered"."""
    before = reload(world).status
    err = run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": ""})

    assert err == "An answer needs some text."
    q = reload(world)
    assert q.answer_text is None
    assert q.status == before
    assert sent == []


def test_a_whitespace_only_answer_is_refused_too(world, sent):
    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": "   \n\t "})
    q = reload(world)
    assert (q.answer_text, q.status) == (None, "approved")


def test_a_missing_answer_field_is_refused(world, sent):
    assert run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id)})
    assert reload(world).status == "approved"


def test_an_unknown_question_id_answers_nothing(world, sent):
    err = run(_ctx(world, moderate=True), "qa.respond",
              {"id": str(uuid.uuid4()), "answer": HOST})
    assert err == "That question no longer exists."
    assert reload(world).answer_text is None
    assert sent == []


def test_a_rejected_answer_leaves_the_status_untouched_even_when_already_answered(world, sent):
    """The nastiest version of the same rule: a question that was ticked answered, then a
    failed respond. The tick must not be upgraded into a written answer."""
    run(_ctx(world, moderate=True), "qa.answer", {"id": str(world["q"].id)})
    sent.clear()

    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": ""})

    q = reload(world)
    assert q.status == "answered"
    assert q.answer_text is None


# ── the tick and the answer are different things ───────────────────────────────────────

def test_the_answered_tick_still_works_and_still_writes_no_text(world, sent):
    """qa.answer is deliberately unchanged. It is a moderator's "dealt with" marker — for a
    question answered out loud — and the clients render the answer body from `answer`, so
    this leaves nothing for them to show."""
    assert run(_ctx(world, moderate=True), "qa.answer", {"id": str(world["q"].id)}) is None

    q = reload(world)
    assert q.status == "answered"
    assert q.answer_text is None
    assert m.question_out(q)["answer"] is None


def test_a_written_answer_survives_a_later_tick(world, sent):
    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": HOST})
    run(_ctx(world, moderate=True), "qa.answer", {"id": str(world["q"].id)})
    assert reload(world).answer_text == HOST


# ── 6-adjacent: editing ────────────────────────────────────────────────────────────────

def test_a_second_answer_edits_the_first_rather_than_duplicating_it(world, sent):
    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": HOST})
    first_at = reload(world).answered_at

    run(_ctx(world, moderate=True), "qa.respond",
        {"id": str(world["q"].id), "answer": "Correction: eight, not nine."})

    q = reload(world)
    assert q.answer_text == "Correction: eight, not nine."
    assert q.answered_at >= first_at
    assert world["db"].query(LiveQuestion).filter(
        LiveQuestion.event_id == world["event"].id).count() == 1


def test_an_edit_is_audited_as_an_edit(world, sent):
    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": HOST})
    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": "Again."})

    lines = [a.text for a in world["db"].query(LiveActivity).filter(
        LiveActivity.event_id == world["event"].id, LiveActivity.kind == "qa").all()]
    assert any(t.startswith("Answered a question") for t in lines)
    assert any(t.startswith("Edited the answer to") for t in lines)


def test_answering_is_audited(world, sent):
    run(_ctx(world, moderate=True), "qa.respond", {"id": str(world["q"].id), "answer": HOST})

    rows = world["db"].query(AuditLog).filter(
        AuditLog.action == "live.question.respond",
        AuditLog.target_id == str(world["q"].id),
    ).all()
    assert len(rows) == 1


# ── permissions ────────────────────────────────────────────────────────────────────────

def test_a_plain_viewer_cannot_answer(world, sent):
    """The gate that matters. qa.respond sits in VIEWER_ACTIONS only to get past the
    dispatcher's blanket can_moderate check so a speaker can reach it — which makes
    _qa_respond's own first line the only thing standing in front of the audience."""
    err = run(_ctx(world, moderate=False, contribute=False), "qa.respond",
              {"id": str(world["q"].id), "answer": "I'll take this one."})

    assert err == "You are not authorized to answer questions in this event"
    q = reload(world)
    assert q.answer_text is None
    assert q.status == "approved"
    assert sent == []


def test_an_assigned_speaker_can_answer(world, sent):
    """A question routed to a speaker (qa.assign already exists) is answerable by them."""
    assert run(_ctx(world, moderate=False, contribute=True, name="Speaker Sam"),
               "qa.respond", {"id": str(world["q"].id), "answer": HOST}) is None

    q = reload(world)
    assert q.answer_text == HOST
    assert q.answered_by_name == "Speaker Sam"


def test_another_organization_cannot_answer_this_question(world, sent):
    """_row scopes on event_id AND org_id, so a moderator elsewhere gets the not-found
    path rather than write access."""
    err = run(_ctx(world, moderate=True, org=world["other"]), "qa.respond",
              {"id": str(world["q"].id), "answer": "Not mine to answer."})

    assert err == "That question no longer exists."
    assert reload(world).answer_text is None


def test_the_action_is_registered_and_not_host_only(world):
    assert m.ACTIONS["qa.respond"] is m._qa_respond
    assert "qa.respond" not in m.HOST_ONLY
    # Still present, still separate.
    assert "qa.answer" in m.ACTIONS


# ── nothing else moved ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("action,status", [
    ("qa.approve", "approved"), ("qa.dismiss", "dismissed"), ("qa.answer", "answered"),
])
def test_the_existing_moderation_actions_are_untouched(world, sent, action, status):
    assert run(_ctx(world, moderate=True), action, {"id": str(world["q"].id)}) is None
    assert reload(world).status == status


def test_pin_and_delete_still_work(world, sent):
    qid = world["q"].id                      # read before the row goes away
    run(_ctx(world, moderate=True), "qa.pin", {"id": str(qid)})
    assert reload(world).pinned is True

    run(_ctx(world, moderate=True), "qa.delete", {"id": str(qid)})
    world["db"].expunge_all()                # not expire_all: the instance no longer exists
    assert world["db"].query(LiveQuestion).filter(LiveQuestion.id == qid).count() == 0


def test_upvoting_still_works(world, sent):
    ctx = _ctx(world, moderate=False)
    run(ctx, "qa.vote", {"id": str(world["q"].id)})
    assert reload(world).votes == 1
