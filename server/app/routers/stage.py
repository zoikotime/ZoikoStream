from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.models.stream import Stream
from app.schemas.stage import StageDemoteIn, StageOut, StagePromoteIn
from app.security import get_current_user
from app.services.livekit import update_publish_permission
from app.services.stage import (
    HAND_QUEUE,
    MANAGER_ROLES,
    MAX_SPEAKERS,
    ON_STAGE,
    resolve_identity,
    stage_room_for,
    stage_snapshot,
)
from app.sockets import sio

router = APIRouter(prefix="/streams/{stream_id}/stage", tags=["Stage"])


def _require_manager(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(403, "Only org admins, hosts, and moderators can manage the stage")


def _get_live_org_stream(db: Session, user: User, stream_id: str) -> Stream:
    stream = db.scalar(select(Stream).where(Stream.id == stream_id, Stream.org_id == user.org_id))
    if not stream:
        raise HTTPException(404, "Event not found")
    if not stream.is_live or not stream.livekit_room:
        raise HTTPException(400, "Event is not live")
    return stream


@router.post("/promote", response_model=StageOut)
async def promote(
    stream_id: str,
    data: StagePromoteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_manager(user)
    stream = _get_live_org_stream(db, user, stream_id)
    key = str(stream.id)

    identity = data.identity or resolve_identity(None, data.email)
    display_name = HAND_QUEUE.get(key, {}).get(identity, data.display_name)

    roster = ON_STAGE.setdefault(key, {})
    if identity not in roster and len(roster) >= MAX_SPEAKERS:
        raise HTTPException(409, f"Only {MAX_SPEAKERS} speakers can be on stage at once")

    roster[identity] = display_name
    HAND_QUEUE.get(key, {}).pop(identity, None)

    # Best-effort live upgrade for someone already connected; a not-yet-connected
    # invitee picks up can_publish=True from get_viewer_token once they do join.
    await update_publish_permission(stream.livekit_room, identity, True)

    snapshot = stage_snapshot(key)
    await sio.emit("stage:hands", snapshot["hands"], room=stage_room_for(key))
    await sio.emit("stage:roster", snapshot["roster"], room=stage_room_for(key))
    return snapshot


@router.post("/demote", response_model=StageOut)
async def demote(
    stream_id: str,
    data: StageDemoteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_manager(user)
    stream = _get_live_org_stream(db, user, stream_id)
    key = str(stream.id)

    ON_STAGE.get(key, {}).pop(data.identity, None)
    await update_publish_permission(stream.livekit_room, data.identity, False)

    snapshot = stage_snapshot(key)
    await sio.emit("stage:roster", snapshot["roster"], room=stage_room_for(key))
    return snapshot
