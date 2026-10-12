"""Conversation store (ZST-UX-GUIDE-001 §6.16, §13, §16).

Conversations belong to exactly one (organization, user) pair; nobody else — including an
organization admin — can read them through Guide. Text is redacted for credentials before it
reaches this module. Retention is bounded (OPGUIDE_CONVERSATION_RETENTION_DAYS from last
activity) and expired rows are purged opportunistically, a small batch per write.

The rate limit counts the user's own messages in the database, so it holds across server
instances without shared memory.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import errors
from .config import get_settings
from .policy import Actor
from .schema import AuditEvent, Conversation, Feedback, Message
from .sources import iso, now

PURGE_BATCH = 50
FEEDBACK_REASONS = ("incorrect", "not_helpful", "outdated", "unsafe", "other")


def _retention() -> timedelta:
    return timedelta(days=get_settings().OPGUIDE_CONVERSATION_RETENTION_DAYS)


def _title(text: str) -> str:
    flat = " ".join(text.split())
    return (flat[:77] + "…") if len(flat) > 80 else (flat or "New conversation")


def own(db: Session, actor: Actor, conversation_id) -> Conversation:
    try:
        cid = uuid.UUID(str(conversation_id))
    except (TypeError, ValueError):
        raise errors.NotFound("That conversation does not exist.") from None
    conv = db.scalar(select(Conversation).where(
        Conversation.id == cid, Conversation.org_id == actor.org_id,
        Conversation.user_id == actor.user.id, Conversation.archived_at.is_(None)))
    if conv is None:
        raise errors.NotFound("That conversation does not exist.")
    return conv


def start(db: Session, actor: Actor, first_text: str, context: dict) -> Conversation:
    conv = Conversation(org_id=actor.org_id, user_id=actor.user.id, title=_title(first_text),
                        context=context, purge_after=now() + _retention())
    db.add(conv)
    db.flush()
    return conv


def add(db: Session, conv: Conversation, role: str, text: str, *, payload: dict | None = None,
        intent: str | None = None, context: dict | None = None) -> Message:
    msg = Message(conversation_id=conv.id, role=role, text=text, payload=payload or {},
                  intent=intent)
    db.add(msg)
    conv.updated_at = now()
    conv.purge_after = now() + _retention()
    if context is not None:
        conv.context = context
    db.flush()
    return msg


def enforce_rate_limit(db: Session, actor: Actor) -> None:
    limit = get_settings().OPGUIDE_MESSAGES_PER_MINUTE
    recent = db.scalar(select(func.count(Message.id)).join(Conversation).where(
        Conversation.user_id == actor.user.id, Conversation.org_id == actor.org_id,
        Message.role == "user", Message.created_at >= now() - timedelta(minutes=1))) or 0
    if recent >= limit:
        raise errors.RateLimited("You're sending messages faster than Guide can answer. "
                                 "Wait a moment and try again.")


def recent_history(db: Session, conv: Conversation, limit: int = 6) -> list[Message]:
    rows = db.scalars(select(Message).where(Message.conversation_id == conv.id)
                      .order_by(Message.created_at.desc()).limit(limit)).all()
    return list(reversed(rows))


def listing(db: Session, actor: Actor, *, q: str = "", limit: int = 30) -> list[dict]:
    stmt = select(Conversation).where(Conversation.org_id == actor.org_id,
                                      Conversation.user_id == actor.user.id,
                                      Conversation.archived_at.is_(None))
    if q.strip():
        term = f"%{q.strip()[:80]}%"
        stmt = stmt.where(Conversation.title.ilike(term) | Conversation.id.in_(
            select(Message.conversation_id).where(Message.text.ilike(term))))
    rows = db.scalars(stmt.order_by(Conversation.updated_at.desc()).limit(min(limit, 100))).all()
    return [{"id": str(c.id), "title": c.title, "updated_at": iso(c.updated_at),
             "event": (c.context or {}).get("event")} for c in rows]


def transcript(db: Session, actor: Actor, conversation_id) -> dict:
    conv = own(db, actor, conversation_id)
    msgs = db.scalars(select(Message).where(Message.conversation_id == conv.id)
                      .order_by(Message.created_at)).all()
    return {"id": str(conv.id), "title": conv.title, "context": conv.context,
            "messages": [message_out(m) for m in msgs]}


def message_out(m: Message) -> dict:
    return {"id": str(m.id), "role": m.role, "text": m.text, "intent": m.intent,
            "at": iso(m.created_at), **({"payload": m.payload} if m.role == "guide" else {})}


def remove(db: Session, actor: Actor, conversation_id) -> None:
    """User deletion: the transcript is deleted outright, not just hidden. The audit trail
    keeps only hashes and metadata, so nothing readable survives."""
    conv = own(db, actor, conversation_id)
    db.delete(conv)
    db.flush()


def feedback(db: Session, actor: Actor, message_id, rating: str, reason: str | None) -> None:
    if rating not in ("up", "down"):
        raise errors.ValidationFailed("Rating must be up or down.")
    if reason is not None and reason not in FEEDBACK_REASONS:
        raise errors.ValidationFailed("Unknown feedback reason.")
    try:
        mid = uuid.UUID(str(message_id))
    except (TypeError, ValueError):
        raise errors.NotFound("That message does not exist.") from None
    msg = db.scalar(select(Message).join(Conversation).where(
        Message.id == mid, Message.role == "guide", Conversation.org_id == actor.org_id,
        Conversation.user_id == actor.user.id))
    if msg is None:
        raise errors.NotFound("That message does not exist.")
    row = db.scalar(select(Feedback).where(Feedback.message_id == mid,
                                           Feedback.user_id == actor.user.id))
    if row is None:
        db.add(Feedback(message_id=mid, org_id=actor.org_id, user_id=actor.user.id,
                        rating=rating, reason=reason))
    else:
        row.rating, row.reason = rating, reason
    db.flush()


def purge_expired(db: Session) -> int:
    """Delete a small batch of conversations past retention (messages cascade)."""
    ids = db.scalars(select(Conversation.id).where(Conversation.purge_after < now())
                     .limit(PURGE_BATCH)).all()
    if ids:
        db.execute(delete(Conversation).where(Conversation.id.in_(ids)))
    db.execute(delete(AuditEvent).where(AuditEvent.id.in_(
        select(AuditEvent.id).where(AuditEvent.purge_after < now()).limit(PURGE_BATCH))))
    return len(ids)
