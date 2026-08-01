"""DB access + validation helpers for the Events API. Pure queries and partial updates,
no HTTP. Mirrors crud/organization.py."""

import re
from datetime import datetime, timezone

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from ..models import Event, EventAssignment, User

_EVENT_SORTS = {
    "created_at": Event.created_at,
    "start_time": Event.start_time,
    "title": Event.title,
    "status": Event.status,
}


# ── Lifecycle validation (pure — unit-testable without a DB) ──────────────────

def status_transition_error(current: str, new: str, title) -> str | None:
    """Return an error message if current -> new is not allowed, else None.
    Encodes exactly the four spec rules; other transitions are permitted."""
    if new == current:
        return None
    if new in ("published", "scheduled") and not (title and str(title).strip()):
        return "Cannot publish an event without a title"
    if new == "live" and current not in ("published", "scheduled"):
        return "Cannot go live unless the event is published"
    if new == "ended" and current != "live":
        return "Cannot end an event that is not live"
    if new == "archived" and current == "live":
        return "Cannot archive a live event"
    return None


# ── Slugs (unique within org) ─────────────────────────────────────────────────

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "event"


def event_slug_taken(db, org_id, slug, exclude_id=None) -> bool:
    stmt = select(Event.id).where(
        func.lower(Event.slug) == slug.lower(),
        Event.org_id == org_id,
        Event.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Event.id != exclude_id)
    return db.scalar(stmt) is not None


def unique_event_slug(db, org_id, base: str, exclude_id=None) -> str:
    base = slugify(base)
    candidate, n = base, 1
    while event_slug_taken(db, org_id, candidate, exclude_id):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


# ── Events ────────────────────────────────────────────────────────────────────

def list_events(db, org_id, q=None, status=None, host_id=None, date_from=None, date_to=None,
                sort_by="created_at", order="desc", page=1, page_size=20):
    stmt = select(Event).where(Event.org_id == org_id, Event.deleted_at.is_(None))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Event.title).like(like),
                              func.lower(Event.description).like(like)))
    if status:
        stmt = stmt.where(Event.status == status)
    if host_id:
        stmt = stmt.where(Event.created_by == host_id)
    if date_from:
        stmt = stmt.where(Event.start_time >= date_from)
    if date_to:
        stmt = stmt.where(Event.start_time <= date_to)

    col = _EVENT_SORTS.get(sort_by, Event.created_at)
    stmt = stmt.order_by(asc(col) if order == "asc" else desc(col))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return items, total


def get_event(db, org_id, event_id) -> Event | None:
    return db.scalar(
        select(Event).where(Event.id == event_id, Event.org_id == org_id, Event.deleted_at.is_(None))
    )


def get_event_unscoped(db, event_id) -> Event | None:
    """Not org-scoped: the /watch page is reachable by a signed-out visitor, who by
    definition isn't a member of the event's org. The endpoint itself enforces
    visibility (private events still require the caller to belong to the org)."""
    return db.scalar(select(Event).where(Event.id == event_id, Event.deleted_at.is_(None)))


def create_event(db, org_id, created_by, data, slug) -> Event:
    ev = Event(org_id=org_id, created_by=created_by, slug=slug,
               **data.model_dump(exclude={"slug"}))
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def update_event(db, event: Event, fields: dict) -> Event:
    for key, value in fields.items():
        setattr(event, key, value)
    db.commit()
    db.refresh(event)
    return event


def soft_delete_event(db, event: Event) -> None:
    event.deleted_at = datetime.now(timezone.utc)
    db.commit()


# ── Assignments (host / moderator / speaker) ──────────────────────────────────

def valid_member_ids(db, org_id, user_ids) -> set:
    """Subset of user_ids that are live members of this org."""
    if not user_ids:
        return set()
    rows = db.scalars(
        select(User.id).where(User.id.in_(user_ids), User.org_id == org_id, User.deleted_at.is_(None))
    ).all()
    return set(rows)


def list_assignees(db, event_id, role) -> list:
    return db.scalars(
        select(User)
        .join(EventAssignment, EventAssignment.user_id == User.id)
        .where(EventAssignment.event_id == event_id, EventAssignment.role == role,
               User.deleted_at.is_(None))
        .order_by(User.full_name)
    ).all()


def is_assigned(db, event_id, user_id, role) -> bool:
    return db.scalar(
        select(EventAssignment.id).where(
            EventAssignment.event_id == event_id,
            EventAssignment.user_id == user_id,
            EventAssignment.role == role,
        )
    ) is not None


def set_assignees(db, event, role, user_ids) -> None:
    """Replace the full set of `role` assignees for the event."""
    for a in db.scalars(
        select(EventAssignment).where(EventAssignment.event_id == event.id, EventAssignment.role == role)
    ).all():
        db.delete(a)
    db.flush()
    for uid in dict.fromkeys(user_ids):  # dedupe, keep order
        db.add(EventAssignment(event_id=event.id, user_id=uid, role=role))
    db.commit()
