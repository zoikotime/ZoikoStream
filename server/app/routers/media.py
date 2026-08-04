"""Recording & Media Library API (/media/*).

Two audiences, two scoping rules, deliberately in one router because they serve the same rows:

  * ORGANIZATION routes (`/media/library`, `/media/folders`, `/media/recordings/{id}` …) scope by
    the caller's own org and are gated at host-and-above for writes. This is the library.
  * AUDIENCE routes (`/media/events/{event_id}/recordings`) scope by EVENT and resolve visibility
    through the same viewer rules the watch page uses. An attendee is legitimately outside the
    organizing org, so these cannot scope by the caller's org.

Never in this API: the storage key. A download is a POST that returns a freshly-signed, expiring
URL; there is no endpoint that hands out a durable location. That is what makes "Download Disabled"
and the expiry window real rather than a UI state a client can skip.

Audit: every state change and every issued download writes to `audit_logs` via
crud.admin.create_audit_log — the same trail the platform console reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crud import media as crud
from ..crud.admin import create_audit_log
from ..db import get_db
from ..models import (
    MEDIA_CATEGORIES,
    MEDIA_VISIBILITY,
    Event,
    LiveRecording,
    MediaFolder,
    Organization,
    User,
)
from ..security import get_current_user, hash_password, require_min_role
from ..services import media as media_svc
from ..services import storage
from .events import _viewable_or_404

router = APIRouter(prefix="/media", tags=["media"])

# A transcript upload. WebVTT for a 3-hour event is well under a megabyte; the cap is here because
# the file is read into memory to be parsed.
MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024
# How long a per-recording download window may be set for.
MAX_DOWNLOAD_WINDOW_DAYS = 3650


# ── scoping helpers ───────────────────────────────────────────────────────────

def _org(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Organization:
    """The caller's organization. A user with no org has no library — that is a 400, not an empty
    list, because every write below would otherwise silently target NULL."""
    if not user.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your account is not in an organization")
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your organization no longer exists")
    return org


def _recording(db: Session, org: Organization, recording_id, *, write: bool = False,
               user: User | None = None) -> LiveRecording:
    """Resolve one recording inside the caller's org. Missing and foreign both 404.

    `write=True` additionally requires host-and-above (services.media.can_manage): a moderator can
    watch and download the library but cannot rename, move, archive or delete it.
    """
    rec = crud.get(db, org.id, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if write and not media_svc.can_manage(rec, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Managing recordings requires a host or organization admin role")
    return rec


def _folder(db: Session, org: Organization, folder_id) -> MediaFolder:
    folder = db.scalar(select(MediaFolder).where(
        MediaFolder.id == folder_id, MediaFolder.org_id == org.id,
        MediaFolder.deleted_at.is_(None)))
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    return folder


def _audit(db, user, request: Request | None, action: str, rec: LiveRecording | None = None, **meta):
    create_audit_log(
        db, actor=user, action=action,
        target_type="live_recording" if rec is not None else "media",
        target_id=rec.id if rec is not None else None,
        org_id=rec.org_id if rec is not None else user.org_id,
        ip=(request.client.host if request and request.client else None),
        meta={k: v for k, v in meta.items() if v is not None},
    )


def _viewable(db: Session, user: User, rec: LiveRecording) -> Event | None:
    """The recording's event, for the visibility rules that need it."""
    return db.get(Event, rec.event_id)


# ── the library ───────────────────────────────────────────────────────────────

@router.get("/library")
def library(
    scope: str = Query("all", description=f"one of {crud.SCOPES}"),
    folder_id: uuid.UUID | None = Query(None),
    root_only: bool = Query(False, description="only recordings not in any folder"),
    event_id: uuid.UUID | None = Query(None),
    host_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, max_length=200),
    tag: list[str] | None = Query(None),
    category: str | None = Query(None),
    visibility: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    sort: str = Query("newest", description=f"one of {tuple(crud.SORTS)}"),
    page: int = Query(1, ge=1),
    page_size: int = Query(crud.DEFAULT_PAGE_SIZE, ge=1, le=crud.MAX_PAGE_SIZE),
    org: Organization = Depends(_org),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The organization media library — one endpoint behind the org library, the host library
    (`host_id=me`), the event library (`event_id=…`) and the recycle bin (`scope=deleted`).

    Four screens, one query shape: they differ only in a filter, and four endpoints would be four
    places to keep the tenant scoping and the sort whitelist correct.
    """
    if scope not in crud.SCOPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown scope. Expected one of {crud.SCOPES}")
    if sort not in crud.SORTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown sort. Expected one of {tuple(crud.SORTS)}")
    return crud.library(
        db, org.id, scope=scope, folder_id=folder_id, root_only=root_only, event_id=event_id,
        host_id=host_id, query=q, tags=tag, category=category, visibility=visibility,
        date_from=date_from, date_to=date_to, sort=sort, page=page, page_size=page_size,
        # `private` recordings belonging to someone else are excluded IN SQL, for the same reason
        # can_view refuses them individually — otherwise the grid shows cards that 404 on click,
        # and the total count includes rows the caller may not open.
        viewer_id=user.id,
        viewer_privileged=user.role in ("org_admin", "super_admin"),
    )


@router.get("/stats")
def stats(org: Organization = Depends(_org), db: Session = Depends(get_db)):
    """Storage usage, quota and library totals — the header of every library screen.

    Usage is SUM(size_bytes) over real rows, not `organizations.storage_used_gb`: that column is an
    admin-entered figure and this module will not report an entered number as a measurement.
    """
    policy = media_svc.policy_for(org)
    storage_stats = crud.storage_stats(db, org.id, org.storage_quota_gb)
    percent = storage_stats["percent_used"]
    return {
        **crud.library_totals(db, org.id),
        "storage": storage_stats,
        "storage_configured": storage.configured(),
        # The alert the brief asks for, computed rather than delivered: there is no scheduled job
        # in this stack, so "storage almost full" is a fact about now, evaluated when read.
        "storage_alert": (
            percent is not None and percent >= policy["alert_storage_percent"]
        ),
        "alert_threshold_percent": policy["alert_storage_percent"],
        "retention": media_svc.retention_plan(policy),
        "recycle_bin_days": policy["recycle_bin_days"],
    }


@router.get("/filters")
def filters(org: Organization = Depends(_org), db: Session = Depends(get_db)):
    """Distinct tags, categories and hosts that actually occur — so no filter offers an empty
    result, and the tag list is ranked by how much of the library each tag covers."""
    return crud.filter_options(db, org.id)


# ── one recording ─────────────────────────────────────────────────────────────

@router.get("/recordings/{recording_id}")
def detail(recording_id: uuid.UUID, request: Request,
           org: Organization = Depends(_org), user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Everything the detail page needs in one read: the item, its event, its marks, its download
    rules and — when a file exists — a signed playback URL.

    The playback URL is minted here rather than on a second request because the player needs it to
    render at all; it expires on the policy's TTL, so a stale page fetches a fresh one by reloading.
    """
    rec = _recording(db, org, recording_id)
    event = _viewable(db, user, rec)
    allowed, reason = media_svc.can_view(rec, event, user)
    if not allowed:
        # 404, not 403: a private recording's existence is not something to confirm to a colleague
        # who may not see it.
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason or "Recording not found")

    policy = media_svc.policy_for(org)
    rules = media_svc.download_rules(rec, policy)
    return {
        "recording": crud.item_out(rec, event),
        "playback_url": media_svc.playback_url(rec, policy),
        "storage_configured": storage.configured(),
        "marks": crud.marks(db, rec.id, user, org.id),
        "can_manage": media_svc.can_manage(rec, user),
        "download": {
            "mode": rules["mode"],
            "expires_at": rules["expires_at"].isoformat() if rules["expires_at"] else None,
            "watermark": rules["watermark"],
            # Stated plainly rather than implied: a watermark on a DOWNLOADED file needs a
            # re-encode, and this stack has no transcoding worker. The player overlay is what the
            # flag actually does.
            "watermark_scope": "player_overlay_only",
        },
        "transcript_available": bool((rec.transcript or {}).get("segments")),
        "insights_available": bool(rec.insights),
    }


@router.patch("/recordings/{recording_id}")
def update(recording_id: uuid.UUID, request: Request, patch: dict = Body(...),
           org: Organization = Depends(_org), user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Rename, move, retag, recategorise, re-scope. One endpoint: they are all one UPDATE, and the
    library's inline rename, drag-to-folder and tag editor all use it."""
    rec = _recording(db, org, recording_id, write=True, user=user)
    if patch.get("visibility") is not None and patch["visibility"] not in MEDIA_VISIBILITY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unknown visibility. Expected one of {MEDIA_VISIBILITY}")
    if patch.get("category") is not None and patch["category"] not in MEDIA_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unknown category. Expected one of {MEDIA_CATEGORIES}")
    before = {"title": rec.title, "folder_id": str(rec.folder_id) if rec.folder_id else None,
              "visibility": rec.visibility}
    crud.update(db, rec, patch)
    _audit(db, user, request, "media.recording.update", rec,
           before=before, changed=sorted(patch.keys()))
    db.commit()
    db.refresh(rec)
    return crud.item_out(rec, _viewable(db, user, rec))


@router.post("/recordings/{recording_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate(recording_id: uuid.UUID, request: Request,
              org: Organization = Depends(_org), user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """A second library entry over the same object. Starts `private` — see crud.media.duplicate."""
    rec = _recording(db, org, recording_id, write=True, user=user)
    copy = crud.duplicate(db, rec, user)
    _audit(db, user, request, "media.recording.duplicate", copy, source=str(rec.id))
    db.commit()
    db.refresh(copy)
    return crud.item_out(copy, _viewable(db, user, copy))


@router.post("/recordings/{recording_id}/archive")
def archive(recording_id: uuid.UUID, request: Request, archived: bool = Body(True, embed=True),
            org: Organization = Depends(_org), user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    rec = _recording(db, org, recording_id, write=True, user=user)
    crud.set_archived(db, rec, archived, user)
    _audit(db, user, request, f"media.recording.{'archive' if archived else 'unarchive'}", rec)
    db.commit()
    return {"id": str(rec.id), "archived_at": rec.archived_at.isoformat() if rec.archived_at else None}


@router.delete("/recordings/{recording_id}")
def soft_delete(recording_id: uuid.UUID, request: Request,
                org: Organization = Depends(_org), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Into the recycle bin. The stored object is untouched, which is what makes Restore work."""
    rec = _recording(db, org, recording_id, write=True, user=user)
    crud.soft_delete(db, rec, user)
    _audit(db, user, request, "media.recording.delete", rec, soft=True)
    db.commit()
    policy = media_svc.policy_for(org)
    return {"id": str(rec.id), "deleted_at": rec.deleted_at.isoformat(),
            "restorable_until": (rec.deleted_at + timedelta(
                days=policy["recycle_bin_days"])).isoformat()}


@router.post("/recordings/{recording_id}/restore")
def restore(recording_id: uuid.UUID, request: Request,
            org: Organization = Depends(_org), user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    rec = _recording(db, org, recording_id, write=True, user=user)
    if rec.deleted_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That recording is not in the recycle bin")
    crud.restore(db, rec)
    _audit(db, user, request, "media.recording.restore", rec)
    db.commit()
    return crud.item_out(rec, _viewable(db, user, rec))


@router.delete("/recordings/{recording_id}/purge")
def purge(recording_id: uuid.UUID, request: Request,
          org: Organization = Depends(_org),
          user: User = Depends(require_min_role("org_admin")),
          db: Session = Depends(get_db)):
    """Permanent deletion: the row AND the stored object.

    org_admin only. This is the one irreversible operation in the module, and the storage result is
    REPORTED rather than assumed — an admin who is told "purged" while the bytes remain in the
    bucket has been misinformed about a data-deletion request, which for a GDPR erasure is the
    difference between compliant and not.

    A duplicate sharing the object keeps the bytes: the row goes, the file stays, and the response
    says so.
    """
    rec = _recording(db, org, recording_id)
    if rec.deleted_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Delete the recording first — purge only empties the recycle bin")
    key, shared = rec.storage_key, crud.purge_blockers(db, rec)
    object_deleted, object_error = False, None
    if key and shared == 0:
        object_deleted, object_error = storage.delete_object(key)
    elif key and shared:
        object_error = f"Kept in storage — {shared} other library item(s) reference this file"

    _audit(db, user, request, "media.recording.purge", rec,
           storage_key=key, object_deleted=object_deleted, object_error=object_error)
    db.delete(rec)
    db.commit()
    return {"purged": True, "object_deleted": object_deleted, "object_error": object_error}


@router.post("/recordings/{recording_id}/view")
def register_view(recording_id: uuid.UUID, request: Request,
                  org: Organization = Depends(_org), user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Count one play. Called by the player once per session, not per seek.

    Audited because "Viewed" is on the brief's audit list — who watched what is a real compliance
    question for a recorded internal meeting.
    """
    rec = _recording(db, org, recording_id)
    allowed, reason = media_svc.can_view(rec, _viewable(db, user, rec), user)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason or "Recording not found")
    crud.record_view(db, rec)
    _audit(db, user, request, "media.recording.view", rec)
    db.commit()
    return {"view_count": rec.view_count}


# ── downloads ─────────────────────────────────────────────────────────────────

@router.post("/recordings/{recording_id}/download")
def download(recording_id: uuid.UUID, request: Request,
             password: str | None = Body(None, embed=True),
             org: Organization = Depends(_org), user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Issue one expiring, signed download URL — or explain why not.

    POST rather than GET, and a URL rather than a redirect, for three reasons: it is a state change
    (the download counter and the audit row), the passphrase must travel in a body rather than a
    query string that lands in access logs, and the client needs the refusal REASON to render
    ("password required" is a different UI from "downloads are off").
    """
    rec = _recording(db, org, recording_id)
    policy = media_svc.policy_for(org)
    decision = media_svc.resolve_download(rec, _viewable(db, user, rec), user, policy,
                                          password=password)
    if decision.url is None:
        # 403 for a policy refusal, 402-free: the reason is the payload, and `needs_password`
        # distinguishes "try again with a passphrase" from "you may never have this".
        _audit(db, user, request, "media.recording.download_denied", rec, reason=decision.error)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=decision.as_dict())

    crud.record_download(db, rec)
    _audit(db, user, request, "media.recording.download", rec,
           watermarked=decision.watermarked, expires_in=decision.expires_in)
    db.commit()
    return decision.as_dict()


@router.patch("/recordings/{recording_id}/download-policy")
def set_download_policy(recording_id: uuid.UUID, request: Request, patch: dict = Body(...),
                        org: Organization = Depends(_org),
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Per-recording download override: mode, passphrase, expiry, watermark.

    Sending `password` sets it; sending `null` clears it. The passphrase is bcrypt-hashed with the
    same helper the event access passphrase uses — it is never stored or returned in clear.
    """
    rec = _recording(db, org, recording_id, write=True, user=user)
    if "mode" in patch:
        mode = patch["mode"]
        if mode not in (None, "allowed", "disabled", "password"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "mode must be allowed, disabled, password or null to inherit")
        rec.download_policy = mode
    if "password" in patch:
        raw = (patch["password"] or "").strip()
        rec.download_password_hash = hash_password(raw) if raw else None
    if "expires_at" in patch:
        rec.download_expires_at = _parse_when(patch["expires_at"])
    if "watermark" in patch:
        rec.watermark = None if patch["watermark"] is None else bool(patch["watermark"])

    if rec.download_policy == "password" and not rec.download_password_hash:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Set a passphrase to use password-protected downloads")
    _audit(db, user, request, "media.recording.download_policy", rec,
           mode=rec.download_policy, has_password=bool(rec.download_password_hash))
    db.commit()
    rules = media_svc.download_rules(rec, media_svc.policy_for(org))
    return {"mode": rules["mode"], "watermark": rules["watermark"],
            "password_protected": bool(rec.download_password_hash),
            "expires_at": rules["expires_at"].isoformat() if rules["expires_at"] else None}


def _parse_when(value) -> datetime | None:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expires_at must be an ISO timestamp")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    ceiling = datetime.now(timezone.utc) + timedelta(days=MAX_DOWNLOAD_WINDOW_DAYS)
    return min(when, ceiling)


# ── transcripts ───────────────────────────────────────────────────────────────

@router.get("/recordings/{recording_id}/transcript")
def get_transcript(recording_id: uuid.UUID, q: str | None = Query(None, max_length=200),
                   org: Organization = Depends(_org), user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """The transcript, optionally searched. `q` returns only the matching cues with timestamps."""
    rec = _recording(db, org, recording_id)
    allowed, reason = media_svc.can_view(rec, _viewable(db, user, rec), user)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason or "Recording not found")
    transcript = rec.transcript or None
    if not transcript:
        return {
            "available": False,
            # The honest reason, and the way out. An organization running Whisper elsewhere can use
            # every transcript feature by uploading the result.
            "reason": ("No transcript yet. Automatic speech recognition is not configured on this "
                       "deployment — upload a WebVTT or SRT file to enable search, captions, "
                       "copy and download."),
            "can_upload": media_svc.can_manage(rec, user),
            "asr_configured": media_svc.ASR_PROVIDER is not None,
        }
    if q:
        return {"available": True, "language": transcript.get("language"),
                "query": q, "hits": media_svc.search_transcript(transcript, q)}
    return {"available": True, **transcript,
            "speaker_separation_note": (
                None if transcript.get("speaker_separation")
                else "The uploaded file carried no speaker labels, so segments are unattributed."
            )}


@router.post("/recordings/{recording_id}/transcript", status_code=status.HTTP_201_CREATED)
async def upload_transcript(recording_id: uuid.UUID, request: Request,
                            file: UploadFile,
                            language: str = Query("en", max_length=12),
                            org: Organization = Depends(_org),
                            user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """Attach a WebVTT or SRT transcript.

    This is the generation path too: `ASR_PROVIDER` is the seam for real speech recognition, and
    until one is registered an upload is the only honest way to get real words in here. Parsing is
    strict — a file with no timed cues is refused rather than stored as an empty transcript that
    makes the UI claim a transcript exists.
    """
    rec = _recording(db, org, recording_id, write=True, user=user)
    raw = await file.read(MAX_TRANSCRIPT_BYTES + 1)
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"Transcripts are limited to {MAX_TRANSCRIPT_BYTES // (1024 * 1024)} MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The transcript must be UTF-8 text")

    transcript, error = media_svc.parse_transcript(text, language=language, source="upload")
    if error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, error)
    rec.transcript = transcript
    _audit(db, user, request, "media.transcript.upload", rec,
           segments=len(transcript["segments"]), language=transcript["language"],
           speakers=len(transcript["speakers"]))
    db.commit()
    return {"available": True, "segments": len(transcript["segments"]),
            "speakers": transcript["speakers"], "language": transcript["language"],
            "speaker_separation": transcript["speaker_separation"]}


@router.delete("/recordings/{recording_id}/transcript", status_code=status.HTTP_204_NO_CONTENT)
def delete_transcript(recording_id: uuid.UUID, request: Request,
                      org: Organization = Depends(_org),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _recording(db, org, recording_id, write=True, user=user)
    rec.transcript = None
    _audit(db, user, request, "media.transcript.delete", rec)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/recordings/{recording_id}/transcript.vtt", response_class=PlainTextResponse)
def transcript_vtt(recording_id: uuid.UUID, org: Organization = Depends(_org),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """WebVTT for a <track> element — the captions the player shows.

    Served as text/vtt with nosniff so a browser treats it as captions and nothing else.
    """
    rec = _recording(db, org, recording_id)
    allowed, reason = media_svc.can_view(rec, _viewable(db, user, rec), user)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason or "Recording not found")
    if not (rec.transcript or {}).get("segments"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No transcript for this recording")
    return PlainTextResponse(
        media_svc.to_vtt(rec.transcript),
        media_type="text/vtt; charset=utf-8",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"},
    )


@router.get("/recordings/{recording_id}/transcript.txt", response_class=PlainTextResponse)
def transcript_txt(recording_id: uuid.UUID, request: Request, org: Organization = Depends(_org),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Timestamped plain text for Download. Audited — taking a copy of what was said in a private
    meeting is a download, even though it is not the video."""
    rec = _recording(db, org, recording_id)
    allowed, reason = media_svc.can_view(rec, _viewable(db, user, rec), user)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason or "Recording not found")
    if not (rec.transcript or {}).get("segments"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No transcript for this recording")
    _audit(db, user, request, "media.transcript.download", rec)
    db.commit()
    name = media_svc.download_filename(rec).rsplit(".", 1)[0]
    return PlainTextResponse(
        media_svc.to_text(rec.transcript),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.txt"',
                 "X-Content-Type-Options": "nosniff"},
    )


# ── insights ──────────────────────────────────────────────────────────────────

@router.get("/recordings/{recording_id}/insights")
def get_insights(recording_id: uuid.UUID, refresh: bool = Query(False),
                 org: Organization = Depends(_org), user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Chapters, highlights, keywords, topics and poll outcomes — plus what needs a model.

    Cached on the row and re-derived on `refresh=true`: derivation reads five tables, and the inputs
    only change while the event is live. Every field carries its provenance; see services/media.py
    for the derived-versus-unavailable split.
    """
    rec = _recording(db, org, recording_id)
    allowed, reason = media_svc.can_view(rec, _viewable(db, user, rec), user)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason or "Recording not found")
    if rec.insights and not refresh:
        return {"cached": True, **rec.insights}
    context = crud.insight_context(db, rec)
    insights = media_svc.derive_insights(rec, context)
    if media_svc.can_manage(rec, user):
        # Only a manager's read persists the cache — a viewer must not be able to write to the row.
        rec.insights = insights
        db.commit()
    return {"cached": False, "truncated": context.get("truncated", False), **insights}


# ── bookmarks & notes ─────────────────────────────────────────────────────────

@router.get("/recordings/{recording_id}/marks")
def list_marks(recording_id: uuid.UUID, org: Organization = Depends(_org),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _recording(db, org, recording_id)
    allowed, reason = media_svc.can_view(rec, _viewable(db, user, rec), user)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason or "Recording not found")
    return crud.marks(db, rec.id, user, org.id)


@router.post("/recordings/{recording_id}/marks", status_code=status.HTTP_201_CREATED)
def add_mark(recording_id: uuid.UUID, at_ms: int = Body(...), note: str | None = Body(None),
             shared: bool = Body(False),
             org: Organization = Depends(_org), user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Add a bookmark (no note) or a timestamped note. Anyone who can watch can mark — a bookmark
    is the viewer's own record, not a change to the recording, which is why this is not `write=True`."""
    rec = _recording(db, org, recording_id)
    allowed, reason = media_svc.can_view(rec, _viewable(db, user, rec), user)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, reason or "Recording not found")
    mark, error = crud.add_mark(db, rec, user, at_ms, note, shared)
    if error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, error)
    db.commit()
    return {"id": str(mark.id), "at_ms": mark.at_ms, "note": mark.note,
            "kind": "note" if mark.note else "bookmark", "shared": mark.shared, "mine": True}


@router.delete("/recordings/{recording_id}/marks/{mark_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mark(recording_id: uuid.UUID, mark_id: uuid.UUID, org: Organization = Depends(_org),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not crud.delete_mark(db, mark_id, user, org.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mark not found")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── folders ───────────────────────────────────────────────────────────────────

@router.get("/folders")
def list_folders(org: Organization = Depends(_org), db: Session = Depends(get_db)):
    return crud.folders(db, org.id)


@router.post("/folders", status_code=status.HTTP_201_CREATED)
def create_folder(request: Request, name: str = Body(...), parent_id: uuid.UUID | None = Body(None),
                  colour: str | None = Body(None),
                  org: Organization = Depends(_org),
                  user: User = Depends(require_min_role("host")), db: Session = Depends(get_db)):
    folder, error = crud.create_folder(db, org.id, name, user, parent_id=parent_id, colour=colour)
    if error:
        raise HTTPException(status.HTTP_409_CONFLICT, error)
    _audit(db, user, request, "media.folder.create", None, folder=str(folder.id), name=folder.name)
    db.commit()
    return {"id": str(folder.id), "name": folder.name,
            "parent_id": str(folder.parent_id) if folder.parent_id else None,
            "colour": folder.colour, "count": 0}


@router.patch("/folders/{folder_id}")
def rename_folder(folder_id: uuid.UUID, request: Request, name: str = Body(..., embed=True),
                  org: Organization = Depends(_org),
                  user: User = Depends(require_min_role("host")), db: Session = Depends(get_db)):
    folder = _folder(db, org, folder_id)
    updated, error = crud.rename_folder(db, folder, name)
    if error:
        raise HTTPException(status.HTTP_409_CONFLICT, error)
    _audit(db, user, request, "media.folder.rename", None, folder=str(folder.id), name=updated.name)
    db.commit()
    return {"id": str(updated.id), "name": updated.name}


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: uuid.UUID, request: Request, org: Organization = Depends(_org),
                  user: User = Depends(require_min_role("host")), db: Session = Depends(get_db)):
    """Delete the shelf, keep what was on it. Recordings and sub-folders move to the root."""
    folder = _folder(db, org, folder_id)
    moved = crud.delete_folder(db, folder)
    _audit(db, user, request, "media.folder.delete", None, folder=str(folder.id), moved=moved)
    db.commit()
    return {"deleted": True, "recordings_moved_to_root": moved}


# ── organization policy ───────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(org: Organization = Depends(_org), db: Session = Depends(get_db)):
    """The effective media policy plus what the platform can actually honour right now."""
    policy = media_svc.policy_for(org)
    return {
        "policy": policy,
        "defaults": media_svc.DEFAULT_MEDIA_POLICY,
        "storage_configured": storage.configured(),
        "quota_gb": org.storage_quota_gb,
        "capabilities": {
            # Said here so the settings screen can disable what it cannot deliver instead of
            # offering a toggle that quietly does nothing.
            "signed_urls": storage.configured(),
            "asr": media_svc.ASR_PROVIDER is not None,
            "ai_summaries": media_svc.AI_PROVIDER is not None,
            "download_watermark": False,
            "cold_storage_transition": False,
        },
        "notes": {
            "download_watermark": ("Watermarking a downloaded file requires re-encoding it; this "
                                   "deployment has no transcoding worker, so the watermark is a "
                                   "viewer-identity overlay in the player only."),
            "cold_storage": ("Cold storage marks the recording and reports it; moving the object "
                             "between storage classes is a bucket lifecycle rule, configured on "
                             "the bucket rather than from here."),
            "retention": ("Retention runs when POST /media/retention/run is called — there is no "
                          "scheduler in this deployment, so nothing sweeps unattended."),
        },
    }


@router.patch("/settings")
def update_settings(request: Request, patch: dict = Body(...),
                    org: Organization = Depends(_org),
                    user: User = Depends(require_min_role("org_admin")),
                    db: Session = Depends(get_db)):
    """Set the org's media policy. Unknown keys are refused and named, never silently dropped."""
    accepted, rejected = media_svc.clean_policy(patch)
    if rejected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unknown or invalid settings: {', '.join(sorted(rejected))}")
    org.media = {**(org.media or {}), **accepted}
    _audit(db, user, request, "media.settings.update", None, changed=sorted(accepted.keys()))
    db.commit()
    return {"policy": media_svc.policy_for(org)}


# ── retention ─────────────────────────────────────────────────────────────────

@router.post("/retention/run")
def run_retention(request: Request, dry_run: bool = Body(True, embed=True),
                  org: Organization = Depends(_org),
                  user: User = Depends(require_min_role("org_admin")),
                  db: Session = Depends(get_db)):
    """Apply the org's retention policy now, or preview what it would do.

    Explicitly triggered. There is no scheduler in this deployment, and a retention rule that
    silently deletes on a timer that does not exist is the most dangerous kind of fake feature —
    the organization would believe old recordings were being cleaned up. `dry_run` defaults to TRUE
    so the destructive form is always the deliberate one.

    Actions apply weakest-first (archive → cold → delete) so nothing skips a stage.
    """
    policy = media_svc.policy_for(org)
    plan = media_svc.retention_plan(policy)
    applied = {"archive": [], "cold": [], "delete": [], "purge": []}

    for action, age_days in plan:
        for rec in crud.retention_candidates(db, org.id, action, age_days):
            applied[action].append(str(rec.id))
            if dry_run:
                continue
            if action == "archive":
                crud.set_archived(db, rec, True, user)
            elif action == "cold":
                rec.storage_class = "cold"
            elif action == "delete":
                crud.soft_delete(db, rec, user)

    # Emptying the recycle bin is separate from the retention plan: it acts on rows a person already
    # deleted, and it PURGES, which is irreversible.
    for rec in crud.expired_bin(db, org.id, policy["recycle_bin_days"]):
        applied["purge"].append(str(rec.id))
        if dry_run:
            continue
        if rec.storage_key and crud.purge_blockers(db, rec) == 0:
            storage.delete_object(rec.storage_key)
        db.delete(rec)

    if not dry_run:
        _audit(db, user, request, "media.retention.run", None,
               counts={k: len(v) for k, v in applied.items()})
        db.commit()
    else:
        db.rollback()

    return {
        "dry_run": dry_run,
        "plan": plan,
        "recycle_bin_days": policy["recycle_bin_days"],
        "counts": {k: len(v) for k, v in applied.items()},
        "ids": applied,
    }


# ── audience-facing ───────────────────────────────────────────────────────────

@router.get("/events/{event_id}/recordings")
def event_recordings(event_id: uuid.UUID, token: str | None = Query(None),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Replays an ATTENDEE may watch for one event.

    Scoped by event, not by the caller's org: an attendee of a public event is in another tenant.
    Visibility is decided per recording by services.media.can_view, which also honours the
    organizer's `replay_enabled` switch — so turning replay off hides every replay for the event
    regardless of how each recording is labelled.
    """
    ev, _link_ok = _viewable_or_404(db, event_id, user, token)
    org = db.get(Organization, ev.org_id)
    policy = media_svc.policy_for(org)
    rows = crud.library(db, ev.org_id, scope="all", event_id=event_id,
                        sort="newest", page_size=crud.MAX_PAGE_SIZE)["items"]
    out = []
    for item in rows:
        rec = db.get(LiveRecording, uuid.UUID(item["id"]))
        allowed, _reason = media_svc.can_view(rec, ev, user)
        if not allowed:
            continue
        rules = media_svc.download_rules(rec, policy)
        out.append({
            # A deliberately narrower projection than the library card: no error text, no storage
            # class, no download counts, no per-recording policy internals.
            "id": item["id"],
            "title": item["title"],
            "description": item["description"],
            "duration_ms": item["duration_ms"],
            "size_bytes": item["size_bytes"],
            "stopped_at": item["stopped_at"],
            "view_count": item["view_count"],
            "has_transcript": item["has_transcript"],
            "playback_url": media_svc.playback_url(rec, policy),
            "downloadable": rules["mode"] != "disabled",
            "password_required": rules["mode"] == "password",
        })
    return {"event_id": str(ev.id), "replay_enabled": ev.replay_enabled, "recordings": out}
