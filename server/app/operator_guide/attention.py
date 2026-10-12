"""Proactive attention (ZST-UX-GUIDE-001 §20): material items only.

Guide surfaces what an operator would want to know without asking — and nothing else. Every
item is derived from authoritative platform state; nothing here is inferred by a model. The
launcher shows the count as a badge (never an animation), and the Guide home lists the items
with a question the user can ask to get the full picture.

Material means: an upcoming event that is Blocked or Conditional, a broadcast that is
degraded or down right now, a recording that failed, access links about to expire on an
upcoming event, and a support case waiting on the customer.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event, EventAccessLink
from app.models.live import BroadcastSession, LiveRecording
from app.models.support_ticket import SupportTicket
from app.services import org as org_svc

from . import policy
from .policy import Actor
from .sources import iso, now
from .tools.events import event_href

SEVERITY_ORDER = {"blocker": 0, "warning": 1, "info": 2}
HORIZON = timedelta(days=7)


def _item(kind: str, severity: str, title: str, detail: str, *, href: str,
          ask: str, event=None) -> dict:
    return {"id": f"{kind}:{event.id if event is not None else 'org'}", "kind": kind,
            "severity": severity, "title": title, "detail": detail, "href": href, "ask": ask,
            "event": {"id": str(event.id), "title": event.title} if event is not None else None}


def items(db: Session, actor: Actor) -> list[dict]:
    t = now()
    out: list[dict] = []
    relevant = _relevant(db, actor)

    # Live problems first: they are the only items that cannot wait.
    sessions = db.scalars(select(BroadcastSession).where(
        BroadcastSession.org_id == actor.org_id, BroadcastSession.status.in_(("live", "paused")),
        BroadcastSession.health_level.in_(("warn", "down")))).all()
    for s in sessions:
        ev = db.get(Event, s.event_id)
        if ev is None or not relevant(ev):
            continue
        down = s.health_level == "down"
        out.append(_item("live_health", "blocker" if down else "warning",
                         f"{ev.title}: broadcast {'down' if down else 'degraded'}",
                         (s.health_issues or "The platform reports a health problem.")[:200],
                         href=f"/host/dashboard?event={ev.id}", event=ev,
                         ask="What's wrong with the live broadcast?"))

    for e in org_svc.upcoming_events(db, actor.org_id, include_test=True, limit=20):
        start = e.get("start_time")
        if start is None or start - t > HORIZON:
            continue
        # Blocked always matters: a REQUIRED gate fails. Conditional matters only once setup
        # work is done ("conditional" state) — otherwise every fresh standard event would be
        # flagged for recommended gates nobody has started on yet.
        if e.get("verdict") == "blocked":
            state = "blocked"
        elif e.get("readiness_state") == "conditional":
            state = "conditional"
        else:
            continue
        ev = db.get(Event, uuid.UUID(e["id"]))
        if ev is None or not relevant(ev):
            continue
        out.append(_item("readiness", "blocker" if state == "blocked" else "warning",
                         f"{ev.title}: readiness {state}",
                         f"Starts {start:%a %d %b %H:%M} UTC · "
                         + (", ".join(e.get("failing") or [])[:160] or "gates not met"),
                         href=event_href(actor, ev), event=ev, ask="Is this event ready?"))

    failed = db.scalars(select(LiveRecording).where(
        LiveRecording.org_id == actor.org_id, LiveRecording.status == "failed",
        LiveRecording.created_at >= t - timedelta(hours=24))).all()
    for r in failed:
        ev = db.get(Event, r.event_id)
        if ev is None or not relevant(ev):
            continue
        out.append(_item("recording_failed", "warning", f"{ev.title}: a recording failed",
                         "A recording in the last 24 hours ended in a failed state.",
                         href=event_href(actor, ev), event=ev,
                         ask="What happened to the recording?"))

    expiring = db.scalars(select(EventAccessLink).where(
        EventAccessLink.org_id == actor.org_id, EventAccessLink.revoked_at.is_(None),
        EventAccessLink.expires_at > t, EventAccessLink.expires_at <= t + timedelta(hours=24)
    )).all()
    by_event: dict = {}
    for lk in expiring:
        by_event.setdefault(lk.event_id, []).append(lk)
    for event_id, links in by_event.items():
        ev = db.get(Event, event_id)
        if ev is None or ev.deleted_at is not None or not relevant(ev) or \
                ev.status in ("ended", "cancelled", "archived"):
            continue
        out.append(_item("links_expiring", "info", f"{ev.title}: access links expiring",
                         f"{len(links)} access link(s) expire within 24 hours.",
                         href=event_href(actor, ev), event=ev, ask="Who can access this event?"))

    if policy.is_org_admin(actor.user):
        waiting = db.scalars(select(SupportTicket).where(
            SupportTicket.org_id == actor.org_id,
            SupportTicket.status == "waiting_for_customer")).all()
        if waiting:
            out.append(_item("support_waiting", "info", "Support is waiting on you",
                             f"{len(waiting)} support case(s) need a reply from your "
                             "organization.", href="/organization/support",
                             ask="Show my open support cases"))

    out.sort(key=lambda i: SEVERITY_ORDER[i["severity"]])
    return out


def _relevant(db: Session, actor: Actor):
    """Admins see the whole organization; hosts see the events they run (§20: material to
    THIS user, not every event in the tenant)."""
    if policy.is_org_admin(actor.user):
        return lambda ev: ev.org_id == actor.org_id and ev.deleted_at is None
    return lambda ev: (ev.org_id == actor.org_id and ev.deleted_at is None
                       and policy.can_edit_event(db, actor, ev))


def summary(db: Session, actor: Actor) -> dict:
    found = items(db, actor)
    return {"count": len(found), "items": found[:10], "checked_at": iso(now())}
