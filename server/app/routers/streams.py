import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone

from app.db import get_db
from app.config import settings

from app.models import User
from app.models.stream import Stream
from app.models.channel import Channel

from app.security import get_current_user

from app.schemas.stream import (
    StreamCreate,
    StreamUpdate,
    StreamResponse
)

from app.services.livekit import create_stream_token


router = APIRouter(
    prefix="/streams",
    tags=["Streams"]
)


# CREATE STREAM
@router.post(
    "/",
    response_model=StreamResponse,
    status_code=201
)
def create_stream(
    data: StreamCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):

    channel = db.scalar(
        select(Channel)
        .where(
            Channel.id == data.channel_id,
            Channel.owner_id == user.id
        )
    )

    if not channel:
        raise HTTPException(
            403,
            "You don't own this channel"
        )


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



# GET ALL STREAMS
@router.get(
    "/",
    response_model=list[StreamResponse]
)
def get_streams(
    db: Session = Depends(get_db)
):

    streams = db.scalars(
        select(Stream)
    ).all()

    return streams



# GET SINGLE STREAM
@router.get(
    "/{stream_id}",
    response_model=StreamResponse
)
def get_stream(
    stream_id: str,
    db: Session = Depends(get_db)
):

    stream = db.scalar(
        select(Stream)
        .where(Stream.id == stream_id)
    )


    if not stream:
        raise HTTPException(
            404,
            "Stream not found"
        )

    return stream



# UPDATE STREAM
@router.put(
    "/{stream_id}",
    response_model=StreamResponse
)
def update_stream(
    stream_id: str,
    data: StreamUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):

    stream = db.scalar(
        select(Stream)
        .join(Channel)
        .where(
            Stream.id == stream_id,
            Channel.owner_id == user.id
        )
    )


    if not stream:
        raise HTTPException(
            404,
            "Stream not found or not owner"
        )


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




# DELETE STREAM
@router.delete(
    "/{stream_id}"
)
def delete_stream(
    stream_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):

    stream = db.scalar(
        select(Stream)
        .join(Channel)
        .where(
            Stream.id == stream_id,
            Channel.owner_id == user.id
        )
    )


    if not stream:
        raise HTTPException(
            404,
            "Stream not found or not owner"
        )


    db.delete(stream)
    db.commit()


    return {
        "message": "Stream deleted successfully"
    }

# START STREAM
@router.post("/{stream_id}/start")
def start_stream(
    stream_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):

    stream = db.scalar(
        select(Stream)
        .join(Channel)
        .where(
            Stream.id == stream_id,
            Channel.owner_id == user.id
        )
    )


    if not stream:
        raise HTTPException(
            404,
            "Stream not found"
        )


    if stream.is_live:
        raise HTTPException(
            400,
            "Stream already live"
        )


    stream.is_live = True

    stream.started_at = datetime.now(timezone.utc)

    stream.livekit_room = f"stream_{stream.id}"


    db.commit()
    db.refresh(stream)


    token = create_stream_token(
        identity=str(user.id),
        room_name=stream.livekit_room,
        can_publish=True
    )


    return {
        "message": "Stream started",
        "room": stream.livekit_room,
        "token": token,
        "livekit_url": settings.LIVEKIT_URL
    }

# GET VIEWER TOKEN
@router.get(
    "/{stream_id}/token"
)
def get_viewer_token(
    stream_id: str,
    db: Session = Depends(get_db)
):
    
    if stream_id.startswith("stream_"):
        stream_id = stream_id.replace("stream_", "")

    stream = db.scalar(
        select(Stream)
        .where(
            Stream.id == stream_id
        )
    )


    if not stream:
        raise HTTPException(
            404,
            "Stream not found"
        )


    if not stream.is_live:
        raise HTTPException(
            400,
            "Stream is offline"
        )


    token = create_stream_token(
        identity=f"viewer-{uuid.uuid4()}",
        room_name=stream.livekit_room,
        can_publish=False
    )


    return {
        "room": stream.livekit_room,
        "token": token
    }

# STOP STREAM
@router.post(
    "/{stream_id}/stop",
    response_model=StreamResponse
)
def stop_stream(
    stream_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):

    stream = db.scalar(
        select(Stream)
        .join(Channel)
        .where(
            Stream.id == stream_id,
            Channel.owner_id == user.id
        )
    )


    if not stream:
        raise HTTPException(
            404,
            "Stream not found or not owner"
        )


    if not stream.is_live:
        raise HTTPException(
            400,
            "Stream is not live"
        )


    stream.is_live = False
    stream.ended_at = datetime.now(timezone.utc)


    db.commit()
    db.refresh(stream)

    return stream