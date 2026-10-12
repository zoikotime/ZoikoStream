"""Analytics Guide (§6.13): "The assistant defines the metric before interpreting it."

Values come from the platform's own analytics services (services/org, services/audience) for
the caller's organization only. Each metric carries its definition and measurement quality,
and estimated values say so. Vague outcomes ("good experience") are not metrics: Guide shows
the measurable components instead of inventing a score.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import audience as audience_svc
from app.services import org as org_svc

from ..policy import Actor
from ..sources import Source, now
from .base import ToolContext, ToolResult, metric, metric_card, nav, notice, register

RANGES = {"7d": "last 7 days", "30d": "last 30 days", "90d": "last 90 days", "12m": "last 12 months"}

DEFINITIONS = {
    "peak_sum": "The sum of each event's peak concurrent viewers in the period. It is not a "
                "count of unique viewers.",
    "peak": "The highest number of people watching one event at the same moment.",
    "watch_hours": "Total time viewers spent watching, measured by the 15-second sampler. "
                   "Events without a measurement are excluded, not counted as zero.",
    "engagement": "Average engagement score across events that had an audience.",
    "attendees": "People who joined at least one event, de-duplicated per event. Estimated.",
    "avg_watch": "Average minutes watched per attendee. Estimated from sampler data.",
    "show_rate": "Share of invited people who attended. Calculated for private, invite-only "
                 "events only.",
    "sessions": "Viewing sessions admitted to the live stream.",
    "rejoin": "Share of viewers who left and joined again.",
    "event_viewers": "Identified viewers of this event (one per viewer per event).",
    "attendance": "Registered people who were admitted to the live stream.",
    "blocked": "Join attempts the platform refused (full, not invited, invalid link, ...).",
}


def _fmt(v, unit: str | None = None):
    if v is None:
        return None
    if isinstance(v, float):
        v = round(v, 1)
    return f"{v}{unit or ''}"


@register(id="org.analytics", title="Audience analytics", owner="Analytics service",
          risk="R1", capability="analytics")
def org_analytics(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    rng = ctx.range_key if ctx.range_key in RANGES else "30d"
    a = org_svc.analytics(db, actor.org, rng)
    att = org_svc.audience_attendance(db, actor.org, rng)
    s, sess = a.get("summary", {}), a.get("session_summary") or {}
    src = Source(id=f"analytics:{rng}", label="Organization analytics",
                 source_class="authoritative", owner="Analytics service", updated_at=now(),
                 detail={"range": rng})
    metrics = [
        metric("Highest peak audience", _fmt(s.get("peak")), definition=DEFINITIONS["peak"]),
        metric("Peak viewers (summed)", _fmt(s.get("peak_viewers_summed")),
               definition=DEFINITIONS["peak_sum"]),
        metric("Watch time", _fmt(s.get("watch_hours"), " h"), definition=DEFINITIONS["watch_hours"],
               quality="measured" if s.get("watch_hours") is not None else "unmeasured"),
        metric("Engagement", _fmt(s.get("engagement"), "%"), definition=DEFINITIONS["engagement"],
               quality="measured" if s.get("engagement") is not None else "unmeasured"),
        metric("Unique attendees", _fmt(att.get("unique_attendees")),
               definition=DEFINITIONS["attendees"], quality="estimated"),
        metric("Average watch", _fmt(att.get("avg_watch_minutes"), " min"),
               definition=DEFINITIONS["avg_watch"], quality="estimated"),
    ]
    if att.get("show_rate") is not None:
        metrics.append(metric("Show rate", _fmt(att.get("show_rate"), "%"),
                              definition=DEFINITIONS["show_rate"]))
    if sess.get("measured"):
        metrics.append(metric("Sessions admitted", _fmt(sess.get("sessions_admitted")),
                              definition=DEFINITIONS["sessions"]))
        if sess.get("rejoin_rate") is not None:
            metrics.append(metric("Rejoin rate", _fmt(sess["rejoin_rate"] * 100, "%"),
                                  definition=DEFINITIONS["rejoin"]))
    blocks = [metric_card("Audience analytics", metrics, [src],
                          scope=f"Your organization · {RANGES[rng]}")]
    sources = [src]
    if ctx.event is not None:
        ins = audience_svc.insights(db, actor.org, rng, event_id=ctx.event.id)
        ev_src = Source(id=f"insights:{ctx.event.id}", label="Event audience insights",
                        source_class="authoritative", owner="Audience service", updated_at=now())
        ra, blocked = ins.get("registration_attendance") or {}, ins.get("blocked_join_attempts") or {}
        ev_metrics = [metric("Viewers", _fmt(ins.get("viewers")), definition=DEFINITIONS["event_viewers"])]
        if ra.get("applicable"):
            ev_metrics.append(metric("Registered → attended",
                                     f"{ra.get('attended')} of {ra.get('registered')}"
                                     + (f" ({_fmt(ra.get('rate'), '%')})" if ra.get("rate") is not None else ""),
                                     definition=DEFINITIONS["attendance"]))
        ev_metrics.append(metric("Refused joins", _fmt(blocked.get("total", 0)),
                                 definition=DEFINITIONS["blocked"]))
        blocks.append(metric_card(f"Audience · {ctx.event.title}", ev_metrics, [ev_src],
                                  scope=f"This event · {RANGES[rng]}"))
        sources.append(ev_src)
    note = a.get("breakdowns_note")
    if note:
        blocks.append(notice("info", note))
    peak = s.get("peak")
    summary = (f"In the {RANGES[rng]}, your highest peak audience was {peak} concurrent viewers."
               if peak else f"No measured audience in the {RANGES[rng]}.")
    return ToolResult("org.analytics", blocks, sources, summary=summary, inference=False,
                      actions=[nav("Open analytics", "/organization/analytics")],
                      facts={"range": rng, "peak": peak, "peak_summed": s.get("peak_viewers_summed"),
                             "watch_hours": s.get("watch_hours"), "engagement": s.get("engagement"),
                             "unique_attendees_estimated": att.get("unique_attendees"),
                             "avg_watch_minutes_estimated": att.get("avg_watch_minutes")})
