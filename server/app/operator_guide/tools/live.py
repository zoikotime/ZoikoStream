"""Live operations tools (P2): program health and incident triage.

Telemetry is freshness-sensitive (§13, §19): every value carries its age, and anything older
than OPGUIDE_TELEMETRY_STALE_SECONDS is labelled stale rather than described as current
("The latest [data] is [age]. I will not describe it as current." — §15).

Triage distinguishes detected / suspected / confirmed / unknown (§6.10):
  confirmed  — an incident record exists (event incident or published status incident)
  detected   — a governed platform signal says so (session health, ingest signal state)
  suspected  — Guide's inference from indirect evidence (e.g. falling audience) — disclosed
  unknown    — no current data
Guide never declares a platform outage from one event's symptoms.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.commercial import EventIncident
from app.models.live import (
    AnalyticsSnapshot, BroadcastSession, ContributorSession, LiveIngressEndpoint, LiveRecording,
)
from app.models.status_page import PublicStatusIncident

from .. import errors
from ..config import get_settings
from ..policy import Actor
from ..sources import Source, age_text, aware, iso, now
from .base import (
    ToolContext, ToolResult, diagnostic_card, nav, notice, register, row, status_card,
    timeline_card, timeline_item,
)
from .events import event_href

HEALTH_STATE = {"ok": "stable", "warn": "degraded", "down": "down"}
SIGNAL_STATE = {"healthy": "stable", "intermittent": "degraded", "interrupted": "down"}


def _current_session(db: Session, ev) -> BroadcastSession | None:
    return db.scalar(select(BroadcastSession).where(
        BroadcastSession.event_id == ev.id, BroadcastSession.org_id == ev.org_id)
        .order_by(BroadcastSession.created_at.desc()).limit(1))


def _latest_snapshots(db: Session, ev, n: int = 8) -> list[AnalyticsSnapshot]:
    return list(db.scalars(select(AnalyticsSnapshot).where(
        AnalyticsSnapshot.event_id == ev.id, AnalyticsSnapshot.org_id == ev.org_id)
        .order_by(AnalyticsSnapshot.created_at.desc()).limit(n)).all())


def _elapsed(session: BroadcastSession | None) -> str | None:
    if not session or not session.started_at or session.status not in ("live", "paused"):
        return None
    secs = int((now() - aware(session.started_at)).total_seconds()) - (session.paused_ms or 0) // 1000
    secs = max(secs, 0)
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def gather(db: Session, ev) -> dict:
    """One consistent read of everything that describes the event's live state."""
    stale = get_settings().OPGUIDE_TELEMETRY_STALE_SECONDS
    session = _current_session(db, ev)
    inputs = list(db.scalars(select(LiveIngressEndpoint).where(
        LiveIngressEndpoint.event_id == ev.id, LiveIngressEndpoint.org_id == ev.org_id)).all())
    rec = db.scalar(select(LiveRecording).where(
        LiveRecording.event_id == ev.id, LiveRecording.org_id == ev.org_id)
        .order_by(LiveRecording.created_at.desc()).limit(1))
    snaps = _latest_snapshots(db, ev)
    contributors = list(db.scalars(select(ContributorSession).where(
        ContributorSession.event_id == ev.id, ContributorSession.org_id == ev.org_id)).all())
    return {"session": session, "inputs": inputs, "recording": rec, "snapshots": snaps,
            "contributors": contributors, "stale_after": stale}


@register(id="event.live", title="Live program health", owner="Broadcast service",
          risk="R1", capability="event_analysis", needs_event=True)
def event_live(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    ev = ctx.event
    g = gather(db, ev)
    session, snaps = g["session"], g["snapshots"]
    session_src = Source(id=f"session:{ev.id}", label="Broadcast session",
                         source_class="authoritative", owner="Broadcast service",
                         updated_at=(session.health_changed_at or session.started_at
                                     or session.created_at) if session else None)
    tele_src = Source(id=f"telemetry:{ev.id}", label="Audience telemetry (15 s sampler)",
                      source_class="operational", owner="Analytics sampler",
                      updated_at=snaps[0].created_at if snaps else None,
                      stale_after=g["stale_after"])
    sources = [session_src, tele_src]

    if session is None or session.status == "ended":
        msg = ("This event is not live right now."
               + (f" The last broadcast ended {age_text((now() - aware(session.ended_at)).total_seconds())}."
                  if session and session.ended_at else ""))
        return ToolResult("event.live", [status_card(f"Broadcast · {ev.title}", [
            row("Broadcast", "inactive", value=session.status.title() if session else "Not started")
        ], sources)], sources, summary=msg,
            actions=[nav("Open event", event_href(actor, ev))],
            facts={"live": False, "status": session.status if session else None})

    rows = [row("Program", HEALTH_STATE.get(session.health_level or "", "unknown"),
                value=(session.health_level or "unknown").upper(),
                detail=session.health_issues or None)]
    for inp in g["inputs"]:
        rows.append(row(f"Ingest · {inp.title or inp.input_type.upper()}",
                        SIGNAL_STATE.get(inp.signal_state, "unknown"),
                        value=f"{inp.state} · signal {inp.signal_state}",
                        detail=(f"{inp.interruption_count} interruption(s)"
                                if inp.interruption_count else None)))
    rec = g["recording"]
    if rec is not None:
        rows.append(row("Recording", {"recording": "active", "paused": "review",
                                      "failed": "down", "stopped": "inactive"}
                        .get(rec.status, "unknown"), value=rec.status.title(),
                        detail=rec.health_reason or rec.error or None))
    if snaps and not tele_src.stale:
        s = snaps[0]
        rows.append(row("Audience", "stable", value=f"{s.viewers} watching",
                        detail=f"{s.on_stage} on stage · updated {age_text(tele_src.age_seconds)}"))
    else:
        rows.append(row("Audience", "unknown", value="No current sample",
                        detail=(f"The latest audience sample is {age_text(tele_src.age_seconds)}. "
                                "I will not describe it as current.") if snaps else
                        errors.no_data("audience telemetry", "session")))
    elapsed = _elapsed(session)
    title = f"{session.status.upper()} {elapsed or ''} · {ev.title}".strip()
    issues = [r for r in rows if r["state"] in ("degraded", "down")]
    summary = ("No critical issue detected." if not issues else
               f"{len(issues)} signal(s) need attention: " + ", ".join(r["label"] for r in issues) + ".")
    return ToolResult("event.live", [status_card(title, rows, sources)], sources,
                      summary=summary, mode="live", inference=False,
                      actions=[nav("Open live console", f"/host/dashboard?event={ev.id}")],
                      facts={"live": True, "status": session.status, "health": session.health_level,
                             "issues": [r["label"] for r in issues],
                             "viewers": snaps[0].viewers if snaps and not tele_src.stale else None})


@register(id="event.incidents", title="Incident triage", owner="Incident service",
          risk="R1", capability="event_analysis", needs_event=True)
def event_incidents(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    ev = ctx.event
    g = gather(db, ev)
    session, snaps = g["session"], g["snapshots"]
    open_incidents = list(db.scalars(select(EventIncident).where(
        EventIncident.event_id == ev.id, EventIncident.closed_at.is_(None))
        .order_by(EventIncident.opened_at.desc())).all())
    public = list(db.scalars(select(PublicStatusIncident).where(
        PublicStatusIncident.published_at.isnot(None),
        PublicStatusIncident.status != "resolved")).all())

    inc_src = Source(id=f"incidents:{ev.id}", label="Event incident records",
                     source_class="authoritative", owner="Incident service", updated_at=now())
    tele_src = Source(id=f"telemetry:{ev.id}", label="Session health and ingest signal",
                      source_class="operational", owner="Broadcast service",
                      updated_at=(session.health_changed_at or session.started_at) if session else None,
                      stale_after=g["stale_after"] * 5)
    status_src = Source(id="status:public", label="Public status page",
                        source_class="authoritative", owner="Status service", updated_at=now())
    sources = [inc_src, tele_src, status_src]

    rows = []
    # Ingest — detected from the governed ingest signal.
    if g["inputs"]:
        worst = max(g["inputs"], key=lambda i: {"healthy": 0, "intermittent": 1,
                                                 "interrupted": 2}.get(i.signal_state, 0))
        rows.append(row("Primary ingest", "detected" if worst.signal_state != "healthy" else "stable",
                        value="Interrupted" if worst.signal_state == "interrupted" else
                        "Intermittent" if worst.signal_state == "intermittent" else "Stable"))
    else:
        rows.append(row("Primary ingest", "unknown", value="No ingest endpoint data"))
    # Program/delivery — detected from the session health monitor.
    if session and session.status in ("live", "paused"):
        lvl = session.health_level or "unknown"
        rows.append(row("Program health", "detected" if lvl in ("warn", "down") else
                        "stable" if lvl == "ok" else "unknown",
                        value={"ok": "Stable", "warn": "Degraded", "down": "Down"}.get(lvl, "Unknown"),
                        detail=session.health_issues or None))
    else:
        rows.append(row("Program health", "unknown", value="Not live"))
    # Audience — SUSPECTED only: a fall in viewers is evidence, not a diagnosis.
    suspected = None
    fresh = [s for s in snaps if (now() - aware(s.created_at)).total_seconds() < g["stale_after"] * 5]
    if len(fresh) >= 4:
        recent, earlier = fresh[0].viewers, max(s.viewers for s in fresh[2:])
        if earlier >= 10 and recent < earlier * 0.6:
            suspected = f"Viewers fell from {earlier} to {recent} in the last few minutes"
            rows.append(row("Audience impact", "suspected", value="Possible drop", detail=suspected))
        else:
            rows.append(row("Audience impact", "stable", value="No unusual change"))
    else:
        rows.append(row("Audience impact", "unknown", value="Not enough recent samples"))
    # Confirmed — only incident records confirm.
    rows.append(row("Event incident", "confirmed" if open_incidents else "stable",
                    value=f"{len(open_incidents)} open" if open_incidents else "None open"))
    rows.append(row("Platform incident", "confirmed" if public else "stable",
                    value=public[0].title if public else "None confirmed"))

    detected = [r for r in rows if r["state"] == "detected"]
    if open_incidents or public:
        assessment = "An incident is confirmed. Follow the incident record for status and updates."
    elif detected:
        assessment = ("Current evidence points to " + " and ".join(r["label"].lower() for r in detected)
                      + ". No platform incident is confirmed.")
    elif suspected:
        assessment = ("No platform signal is degraded. The audience change is a suspicion based on "
                      "telemetry, not a confirmed issue.")
    else:
        assessment = "No issue is detected in the signals available to Guide."
    blocks = [diagnostic_card(f"Triage · {ev.title}", assessment, rows, sources)]
    summary = assessment
    if open_incidents:
        i = open_incidents[0]
        blocks.append(status_card("Open incident", [
            row("Severity", "confirmed", value=(i.severity or "").upper()),
            row("State", "info", value=(i.operational_state or "open").replace("_", " ")),
            row("Customer summary", "info", value=i.customer_summary or "Not yet written"),
            row("Next update", "info", value=iso(i.next_update_at) or "Not scheduled"),
        ], [inc_src], subtitle=f"Incident {str(i.id)[:8]} · opened "
                               f"{age_text((now() - aware(i.opened_at)).total_seconds())}"
                               if i.opened_at else f"Incident {str(i.id)[:8]}"))
        summary = incident_summary(i, more=len(open_incidents) - 1)
    timeline = incident_timeline(session, open_incidents)
    if timeline:
        blocks.append(timeline_card(f"Timeline · {ev.title}", timeline, [tele_src, inc_src]))
    blocks.append(notice("info", "Guide labels each signal as confirmed, detected, suspected or "
                                 "unknown. Recovery actions stay in the live console."))
    return ToolResult("event.incidents", blocks, sources, summary=summary, mode="live"
                      if session and session.status in ("live", "paused") else "standard",
                      inference=bool(suspected),
                      actions=[nav("Open live console", f"/host/dashboard?event={ev.id}")],
                      facts={"confirmed_incidents": len(open_incidents) + len(public),
                             "detected": [r["label"] for r in detected],
                             "suspected": suspected,
                             "open_incident": {"severity": open_incidents[0].severity,
                                               "state": open_incidents[0].operational_state}
                             if open_incidents else None})


def incident_summary(i: EventIncident, *, more: int = 0) -> str:
    """The incident record in one deterministic sentence (§6.11: the conversation supports
    the record, it does not replace it). Only recorded fields — nothing is estimated."""
    state = (i.operational_state or "open").replace("_", " ")
    text = f"Incident {(i.severity or '').upper()} is open ({state})."
    if i.customer_summary:
        text += f" Customer summary: {i.customer_summary.rstrip('.')}."
    text += (f" Next update is due {iso(i.next_update_at)}." if i.next_update_at else
             " No next update has been scheduled.")
    if more:
        text += f" {more} more incident(s) are open."
    return text


def incident_timeline(session: BroadcastSession | None, incidents: list) -> list[dict]:
    """Authoritative timestamps only: each item is a recorded field, never a derived time."""
    items = []
    if session is not None:
        items.append(timeline_item(session.started_at, "Broadcast started", "active"))
        if session.health_changed_at and session.health_level:
            items.append(timeline_item(
                session.health_changed_at,
                f"Program health changed to {HEALTH_STATE.get(session.health_level, 'unknown')}",
                HEALTH_STATE.get(session.health_level, "unknown")))
        items.append(timeline_item(session.ended_at, "Broadcast ended", "inactive"))
    for i in incidents[:3]:
        sev = (i.severity or "").upper()
        items += [timeline_item(i.opened_at, f"Incident {sev} opened", "confirmed"),
                  timeline_item(i.delay_started_at, "Start delayed", "review"),
                  timeline_item(i.hold_started_at, "Event placed on hold", "review"),
                  timeline_item(i.resumed_at, "Event resumed", "stable"),
                  timeline_item(i.next_update_at, f"Next update due ({sev})", "info")]
    return [it for it in items if it["at"]]
