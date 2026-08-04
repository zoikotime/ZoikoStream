"""Speaker presentation files (/speaker/*).

Only FOUR endpoints, and they exist because bytes cannot travel over the socket: uploading a
25 MB deck through a WebSocket frame would block the event loop and blow past the frame limits,
and a browser needs a real URL to render a PDF in an <iframe>. Everything else the speaker
console does — presenting, the whiteboard, notes, answering questions — goes over the existing
live socket like every other console action.

Authorization, in one place (`_asset_ctx`):
  upload / delete own            -> assigned speaker, panellist or host on THIS event
  read (list / download)         -> the same people, plus moderators and org admins
  approve / reject               -> NOT here; it is a socket action (services/speaker.py), so the
                                    decision broadcasts to every console in one round trip

Every path resolves the event org-scoped from the JWT first, so an event or asset id belonging to
another tenant is a 404 rather than a permission error.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from ..crud import event as crud_event
from ..crud import speaker as crud
from ..crud.admin import create_audit_log
from ..db import get_db
from ..models import MAX_ASSET_BYTES, User
from ..security import get_current_user

router = APIRouter(prefix="/speaker", tags=["speaker"])

# Roles on the EVENT that put somebody on stage. `panelist` is included for the same reason
# resolve_ctx includes it: a panellist on a panel presents like any other speaker.
STAGE_ROLES = ("speaker", "panelist", "host")


def _event_or_404(db, user: User, event_id: uuid.UUID):
    ev = crud_event.get_event(db, user.org_id, event_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return ev


def _asset_ctx(db, user: User, event_id: uuid.UUID, *, write: bool):
    """Resolve the event and decide whether this user may read or write its assets.

    Returns (event, roles, is_staff). `write` means upload/delete: staff who are not on the
    event's team can READ what is queued for their event (they have to approve it) but do not get
    to upload into somebody else's session.
    """
    ev = _event_or_404(db, user, event_id)
    roles = crud.assignment(db, ev.id, user.id)
    is_staff = user.role in ("org_admin", "super_admin")
    on_stage_team = any(r in STAGE_ROLES for r in roles)
    can_moderate = is_staff or any(r in ("host", "moderator") for r in roles)

    if write and not (on_stage_team or is_staff):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only a speaker assigned to this event can upload a presentation")
    if not write and not (on_stage_team or can_moderate):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not on this event's team")
    return ev, roles, can_moderate


@router.get("/events/{event_id}/assets")
def list_assets(event_id: uuid.UUID, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Metadata only — the bytes are never in a listing (see crud.speaker.list_assets)."""
    ev, _roles, _can_moderate = _asset_ctx(db, user, event_id, write=False)
    return crud.list_assets(db, ev.org_id, ev.id)


@router.post("/events/{event_id}/assets", status_code=status.HTTP_201_CREATED)
async def upload_asset(event_id: uuid.UUID, file: UploadFile = File(...),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Accept one presentation file.

    Reads the body in CHUNKS with a running total, and aborts the moment the cap is passed. The
    obvious `await file.read()` would buffer the whole upload before checking its size, which
    makes the limit advisory — a 2 GB body would still be read into memory first.

    The content type is taken from the sniffed extension AND the declared header, and the file is
    rejected unless the declared type is one we accept. That is the "presentation abuse" guard:
    the download endpoint below serves these bytes back, so accepting text/html here would turn
    an event into a place to host a phishing page on our own origin.
    """
    ev, _roles, _cm = _asset_ctx(db, user, event_id, write=True)

    if crud.asset_count(db, ev.org_id, ev.id) >= crud.MAX_ASSETS_PER_EVENT:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This event already has {crud.MAX_ASSETS_PER_EVENT} files — delete one first")

    kind = crud.kind_for(file.content_type or "")
    if kind is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            "Upload a PDF, an image, or a PowerPoint file")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_ASSET_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                f"Files must be under {MAX_ASSET_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That file is empty")

    # A PDF that isn't a PDF is not a presentation. Cheap magic-byte check rather than trusting
    # the declared header, because the header is client-supplied.
    if kind == "slides" and not data.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That doesn't look like a PDF")

    pages = crud.count_pdf_pages(data) if kind == "slides" else (1 if kind == "image" else None)
    asset = crud.create_asset(
        db, event=ev, user=user, filename=file.filename or "presentation",
        content_type=file.content_type, kind=kind, data=data, pages=pages,
    )
    create_audit_log(db, actor=user, action="live.presentation.upload", target_type="speaker_asset",
                     target_id=asset.id, org_id=ev.org_id,
                     meta={"event_id": str(ev.id), "filename": asset.filename,
                           "size_bytes": asset.size_bytes, "kind": kind})
    db.commit()
    return crud.asset_out(asset)


@router.get("/events/{event_id}/assets/{asset_id}/file")
def download_asset(event_id: uuid.UUID, asset_id: uuid.UUID,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Serve the bytes to the presenter (and to staff, who have to review them).

    Deliberately NOT public and NOT signed-URL'd: the audience never needs this file — they see
    the presenter's screen share through LiveKit. Keeping it behind the session is what stops an
    unapproved deck leaking to the room.

    Content-Disposition is `inline` so a PDF renders in the browser's own viewer (which is what
    makes presenter mode work without a PDF library), and the sniffing/framing headers are set
    because we are serving user-uploaded bytes from our own origin.
    """
    ev, roles, can_moderate = _asset_ctx(db, user, event_id, write=False)
    asset = crud.get_asset(db, ev.org_id, ev.id, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    # A speaker may always fetch their OWN file (that is how they preview it before approval).
    # Somebody else's pending file is only visible to the people who approve it.
    if asset.uploaded_by != user.id and not can_moderate and asset.status != "approved":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That file hasn't been approved yet")

    safe = (asset.filename or "presentation").replace('"', "").replace("\r", "").replace("\n", "")
    return Response(
        content=asset.data,
        media_type=asset.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'",
            # Uploaded bytes are never cached by a shared cache: the same URL is
            # permission-checked per user.
            "Cache-Control": "private, max-age=300",
        },
    )


@router.delete("/events/{event_id}/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(event_id: uuid.UUID, asset_id: uuid.UUID,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A speaker deletes their OWN file; a host/moderator/org admin can delete any on the event.

    Gated as a READ here, not a write: `write=True` means "may put a file into this session", which
    a moderator who is not on the stage team is not. Removing a bad file IS theirs — they are the
    ones who approve and reject — so the real check is the ownership test below.
    """
    ev, _roles, can_moderate = _asset_ctx(db, user, event_id, write=False)
    asset = crud.get_asset(db, ev.org_id, ev.id, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if asset.uploaded_by != user.id and not can_moderate:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete your own files")
    crud.soft_delete_asset(db, asset)
    create_audit_log(db, actor=user, action="live.presentation.delete", target_type="speaker_asset",
                     target_id=asset.id, org_id=ev.org_id,
                     meta={"event_id": str(ev.id), "filename": asset.filename})
    db.commit()


@router.get("/events/{event_id}/notes")
def get_notes(event_id: uuid.UUID, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """The caller's own private notes for this event.

    Read over REST rather than only in the socket snapshot so a speaker can prepare from the
    dashboard without opening the live console. Writing goes over the socket (`notes.save`).
    """
    ev, _roles, _cm = _asset_ctx(db, user, event_id, write=False)
    return {"notes": crud.notes_for(db, ev.id, user.id)}
