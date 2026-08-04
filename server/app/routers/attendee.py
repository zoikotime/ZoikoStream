"""Attendee & Viewer API (/attendee/*).

Small on purpose. Everything an attendee does DURING a session already travels on the live socket
(chat.send, qa.ask, qa.vote, poll.vote, participant.hand, reaction.send) and the attendee's live
state already arrives in the socket snapshot. What is left for REST is the things that outlive a
session: registration, personalisation, watch history and downloading a file.

Isolation: an attendee is legitimately OUTSIDE the organizing org — that is what a public event
means — so these routes do NOT scope by the caller's org. They resolve the event through the SAME
viewer rules the watch page uses (`_viewable_or_404` in routers/events.py, reused here), and then
scope every row by `user_id`. A private event the caller cannot see is a 404, not a 403.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from ..crud import attendee as crud
from ..crud.admin import create_audit_log
from ..db import get_db
from ..models import User
from ..security import get_current_user
from ..services import attendee as attendee_svc
from .events import _viewable_or_404

router = APIRouter(prefix="/attendee", tags=["attendee"])

# Reminders are bounded so a client cannot store an alarm a century out (or in the past, which
# would fire immediately and forever).
MAX_REMINDER_DAYS = 365


def _event(db, user, event_id, token=None):
    """Resolve an event through the attendee's OWN visibility rules. Reused from the events router
    so there is one definition of "an attendee may see this event" rather than two that drift."""
    ev, _link_ok = _viewable_or_404(db, event_id, user, token)
    return ev


def _audit(db, user, action, ev, **meta):
    """Audit into the EVENT's org, not the attendee's. An attendee of a public event belongs to
    another tenant, and the organizer is the one who has to be able to read their own event's
    trail."""
    create_audit_log(db, actor=user, action=action, target_type="event", target_id=ev.id,
                     org_id=ev.org_id, meta={"event_id": str(ev.id), **meta})


# ── the dashboard ─────────────────────────────────────────────────────────────

@router.get("/events")
def my_events(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every event this attendee can see, each carrying their own relationship with it.

    ONE query for the whole dashboard (crud.attendee.my_events): registered, bookmarked, watched
    and discoverable events all come back together with the flags that let the client bucket them.
    Six status-filtered requests would be six round trips for the same rows.
    """
    return crud.my_events(db, user)


# ── registration ──────────────────────────────────────────────────────────────

@router.post("/events/{event_id}/register")
def register(event_id: uuid.UUID, token: str | None = Query(None),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sign up. Idempotent, and capacity-checked against the organizer's registration_limit."""
    ev = _event(db, user, event_id, token)
    row, error = crud.register(db, ev, user)
    if error:
        raise HTTPException(status.HTTP_409_CONFLICT, error)
    _audit(db, user, "attendee.register", ev)
    db.commit()
    return {"status": row.status, "registered": True,
            "registered_at": row.registered_at.isoformat() if row.registered_at else None}


@router.delete("/events/{event_id}/register")
def cancel_registration(event_id: uuid.UUID, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Cancel. The row survives, so watch history and bookmarks are not collateral damage."""
    ev = _event(db, user, event_id)
    row = crud.cancel(db, ev, user)
    _audit(db, user, "attendee.cancel_registration", ev)
    db.commit()
    return {"status": row.status, "registered": False}


# ── personalisation ───────────────────────────────────────────────────────────

@router.post("/events/{event_id}/bookmark")
def bookmark(event_id: uuid.UUID, on: bool = Body(True, embed=True),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Bookmarking is NOT registering. It creates the row with status `cancelled`, so saving an
    event for later cannot smuggle anybody past registration enforcement."""
    ev = _event(db, user, event_id)
    row = crud.set_bookmark(db, ev, user, on)
    _audit(db, user, "attendee.bookmark", ev, on=bool(on))
    db.commit()
    return {"bookmarked": row.bookmarked}


@router.post("/events/{event_id}/reminder")
def reminder(event_id: uuid.UUID, minutes_before: int | None = Body(None, embed=True),
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Set or clear a reminder, expressed as minutes before the start.

    Stored as an ABSOLUTE time: a rescheduled event should not silently move somebody's alarm to a
    slot they never agreed to. `null` clears it.

    ponytail: this records the intent. There is no scheduled-delivery worker in this platform (the
    only outbound channel is Resend, invoked inline), so the reminder is what the console counts
    down to — it does not yet send an email. Stated rather than implied: a reminder that silently
    never arrives is worse than no reminder.
    """
    ev = _event(db, user, event_id)
    at = None
    if minutes_before is not None:
        if not ev.start_time:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "This event has no start time to remind you about.")
        offset = max(0, min(int(minutes_before), MAX_REMINDER_DAYS * 24 * 60))
        at = ev.start_time - timedelta(minutes=offset)
        if at < datetime.now(timezone.utc) - timedelta(days=MAX_REMINDER_DAYS):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That reminder is in the past.")
    row = crud.set_reminder(db, ev, user, at)
    db.commit()
    return {"reminder_at": row.reminder_at.isoformat() if row.reminder_at else None,
            "delivery": "in_app_only"}


@router.post("/events/{event_id}/questions/{question_id}/bookmark")
def bookmark_question(event_id: uuid.UUID, question_id: uuid.UUID,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Save a question to come back to. Toggles."""
    ev = _event(db, user, event_id)
    ids, saved = crud.toggle_question_bookmark(db, ev, user, question_id)
    db.commit()
    return {"question_bookmarks": ids, "saved": saved}


@router.post("/events/{event_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(event_id: uuid.UUID, seconds: int = Body(30, embed=True),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Accumulate watch time. The client reports the interval it just watched; the server clamps it
    (crud.attendee.MAX_HEARTBEAT_SECONDS), so a tab suspended for an hour cannot bank an hour."""
    ev = _event(db, user, event_id)
    crud.record_watch(db, ev, user, seconds)
    db.commit()


# ── preferences ───────────────────────────────────────────────────────────────

@router.get("/preferences")
def get_preferences(user: User = Depends(get_current_user)):
    """The caller's own settings, merged over the defaults so the client never has to know them."""
    return attendee_svc.preferences_out(user.preferences)


@router.patch("/preferences")
def update_preferences(patch: dict = Body(...), user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Merge a patch into the caller's settings.

    Whitelisted per key (services.attendee.clean_preferences), so an unknown key is dropped rather
    than stored — this blob is echoed straight back and rendered.
    """
    accepted = attendee_svc.clean_preferences(patch)
    if not accepted:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "No recognised preferences in that request")
    merged = {**(user.preferences or {}), **accepted}
    if "notify" in accepted:
        merged["notify"] = {**((user.preferences or {}).get("notify") or {}), **accepted["notify"]}
    user.preferences = merged
    db.commit()
    return attendee_svc.preferences_out(user.preferences)


# ── shared resources ──────────────────────────────────────────────────────────

@router.get("/events/{event_id}/resources")
def resources(event_id: uuid.UUID, token: str | None = Query(None),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Files the organizers shared with the audience: approved AND explicitly shared."""
    ev = _event(db, user, event_id, token)
    return [crud.resource_out(a) for a in crud.shared_assets(db, ev.id)]


@router.get("/events/{event_id}/resources/{asset_id}/file")
def download_resource(event_id: uuid.UUID, asset_id: uuid.UUID, token: str | None = Query(None),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download one shared file.

    Deliberately a SEPARATE route from the speaker one (routers/speaker.py), even though both serve
    the same bytes. That route's rule is "you are on this event's team"; this one's is "this file
    was shared with the audience". One route with a branch would put an unshared draft one boolean
    slip away from ten thousand people.

    `attachment`, not `inline`: an attendee is downloading a file, not presenting it, and forcing a
    download is one less way for user-uploaded bytes to be rendered on our own origin.
    """
    ev = _event(db, user, event_id, token)
    asset = crud.shared_asset(db, ev.id, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That file isn't available")
    _audit(db, user, "attendee.download", ev, filename=asset.filename, asset_id=str(asset.id))
    db.commit()

    safe = (asset.filename or "resource").replace('"', "").replace("\r", "").replace("\n", "")
    return Response(
        content=asset.data,
        media_type=asset.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            "Cache-Control": "private, max-age=300",
        },
    )
