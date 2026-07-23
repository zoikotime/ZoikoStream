from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.models.recording import Recording
from app.models.stream import Stream
from app.schemas.recording import RecordingOut
from app.security import get_current_user
from app.services.recording import apply_egress_update, get_egress_status, start_recording, status_from_egress, stop_recording

router = APIRouter(prefix="/streams/{stream_id}/recordings", tags=["Recordings"])

MANAGER_ROLES = ("org_admin", "host")


def _require_manager(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(403, "Only organization admins and hosts can manage recordings")


def _get_org_stream(db: Session, user: User, stream_id: str) -> Stream:
    stream = db.scalar(select(Stream).where(Stream.id == stream_id, Stream.org_id == user.org_id))
    if not stream:
        raise HTTPException(404, "Event not found")
    return stream


def _get_recording(db: Session, stream_id: str, recording_id: str) -> Recording:
    recording = db.scalar(select(Recording).where(Recording.id == recording_id, Recording.stream_id == stream_id))
    if not recording:
        raise HTTPException(404, "Recording not found")
    return recording


# LIST -- public, same as GET /streams/{id} (see that endpoint's visibility note).
@router.get("", response_model=list[RecordingOut])
def list_recordings(stream_id: str, db: Session = Depends(get_db)):
    if not db.get(Stream, stream_id):
        raise HTTPException(404, "Event not found")
    return db.scalars(
        select(Recording).where(Recording.stream_id == stream_id).order_by(Recording.created_at.desc())
    ).all()


# START
@router.post("/start", response_model=RecordingOut, status_code=201)
async def start(stream_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_manager(user)
    stream = _get_org_stream(db, user, stream_id)

    if not stream.is_live or not stream.livekit_room:
        raise HTTPException(400, "Start the stream before recording it")

    try:
        egress_info = await start_recording(stream.livekit_room, str(stream.id))
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    recording = Recording(stream_id=stream.id, egress_id=egress_info.egress_id, status="recording")
    db.add(recording)
    db.commit()
    db.refresh(recording)
    return recording


# STOP
@router.post("/{recording_id}/stop", response_model=RecordingOut)
async def stop(
    stream_id: str,
    recording_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_manager(user)
    _get_org_stream(db, user, stream_id)
    recording = _get_recording(db, stream_id, recording_id)

    egress_info = await stop_recording(recording.egress_id)
    recording.status = status_from_egress(egress_info)
    db.commit()
    db.refresh(recording)
    return recording


# REFRESH -- fallback for whenever the webhook (routers/webhooks.py) hasn't fired yet,
# e.g. no public tunnel in local dev. The frontend calls this periodically while a
# recording is non-final.
@router.post("/{recording_id}/refresh", response_model=RecordingOut)
async def refresh(
    stream_id: str,
    recording_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_manager(user)
    _get_org_stream(db, user, stream_id)
    recording = _get_recording(db, stream_id, recording_id)

    if recording.status in ("ready", "failed"):
        return recording

    egress_info = await get_egress_status(recording.egress_id)
    if egress_info:
        apply_egress_update(recording, egress_info)
        db.commit()
        db.refresh(recording)
    return recording
