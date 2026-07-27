"""Real-time layer for live chat, Q&A, polls, the on-stage speaker flow, and live
viewer presence. Mounted alongside the FastAPI app in main.py.

Socket.IO events (client -> server):
  "join"            {stream_id, token?, display_name?, email?} -> {history, stage, qa, polls, viewers} | {error}
                    token identifies a logged-in user; guests pass display_name instead.
                    email is only needed for registration_required events -- a guest who
                    registered proves it this way; a logged-in caller is checked against
                    their own account email instead (see services/registration.py).
  "chat:send"       {text} -> {ok: true} | {error}
                    uses the identity established by "join" (stored in the socket session).
  "stage:raise_hand"  {} -> {ok: true} | {error} -- add self to the raised-hand queue.
  "stage:lower_hand"  {} -> {ok: true} | {error} -- withdraw a raised hand.
  "qa:ask"          {text} -> {ok: true} | {error} -- ask a question.
  "qa:vote"         {question_id} -> {ok: true, voted: bool} | {error} -- toggle an upvote.
  "poll:vote"       {poll_id, option_id} -> {ok: true} | {error} -- vote (or change vote).

Events (server -> room "stream:{id}"):
  "chat:new"     a freshly sent message
  "chat:updated" a message's pinned/flagged state changed (moderation, via REST)
  "chat:deleted" {id} a message was removed (moderation, via REST)
  "qa:new"       a freshly asked question
  "qa:updated"   a question's vote count or answered state changed
  "qa:deleted"   {id} a question was removed (moderation, via REST)
  "poll:new"     a poll was created (via REST)
  "poll:updated" a poll's vote counts or closed state changed
  "poll:deleted" {id} a poll was removed (via REST)
  "viewers:count" {count} the live-viewer count changed (see services/presence.py)

Events (server -> room "stage:{id}", managers only -- see services/stage.py):
  "stage:hands"  the current raised-hand queue changed
  "stage:roster" the current on-stage speaker roster changed (also via REST promote/demote)
"""
import socketio
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .config import settings
from .db import SessionLocal
from .models import User
from .models.chat import ChatMessage
from .models.poll import Poll, PollOption, PollVote
from .models.qa import QaQuestion, QaVote
from .models.stream import Stream
from .schemas.chat import ChatMessageOut
from .schemas.poll import PollOut
from .schemas.qa import QaQuestionOut
from .security import ALGORITHM
from .services import presence
from .services.registration import is_registered
from .services.stage import HAND_QUEUE, MANAGER_ROLES, resolve_identity, stage_room_for, stage_snapshot

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
)

HISTORY_LIMIT = 50


def room_for(stream_id) -> str:
    return f"stream:{stream_id}"


def serialize_message(message: ChatMessage) -> dict:
    return ChatMessageOut.model_validate(message).model_dump(mode="json")


def serialize_question(question: QaQuestion) -> dict:
    return QaQuestionOut.model_validate(question).model_dump(mode="json")


def serialize_poll(poll: Poll) -> dict:
    return PollOut.model_validate(poll).model_dump(mode="json")


def _identify_user(token: str | None, db) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user = db.get(User, payload["sub"])
    except (JWTError, KeyError):
        return None
    return user if user and user.is_active else None


@sio.on("join")
async def join(sid, data):
    data = data or {}
    stream_id = data.get("stream_id")
    if not stream_id:
        return {"error": "stream_id is required"}

    with SessionLocal() as db:
        stream = db.get(Stream, stream_id)
        user = _identify_user(data.get("token"), db)
        if not stream or not stream.visible_to(user):
            return {"error": "Event not found"}

        if not is_registered(db, stream, user, data.get("email")):
            return {"error": "This event requires registration before you can join"}

        if user:
            identity = {"user_id": str(user.id), "display_name": user.full_name}
        else:
            display_name = (data.get("display_name") or "").strip()[:60]
            if not display_name:
                return {"error": "display_name is required for guests"}
            identity = {"user_id": None, "display_name": display_name}

        recent = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.stream_id == stream_id, ChatMessage.is_deleted.is_(False))
            .order_by(ChatMessage.created_at.desc())
            .limit(HISTORY_LIMIT)
        ).all()

        questions = db.scalars(
            select(QaQuestion)
            .where(QaQuestion.stream_id == stream_id, QaQuestion.is_deleted.is_(False))
            .order_by(QaQuestion.votes.desc(), QaQuestion.created_at.asc())
        ).all()

        polls = db.scalars(
            select(Poll)
            .options(selectinload(Poll.options))
            .where(Poll.stream_id == stream_id)
            .order_by(Poll.created_at.desc())
        ).all()

        stage_identity = resolve_identity(user, data.get("email"))
        is_manager = bool(user) and user.role in MANAGER_ROLES and user.org_id == stream.org_id

        my_qa_votes = set(
            db.scalars(
                select(QaVote.question_id).where(
                    QaVote.voter_key == stage_identity,
                    QaVote.question_id.in_([q.id for q in questions]),
                )
            ).all()
        ) if questions else set()

        my_poll_votes = dict(
            db.execute(
                select(PollVote.poll_id, PollVote.option_id).where(
                    PollVote.voter_key == stage_identity,
                    PollVote.poll_id.in_([p.id for p in polls]),
                )
            ).all()
        ) if polls else {}

    session = {
        "stream_id": str(stream_id),
        "stage_identity": stage_identity,
        "is_manager": is_manager,
        "counted_viewer": False,
        **identity,
    }
    await sio.save_session(sid, session)
    await sio.enter_room(sid, room_for(stream_id))

    if is_manager:
        await sio.enter_room(sid, stage_room_for(stream_id))
        viewers = presence.viewer_count(str(stream_id))
    else:
        # Managers watching from the studio/dashboard aren't "audience" -- see
        # services/presence.py. A viewer may hold more than one socket (chat panel +
        # video player today); presence dedupes by identity, so only the first one
        # bumps the broadcast count.
        session["counted_viewer"] = True
        await sio.save_session(sid, session)
        viewers = presence.add_viewer(str(stream_id), stage_identity, sid)
        await sio.emit("viewers:count", {"count": viewers}, room=room_for(stream_id))

    qa_out = []
    for q in questions:
        item = QaQuestionOut.model_validate(q)
        item.voted_by_me = q.id in my_qa_votes
        qa_out.append(item.model_dump(mode="json"))

    polls_out = []
    for p in polls:
        item = PollOut.model_validate(p)
        item.voted_option_id = my_poll_votes.get(p.id)
        polls_out.append(item.model_dump(mode="json"))

    return {
        "history": [serialize_message(m) for m in reversed(recent)],
        "stage": stage_snapshot(str(stream_id)),
        "qa": qa_out,
        "polls": polls_out,
        "viewers": viewers,
    }


@sio.on("stage:raise_hand")
async def raise_hand(sid, _data=None):
    session = await sio.get_session(sid)
    if not session or "stream_id" not in session:
        return {"error": "Join a stream first"}

    stream_id = session["stream_id"]
    HAND_QUEUE.setdefault(stream_id, {})[session["stage_identity"]] = session["display_name"]

    await sio.emit("stage:hands", stage_snapshot(stream_id)["hands"], room=stage_room_for(stream_id))
    return {"ok": True}


@sio.on("stage:lower_hand")
async def lower_hand(sid, _data=None):
    session = await sio.get_session(sid)
    if not session or "stream_id" not in session:
        return {"error": "Join a stream first"}

    stream_id = session["stream_id"]
    HAND_QUEUE.get(stream_id, {}).pop(session["stage_identity"], None)

    await sio.emit("stage:hands", stage_snapshot(stream_id)["hands"], room=stage_room_for(stream_id))
    return {"ok": True}


@sio.on("disconnect")
async def disconnect(sid):
    session = await sio.get_session(sid)
    if not session or "stream_id" not in session:
        return
    stream_id = session["stream_id"]

    # Best-effort: a dropped connection shouldn't leave a stale hand raised. Demote
    # (ON_STAGE) deliberately stays untouched -- a network blip shouldn't pull someone
    # off stage; that's an explicit host action.
    if HAND_QUEUE.get(stream_id, {}).pop(session.get("stage_identity"), None) is not None:
        await sio.emit("stage:hands", stage_snapshot(stream_id)["hands"], room=stage_room_for(stream_id))

    if session.get("counted_viewer"):
        viewers = presence.remove_viewer(stream_id, session["stage_identity"], sid)
        await sio.emit("viewers:count", {"count": viewers}, room=room_for(stream_id))


@sio.on("chat:send")
async def chat_send(sid, data):
    session = await sio.get_session(sid)
    if not session or "stream_id" not in session:
        return {"error": "Join a stream first"}

    text = ((data or {}).get("text") or "").strip()[:500]
    if not text:
        return {"error": "text is required"}

    with SessionLocal() as db:
        message = ChatMessage(
            stream_id=session["stream_id"],
            user_id=session.get("user_id"),
            display_name=session["display_name"],
            text=text,
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        payload = serialize_message(message)

    await sio.emit("chat:new", payload, room=room_for(session["stream_id"]))
    return {"ok": True}


@sio.on("qa:ask")
async def qa_ask(sid, data):
    session = await sio.get_session(sid)
    if not session or "stream_id" not in session:
        return {"error": "Join a stream first"}

    text = ((data or {}).get("text") or "").strip()[:500]
    if not text:
        return {"error": "text is required"}

    with SessionLocal() as db:
        question = QaQuestion(
            stream_id=session["stream_id"],
            user_id=session.get("user_id"),
            display_name=session["display_name"],
            text=text,
        )
        db.add(question)
        db.commit()
        db.refresh(question)
        payload = serialize_question(question)

    await sio.emit("qa:new", payload, room=room_for(session["stream_id"]))
    return {"ok": True}


@sio.on("qa:vote")
async def qa_vote(sid, data):
    session = await sio.get_session(sid)
    if not session or "stream_id" not in session:
        return {"error": "Join a stream first"}

    question_id = (data or {}).get("question_id")
    if not question_id:
        return {"error": "question_id is required"}

    voter_key = session["stage_identity"]

    with SessionLocal() as db:
        question = db.scalar(
            select(QaQuestion).where(QaQuestion.id == question_id, QaQuestion.stream_id == session["stream_id"])
        )
        if not question:
            return {"error": "Question not found"}

        existing = db.scalar(
            select(QaVote).where(QaVote.question_id == question_id, QaVote.voter_key == voter_key)
        )
        if existing:
            db.delete(existing)
            question.votes = max(0, question.votes - 1)
            voted = False
        else:
            db.add(QaVote(question_id=question_id, voter_key=voter_key))
            question.votes += 1
            voted = True

        db.commit()
        db.refresh(question)
        payload = serialize_question(question)

    await sio.emit("qa:updated", payload, room=room_for(session["stream_id"]))
    return {"ok": True, "voted": voted}


@sio.on("poll:vote")
async def poll_vote(sid, data):
    session = await sio.get_session(sid)
    if not session or "stream_id" not in session:
        return {"error": "Join a stream first"}

    data = data or {}
    poll_id = data.get("poll_id")
    option_id = data.get("option_id")
    if not poll_id or not option_id:
        return {"error": "poll_id and option_id are required"}

    voter_key = session["stage_identity"]

    with SessionLocal() as db:
        poll = db.scalar(
            select(Poll)
            .options(selectinload(Poll.options))
            .where(Poll.id == poll_id, Poll.stream_id == session["stream_id"])
        )
        if not poll:
            return {"error": "Poll not found"}
        if poll.is_closed:
            return {"error": "This poll is closed"}

        option = next((o for o in poll.options if str(o.id) == str(option_id)), None)
        if not option:
            return {"error": "Option not found"}

        existing = db.scalar(select(PollVote).where(PollVote.poll_id == poll_id, PollVote.voter_key == voter_key))
        if existing and str(existing.option_id) == str(option_id):
            pass  # already voted for this option -- no-op
        else:
            if existing:
                old_option = next((o for o in poll.options if o.id == existing.option_id), None)
                if old_option:
                    old_option.votes = max(0, old_option.votes - 1)
                existing.option_id = option_id
            else:
                db.add(PollVote(poll_id=poll_id, option_id=option_id, voter_key=voter_key))
            option.votes += 1
            db.commit()

        db.refresh(poll)
        payload = serialize_poll(poll)

    await sio.emit("poll:updated", payload, room=room_for(session["stream_id"]))
    return {"ok": True}
