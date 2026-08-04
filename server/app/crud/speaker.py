"""Presentation-asset persistence for the Speaker console.

Isolation is the same shape as every other live table: `org_id` is copied from the event, and
every query filters on it, so an asset id from another tenant resolves to None -> 404. Nothing
here reads an org from the request.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from ..models import (
    ASSET_CONTENT_TYPES,
    Event,
    EventAssignment,
    SpeakerAsset,
    User,
)

# Per-event ceiling, on top of the per-file one in models.live.MAX_ASSET_BYTES. Without it a
# speaker can upload 25 MB a hundred times.
MAX_ASSETS_PER_EVENT = 40


def kind_for(content_type: str) -> str | None:
    """Map a MIME type to how it can be USED, or None if we don't accept it at all."""
    return ASSET_CONTENT_TYPES.get((content_type or "").split(";")[0].strip().lower())


# A PDF's page count, without a PDF library.
#
# Two strategies, in order of trustworthiness:
#   1. /Type /Pages ... /Count N  — the page-tree root. Correct when present and uncompressed.
#   2. count of /Type /Page objects — a fallback that OVER-counts on some producers.
#
# Neither works on a linearised or object-stream-compressed PDF, where the catalogue is inside a
# compressed stream. That is why this returns None rather than a guess: the console then lets the
# browser's own viewer page through the file freely instead of clamping to a wrong number.
_COUNT = re.compile(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", re.S)
_PAGE = re.compile(rb"/Type\s*/Page[^s]")


def count_pdf_pages(data: bytes) -> int | None:
    counts = [int(m.group(1)) for m in _COUNT.finditer(data)]
    if counts:
        return max(counts)
    pages = len(_PAGE.findall(data))
    return pages or None


def visible_assets(db, org_id, event_id) -> list[SpeakerAsset]:
    """Every live asset on this event, newest first. Includes the bytes column by SQLAlchemy
    default, so callers that only need metadata should use `list_assets`."""
    return list(db.scalars(
        select(SpeakerAsset).where(
            SpeakerAsset.event_id == event_id,
            SpeakerAsset.org_id == org_id,
            SpeakerAsset.deleted_at.is_(None),
        ).order_by(SpeakerAsset.created_at.desc())
    ).all())


def list_assets(db, org_id, event_id) -> list[dict]:
    """Metadata only — the `data` column is deliberately NOT selected. Listing ten decks would
    otherwise pull 250 MB of bytes through the connection to render a file list."""
    cols = (
        SpeakerAsset.id, SpeakerAsset.filename, SpeakerAsset.content_type, SpeakerAsset.kind,
        SpeakerAsset.size_bytes, SpeakerAsset.pages, SpeakerAsset.status,
        SpeakerAsset.uploaded_by, SpeakerAsset.uploader_name, SpeakerAsset.review_note,
        SpeakerAsset.reviewed_at, SpeakerAsset.created_at,
    )
    rows = db.execute(
        select(*cols).where(
            SpeakerAsset.event_id == event_id,
            SpeakerAsset.org_id == org_id,
            SpeakerAsset.deleted_at.is_(None),
        ).order_by(SpeakerAsset.created_at.desc())
    ).all()
    return [asset_out(r) for r in rows]


def asset_out(row) -> dict:
    """Serialise a metadata row (or a full ORM object — both expose the same attributes)."""
    return {
        "id": str(row.id),
        "filename": row.filename,
        "content_type": row.content_type,
        "kind": row.kind,
        "size_bytes": int(row.size_bytes or 0),
        "pages": row.pages,
        "status": row.status,
        "uploaded_by": str(row.uploaded_by) if row.uploaded_by else None,
        "uploader_name": row.uploader_name,
        "review_note": row.review_note,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def get_asset(db, org_id, event_id, asset_id) -> SpeakerAsset | None:
    try:
        aid = uuid.UUID(str(asset_id))
    except (ValueError, TypeError, AttributeError):
        return None
    return db.scalar(
        select(SpeakerAsset).where(
            SpeakerAsset.id == aid,
            SpeakerAsset.event_id == event_id,
            SpeakerAsset.org_id == org_id,
            SpeakerAsset.deleted_at.is_(None),
        )
    )


def asset_count(db, org_id, event_id) -> int:
    return db.scalar(
        select(func.count()).select_from(SpeakerAsset).where(
            SpeakerAsset.event_id == event_id,
            SpeakerAsset.org_id == org_id,
            SpeakerAsset.deleted_at.is_(None),
        )
    ) or 0


def create_asset(db, *, event: Event, user: User, filename: str, content_type: str,
                 kind: str, data: bytes, pages: int | None) -> SpeakerAsset:
    asset = SpeakerAsset(
        event_id=event.id,
        org_id=event.org_id,
        uploaded_by=user.id,
        uploader_name=user.full_name or user.email,
        filename=filename[:255],
        content_type=content_type[:120],
        kind=kind,
        size_bytes=len(data),
        pages=pages,
        # Not approved on arrival. "Presentation Approved" is a real step: a host vets what is
        # going on the main screen. A speaker can still PREVIEW their own pending file.
        status="pending",
        data=data,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def soft_delete_asset(db, asset: SpeakerAsset) -> None:
    """Soft delete, so an audit row pointing at a deleted deck still resolves."""
    asset.deleted_at = datetime.now(timezone.utc)
    db.commit()


def assignment(db, event_id, user_id) -> list[str]:
    """This person's roles on this event. Used to decide whether they may upload at all."""
    return list(db.scalars(
        select(EventAssignment.role).where(
            EventAssignment.event_id == event_id,
            EventAssignment.user_id == user_id,
        )
    ).all())


def notes_for(db, event_id, user_id) -> str | None:
    """A speaker's private notes for one event, stored on their assignment row."""
    return db.scalar(
        select(EventAssignment.notes).where(
            EventAssignment.event_id == event_id,
            EventAssignment.user_id == user_id,
        ).order_by(EventAssignment.created_at).limit(1)
    )


def save_notes(db, event_id, user_id, text: str | None) -> bool:
    """Write notes onto the caller's own assignment row. Returns False when they have none —
    which is also the authorization check: no assignment, no notes."""
    row = db.scalar(
        select(EventAssignment).where(
            EventAssignment.event_id == event_id,
            EventAssignment.user_id == user_id,
        ).order_by(EventAssignment.created_at).limit(1)
    )
    if row is None:
        return False
    row.notes = (text or None)
    return True
