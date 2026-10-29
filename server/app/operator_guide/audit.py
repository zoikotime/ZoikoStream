"""Audit Service (ZST-UX-GUIDE-001 §16.1): the minimum audit event for every interaction.

An audit row holds references and metadata — a hash of the request, tool ids, source ids,
receipts — never raw credentials and never the conversation text itself (that lives in the
conversation store under its own retention). Rows are written in the caller's transaction so
an interaction and its audit commit or roll back together.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from .config import get_settings
from .policy import POLICY_VERSION, Actor
from .schema import AuditEvent
from .sources import now


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"


def request_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def record(db: Session, actor: Actor, *, kind: str, outcome: str, interaction_id: str | None = None,
           context: dict | None = None, request_text: str | None = None,
           intent: str | None = None, model_release: str | None = None,
           source_refs: list[str] | None = None, tool_plan: list[dict] | None = None,
           authorization: str | None = None, receipts: list[dict] | None = None,
           confirmation_actor=None, correlation_id: str | None = None,
           response_ref: str | None = None, latency_ms: int | None = None) -> AuditEvent:
    return write(db, org_id=actor.org_id, actor_id=actor.user.id, role=actor.role, kind=kind,
                 outcome=outcome, interaction_id=interaction_id, context=context,
                 request_text=request_text, intent=intent, model_release=model_release,
                 source_refs=source_refs, tool_plan=tool_plan, authorization=authorization,
                 receipts=receipts, confirmation_actor=confirmation_actor,
                 correlation_id=correlation_id, response_ref=response_ref, latency_ms=latency_ms)


def write(db: Session, *, org_id, actor_id, role: str, kind: str, outcome: str,
          interaction_id: str | None = None, context: dict | None = None,
          request_text: str | None = None, intent: str | None = None,
          model_release: str | None = None, source_refs: list[str] | None = None,
          tool_plan: list[dict] | None = None, authorization: str | None = None,
          receipts: list[dict] | None = None, confirmation_actor=None,
          correlation_id: str | None = None, response_ref: str | None = None,
          latency_ms: int | None = None) -> AuditEvent:
    """The row itself. ``record`` is the usual entry point; this one also serves events with
    no request behind them (a proposal expiring by TTL), attributed to the original actor."""
    row = AuditEvent(
        interaction_id=interaction_id or new_id("int"),
        org_id=org_id, actor_id=actor_id, role_context=role,
        context={k: v for k, v in (context or {}).items() if k in ("workspace", "route", "event",
                                                                     "environment")},
        kind=kind,
        user_request_hash=request_hash(request_text) if request_text is not None else None,
        intent=intent, model_release=model_release, policy_version=POLICY_VERSION,
        retrieval_source_refs=list(source_refs or [])[:50],
        tool_plan=list(tool_plan or []), authorization_result=authorization,
        tool_execution_receipts=list(receipts or []), confirmation_actor=confirmation_actor,
        correlation_id=correlation_id, final_response_ref=response_ref, outcome=outcome,
        latency_ms=latency_ms,
        purge_after=now() + timedelta(days=get_settings().OPGUIDE_AUDIT_RETENTION_DAYS),
    )
    db.add(row)
    return row
