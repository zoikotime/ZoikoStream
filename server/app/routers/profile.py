"""The signed-in user's own profile photo (services/avatars.py).

  POST   /api/profile/avatar               upload or replace YOUR photo (the request body is the
                                           image itself); returns your updated account
  DELETE /api/profile/avatar               remove YOUR photo; returns your updated account
  GET    /api/avatars/<user_id>/<name>     the photo

There is no user id in the two write routes: they act on the caller's own account and nobody
else's, so an administrator cannot set or clear another member's personal photo through them,
and no admin route accepts one either.

The read route is unauthenticated because an <img> cannot send a bearer token. What protects it
is the URL: a random 32-character name per upload, handed out only inside authenticated
payloads, and served only while it is still that user's CURRENT photo — a replaced or removed
one is a 404 at once, whether or not its object has been cleaned up yet.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..ratelimit import rate_limit
from ..schemas import UserOut
from ..security import get_current_user
from ..services import avatars
from . import auth as auth_routes

router = APIRouter(tags=["profile"])

# Per client address, on top of needing a signed-in session: re-encoding images is real work.
_UPLOAD_LIMIT = rate_limit("avatar-upload", limit=20, window=600.0)


async def _read_capped(request: Request) -> bytes:
    """The request body, refusing it as soon as it passes the size limit — before the whole of
    an oversized upload has been read into memory."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > avatars.MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "That image is larger than 5 MB.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > avatars.MAX_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "That image is larger than 5 MB.")
    return bytes(body)


@router.post("/profile/avatar", response_model=UserOut, dependencies=[_UPLOAD_LIMIT])
async def upload_avatar(request: Request, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    raw = await _read_capped(request)
    try:
        image = await run_in_threadpool(avatars.process, raw)
    except avatars.AvatarRejected as exc:
        raise HTTPException(exc.status, exc.message) from exc
    try:
        url = await run_in_threadpool(avatars.save, user.id, image)
    except avatars.AvatarStorageUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Profile photos are not available right now.") from exc
    previous = user.avatar_url
    user.avatar_url = url
    db.commit()
    db.refresh(user)
    # Only after the new photo is saved and recorded: a failed upload never loses the old one.
    if previous and previous != url:
        await run_in_threadpool(avatars.delete, previous)
    return auth_routes.me(user)


@router.delete("/profile/avatar", response_model=UserOut)
def remove_avatar(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    previous = user.avatar_url
    user.avatar_url = None
    db.commit()
    db.refresh(user)
    if previous:
        avatars.delete(previous)
    return auth_routes.me(user)


@router.get("/avatars/{user_id}/{name}", include_in_schema=False)
def get_avatar(user_id: uuid.UUID, name: str, db: Session = Depends(get_db)):
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not avatars.valid_name(name):
        raise not_found
    owner = db.get(User, user_id)
    if owner is None or owner.deleted_at is not None or owner.avatar_url != avatars.url_for(user_id, name):
        raise not_found
    data = avatars.load(user_id, name)
    if data is None:
        raise not_found
    return Response(
        content=data,
        media_type="image/webp",
        headers={
            # The URL changes with every new photo, so a cached copy is never a stale photo; the
            # hour bounds how long a REMOVED one can linger in a browser that already had it.
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
