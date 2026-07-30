"""DB access for the streams API. Pure queries + partial updates, no HTTP.
Mirrors crud/event.py and crud/organization.py.

Ownership note: streams reach their owner through their channel (streams have no org_id of
their own), so every owner-scoped read joins Channel. That join IS the isolation boundary
for this subsystem — see the module docstring in routers/streams.py.
"""

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Channel, Stream

STREAM_KEY_BYTES = 32


def owned_channel(db: Session, channel_id: uuid.UUID, owner_id: uuid.UUID) -> Channel | None:
    return db.scalar(select(Channel).where(Channel.id == channel_id, Channel.owner_id == owner_id))


def create_stream(db: Session, channel_id: uuid.UUID, data) -> Stream:
    stream = Stream(
        channel_id=channel_id,
        title=data.title,
        description=data.description,
        category=data.category,
        stream_key=secrets.token_urlsafe(STREAM_KEY_BYTES),
    )
    db.add(stream)
    db.commit()
    db.refresh(stream)
    return stream


def list_streams(db: Session, owner_id: uuid.UUID, search: str | None = None,
                 page: int = 1, limit: int = 10) -> tuple[list[Stream], int]:
    """Streams belonging to this caller's channels. Never platform-wide."""
    stmt = (
        select(Stream)
        .join(Channel, Stream.channel_id == Channel.id)
        .where(Channel.owner_id == owner_id)
        .order_by(Stream.created_at.desc())
    )
    if search:
        stmt = stmt.where(Stream.title.ilike(f"%{search}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.offset((page - 1) * limit).limit(limit)).all()
    return list(items), total


def get_owned_stream(db: Session, stream_id: uuid.UUID, owner_id: uuid.UUID) -> Stream | None:
    """One stream, only if the caller owns its channel. Used by every read that returns
    `stream_key` and by every mutation."""
    return db.scalar(
        select(Stream)
        .join(Channel, Stream.channel_id == Channel.id)
        .where(Stream.id == stream_id, Channel.owner_id == owner_id)
    )


def get_stream(db: Session, stream_id: uuid.UUID) -> Stream | None:
    """Unscoped lookup. Only for flows that must resolve a stream the caller does not own
    (the viewer token); never use it for a response carrying `stream_key`."""
    return db.scalar(select(Stream).where(Stream.id == stream_id))


def update_stream(db: Session, stream: Stream, data) -> Stream:
    for field in ("title", "description", "category", "thumbnail_url"):
        value = getattr(data, field, None)
        if value is not None:
            setattr(stream, field, value)
    db.commit()
    db.refresh(stream)
    return stream


def delete_stream(db: Session, stream: Stream) -> None:
    db.delete(stream)
    db.commit()


def start_stream(db: Session, stream: Stream) -> Stream:
    stream.is_live = True
    stream.started_at = datetime.now(timezone.utc)
    stream.livekit_room = f"stream_{stream.id}"
    db.commit()
    db.refresh(stream)
    return stream


def stop_stream(db: Session, stream: Stream) -> Stream:
    stream.is_live = False
    stream.ended_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(stream)
    return stream
