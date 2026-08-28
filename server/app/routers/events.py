"""Events API (/events/*). Management layer only — no streaming/chat/recording.

Isolation: every query is scoped to the caller's `user.org_id` from the JWT (never read
from the request) via security.org_scoped(), which super_admin bypasses — matching
_can_edit's and watch_event's own super_admin carve-outs elsewhere in this file. An
event_id from another org resolves to None -> 404 for everyone else.

Permissions:
  create / delete / assign host|moderator|speaker  -> org admin only
  edit (PATCH)                                     -> org admin, or a host who owns/hosts it
  read (list / get / view assignees)               -> any org member
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import commercial as commercial_crud
from ..crud import event as crud
from ..db import get_db
from ..email import (
    send_assignment_email, send_contributor_invite_email, send_event_created_email,
    send_registration_confirmation_email, send_viewer_invite_email,
)
from ..models import Event, LiveRecording, User
from ..schemas.admin import AdminUserOut, Page
from ..schemas.event import (
    AccessLinkCreate, AccessLinkIssued, AccessLinkOut,
    AssignmentUpdate, ContributorInvite, ContributorSessionOut, EventCreate, EventOut,
    EventUpdate, FeedbackOut,
    RegistrantOut, RegistrationCreate, RegistrationOut, ViewerInviteCreate, WatchOut,
)
from ..security import (
    create_registration_token, decode_registration_payload,
    get_current_user, get_current_user_optional, org_scoped, require_org_admin,
)
from ..services import broadcast as broadcast_svc
from ..services import livekit
from ..services import moderation as mod
from ..services import webhooks

router = APIRouter(prefix="/events", tags=["events"])


def _get_event_or_404(db, user: User, event_id) -> Event:
    # org_scoped(), not crud.get_event()'s hard org_id filter: a super_admin managing
    # events from outside their own org (see _can_edit and watch_event's is_org_member,
    # which already assume this) would otherwise 404 before the permission check below
    # ever runs — the same bug this fixed in commercial.py's version of this helper.
    stmt = select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
    ev = db.scalar(org_scoped(stmt, Event, user))
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
    return _get_event_or_404(db, user, event_id)


@router.get("/{event_id}/watch", response_model=WatchOut)
def watch_event(
    event_id: uuid.UUID,
    request: Request,
    response: Response,
    reg: str | None = Query(None, description="Registration access token from POST /register"),
    link: str | None = Query(None, description="Access-link token from POST /access-links"),
    monitor: bool = Query(
        False,
        description="Set by client/src/pages/speaker/Backstage.jsx's return-feed monitor "
                     "only — see services/livekit.py's secondary()/primary() docstring.",
    ),
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """The public viewer page's one read: thin event info, plus a subscribe-only LiveKit
    token while the event is live. Not org-scoped — a signed-out visitor watching a public
    event isn't a member of any org — but a private event requires either org membership
    (or super_admin) OR a valid `reg` token, i.e. the caller was specifically invited via
    POST /invite-viewers (self-serve POST /register refuses private events, so a token here
    always traces back to a host's deliberate invite, never a stranger inviting themselves),
    OR a valid `link` token from a host-generated access link (POST /access-links) — the
    revocable counterpart to a `reg` invite. A valid `link` also counts as a use on that row
    (crud.find_access_link).
    Independently, a registration_required event withholds the stream token until the caller
    is registered (org members always pass; everyone else needs a valid `reg` or `link` token).

    A private event's `reg` token is otherwise a plain 90-day bearer credential — anyone who
    gets the URL (forwarded, screenshotted, ...) could use it. The first browser to present a
    valid one claims the registration row to itself (crud.claim_registration) via an httpOnly
    cookie; every later request for a PRIVATE event must present the matching cookie, or the
    token is treated as not-invited. Not enforced for a merely registration_required PUBLIC
    event — that's capacity/data collection, not a confidentiality boundary, so sharing that
    link isn't the problem this exists to solve.

    A scheduled start_time/end_time also time-boxes the VIEWER link: before start_time or
    after end_time, no stream token goes out even if the host is live — this is deliberately
    independent of `status`, which the host still drives manually (going live early or
    running long past end_time never force-ends their broadcast; it only stops handing new
    viewers a token)."""
    ev = crud.get_event_unscoped(db, event_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    is_org_member = bool(user and (user.role == "super_admin" or user.org_id == ev.org_id))
    reg_payload = decode_registration_payload(reg, ev.id) if reg else None
    invited = reg_payload is not None
    link_row = crud.find_access_link(db, ev.id, link) if link else None
    link_admitted = link_row is not None

    claim_rejected = False
    if invited and ev.visibility == "private" and not is_org_member:
        reg_row = crud.get_registration_by_id(db, ev.id, uuid.UUID(reg_payload["reg"]))
        if reg_row is None:
            invited = False
        elif reg_row.claim_token_hash is None:
            raw_claim = crud.claim_registration(db, reg_row)
            # Cloud Run terminates TLS and forwards to this container over plain HTTP, so
            # request.url.scheme alone reads "http" even in production — X-Forwarded-Proto
            # is what actually says the browser connection was HTTPS. Deriving this from
            # settings.APP_URL instead is a trap: this project's local .env often points
            # APP_URL at the deployed prod URL even while running against 127.0.0.1, which
            # would mark the cookie Secure and make the browser silently refuse to ever send
            # it back over plain http — locking out the real invitee on their own next visit.
            is_https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
            response.set_cookie(
                f"zk_claim_{reg_row.id}", raw_claim, httponly=True, samesite="lax",
                secure=is_https, max_age=60 * 60 * 24 * 90,
            )
        elif not crud.claim_matches(reg_row, request.cookies.get(f"zk_claim_{reg_row.id}")):
            invited = False
            claim_rejected = True

    registered = is_org_member or invited or link_admitted

    if ev.visibility == "private" and not is_org_member and not invited and not link_admitted:
        detail = (
            "This invite has already been used on another device — ask the host to resend it"
            if claim_rejected else "This event is private"
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail)

    now = datetime.now(timezone.utc)
    not_started = bool(ev.start_time and now < ev.start_time)
    expired = bool(ev.end_time and now > ev.end_time)

    room = f"event_{ev.id}"
    token = url = None
    # "degraded" (persisted by services/broadcast.py's sampler/webhook-driven
    # mark_degraded/mark_recovered when the producer's media drops mid-broadcast) still gets
    # a token — a viewer should be able to sit connected and recover automatically once the
    # producer reconnects, same as before the drop, rather than being kicked out to a "not
    # live" state and having to refresh. media_status (below) is what tells the frontend the
    # difference between this and a genuinely healthy "live".
    can_stream = (
        ev.status in ("live", "degraded") and livekit.configured()
        and not not_started and not expired
        and (not ev.registration_required or registered)
    )
    if can_stream:
        # This identity has to be the SAME string the live moderation socket uses as this
        # visitor's presence identity (services/moderation.py resolve_ctx and friends) —
        # host actions like Promote to Speaker (participant.role), Mute, and Remove all
        # call into services/livekit.py with THAT identity to update/kick the matching
        # LiveKit room participant. A mismatched identity here meant those calls were
        # silently updating (or kicking) a LiveKit participant that didn't exist, so a
        # promoted viewer's own client still held stale (no-publish) permissions and got
        # "insufficient permissions" the moment it tried to publish its mic — the state
        # changed everywhere except the one place (LiveKit) that actually enforces it.
        # Mirrors routers/live.py's own resolve_ctx / resolve_ctx_from_registration /
        # resolve_ctx_from_access_link precedence (user, then reg, then link) exactly.
        if user:
            identity = str(user.id)
        elif invited and reg_payload:
            identity = f"guest-{reg_payload['reg']}"
        elif link_admitted and link_row:
            identity = f"guest-link-{link_row.id}"
        else:
            # No credential the live socket would accept either (see live.py's own
            # "Invalid or expired session" refusal) — this viewer can watch/listen but was
            # never going to hold a moderation-socket identity to promote in the first
            # place, so a disposable identity is correct here, not a bug.
            identity = f"viewer-{uuid.uuid4()}"
        # monitor=True is Backstage.jsx's own return-feed subscription, requested BY the
        # contributor themselves alongside their own publish connection (services/
        # contributor.py's my_publish_token, identity=ctx.identity=str(user.id) — the exact
        # same string `identity` resolves to here for a signed-in user). Without tagging,
        # both connections share one identity and evict each other (DUPLICATE_IDENTITY —
        # see services/livekit.py's secondary()/primary() docstring). Every other caller of
        # this endpoint (ordinary viewers, guest/link tokens) is untouched — only a
        # contributor watching their own return feed ever sends monitor=True, and only when
        # they're the signed-in user the identity would otherwise collide for.
        token_identity = livekit.secondary(identity, "monitor") if (monitor and user) else identity
        token = livekit.create_stream_token(token_identity, room, False)
        url = livekit.settings.LIVEKIT_URL

    # Replay: same access rule as the live token (registration_required gates it the same
    # way), but independent of not_started/expired — the whole point of a replay is that it
    # stays watchable after the scheduled window closes.
    #
    # Gated on the audience ReplayEntitlement's publish_state (BRD table 53: "never
    # auto-publish on event end") — until an operator explicitly publishes
    # (routers/commercial.py's publish endpoint, surfaced in pages/admin/Media.jsx),
    # recording_url stays None here even for a fully captured, already-validated file. No
    # row / not "published" both read as "no replay yet" — same as the pre-existing
    # not-recorded case, so this needed no frontend change.
    #
    # ALSO gated on watermark_status == "ready": publish_replay queues the burn but doesn't
    # wait for it (services/delivery.py's shared ticker — a real recording can run hours),
    # so "published" alone isn't enough to serve the file yet. Same "publish now, deliver
    # once ready" split the customer export's /deliveries/{token} page already uses.
    replay_entitlement = commercial_crud.get_replay_entitlement(db, ev.id, scope="audience")
    replay_published = (
        replay_entitlement is not None
        and replay_entitlement.publish_state == "published"
        and replay_entitlement.watermark_status == "ready"
        # Retention (doc Section 14/J): `expires_at` was stored and read by nothing, so a
        # replay whose retention window had lapsed stayed playable forever. Checked live rather
        # than relying only on the maintenance sweep — access must stop on the date it was sold
        # to stop, not on the next time a scheduler happens to run.
        and not commercial_crud.replay_access_expired(replay_entitlement)
    )

    recording_url = recording_duration = None
    if replay_published and not can_stream and (not ev.registration_required or registered):
        # The watermarked copy is the ONLY thing ever served here — never the original
        # recording.file_url — so every replay a viewer can reach already carries the
        # policy watermark (BRD LE-AC-12). object_exists still guards it: the burn could
        # have completed and then the object gone missing from storage since.
        if livekit.object_exists(replay_entitlement.watermarked_file_key):
            recording_url = livekit.signed_url(replay_entitlement.watermarked_file_key)
            source = db.get(LiveRecording, replay_entitlement.source_recording_id)
            if source and source.started_at and source.stopped_at:
                recording_duration = int(
                    (source.stopped_at - source.started_at).total_seconds() - source.paused_ms / 1000
                )

    org_name = ev.organization.name if ev.organization else None
    hosts = crud.list_assignees(db, ev.id, "host")

    # A real, persisted liveness signal — see WatchOut.media_status's own docstring. Reads
    # only Event.status (already up to date via the sampler/webhook, see
    # services/broadcast.py), never calls LiveKit directly here.
    if ev.status == "live":
        media_status = "live"
    elif ev.status == "degraded":
        media_status = "reconnecting"
    elif ev.status == "ended" or expired:
        media_status = "ended"
    elif not_started:
        media_status = "waiting_for_host"
    else:
        media_status = "unavailable"

    return WatchOut(
        id=ev.id, title=ev.title, description=ev.description, status=ev.status,
        visibility=ev.visibility, start_time=ev.start_time,
        organization_name=org_name, host_name=hosts[0].full_name if hosts else org_name,
        chat_enabled=ev.chat_enabled, qa_enabled=ev.qa_enabled, polls_enabled=ev.polls_enabled,
        reactions_enabled=not crud.is_memorial_category(ev.category),
        raise_hand_enabled=False if crud.is_memorial_category(ev.category) else ev.raise_hand_enabled,
        category=ev.category, end_time=ev.end_time,
        registration_required=ev.registration_required, registered=registered or not ev.registration_required,
        not_started=not_started, expired=expired,
        livekit_url=url, livekit_token=token, room=room if token else None,
        recording_url=recording_url, recording_duration_seconds=recording_duration,
        media_status=media_status,
    )


def _registration_console_url(event_id: uuid.UUID, token: str | None = None) -> str:
    base = settings.APP_URL.rstrip("/")
    url = f"{base}/events/{event_id}/watch"
    return f"{url}?reg={token}" if token else url


@router.post("/{event_id}/register", response_model=RegistrationOut)
def register_for_event(
    event_id: uuid.UUID,
    data: RegistrationCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Self-serve, anonymous registration — no auth, mirrors watch_event's public reach.
    Two callers use this: the registration_required video gate (RegistrationGate.jsx), and
    an anonymous viewer identifying themselves with name+email to use chat/Q&A/polls on an
    event that doesn't require registration at all (see routers/live.py's `reg` fallback for
    the live socket, which needs one of these rows to exist). Idempotent on email:
    resubmitting the same address never errors, it just re-issues a fresh access token."""
    ev = crud.get_event_unscoped(db, event_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if ev.visibility == "private":
        # Self-serve registration must never become a side-door into a private event — that
        # access is host-granted only, via invite_viewers below.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This event is private — ask the host for an invite")

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
        reg.email, reg.name, ev.title or "this event",
        _registration_console_url(ev.id, create_registration_token(reg)),
    )
    webhooks.enqueue(db, ev.org_id, "registration.created", {
        "event_id": str(ev.id), "registration_id": str(reg.id), "email": reg.email, "name": reg.name,
    })
    return RegistrationOut(id=reg.id, name=reg.name, email=reg.email, token=create_registration_token(reg))


@router.patch("/{event_id}", response_model=EventOut)
def update_event(event_id: uuid.UUID, data: EventUpdate,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, user, event_id)
    if not _can_edit(db, ev, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only edit events you host")

    fields = data.model_dump(exclude_unset=True)

    if fields.get("slug") and crud.event_slug_taken(db, user.org_id, fields["slug"], exclude_id=ev.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "An event with that slug already exists")

    if "category" in fields:
        fields["risk_tier"] = crud.elevated_risk_tier(fields["category"], ev.risk_tier)

    if fields.get("status"):
        title_after = fields.get("title", ev.title)
        readiness_ready, readiness_reasons = None, None
        target = fields["status"]
        # Every escalation into a production state clears the SAME gate — not just "armed".
        # Previously only "armed" was checked, so published/scheduled -> live (a legal
        # transition) skipped commercial readiness entirely (CF-3).
        if target in commercial_crud.PRODUCTION_EVENT_STATES and target != ev.status:
            evaluation = commercial_crud.golive_readiness(db, ev)
            commercial_crud.audit_golive_decision(db, ev, evaluation, actor=user, target_state=target)
            db.commit()
            readiness_ready, readiness_reasons = evaluation["ready"], evaluation["blocking_reasons"]
            if not readiness_ready:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Cannot move to '{target}' — " + "; ".join(evaluation["blocking_reasons"]),
                )
        err = crud.status_transition_error(
            ev.status, target, title_after,
            readiness_ready=readiness_ready, readiness_reasons=readiness_reasons,
        )
        if err:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, err)

    start = fields.get("start_time", ev.start_time)
    end = fields.get("end_time", ev.end_time)
    if start and end and end <= start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_time must be after start_time")

    return crud.update_event(db, ev, fields)


@router.post("/{event_id}/end", response_model=EventOut)
async def end_event(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Force-end a live event from the org dashboard — same real teardown delete_event already
    uses (stop recording, close the LiveKit room, publish broadcast.update so every connected
    viewer/host updates immediately), just without also deleting the event. This is the
    guaranteed way out of "live": if a real BroadcastSession exists, _end() drives the normal
    live -> ended transition; if the data is inconsistent (status says live but no session
    exists — e.g. debug/manual writes), the fallback below still forces status to ended rather
    than leaving the event stuck live with no recovery path."""
    ev = _get_event_or_404(db, user, event_id)
    if not _can_edit(db, ev, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only end events you host")
    if ev.status not in ("live", "degraded"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This event is not live")

    ctx = mod.Ctx(
        event_id=ev.id, org_id=ev.org_id, room=f"event_{ev.id}",
        user_id=user.id, name=user.full_name or user.email,
        identity=f"host-{user.id}", role=user.role,
        can_moderate=True, can_host=True,
    )
    await broadcast_svc._end(ctx, {}, emergency=True)
    db.refresh(ev)

    if ev.status in ("live", "degraded") and not crud.status_transition_error(ev.status, "ended", ev.title):
        ev = crud.update_event(db, ev, {"status": "ended"})
    return ev


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: uuid.UUID, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    ev = _get_event_or_404(db, admin, event_id)
    if ev.status in ("live", "paused"):
        # A soft delete alone would orphan the running broadcast_session at "live" forever —
        # nothing can ever reach it again to end it once the event is gone. Force-end first.
        ctx = mod.Ctx(
            event_id=ev.id, org_id=ev.org_id, room=f"event_{ev.id}",
            user_id=admin.id, name=admin.full_name or admin.email,
            identity=f"admin-{admin.id}", role=admin.role,
            can_moderate=True, can_host=True,
        )
        await broadcast_svc._end(ctx, {}, emergency=True)
        db.refresh(ev)
    crud.soft_delete_event(db, ev)  # soft delete: retained, excluded from listings


# ── Assignments (host / moderator / speaker) ──────────────────────────────────
# Reads: any member. Writes: org admin. Assignees must be live members of the same org.
# Newly-added assignees (not already holding the role) get a best-effort notification email.

def _list_role(db, user, event_id, role):
    _get_event_or_404(db, user, event_id)  # 404s if the event isn't visible to the caller
    return [_user_out(u) for u in crud.list_assignees(db, event_id, role)]


def _console_url(role: str, event_id: uuid.UUID) -> str:
    base = settings.APP_URL.rstrip("/")
    if role == "host":
        return f"{base}/host/dashboard?event={event_id}"
    if role == "moderator":
        return f"{base}/moderator/dashboard?event={event_id}"
    if role == "speaker":
        return f"{base}/speaker/backstage?event={event_id}"
    return base


def _set_role(db, admin, event_id, role, user_ids, background: BackgroundTasks):
    ev = _get_event_or_404(db, admin, event_id)
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
    return _list_role(db, user, event_id, "host")


@router.patch("/{event_id}/hosts", response_model=list[AdminUserOut])
def set_hosts(event_id: uuid.UUID, data: AssignmentUpdate, background: BackgroundTasks,
              admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    return _set_role(db, admin, event_id, "host", data.user_ids, background)


@router.get("/{event_id}/moderators", response_model=list[AdminUserOut])
def get_moderators(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _list_role(db, user, event_id, "moderator")


@router.patch("/{event_id}/moderators", response_model=list[AdminUserOut])
def set_moderators(event_id: uuid.UUID, data: AssignmentUpdate, background: BackgroundTasks,
                   admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    return _set_role(db, admin, event_id, "moderator", data.user_ids, background)


@router.get("/{event_id}/speakers", response_model=list[AdminUserOut])
def get_speakers(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _list_role(db, user, event_id, "speaker")


@router.patch("/{event_id}/speakers", response_model=list[AdminUserOut])
def set_speakers(event_id: uuid.UUID, data: AssignmentUpdate, background: BackgroundTasks,
                 admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    return _set_role(db, admin, event_id, "speaker", data.user_ids, background)


# ── Contributor (speaker) backstage invitations ─────────────────────────────────
# EventAssignment(role="speaker") above is only eligibility. Inviting is a separate,
# repeatable act — its own join window/expiry/consent notice, sent as a REST call (not a
# socket action) because it can happen well before any live socket exists, same reasoning
# as host/moderator assignment above.

def _assigned_speaker_or_404(db, admin, event_id, user_id) -> tuple[Event, User]:
    ev = _get_event_or_404(db, admin, event_id)
    if not crud.is_assigned(db, ev.id, user_id, "speaker"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This user is not assigned as a speaker for this event")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return ev, user


@router.post("/{event_id}/speakers/{user_id}/invite", response_model=ContributorSessionOut)
def invite_contributor(
    event_id: uuid.UUID, user_id: uuid.UUID, data: ContributorInvite, background: BackgroundTasks,
    admin: User = Depends(require_org_admin), db: Session = Depends(get_db),
):
    """(Re-)send a backstage invitation. Re-inviting resets the session's runtime state
    (consent/preflight/rehearsal) to fresh — see crud.upsert_contributor_invite."""
    ev, user = _assigned_speaker_or_404(db, admin, event_id, user_id)
    session = crud.upsert_contributor_invite(
        db, ev, user, admin.id,
        join_window_start=data.join_window_start, join_window_end=data.join_window_end,
        expires_at=data.expires_at, contribution_method=data.contribution_method,
        consent_notice=data.consent_notice, support_contact=data.support_contact,
    )
    org_name = admin.organization.name if admin.organization else None
    background.add_task(
        send_contributor_invite_email,
        user.email, user.full_name, ev.title or "this event", org_name,
        _console_url("speaker", ev.id), data.join_window_start, data.join_window_end,
        data.consent_notice,
    )
    return session


@router.get("/{event_id}/speakers/{user_id}/invite", response_model=ContributorSessionOut)
def get_contributor_invite(
    event_id: uuid.UUID, user_id: uuid.UUID,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _get_event_or_404(db, user, event_id)
    session = crud.get_contributor_session(db, event_id, user_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No invitation on record for this speaker")
    return session


@router.post("/{event_id}/speakers/{user_id}/revoke", response_model=ContributorSessionOut)
def revoke_contributor_invite(
    event_id: uuid.UUID, user_id: uuid.UUID,
    admin: User = Depends(require_org_admin), db: Session = Depends(get_db),
):
    """Revoke a speaker's backstage access. "Rotate" is deliberately not offered here —
    unlike EventAccessLink, a contributor invite has no bearer token to rotate under
    login-based auth, only an invitation window to close."""
    _get_event_or_404(db, admin, event_id)
    session = crud.get_contributor_session(db, event_id, user_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No invitation on record for this speaker")
    return crud.revoke_contributor_session(db, session)


@router.get("/{event_id}/registrations", response_model=list[RegistrantOut])
def get_registrations(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Who has registered for this event — self-serve or host-invited (see `invited_by`).
    Org-scoped like every other event read."""
    _get_event_or_404(db, user, event_id)
    return crud.list_registrations(db, event_id)


@router.get("/{event_id}/feedback", response_model=list[FeedbackOut])
def get_feedback(
    event_id: uuid.UUID,
    role: str | None = Query(None, pattern="^(host|viewer)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Feedback submitted through the end-of-event modal a VIEWER sees on leaving (see
    moderation._feedback_submit — the host console no longer collects its own). Same
    org-scoped read as every other event sub-resource — any org member can view it: both
    the host dashboard and the organizer's event page pass role=viewer to see what
    attendees said."""
    _get_event_or_404(db, user, event_id)
    return crud.list_feedback(db, event_id, role=role)


@router.post("/{event_id}/invite-viewers", response_model=list[RegistrationOut])
def invite_viewers(
    event_id: uuid.UUID,
    data: ViewerInviteCreate,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Host-initiated viewer invites — the access grant for a PRIVATE event (watch_event's
    visibility gate accepts any valid registration token regardless of org membership), and
    for a public/unlisted event just a courtesy email of the watch link. Same permission as
    editing the event: org admin, or the host who owns/is assigned it."""
    ev = _get_event_or_404(db, user, event_id)
    if not _can_edit(db, ev, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only invite viewers to events you host")

    seen = set()
    out = []
    for item in data.invites:
        email = item.email.lower()
        if email in seen:
            continue
        seen.add(email)
        reg = crud.get_registration(db, event_id, email)
        if reg is None:
            reg = crud.create_registration(db, event_id, item.name, email, invited_by=user.id)
            webhooks.enqueue(db, ev.org_id, "registration.created", {
                "event_id": str(ev.id), "registration_id": str(reg.id), "email": reg.email, "name": reg.name,
            })
        token = create_registration_token(reg)
        background.add_task(
            send_viewer_invite_email,
            reg.email, reg.name, ev.title or "this event",
            _registration_console_url(ev.id, token), user.full_name,
        )
        out.append(RegistrationOut(id=reg.id, name=reg.name, email=reg.email, token=token))
    return out


# ── Access links (revocable, shareable — link-based counterpart to invite-viewers) ────────

def _access_link_url(event_id: uuid.UUID, token: str) -> str:
    # THE BUG THIS FIXES: this used to read settings.CORS_ORIGINS (a comma-separated list
    # of allowed browser origins, meant for CORS — not a "public URL" setting) instead of
    # settings.APP_URL, which every other email link builder in this app uses
    # (_invite_url, _console_url, _registration_console_url, _base_url in email.py). Since
    # CORS_ORIGINS is commonly left at its dev default of localhost origins, access-link
    # invite emails sent from a real deployment pointed viewers at http://localhost:5173.
    base = settings.APP_URL.rstrip("/")
    return f"{base}/events/{event_id}/watch?link={token}"


@router.get("/{event_id}/access-links", response_model=list[AccessLinkOut])
def list_access_links(event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_event_or_404(db, user, event_id)
    return crud.list_access_links(db, event_id)


@router.post("/{event_id}/access-links", response_model=AccessLinkIssued, status_code=status.HTTP_201_CREATED)
def create_access_link(
    event_id: uuid.UUID, data: AccessLinkCreate,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    ev = _get_event_or_404(db, user, event_id)
    if not _can_edit(db, ev, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only manage access links for events you host")
    link, raw = crud.create_access_link(db, event_id, ev.org_id, user.id, data.label, data.expires_in_days)
    return AccessLinkIssued(**AccessLinkOut.model_validate(link).model_dump(), url=_access_link_url(event_id, raw))


@router.post("/{event_id}/access-links/{link_id}/rotate", response_model=AccessLinkIssued)
def rotate_access_link(
    event_id: uuid.UUID, link_id: uuid.UUID, data: AccessLinkCreate,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    ev = _get_event_or_404(db, user, event_id)
    if not _can_edit(db, ev, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only manage access links for events you host")
    link = crud.get_access_link(db, event_id, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access link not found")
    link, raw = crud.rotate_access_link(db, link, data.expires_in_days)
    return AccessLinkIssued(**AccessLinkOut.model_validate(link).model_dump(), url=_access_link_url(event_id, raw))


@router.post("/{event_id}/access-links/{link_id}/revoke", response_model=AccessLinkOut)
def revoke_access_link(
    event_id: uuid.UUID, link_id: uuid.UUID,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    ev = _get_event_or_404(db, user, event_id)
    if not _can_edit(db, ev, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only manage access links for events you host")
    link = crud.get_access_link(db, event_id, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access link not found")
    out = crud.revoke_access_link(db, link)
    webhooks.enqueue(db, ev.org_id, "access_link.revoked", {
        "event_id": str(ev.id), "access_link_id": str(link.id), "label": link.label,
    })
    return out


@router.delete("/{event_id}/access-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_access_link(
    event_id: uuid.UUID, link_id: uuid.UUID,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    ev = _get_event_or_404(db, user, event_id)
    if not _can_edit(db, ev, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You may only manage access links for events you host")
    link = crud.get_access_link(db, event_id, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access link not found")
    crud.delete_access_link(db, link)