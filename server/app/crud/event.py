"""DB access + validation helpers for the Events API. Pure queries and partial updates,
no HTTP. Mirrors crud/organization.py."""

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import asc, desc, func, or_, select, update
from sqlalchemy.orm import Session

from ..models import (
    AnalyticsSnapshot,
    Event,
    EventAccessLink,
    EventAssignment,
    Invitation,
    LiveActivity,
    LivePoll,
    LiveQuestion,
    LiveRecording,
    OPEN_STATUSES,
    Organization,
    User,
)

_EVENT_SORTS = {
    "created_at": Event.created_at,
    "start_time": Event.start_time,
    "end_time": Event.end_time,
    "title": Event.title,
    "status": Event.status,
    "updated_at": Event.updated_at,
}

# Default lifetime for a generated viewer link. Same 7 days as an org invitation
# (crud.organization.create_invitation) so the two share one mental model.
ACCESS_LINK_DAYS = 7


# ── Lifecycle validation (pure — unit-testable without a DB) ──────────────────
# ONE function, reused by POST /events, PATCH /events/{id}, POST /events/bulk and the
# broadcast socket actions (services.broadcast._golive/_pause/_resume/_end). Adding a rule
# here closes it on every path at once, which is the whole reason it is not inlined.

def status_transition_error(current: str, new: str, title) -> str | None:
    """Return an error message if current -> new is not allowed, else None.

    Cancellation is deliberately unrestricted from every state (including `live`): an
    organizer pulling an event mid-incident must not be blocked by a state machine.
    """
    if new == current:
        return None
    if new in ("published", "scheduled") and not (title and str(title).strip()):
        return "Cannot publish an event without a title"
    # published|scheduled -> draft is "unpublish". Anything else back to draft would
    # resurrect a finished or cancelled event, which is what archive/duplicate are for.
    if new == "draft" and current not in ("published", "scheduled"):
        return "Only a published or scheduled event can be moved back to draft"
    # `paused` is a resumable live state, so it is a legal springboard back to live.
    if new == "live" and current not in ("published", "scheduled", "paused"):
        return "Cannot go live unless the event is published"
    if new == "paused" and current != "live":
        return "Only a live event can be paused"
    if new == "ended" and current not in ("live", "paused"):
        return "Cannot end an event that is not live"
    if new == "archived" and current in ("live", "paused"):
        return "Cannot archive an event that is still on air"
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
                sort_by="created_at", order="desc", page=1, page_size=20,
                visibility=None, category=None, statuses=None, assigned_to=None,
                assigned_roles=None):
    stmt = select(Event).where(Event.org_id == org_id, Event.deleted_at.is_(None))

    # "Events I am assigned to" — what a host's own dashboard shows, as opposed to every event
    # in the organization. An EXISTS subquery rather than a join: a person can hold several roles
    # on one event (host AND speaker), and a join would return that event twice and corrupt both
    # the page and the total.
    if assigned_to is not None:
        cond = [EventAssignment.event_id == Event.id, EventAssignment.user_id == assigned_to]
        if assigned_roles:
            cond.append(EventAssignment.role.in_(list(assigned_roles)))
        stmt = stmt.where(select(EventAssignment.id).where(*cond).exists())
    if q:
        like = f"%{q.lower()}%"
        # Slug included so the console's search box matches what it displays under the title.
        stmt = stmt.where(or_(func.lower(Event.title).like(like),
                              func.lower(Event.description).like(like),
                              func.lower(Event.slug).like(like)))
    if status:
        stmt = stmt.where(Event.status == status)
    # `statuses` powers the console's multi-select filter; `status` stays for the existing
    # single-value callers (hooks/useLiveEvent.js asks for status=live).
    if statuses:
        stmt = stmt.where(Event.status.in_(list(statuses)))
    if visibility:
        stmt = stmt.where(Event.visibility == visibility)
    if category:
        stmt = stmt.where(Event.category == category)
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


def recent_activity(db, org_id, *, assigned_to=None, limit=25) -> list[dict]:
    """The activity feed across several events, for a dashboard rather than one console.

    The in-console feed (services/moderation) reads one event; this answers "what happened on
    MY events". Org-scoped, and narrowed to the caller's assignments when asked, so it can never
    surface another tenant's — or another host's — room.
    """
    stmt = (
        select(LiveActivity, Event.title)
        .join(Event, Event.id == LiveActivity.event_id)
        .where(LiveActivity.org_id == org_id, Event.deleted_at.is_(None))
        .order_by(LiveActivity.created_at.desc())
        .limit(min(limit, 100))
    )
    if assigned_to is not None:
        stmt = stmt.where(
            select(EventAssignment.id).where(
                EventAssignment.event_id == Event.id,
                EventAssignment.user_id == assigned_to,
            ).exists()
        )
    return [
        {
            "id": str(a.id), "event_id": str(a.event_id), "event_title": title,
            "kind": a.kind, "text": a.text, "actor": a.actor_name,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a, title in db.execute(stmt).all()
    ]


def get_event(db, org_id, event_id) -> Event | None:
    return db.scalar(
        select(Event).where(Event.id == event_id, Event.org_id == org_id, Event.deleted_at.is_(None))
    )


def create_event(db, org_id, created_by, data, slug, password_hash: str | None = None) -> Event:
    """`access_password` is dropped from the payload here and the already-hashed value is
    passed separately — hashing stays in the router, matching how the invitation and reset
    flows do it (routers/organization.py). A plaintext passphrase never reaches this layer."""
    ev = Event(org_id=org_id, created_by=created_by, slug=slug,
               access_password_hash=password_hash,
               **data.model_dump(exclude={"slug", "access_password"}))
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
    """Soft delete, and close out anything still pointing at this event.

    Open invitations are cancelled here rather than at the accept path alone: the accept path
    already refuses (it re-resolves the event org-scoped, and a deleted event resolves to
    None), but leaving them `pending` would show the organizer a live invitation to an event
    that no longer exists. One edit covers both callers — DELETE /events/{id} and the bulk
    delete verb — which is why this helper exists.
    """
    event.deleted_at = datetime.now(timezone.utc)
    db.execute(
        update(Invitation)
        .where(
            Invitation.event_id == event.id,
            Invitation.status.in_(OPEN_STATUSES),
            Invitation.deleted_at.is_(None),
        )
        .values(status="cancelled")
    )
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
    """Replace the full set of `role` assignees for the event.

    `dict.fromkeys` is the duplicate-prevention: the same user twice in one request
    collapses to one row, which is also what the UNIQUE(event_id,user_id,role) constraint
    would otherwise reject mid-transaction.
    """
    for a in db.scalars(
        select(EventAssignment).where(EventAssignment.event_id == event.id, EventAssignment.role == role)
    ).all():
        db.delete(a)
    db.flush()
    for uid in dict.fromkeys(user_ids):  # dedupe, keep order
        db.add(EventAssignment(event_id=event.id, user_id=uid, role=role))
    db.commit()


def add_assignee(db, event, role, user_id) -> bool:
    """Add one assignee. Returns False when the row already exists (idempotent add, so a
    double-click is not a 409 the operator has to think about)."""
    if add_assignee_pending(db, event.id, role, user_id):
        db.commit()
        return True
    return False


def add_assignee_pending(db, event_id, role, user_id) -> bool:
    """add_assignee without the commit, for callers that own a wider transaction.

    The invitation accept path needs this: the assignment, the member row and the invitation's
    status change must all land together, so a failure anywhere cannot leave a half-accepted
    invitation. Still idempotent — an admin who added the person manually while the invitation
    was open must not turn their accept into a uq_event_user_role violation.
    """
    if is_assigned(db, event_id, user_id, role):
        return False
    db.add(EventAssignment(event_id=event_id, user_id=user_id, role=role))
    db.flush()
    return True


def remove_assignee(db, event_id, role, user_id) -> bool:
    """Remove one assignee. Returns whether a row was actually deleted."""
    removed = remove_assignee_pending(db, event_id, role, user_id)
    if removed:
        db.commit()
    return removed


def remove_assignee_pending(db, event_id, role, user_id) -> bool:
    """remove_assignee without the commit, for callers that own a wider transaction.

    Revoking an invitation needs this: the status change and the assignment removal must land
    together, or a failure between them leaves the row reading `revoked` while the person still
    holds the role.
    """
    row = db.scalar(
        select(EventAssignment).where(
            EventAssignment.event_id == event_id,
            EventAssignment.user_id == user_id,
            EventAssignment.role == role,
        )
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def team(db, event_id) -> dict[str, list[User]]:
    """Every assignee of an event, grouped by role, in ONE query.

    The per-role endpoints still exist, but the detail page needs all six roles at once —
    six round trips for one panel is the N+1 the console kept paying (audit P9).
    """
    rows = db.execute(
        select(EventAssignment.role, User)
        .join(User, User.id == EventAssignment.user_id)
        .where(EventAssignment.event_id == event_id, User.deleted_at.is_(None))
        .order_by(EventAssignment.role, User.full_name)
    ).all()
    out: dict[str, list[User]] = {}
    for role, user in rows:
        out.setdefault(role, []).append(user)
    return out


# ── List enrichment (one grouped query per fact, never per row) ───────────────

def summarize(db, events: list[Event]) -> dict:
    """Dashboard-column facts for a page of events, keyed by event id.

    Deliberately built from four set-based queries rather than per-event lookups: the
    console renders 25 rows and each of these would otherwise be an N+1 (the exact shape
    the audit flagged at services/admin._streaming_hours).

    `registrations` is absent on purpose. There is no registrations table in this platform
    (documented in services/viewer.py), so the honest answer is "not collected" — a zero
    here would read as "nobody signed up", which is a different and false statement.
    """
    ids = [e.id for e in events]
    if not ids:
        return {}

    counts: dict[uuid.UUID, dict] = {
        eid: {"team": {}, "viewers": None, "has_recording": False, "has_replay": False,
              "links": 0,
              # Moderation queue. hands/waiting are None until a live sample exists, so a
              # scheduled event reads "—" rather than a zero that looks measured.
              "hands": None, "waiting": None, "open_questions": 0, "live_polls": 0}
        for eid in ids
    }

    # Live (not revoked) viewer links per event — the "Viewer Access" column.
    for event_id, n in db.execute(
        select(EventAccessLink.event_id, func.count(EventAccessLink.id))
        .where(EventAccessLink.event_id.in_(ids), EventAccessLink.revoked_at.is_(None))
        .group_by(EventAccessLink.event_id)
    ).all():
        counts[event_id]["links"] = int(n)

    # Team head-count per role.
    for event_id, role, n in db.execute(
        select(EventAssignment.event_id, EventAssignment.role, func.count(EventAssignment.id))
        .join(User, User.id == EventAssignment.user_id)
        .where(EventAssignment.event_id.in_(ids), User.deleted_at.is_(None))
        .group_by(EventAssignment.event_id, EventAssignment.role)
    ).all():
        counts[event_id]["team"][role] = int(n)

    # Current audience for events that are on air, from the newest analytics sample the
    # broadcast sampler wrote. A real measurement or nothing — never an estimate.
    live_ids = [e.id for e in events if e.status in ("live", "paused")]
    if live_ids:
        newest = (
            select(AnalyticsSnapshot.event_id,
                   func.max(AnalyticsSnapshot.created_at).label("at"))
            .where(AnalyticsSnapshot.event_id.in_(live_ids))
            .group_by(AnalyticsSnapshot.event_id)
            .subquery()
        )
        # Raised hands and lobby depth ride along on the SAME row — the moderator dashboard
        # needs "how deep is the queue on each of my events" and this costs no extra query.
        # 15s old by construction (that is the sampler's cadence); the console shows live.
        for event_id, viewers, hands, waiting in db.execute(
            select(AnalyticsSnapshot.event_id, AnalyticsSnapshot.viewers,
                   AnalyticsSnapshot.hands, AnalyticsSnapshot.waiting).join(
                newest,
                (newest.c.event_id == AnalyticsSnapshot.event_id)
                & (newest.c.at == AnalyticsSnapshot.created_at),
            )
        ).all():
            counts[event_id]["viewers"] = int(viewers)
            counts[event_id]["hands"] = int(hands)
            counts[event_id]["waiting"] = int(waiting)

    # The moderator's actual to-do list. Scoped to `ids`, not `live_ids`: questions arrive
    # before an event goes live and a moderator wants them triaged by then. Two grouped counts
    # over an indexed event_id — the same set-based shape as everything else here.
    for model, key, condition in (
        (LiveQuestion, "open_questions", LiveQuestion.status == "pending"),
        (LivePoll, "live_polls", LivePoll.status == "live"),
    ):
        for event_id, n in db.execute(
            select(model.event_id, func.count(model.id))
            .where(model.event_id.in_(ids), condition)
            .group_by(model.event_id)
        ).all():
            counts[event_id][key] = int(n)

    # Recording presence, and whether any of them produced a file worth replaying.
    for event_id, file_url in db.execute(
        select(LiveRecording.event_id, LiveRecording.file_url)
        .where(LiveRecording.event_id.in_(ids))
    ).all():
        counts[event_id]["has_recording"] = True
        if file_url:
            counts[event_id]["has_replay"] = True

    return counts


def actor_names(db, events: list[Event]) -> dict:
    """created_by -> display name, for the "Created By" column. One IN query."""
    ids = {e.created_by for e in events if e.created_by}
    if not ids:
        return {}
    return {
        u.id: (u.full_name or u.email)
        for u in db.scalars(select(User).where(User.id.in_(ids))).all()
    }


def org_name(db, org_id) -> str | None:
    org = db.get(Organization, org_id)
    return org.name if org else None


def distinct_categories(db, org_id) -> list[str]:
    """Categories actually in use by this org — the filter dropdown's option list, so it
    can never offer a category that matches nothing."""
    return [
        c for c in db.scalars(
            select(Event.category)
            .where(Event.org_id == org_id, Event.deleted_at.is_(None), Event.category.isnot(None))
            .distinct()
            .order_by(Event.category)
        ).all() if c
    ]


# ── Duplicate ─────────────────────────────────────────────────────────────────

# Everything that describes HOW the event runs. Excluded by omission: identity (id, slug),
# ownership/audit (org_id, created_by, timestamps), lifecycle (status), the access password,
# and the schedule — a copy starts as an unscheduled draft rather than silently inheriting a
# date that has already passed.
_DUPLICABLE = (
    "description", "short_description", "banner_image", "thumbnail", "category", "tags",
    "language", "timezone", "location", "visibility", "registration_required",
    "registration_limit", "max_participants", "captions_enabled", "translation_enabled",
    "waiting_room_enabled", "recording_enabled", "chat_enabled", "qa_enabled",
    "polls_enabled", "raise_hand_enabled", "allow_screen_share", "auto_start_recording",
    "auto_end_event", "replay_enabled", "stream_quality", "impact",
)


def duplicate_event(db, source: Event, created_by, title: str, slug: str,
                    copy_team: bool = True) -> Event:
    """Copy an event's configuration into a fresh draft.

    Never copied: the access password (a secret is not a template), access links (a link is
    bound to one event's audience), the schedule, and any live/recording history.
    """
    clone = Event(
        org_id=source.org_id,
        created_by=created_by,
        title=title,
        slug=slug,
        status="draft",
        **{field: getattr(source, field) for field in _DUPLICABLE},
    )
    db.add(clone)
    db.flush()
    if copy_team:
        for a in db.scalars(
            select(EventAssignment).where(EventAssignment.event_id == source.id)
        ).all():
            db.add(EventAssignment(event_id=clone.id, user_id=a.user_id, role=a.role))
    db.commit()
    db.refresh(clone)
    return clone


# ── Viewer access links ───────────────────────────────────────────────────────
# Same construction as crud.organization's invitation tokens: urlsafe secret out, sha256
# hash in. _hash_token is duplicated rather than imported because the two modules must be
# able to change their token format independently.

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, _hash_token(raw)


def list_access_links(db, event_id) -> list[EventAccessLink]:
    return list(db.scalars(
        select(EventAccessLink)
        .where(EventAccessLink.event_id == event_id)
        .order_by(EventAccessLink.created_at.desc())
    ).all())


def get_access_link(db, event_id, link_id) -> EventAccessLink | None:
    return db.scalar(
        select(EventAccessLink).where(
            EventAccessLink.id == link_id, EventAccessLink.event_id == event_id
        )
    )


def create_access_link(db, event: Event, label, created_by,
                       expires_in_days: int | None = ACCESS_LINK_DAYS) -> tuple[EventAccessLink, str]:
    """Returns (row, raw_token). The raw token is the caller's ONLY chance to see it."""
    raw, token_hash = _new_token()
    link = EventAccessLink(
        event_id=event.id,
        org_id=event.org_id,
        label=(label or None),
        token_hash=token_hash,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=expires_in_days))
        if expires_in_days else None,
        created_by=created_by,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link, raw


def rotate_access_link(db, link: EventAccessLink,
                       expires_in_days: int | None = ACCESS_LINK_DAYS) -> tuple[EventAccessLink, str]:
    """Regenerate: new secret on the SAME row, so the label and usage history survive and
    the previous token stops working the moment this commits."""
    raw, token_hash = _new_token()
    link.token_hash = token_hash
    link.revoked_at = None
    link.expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)) if expires_in_days else None
    db.commit()
    db.refresh(link)
    return link, raw


def revoke_access_link(db, link: EventAccessLink) -> EventAccessLink:
    """Soft: the row stays so a revoked link remains auditable."""
    if link.revoked_at is None:
        link.revoked_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(link)
    return link


def delete_access_link(db, link: EventAccessLink) -> None:
    db.delete(link)
    db.commit()


def find_access_link(db, event_id, raw: str) -> EventAccessLink | None:
    """Resolve a raw token to a USABLE link for this event, or None.

    Scoped to the event id from the path, so a valid link for event A can never open
    event B. Revoked and expired both return None — the caller cannot tell which, and
    does not need to.
    """
    if not raw:
        return None
    link = db.scalar(
        select(EventAccessLink).where(
            EventAccessLink.token_hash == _hash_token(raw),
            EventAccessLink.event_id == event_id,
        )
    )
    if link is None or link.revoked_at is not None:
        return None
    if link.expires_at is not None and link.expires_at < datetime.now(timezone.utc):
        return None
    return link


def touch_access_link(db, link: EventAccessLink) -> None:
    """Record a redemption. Best-effort telemetry on the read path, so a failure here must
    never deny an attendee who legitimately holds the link."""
    link.uses = (link.uses or 0) + 1
    link.last_used_at = datetime.now(timezone.utc)
    db.commit()
