"""Context Service (ZST-UX-GUIDE-001 §6.1, §11): organization · workspace · event · environment.

The client tells Guide which page it is on and which event is selected; the server treats both
as hints only. The organization always comes from the authenticated user, and an event id is
honoured only after policy.resolve_event has proven it belongs to that organization.
"""
from __future__ import annotations

import re

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.models.event import Event

from . import policy
from .policy import Actor
from .sources import iso, now

# Console route prefix -> workspace label shown in the Guide header.
WORKSPACES = (
    ("/organization/events", "Events"),
    ("/organization/analytics", "Analytics"),
    ("/organization/recordings", "Recordings"),
    ("/organization/developers", "Developers"),
    ("/organization/support", "Support"),
    ("/organization", "Organization"),
    ("/host", "Host console"),
    ("/admin", "Platform admin"),
    ("/dashboard", "Dashboard"),
)
_EVENT_IN_ROUTE = re.compile(
    r"(?:/events/|[?&]event=)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12})")


def workspace_for(route: str) -> str:
    for prefix, label in WORKSPACES:
        if route.startswith(prefix):
            return label
    return "Console"


def event_id_from_route(route: str) -> str | None:
    m = _EVENT_IN_ROUTE.search(route or "")
    return m.group(1) if m else None


def event_brief(ev: Event) -> dict:
    return {"id": str(ev.id), "title": ev.title or "Untitled event", "status": ev.status,
            "start_time": iso(ev.start_time)}


def resolve(db: Session, actor: Actor, *, route: str = "", event_id: str | None = None) -> dict:
    """The context for one request. An event that is not the caller's is silently dropped
    (the route may be stale); the explicit id is checked strictly by the caller instead."""
    route = (route or "")[:300]
    event = None
    candidate = event_id or event_id_from_route(route)
    if candidate:
        try:
            event = policy.resolve_event(db, actor, candidate)
        except Exception:
            event = None
    return {
        "organization": {"id": str(actor.org_id), "name": actor.org.name},
        "workspace": workspace_for(route),
        "route": route,
        "event": event_brief(event) if event else None,
        "environment": "test" if getattr(actor.org, "is_test", False) else "production",
        "role": actor.role,
        "_event": event,
    }


def public(ctx: dict) -> dict:
    return {k: v for k, v in ctx.items() if not k.startswith("_")}


def selectable_events(db: Session, actor: Actor, *, q: str = "", limit: int = 20) -> list[dict]:
    """Events for the context selector: on-air first, then upcoming, then most recent."""
    on_air = case((Event.status.in_(("live", "degraded")), 0), else_=1)
    upcoming = case((Event.start_time >= now(), 0), else_=1)
    stmt = (select(Event).where(Event.org_id == actor.org_id, Event.deleted_at.is_(None),
                                Event.status != "archived")
            .order_by(on_air, upcoming, Event.start_time.asc().nulls_last(),
                      Event.created_at.desc()).limit(min(limit, 50)))
    if q:
        stmt = stmt.where(Event.title.ilike(f"%{q.strip()[:80]}%"))
    return [event_brief(ev) for ev in db.scalars(stmt).all()]


def event_named_in(db: Session, actor: Actor, text: str) -> Event | None:
    """An event whose full title appears in the message ("is Spring Gala ready?"). Exact
    title containment on word boundaries only ("Test" is not named in "latest") — never a
    fuzzy guess at which event the user meant. A named event can switch the context, so the
    match has to be exact."""
    lowered = f" {text.lower()} "
    best = None
    for ev in db.scalars(select(Event).where(
            Event.org_id == actor.org_id, Event.deleted_at.is_(None),
            Event.status != "archived").order_by(Event.created_at.desc()).limit(200)).all():
        title = (ev.title or "").strip().lower()
        if len(title) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(title)}(?![a-z0-9])", lowered) \
                and (best is None or len(title) > len(best[0])):
            best = (title, ev)
    return best[1] if best else None
