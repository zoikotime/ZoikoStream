"""Streams API (/streams/*) — the legacy channel-based streaming surface.

Isolation: streams carry no org_id; they reach an owner through their channel, so every
read and write here is scoped by `Channel.owner_id == user.id` via crud/stream.py. That is
narrower than the org_scoped() model used by /events and /organization, and it is the
reason this router cannot simply share their helpers.

Two reads used to be completely unauthenticated: the list (which also serialized
`stream_key`, a publish credential, for every stream on the platform) and the viewer token.
Both now require a session; the list is owner-scoped and `stream_key` is confined to
single-stream owner reads via schemas.StreamListItem.

ponytail: the remaining gap is structural — a viewer token still only proves the caller is
signed in, not that they may watch THIS org's stream, because there is no org_id to check
against. Closing that needs streams.org_id (a migration), which is why it is recorded here
rather than faked with a join that doesn't exist.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import stream as crud
from ..db import get_db
from ..models import Stream, User
from ..schemas.stream import (
    StreamCreate,
    StreamListResponse,
    StreamResponse,
    StreamUpdate,
)
from ..security import get_current_user
from ..services.livekit import create_stream_token

router = APIRouter(prefix="/streams", tags=["Streams"])


def _owned_or_404(db: Session, stream_id: uuid.UUID, user: User) -> Stream:
    stream = crud.get_owned_stream(db, stream_id, user.id)
    if stream is None:
        # Same response for "missing" and "not yours" — an existence oracle on another
        # tenant's stream ids is worth nothing to a legitimate caller.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")
    return stream


@router.post("", response_model=StreamResponse, status_code=status.HTTP_201_CREATED)
def create_stream(
    data: StreamCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if crud.owned_channel(db, data.channel_id, user.id) is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't own this channel")
    return crud.create_stream(db, data.channel_id, data)


@router.get("", response_model=StreamListResponse)
def get_streams(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The caller's own streams. Rows omit `stream_key` (see schemas.StreamListItem)."""
    items, total = crud.list_streams(db, user.id, search=search, page=page, limit=limit)
    return {"page": page, "limit": limit, "total": total, "items": items}


@router.get("/{stream_id}", response_model=StreamResponse)
def get_stream(
    stream_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Owner-scoped: this response includes `stream_key`."""
    return _owned_or_404(db, stream_id, user)


@router.put("/{stream_id}", response_model=StreamResponse)
def update_stream(
    stream_id: uuid.UUID,
    data: StreamUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return crud.update_stream(db, _owned_or_404(db, stream_id, user), data)


@router.delete("/{stream_id}")
def delete_stream(
    stream_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    crud.delete_stream(db, _owned_or_404(db, stream_id, user))
    return {"message": "Stream deleted successfully"}


@router.post("/{stream_id}/start")
def start_stream(
    stream_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stream = _owned_or_404(db, stream_id, user)
    if stream.is_live:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Stream already live")

    stream = crud.start_stream(db, stream)
    return {
        "message": "Stream started",
        "room": stream.livekit_room,
        "token": create_stream_token(
            identity=str(user.id), room_name=stream.livekit_room, can_publish=True
        ),
        "livekit_url": settings.LIVEKIT_URL,
    }


@router.get("/{stream_id}/token")
def get_viewer_token(
    stream_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Subscribe-only token for a live stream.

    `stream_id` stays a plain str (not uuid.UUID) because callers may pass the LiveKit room
    name — `stream_<uuid>` — and that behaviour predates this refactor.
    """
    raw = stream_id.removeprefix("stream_")
    try:
        parsed = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")

    stream = crud.get_stream(db, parsed)
    if stream is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stream not found")
    if not stream.is_live:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Stream is offline")

    return {
        "room": stream.livekit_room,
        "token": create_stream_token(
            identity=f"viewer-{uuid.uuid4()}", room_name=stream.livekit_room, can_publish=False
        ),
    }


@router.post("/{stream_id}/stop", response_model=StreamResponse)
def stop_stream(
    stream_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stream = _owned_or_404(db, stream_id, user)
    if not stream.is_live:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Stream is not live")
    return crud.stop_stream(db, stream)
