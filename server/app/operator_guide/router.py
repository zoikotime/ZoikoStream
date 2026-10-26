"""Guide API (ZST-UX-GUIDE-001 §11): /api/operator-guide.

Authentication is the platform's own (bearer JWT + server-side session via
get_current_user); the organization-state gate is applied at mount time exactly as on the
other organization routers. Every endpoint resolves the Actor first, so role, platform
availability, organization availability and tenant are checked before anything else runs.

Errors are returned as {"code", "message"} JSON with the §15 wording, through a route class
local to this router — the platform's own exception handling is untouched.
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Callable

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.guide.secrets_detect import detect as detect_secrets
from app.models.user import User
from app.security import get_current_user

from . import (
    actions, attention, audit, composer, context as context_svc, conversations, errors,
    governance, memory, policy, schema,
)
from .intents import Intent, classify
from .tools.base import notice

log = logging.getLogger(__name__)

MAX_TEXT = 2000
PURGE_PROBABILITY = 0.05


class GuideRoute(APIRoute):
    """Turns typed Guide errors into their §15 JSON body; anything unexpected becomes the
    generic 'Guide is temporarily unavailable' — never a stack trace or internal detail."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def guarded(request: Request):
            try:
                return await handler(request)
            except errors.GuideError as exc:
                return JSONResponse(exc.body(), status_code=exc.status,
                                    headers={"Cache-Control": "no-store"})
        return guarded


router = APIRouter(prefix="/operator-guide", tags=["operator-guide"], route_class=GuideRoute)


# ── Dependencies ─────────────────────────────────────────────────────────────────────

_schema_ok = False


def _require_schema(db: Session) -> None:
    global _schema_ok
    if not _schema_ok:
        _schema_ok = schema.schema_ready(db)
    if not _schema_ok:
        raise errors.Unavailable(errors.GUIDE_UNAVAILABLE, detail={"reason": "schema_missing"})


def _token(request: Request) -> str:
    return request.headers.get("authorization", "").partition(" ")[2].strip()


def actor_dep(request: Request, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)) -> policy.Actor:
    _require_schema(db)
    return policy.require_actor(db, user, _token(request))


def governance_actor_dep(request: Request, user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)) -> policy.Actor:
    _require_schema(db)
    return policy.require_actor(db, user, _token(request), allow_org_disabled=True)


# ── Bodies ───────────────────────────────────────────────────────────────────────────

class StructuredAction(BaseModel):
    action_id: str = Field(..., max_length=64)
    params: dict = Field(default_factory=dict)


class MessageIn(BaseModel):
    text: str = Field("", max_length=MAX_TEXT)
    conversation_id: str | None = None
    route: str = Field("", max_length=300)
    event_id: str | None = None
    # A user click on a Guide-rendered choice (e.g. which access link, "use this draft").
    action: StructuredAction | None = None


class FeedbackIn(BaseModel):
    rating: str
    reason: str | None = None


class SettingsIn(BaseModel):
    enabled: bool | None = None
    capabilities: dict[str, bool] | None = None


# ── Home / context ───────────────────────────────────────────────────────────────────

@router.get("/bootstrap")
def bootstrap(route: str = Query("", max_length=300), event_id: str | None = None,
              actor: policy.Actor = Depends(actor_dep), db: Session = Depends(get_db)):
    ctx = context_svc.resolve(db, actor, route=route, event_id=event_id)
    caps = governance.settings_for(db, actor.org_id)["capabilities"]
    return {
        "user": {"name": actor.user.full_name,
                 "role": actor.role, "is_org_admin": policy.is_org_admin(actor.user)},
        "context": context_svc.public(ctx),
        "capabilities": caps,
        "attention": attention.summary(db, actor),
        "suggestions": composer.suggestions_for_ctx(ctx, actor),
        "recent": conversations.listing(db, actor, limit=5),
    }


@router.get("/context/events")
def context_events(q: str = Query("", max_length=80), actor: policy.Actor = Depends(actor_dep),
                   db: Session = Depends(get_db)):
    return {"items": context_svc.selectable_events(db, actor, q=q)}


@router.get("/attention")
def attention_items(actor: policy.Actor = Depends(actor_dep), db: Session = Depends(get_db)):
    return attention.summary(db, actor)


# ── Messages ─────────────────────────────────────────────────────────────────────────

@router.post("/messages")
def post_message(body: MessageIn, actor: policy.Actor = Depends(actor_dep),
                 db: Session = Depends(get_db)):
    started = time.monotonic()
    if not body.text.strip() and body.action is None:
        raise errors.ValidationFailed("Type a question for Guide.")
    conversations.enforce_rate_limit(db, actor)

    # Credentials are removed before the text is stored, logged, audited or sent to a model.
    redaction = detect_secrets(body.text)
    text = redaction.text.strip()

    ctx = context_svc.resolve(db, actor, route=body.route)
    if body.event_id:
        # An explicitly selected event is checked strictly: another tenant's id is a 404.
        ctx = {**ctx, "_event": policy.resolve_event(db, actor, body.event_id)}
        ctx["event"] = context_svc.event_brief(ctx["_event"])
    if text and body.action is None:
        # An event named in the message ("is Spring Gala ready?") becomes the context, even
        # over a selected one — and the switch is announced in the thread, never silent.
        named = context_svc.event_named_in(db, actor, text)
        if named is not None and (ctx["_event"] is None or named.id != ctx["_event"].id):
            ctx["_event"], ctx["event"] = named, context_svc.event_brief(named)

    conv = conversations.own(db, actor, body.conversation_id) if body.conversation_id else None
    history = memory.history(db, conv)
    intent = _structured_intent(body.action) if body.action else classify(text)
    # §13: a follow-up ("and the replay?") reuses the previous turn's event and topic.
    intent, ctx = memory.follow_up(db, actor, conv, text, intent, ctx)
    actions.expire_due(db, actor=actor)

    public_ctx = context_svc.public(ctx)
    change = memory.context_change(conv.context if conv is not None else None, public_ctx)
    if conv is None:
        conv = conversations.start(db, actor, text or body.action.action_id, public_ctx)
    notice_msg = (conversations.add(db, conv, "guide", change, intent="context.change",
                                    payload=_context_payload(change)) if change else None)
    user_text = text or _describe_action(body.action)
    user_msg = conversations.add(db, conv, "user", user_text, context=public_ctx)

    savepoint = db.begin_nested()
    try:
        composed = composer.compose(db, actor, ctx, text, intent, conversation_id=conv.id,
                                    history=history)
        savepoint.commit()
    except Exception:
        savepoint.rollback()
        log.exception("opguide compose failed intent=%s", intent.name)
        composed = composer.Composed(errors.GUIDE_UNAVAILABLE, intent.name, outcome="error")
    if redaction.suspected:
        composed.blocks.insert(0, notice("warning", "I removed something that looked like a "
                                                    "password, key or private link from your "
                                                    "message. Never share credentials in chat."))

    payload = composed.payload()
    guide_msg = conversations.add(db, conv, "guide", composed.answer, payload=payload,
                                  intent=composed.intent)
    audit.record(db, actor, kind="message", outcome=composed.outcome, context=public_ctx,
                 request_text=user_text, intent=composed.intent,
                 model_release=composed.model_release,
                 source_refs=[s["id"] for s in payload["sources"]],
                 tool_plan=[{"tool": t} for t in composed.tools],
                 authorization=composed.authorization, response_ref=str(guide_msg.id),
                 correlation_id=composed.correlation_id,
                 latency_ms=int((time.monotonic() - started) * 1000))
    if random.random() < PURGE_PROBABILITY:
        conversations.purge_expired(db)
        actions.expire_due(db)
    db.commit()
    return {"conversation_id": str(conv.id), "user_message_id": str(user_msg.id),
            "message": conversations.message_out(guide_msg), "context": public_ctx,
            "context_notice": conversations.message_out(notice_msg) if notice_msg else None}


def _context_payload(text: str) -> dict:
    """A context-change notice as a thread item (§4.2); rendered as a divider, not an answer."""
    return {"answer": text, "notice": "context", "blocks": [], "sources": [], "actions": [],
            "suggestions": [], "mode": "standard", "intent": "context.change",
            "explanation": None, "stale": False}


def _structured_intent(action: StructuredAction) -> Intent:
    if action.action_id not in actions.CATALOG:
        raise errors.ValidationFailed("That is not an action Guide can perform.")
    return Intent("action", action.action_id, action=action.action_id,
                  params={k: v for k, v in action.params.items()
                          if k in ("link_id", "text", "feature", "enabled", "visibility",
                                   "required")})


def _describe_action(action: StructuredAction | None) -> str:
    return {"access_link.revoke": "Revoke the selected access link",
            "event.update_description": "Use this draft as the event description"}.get(
        action.action_id if action else "", "Run the selected action")


# ── Conversations / feedback ─────────────────────────────────────────────────────────

@router.get("/conversations")
def list_conversations(q: str = Query("", max_length=80), actor: policy.Actor = Depends(actor_dep),
                       db: Session = Depends(get_db)):
    return {"items": conversations.listing(db, actor, q=q)}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, actor: policy.Actor = Depends(actor_dep),
                     db: Session = Depends(get_db)):
    return conversations.transcript(db, actor, conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, actor: policy.Actor = Depends(actor_dep),
                        db: Session = Depends(get_db)):
    conversations.remove(db, actor, conversation_id)
    db.commit()


@router.post("/messages/{message_id}/feedback", status_code=204)
def message_feedback(message_id: str, body: FeedbackIn, actor: policy.Actor = Depends(actor_dep),
                     db: Session = Depends(get_db)):
    conversations.feedback(db, actor, message_id, body.rating, body.reason)
    db.commit()


# ── Governed actions ─────────────────────────────────────────────────────────────────

@router.post("/proposals/{proposal_id}/confirm")
def confirm_proposal(proposal_id: str, request: Request,
                     idempotency_key: str = Header("", alias="Idempotency-Key"),
                     actor: policy.Actor = Depends(actor_dep), db: Session = Depends(get_db)):
    executor = actions.asgi_executor(request.app, host=request.headers.get("host", "localhost"),
                                     token=actor.token)
    result = actions.confirm(db, actor, proposal_id, idempotency_key=idempotency_key,
                             execute=executor)
    if not result.get("replayed"):
        _post_receipt(db, actor, result)
    return result


@router.post("/proposals/{proposal_id}/cancel")
def cancel_proposal(proposal_id: str, actor: policy.Actor = Depends(actor_dep),
                    db: Session = Depends(get_db)):
    result = actions.cancel(db, actor, proposal_id)
    db.commit()
    return result


def _post_receipt(db: Session, actor: policy.Actor, result: dict) -> None:
    """Append the outcome to the conversation the proposal came from, so history shows what
    actually happened — success, failure or unknown — in Guide's own words."""
    p = db.get(schema.Proposal, uuid.UUID(result["id"]))
    if p is None or p.conversation_id is None or not p.receipt:
        return
    try:
        conv = conversations.own(db, actor, p.conversation_id)
    except errors.NotFound:
        return
    text = p.receipt.get("message") or ""
    if p.receipt.get("reason"):
        text += " " + p.receipt["reason"]
    conversations.add(db, conv, "guide", text.strip(), intent="action.receipt",
                      payload={"answer": text.strip(), "blocks": [result], "sources": [],
                               "actions": [], "suggestions": [], "mode": "standard",
                               "intent": "action.receipt", "explanation": None, "stale": False})
    db.commit()


# ── Governance (§6.17) ───────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(actor: policy.Actor = Depends(governance_actor_dep),
                 db: Session = Depends(get_db)):
    return governance.describe(db, actor)


@router.put("/settings")
def put_settings(body: SettingsIn, actor: policy.Actor = Depends(governance_actor_dep),
                 db: Session = Depends(get_db)):
    result = governance.update(db, actor, enabled=body.enabled, capabilities=body.capabilities)
    audit.record(db, actor, kind="governance", outcome="updated", intent="settings.update",
                 authorization="allowed",
                 tool_plan=[{"tool": "settings.update", "enabled": body.enabled,
                             "capabilities": body.capabilities}])
    db.commit()
    return result


@router.get("/audit")
def get_audit(limit: int = Query(50, ge=1, le=200), before_id: int | None = None,
              actor: policy.Actor = Depends(governance_actor_dep), db: Session = Depends(get_db)):
    return governance.audit_log(db, actor, limit=limit, before_id=before_id)
