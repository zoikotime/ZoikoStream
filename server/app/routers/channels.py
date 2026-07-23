from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import get_db
from app.models import Channel, User
from app.security import get_current_user
from app.schemas import ChannelCreate, ChannelResponse


router = APIRouter(
    prefix="/channels",
    tags=["Channels"],
)


# CREATE CHANNEL
@router.post(
    "/",
    response_model=ChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_channel(
    data: ChannelCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):

    slug = data.slug.strip().lower()

    existing = db.scalar(
        select(Channel).where(Channel.slug == slug)
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Channel slug already exists",
        )

    channel = Channel(
        owner_id=user.id,
        name=data.name,
        slug=slug,
        description=data.description,
        category=data.category,
    )

    db.add(channel)

    try:
        db.commit()
    except IntegrityError:
        # Precheck above is racy under concurrent requests -- the unique constraint is
        # the real guard. Without this, a collision here is an uncaught 500 instead of
        # the same clean 409 as the precheck.
        db.rollback()
        raise HTTPException(status_code=409, detail="Channel slug already exists")

    db.refresh(channel)

    return channel



# GET ALL CHANNELS
@router.get(
    "/",
    response_model=list[ChannelResponse],
)
def get_channels(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):

    channels = db.scalars(
        select(Channel)
        .where(Channel.owner_id == user.id)
    ).all()

    return channels



# GET SINGLE CHANNEL
@router.get(
    "/{channel_id}",
    response_model=ChannelResponse,
)
def get_channel(
    channel_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):

    channel = db.scalar(
        select(Channel)
        .where(
            Channel.id == channel_id,
            Channel.owner_id == user.id
        )
    )

    if not channel:
        raise HTTPException(
            status_code=404,
            detail="Channel not found",
        )

    return channel



# UPDATE CHANNEL
@router.put(
    "/{channel_id}",
    response_model=ChannelResponse,
)
def update_channel(
    channel_id: str,
    data: ChannelCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):

    channel = db.scalar(
        select(Channel)
        .where(
            Channel.id == channel_id,
            Channel.owner_id == user.id
        )
    )

    if not channel:
        raise HTTPException(
            status_code=404,
            detail="Channel not found",
        )


    channel.name = data.name
    channel.slug = data.slug.strip().lower()
    channel.description = data.description
    channel.category = data.category

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Channel slug already exists")

    db.refresh(channel)

    return channel



# DELETE CHANNEL
@router.delete(
    "/{channel_id}",
)
def delete_channel(
    channel_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):

    channel = db.scalar(
        select(Channel)
        .where(
            Channel.id == channel_id,
            Channel.owner_id == user.id
        )
    )

    if not channel:
        raise HTTPException(
            status_code=404,
            detail="Channel not found",
        )


    db.delete(channel)
    db.commit()


    return {
        "message": "Channel deleted successfully"
    }