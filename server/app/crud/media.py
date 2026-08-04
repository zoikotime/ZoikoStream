"""Media-library persistence: the shelf, the folders, the marks and the retention sweep.

Scoped by `org_id` on every statement, without exception. A recording belongs to the organizing
org even when its audience is public, so unlike crud/attendee.py (where the caller is legitimately
an outsider) there is no path here that scopes by anything else.

The library ITEM is `live_recordings` — see models/live.py for why there is no second table. That
means every query below filters `deleted_at IS NULL` explicitly rather than relying on a view: the
recycle bin needs the opposite filter, and one query with a flag is fewer moving parts than a view
plus a query.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select

from ..models import (
    AnalyticsSnapshot,
    Event,
    LiveActivity,
    LiveAnnouncement,
    LiveMessage,
    LivePoll,
    LiveQuestion,
    LiveRecording,
    MediaFolder,
    MediaMark,
    User,
)

# A library page. High enough that an org with a year of weekly events sees everything without
# paging, low enough that one request cannot serialise 100k rows.
DEFAULT_PAGE_SIZE = 60
MAX_PAGE_SIZE = 200
# Per person per recording. A note-taking aid, not a document store.
MAX_MARKS_PER_RECORDING = 300
MAX_NOTE_CHARS = 2000
MAX_TAGS = 12
MAX_TAG_CHARS = 32
# Insight derivation reads real rows; these caps stop one very long event making the request
# unbounded. Both are far above a normal event and are reported when hit (see insight_context).
INSIGHT_TEXT_LIMIT = 4000
INSIGHT_SAMPLE_LIMIT = 5000


def _now():
    return datetime.now(timezone.utc)


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


# ── one recording ─────────────────────────────────────────────────────────────

def get(db, org_id, recording_id, *, include_deleted: bool = True) -> LiveRecording | None:
    """One recording, org-scoped. A foreign id resolves to None so the router can 404 rather than
    403 — a 403 would confirm the recording exists in someone else's organization."""
    rid = _uuid(recording_id)
    if rid is None:
        return None
    stmt = select(LiveRecording).where(LiveRecording.id == rid, LiveRecording.org_id == org_id)
    if not include_deleted:
        stmt = stmt.where(LiveRecording.deleted_at.is_(None))
    return db.scalar(stmt)


def get_for_event(db, event_id, recording_id) -> LiveRecording | None:
    """One recording scoped by EVENT rather than org — the audience path, where the caller is
    outside the organizing org and `event_id` is the only thing they legitimately hold."""
    rid = _uuid(recording_id)
    if rid is None:
        return None
    return db.scalar(select(LiveRecording).where(
        LiveRecording.id == rid,
        LiveRecording.event_id == event_id,
        LiveRecording.deleted_at.is_(None),
    ))


# ── the library ───────────────────────────────────────────────────────────────

# Sort keys the API accepts, mapped to real columns. A whitelist, not a getattr on user input.
SORTS = {
    "newest": (LiveRecording.stopped_at.desc().nullslast(), LiveRecording.created_at.desc()),
    "oldest": (LiveRecording.stopped_at.asc().nullsfirst(), LiveRecording.created_at.asc()),
    "views": (LiveRecording.view_count.desc(), LiveRecording.created_at.desc()),
    "size": (LiveRecording.size_bytes.desc().nullslast(), LiveRecording.created_at.desc()),
    "duration": (LiveRecording.duration_ms.desc().nullslast(), LiveRecording.created_at.desc()),
    "title": (func.lower(func.coalesce(LiveRecording.title, Event.title)).asc(),),
    "downloads": (LiveRecording.download_count.desc(), LiveRecording.created_at.desc()),
}
# Shelf views. `all` is everything live; `archived` and `deleted` are the two shelves that are
# hidden from it, and `failed` is the recording queue's recovery list.
SCOPES = ("all", "archived", "deleted", "failed", "processing", "shared")


def library(
    db, org_id, *,
    scope: str = "all",
    folder_id=None,
    root_only: bool = False,
    event_id=None,
    host_id=None,
    query: str | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    visibility: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = "newest",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    viewer_id=None,
    viewer_privileged: bool = False,
) -> dict:
    """The shelf: {items, total, page, pages}. One JOIN to events, because every row needs the
    event's title and start time and a per-row lookup would be an N+1 across 60 cards.

    Filters compose — an organizer narrowing by folder AND tag AND date range gets all three.

    `viewer_id` applies the `private` rule IN SQL. Filtering it out after the query would make
    `total` and the page size lie (a page of 60 could return 12 cards), and checking ownership
    per row would be exactly the N+1 the JOIN above exists to avoid.
    """
    stmt = select(LiveRecording, Event).join(Event, Event.id == LiveRecording.event_id, isouter=True)
    stmt = stmt.where(LiveRecording.org_id == org_id)
    if viewer_id is not None and not viewer_privileged:
        stmt = stmt.where(or_(LiveRecording.visibility != "private",
                              LiveRecording.created_by == viewer_id))

    # Scope first: it decides which of deleted/archived are in play, and every other filter is
    # applied on top of that population.
    if scope == "deleted":
        stmt = stmt.where(LiveRecording.deleted_at.isnot(None))
    else:
        stmt = stmt.where(LiveRecording.deleted_at.is_(None))
        if scope == "archived":
            stmt = stmt.where(LiveRecording.archived_at.isnot(None))
        elif scope == "failed":
            stmt = stmt.where(LiveRecording.status == "failed")
        elif scope == "processing":
            # Still capturing, or stopped but with no file reported yet — the recording queue.
            stmt = stmt.where(or_(
                LiveRecording.status.in_(("recording", "paused")),
                (LiveRecording.status == "stopped") & LiveRecording.size_bytes.is_(None),
            ))
        elif scope == "shared":
            stmt = stmt.where(LiveRecording.visibility.in_(("event_audience", "public")))
        else:
            # The default shelf hides the archive and anything that never produced a file. A failed
            # capture is not a library item; it lives in the queue until it is retried or cleared.
            stmt = stmt.where(LiveRecording.archived_at.is_(None),
                              LiveRecording.status.notin_(("failed",)))

    if root_only:
        stmt = stmt.where(LiveRecording.folder_id.is_(None))
    elif folder_id is not None:
        fid = _uuid(folder_id)
        stmt = stmt.where(LiveRecording.folder_id == fid) if fid else stmt.where(False)
    if event_id is not None:
        eid = _uuid(event_id)
        stmt = stmt.where(LiveRecording.event_id == eid) if eid else stmt.where(False)
    if host_id is not None:
        hid = _uuid(host_id)
        stmt = stmt.where(LiveRecording.created_by == hid) if hid else stmt.where(False)
    if category:
        stmt = stmt.where(LiveRecording.category == category)
    if visibility:
        stmt = stmt.where(LiveRecording.visibility == visibility)
    if date_from:
        stmt = stmt.where(func.coalesce(LiveRecording.stopped_at, LiveRecording.created_at) >= date_from)
    if date_to:
        stmt = stmt.where(func.coalesce(LiveRecording.stopped_at, LiveRecording.created_at) <= date_to)
    if query and query.strip():
        like = f"%{query.strip().lower()}%"
        # Searches the recording's own title, the event's title, and the description. NOT the
        # transcript: a LIKE over a JSON blob would table-scan every recording in the org, and
        # transcript search is per-recording (services/media.search_transcript) where it belongs.
        stmt = stmt.where(or_(
            func.lower(func.coalesce(LiveRecording.title, "")).like(like),
            func.lower(func.coalesce(LiveRecording.description, "")).like(like),
            func.lower(func.coalesce(Event.title, "")).like(like),
        ))
    for tag in (tags or [])[:MAX_TAGS]:
        # Containment on the JSON array. Postgres can answer this without unnesting, and ANDing
        # the tags (rather than ORing) is what "filter by tag" means once you pick two.
        stmt = stmt.where(LiveRecording.tags.contains([tag]))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    page = max(1, int(page or 1))
    ordering = SORTS.get(sort) or SORTS["newest"]
    rows = db.execute(stmt.order_by(*ordering).offset((page - 1) * size).limit(size)).all()

    return {
        "items": [item_out(r, e) for r, e in rows],
        "total": total,
        "page": page,
        "pages": max(1, -(-total // size)),
        "page_size": size,
    }


def item_out(r: LiveRecording, event: Event | None = None, *, marks: int | None = None) -> dict:
    """One library card. Everything the grid, the list and the detail header need, and nothing that
    would let a client construct its own download — the storage key never leaves the server."""
    return {
        "id": str(r.id),
        "event_id": str(r.event_id),
        "event_title": event.title if event else None,
        "event_status": event.status if event else None,
        "title": r.title or (event.title if event else None) or "Untitled recording",
        "custom_title": r.title,
        "description": r.description,
        "folder_id": str(r.folder_id) if r.folder_id else None,
        "tags": list(r.tags or []),
        "category": r.category,
        "visibility": r.visibility,
        "status": r.status,
        "quality": r.quality,
        "size_bytes": int(r.size_bytes or 0),
        "duration_ms": int(r.duration_ms or 0),
        "view_count": r.view_count or 0,
        "download_count": r.download_count or 0,
        "last_viewed_at": _iso(r.last_viewed_at),
        "started_at": _iso(r.started_at),
        "stopped_at": _iso(r.stopped_at),
        "created_at": _iso(r.created_at),
        "archived_at": _iso(r.archived_at),
        "deleted_at": _iso(r.deleted_at),
        "storage_class": r.storage_class,
        "enforced": r.enforced,
        "error": r.error,
        # `has_file` is the single honest answer to "can this be played". A row can be `stopped`
        # with no bytes (egress died); the UI must show that difference, not a broken player.
        "has_file": bool(r.storage_key and r.size_bytes),
        "retryable": r.status == "failed" and (r.retry_count or 0) < 3,
        "retry_count": r.retry_count or 0,
        "retry_of": str(r.retry_of) if r.retry_of else None,
        "has_transcript": bool((r.transcript or {}).get("segments")),
        "transcript_language": (r.transcript or {}).get("language"),
        "has_insights": bool(r.insights),
        "download_policy": r.download_policy,
        "download_expires_at": _iso(r.download_expires_at),
        "password_protected": bool(r.download_password_hash),
        "watermark": r.watermark,
        "marks": marks,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ── mutations ─────────────────────────────────────────────────────────────────

def clean_tags(tags) -> list[str]:
    """Trim, cap, de-duplicate case-insensitively, preserve order.

    Case-insensitive de-dup matters: "Sales" and "sales" as two tags means two filter chips that
    each show half the recordings, which reads as data loss.
    """
    out, seen = [], set()
    for tag in (tags or []):
        clean = str(tag).strip()[:MAX_TAG_CHARS]
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(clean)
        if len(out) >= MAX_TAGS:
            break
    return out


def update(db, rec: LiveRecording, patch: dict) -> LiveRecording:
    """Rename / move / retag / recategorise / re-scope. Only the keys present are touched.

    `folder_id` is validated against the SAME org before it is written — otherwise a crafted id
    would file one tenant's recording inside another tenant's folder, which is a cross-tenant write
    even though every row involved stays where it is.
    """
    if "title" in patch:
        title = (patch["title"] or "").strip()
        # Empty means "go back to inheriting the event's title", which is why it stores NULL rather
        # than an empty string that would render as a blank card.
        rec.title = title[:200] or None
    if "description" in patch:
        rec.description = (patch["description"] or "").strip()[:5000] or None
    if "tags" in patch:
        rec.tags = clean_tags(patch["tags"])
    if "category" in patch:
        rec.category = (patch["category"] or None)
    if "visibility" in patch and patch["visibility"]:
        rec.visibility = patch["visibility"]
    if "folder_id" in patch:
        fid = _uuid(patch["folder_id"]) if patch["folder_id"] else None
        if fid is not None:
            folder = db.scalar(select(MediaFolder).where(
                MediaFolder.id == fid, MediaFolder.org_id == rec.org_id,
                MediaFolder.deleted_at.is_(None)))
            fid = folder.id if folder else None
        rec.folder_id = fid
    return rec


def set_archived(db, rec: LiveRecording, on: bool, user: User) -> LiveRecording:
    rec.archived_at = _now() if on else None
    rec.archived_by = user.id if on else None
    return rec


def soft_delete(db, rec: LiveRecording, user: User) -> LiveRecording:
    """Into the recycle bin. The object in storage is untouched — that is what makes restore a
    single UPDATE and what makes an accidental delete recoverable."""
    if rec.deleted_at is None:
        rec.deleted_at, rec.deleted_by = _now(), user.id
    return rec


def restore(db, rec: LiveRecording) -> LiveRecording:
    rec.deleted_at, rec.deleted_by = None, None
    return rec


def duplicate(db, rec: LiveRecording, user: User) -> LiveRecording:
    """A second library entry pointing at the SAME object.

    Deliberately not a byte copy: duplicating a 4 GB recording to give it different tags and a
    different audience would double the storage bill for a metadata change. Both entries share
    `storage_key`, which is exactly why a PURGE has to check for other referents before it deletes
    the object (see `purge_blockers`).

    Marks, view counts and download counts do NOT come across — they belong to the original.
    """
    copy = LiveRecording(
        event_id=rec.event_id, org_id=rec.org_id, session_id=rec.session_id,
        status=rec.status, quality=rec.quality,
        started_at=rec.started_at, stopped_at=rec.stopped_at, paused_ms=rec.paused_ms,
        size_bytes=rec.size_bytes, file_url=rec.file_url, storage_key=rec.storage_key,
        duration_ms=rec.duration_ms, enforced=rec.enforced,
        auto_upload=rec.auto_upload,
        title=f"{rec.title or 'Recording'} (copy)"[:200],
        description=rec.description,
        folder_id=rec.folder_id, tags=list(rec.tags or []), category=rec.category,
        # A copy starts PRIVATE regardless of the original's audience. Duplicating is usually the
        # first step in re-cutting something for a different audience, and inheriting `public`
        # would publish the draft the moment it was created.
        visibility="private",
        transcript=rec.transcript, insights=rec.insights,
        created_by=user.id,
    )
    db.add(copy)
    db.flush()
    return copy


def purge_blockers(db, rec: LiveRecording) -> int:
    """How many OTHER live rows point at this recording's object.

    A purge must delete the row always and the object only when nothing else references it — a
    duplicate sharing the key would otherwise be left pointing at bytes that are gone.
    """
    if not rec.storage_key:
        return 0
    return db.scalar(
        select(func.count()).select_from(LiveRecording).where(
            LiveRecording.storage_key == rec.storage_key,
            LiveRecording.id != rec.id,
            LiveRecording.deleted_at.is_(None),
        )
    ) or 0


def record_view(db, rec: LiveRecording) -> LiveRecording:
    rec.view_count = (rec.view_count or 0) + 1
    rec.last_viewed_at = _now()
    return rec


def record_download(db, rec: LiveRecording) -> LiveRecording:
    rec.download_count = (rec.download_count or 0) + 1
    return rec


# ── folders ───────────────────────────────────────────────────────────────────

def folders(db, org_id) -> list[dict]:
    """Every folder with its live recording count, in one query.

    The count is a correlated subquery rather than a GROUP BY join so that empty folders still
    appear — an inner join would silently hide a folder the moment its last recording was moved
    out, which looks exactly like the folder having been deleted.
    """
    count = (
        select(func.count(LiveRecording.id))
        .where(LiveRecording.folder_id == MediaFolder.id, LiveRecording.deleted_at.is_(None))
        .correlate(MediaFolder).scalar_subquery()
    )
    rows = db.execute(
        select(MediaFolder, count)
        .where(MediaFolder.org_id == org_id, MediaFolder.deleted_at.is_(None))
        .order_by(func.lower(MediaFolder.name))
    ).all()
    return [{
        "id": str(f.id),
        "name": f.name,
        "parent_id": str(f.parent_id) if f.parent_id else None,
        "colour": f.colour,
        "count": int(n or 0),
        "created_at": _iso(f.created_at),
    } for f, n in rows]


def create_folder(db, org_id, name: str, user: User, *, parent_id=None,
                  colour: str | None = None) -> tuple[MediaFolder | None, str | None]:
    """Returns (folder, error). Names are unique per parent — enforced by a partial unique index in
    create_tables.py, and pre-checked here only so the message is friendly."""
    clean = (name or "").strip()[:120]
    if not clean:
        return None, "A folder needs a name"
    parent = None
    if parent_id:
        parent = db.scalar(select(MediaFolder).where(
            MediaFolder.id == _uuid(parent_id), MediaFolder.org_id == org_id,
            MediaFolder.deleted_at.is_(None)))
        if parent is None:
            return None, "That parent folder no longer exists"
        if parent.parent_id is not None:
            # Two levels is a shelf; arbitrary depth is a filesystem, and a filesystem needs a
            # move-with-descendants operation, a breadcrumb and a cycle check nobody asked for.
            return None, "Folders can only be nested one level deep"
    existing = db.scalar(select(MediaFolder).where(
        MediaFolder.org_id == org_id,
        MediaFolder.parent_id == (parent.id if parent else None),
        func.lower(MediaFolder.name) == clean.lower(),
        MediaFolder.deleted_at.is_(None)))
    if existing is not None:
        return None, "A folder with that name already exists here"
    folder = MediaFolder(org_id=org_id, name=clean, parent_id=parent.id if parent else None,
                         colour=(colour or None), created_by=user.id)
    db.add(folder)
    db.flush()
    return folder, None


def rename_folder(db, folder: MediaFolder, name: str) -> tuple[MediaFolder | None, str | None]:
    clean = (name or "").strip()[:120]
    if not clean:
        return None, "A folder needs a name"
    clash = db.scalar(select(MediaFolder).where(
        MediaFolder.org_id == folder.org_id, MediaFolder.parent_id == folder.parent_id,
        func.lower(MediaFolder.name) == clean.lower(), MediaFolder.id != folder.id,
        MediaFolder.deleted_at.is_(None)))
    if clash is not None:
        return None, "A folder with that name already exists here"
    folder.name = clean
    return folder, None


def delete_folder(db, folder: MediaFolder) -> int:
    """Remove a folder. Recordings inside it fall back to the library root — deleting a shelf must
    never delete what was on it. Returns how many were moved out."""
    moved = db.execute(
        select(func.count()).select_from(LiveRecording)
        .where(LiveRecording.folder_id == folder.id)
    ).scalar() or 0
    db.query(LiveRecording).filter(LiveRecording.folder_id == folder.id).update(
        {LiveRecording.folder_id: None}, synchronize_session=False)
    # Child folders are promoted to the root for the same reason.
    db.query(MediaFolder).filter(MediaFolder.parent_id == folder.id).update(
        {MediaFolder.parent_id: None}, synchronize_session=False)
    folder.deleted_at = _now()
    return int(moved)


# ── bookmarks & notes ─────────────────────────────────────────────────────────

def marks(db, recording_id, user: User, org_id) -> list[dict]:
    """This person's marks plus everyone's SHARED marks, oldest position first.

    One query with an OR rather than two: the timeline renders them interleaved by position, so
    merging in Python would only mean sorting the same rows again.
    """
    rows = db.execute(
        select(MediaMark, User.full_name)
        .join(User, User.id == MediaMark.user_id, isouter=True)
        .where(MediaMark.recording_id == recording_id, MediaMark.org_id == org_id,
               or_(MediaMark.user_id == user.id, MediaMark.shared.is_(True)))
        .order_by(MediaMark.at_ms, MediaMark.created_at)
    ).all()
    return [{
        "id": str(m.id),
        "at_ms": m.at_ms,
        "note": m.note,
        "kind": "note" if m.note else "bookmark",
        "shared": m.shared,
        "mine": m.user_id == user.id,
        "author": name if m.shared else None,
        "created_at": _iso(m.created_at),
    } for m, name in rows]


def add_mark(db, rec: LiveRecording, user: User, at_ms: int, note: str | None,
             shared: bool) -> tuple[MediaMark | None, str | None]:
    """Add a bookmark (no note) or a note. Returns (mark, error)."""
    try:
        position = max(0, int(at_ms))
    except (TypeError, ValueError):
        return None, "A mark needs a position"
    # Clamped to the recording. A mark past the end of the file is a seek target that does nothing.
    if rec.duration_ms:
        position = min(position, int(rec.duration_ms))
    text = (note or "").strip()[:MAX_NOTE_CHARS] or None

    mine = db.scalar(select(func.count()).select_from(MediaMark).where(
        MediaMark.recording_id == rec.id, MediaMark.user_id == user.id)) or 0
    if mine >= MAX_MARKS_PER_RECORDING:
        return None, f"You already have {MAX_MARKS_PER_RECORDING} marks on this recording"

    if text is None:
        # Bookmarks are idempotent per position — the unique index enforces it, and returning the
        # existing row means a double-click is a no-op instead of a 500.
        existing = db.scalar(select(MediaMark).where(
            MediaMark.recording_id == rec.id, MediaMark.user_id == user.id,
            MediaMark.at_ms == position, MediaMark.note.is_(None)))
        if existing is not None:
            existing.shared = bool(shared)
            return existing, None

    mark = MediaMark(recording_id=rec.id, org_id=rec.org_id, user_id=user.id,
                     at_ms=position, note=text, shared=bool(shared))
    db.add(mark)
    db.flush()
    return mark, None


def delete_mark(db, mark_id, user: User, org_id) -> bool:
    """Only the author may remove their own mark, shared or not. An org_admin deleting somebody
    else's note is a moderation action this module does not have a use for."""
    mark = db.scalar(select(MediaMark).where(
        MediaMark.id == _uuid(mark_id), MediaMark.org_id == org_id, MediaMark.user_id == user.id))
    if mark is None:
        return False
    db.delete(mark)
    return True


# ── storage ───────────────────────────────────────────────────────────────────

def storage_stats(db, org_id, quota_gb: float) -> dict:
    """Usage against quota, from SUM(size_bytes) — the real figure, not organizations.storage_used_gb
    (which is an admin-entered number this module deliberately does not trust).

    Deleted recordings are counted SEPARATELY and included in the total, because a recycle bin that
    does not count towards the quota is a way to be over quota without being told.
    """
    live, archived, binned, count, archived_count, binned_count = db.execute(
        select(
            func.coalesce(func.sum(case(
                ((LiveRecording.deleted_at.is_(None)) & (LiveRecording.archived_at.is_(None)),
                 LiveRecording.size_bytes), else_=0)), 0),
            func.coalesce(func.sum(case(
                ((LiveRecording.deleted_at.is_(None)) & (LiveRecording.archived_at.isnot(None)),
                 LiveRecording.size_bytes), else_=0)), 0),
            func.coalesce(func.sum(case(
                (LiveRecording.deleted_at.isnot(None), LiveRecording.size_bytes), else_=0)), 0),
            func.count(case(((LiveRecording.deleted_at.is_(None)) &
                             (LiveRecording.archived_at.is_(None)), 1))),
            func.count(case(((LiveRecording.deleted_at.is_(None)) &
                             (LiveRecording.archived_at.isnot(None)), 1))),
            func.count(case((LiveRecording.deleted_at.isnot(None), 1))),
        ).where(LiveRecording.org_id == org_id)
    ).one()

    total = int(live) + int(archived) + int(binned)
    quota_bytes = int(max(0.0, float(quota_gb or 0)) * 1024 ** 3)
    return {
        "used_bytes": total,
        "active_bytes": int(live),
        "archived_bytes": int(archived),
        "recycle_bin_bytes": int(binned),
        "quota_bytes": quota_bytes,
        "remaining_bytes": max(0, quota_bytes - total) if quota_bytes else None,
        "percent_used": round(total * 100 / quota_bytes, 1) if quota_bytes else None,
        "counts": {"active": int(count), "archived": int(archived_count),
                   "recycle_bin": int(binned_count)},
    }


def library_totals(db, org_id) -> dict:
    """Header figures for the library: real counts and real sums over live rows only."""
    row = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(LiveRecording.view_count), 0),
            func.coalesce(func.sum(LiveRecording.download_count), 0),
            func.coalesce(func.sum(LiveRecording.duration_ms), 0),
            func.count(case((LiveRecording.status == "failed", 1))),
            func.count(case((LiveRecording.status.in_(("recording", "paused")), 1))),
        ).where(LiveRecording.org_id == org_id, LiveRecording.deleted_at.is_(None))
    ).one()
    count, views, downloads, duration, failed, capturing = row
    return {
        "recordings": int(count),
        "views": int(views),
        "downloads": int(downloads),
        "total_duration_ms": int(duration),
        # A real average or nothing — an average over zero recordings is not 0 minutes.
        "avg_duration_ms": int(int(duration) / count) if count else None,
        "failed": int(failed),
        "capturing": int(capturing),
    }


def filter_options(db, org_id) -> dict:
    """The distinct values actually present, so a filter chip never offers an empty result.

    Tags need a Python pass because they live in a JSON array; the alternative is a lateral
    jsonb_array_elements_text join, which is more SQL than a few thousand short lists deserve.
    """
    rows = db.execute(
        select(LiveRecording.tags, LiveRecording.category, LiveRecording.created_by)
        .where(LiveRecording.org_id == org_id, LiveRecording.deleted_at.is_(None))
    ).all()
    tags: Counter[str] = Counter()
    categories, hosts = set(), set()
    for tag_list, category, created_by in rows:
        tags.update(t for t in (tag_list or []) if t)
        if category:
            categories.add(category)
        if created_by:
            hosts.add(created_by)
    host_rows = db.execute(
        select(User.id, User.full_name).where(User.id.in_(hosts))
    ).all() if hosts else []
    return {
        "tags": [{"tag": t, "count": n} for t, n in tags.most_common(50)],
        "categories": sorted(categories),
        "hosts": [{"id": str(i), "name": n} for i, n in host_rows],
    }


# ── retention ─────────────────────────────────────────────────────────────────

def retention_candidates(db, org_id, action: str, age_days: int, *, limit: int = 500) -> list[LiveRecording]:
    """Recordings older than `age_days` that this action has not already been applied to.

    Age is measured from `stopped_at` (falling back to created_at) — when the capture finished, not
    when the row was made, which for a long event differ by hours.
    """
    cutoff = _now() - timedelta(days=int(age_days))
    stmt = select(LiveRecording).where(
        LiveRecording.org_id == org_id,
        LiveRecording.deleted_at.is_(None),
        func.coalesce(LiveRecording.stopped_at, LiveRecording.created_at) <= cutoff,
    )
    if action == "archive":
        stmt = stmt.where(LiveRecording.archived_at.is_(None))
    elif action == "cold":
        stmt = stmt.where(LiveRecording.storage_class.is_(None))
    # `delete` has no extra predicate: deleted_at IS NULL above is exactly the condition.
    return list(db.scalars(stmt.order_by(
        func.coalesce(LiveRecording.stopped_at, LiveRecording.created_at)).limit(limit)).all())


def expired_bin(db, org_id, days: int, *, limit: int = 500) -> list[LiveRecording]:
    """Recycle-bin rows past their retention window — the purge queue."""
    cutoff = _now() - timedelta(days=int(days))
    return list(db.scalars(
        select(LiveRecording).where(
            LiveRecording.org_id == org_id,
            LiveRecording.deleted_at.isnot(None),
            LiveRecording.deleted_at <= cutoff,
        ).order_by(LiveRecording.deleted_at).limit(limit)
    ).all())


# ── insight context ───────────────────────────────────────────────────────────

def insight_context(db, rec: LiveRecording) -> dict:
    """Assemble the real rows services.media.derive_insights reasons over.

    Five set-based queries for one recording. Everything is scoped by event AND org, and the text
    corpus is capped — a very long event must not turn insight generation into an unbounded read.
    """
    event = db.get(Event, rec.event_id)

    polls = list(db.scalars(select(LivePoll).where(
        LivePoll.event_id == rec.event_id, LivePoll.org_id == rec.org_id
    ).order_by(LivePoll.created_at)).all())
    announcements = list(db.scalars(select(LiveAnnouncement).where(
        LiveAnnouncement.event_id == rec.event_id, LiveAnnouncement.org_id == rec.org_id,
        LiveAnnouncement.sent_at.isnot(None)
    ).order_by(LiveAnnouncement.sent_at)).all())
    # Only the activity kinds that mark a real change of what is on screen. Chat and join/leave
    # noise would produce a chapter every few seconds.
    activity = list(db.scalars(select(LiveActivity).where(
        LiveActivity.event_id == rec.event_id, LiveActivity.org_id == rec.org_id,
        LiveActivity.kind.in_(("role", "recording", "system"))
    ).order_by(LiveActivity.created_at).limit(80)).all())
    samples = list(db.scalars(select(AnalyticsSnapshot).where(
        AnalyticsSnapshot.event_id == rec.event_id, AnalyticsSnapshot.org_id == rec.org_id
    ).order_by(AnalyticsSnapshot.created_at).limit(INSIGHT_SAMPLE_LIMIT)).all())

    messages = list(db.scalars(select(LiveMessage.text).where(
        LiveMessage.event_id == rec.event_id, LiveMessage.org_id == rec.org_id,
        LiveMessage.deleted_at.is_(None)
    ).limit(INSIGHT_TEXT_LIMIT)).all())
    questions = list(db.scalars(select(LiveQuestion.text).where(
        LiveQuestion.event_id == rec.event_id, LiveQuestion.org_id == rec.org_id
    ).limit(INSIGHT_TEXT_LIMIT)).all())

    answered = db.scalar(select(func.count()).select_from(LiveQuestion).where(
        LiveQuestion.event_id == rec.event_id, LiveQuestion.org_id == rec.org_id,
        LiveQuestion.status == "answered")) or 0

    # The transcript, when there is one, is by far the best corpus for keywords — real speech
    # rather than chat shorthand. Chat and Q&A stay in the mix because they are what the audience
    # cared about, which is different information.
    texts = list(messages) + list(questions)
    for seg in ((rec.transcript or {}).get("segments") or [])[:INSIGHT_TEXT_LIMIT]:
        texts.append(seg.get("text") or "")

    poll_votes = sum(
        int(o.get("votes") or 0)
        for p in polls for o in (p.options or []) if isinstance(o, dict)
    )
    return {
        "event_tags": list((event.tags if event else None) or []),
        "event_category": event.category if event else None,
        "polls": [{"question": p.question, "status": p.status, "options": p.options or [],
                   "launched_at": p.launched_at, "closed_at": p.closed_at} for p in polls],
        "announcements": [{"text": a.text, "sent_at": a.sent_at} for a in announcements],
        "activity": [{"kind": a.kind, "text": a.text, "created_at": a.created_at} for a in activity],
        "samples": [{"created_at": s.created_at, "viewers": s.viewers, "messages": s.messages,
                     "questions": s.questions, "reactions": s.reactions, "hands": s.hands}
                    for s in samples],
        "texts": texts,
        "totals": {
            "messages": len(messages),
            "questions": len(questions),
            "questions_answered": int(answered),
            "polls": len(polls),
            "poll_votes": poll_votes,
            "reactions": sum(int(s.reactions or 0) for s in samples),
            "hands": max((int(s.hands or 0) for s in samples), default=0),
            "peak_viewers": max((int(s.viewers or 0) for s in samples), default=0),
        },
        "truncated": len(messages) >= INSIGHT_TEXT_LIMIT or len(samples) >= INSIGHT_SAMPLE_LIMIT,
    }
