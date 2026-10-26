"""Organization-level tools: developer platform, support cases, public service status.

Allowlisted output only: API keys are counted (never shown, never even their prefix), webhook
secrets/verification tokens are never read, and support-case staff notes are excluded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.status_page import PublicStatusIncident, ScheduledMaintenance
from app.models.support_ticket import SupportTicket
from app.models.webhook import WebhookEndpoint

from .. import errors, knowledge
from ..policy import Actor, is_org_admin
from ..sources import Source, iso, now
from .base import ToolContext, ToolResult, nav, notice, register, row, status_card


def _key_state(rec: dict, at: datetime) -> str:
    if rec.get("revoked"):
        return "revoked"
    exp = rec.get("expires_at")
    if exp:
        try:
            e = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if (e if e.tzinfo else e.replace(tzinfo=timezone.utc)) <= at:
                return "expired"
        except ValueError:
            pass
    return "active"


def technical_context(actor: Actor) -> dict:
    """API version + environment, attached to every technical answer (§6.15)."""
    env = "Test" if getattr(actor.org, "is_test", False) else "Production"
    api = knowledge.api_version()
    return {"api": api, "environment": env,
            "label": f"Environment: {env}" + (f" · API: {api}" if api else "")}


@register(id="org.developer", title="Developer platform", owner="Developer platform",
          risk="R0", capability="docs_qa")
def org_developer(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    src = Source(id="developer", label="API credentials and webhooks",
                 source_class="authoritative", owner="Developer platform", updated_at=now())
    if not is_org_admin(actor.user):
        # The platform restricts the developer console to organization admins.
        return ToolResult("org.developer", [notice("info", errors.permission_denied(
                          "API credentials and webhooks") + " They are managed by your "
                          "organization admin. Guide can still explain how the API works.",
                          rule="Platform permissions")], [],
                          summary=errors.permission_denied("API credentials and webhooks"),
                          facts={"restricted": True})
    t = now()
    keys = [_key_state(k, t) for k in (actor.org.api_keys or [])]
    hooks = list(db.scalars(select(WebhookEndpoint).where(
        WebhookEndpoint.org_id == actor.org_id)).all())
    rows = [row("API keys", "active" if "active" in keys else "inactive",
                value=f"{keys.count('active')} active",
                detail=f"{keys.count('expired')} expired · {keys.count('revoked')} revoked"
                if len(keys) > keys.count("active") else None)]
    hstate = {"healthy": "stable", "degraded": "degraded", "disabled": "down"}
    for h in hooks[:6]:
        rows.append(row(f"Webhook · {h.label or urlsplit(h.url).hostname or 'endpoint'}",
                        "inactive" if not h.enabled else hstate.get(h.health or "healthy", "unknown"),
                        value=("Disabled" if not h.enabled else (h.status or "active").title()),
                        detail=" · ".join(filter(None, [
                            urlsplit(h.url).hostname,
                            "verified" if h.verified_at else "not verified",
                            f"{h.consecutive_failures} consecutive failures"
                            if h.consecutive_failures else None]))))
    if not hooks:
        rows.append(row("Webhooks", "inactive", value="None configured"))
    tech = technical_context(actor)
    return ToolResult("org.developer", [status_card("Developer platform", rows, [src],
                                                    subtitle=tech["label"]),
                                        notice("info", "Keys and signing secrets are never shown "
                                                       "in Guide.")],
                      [src], summary=f"{keys.count('active')} active API key(s) and "
                                     f"{len(hooks)} webhook endpoint(s).",
                      actions=[nav("Open developer platform", "/organization/developers")],
                      facts={"api": tech["api"], "environment": tech["environment"],
                             "active_keys": keys.count("active"), "webhooks": len(hooks),
                             "failing_webhooks": sum(1 for h in hooks if h.consecutive_failures)})


@register(id="org.support", title="Support cases", owner="Support service",
          risk="R0", capability="event_analysis")
def org_support(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    cases = list(db.scalars(select(SupportTicket).where(
        SupportTicket.org_id == actor.org_id,
        SupportTicket.status.notin_(("resolved", "closed")))
        .order_by(SupportTicket.updated_at.desc()).limit(8)).all())
    src = Source(id="support", label="Support cases", source_class="authoritative",
                 owner="Support service", updated_at=max((c.updated_at for c in cases), default=now()))
    state = {"open": "review", "in_progress": "active", "waiting_for_customer": "review",
             "reopened": "review"}
    rows = [row(f"{c.case_reference or 'Case'} · {c.subject}", state.get(c.status, "info"),
                value=c.status.replace("_", " ").title(),
                detail=" · ".join(filter(None, [f"priority {c.priority}" if c.priority else None,
                                                f"next update {iso(c.next_update_at)}"
                                                if c.next_update_at else None,
                                                c.customer_update])))
            for c in cases]
    return ToolResult("org.support", [status_card("Open support cases",
                                                  rows or [row("No open cases", "ready")], [src])],
                      [src], summary=f"{len(cases)} open support case(s).",
                      actions=[nav("Open support", "/organization/support")],
                      facts={"open_cases": len(cases),
                             "waiting_on_you": sum(1 for c in cases if c.status == "waiting_for_customer")})


@register(id="platform.status", title="Service status", owner="Status service",
          risk="R0", capability="docs_qa")
def platform_status(db: Session, actor: Actor, ctx: ToolContext) -> ToolResult:
    incidents = list(db.scalars(select(PublicStatusIncident).where(
        PublicStatusIncident.published_at.isnot(None),
        PublicStatusIncident.status != "resolved")).all())
    maint = list(db.scalars(select(ScheduledMaintenance).where(
        ScheduledMaintenance.status.in_(("scheduled", "in_progress", "extended")))
        .order_by(ScheduledMaintenance.starts_at_utc).limit(3)).all())
    src = Source(id="status:public", label="Public status page", source_class="authoritative",
                 owner="Status service", updated_at=now())
    rows = [row(i.title, "confirmed", value=f"{i.status} · {i.impact}",
                detail=i.current_update) for i in incidents]
    rows += [row(m.title, "info", value=m.status.replace("_", " "),
                 detail=f"{iso(m.starts_at_utc)} → {iso(m.ends_at_utc)}") for m in maint]
    summary = (f"{len(incidents)} published incident(s) are active." if incidents else
               "No platform incident is published right now.")
    return ToolResult("platform.status", [status_card("ZoikoStream service status",
                                                      rows or [row("All published components", "stable",
                                                                   value="No active incidents")],
                                                      [src])],
                      [src], summary=summary, actions=[nav("Open status page", "/status")],
                      facts={"incidents": len(incidents), "maintenance": len(maint)})
