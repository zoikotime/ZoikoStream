import re
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone

from app.db import get_db
from app.config import settings

from app.models import Organization, User
from app.models.stream import Stream
from app.models.channel import Channel
from app.models.registration import Registration
from app.models.chat import ChatMessage
from app.models.recording import Recording

from app.security import get_current_user, get_optional_user

from app.schemas.stream import (
    StreamCreate,
    StreamUpdate,
    StreamResponse
)

from app.services.livekit import create_stream_token
from app.services.registration import is_registered


router = APIRouter(
    prefix="/streams",
    tags=["Streams"]
)

# Who may create/manage events for an org. Viewers/moderators can watch/moderate but not
# schedule or edit events.
MANAGER_ROLES = ("org_admin", "host")


def _require_manager(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(403, "Only organization admins and hosts can manage events")


def _get_or_create_default_channel(db: Session, user: User) -> Channel:
    """Events need a channel to hang off, but the Events UI has no channel picker —
    reuse (or lazily create) one default channel per organization."""
    channel = db.scalar(
        select(Channel).join(User, Channel.owner_id == User.id).where(User.org_id == user.org_id)
    )
    if channel:
        return channel

    org = db.get(Organization, user.org_id)
    base_slug = re.sub(r"[^a-z0-9]+", "-", (org.name if org else "channel").lower()).strip("-") or "channel"
    slug = base_slug
    counter = 1
    while db.scalar(select(Channel).where(Channel.slug == slug)):
        slug = f"{base_slug}-{counter}"
        counter += 1

    channel = Channel(owner_id=user.id, name=org.name if org else "My Channel", slug=slug)
    db.add(channel)
    db.flush()  # assign channel.id without committing yet
    return channel


def _get_org_stream(db: Session, user: User, stream_id: str) -> Stream:
    stream = db.scalar(select(Stream).where(Stream.id == stream_id, Stream.org_id == user.org_id))
    if not stream:
        raise HTTPException(404, "Stream not found")
    return stream


def _validate_assignee(db: Session, user: User, member_id, role: str, field: str) -> None:
    """host_id/moderator_id must be a real user, in the caller's org, with the matching
    role -- otherwise this fails as an unhandled 500 (FK violation) instead of a clean 400."""
    if member_id is None:
        return
    member = db.scalar(select(User).where(User.id == member_id, User.org_id == user.org_id))
    if not member:
        raise HTTPException(400, f"{field} must be a member of your organization")
    if member.role != role:
        raise HTTPException(400, f"{field} must reference a user with the '{role}' role")


# CREATE STREAM
@router.post(
    "",
    response_model=StreamResponse,
    status_code=201
)
def create_stream(
    data: StreamCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    _require_manager(user)

    if data.channel_id:
        channel = db.scalar(
            select(Channel)
            .join(User, Channel.owner_id == User.id)
            .where(Channel.id == data.channel_id, User.org_id == user.org_id)
        )
        if not channel:
            raise HTTPException(403, "Channel not found in your organization")
    else:
        channel = _get_or_create_default_channel(db, user)

    _validate_assignee(db, user, data.host_id, "host", "host_id")
    _validate_assignee(db, user, data.moderator_id, "moderator", "moderator_id")

    stream = Stream(
        channel_id=channel.id,
        org_id=user.org_id,
        host_id=data.host_id,
        moderator_id=data.moderator_id,
        title=data.title,
        description=data.description,
        category=data.category,
        thumbnail_url=data.thumbnail_url,
        visibility=data.visibility,
        registration_required=data.registration_required,
        scheduled_date=data.scheduled_date,
        start_time=data.start_time,
        end_time=data.end_time,
        timezone=data.timezone,
        status=data.status,
        stream_key=secrets.token_urlsafe(32),
    )


    db.add(stream)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Could not create event — check host_id/moderator_id/channel_id are valid")

    db.refresh(stream)

    return stream



# GET ALL STREAMS (scoped to the caller's organization)
@router.get(
    "",
    response_model=list[StreamResponse]
)
def get_streams(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):

    streams = db.scalars(
        select(Stream).where(Stream.org_id == user.org_id).order_by(Stream.created_at.desc())
    ).all()

    return streams



# GET SINGLE STREAM
# Unauthenticated requests are allowed through -- shareable event links (/e/:id) and the
# watch page need this with no login -- but Stream.visible_to() still gates draft/private
# events to org members, using get_optional_user so a logged-in caller's org membership
# is taken into account without *requiring* a login.
@router.get(
    "/{stream_id}",
    response_model=StreamResponse
)
def get_stream(
    stream_id: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):

    stream = db.scalar(
        select(Stream)
        .where(Stream.id == stream_id)
    )


    if not stream or not stream.visible_to(user):
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
    _require_manager(user)
    stream = _get_org_stream(db, user, stream_id)

    updates = data.model_dump(exclude_unset=True)
    if "host_id" in updates:
        _validate_assignee(db, user, updates["host_id"], "host", "host_id")
    if "moderator_id" in updates:
        _validate_assignee(db, user, updates["moderator_id"], "moderator", "moderator_id")

    for field, value in updates.items():
        setattr(stream, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Could not update event — check the fields you're changing are valid")

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
    _require_manager(user)
    stream = _get_org_stream(db, user, stream_id)

    db.query(Registration).filter(Registration.stream_id == stream.id).delete()
    db.query(ChatMessage).filter(ChatMessage.stream_id == stream.id).delete()
    db.query(Recording).filter(Recording.stream_id == stream.id).delete()

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
    _require_manager(user)
    stream = _get_org_stream(db, user, stream_id)


    if stream.is_live:
        raise HTTPException(
            400,
            "Stream already live"
        )


    stream.is_live = True
    stream.status = "live"

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
    email: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):

    if stream_id.startswith("stream_"):
        stream_id = stream_id.replace("stream_", "")

    stream = db.scalar(
        select(Stream)
        .where(
            Stream.id == stream_id
        )
    )


    if not stream or not stream.visible_to(user):
        raise HTTPException(
            404,
            "Stream not found"
        )

    # `email` lets a guest who registered prove it via query param; a logged-in caller
    # is checked against their own account email instead (see is_registered).
    if not is_registered(db, stream, user, email):
        raise HTTPException(
            403,
            "This event requires registration before you can join"
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
        "token": token,
        "livekit_url": settings.LIVEKIT_URL
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
    _require_manager(user)
    stream = _get_org_stream(db, user, stream_id)


    if not stream.is_live:
        raise HTTPException(
            400,
            "Stream is not live"
        )


    stream.is_live = False
    stream.status = "completed"
    stream.ended_at = datetime.now(timezone.utc)


    db.commit()
    db.refresh(stream)

    return stream
