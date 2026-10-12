"""Event intelligence tools (P1): overview, configuration, readiness, audience access.

Readiness is computed by the platform's readiness service functions (services/ops gate
results + commercial readiness), never by Guide: "The readiness service, not the model, owns
the final state" (§6.2) and "Guide may explain blockers. It cannot convert Conditional to
Ready" (§6.5). Endpoints with side effects (the commercial readiness GET notifies; the
planning GET writes) are bypassed in favour of the pure functions underneath them.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audience import JoinDenial
from app.models.event import EventAccessLink, EventRegistration
from app.models.event_planning import EventIntake, EventPlanningRequirement, EventRehearsal
from app.models.live import BroadcastSession
from app.models.platform_ops import GovernanceRecord
from app.services import ops as ops_svc
from app.services import org as org_svc

from ..policy import Actor, is_org_admin
from ..sources import Source, iso, now
from .base import (
    ToolContext, ToolResult, nav, notice, readiness_card, register, row, status_card,
)

ON_AIR = ("live", "degraded")
VERDICT_STATE = {"passed": ("ready", "READY"), "conditional": ("conditional", "CONDITIONAL"),
                 "blocked": ("blocked", "BLOCKED")}
VISIBILITY_TEXT = {
    "public": "Public — anyone with the event page can register and watch.",
    "unlisted": "Unlisted — not listed anywhere; people with the link can register and watch.",
    "private": "Private — only invited people, holders of an access link, or members of your "
               "organization can watch.",
}


def event_href(actor: Actor, event) -> str:
    """The authoritative surface for this event for this role (never an execution link)."""
    if is_org_admin(actor.user):
        return f"/organization/events/{event.id}"
    return f"/host/dashboard?event={event.id}"


def _event_source(event) -> Source:
    return Source(id=f"event:{event.id}", label="Event configuration",
                  source_class="authoritative", owner="Events service",
                  updated_at=event.updated_at or event.created_at)


def _fmt_when(dt, tz: str | None) -> str:
    if dt is None:
        return "No start time scheduled"
    from zoneinfo import ZoneInfo
    try:
        local = dt.astimezone(ZoneInfo(tz)) if tz else dt
    except Exception:
        local = dt
    return local.strftime("%a %d %b %Y, %H:%M ") + (tz or "UTC")


# ── Organization overview ────────────────────────────────────────────────────────────

@register(id="org.overview", title="Upcoming events and readiness", owner="Organization service",
          risk="R0", capability="event_analysis")
def org_overview(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    upcoming = org_svc.upcoming_events(db, actor.org_id, include_test=True, limit=8)
    live = db.scalar(select(func.count(BroadcastSession.id)).where(
        BroadcastSession.org_id == actor.org_id,
        BroadcastSession.status.in_(("live", "paused")))) or 0
    src = Source(id="org:readiness", label="Upcoming-event readiness",
                 source_class="authoritative", owner="Readiness service", updated_at=now())
    state_map = {"passed": "ready", "conditional": "conditional", "blocked": "blocked",
                 "in_progress": "review", "not_started": "review"}
    rows = [row(e["title"], state_map.get(e.get("readiness_state"), "unknown"),
                detail=f"{_fmt_when(e['start_time'], e.get('timezone'))} · "
                       f"{(e.get('readiness_state') or 'unknown').replace('_', ' ')}")
            for e in upcoming]
    blocks = [status_card("Upcoming events", rows or [row("No upcoming events", "info")], [src],
                          subtitle=f"{live} live session(s) right now")]
    needs = [e for e in upcoming if e.get("readiness_state") in ("blocked", "conditional")]
    if needs:
        blocks.append(notice("warning", f"{len(needs)} upcoming event(s) need readiness "
                                        "attention: " + ", ".join(e["title"] for e in needs[:3]),
                             rule="Readiness service"))
    summary = (f"You have {len(upcoming)} upcoming event(s)"
               + (f"; {len(needs)} need readiness attention." if needs else
                  " and none has an open readiness problem." if upcoming else "."))
    return ToolResult("org.overview", blocks, [src], summary=summary, facts={
        "upcoming": [{"title": e["title"], "start": iso(e["start_time"]),
                      "readiness": e.get("readiness_state"), "failing": e.get("failing", [])}
                     for e in upcoming],
        "live_sessions": live})


# ── Event configuration ──────────────────────────────────────────────────────────────

@register(id="event.summary", title="Event configuration", owner="Events service",
          risk="R0", capability="event_analysis", needs_event=True)
def event_summary(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    ev = ctx.event
    src = _event_source(ev)
    hosts = ops_svc._event_hosts(db, [ev.id])
    on = lambda flag: "active" if flag else "inactive"  # noqa: E731
    rows = [
        row("Status", "info", value=ev.status.replace("_", " ").title()),
        row("Starts", "info", value=_fmt_when(ev.start_time, ev.timezone)),
        row("Impact", "info", value=(ev.impact or "standard").title()),
        row("Host", "ready" if hosts.get(ev.id) else "review",
            value=hosts.get(ev.id) or "No host assigned"),
        row("Audience access", "info", value=(ev.visibility or "public").title()),
        row("Registration", on(ev.registration_required),
            value="Required" + (f" (limit {ev.registration_limit})" if ev.registration_limit
                                else "") if ev.registration_required else "Not required"),
        row("Recording", on(ev.recording_enabled),
            value="Enabled" if ev.recording_enabled else "Disabled"),
        row("Chat", on(ev.chat_enabled), value="On" if ev.chat_enabled else "Off"),
        row("Q&A", on(ev.qa_enabled), value="On" if ev.qa_enabled else "Off"),
        row("Polls", on(ev.polls_enabled), value="On" if ev.polls_enabled else "Off"),
        row("Waiting room", on(ev.waiting_room_enabled),
            value="On" if ev.waiting_room_enabled else "Off"),
    ]
    return ToolResult("event.summary", [status_card(ev.title or "Untitled event", rows, [src])],
                      [src], summary=f"Here is the current configuration of {ev.title}.",
                      actions=[nav("Open event", event_href(actor, ev))],
                      mode="live" if ev.status in ON_AIR else "standard",
                      facts={"title": ev.title, "status": ev.status,
                             "start": iso(ev.start_time), "visibility": ev.visibility,
                             "host": hosts.get(ev.id), "recording": ev.recording_enabled,
                             "registration_required": ev.registration_required})


# ── Readiness ────────────────────────────────────────────────────────────────────────

def compute_readiness(db: Session, ev) -> dict:
    """The readiness service's own verdict for one event (same gates as the Super Admin
    readiness page and the org overview)."""
    hosts = ops_svc._event_hosts(db, [ev.id])
    single_path = set(db.scalars(select(GovernanceRecord.event_id).where(
        GovernanceRecord.kind == "single_path_override",
        GovernanceRecord.resolved_at.is_(None), GovernanceRecord.event_id == ev.id)).all())
    gates = ops_svc._gate_results(ev, hosts, single_path)
    commercial = ops_svc._commercial_gate(db, ev)
    if commercial is not None:
        gates = gates + [commercial]
    return {"verdict": ops_svc._verdict(gates), "gates": gates}


@register(id="event.readiness", title="Event readiness", owner="Readiness service",
          risk="R0", capability="event_analysis", needs_event=True)
def event_readiness(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    ev = ctx.event
    result = compute_readiness(db, ev)
    verdict = result["verdict"]
    state, label = VERDICT_STATE[verdict]
    ready_src = Source(id=f"readiness:{ev.id}", label="Readiness gates",
                       source_class="authoritative", owner="Readiness service", updated_at=now())
    rows = [row(g["label"].split(":")[0], "ready" if g["passed"] else
                ("blocked" if g["required"] else "review"),
                detail=None if g["passed"] else (
                    (g["label"].split(": ", 1)[1] if ": " in g["label"] else "Not met")
                    + (" · required for this event" if g["required"] else " · recommended")))
            for g in result["gates"]]

    planning = _planning_rows(db, ev)
    sources = [ready_src, _event_source(ev)]
    blocks = [readiness_card(f"Readiness · {ev.title}", state, label, rows, sources,
                             note="Guide explains readiness; it cannot change it. Only the "
                                  "readiness service moves an event to Ready.")]
    if planning["rows"]:
        plan_src = Source(id=f"planning:{ev.id}", label="Event planning requirements",
                          source_class="authoritative", owner="Event planning service",
                          updated_at=planning["updated_at"])
        sources.append(plan_src)
        blocks.append(status_card("Planning", planning["rows"], [plan_src]))
    failing = [g["label"] for g in result["gates"] if not g["passed"]]
    if ev.status in ON_AIR:
        direct = "This event is already on air; readiness gates applied before going live."
    elif verdict == "passed":
        direct = "Yes — every readiness gate for this event currently passes."
    else:
        direct = ("Not yet. " + (f"{len(failing)} gate(s) are not met: "
                                 + "; ".join(failing) + "." if failing else ""))
    actions = [nav("Review event setup", event_href(actor, ev))]
    return ToolResult("event.readiness", blocks, sources, summary=direct, actions=actions,
                      mode="live" if ev.status in ON_AIR else "standard",
                      facts={"event": ev.title, "verdict": verdict, "failing_gates": failing,
                             "planning_outstanding": planning["outstanding"]})


def _planning_rows(db: Session, ev) -> dict:
    reqs = db.scalars(select(EventPlanningRequirement).where(
        EventPlanningRequirement.event_id == ev.id,
        EventPlanningRequirement.org_id == ev.org_id)).all()
    state = {"complete": "ready", "not_applicable": "ready", "in_progress": "review",
             "incomplete": "review", "blocked": "blocked"}
    rows, outstanding, updated = [], [], None
    for r in sorted(reqs, key=lambda r: r.category):
        rows.append(row(r.category.replace("_", " ").title(), state.get(r.status, "unknown"),
                        detail=None if r.status in ("complete", "not_applicable") else
                        ("Blocking · " if r.blocking else "") + r.status.replace("_", " ")))
        if r.status not in ("complete", "not_applicable"):
            outstanding.append(r.category)
        updated = max(filter(None, [updated, r.completed_at, r.created_at]), default=updated)
    return {"rows": rows, "outstanding": outstanding, "updated_at": updated}


# ── Audience access ──────────────────────────────────────────────────────────────────

@register(id="event.access", title="Audience access", owner="Access policy service",
          risk="R0", capability="event_analysis", needs_event=True)
def event_access(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    ev = ctx.event
    t = now()
    regs = db.scalar(select(func.count(EventRegistration.id)).where(
        EventRegistration.event_id == ev.id)) or 0
    links = db.scalars(select(EventAccessLink).where(
        EventAccessLink.event_id == ev.id, EventAccessLink.org_id == actor.org_id)).all()
    active = [lk for lk in links if lk.revoked_at is None
              and (lk.expires_at is None or lk.expires_at > t)]
    expiring = [lk for lk in active if lk.expires_at and lk.expires_at - t < timedelta(hours=24)]
    denials = db.execute(select(JoinDenial.reason, func.count()).where(
        JoinDenial.event_id == ev.id, JoinDenial.org_id == actor.org_id,
        JoinDenial.created_at >= t - timedelta(days=7)).group_by(JoinDenial.reason)).all()

    src = _event_source(ev)
    link_src = Source(id=f"access:{ev.id}", label="Access links and registrations",
                      source_class="authoritative", owner="Access policy service", updated_at=t)
    at_capacity = bool(ev.registration_limit and regs >= ev.registration_limit)
    rows = [
        row("Access profile", "info", value=(ev.visibility or "public").title(),
            detail=VISIBILITY_TEXT.get(ev.visibility)),
        row("Registration", "active" if ev.registration_required else "inactive",
            value="Required" if ev.registration_required else "Not required"),
        row("Registrations", "review" if at_capacity else "info",
            value=f"{regs}" + (f" of {ev.registration_limit}" if ev.registration_limit else ""),
            detail="At capacity — new registrations are refused" if at_capacity else None),
        row("Active access links", "active" if active else "inactive", value=str(len(active)),
            detail=(f"{len(expiring)} expire within 24 hours" if expiring else None)),
        row("Revoked or expired links", "info", value=str(len(links) - len(active))),
    ]
    blocks = [status_card(f"Audience access · {ev.title}", rows, [src, link_src])]
    if denials:
        blocks.append(status_card("Refused joins (last 7 days)", [
            row(reason.replace("_", " ").capitalize(), "review", value=str(n))
            for reason, n in sorted(denials, key=lambda d: -d[1])], [link_src]))
    if expiring:
        blocks.append(notice("warning", f"{len(expiring)} access link(s) expire within 24 hours.",
                             rule="Access policy service"))
    blocks.append(notice("info", "Access link secrets are never shown in Guide. Manage links "
                                 "in the event's access settings."))
    return ToolResult("event.access", blocks, [src, link_src],
                      summary=f"{ev.title} is {ev.visibility or 'public'}"
                              + (" with registration required." if ev.registration_required
                                 else " without registration."),
                      actions=[nav("Review access", event_href(actor, ev))],
                      facts={"visibility": ev.visibility, "registrations": regs,
                             "registration_limit": ev.registration_limit,
                             "active_links": len(active), "expiring_links": len(expiring),
                             "denials_7d": {r: n for r, n in denials}})


# ── Planning / run-of-show context ───────────────────────────────────────────────────

@register(id="event.planning", title="Planning and rehearsals", owner="Event planning service",
          risk="R0", capability="event_analysis", needs_event=True)
def event_planning(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    ev = ctx.event
    intake = db.scalar(select(EventIntake).where(EventIntake.event_id == ev.id,
                                                 EventIntake.org_id == ev.org_id))
    planning = _planning_rows(db, ev)
    rehearsals = db.scalars(select(EventRehearsal).where(
        EventRehearsal.event_id == ev.id, EventRehearsal.org_id == ev.org_id)
        .order_by(EventRehearsal.scheduled_at)).all()
    src = Source(id=f"planning:{ev.id}", label="Event planning",
                 source_class="authoritative", owner="Event planning service",
                 updated_at=planning["updated_at"] or (intake.opened_at if intake else None))
    blocks = []
    if intake:
        blocks.append(status_card("Event intake", [
            row("Intake", "ready" if intake.status == "completed" else "review",
                value=intake.status.replace("_", " ").title(),
                detail=("Outstanding: " + ", ".join(intake.outstanding_sections or []))
                if intake.outstanding_sections else None)], [src]))
    if planning["rows"]:
        blocks.append(status_card("Requirements", planning["rows"], [src]))
    if rehearsals:
        rstate = {"completed": "ready", "scheduled": "info", "needs_repeat": "review",
                  "canceled": "inactive"}
        blocks.append(status_card("Rehearsals", [
            row(_fmt_when(r.scheduled_at, r.timezone_name), rstate.get(r.status, "unknown"),
                value=r.status.replace("_", " ").title(),
                detail=("Repeat required" if r.repeat_required else None))
            for r in rehearsals], [src]))
    blocks.append(notice("info", "ZoikoStream has no cue-by-cue run-of-show yet. Guide shows the "
                                 "intake, planning requirements and rehearsals that exist."))
    outstanding = planning["outstanding"] + (intake.outstanding_sections or [] if intake else [])
    summary = ("Planning is complete." if not outstanding and (intake or planning["rows"])
               else f"{len(outstanding)} planning item(s) are outstanding."
               if outstanding else "No planning has been started for this event.")
    return ToolResult("event.planning", blocks, [src], summary=summary,
                      actions=[nav("Open event planning", event_href(actor, ev))],
                      facts={"intake": intake.status if intake else None,
                             "outstanding": outstanding,
                             "rehearsals": [{"at": iso(r.scheduled_at), "status": r.status}
                                            for r in rehearsals]})

