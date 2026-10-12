"""Recording & replay tool (§6.14): "Recording existence never equals replay authorization."

Reads recording and replay-entitlement rows directly (tenant-pinned), never the recording list
endpoints — those sign storage URLs and probe storage, and a signed URL is a credential.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.commercial import ReplayEntitlement
from app.models.live import LiveRecording

from ..policy import Actor
from ..sources import Source, iso
from .base import ToolContext, ToolResult, nav, notice, register, row, status_card
from .events import event_href

REC_STATE = {"recording": "active", "paused": "review", "stopped": "ready", "failed": "down",
             "idle": "inactive"}
VALIDATION_STATE = {"valid": "ready", "captured": "review", "validating": "review",
                    "degraded": "degraded", "failed": "down"}
PUBLISH_STATE = {"published": "ready", "ready_for_review": "review", "validating": "review",
                 "not_available": "inactive", "withheld": "blocked", "expired": "inactive",
                 "deleted_preserved": "inactive"}


def _size(n: int | None) -> str | None:
    if not n:
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


@register(id="event.replay", title="Recording and replay", owner="Media service",
          risk="R0", capability="event_analysis", needs_event=True)
def event_replay(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    ev = ctx.event
    recs = list(db.scalars(select(LiveRecording).where(
        LiveRecording.event_id == ev.id, LiveRecording.org_id == ev.org_id,
        LiveRecording.deleted_at.is_(None)).order_by(LiveRecording.created_at.desc())).all())
    ent = db.scalar(select(ReplayEntitlement).where(ReplayEntitlement.event_id == ev.id)
                    .order_by(ReplayEntitlement.created_at.desc()).limit(1))
    rec_src = Source(id=f"recordings:{ev.id}", label="Recordings", source_class="authoritative",
                     owner="Media service",
                     updated_at=max((r.stopped_at or r.started_at or r.created_at for r in recs),
                                    default=None))
    rep_src = Source(id=f"replay:{ev.id}", label="Replay entitlement",
                     source_class="authoritative", owner="Replay service",
                     updated_at=(ent.state_changed_at or ent.created_at) if ent else None)
    sources = [rec_src, rep_src]

    rows = []
    for r in recs[:4]:
        rows.append(row(f"Recording · {r.role or 'primary'}", REC_STATE.get(r.status, "unknown"),
                        value=r.status.title(),
                        detail=" · ".join(filter(None, [
                            f"integrity {r.validation_status}" if r.validation_status else None,
                            _size(r.size_bytes),
                            f"retained until {iso(r.retention_expires_at)[:10]}"
                            if r.retention_expires_at else None,
                            "legal hold" if r.legal_hold else None,
                            r.error]))))
    if not rows:
        rows.append(row("Recording", "inactive", value="None captured",
                        detail="Recording is enabled for this event." if ev.recording_enabled
                        else "Recording is turned off for this event."))
    publish = ent.publish_state if ent else "not_available"
    rows.append(row("Replay publication", PUBLISH_STATE.get(publish, "unknown"),
                    value=publish.replace("_", " ").capitalize(),
                    detail=(f"Withdrawn: {ent.withdraw_reason}" if ent and ent.withdrawn_at else
                            f"Expires {iso(ent.expires_at)[:10]}" if ent and ent.expires_at else None)))
    if ent:
        rows.append(row("Downloads", "active" if ent.download_permission else "inactive",
                        value="Allowed" if ent.download_permission else "Not allowed"))
    blocks = [status_card(f"Replay · {ev.title}", rows, sources)]

    best = recs[0] if recs else None
    if publish == "published":
        summary = "The replay is published to its audience."
    elif best and best.validation_status in ("valid",) and publish in ("ready_for_review", "validating"):
        summary = ("The recording is captured and valid, but the replay is not published yet — "
                   f"its publication state is {publish.replace('_', ' ')}.")
    elif best:
        summary = (f"A recording exists ({best.status}), but that does not authorize a replay. "
                   f"Replay publication is {publish.replace('_', ' ')}.")
    else:
        summary = "No recording exists for this event yet."
    blocks.append(notice("info", "A recording existing never means the replay is authorized. "
                                 "Publication is decided by the replay workflow."))
    return ToolResult("event.replay", blocks, sources, summary=summary,
                      actions=[nav("Open recordings", "/organization/recordings")
                               if actor.role != "host" else nav("Open event", event_href(actor, ev))],
                      facts={"recordings": [{"status": r.status, "validation": r.validation_status,
                                             "legal_hold": r.legal_hold} for r in recs[:4]],
                             "replay_publish_state": publish})
