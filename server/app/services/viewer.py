"""Viewer landing page payload (/events/{id}/viewer) and playback authorization.

One request paints the whole attendee page — event, organizer, access verdict, security
posture and support links — matching the `/organization/overview` convention rather than
making the page fan out to six endpoints before it can render.

Everything here is READ-ONLY and viewer-safe. Nothing in this module returns a figure an
attendee shouldn't see: no analytics, no recordings, no moderation state, no other
attendee's identity. That is the whole reason it exists as its own module instead of
reusing the moderator snapshot, which carries all four.

Honesty rules this module follows, because a security indicator that lies is worse than
no indicator:
  * `encrypted` reports whether media actually travels over WebRTC/DTLS-SRTP — which is
    only true when LiveKit is configured. Unconfigured returns False, not "yes anyway".
  * `edge_delivery` reports LiveKit's presence, not a CDN we don't operate. There is no
    HLS/CDN egress pipeline in this server, so nothing claims one.
  * DRM is absent from the payload entirely. No provider is configured; an
    `"drm": false` field would only invite the UI to render a control for it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Event, Organization, PlatformSetting, User
from ..security import verify_password
from ..services import livekit

# Statuses an attendee may open the landing page for. A draft or archived event does not
# exist as far as the audience is concerned — same 404 as a wrong id, so the endpoint is
# not an existence oracle for unpublished events.
VIEWABLE_STATUSES = ("published", "scheduled", "live", "paused", "ended", "cancelled")

# Attendee-facing platform config, read from the PlatformSetting KV store. Keys absent from
# the store are omitted from the response and the UI drops that row — an unconfigured help
# centre renders as one fewer link, never as a dead one.
SUPPORT_KEY = "support"
SUPPORT_FIELDS = (
    "live_chat_url", "support_email", "help_center_url", "faq_url",
    "contact_url", "privacy_url", "terms_url", "report_issue_url",
)


def room_name(event_id) -> str:
    """LiveKit room for an event. Must match services.moderation.Ctx.room — a viewer that
    joins a differently-named room silently sees an empty stage."""
    return f"event_{event_id}"


# ── access ────────────────────────────────────────────────────────────────────

def access_for(event: Event, user: User, *, link_ok: bool = False) -> tuple[bool, str, str | None]:
    """(allowed, basis, denial_reason).

    `basis` is how the caller qualified, and it is what the page reports as "access
    validated" — a UI tick that isn't backed by a named rule is decoration.

    `link_ok` is the caller's verdict on a viewer-link token. The lookup is a DB query, so it
    happens in the router (crud.event.find_access_link) and only the boolean arrives here —
    which keeps this function pure and testable without a session.

    Registration is NOT checked here: this platform has no registrations table, so there is
    nothing to validate against. `registration_required` is surfaced to the client as stored
    config and explicitly does not gate entry yet.

    The passphrase gate is deliberately SEPARATE (see password_gate): it protects the media,
    not the existence of the page, so it must not collapse into this 404 decision.
    """
    if user.role == "super_admin":
        return True, "platform_admin", None
    if user.org_id and user.org_id == event.org_id:
        return True, "organization_member", None
    # A valid, unexpired, unrevoked link is its own basis — it is how an external host,
    # partner or press attendee reaches a non-public event.
    if link_ok:
        return True, "access_link", None
    # `visibility` already carries the organizer's intent, so a public or unlisted event is
    # open to any signed-in attendee.
    if event.visibility in ("public", "unlisted"):
        return True, f"{event.visibility}_event", None
    if event.visibility == "invite_only":
        return False, "denied", "This event needs an invitation link."
    return False, "denied", "This event is private to its organization."


def password_gate(event: Event, provided: str | None, *, exempt: bool = False) -> str | None:
    """Reason the passphrase check failed, or None to pass.

    Separate from access_for because it answers a different question: access_for decides
    whether the page EXISTS for this caller, this decides whether the MEDIA opens. Merging
    them would turn a mistyped password on a public event into "event not found".

    `exempt` is for the organizing org and platform admins — an organizer must not be locked
    out of their own event by a passphrase they set for the audience.
    """
    if not event.access_password_hash or exempt:
        return None
    if not provided:
        return "This event requires a passphrase."
    if not verify_password(provided, event.access_password_hash):
        return "That passphrase is not correct."
    return None


def playback_blocked_reason(event: Event, *, registered: bool | None = None,
                            exempt: bool = False) -> str | None:
    """Why a playback token must not be issued, or None to issue one. Separate from
    access_for: being allowed on the page is not the same as being allowed on the media.

    `paused` still issues a token: the room is open and already-connected attendees stay in
    it, so refusing a late joiner would make a pause look like an ended event to them.

    Registration is enforced HERE, for the same reason the passphrase is: it guards the media, not
    the existence of the page. An attendee who has not signed up must still be able to open the
    page — that is where the Register button is. Collapsing this into access_for would 404 them and
    leave them nowhere to sign up.

    `registered` is the caller's verdict (a DB lookup, so it happens in the router) and is None
    when the caller did not check — in which case enforcement is skipped rather than guessed.
    `exempt` covers the organizing org and platform admins, who never register for their own event.

    The scheduled window time-boxes the MEDIA and is deliberately independent of `status`,
    which the host drives by hand: going live early or running past end_time never force-ends
    their broadcast, it only stops handing new viewers a token. Checked before `status` so a
    scheduled event says "hasn't started yet" instead of the vaguer "not live right now", and
    it applies to `exempt` callers too — an organizer previewing outside the window should see
    what an attendee sees.
    """
    now = datetime.now(timezone.utc)
    if event.start_time and now < event.start_time:
        return "This event hasn't started yet."
    if event.end_time and now > event.end_time:
        return "This event has ended."
    if event.status not in ("live", "paused"):
        return "This event is not live right now."
    if not livekit.configured():
        return "Streaming is not configured on this deployment."
    if event.registration_required and registered is False and not exempt:
        return "Register for this event to watch."
    return None


# ── serializers ───────────────────────────────────────────────────────────────

def _event_out(ev: Event) -> dict:
    """Viewer-safe event fields. Deliberately omits the organizer's operational config
    (waiting room, auto-recording, impact class, created_by, internal timestamps)."""
    return {
        "id": str(ev.id),
        "title": ev.title,
        "slug": ev.slug,
        "description": ev.description,
        "short_description": ev.short_description,
        "banner_image": ev.banner_image,
        "thumbnail": ev.thumbnail,
        "category": ev.category,
        "tags": ev.tags or [],
        "language": ev.language,
        "timezone": ev.timezone,
        "location": ev.location,
        "start_time": ev.start_time.isoformat() if ev.start_time else None,
        "end_time": ev.end_time.isoformat() if ev.end_time else None,
        "visibility": ev.visibility,
        "status": ev.status,
        "captions_enabled": ev.captions_enabled,
        "translation_enabled": ev.translation_enabled,
        # Stored config the attendee is told about up front. registration_required does not
        # gate entry (see access_for) — the client shows it, it does not act on it.
        "registration_required": ev.registration_required,
        "recording_enabled": ev.recording_enabled,
        # Whether a replay is promised after the event. Told to the attendee up front so
        # "will this be available later?" is answered on the page, not in a support ticket.
        "replay_enabled": ev.replay_enabled,
        "chat_enabled": ev.chat_enabled,
        "qa_enabled": ev.qa_enabled,
    }


def _organizer_out(org: Organization | None) -> dict | None:
    if org is None:
        return None
    return {
        "name": org.name,
        "logo_url": org.logo_url,
        "description": org.description,
        "website": org.website,
        "support_email": org.support_email,
        "verified": bool(org.verified),
        "social_links": org.social_links or [],
    }


def _stream_out(ev: Event, *, registered: bool | None = None, exempt: bool = False) -> dict:
    """What the player needs to decide its own state before it asks for a token."""
    blocked = playback_blocked_reason(ev, registered=registered, exempt=exempt)
    return {
        "status": ev.status,
        "live": ev.status == "live",
        "starts_at": ev.start_time.isoformat() if ev.start_time else None,
        "playback_ready": blocked is None,
        "playback_blocked_reason": blocked,
        # WebRTC through LiveKit. Named so the client never has to infer the transport
        # (and so adding an HLS path later is a new value here, not a new contract).
        "protocol": "webrtc" if livekit.configured() else None,
    }


def _security_out(ev: Event, org: Organization | None, basis: str) -> dict:
    """Every field is a fact this process can check, not a marketing claim."""
    url = livekit.settings.LIVEKIT_URL or ""
    return {
        # WebRTC media is DTLS-SRTP encrypted end to end — true exactly when there is a
        # LiveKit to carry it. No LiveKit, no encrypted stream to promise.
        "encrypted": livekit.configured(),
        "transport_secure": url.startswith("wss://") or url.startswith("https://"),
        # The platform's organizer badge, not a DNS check.
        "verified_event": bool(org and org.verified),
        # Which rule let this caller in — the UI shows this rather than a bare green tick.
        "access_basis": basis,
        # LiveKit routes participants to its nearest edge; this reports that it is in the
        # path, NOT that we run a CDN (we don't — there is no HLS/CDN egress here).
        "edge_delivery": livekit.configured(),
        "edge_host": urlparse(url).hostname if url else None,
        "region": org.region if org else None,
        "visibility": ev.visibility,
    }


def _support_out(db: Session) -> dict:
    """Attendee help + legal links from platform settings. Unset keys are omitted so the
    UI renders one fewer row instead of a link that goes nowhere."""
    row = db.get(PlatformSetting, SUPPORT_KEY)
    value = row.value if row and isinstance(row.value, dict) else {}
    return {k: value[k] for k in SUPPORT_FIELDS if value.get(k)}


# ── entry point ───────────────────────────────────────────────────────────────

def landing(db: Session, ev: Event, user: User, *, link_ok: bool = False,
            password: str | None = None) -> dict:
    """Whole-page payload. Callers resolve+authorize the event first (routers/events.py),
    so this never decides a 404 — it only serializes what was already permitted."""
    from ..crud import attendee as crud_attendee

    org = db.scalar(select(Organization).where(Organization.id == ev.org_id))
    allowed, basis, _ = access_for(ev, user, link_ok=link_ok)
    exempt = user.role == "super_admin" or (bool(user.org_id) and user.org_id == ev.org_id)
    password_error = password_gate(ev, password, exempt=exempt)
    # The attendee's own relationship with this event: registered, bookmarked, watch history. One
    # row, and it is what turns registration from stored config into an enforced gate.
    reg = crud_attendee.get_registration(db, ev.id, user.id)
    registered = bool(reg and reg.status in ("registered", "attended"))
    return {
        "event": _event_out(ev),
        "organizer": _organizer_out(org),
        "stream": _stream_out(ev, registered=registered, exempt=exempt),
        "security": _security_out(ev, org, basis),
        "support": _support_out(db),
        "access": {
            "allowed": allowed,
            "basis": basis,
            # Registration is now a real gate on the MEDIA (see playback_blocked_reason). The page
            # still renders for somebody who has not signed up — that is where the Register button
            # is — so `registered` is what the client acts on and `registration_required` is what
            # it explains.
            "registration_required": ev.registration_required,
            "registration_enforced": True,
            "registered": registered,
            "registration_status": reg.status if reg else None,
            "registration_limit": ev.registration_limit,
            # Passphrase state, so the page can render the prompt instead of a dead player.
            # `password_satisfied` is the fact the client acts on; `password_required` is
            # what it explains to the attendee.
            "password_required": bool(ev.access_password_hash),
            "password_satisfied": password_error is None,
        },
        "viewer": {"id": str(user.id), "name": user.full_name or user.email, "role": user.role},
        # This attendee's own personalisation for this event, so the page can render the bookmark
        # and reminder state without a second request.
        "me": {
            "bookmarked": bool(reg and reg.bookmarked),
            "reminder_at": reg.reminder_at.isoformat() if reg and reg.reminder_at else None,
            "watch_seconds": (reg.watch_seconds or 0) if reg else 0,
            "question_bookmarks": (reg.question_bookmarks or []) if reg else [],
        },
    }
