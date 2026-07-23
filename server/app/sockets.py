"""Real-time layer for live chat. Mounted alongside the FastAPI app in main.py.

Socket.IO events (client -> server):
  "join"      {stream_id, token?, display_name?, email?} -> {history: [...]} | {error}
              token identifies a logged-in user; guests pass display_name instead.
              email is only needed for registration_required events -- a guest who
              registered proves it this way; a logged-in caller is checked against
              their own account email instead (see services/registration.py).
  "chat:send" {text} -> {ok: true} | {error}
              uses the identity established by "join" (stored in the socket session).

Events (server -> room "stream:{id}"):
  "chat:new"     a freshly sent message
  "chat:updated" a message's pinned/flagged state changed (moderation, via REST)
  "chat:deleted" {id} a message was removed (moderation, via REST)
"""
import socketio
from jose import JWTError, jwt
from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import User
from .models.chat import ChatMessage
from .models.stream import Stream
from .schemas.chat import ChatMessageOut
from .security import ALGORITHM
from .services.registration import is_registered

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
)

HISTORY_LIMIT = 50


def room_for(stream_id) -> str:
    return f"stream:{stream_id}"


def serialize_message(message: ChatMessage) -> dict:
    return ChatMessageOut.model_validate(message).model_dump(mode="json")


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

    await sio.save_session(sid, {"stream_id": str(stream_id), **identity})
    await sio.enter_room(sid, room_for(stream_id))
    return {"history": [serialize_message(m) for m in reversed(recent)]}


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
