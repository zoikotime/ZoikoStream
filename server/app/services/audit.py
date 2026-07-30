"""Append-only admin audit trail. Every mutating endpoint in routers/admin.py calls
log_action() right before its own db.commit() -- the row lands in the same
transaction as the change it's recording, so nothing to log ever survives a rollback
that its parent change doesn't."""
from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.user import User


def log_action(
    db: Session,
    actor: User,
    action: str,
    *,
    target_type: str | None = None,
    target_id: object = None,
    org_id: object = None,
    meta: dict | None = None,
    request: Request | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor.id,
            actor_email=actor.email,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            org_id=org_id,
            ip=request.client.host if request is not None and request.client else None,
            meta=meta,
        )
    )
