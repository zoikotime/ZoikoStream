"""Events API (/events/*). Management layer only — no streaming/chat/recording.

Isolation: every query is scoped to the caller's `user.org_id` from the JWT; org_id is
never read from the request. An event_id from another org resolves to None -> 404.

Permissions:
  create / delete / assign host|moderator|speaker  -> org admin only
  edit (PATCH)                                     -> org admin, or a host who owns/hosts it
  read (list / get / view assignees)               -> any org member
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import event as crud
from ..db import get_db
from ..email import send_assignment_email, send_event_created_email, send_registration_confirmation_email
from ..models import Event, User
from ..schemas.admin import AdminUserOut, Page
from ..schemas.event import (
    AssignmentUpdate, EventCreate, EventOut, EventUpdate,
    RegistrantOut, RegistrationCreate, RegistrationOut, WatchOut,
)
from ..security import (
    create_registration_token, decode_registration_token,
    get_current_user, get_current_user_optional, require_org_admin,
)
from ..services import livekit

router = APIRouter(prefix="/events", tags=["events"])


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


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=Page)
def list_events(
    q: str | None = None,
    status_: str | None = Query(None, alias="status"),
    host: uuid.UUID | None = Query(None, description="filter by created_by (event owner)"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    sort_by: str = Query("created_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items, total = crud.list_events(db, user.org_id, q=q, status=status_, host_id=host,
                                    date_from=date_from, date_to=date_to,
                                    sort_by=sort_by, order=order, page=page, page_size=page_size)
    return Page(items=[EventOut.model_validate(e) for e in items], total=total, page=page, page_size=page_size)


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
    event = crud.create_event(db, admin.org_id, admin.id, data, slug)

    # After the response, same as signup's welcome mail — a Resend outage never delays or
    # breaks event creation (send_event_created_email is best-effort and logs its own errors).
    background.add_task(
        send_event_created_email,
        admin.email, admin.full_name, event.title, event.start_time, event.status,
    )

    return event


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_event_or_404(db, user.org_id, event_id)


@router.get("/{event_id}/watch", response_model=WatchOut)
def watch_event(
    event_id: uuid.UUID,
    reg: str | None = Query(None, description="Registration access token from POST /register"),
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """The public viewer page's one read: thin event info, plus a subscribe-only LiveKit
    token while the event is live. Not org-scoped — a signed-out visitor watching a public
    event isn't a member of any org — but a private event still requires the caller to
    belong to the org (or be super_admin). Independently, a registration_required event
    withholds the stream token until the caller is registered (org members always pass;
    everyone else needs a valid `reg` token from having registered).

    A scheduled start_time/end_time also time-boxes the VIEWER link: before start_time or
    after end_time, no stream token goes out even if the host is live — this is deliberately
    independent of `status`, which the host still drives manually (going live early or
    running long past end_time never force-ends their broadcast; it only stops handing new
    viewers a token)."""
    ev = crud.get_event_unscoped(db, event_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if ev.visibility == "private":
        if user is None or (user.role != "super_admin" and user.org_id != ev.org_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This event is private")

    is_org_member = bool(user and (user.role == "super_admin" or user.org_id == ev.org_id))
    registered = is_org_member or (bool(reg) and decode_registration_token(reg, ev.id) is not None)

    now = datetime.now(timezone.utc)
    not_started = bool(ev.start_time and now < ev.start_time)
    expired = bool(ev.end_time and now > ev.end_time)

    room = f"event_{ev.id}"
    token = url = None
    can_stream = (
        ev.status == "live" and livekit.configured()
        and not not_started and not expired
        and (not ev.registration_required or registered)
    )
    if can_stream:
        identity = f"viewer-{user.id}" if user else f"viewer-{uuid.uuid4()}"
        token = livekit.create_stream_token(identity, room, False)
        url = livekit.settings.LIVEKIT_URL

    # Replay: same access rule as the live token (registration_required gates it the same
    # way), but independent of not_started/expired — the whole point of a replay is that it
    # stays watchable after the scheduled window closes.
    recording_url = recording_duration = None
    if not can_stream and (not ev.registration_required or registered):
        rec = crud.get_latest_recording(db, ev.id)
        if rec:
            recording_url = livekit.signed_url(rec.file_url)
            if rec.started_at and rec.stopped_at:
                recording_duration = int(
                    (rec.stopped_at - rec.started_at).total_seconds() - rec.paused_ms / 1000
                )

    org_name = ev.organization.name if ev.organization else None
    hosts = crud.list_assignees(db, ev.id, "host")

    return WatchOut(
        id=ev.id, title=ev.title, description=ev.description, status=ev.status,
        visibility=ev.visibility, start_time=ev.start_time,
        organization_name=org_name, host_name=hosts[0].full_name if hosts else org_name,
        chat_enabled=ev.chat_enabled, qa_enabled=ev.qa_enabled, polls_enabled=ev.polls_enabled,
        registration_required=ev.registration_required, registered=registered or not ev.registration_required,
        not_started=not_started, expired=expired,
        livekit_url=url, livekit_token=token, room=room if token else None,
        recording_url=recording_url, recording_duration_seconds=recording_duration,
    )


def _registration_console_url(event_id: uuid.UUID) -> str:
    base = (settings.CORS_ORIGINS.split(",")[0].strip() or "https://zoikostream.com").rstrip("/")
    return f"{base}/events/{event_id}/watch"


@router.post("/{event_id}/register", response_model=RegistrationOut)
def register_for_event(
    event_id: uuid.UUID,
    data: RegistrationCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Self-serve, anonymous registration for a registration_required event — no auth,
    mirrors watch_event's public reach. Idempotent on email: resubmitting the same
    address never errors, it just re-issues a fresh access token."""
    ev = crud.get_event_unscoped(db, event_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if not ev.registration_required:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This event does not require registration")

    existing = crud.get_registration(db, event_id, data.email)
    if existing is not None:
        return RegistrationOut(
            id=existing.id, name=existing.name, email=existing.email,
            token=create_registration_token(existing),
        )

    if ev.registration_limit is not None and crud.count_registrations(db, event_id) >= ev.registration_limit:
        raise HTTPException(status.HTTP_409_CONFLICT, "This event is full")

    reg = crud.create_registration(db, event_id, data.name, data.email)
    background.add_task(
        send_registration_confirmation_email,
        reg.email, reg.name, ev.title or "this event", _registration_console_url(ev.id),
    )
    return RegistrationOut(id=reg.id, name=reg.name, email=reg.email, token=create_registration_token(reg))


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

    return crud.update_event(db, ev, fields)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: uuid.UUID, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin.org_id, event_id)
    crud.soft_delete_event(db, ev)  # soft delete: retained, excluded from listings


# ── Assignments (host / moderator / speaker) ──────────────────────────────────
# Reads: any member. Writes: org admin. Assignees must be live members of the same org.
# Newly-added assignees (not already holding the role) get a best-effort notification email.

def _list_role(db, org_id, event_id, role):
    _get_event_or_404(db, org_id, event_id)  # 404s if the event isn't in the caller's org
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
    valid = crud.valid_member_ids(db, admin.org_id, user_ids)
    invalid = [str(u) for u in user_ids if u not in valid]
    if invalid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Not members of this organization: {', '.join(invalid)}")
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


@router.get("/{event_id}/registrations", response_model=list[RegistrantOut])
def get_registrations(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Who has self-registered for this event. Org-scoped like every other event read."""
    _get_event_or_404(db, user.org_id, event_id)
    return crud.list_registrations(db, event_id)
