"""Attendee persistence: registrations, personalisation and watch history.

One table behind all of it (models.attendee.EventRegistration) — see that module for why.

Isolation note: an attendee is legitimately OUTSIDE the organizing org (that is what a public
event means), so these queries scope by `user_id`, not by the caller's org. `org_id` is copied
from the event so an ORGANIZER's reporting can still be org-scoped without a join. Every function
that takes an event resolves it through the viewer access rules in the router first; nothing here
decides visibility.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import case, func, or_, select

from ..models import Event, EventRegistration, SpeakerAsset, User

# A heartbeat claims at most this many seconds of watch time, whatever the client says. The client
# reports an interval; a tab that was suspended for an hour must not bank an hour of "watching".
MAX_HEARTBEAT_SECONDS = 120
# Saved questions per event. A reading aid, not a library.
MAX_QUESTION_BOOKMARKS = 50


def _now():
    return datetime.now(timezone.utc)


def get_registration(db, event_id, user_id) -> EventRegistration | None:
    return db.scalar(
        select(EventRegistration).where(
            EventRegistration.event_id == event_id,
            EventRegistration.user_id == user_id,
        )
    )


def ensure_row(db, event: Event, user: User) -> EventRegistration:
    """The attendee's row for this event, created empty if absent.

    Created by ANY interaction — bookmarking, watching, saving a question — not just by
    registering, which is why `status` starts as `cancelled` rather than `registered`. Bookmarking
    an event you have not signed up for must not smuggle you past registration enforcement.
    """
    row = get_registration(db, event.id, user.id)
    if row is None:
        row = EventRegistration(
            event_id=event.id, org_id=event.org_id, user_id=user.id,
            name=user.full_name, email=user.email,
            status="cancelled", question_bookmarks=[],
        )
        db.add(row)
        db.flush()
    return row


def is_registered(db, event_id, user_id) -> bool:
    """Registration enforcement reads exactly this. `attended` counts — somebody who got in
    before the organizer switched enforcement on is not thrown out mid-event."""
    row = get_registration(db, event_id, user_id)
    return bool(row and row.status in ("registered", "attended"))


def registration_count(db, event_id) -> int:
    return db.scalar(
        select(func.count()).select_from(EventRegistration).where(
            EventRegistration.event_id == event_id,
            EventRegistration.status.in_(("registered", "attended")),
        )
    ) or 0


def register(db, event: Event, user: User) -> tuple[EventRegistration | None, str | None]:
    """Sign up. Returns (row, error). Idempotent — registering twice is not an error.

    The capacity check is the organizer's `registration_limit`. It is checked here and not in a
    constraint because "full" is a friendly refusal, not a corrupted state; the race it leaves
    (two simultaneous sign-ups on the last seat) would need SELECT FOR UPDATE and is not worth a
    lock on a number the organizer set as a soft target.
    """
    row = ensure_row(db, event, user)
    if row.status in ("registered", "attended"):
        return row, None
    if event.registration_limit and registration_count(db, event.id) >= event.registration_limit:
        return None, "This event is full."
    row.status = "registered"
    row.registered_at = _now()
    row.cancelled_at = None
    # Refresh the snapshot: a name changed since the last sign-up should not persist stale.
    row.name, row.email = user.full_name, user.email
    return row, None


def cancel(db, event: Event, user: User) -> EventRegistration:
    """Cancel, keeping the row so watch history, bookmarks and the audit trail survive."""
    row = ensure_row(db, event, user)
    if row.status != "cancelled":
        row.status = "cancelled"
        row.cancelled_at = _now()
    return row


def set_bookmark(db, event: Event, user: User, on: bool) -> EventRegistration:
    row = ensure_row(db, event, user)
    row.bookmarked = bool(on)
    return row


def set_reminder(db, event: Event, user: User, at: datetime | None) -> EventRegistration:
    row = ensure_row(db, event, user)
    row.reminder_at = at
    return row


def toggle_question_bookmark(db, event: Event, user: User, question_id: str) -> tuple[list, bool]:
    """Save or unsave one question. Returns (ids, saved_now)."""
    row = ensure_row(db, event, user)
    ids = list(row.question_bookmarks or [])
    qid = str(question_id)
    if qid in ids:
        ids.remove(qid)
        saved = False
    else:
        # Newest first, capped — a reading aid, so the oldest saved question falls off.
        ids = [qid, *ids][:MAX_QUESTION_BOOKMARKS]
        saved = True
    row.question_bookmarks = ids
    return ids, saved


def record_join(db, event: Event, user: User) -> EventRegistration:
    """First frame of a watch session.

    Sets `attended` — but only from `registered`. A cancelled registration that starts watching is
    NOT promoted: on an enforced event the media is refused anyway, and on an unenforced one their
    history is still recorded without inventing a registration they never made.
    """
    row = ensure_row(db, event, user)
    now = _now()
    row.first_joined_at = row.first_joined_at or now
    row.last_joined_at = now
    row.join_count = (row.join_count or 0) + 1
    if row.status == "registered":
        row.status = "attended"
    return row


def record_watch(db, event: Event, user: User, seconds: int) -> EventRegistration:
    """Accumulate watch time from a heartbeat. Clamped — see MAX_HEARTBEAT_SECONDS."""
    row = ensure_row(db, event, user)
    try:
        claimed = int(seconds)
    except (TypeError, ValueError):
        claimed = 0
    row.watch_seconds = (row.watch_seconds or 0) + max(0, min(claimed, MAX_HEARTBEAT_SECONDS))
    row.last_joined_at = _now()
    return row


# ── the dashboard ─────────────────────────────────────────────────────────────

# Statuses an attendee may see at all. Mirrors services.viewer.VIEWABLE_STATUSES — a draft or
# archived event does not exist as far as the audience is concerned.
DASHBOARD_STATUSES = ("published", "scheduled", "live", "paused", "ended", "cancelled")


def my_events(db, user: User, *, limit: int = 300) -> list[dict]:
    """Every event this attendee has a relationship with, PLUS every event they can discover.

    One query, two sources joined in SQL rather than in the browser:
      * events they registered for, bookmarked, or watched (their row exists), from any org;
      * public and unlisted events, which are open to any signed-in attendee.

    Private and invite-only events they have no row for are excluded — the same rule
    services.viewer.access_for applies, expressed as a WHERE clause so the dashboard cannot list
    an event whose page would 404.

    ORDER matters more than it looks, because the LIMIT decides what an attendee never sees. Three
    keys, and each one earns its place:
      1. on air first — the only rows with any urgency;
      2. then MINE — a registration, bookmark or watch history must never be truncated away by a
         public catalogue that happens to be larger;
      3. then nearest in time, past or future. Ordering by start_time ascending (the obvious
         choice) surfaces the OLDEST events in the system and buries tomorrow's, which is exactly
         backwards for a dashboard.
    """
    reg = EventRegistration
    on_air = case((Event.status.in_(("live", "paused")), 0), else_=1)
    mine = case((reg.id.isnot(None), 0), else_=1)
    # Absolute distance from now, in seconds. NULLs (an unscheduled event) sort last.
    distance = func.abs(func.extract("epoch", Event.start_time - func.now()))
    stmt = (
        select(Event, reg)
        .outerjoin(reg, (reg.event_id == Event.id) & (reg.user_id == user.id))
        .where(Event.deleted_at.is_(None), Event.status.in_(DASHBOARD_STATUSES))
        .where(or_(
            reg.id.isnot(None),                              # I have a relationship with it
            Event.visibility.in_(("public", "unlisted")),     # or it is open to me
            Event.org_id == user.org_id,                      # or it is my own org's
        ))
        .order_by(on_air, mine, distance.nulls_last())
        .limit(limit)
    )
    return [_row_out(ev, r) for ev, r in db.execute(stmt).all()]


def _row_out(ev: Event, r: EventRegistration | None) -> dict:
    return {
        "id": str(ev.id),
        "title": ev.title,
        "slug": ev.slug,
        "short_description": ev.short_description,
        "banner_image": ev.banner_image,
        "thumbnail": ev.thumbnail,
        "category": ev.category,
        "tags": ev.tags or [],
        "language": ev.language,
        "timezone": ev.timezone,
        "location": ev.location,
        "status": ev.status,
        "visibility": ev.visibility,
        "start_time": ev.start_time.isoformat() if ev.start_time else None,
        "end_time": ev.end_time.isoformat() if ev.end_time else None,
        "registration_required": ev.registration_required,
        "replay_enabled": ev.replay_enabled,
        "recording_enabled": ev.recording_enabled,
        # My relationship with it. All null/false when I have never touched it, which is what lets
        # the dashboard bucket "discover" separately from "mine" without a second request.
        "registration_status": r.status if r else None,
        "registered": bool(r and r.status in ("registered", "attended")),
        "bookmarked": bool(r and r.bookmarked),
        "reminder_at": r.reminder_at.isoformat() if r and r.reminder_at else None,
        "watch_seconds": (r.watch_seconds or 0) if r else 0,
        "last_joined_at": r.last_joined_at.isoformat() if r and r.last_joined_at else None,
        "join_count": (r.join_count or 0) if r else 0,
    }


# ── shared resources ──────────────────────────────────────────────────────────

def shared_assets(db, event_id) -> list[SpeakerAsset]:
    """Files an attendee may download: approved AND explicitly shared.

    Both flags are required. Approval means "may go on the main screen"; sharing means "ten
    thousand people may fetch this file". Treating them as one decision is how an unreleased deck
    leaks.
    """
    return list(db.scalars(
        select(SpeakerAsset).where(
            SpeakerAsset.event_id == event_id,
            SpeakerAsset.deleted_at.is_(None),
            SpeakerAsset.status == "approved",
            SpeakerAsset.shared.is_(True),
        ).order_by(SpeakerAsset.created_at)
    ).all())


def shared_asset(db, event_id, asset_id) -> SpeakerAsset | None:
    try:
        aid = uuid.UUID(str(asset_id))
    except (ValueError, TypeError, AttributeError):
        return None
    return db.scalar(
        select(SpeakerAsset).where(
            SpeakerAsset.id == aid,
            SpeakerAsset.event_id == event_id,
            SpeakerAsset.deleted_at.is_(None),
            SpeakerAsset.status == "approved",
            SpeakerAsset.shared.is_(True),
        )
    )


def resource_out(a: SpeakerAsset) -> dict:
    """Attendee-safe projection. Deliberately omits the uploader's user id, the review note and
    the internal status — an attendee gets a filename, a size and a type."""
    return {
        "id": str(a.id),
        "filename": a.filename,
        "content_type": a.content_type,
        "kind": a.kind,
        "size_bytes": int(a.size_bytes or 0),
        "pages": a.pages,
        "shared_by": a.uploader_name,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
