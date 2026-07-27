from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import User
from app.models.poll import Poll, PollOption, PollVote
from app.models.stream import Stream
from app.schemas.poll import PollCreateIn, PollOut
from app.security import get_current_user, get_optional_user
from app.services.stage import MANAGER_ROLES, resolve_identity
from app.sockets import room_for, serialize_poll, sio

router = APIRouter(prefix="/streams/{stream_id}/polls", tags=["Polls"])


def _require_moderator(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(403, "Only org admins, hosts, and moderators can manage polls")


def _get_stream_in_org(db: Session, user: User, stream_id: str) -> Stream:
    stream = db.scalar(select(Stream).where(Stream.id == stream_id, Stream.org_id == user.org_id))
    if not stream:
        raise HTTPException(404, "Event not found")
    return stream


def _get_poll(db: Session, stream_id: str, poll_id: str) -> Poll:
    poll = db.scalar(
        select(Poll)
        .options(selectinload(Poll.options))
        .where(Poll.id == poll_id, Poll.stream_id == stream_id)
    )
    if not poll:
        raise HTTPException(404, "Poll not found")
    return poll


# LIST POLLS -- public, gated the same way as GET /streams/{id}. Same voted_option_id
# caveat as Q&A's voted_by_me (schemas/poll.py): only known for logged-in/registered callers.
@router.get("", response_model=list[PollOut])
def list_polls(
    stream_id: str,
    email: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    stream = db.get(Stream, stream_id)
    if not stream or not stream.visible_to(user):
        raise HTTPException(404, "Event not found")

    polls = db.scalars(
        select(Poll)
        .options(selectinload(Poll.options))
        .where(Poll.stream_id == stream_id)
        .order_by(Poll.created_at.desc())
    ).all()

    voter_key = resolve_identity(user, email) if (user or email) else None
    my_votes = {}
    if voter_key and polls:
        rows = db.execute(
            select(PollVote.poll_id, PollVote.option_id).where(
                PollVote.voter_key == voter_key,
                PollVote.poll_id.in_([p.id for p in polls]),
            )
        ).all()
        my_votes = {poll_id: option_id for poll_id, option_id in rows}

    out = []
    for p in polls:
        item = PollOut.model_validate(p)
        item.voted_option_id = my_votes.get(p.id)
        out.append(item)
    return out


# CREATE POLL
@router.post("", response_model=PollOut, status_code=201)
async def create_poll(
    stream_id: str,
    data: PollCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)

    poll = Poll(stream_id=stream_id, question=data.question)
    db.add(poll)
    db.flush()  # assign poll.id before options reference it
    for i, label in enumerate(data.options):
        db.add(PollOption(poll_id=poll.id, label=label.strip()[:160], position=i))
    db.commit()

    poll = _get_poll(db, stream_id, str(poll.id))
    await sio.emit("poll:new", serialize_poll(poll), room=room_for(stream_id))
    return poll


# CLOSE POLL
@router.post("/{poll_id}/close", response_model=PollOut)
async def close_poll(
    stream_id: str,
    poll_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)
    poll = _get_poll(db, stream_id, poll_id)

    poll.is_closed = True
    db.commit()
    db.refresh(poll)

    await sio.emit("poll:updated", serialize_poll(poll), room=room_for(stream_id))
    return poll


# DELETE POLL
@router.delete("/{poll_id}")
async def delete_poll(
    stream_id: str,
    poll_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)
    poll = _get_poll(db, stream_id, poll_id)

    db.delete(poll)
    db.commit()

    await sio.emit("poll:deleted", {"id": str(poll_id)}, room=room_for(stream_id))
    return {"message": "Poll deleted"}
