"""DB access + validation helpers for the Events API. Pure queries and partial updates,
no HTTP. Mirrors crud/organization.py."""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import asc, case, desc, func, or_, select

from ..models import (
    ContributorSession, Event, EventAccessLink, EventAssignment, EventFeedback,
    EventRegistration, LiveIngressEndpoint, LiveRecording, User,
)

_EVENT_SORTS = {
    "created_at": Event.created_at,
    "start_time": Event.start_time,
    "title": Event.title,
    "status": Event.status,
}


# ── Lifecycle validation (pure — unit-testable without a DB) ──────────────────

def status_transition_error(
    current: str, new: str, title,
    *, readiness_ready: bool | None = None, readiness_reasons: list[str] | None = None,
) -> str | None:
    """Return an error message if current -> new is not allowed, else None.
    Encodes the base spec rules plus the optional v1.1 canonical-spec chain
    (rehearsal -> ready_to_arm -> armed -> live -> degraded -> ending -> processing ->
    replay_ready -> ended); other transitions are permitted. Ordinary events that skip
    straight from published/scheduled to live are unaffected — that path is untouched.

    `readiness_ready` gates armed and MUST be explicitly True (not just not-False) for the
    caller to arm — this is the spec's "non-waivable" guard, so an omitted/None value blocks
    rather than silently passing."""
    if new == current:
        return None
    if new in ("published", "scheduled") and not (title and str(title).strip()):
        return "Cannot publish an event without a title"
    if new == "ready_to_arm" and current not in ("published", "scheduled", "rehearsal"):
        return "Must be published or rehearsed before marking ready to arm"
    if new == "armed":
        if current != "ready_to_arm":
            return "Must be ready_to_arm before arming"
        if readiness_ready is not True:
            reasons = f": {'; '.join(readiness_reasons)}" if readiness_reasons else ""
            return f"Cannot arm — readiness checks have not passed{reasons}"
    if new == "live" and current not in ("published", "scheduled", "armed"):
        return "Cannot go live unless the event is published or armed"
    if new == "degraded" and current != "live":
        return "Only a live event can be marked degraded"
    if new == "ending" and current not in ("live", "degraded"):
        return "Can only end from live or degraded"
    if new == "processing" and current != "ending":
        return "Must be ending before processing"
    if new == "replay_ready" and current != "processing":
        return "Must be processing before replay is ready"
    if new == "ended" and current not in ("live", "degraded", "replay_ready"):
        return "Cannot end an event that is not live"
    if new == "archived" and current in ("live", "armed", "degraded", "ending", "processing"):
        return "Cannot archive an active event"
    return None


# Category -> minimum risk tier (doc Sec. 4.1/4.2: "Category sets the minimum risk class...
# Memorials default here and cannot be downgraded"). Only the memorial category has a
# defined floor in the current registry; every other category stays at the r0 baseline
# until later categories get their own entries.
CATEGORY_MIN_RISK_TIER = {"Funeral / Memorial": "r2"}
_RISK_ORDER = {"r0": 0, "r1": 1, "r2": 2, "r3": 3}


def category_min_risk_tier(category: str | None) -> str:
    return CATEGORY_MIN_RISK_TIER.get(category or "", "r0")


def elevated_risk_tier(category: str | None, proposed: str) -> str:
    """The risk tier to actually store: never below the category's floor. Category alone
    can only raise a tier, never lower one an operator or commercial order explicitly set
    higher — so this is safe to apply unconditionally on every write."""
    minimum = category_min_risk_tier(category)
    return minimum if _RISK_ORDER[minimum] > _RISK_ORDER.get(proposed, 0) else proposed


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
    fields = data.model_dump(exclude={"slug"})
    fields["risk_tier"] = elevated_risk_tier(fields.get("category"), "r0")
    ev = Event(org_id=org_id, created_by=created_by, slug=slug, **fields)
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


# ── Registrations (self-serve, anonymous) ─────────────────────────────────────

def get_registration(db, event_id, email) -> EventRegistration | None:
    return db.scalar(
        select(EventRegistration).where(
            EventRegistration.event_id == event_id,
            EventRegistration.email == email.lower(),
        )
    )


def count_registrations(db, event_id) -> int:
    return db.scalar(
        select(func.count()).select_from(EventRegistration).where(EventRegistration.event_id == event_id)
    )


def create_registration(db, event_id, name, email, invited_by=None) -> EventRegistration:
    reg = EventRegistration(event_id=event_id, name=name, email=email.lower(), invited_by=invited_by)
    db.add(reg)
    db.commit()
    db.refresh(reg)
    return reg


def list_registrations(db, event_id) -> list[EventRegistration]:
    return db.scalars(
        select(EventRegistration)
        .where(EventRegistration.event_id == event_id)
        .order_by(EventRegistration.created_at)
    ).all()


def list_feedback(db, event_id, role: str | None = None) -> list[EventFeedback]:
    """Feedback rows for an event, newest first. `role` filters to "host" or "viewer" —
    the host dashboard only wants viewer feedback, and the organizer's event page only
    wants the host's own; leaving it unset returns everything."""
    stmt = select(EventFeedback).where(EventFeedback.event_id == event_id)
    if role:
        stmt = stmt.where(EventFeedback.role == role)
    return db.scalars(stmt.order_by(EventFeedback.created_at.desc())).all()


# ── Contributor (speaker) backstage invitations ────────────────────────────────
# EventAssignment(role="speaker") stays the eligibility list; ContributorSession is the
# per-event invitation + runtime backstage state for one of those assignees (see
# models/live.py's ContributorSession docstring — presence in services/bus.py is
# ephemeral and can't hold consent/preflight/rehearsal across a dropped room).

def get_contributor_session(db, event_id, user_id) -> ContributorSession | None:
    return db.scalar(
        select(ContributorSession).where(
            ContributorSession.event_id == event_id, ContributorSession.user_id == user_id,
        )
    )


def upsert_contributor_invite(db, event: Event, user: User, invited_by_id, *, join_window_start,
                              join_window_end, expires_at, contribution_method, consent_notice,
                              support_contact) -> ContributorSession:
    """(Re-)invite an assigned speaker. Re-inviting resets `state` to "waiting" — a fresh
    invite means a fresh backstage session, not a resumption of whatever the last one
    reached (consent/preflight from a stale invite must not silently carry over)."""
    s = get_contributor_session(db, event.id, user.id)
    if s is None:
        s = ContributorSession(event_id=event.id, org_id=event.org_id, user_id=user.id,
                               identity=str(user.id))
        db.add(s)
    s.state = "waiting"
    s.invited_at = datetime.now(timezone.utc)
    s.invited_by = invited_by_id
    s.join_window_start = join_window_start
    s.join_window_end = join_window_end
    s.expires_at = expires_at
    s.contribution_method = contribution_method or "livekit_browser"
    s.consent_notice = consent_notice
    s.support_contact = support_contact
    s.consent_given, s.consent_at = False, None
    s.preflight_result = None
    s.rehearsal_complete, s.rehearsal_at = False, None
    s.removed_by, s.removed_at, s.removed_reason = None, None, None
    db.commit()
    db.refresh(s)
    return s


def revoke_contributor_session(db, session: ContributorSession) -> ContributorSession:
    session.state = "removed"
    session.expires_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session


def get_registration_by_id(db, event_id, registration_id) -> EventRegistration | None:
    return db.scalar(
        select(EventRegistration).where(
            EventRegistration.id == registration_id, EventRegistration.event_id == event_id,
        )
    )


# One-device claim on a private event's personal invite token — see EventRegistration's
# docstring. Reuses _hash_link_token's sha256 scheme further down this file.

def claim_registration(db, reg: EventRegistration) -> str:
    """First successful use of a private event's invite token claims this row to one
    device. Returns the raw claim secret to set as an httpOnly cookie; only its hash is
    stored, so a leaked DB row alone can't forge a claim."""
    raw = secrets.token_urlsafe(32)
    reg.claim_token_hash = _hash_link_token(raw)
    reg.claimed_at = datetime.now(timezone.utc)
    db.commit()
    return raw


def claim_matches(reg: EventRegistration, raw: str | None) -> bool:
    return bool(raw) and reg.claim_token_hash == _hash_link_token(raw)


def list_replay_candidates(db, event_id) -> list[LiveRecording]:
    """Finished, actually-captured recordings for this event, primary-role first and newest
    within each role — what a viewer's replay link points at. `enforced=False` rows (LiveKit
    egress unavailable) are excluded: there is no file behind them. Returns every candidate,
    not just the newest, because the caller verifies each against GCS and a "stopped" row can
    still turn out to have no real file behind it (see services.livekit.object_exists) — the
    next candidate (secondary, or the next-newest) is the fallback.

    Under dual recording (services/broadcast.py._recording_start), `role` distinguishes the
    canonical primary path from its secondary/backup — a viewer should always land on the
    primary's file when it's actually there, not whichever egress happened to finish first."""
    role_order = case((LiveRecording.role == "primary", 0), (LiveRecording.role == "secondary", 1), else_=0)
    return db.scalars(
        select(LiveRecording)
        .where(LiveRecording.event_id == event_id, LiveRecording.status == "stopped",
               LiveRecording.enforced.is_(True))
        .order_by(role_order, LiveRecording.stopped_at.desc())
    ).all()


# ── Access links (revocable, shareable — the link-based counterpart to registrations) ─────
# Mirrors crud/organization.py's invitation tokens: the raw token is returned once by
# create/rotate and never stored, only its sha256 hash (_hash_link_token).

def _hash_link_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def list_access_links(db, event_id) -> list[EventAccessLink]:
    return db.scalars(
        select(EventAccessLink)
        .where(EventAccessLink.event_id == event_id)
        .order_by(EventAccessLink.created_at.desc())
    ).all()


def get_access_link(db, event_id, link_id) -> EventAccessLink | None:
    return db.scalar(
        select(EventAccessLink).where(EventAccessLink.id == link_id, EventAccessLink.event_id == event_id)
    )


def create_access_link(db, event_id, org_id, created_by, label, expires_in_days) -> tuple[EventAccessLink, str]:
    raw = secrets.token_urlsafe(32)
    link = EventAccessLink(
        event_id=event_id, org_id=org_id, label=label, token_hash=_hash_link_token(raw), created_by=created_by,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link, raw


def rotate_access_link(db, link: EventAccessLink, expires_in_days) -> tuple[EventAccessLink, str]:
    """Re-secrets the same row for a lost or revoked link: fresh token, fresh usage stats,
    and — deliberately — un-revokes it, since regenerating a revoked link is how the UI lets
    an admin bring an audience back without recreating the row's label/history."""
    raw = secrets.token_urlsafe(32)
    link.token_hash = _hash_link_token(raw)
    link.revoked_at = None
    link.uses = 0
    link.last_used_at = None
    link.expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days else None
    db.commit()
    db.refresh(link)
    return link, raw


def revoke_access_link(db, link: EventAccessLink) -> EventAccessLink:
    link.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(link)
    return link


def delete_access_link(db, link: EventAccessLink) -> None:
    db.delete(link)
    db.commit()


def find_access_link(db, event_id, raw: str) -> EventAccessLink | None:
    """The three rules a link must pass to admit a viewer: belongs to this event, not
    revoked, not expired. Recording a use is a side effect of a successful lookup here —
    the only place usage is counted, since the raw token only ever reaches this check via a
    link someone actually opened."""
    if not raw:
        return None
    link = db.scalar(
        select(EventAccessLink).where(
            EventAccessLink.event_id == event_id,
            EventAccessLink.token_hash == _hash_link_token(raw),
        )
    )
    if link is None or link.revoked_at is not None:
        return None
    if link.expires_at and link.expires_at < datetime.now(timezone.utc):
        return None
    link.uses += 1
    link.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return link


def get_org_recording(db, org_id, recording_id) -> LiveRecording | None:
    """A single recording, scoped through its event's org_id (not the recording's own
    org_id copy — see list_org_recordings)."""
    return db.scalar(
        select(LiveRecording)
        .join(Event, Event.id == LiveRecording.event_id)
        .where(LiveRecording.id == recording_id, Event.org_id == org_id)
    )


def get_org_ingress_endpoint(db, org_id, endpoint_id) -> LiveIngressEndpoint | None:
    """A single live input, scoped through its event's org_id — same posture as
    get_org_recording."""
    return db.scalar(
        select(LiveIngressEndpoint)
        .join(Event, Event.id == LiveIngressEndpoint.event_id)
        .where(LiveIngressEndpoint.id == endpoint_id, Event.org_id == org_id)
    )


def list_org_ingress_endpoints(db, org_id) -> list[tuple[LiveIngressEndpoint, Event]]:
    """Every live input across the org, newest first — the org-wide Live Inputs page.
    Joined to Event for title, same reasoning as list_org_recordings."""
    rows = db.execute(
        select(LiveIngressEndpoint, Event)
        .join(Event, Event.id == LiveIngressEndpoint.event_id)
        .where(Event.org_id == org_id)
        .order_by(LiveIngressEndpoint.created_at.desc())
    ).all()
    return list(rows)


def list_org_recordings(db, org_id, limit: int = 100) -> list[tuple[LiveRecording, Event]]:
    """Every captured recording across the org, newest first — the org-wide Recordings
    library. Joined to Event for title/category; org-scoped via Event.org_id (matches every
    other org-isolation check in this module) rather than LiveRecording.org_id directly, so
    a stale org_id copy on the recording row can never leak a row from another tenant."""
    rows = db.execute(
        select(LiveRecording, Event)
        .join(Event, Event.id == LiveRecording.event_id)
        .where(Event.org_id == org_id, LiveRecording.status == "stopped",
               LiveRecording.enforced.is_(True))
        .order_by(LiveRecording.stopped_at.desc())
        .limit(limit)
    ).all()
    return list(rows)
