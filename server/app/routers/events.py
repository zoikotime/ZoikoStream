"""Events API (/events/*) — the Organization Admin's event-management surface.

Isolation: every MANAGEMENT query is scoped to the caller's `user.org_id` from the JWT;
org_id is never read from the request. An event_id from another org resolves to None -> 404.

Permissions (all built from the existing security.py ladder — no new gate types):
  create / delete / duplicate / bulk / team writes / viewer links -> org admin only
  edit (PATCH)                                                    -> org admin, or a host who owns/hosts it
  read (list / get / team / categories)                           -> any org member

The three exceptions are the attendee endpoints — `/{id}/viewer`, `/{id}/playback` and
`/{id}/access` — an attendee of a public event is by definition outside the organizing org,
so those resolve the event directly and authorize through services.viewer instead. They
return a viewer-safe projection, never the EventOut used above, which carries the
organizer's operational config.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import attendee as crud_attendee
from ..crud import event as crud
from ..db import get_db
from ..email import send_assignment_email, send_event_created_email
from ..models import ASSIGNMENT_ROLES, Event, User
from ..schemas.admin import AdminUserOut, Page
from ..schemas.event import (
    AccessLinkCreate,
    AccessLinkOut,
    AssignmentSingle,
    AssignmentUpdate,
    BulkAction,
    BulkResult,
    DuplicateIn,
    EventCreate,
    EventOut,
    EventUpdate,
    TeamMember,
    TeamOut,
)
from ..security import get_current_user, hash_password, require_org_admin
from ..services import livekit, viewer as viewer_svc

router = APIRouter(prefix="/events", tags=["events"])

# Lifecycle verbs -> target status. The console never PATCHes a raw status for these; the
# verb is the contract, so "unpublish" cannot be reinterpreted by a client as "go to draft
# from anywhere" (crud.status_transition_error still has the final say).
BULK_TARGET = {
    "publish": "published",
    "unpublish": "draft",
    "schedule": "scheduled",
    "cancel": "cancelled",
    "archive": "archived",
    "end": "ended",
}


def _get_event_or_404(db, org_id, event_id) -> Event:
    ev = crud.get_event(db, org_id, event_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return ev


def _can_edit(db, ev: Event, user: User) -> bool:
    if user.role in ("org_admin", "super_admin"):
        return True
    # A host may edit only events they own (created) or are assigned to host.
    if user.role == "host" and (ev.created_by == user.id or crud.is_assigned(db, ev.id, user.id, "host")):
        return True
    return False


def _user_out(u: User) -> AdminUserOut:
    out = AdminUserOut.model_validate(u)
    out.organization_name = u.organization.name if u.organization else None
    return out


def _apply_password(fields: dict) -> None:
    """Translate the write-only `access_password` into the stored hash, in place.

    Three intents, three wire states (see schemas.event.EventUpdate): absent leaves the
    passphrase alone, "" clears it, a value replaces it. Hashing happens here rather than in
    crud, matching how registration and invitation acceptance do it.
    """
    if "access_password" not in fields:
        return
    raw = fields.pop("access_password")
    fields["access_password_hash"] = hash_password(raw) if raw and raw.strip() else None


def _enrich(db, events: list[Event], org_name: str | None) -> list[EventOut]:
    """Attach the dashboard columns to a page of events.

    Two set-based queries for the whole page (crud.summarize / crud.actor_names) instead of
    a lookup per row — the table shows 25 rows and this is exactly where an N+1 would hide.
    """
    summary = crud.summarize(db, events)
    names = crud.actor_names(db, events)
    out = []
    for e in events:
        item = EventOut.model_validate(e)
        s = summary.get(e.id) or {}
        item.organization_name = org_name
        item.created_by_name = names.get(e.created_by)
        item.team_counts = s.get("team") or {}
        item.current_viewers = s.get("viewers")
        item.has_recording = bool(s.get("has_recording"))
        # A file existing is not the same as a replay being offered: the organizer's
        # replay_enabled switch is what makes it watchable.
        item.has_replay = bool(s.get("has_replay")) and e.replay_enabled
        item.access_link_count = int(s.get("links") or 0)
        item.raised_hands = s.get("hands")
        item.waiting = s.get("waiting")
        item.open_questions = int(s.get("open_questions") or 0)
        item.live_polls = int(s.get("live_polls") or 0)
        out.append(item)
    return out


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=Page)
def list_events(
    q: str | None = None,
    status_: str | None = Query(None, alias="status"),
    statuses: list[str] | None = Query(None, description="repeatable; multi-select filter"),
    visibility: str | None = Query(None),
    category: str | None = Query(None),
    host: uuid.UUID | None = Query(None, description="filter by created_by (event owner)"),
    assigned: str | None = Query(None, description="'me' — only events I am assigned to"),
    assigned_role: list[str] | None = Query(None, description="repeatable; narrows `assigned`"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    sort_by: str = Query("created_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Paginated, filtered, sorted — SERVER-side. The console passes its filter state
    through rather than fetching everything and narrowing in the browser (which silently
    truncated any org with more than 100 events).

    `assigned=me` is what the host and moderator dashboards use: an org member can READ every
    event in their org, but "my events" means the ones they were actually assigned to. Only
    "me" is accepted — an arbitrary user id here would let any member enumerate somebody
    else's workload, and there is no requirement for that.
    """
    if assigned is not None and assigned != "me":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "assigned only accepts 'me'")
    if assigned_role:
        unknown = [r for r in assigned_role if r not in ASSIGNMENT_ROLES]
        if unknown:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"Unknown role(s): {', '.join(unknown)}")
    items, total = crud.list_events(db, user.org_id, q=q, status=status_, statuses=statuses,
                                    visibility=visibility, category=category, host_id=host,
                                    assigned_to=user.id if assigned == "me" else None,
                                    assigned_roles=assigned_role,
                                    date_from=date_from, date_to=date_to,
                                    sort_by=sort_by, order=order, page=page, page_size=page_size)
    org_name = user.organization.name if user.organization else None
    return Page(items=_enrich(db, items, org_name), total=total, page=page, page_size=page_size)


@router.get("/activity")
def list_recent_activity(
    assigned: str | None = Query(None, description="'me' — only events I am assigned to"),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Activity across SEVERAL events, for the host dashboard's Recent Activity panel.

    The in-console feed reads one event; this answers "what happened on my events" without the
    dashboard opening a socket per event. Declared before /{event_id} so the literal path wins.
    """
    if assigned is not None and assigned != "me":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "assigned only accepts 'me'")
    return crud.recent_activity(db, user.org_id,
                                assigned_to=user.id if assigned == "me" else None, limit=limit)


@router.get("/categories", response_model=list[str])
def list_categories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Categories this org actually uses — declared BEFORE /{event_id} so the literal path
    wins over the UUID parameter."""
    return crud.distinct_categories(db, user.org_id)


@router.post("", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(data: EventCreate, background: BackgroundTasks, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    if data.start_time and data.end_time and data.end_time <= data.start_time:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_time must be after start_time")
    err = crud.status_transition_error("draft", data.status, data.title)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    if data.slug:
        if crud.event_slug_taken(db, admin.org_id, data.slug):
            raise HTTPException(status.HTTP_409_CONFLICT, "An event with that slug already exists")
        slug = data.slug
    elif data.title:
        slug = crud.unique_event_slug(db, admin.org_id, data.title)
    else:
        slug = None
    password_hash = hash_password(data.access_password) if data.access_password else None
    event = crud.create_event(db, admin.org_id, admin.id, data, slug, password_hash=password_hash)

    # After the response, same as signup's welcome mail — a Resend outage never delays or
    # breaks event creation (send_event_created_email is best-effort and logs its own errors).
    background.add_task(
        send_event_created_email,
        admin.email, admin.full_name, event.title, event.start_time, event.status,
    )

    return event


@router.post("/bulk", response_model=BulkResult)
def bulk_action(data: BulkAction, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Apply one lifecycle verb to up to 100 events.

    Per-id results, never all-or-nothing: bulk-publishing twenty events where three have no
    title must tell the operator WHICH three and why, not fail the whole request or silently
    skip them. Every id is resolved through the org-scoped lookup first, so an id from
    another tenant lands in `failed` as "not found" — identical to a deleted one.
    """
    result = BulkResult()
    for event_id in dict.fromkeys(data.ids):          # dedupe, preserve order
        ev = crud.get_event(db, admin.org_id, event_id)
        if ev is None:
            result.failed.append({"id": str(event_id), "reason": "Event not found"})
            continue
        if data.action == "delete":
            crud.soft_delete_event(db, ev)
            result.succeeded.append(event_id)
            continue
        target = BULK_TARGET[data.action]
        err = crud.status_transition_error(ev.status, target, ev.title)
        if err:
            result.failed.append({"id": str(event_id), "reason": err})
            continue
        crud.update_event(db, ev, {"status": target})
        result.succeeded.append(event_id)
    return result


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user.org_id, event_id)
    org_name = user.organization.name if user.organization else None
    return _enrich(db, [ev], org_name)[0]


@router.patch("/{event_id}", response_model=EventOut)
def update_event(event_id: uuid.UUID, data: EventUpdate,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user.org_id, event_id)
    if not _can_edit(db, ev, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only edit events you host")

    fields = data.model_dump(exclude_unset=True)

    if fields.get("slug") and crud.event_slug_taken(db, user.org_id, fields["slug"], exclude_id=ev.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "An event with that slug already exists")

    if fields.get("status"):
        title_after = fields.get("title", ev.title)
        err = crud.status_transition_error(ev.status, fields["status"], title_after)
        if err:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, err)

    start = fields.get("start_time", ev.start_time)
    end = fields.get("end_time", ev.end_time)
    if start and end and end <= start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_time must be after start_time")

    _apply_password(fields)
    return crud.update_event(db, ev, fields)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: uuid.UUID, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin.org_id, event_id)
    crud.soft_delete_event(db, ev)  # soft delete: retained, excluded from listings


@router.post("/{event_id}/duplicate", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def duplicate_event(event_id: uuid.UUID, data: DuplicateIn,
                    admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Clone an event's configuration as a fresh draft in the SAME org.

    The copy is always a draft with no schedule, no passphrase and no viewer links: those
    three are bound to one occurrence of an event, and inheriting them silently is how a
    duplicate leaks an audience or goes live against a stale date.
    """
    source = _get_event_or_404(db, admin.org_id, event_id)
    title = (data.title or "").strip() or f"{source.title or 'Untitled event'} (copy)"
    slug = crud.unique_event_slug(db, admin.org_id, title)
    clone = crud.duplicate_event(db, source, admin.id, title[:200], slug, copy_team=data.copy_team)
    return clone


# ── Viewer surface (attendee landing + playback) ──────────────────────────────
# These are the ONLY endpoints here that are not org-scoped: an attendee of a public event
# is legitimately outside the organizing org. All of them resolve the event themselves
# rather than through _get_event_or_404, and all run services.viewer.access_for.

def _link_ok(db: Session, event_id: uuid.UUID, token: str | None) -> bool:
    """Resolve a viewer-link token, recording the redemption when it is valid. Scoped to
    THIS event id, so a link for one event can never open another."""
    if not token:
        return False
    link = crud.find_access_link(db, event_id, token)
    if link is None:
        return False
    crud.touch_access_link(db, link)
    return True


def _viewable_or_404(db: Session, event_id: uuid.UUID, user: User,
                     token: str | None = None) -> tuple[Event, bool]:
    """Resolve an event for an attendee. Missing, deleted, not-yet-public and
    not-permitted all collapse to the SAME 404 — otherwise the status code tells an
    outsider whether a private event exists."""
    ev = db.get(Event, event_id)
    if ev is None or ev.deleted_at is not None or ev.status not in viewer_svc.VIEWABLE_STATUSES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    link_ok = _link_ok(db, event_id, token)
    allowed, _, _ = viewer_svc.access_for(ev, user, link_ok=link_ok)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return ev, link_ok


def _password_exempt(ev: Event, user: User) -> bool:
    """The organizing org and platform admins never face their own audience passphrase."""
    return user.role == "super_admin" or (bool(user.org_id) and user.org_id == ev.org_id)


@router.get("/{event_id}/viewer")
def viewer_landing(event_id: uuid.UUID, token: str | None = Query(None, description="viewer access link token"),
                   password: str | None = Query(None), user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Whole-page payload for the attendee landing page (/events/:id/watch).

    Viewer-safe by construction — see services/viewer.py. Live figures (viewer count,
    stream health) are NOT here: they arrive on the existing socket, which already pushes
    them, so this stays a single cacheable read that doesn't need polling.

    A wrong or missing passphrase does NOT 404 the page — it renders with
    `access.password_satisfied: false` so the attendee gets a prompt instead of a dead end.
    The media is what the passphrase actually guards (see /playback below).
    """
    ev, link_ok = _viewable_or_404(db, event_id, user, token)
    return viewer_svc.landing(db, ev, user, link_ok=link_ok, password=password)


@router.get("/{event_id}/playback")
def viewer_playback(event_id: uuid.UUID, token: str | None = Query(None),
                    password: str | None = Query(None), user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Subscribe-only LiveKit credentials for this event's room.

    Separate from the landing read on purpose: a token is a credential with a lifetime, so
    it is fetched when playback actually starts and re-fetched on recovery, instead of
    riding along on every page load and sitting in a cache.

    can_publish is hard-false. An attendee never gets a publish grant from this endpoint —
    that path is the host console's (services/broadcast.py), and it is gated on ctx.can_host.
    """
    ev, _ = _viewable_or_404(db, event_id, user, token)
    # Registration is enforced on the MEDIA, not on the page — an attendee who hasn't signed up
    # must still reach the landing page, because that is where the Register button is. 409 rather
    # than 403 so the client re-checks its state instead of prompting for a passphrase.
    exempt = _password_exempt(ev, user)
    blocked = viewer_svc.playback_blocked_reason(
        ev, registered=crud_attendee.is_registered(db, ev.id, user.id), exempt=exempt)
    if blocked:
        raise HTTPException(status.HTTP_409_CONFLICT, blocked)
    # The passphrase gate applies to the MEDIA, which is why it is checked here and not in
    # _viewable_or_404: 403 tells the client to prompt, where a 404 would say "no such event".
    pwd_error = viewer_svc.password_gate(ev, password, exempt=_password_exempt(ev, user))
    if pwd_error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, pwd_error)

    # Opening the media IS the join, so the watch history starts here rather than on a separate
    # client call that could be skipped.
    crud_attendee.record_join(db, ev, user)
    db.commit()

    room = viewer_svc.room_name(ev.id)
    return {
        "room": room,
        "livekit_url": livekit.settings.LIVEKIT_URL,
        "token": livekit.create_stream_token(
            identity=str(user.id), room_name=room, can_publish=False
        ),
    }


# ── Team (host / moderator / speaker / producer / cohost / panelist) ──────────
# Reads: any member. Writes: org admin. Assignees must be live members of the same org.
# One table and one code path serve all six roles (models.event.EventAssignment).

def _team_out(db, event_id) -> TeamOut:
    grouped = crud.team(db, event_id)
    return TeamOut(**{
        role: [TeamMember.model_validate(u) for u in grouped.get(role, [])]
        for role in ASSIGNMENT_ROLES
    })


def _valid_role(role: str) -> str:
    if role not in ASSIGNMENT_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Unknown team role '{role}'. Expected one of: {', '.join(ASSIGNMENT_ROLES)}")
    return role


def _require_org_members(db, org_id, user_ids) -> None:
    """Permission validation for assignment: an assignee must be a live member of THIS org.
    Naming the offending ids beats a bare 400 — the console shows which pick was rejected."""
    valid = crud.valid_member_ids(db, org_id, user_ids)
    invalid = [str(u) for u in user_ids if u not in valid]
    if invalid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Not members of this organization: {', '.join(invalid)}")


@router.get("/{event_id}/team", response_model=TeamOut)
def get_team(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every role in one request. The detail page used six round trips for this panel."""
    _get_event_or_404(db, user.org_id, event_id)   # 404s if the event isn't in the caller's org
    return _team_out(db, event_id)


@router.get("/{event_id}/team/{role}", response_model=list[TeamMember])
def get_team_role(event_id: uuid.UUID, role: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    _get_event_or_404(db, user.org_id, event_id)
    return [TeamMember.model_validate(u) for u in crud.list_assignees(db, event_id, _valid_role(role))]


@router.patch("/{event_id}/team/{role}", response_model=TeamOut)
def set_team_role(event_id: uuid.UUID, role: str, data: AssignmentUpdate,
                  admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Replace the whole set for one role. This is the "replace" operation — the picker
    submits every checked member, so unchecking is how you remove someone."""
    ev = _get_event_or_404(db, admin.org_id, event_id)
    _valid_role(role)
    _require_org_members(db, admin.org_id, data.user_ids)
    crud.set_assignees(db, ev, role, data.user_ids)
    return _team_out(db, event_id)


@router.post("/{event_id}/team/{role}", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def add_team_member(event_id: uuid.UUID, role: str, data: AssignmentSingle,
                    admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Add ONE assignee. Idempotent — re-adding an existing member is a no-op, not a 409,
    because a double-submit is an accident and not something an operator should have to
    reason about (duplicate prevention also lives on the UNIQUE constraint)."""
    ev = _get_event_or_404(db, admin.org_id, event_id)
    _valid_role(role)
    _require_org_members(db, admin.org_id, [data.user_id])
    crud.add_assignee(db, ev, role, data.user_id)
    return _team_out(db, event_id)


@router.delete("/{event_id}/team/{role}/{user_id}", response_model=TeamOut)
def remove_team_member(event_id: uuid.UUID, role: str, user_id: uuid.UUID,
                       admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Remove ONE assignee. A missing row is a 404 so the console can tell "already gone"
    from "removed just now" instead of reporting a success that did nothing."""
    _get_event_or_404(db, admin.org_id, event_id)
    _valid_role(role)
    if not crud.remove_assignee(db, event_id, role, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That member is not assigned to this role")
    return _team_out(db, event_id)


# ── Legacy per-role routes ────────────────────────────────────────────────────
# Kept because existing callers use them; they delegate to the same helpers as the generic
# team routes above, so there is one implementation and no second permission path.
# Newly-added assignees (not already holding the role) get a best-effort notification email.

def _list_role(db, org_id, event_id, role):
    _get_event_or_404(db, org_id, event_id)
    return [_user_out(u) for u in crud.list_assignees(db, event_id, role)]


def _console_url(role: str, event_id: uuid.UUID) -> str:
    base = (settings.CORS_ORIGINS.split(",")[0].strip() or "https://zoikostream.com").rstrip("/")
    if role == "host":
        return f"{base}/host/dashboard?event={event_id}"
    if role == "moderator":
        return f"{base}/moderator/dashboard?event={event_id}"
    return base  # speakers have no dedicated console route yet


def _set_role(db, admin, event_id, role, user_ids, background: BackgroundTasks):
    ev = _get_event_or_404(db, admin.org_id, event_id)
    _require_org_members(db, admin.org_id, user_ids)
    previous_ids = {u.id for u in crud.list_assignees(db, event_id, role)}
    crud.set_assignees(db, ev, role, user_ids)
    assignees = crud.list_assignees(db, event_id, role)

    org_name = admin.organization.name if admin.organization else None
    event_url = _console_url(role, ev.id)
    for u in assignees:
        if u.id in previous_ids:
            continue  # already held this role — don't re-notify on every save
        background.add_task(send_assignment_email, u.email, u.full_name, ev.title, role, org_name, event_url)

    return [_user_out(u) for u in assignees]


@router.get("/{event_id}/hosts", response_model=list[AdminUserOut])
def get_hosts(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _list_role(db, user.org_id, event_id, "host")


@router.patch("/{event_id}/hosts", response_model=list[AdminUserOut])
def set_hosts(event_id: uuid.UUID, data: AssignmentUpdate, background: BackgroundTasks,
              admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    return _set_role(db, admin, event_id, "host", data.user_ids, background)


@router.get("/{event_id}/moderators", response_model=list[AdminUserOut])
def get_moderators(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _list_role(db, user.org_id, event_id, "moderator")


@router.patch("/{event_id}/moderators", response_model=list[AdminUserOut])
def set_moderators(event_id: uuid.UUID, data: AssignmentUpdate, background: BackgroundTasks,
                   admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    return _set_role(db, admin, event_id, "moderator", data.user_ids, background)


@router.get("/{event_id}/speakers", response_model=list[AdminUserOut])
def get_speakers(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _list_role(db, user.org_id, event_id, "speaker")


@router.patch("/{event_id}/speakers", response_model=list[AdminUserOut])
def set_speakers(event_id: uuid.UUID, data: AssignmentUpdate, background: BackgroundTasks,
                 admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    return _set_role(db, admin, event_id, "speaker", data.user_ids, background)


# ── Viewer access links ───────────────────────────────────────────────────────
# Org admin only. Same token discipline as an org invitation: the raw secret is returned
# ONCE on create/rotate and only its sha256 hash is stored, so a leaked database row cannot
# be replayed as a link.

def _link_url(event_id, token: str) -> str:
    """Points at the real watch page. The org console previously hand-built /e/{slug},
    which resolves against no endpoint — this is generated server-side so there is one
    definition of "the viewer link"."""
    base = (settings.CORS_ORIGINS.split(",")[0].strip() or "https://zoikostream.com").rstrip("/")
    return f"{base}/events/{event_id}/watch?token={token}"


def _link_out(link, raw: str | None = None) -> AccessLinkOut:
    out = AccessLinkOut.model_validate(link)
    if raw:
        out.token = raw
        out.url = _link_url(link.event_id, raw)
    return out


@router.get("/{event_id}/access-links", response_model=list[AccessLinkOut])
def get_access_links(event_id: uuid.UUID, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Existing links WITHOUT their tokens — the secrets are unrecoverable by design.
    Regenerate a link the holder lost; there is nothing to look up."""
    _get_event_or_404(db, admin.org_id, event_id)
    return [_link_out(link) for link in crud.list_access_links(db, event_id)]


@router.post("/{event_id}/access-links", response_model=AccessLinkOut, status_code=status.HTTP_201_CREATED)
def create_access_link(event_id: uuid.UUID, data: AccessLinkCreate,
                       admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin.org_id, event_id)
    link, raw = crud.create_access_link(db, ev, data.label, admin.id, data.expires_in_days)
    return _link_out(link, raw)


def _link_or_404(db, org_id, event_id, link_id):
    _get_event_or_404(db, org_id, event_id)   # org isolation first
    link = crud.get_access_link(db, event_id, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access link not found")
    return link


@router.post("/{event_id}/access-links/{link_id}/rotate", response_model=AccessLinkOut)
def rotate_access_link(event_id: uuid.UUID, link_id: uuid.UUID, data: AccessLinkCreate,
                       admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Regenerate: the old token stops working immediately and the label + usage history
    stay on the same row."""
    link = _link_or_404(db, admin.org_id, event_id, link_id)
    link, raw = crud.rotate_access_link(db, link, data.expires_in_days)
    return _link_out(link, raw)


@router.post("/{event_id}/access-links/{link_id}/revoke", response_model=AccessLinkOut)
def revoke_access_link(event_id: uuid.UUID, link_id: uuid.UUID,
                       admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Soft revoke — the row survives so a withdrawn link stays auditable."""
    return _link_out(crud.revoke_access_link(db, _link_or_404(db, admin.org_id, event_id, link_id)))


@router.delete("/{event_id}/access-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_access_link(event_id: uuid.UUID, link_id: uuid.UUID,
                       admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    crud.delete_access_link(db, _link_or_404(db, admin.org_id, event_id, link_id))
