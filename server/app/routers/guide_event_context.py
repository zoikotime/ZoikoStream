"""Issuer of the public Guide's event context token (Guide API §18).

POST /api/guide/event-context/{event_id}; the viewer's credential travels in the JSON body.

The viewer page asks this endpoint for a short-lived signed token describing the event being
watched; the page hands it to Guide, which verifies it and answers event questions from it
(start time, live status, replay). Guide itself never reads platform data.

Access mirrors GET /events/{id}/watch, read-only:
  * draft events exist only for their own organization (404 otherwise);
  * public and unlisted events: anyone who has the link — the same facts the event page shows;
  * private events: organization members, an invitee whose registration token AND claim
    cookie both match (the watch page has already claimed it), or the holder of a valid
    access-link PASS. A raw access-link token is not accepted here: verifying one counts a
    use, and asking Guide a question must never count as opening the link.
A refusal yields 403 and the viewer simply gets generic help — never a hint about the event.

Claims are exactly the spec's public-safe set (Conversation Flow §16; API §18; PR-023).
Attendee data, links, credentials, organizer contact details and internal state are never
included. A claim is sent only when the platform actually holds the fact; unknown facts are
OMITTED, never defaulted, so Guide falls back to general wording (CF-AT-21..24).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..crud import commercial as commercial_crud
from ..crud import event as crud
from ..db import get_db
from ..guide import event_context
from ..guide.settings import get_settings as guide_settings
from ..models.user import User
from ..security import decode_registration_payload, get_current_user_optional
from ..services import invitation_links

router = APIRouter(prefix="/guide/event-context", tags=["guide"])

TOKEN_TTL_SECONDS = 3600
_STATUS = {"live": "live", "degraded": "live", "ended": "ended", "cancelled": "cancelled"}


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _public_status(viewer_status: str, start: datetime | None) -> str:
    if viewer_status in _STATUS:
        return _STATUS[viewer_status]
    if start is not None:
        start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        if timedelta(0) <= start - datetime.now(timezone.utc) <= timedelta(minutes=15):
            return "starting_soon"
    return "scheduled"


def _may_view(db: Session, request: Request, ev, is_member: bool, reg: str | None,
              link: str | None) -> bool:
    if is_member or ev.visibility in ("public", "unlisted"):
        return True
    payload = decode_registration_payload(reg, ev.id) if reg else None
    if payload:
        row = crud.get_registration_by_id(db, ev.id, uuid.UUID(payload["reg"]))
        if row is not None and row.claim_token_hash is not None and crud.claim_matches(
                row, request.cookies.get(f"zk_claim_{row.id}")):
            return True
    if link and invitation_links.is_link_pass(link):
        parsed = invitation_links.parse_link_pass(link)
        row = crud.get_access_link(db, ev.id, parsed[0]) if parsed else None
        if (row is not None and row.revoked_at is None
                and (row.expires_at is None or row.expires_at > datetime.now(timezone.utc))
                and invitation_links.verify_link_pass(link, row)):
            return True
    return False


def replay_state_for(db: Session | None, ev, public_status: str) -> str:
    """The replay fact the ended watch page itself shows (routers/events.py watch_event),
    mapped to Guide's vocabulary (event_context.REPLAY_STATES):
      available    audience replay published, watermarked copy ready, not expired
      pending      published and being watermarked, or under automatic validation
      unavailable  recording off, entitlement expired, or the event ended without either
      unknown      recording on but the event has not ended (no decision exists yet)
    "available" is never claimed from recording_enabled alone: publishing is an explicit
    operator act (MED-009)."""
    if not ev.recording_enabled:
        return "unavailable"
    ent = commercial_crud.get_replay_entitlement(db, ev.id, scope="audience") if db else None
    if ent is not None:
        if commercial_crud.replay_access_expired(ent):
            return "unavailable"
        if ent.publish_state == "published" and ent.watermark_status == "ready":
            return "available"
        if ((ent.publish_state == "published" and ent.watermark_status == "pending")
                or ent.publish_state == "validating"):
            return "pending"
    return "unavailable" if public_status in ("ended", "cancelled") else "unknown"


# Route identifier (never the address itself) Guide uses to say the organizer has published a
# support contact. Guide maps it to its own copy; it cannot be turned into a link.
ORGANIZER_ROUTE = "organizer-support"


def claims_for(ev, db: Session | None = None) -> dict:
    """Exactly the public-safe claim set of Conversation Flow §16 / Architecture §9 — the
    event's public name, start time and zone, public status, replay state, and whether the
    organizer has published a support contact. Nothing else (no end time, host,
    description, settings or the contact address itself) is sent to the public Guide.

    Captions (captions_available / caption_languages) are omitted: the platform persists no
    per-event caption setting — captions exist only while a live room carries them — so
    there is no fact to state, and an omitted claim makes Guide say it cannot confirm
    captions instead of guessing."""
    start = ev.start_time
    public_status = _public_status(crud.viewer_status_of(ev), start)
    org = getattr(ev, "organization", None)
    claims = {
        "event_display_name": ev.title,
        "public_start_at": _iso(start),
        "public_timezone": ev.timezone,
        "public_status": public_status,
        "replay_public_state": replay_state_for(db, ev, public_status),
        # The organization's own Support Email, which it sets for its audience in
        # Organization settings. Only the existence of the route is claimed.
        "organizer_contact_route": ORGANIZER_ROUTE if org is not None and (
            getattr(org, "support_email", None) or "").strip() else None,
        "locale": "en-US",
    }
    return {k: v for k, v in claims.items() if v is not None}


class ViewerCredentials(BaseModel):
    """The viewer's own stored credential, sent in the request BODY — never the URL, where
    proxies and access logs would record it."""
    model_config = ConfigDict(extra="forbid")
    reg: str | None = Field(None, max_length=2048)
    link: str | None = Field(None, max_length=2048)


@router.post("/{event_id}")
def issue_event_context(event_id: uuid.UUID, request: Request,
                        body: ViewerCredentials | None = None,
                        user: User | None = Depends(get_current_user_optional),
                        db: Session = Depends(get_db)):
    reg, link = (body.reg, body.link) if body else (None, None)
    if not guide_settings().GUIDE_EVENT_CONTEXT_KEY:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Event context unavailable")
    ev = crud.get_event_unscoped(db, event_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    is_member = bool(user and (user.role == "super_admin" or user.org_id == ev.org_id))
    if crud.viewer_status_of(ev) == "draft" and not is_member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if not _may_view(db, request, ev, is_member, reg, link):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not available")
    token = event_context.issue(claims_for(ev, db), ttl_seconds=TOKEN_TTL_SECONDS)
    return {"token": token, "expires_in": TOKEN_TTL_SECONDS}
