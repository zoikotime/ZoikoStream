"""Conversation memory (ZST-UX-GUIDE-001 §13): the current Guide thread — never platform truth.

Memory is used for exactly two things:
  * Follow-ups. "and the replay?", "what about access?", "why?" lean on the previous turn:
    with no event in this turn, the previous turn's event is reused (re-checked against the
    caller's tenant like any event id); with no topic, the previous turn's read is repeated.
  * The model's explanation call gets the last few turns, so it can phrase an answer to a
    follow-up. Turns are context, never facts: every number is still grounded in tool facts
    (composer.validate_explanation), and every answer re-reads authoritative services.

A user statement may be remembered in the thread but never becomes state (§13 truth
boundary): "the backup encoder is tested" changes nothing until the platform says so.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import context as context_svc, errors, policy
from .intents import EVENT_TOOLS, Intent, is_follow_up
from .policy import Actor
from .schema import Conversation, Message
from .tools import REGISTRY

HISTORY_TURNS = 4


def history(db: Session, conv: Conversation | None) -> list[dict]:
    """The last few turns, oldest first, as plain text (already redacted at write time)."""
    if conv is None:
        return []
    rows = db.scalars(select(Message).where(Message.conversation_id == conv.id,
                                            Message.intent.is_distinct_from("context.change"))
                      .order_by(Message.created_at.desc()).limit(HISTORY_TURNS)).all()
    return [{"role": m.role, "text": m.text[:300]} for m in reversed(rows)]


def _last_read(db: Session, conv: Conversation) -> str | None:
    """The intent of the previous Guide answer, if it was a read Guide can repeat."""
    name = db.scalar(select(Message.intent).where(
        Message.conversation_id == conv.id, Message.role == "guide",
        Message.intent.is_distinct_from("context.change"))
        .order_by(Message.created_at.desc()).limit(1))
    return name if name in REGISTRY or name == "attention" else None


def _read_intent(name: str) -> Intent:
    if name == "attention":
        return Intent("read", "attention", tools=["org.overview"])
    tools = [name] + (["event.live"] if name == "event.incidents" else [])
    return Intent("read", name, tools=tools)


def follow_up(db: Session, actor: Actor, conv: Conversation | None, text: str, intent: Intent,
              ctx: dict) -> tuple[Intent, dict]:
    """Resolve a follow-up against the previous turn. Anything that is not a follow-up, or a
    turn that already names its event and topic, passes through unchanged."""
    if conv is None or not is_follow_up(text):
        return intent, ctx
    if intent.kind == "docs" and intent.name == "docs":
        previous = _last_read(db, conv)
        if previous:
            intent = _read_intent(previous)
    if ctx.get("_event") is None and intent.kind == "read" and \
            any(t in EVENT_TOOLS for t in intent.tools):
        prior = ((conv.context or {}).get("event") or {}).get("id")
        if prior:
            try:
                ev = policy.resolve_event(db, actor, prior)
            except errors.NotFound:
                ev = None
            if ev is not None:
                ctx = {**ctx, "_event": ev, "event": context_svc.event_brief(ev)}
    return intent, ctx


def context_change(previous: dict | None, current: dict) -> str | None:
    """The visible thread notice when the event context changed since the last turn (§4.2:
    "Changing context must be visible in-thread"). None for a new thread or no change."""
    if previous is None:
        return None
    before = (previous.get("event") or {}).get("id")
    after = current.get("event") or {}
    if before == after.get("id"):
        return None
    return (f"Context: now {after['title']}" if after else
            "Context: no event selected — answers now cover your organization.")
