"""DB access for the channels API. Pure queries + partial updates, no HTTP."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Channel


def slug_taken(db: Session, slug: str, exclude_id: uuid.UUID | None = None) -> bool:
    stmt = select(Channel.id).where(func.lower(Channel.slug) == slug.lower())
    if exclude_id is not None:
        stmt = stmt.where(Channel.id != exclude_id)
    return db.scalar(stmt) is not None


def create_channel(db: Session, owner_id: uuid.UUID, data) -> Channel:
    channel = Channel(
        owner_id=owner_id,
        name=data.name,
        slug=data.slug.lower(),
        description=data.description,
        category=data.category,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


def list_channels(db: Session, owner_id: uuid.UUID) -> list[Channel]:
    return list(db.scalars(select(Channel).where(Channel.owner_id == owner_id)))


def get_owned_channel(db: Session, channel_id: uuid.UUID, owner_id: uuid.UUID) -> Channel | None:
    return db.scalar(select(Channel).where(Channel.id == channel_id, Channel.owner_id == owner_id))


def update_channel(db: Session, channel: Channel, data) -> Channel:
    channel.name = data.name
    channel.slug = data.slug.lower()
    channel.description = data.description
    channel.category = data.category
    db.commit()
    db.refresh(channel)
    return channel


def delete_channel(db: Session, channel: Channel) -> None:
    db.delete(channel)
    db.commit()
