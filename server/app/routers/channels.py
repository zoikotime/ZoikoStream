from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

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

    existing = db.scalar(
        select(Channel).where(Channel.slug == data.slug)
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Channel slug already exists",
        )

    channel = Channel(
        owner_id=user.id,
        name=data.name,
        slug=data.slug.lower(),
        description=data.description,
        category=data.category,
    )

    db.add(channel)
    db.commit()
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
    channel.slug = data.slug.lower()
    channel.description = data.description
    channel.category = data.category


    db.commit()
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