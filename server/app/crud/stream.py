import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.stream import Stream


def get_owned_channel(
    db: Session,
    channel_id,
    owner_id,
) -> Channel | None:
    return db.scalar(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.owner_id == owner_id
        )
    )


def create_stream(db: Session, data) -> Stream:
    stream = Stream(
        channel_id=data.channel_id,
        title=data.title,
        description=data.description,
        category=data.category,
        stream_key=secrets.token_urlsafe(32)
    )

    db.add(stream)
    db.commit()
    db.refresh(stream)

    return stream


def get_stream(db, stream_id) -> Stream | None:
    return db.scalar(
        select(Stream).where(Stream.id == stream_id)
    )


def get_owned_stream(db, stream_id, owner_id) -> Stream | None:
    return db.scalar(
        select(Stream)
        .join(Channel)
        .where(
            Stream.id == stream_id,
            Channel.owner_id == owner_id,
        )
    )


def list_streams(
    db: Session,
    page: int,
    limit: int,
    search: str | None = None,
) -> tuple[list[Stream], int]:

    query = select(Stream).order_by(Stream.created_at.desc())

    if search:
        query = query.where(
            Stream.title.ilike(f"%{search}%")
        )

    total = db.scalar(
        select(func.count()).select_from(query.subquery())
    )

    streams = db.scalars(
        query.offset((page-1)*limit).limit(limit)
    ).all()

    return streams, total

def update_stream(
    db: Session,
    stream: Stream,
    data,
) -> Stream:

    if data.title is not None:
        stream.title = data.title

    if data.description is not None:
        stream.description = data.description

    if data.category is not None:
        stream.category = data.category

    if data.thumbnail_url is not None:
        stream.thumbnail_url = data.thumbnail_url

    db.commit()
    db.refresh(stream)

    return stream

def delete_stream(
    db: Session,
    stream: Stream,
) -> None:
    db.delete(stream)
    db.commit()

def start_stream(
        db: Session,
        stream: Stream
) -> Stream:

    stream.is_live = True
    stream.started_at = datetime.now(timezone.utc)
    stream.livekit_room = f"stream_{stream.id}"

    db.commit()
    db.refresh(stream)

    return stream   

def stop_stream(
    db: Session,
    stream: Stream
) -> Stream:

    stream.is_live = False
    stream.ended_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(stream)

    return stream