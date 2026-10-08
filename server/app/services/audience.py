"""Audience records and the Audience page's insights (models/audience.py).

Recording (called from routers/events.py watch_event and routers/live.py):
  record_join    an identified viewer was admitted to the live stream: one row per event and
                 viewer, so reconnects never inflate the audience. join_count counts real
                 rejoins (after REJOIN_GAP), not every poll or reconnect blip.
  record_leave   their live socket closed: last seen, left, time watched live accumulated.
  record_denial  an attempt to watch was refused. De-duplicated per requester for
                 DENIAL_WINDOW in PROCESS MEMORY ONLY (a hash, never persisted), so a page
                 retrying a refused request is one attempt, not fifty.

Every recorder is best-effort: analytics must never break or slow the viewer's request, so a
failure is logged and swallowed.

Privacy: coarse normalized device/browser/OS/client classes from the user agent (never the
string), a country only if the viewer chose one at registration, no IP address, no token.

Aggregation: insights() — geography, device mix, registration -> attendance, blocked join
attempts — for one organization over the Audience page's window, optionally one event.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..countries import COUNTRY_NAMES
from ..models import (
    CLIENT_TYPES,
    DENIAL_REASONS,
    AudienceSession,
    BroadcastSession,
    Event,
    EventRegistration,
    JoinDenial,
    User,
)

log = logging.getLogger(__name__)

REJOIN_GAP = timedelta(minutes=10)
STALE_VISIT = timedelta(hours=6)
DENIAL_WINDOW_SECONDS = 600

DENIAL_LABELS = {
    "invalid_invite": "Invalid or expired invitation",
    "invalid_link": "Invalid or expired access link",
    "invite_used_elsewhere": "Invitation used on another device",
    "not_invited": "Private event, not invited",
    "event_full": "Registration full",
    "at_capacity": "Live capacity reached",
    "event_cancelled": "Event cancelled",
    "event_unavailable": "Event not published",
    "removed_by_host": "Removed by the host",
}
DEVICE_LABELS = {"desktop": "Desktop", "mobile": "Mobile", "tablet": "Tablet", "other": "Other"}
BROWSER_LABELS = {"chrome": "Chrome", "safari": "Safari", "firefox": "Firefox", "edge": "Edge", "other": "Other"}
OS_LABELS = {"windows": "Windows", "macos": "macOS", "android": "Android", "ios": "iOS", "linux": "Linux",
             "other": "Other"}
CLIENT_LABELS = {"web": "Web browser", "mobile": "Mobile app", "embedded": "Embedded player", "other": "Other"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── normalization ─────────────────────────────────────────────────────────────────────────

_MOBILE = re.compile(r"iphone|ipod|android.*mobile|windows phone", re.I)
_TABLET = re.compile(r"ipad|android(?!.*mobile)|tablet", re.I)
_OS = ((r"windows nt", "windows"), (r"iphone|ipad|ipod", "ios"), (r"mac os x|macintosh", "macos"),
       (r"android", "android"), (r"linux", "linux"))
_BROWSER = ((r"edg/|edga/|edgios/", "edge"), (r"chrome/|crios/", "chrome"),
            (r"firefox/|fxios/", "firefox"), (r"safari/", "safari"))


def normalize_agent(user_agent: str | None, client_hint: str | None = None) -> dict:
    """Coarse classes only, from a closed vocabulary. The user agent is read, never kept.

    `client_hint` is what the viewer page says it is running in (web, the packaged mobile app,
    or embedded in another page's iframe) — something only the page can know. Anything outside
    the vocabulary is read as plain web."""
    ua = user_agent or ""

    def first(pairs, default="other"):
        for pattern, label in pairs:
            if re.search(pattern, ua, re.I):
                return label
        return default

    device = ("mobile" if _MOBILE.search(ua) else "tablet" if _TABLET.search(ua)
              else "desktop" if ua else "other")
    hint = (client_hint or "").strip().lower()
    client = hint if hint in CLIENT_TYPES else "web"
    return {"device_type": device, "browser": first(_BROWSER), "os": first(_OS), "client_type": client}


def viewer_key(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def identified(identity: str | None) -> bool:
    """A credential-backed identity that stands for one viewer. The anonymous `viewer-<uuid>`
    is minted fresh per request and cannot be told apart from the next one."""
    return bool(identity) and not identity.startswith("viewer-")


# ── recording ─────────────────────────────────────────────────────────────────────────────

def record_join(db: Session, event: Event, identity: str, *, registration_id: uuid.UUID | None = None,
                user_agent: str | None = None, client_hint: str | None = None) -> None:
    """An identified, non-staff viewer was admitted to the live stream. Best-effort."""
    if not identified(identity):
        return
    try:
        _upsert_join(db, event, identity, registration_id, normalize_agent(user_agent, client_hint))
    except Exception:  # noqa: BLE001 - analytics must never break the viewer's request
        db.rollback()
        log.warning("audience join not recorded for event %s", event.id, exc_info=True)


def _upsert_join(db: Session, event: Event, identity: str, registration_id, agent: dict) -> None:
    now = _now()
    key = viewer_key(identity)
    country = None
    if registration_id is not None:
        reg = db.get(EventRegistration, registration_id)
        if reg is None or reg.event_id != event.id:
            registration_id = None   # a token for a registration since removed
        else:
            country = reg.country_code
    row = db.scalar(select(AudienceSession).where(
        AudienceSession.event_id == event.id, AudienceSession.viewer_key == key))
    if row is None:
        row = AudienceSession(
            org_id=event.org_id, event_id=event.id, viewer_key=key, registration_id=registration_id,
            country_code=country, joined_at=now, last_seen_at=now, join_count=1, watch_seconds=0, **agent)
        db.add(row)
        try:
            db.commit()
            return
        except IntegrityError:
            # Two tabs joined at once: the other request created the row. Update it instead.
            db.rollback()
            row = db.scalar(select(AudienceSession).where(
                AudienceSession.event_id == event.id, AudienceSession.viewer_key == key))
            if row is None:
                return
    # last_seen_at doubles as the start of the visit record_leave credits watch time from.
    # Admitted again mid-visit (a second tab, or a refresh whose new request beat the old
    # socket's close) leaves it alone; only a return after leaving starts a new visit, and
    # only one after REJOIN_GAP counts as another join. A visit whose socket never reported
    # closing (a server restart) is treated as over after STALE_VISIT.
    left = _aware(row.left_at)
    if left is not None or now - _aware(row.last_seen_at) >= STALE_VISIT:
        if now - (left or _aware(row.last_seen_at)) >= REJOIN_GAP:
            row.join_count += 1
        row.last_seen_at = now
        row.left_at = None
    if country and not row.country_code:
        row.country_code = country
    if registration_id and not row.registration_id:
        row.registration_id = registration_id
    db.commit()


def set_registration_country(db: Session, registration: EventRegistration, country: str | None) -> None:
    """A viewer changed their optional Country / Region: the registration and any audience
    row already recorded for it follow, so geography reflects their latest answer."""
    registration.country_code = country
    db.execute(AudienceSession.__table__.update()
               .where(AudienceSession.registration_id == registration.id)
               .values(country_code=country))
    db.commit()


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def record_leave(db: Session, event_id: uuid.UUID, identity: str, connected_since: datetime) -> None:
    """A viewer's live socket closed. Updates an existing row only (staff never have one).

    Watch time credited is the part of this connection that was spent admitted to the live
    stream: from the later of the socket opening and the row's last sighting (the admission,
    or the previous leave), to now while the event is on air, or to when the broadcast ended.
    A page left open on the pre-show screen or the replay page earns nothing, and two tabs
    closing one after the other credit the overlap once (the first leave moves the sighting
    forward). Best-effort."""
    if not identified(identity):
        return
    try:
        row = db.scalar(select(AudienceSession).where(
            AudienceSession.event_id == event_id, AudienceSession.viewer_key == viewer_key(identity)))
        if row is None:
            return
        now = _now()
        status = db.scalar(select(Event.status).where(Event.id == event_id))
        if status in ("live", "degraded"):
            until = now
        else:
            until = _aware(db.scalar(select(func.max(BroadcastSession.ended_at))
                                     .where(BroadcastSession.event_id == event_id)))
        start = max(_aware(connected_since), _aware(row.last_seen_at))
        if until is not None and until > start:
            row.watch_seconds += int((min(until, now) - start).total_seconds())
        row.last_seen_at = now
        row.left_at = now
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("audience leave not recorded for event %s", event_id, exc_info=True)


_recent_denials: dict[str, float] = {}
_denials_lock = threading.Lock()


def requester(address: str | None, user_agent: str | None) -> str:
    """Who is retrying, for de-duplication only: the address AND the browser, so two people
    behind one office or campus address are still two attempts. Hashed in _first_denial and
    held in memory for DENIAL_WINDOW_SECONDS; never written anywhere."""
    return f"{address or ''}|{user_agent or ''}"


def _first_denial(event_id, reason: str, requester: str | None) -> bool:
    key = hashlib.sha256(f"{event_id}|{reason}|{requester or ''}".encode("utf-8")).hexdigest()
    now = time.monotonic()
    with _denials_lock:
        for k, seen in list(_recent_denials.items()):
            if now - seen > DENIAL_WINDOW_SECONDS:
                del _recent_denials[k]
        if key in _recent_denials:
            return False
        _recent_denials[key] = now
        return True


def record_denial(db: Session, event: Event, reason: str, *, requester: str | None = None,
                  registration_id: uuid.UUID | None = None) -> None:
    """A refused attempt to watch. `requester` (an identity, or requester() of the client
    address and browser) is used ONLY for in-memory de-duplication and is never written
    anywhere. Best-effort."""
    if reason not in DENIAL_REASONS:
        return
    if not _first_denial(event.id, reason, requester):
        return
    try:
        db.add(JoinDenial(org_id=event.org_id, event_id=event.id, reason=reason,
                          registration_id=registration_id, created_at=_now()))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("join denial not recorded for event %s", event.id, exc_info=True)


# ── for the live socket (async callers, via asyncio.to_thread; own short-lived session) ────

def _with_session(fn):
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def record_leave_by_id(event_id: uuid.UUID, identity: str, connected_since: datetime) -> None:
    try:
        _with_session(lambda db: record_leave(db, event_id, identity, connected_since))
    except Exception:  # noqa: BLE001
        log.warning("audience leave not recorded for event %s", event_id, exc_info=True)


def record_denial_by_id(event_id: uuid.UUID, reason: str, requester: str | None = None) -> None:
    def go(db):
        event = db.get(Event, event_id)
        if event is not None:
            record_denial(db, event, reason, requester=requester)
    try:
        _with_session(go)
    except Exception:  # noqa: BLE001
        log.warning("join denial not recorded for event %s", event_id, exc_info=True)


# ── aggregation ───────────────────────────────────────────────────────────────────────────

def _pct(part: int, whole: int) -> int | None:
    return round(100 * part / whole) if whole else None


def _breakdown(rows, key, labels=None):
    total = sum(n for _, n in rows)
    out = [{key: value, "label": (labels or {}).get(value, value.title()), "viewers": n,
            "percentage": _pct(n, total)} for value, n in rows]
    return sorted(out, key=lambda r: (-r["viewers"], r[key]))


def insights(db: Session, org, range_key: str = "30d", event_id: uuid.UUID | None = None) -> dict:
    """Geography, device mix, registration -> attendance and blocked joins for one
    organization over the Audience page's window (and optionally one event).

    Every figure counts one identified viewer per event (a viewer of two events counts in
    both), never socket reconnects. Staff are never recorded, so never counted.
    """
    from .org import ANALYTICS_RANGES   # local import: org imports far more than this module

    since = _now() - ANALYTICS_RANGES.get(range_key, ANALYTICS_RANGES["30d"])
    sessions_where = [AudienceSession.org_id == org.id, AudienceSession.last_seen_at >= since]
    denials_where = [JoinDenial.org_id == org.id, JoinDenial.created_at >= since]
    if event_id is not None:
        sessions_where.append(AudienceSession.event_id == event_id)
        denials_where.append(JoinDenial.event_id == event_id)

    viewers = db.scalar(select(func.count()).select_from(AudienceSession).where(*sessions_where)) or 0

    # Geography: share of viewers who gave a country; the rest are reported, not guessed.
    countries = db.execute(
        select(AudienceSession.country_code, func.count())
        .where(*sessions_where, AudienceSession.country_code.isnot(None))
        .group_by(AudienceSession.country_code)
    ).all()
    known = sum(n for _, n in countries)
    geography = sorted(
        ({"country_code": code, "country_name": COUNTRY_NAMES.get(code, code), "viewers": n,
          "percentage": _pct(n, known)} for code, n in countries),
        key=lambda r: (-r["viewers"], r["country_name"]))

    def counts(column):
        return db.execute(select(column, func.count()).where(*sessions_where).group_by(column)).all()

    device_mix = _breakdown(counts(AudienceSession.device_type), "device_type", DEVICE_LABELS)

    # Registration -> attendance: registrations for the window's events (the same window
    # predicate as org.audience_attendance), and how many of those registrants were admitted to
    # the live stream. Every link-shareable event asks viewers to register and a private one
    # registers its invitees (routers/events.registration_gate_required), so the measure is
    # "not applicable" exactly when nobody registered — never a 0% over nothing.
    event_scope = [
        Event.org_id == org.id, Event.deleted_at.is_(None),
        or_(Event.start_time >= since, Event.end_time >= since, Event.status.in_(("live", "degraded")),
            and_(Event.start_time.is_(None), Event.created_at >= since),
            Event.id.in_(select(BroadcastSession.event_id).where(BroadcastSession.started_at >= since))),
    ]
    if event_id is not None:
        event_scope.append(Event.id == event_id)
    staff_emails = select(func.lower(User.email)).where(User.org_id == org.id)
    reg_where = [EventRegistration.event_id.in_(select(Event.id).where(*event_scope)),
                 func.lower(EventRegistration.email).notin_(staff_emails)]
    registered = db.scalar(select(func.count()).select_from(EventRegistration).where(*reg_where)) or 0
    attended = db.scalar(
        select(func.count(func.distinct(EventRegistration.id)))
        .join(AudienceSession, AudienceSession.registration_id == EventRegistration.id)
        .where(*reg_where)
    ) or 0

    reasons = db.execute(
        select(JoinDenial.reason, func.count()).where(*denials_where).group_by(JoinDenial.reason)
    ).all()

    return {
        "range": range_key,
        "since": since,
        "event_id": event_id,
        "viewers": viewers,
        "geography": geography,
        "geography_known": known,
        "geography_unknown": viewers - known,
        "device_mix": device_mix,
        "device_breakdown": {
            "browsers": _breakdown(counts(AudienceSession.browser), "browser", BROWSER_LABELS),
            "operating_systems": _breakdown(counts(AudienceSession.os), "os", OS_LABELS),
            "clients": _breakdown(counts(AudienceSession.client_type), "client_type", CLIENT_LABELS),
        },
        "registration_attendance": {
            "applicable": registered > 0,
            "registered": registered,
            "attended": attended,
            "rate": _pct(attended, registered),
        },
        "blocked_join_attempts": {
            "total": sum(n for _, n in reasons),
            "reasons": sorted(({"reason": r, "label": DENIAL_LABELS.get(r, r), "count": n} for r, n in reasons),
                              key=lambda x: -x["count"]),
        },
    }
