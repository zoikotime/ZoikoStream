"""Admin governance (ZST-UX-GUIDE-001 §6.17): per-organization availability and capability
switches, plus the organization's Guide audit trail. The server enforces these settings
(policy.require_actor / require_capability); the frontend only reflects them."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import errors
from .policy import CAPABILITIES, FIXED_GOVERNANCE, Actor, is_org_admin
from .schema import AuditEvent, OrgSettings


def settings_for(db: Session, org_id) -> dict:
    row = db.get(OrgSettings, org_id)
    caps = {key: True for key in CAPABILITIES}
    if row is not None:
        caps.update({k: bool(v) for k, v in (row.capabilities or {}).items() if k in CAPABILITIES})
    return {"enabled": True if row is None else bool(row.enabled), "capabilities": caps}


def describe(db: Session, actor: Actor) -> dict:
    current = settings_for(db, actor.org_id)
    return {
        "enabled": current["enabled"],
        "capabilities": [{"key": k, "label": label, "enabled": current["capabilities"][k]}
                         for k, label in CAPABILITIES.items()],
        "governance": [{"key": k, "label": {"audit": "Audit AI actions",
                                             "write_confirmation": "Human confirmation for writes",
                                             "high_risk_via_guide": "High-risk controls via Guide"}[k],
                        "value": v} for k, v in FIXED_GOVERNANCE.items()],
        "editable": is_org_admin(actor.user),
    }


def update(db: Session, actor: Actor, *, enabled: bool | None,
           capabilities: dict[str, bool] | None) -> dict:
    if not is_org_admin(actor.user):
        raise errors.Forbidden("Only an organization admin can change Guide settings.")
    unknown = set(capabilities or {}) - set(CAPABILITIES)
    if unknown:
        raise errors.ValidationFailed(f"Unknown capability: {', '.join(sorted(unknown))}.")
    row = db.get(OrgSettings, actor.org_id)
    if row is None:
        row = OrgSettings(org_id=actor.org_id, enabled=True, capabilities={})
        db.add(row)
    if enabled is not None:
        row.enabled = bool(enabled)
    if capabilities:
        row.capabilities = {**(row.capabilities or {}), **{k: bool(v) for k, v in capabilities.items()}}
    row.updated_by = actor.user.id
    db.flush()
    return describe(db, actor)


def audit_log(db: Session, actor: Actor, *, limit: int = 50, before_id: int | None = None) -> dict:
    """The organization's Guide audit trail — metadata only, never conversation text."""
    if not is_org_admin(actor.user):
        raise errors.Forbidden("Only an organization admin can view the Guide audit log.")
    stmt = select(AuditEvent).where(AuditEvent.org_id == actor.org_id)
    if before_id:
        stmt = stmt.where(AuditEvent.id < before_id)
    rows = db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(min(limit, 200))).all()
    return {"items": [{
        "id": r.id, "at": r.created_at.isoformat(), "kind": r.kind, "actor_id": str(r.actor_id),
        "role": r.role_context, "intent": r.intent, "outcome": r.outcome,
        "tools": [t.get("tool") for t in (r.tool_plan or [])],
        "authorization": r.authorization_result, "correlation_id": r.correlation_id,
        "model": r.model_release,
    } for r in rows], "next_before_id": rows[-1].id if len(rows) == min(limit, 200) else None}

