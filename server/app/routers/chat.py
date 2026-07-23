from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.models.chat import ChatMessage
from app.models.stream import Stream
from app.schemas.chat import ChatMessageOut
from app.security import get_current_user
from app.sockets import room_for, serialize_message, sio

router = APIRouter(prefix="/streams/{stream_id}/messages", tags=["Chat"])

MODERATOR_ROLES = ("org_admin", "host", "moderator")


def _require_moderator(user: User) -> None:
    if user.role not in MODERATOR_ROLES:
        raise HTTPException(403, "Only org admins, hosts, and moderators can moderate chat")


def _get_stream_in_org(db: Session, user: User, stream_id: str) -> Stream:
    stream = db.scalar(select(Stream).where(Stream.id == stream_id, Stream.org_id == user.org_id))
    if not stream:
        raise HTTPException(404, "Event not found")
    return stream


def _get_message(db: Session, stream_id: str, message_id: str) -> ChatMessage:
    message = db.scalar(select(ChatMessage).where(ChatMessage.id == message_id, ChatMessage.stream_id == stream_id))
    if not message:
        raise HTTPException(404, "Message not found")
    return message


# GET MESSAGE HISTORY
# ponytail: public, same as GET /streams/{id} -- no visibility check yet either (see
# that endpoint's note). A socket "join" also returns history; this is the REST/polling
# fallback for clients that aren't using the socket layer.
@router.get("", response_model=list[ChatMessageOut])
def get_messages(stream_id: str, limit: int = 50, db: Session = Depends(get_db)):
    if not db.get(Stream, stream_id):
        raise HTTPException(404, "Event not found")

    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.stream_id == stream_id, ChatMessage.is_deleted.is_(False))
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    ).all()
    return list(reversed(messages))


# PIN MESSAGE
@router.post("/{message_id}/pin", response_model=ChatMessageOut)
async def pin_message(
    stream_id: str,
    message_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)
    message = _get_message(db, stream_id, message_id)

    message.pinned = True
    db.commit()
    db.refresh(message)

    await sio.emit("chat:updated", serialize_message(message), room=room_for(stream_id))
    return message


# UNPIN MESSAGE
@router.delete("/{message_id}/pin", response_model=ChatMessageOut)
async def unpin_message(
    stream_id: str,
    message_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)
    message = _get_message(db, stream_id, message_id)

    message.pinned = False
    db.commit()
    db.refresh(message)

    await sio.emit("chat:updated", serialize_message(message), room=room_for(stream_id))
    return message


# FLAG MESSAGE
@router.post("/{message_id}/flag", response_model=ChatMessageOut)
async def flag_message(
    stream_id: str,
    message_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)
    message = _get_message(db, stream_id, message_id)

    message.flagged = True
    db.commit()
    db.refresh(message)

    await sio.emit("chat:updated", serialize_message(message), room=room_for(stream_id))
    return message


# DELETE MESSAGE (soft delete)
@router.delete("/{message_id}")
async def delete_message(
    stream_id: str,
    message_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_moderator(user)
    _get_stream_in_org(db, user, stream_id)
    message = _get_message(db, stream_id, message_id)

    message.is_deleted = True
    db.commit()

    await sio.emit("chat:deleted", {"id": str(message.id)}, room=room_for(stream_id))
    return {"message": "Message deleted"}
