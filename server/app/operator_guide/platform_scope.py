"""Platform scope for Super Admins on the Command Center (ZST-UX-GUIDE-001 §9, §11.1).

Guide pins every read to the caller's OWN organization. For a Super Admin that organization is
the operator's own tenant and is usually empty, so "How did our events perform?" came back as
zeros while the Command Center beside it showed the platform's real numbers. On /admin pages a
Super Admin's questions are about the platform, so these tools read the SAME services the
Command Center reads (services/admin, services/ops): aggregate figures the caller is already
authorised to see on screen, labelled "Platform-wide". Per-tenant content (event details,
registrations, recordings, support subjects) stays organization-scoped, and cross-tenant
support work keeps going through the audited Support Access workflow, never through chat.

Links get the same treatment: the organization console routes (/organization/analytics, ...)
are org_admin-only, so a Super Admin who followed one was bounced back to the Command Center.
"""
from __future__ import annotations

import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.support_ticket import SupportTicket
from app.services import admin as admin_svc
from app.services import ops as ops_svc
from app.services import org as org_svc

from .policy import Actor
from .sources import Source, iso, now
from .tools.base import ToolContext, ToolResult, metric, metric_card, nav, notice, row, status_card

SUPER_ADMIN = "super_admin"
RANGES = {"24h": "last 24 hours", "7d": "last 7 days", "30d": "last 30 days", "90d": "last 90 days"}

# Organization console route -> the Super Admin page for the same subject.
_ROUTES = {
    "/organization/analytics": "/admin/analytics",
    "/organization/support": "/admin/support",
    "/organization/developers": "/admin/developers",
    "/organization/recordings": "/admin/media",
    "/organization/events": "/admin/live-events",
}


_HEALTH = {"at": 0.0, "value": None}
_HEALTH_TTL = 15.0


def _health(db: Session) -> dict:
    """One platform-health probe shared by the attention badge and the welcome tiles: a single
    Guide load asks for both, and the Command Center beside it polls the same probe."""
    if _HEALTH["value"] is None or time.monotonic() - _HEALTH["at"] > _HEALTH_TTL:
        _HEALTH.update(value=admin_svc.platform_health(db), at=time.monotonic())
    return _HEALTH["value"]


def in_scope(actor: Actor, route: str | None) -> bool:
    """A Super Admin asking from a platform console page."""
    return actor.role == SUPER_ADMIN and (route or "").startswith("/admin")


def console_href(actor: Actor, href):
    """The route this role can actually open. Only Super Admins are remapped."""
    if actor.role != SUPER_ADMIN or not isinstance(href, str):
        return href
    if href in _ROUTES:
        return _ROUTES[href]
    prefix = "/organization/events/"
    if href.startswith(prefix):
        return "/admin/live-events/" + href[len(prefix):]
    return href


def localize(node, actor: Actor):
    """Rewrite every href inside a composed reply (actions, attention items, escalation routes)."""
    if actor.role != SUPER_ADMIN:
        return node
    if isinstance(node, dict):
        if isinstance(node.get("href"), str):
            node["href"] = console_href(actor, node["href"])
        for value in node.values():
            localize(value, actor)
    elif isinstance(node, list):
        for value in node:
            localize(value, actor)
    return node


# ── Analytics ────────────────────────────────────────────────────────────────────────

def analytics(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    rng = ctx.range_key if ctx.range_key in RANGES else "30d"
    a = admin_svc.analytics(db, range_key=rng)
    totals = {k: v["value"] for k, v in a["totals"].items()}
    hours = a["streaming_hours"]
    src = Source(id=f"platform-analytics:{rng}", label="Platform analytics",
                 source_class="authoritative", owner="Analytics service", updated_at=now(),
                 detail={"range": rng})
    metrics = [
        metric("Streaming hours", f"{hours} h", definition="Broadcast hours inside the window; "
               "each session is clipped to the window, so one that began earlier counts only "
               "the part inside it."),
        metric("Completed broadcasts", str(totals["broadcasts_completed"]),
               definition="Broadcast sessions that ended inside the window."),
        metric("Events created", str(totals["events_created"]),
               definition="Events created inside the window, across all organizations."),
        metric("Recordings", str(totals["recordings"]),
               definition="Recordings created inside the window."),
        metric("New users", str(totals["new_users"]),
               definition="Accounts created inside the window."),
    ]
    blocks = [metric_card("Platform analytics", metrics, [src],
                          scope=f"Platform-wide · {RANGES[rng]}")]
    if a.get("note"):
        blocks.append(notice("info", a["note"]))
    summary = (f"Across the platform in the {RANGES[rng]}: {hours} streaming hours, "
               f"{totals['broadcasts_completed']} completed broadcasts, "
               f"{totals['events_created']} events created, {totals['recordings']} recordings "
               f"and {totals['new_users']} new users.")
    return ToolResult("org.analytics", blocks, [src], summary=summary,
                      actions=[nav("Open analytics", "/admin/analytics")],
                      facts={"scope": "platform", "range": rng, "streaming_hours": hours,
                             "broadcasts_completed": totals["broadcasts_completed"],
                             "events_created": totals["events_created"],
                             "recordings": totals["recordings"],
                             "new_users": totals["new_users"]})


# ── Upcoming events ──────────────────────────────────────────────────────────────────

def overview(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    upcoming = ops_svc.event_readiness(db, include_test=True, limit=8, high_impact_only=False)
    for e in upcoming:
        e["readiness_state"] = org_svc._readiness_state(e)
    state_map = {"passed": "ready", "conditional": "conditional", "blocked": "blocked",
                 "in_progress": "review", "not_started": "review"}
    from .tools.events import _fmt_when
    src = Source(id="platform:readiness", label="Upcoming-event readiness (platform)",
                 source_class="authoritative", owner="Readiness service", updated_at=now())
    rows = [row(e["title"], state_map.get(e.get("readiness_state"), "unknown"),
                detail=f"{_fmt_when(e['start_time'], e.get('timezone'))} · "
                       f"{(e.get('readiness_state') or 'unknown').replace('_', ' ')}")
            for e in upcoming]
    blocks = [status_card("Upcoming events", rows or [row("No upcoming events", "info")], [src],
                          subtitle=f"Platform-wide · {len(upcoming)} shown")]
    needs = [e for e in upcoming if e.get("readiness_state") in ("blocked", "conditional")]
    summary = (f"The platform has {len(upcoming)} upcoming event(s)"
               + (f"; {len(needs)} need readiness attention." if needs else
                  " and none has an open readiness problem." if upcoming else "."))
    return ToolResult("org.overview", blocks, [src], summary=summary,
                      actions=[nav("Open live events", "/admin/live-events")],
                      facts={"scope": "platform",
                             "upcoming": [{"title": e["title"], "start": iso(e["start_time"]),
                                           "readiness": e.get("readiness_state")}
                                          for e in upcoming]})


# ── Support ──────────────────────────────────────────────────────────────────────────

def support(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    """Counts only: ticket subjects belong to the tenants that filed them."""
    open_states = ("resolved", "closed")
    by_status = dict(db.execute(
        select(SupportTicket.status, func.count(SupportTicket.id))
        .where(SupportTicket.status.notin_(open_states)).group_by(SupportTicket.status)).all())
    orgs = db.scalar(select(func.count(func.distinct(SupportTicket.org_id))).where(
        SupportTicket.status.notin_(open_states))) or 0
    total = sum(by_status.values())
    src = Source(id="platform:support", label="Support cases (platform)",
                 source_class="authoritative", owner="Support service", updated_at=now())
    rows = [row(status.replace("_", " ").title(), "review", value=str(n))
            for status, n in sorted(by_status.items())] or [row("No open cases", "ready")]
    return ToolResult("org.support", [status_card("Open support cases", rows, [src],
                                                  subtitle="Platform-wide")], [src],
                      summary=f"{total} open support case(s) across {orgs} organization(s).",
                      actions=[nav("Open support", "/admin/support")],
                      facts={"scope": "platform", "open_cases": total, "organizations": orgs,
                             "by_status": by_status})


# ── Attention ────────────────────────────────────────────────────────────────────────

def attention_items(db: Session, item) -> list[dict]:
    """Platform health as the Command Center header states it. `item` is attention._item."""
    health = _health(db)
    out = []
    for s in health["services"]:
        if s["status"] in ("warn", "down"):
            out.append(item("platform_health_" + s["id"], "blocker" if s["status"] == "down" else "warning",
                            f"{s['name']}: {'down' if s['status'] == 'down' else 'degraded'}",
                            (s.get("note") or "The platform reports a health problem.")[:200],
                            href="/admin/status", ask="Is ZoikoStream down?"))
    limits = []
    if health["not_configured"]:
        limits.append(f"{health['not_configured']} not integrated")
    if health["unmonitored"]:
        limits.append(f"{health['unmonitored']} not monitored")
    if limits:
        title = ("Platform operational with limitations" if health["overall"] == "ok"
                 else "Platform health has limitations")
        out.append(item("platform_limits", "info", title, " · ".join(limits),
                        href="/admin/status", ask="Is ZoikoStream down?"))
    return out


# ── Welcome screen ───────────────────────────────────────────────────────────────────

def glance(db: Session) -> list[dict]:
    """Three at-a-glance tiles for the Super Admin welcome screen. Counts only, from the same
    services the Command Center header reads; each tile carries the question that explains it."""
    from app.models.live import BroadcastSession
    health = _health(db)
    limits = [f"{health[k]} {label}" for k, label in
              (("not_configured", "not integrated"), ("unmonitored", "not monitored"))
              if health[k]]
    state = {"ok": "ready", "warn": "review", "down": "blocked"}[health["overall"]]
    label = {"ok": "Operational", "warn": "Degraded", "down": "Down"}[health["overall"]]
    if health["overall"] == "ok" and limits:
        label = "Operational with limitations"
    live = db.scalar(select(func.count(BroadcastSession.id)).where(
        BroadcastSession.status.in_(("live", "paused")))) or 0
    open_cases = db.scalar(select(func.count(SupportTicket.id)).where(
        SupportTicket.status.notin_(("resolved", "closed")))) or 0
    return [
        {"id": "health", "label": "Platform health", "value": label, "state": state,
         "detail": " · ".join(limits) or "All probed services healthy",
         "ask": "Is ZoikoStream down?"},
        {"id": "live", "label": "Live now", "value": str(live),
         "state": "active" if live else "inactive",
         "detail": "broadcast session(s) on air" if live else "No broadcast on air",
         "ask": "Show upcoming events"},
        {"id": "support", "label": "Open support cases", "value": str(open_cases),
         "state": "review" if open_cases else "ready",
         "detail": "across all organizations", "ask": "Show open support cases"},
    ]
