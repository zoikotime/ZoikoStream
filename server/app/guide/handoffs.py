"""Human handoff routes and feedback (API §13–14; Conversation Flow §25–26).

Handoff: every target resolves to a REGISTERED route. There is no support-ticket integration,
so no target ever reports a created ticket or callback — the outcome is `route_available`
(PR-027: "never claim a human ticket ... was created"). Leads, the one real submission, go
through the lead flow.
"""
from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import crypto, registry, retention
from .errors import GuideProblem
from .models import GuideFeedback, GuideHandoff, GuideMessage, GuideSession
from .secrets_detect import detect
from .sessions import audit, current_event_claims

COMMENT_CONTEXT = "guide_feedback.comment"

# target -> (destination id, explanation copy). PR-025: say why, no blame, no implied access.
HANDOFF_ROUTES = {
    "support": ("contact-support", "ZoikoStream support can help with this. Use the contact "
                                   "page and choose the topic that fits."),
    "sales": ("contact-sales", "The ZoikoStream team can help with commercial questions. Use the "
                               "contact page, or request a demo here in Guide."),
    "sign_in": ("sign-in", "Account details are only available after you sign in."),
    "help_center": ("help-center", "You can reach the ZoikoStream team through the contact "
                                   "page."),
    "status": ("status-page", "The public status page shows the current state of ZoikoStream "
                              "services."),
    "privacy": ("privacy-request", "Privacy requests are handled through ZoikoStream's privacy "
                                   "channel, not in this chat."),
}
ORGANIZER_COPY = ("The event organizer manages access to their event. Use the contact details "
                  "in your invitation to reach them.")


def resolve_handoff(db: Session, request: Request, session: GuideSession, *, target: str,
                    reason_code: str | None, idempotency_key: str) -> dict:
    if not idempotency_key or not 16 <= len(idempotency_key) <= 128:
        raise GuideProblem("GUIDE_BAD_REQUEST", "A valid Idempotency-Key header is required.")
    actions: list[dict] = []
    if target == "organizer":
        claims = current_event_claims(db, session) or {}
        # Only an intentionally public organizer route from validated context (API §13).
        message = ORGANIZER_COPY
        dest_id = "viewer-support"
        if not claims.get("organizer_contact_route"):
            message = ("I don't have public organizer contact details for this event. Your "
                       "invitation is the best place to find them, or ZoikoStream viewer "
                       "support can help.")
    else:
        dest_id, message = HANDOFF_ROUTES[target]
    action = registry.action_for(db, dest_id)
    if action:
        actions.append(action)
    row = GuideHandoff(session_id=session.session_id, target=target,
                       reason_code=(reason_code or None), destination_id=action and dest_id,
                       outcome="route_available" if action else "failed",
                       purge_after=retention.purge_after("handoff"))
    db.add(row)
    session.state = "SALES_HANDOFF" if target == "sales" else "SUPPORT_HANDOFF"
    audit(db, request, "handoff_resolved", session_id=session.session_id, target=target,
          reason_code=reason_code, outcome=row.outcome)
    db.flush()
    return {"handoff_id": str(row.handoff_id), "target": target, "outcome": row.outcome,
            "message": message, "actions": actions}


def record_feedback(db: Session, request: Request, session: GuideSession, *,
                    client_feedback_id: uuid.UUID, target_type: str, target_id: str | None,
                    rating: str, reason_code: str | None, comment: str | None) -> None:
    existing = db.scalar(select(GuideFeedback).where(
        GuideFeedback.session_id == session.session_id,
        GuideFeedback.client_feedback_id == client_feedback_id))
    if existing is not None:
        return  # idempotent (DB-INV-05)
    if target_type == "answer":
        try:
            mid = uuid.UUID(target_id or "")
        except ValueError:
            raise GuideProblem("GUIDE_VALIDATION_ERROR", "Unknown answer.") from None
        msg = db.get(GuideMessage, mid)
        if msg is None or msg.session_id != session.session_id or msg.role != "assistant":
            raise GuideProblem("GUIDE_RESOURCE_NOT_FOUND")
    cipher, comment_purge = None, None
    if comment and comment.strip():
        cleaned = detect(comment.strip()).text  # never store a pasted secret
        cipher = crypto.encrypt(cleaned, context=COMMENT_CONTEXT)
        comment_purge = retention.purge_after("feedback_comment")
    db.add(GuideFeedback(
        session_id=session.session_id, client_feedback_id=client_feedback_id,
        target_type=target_type, target_id=target_id, rating=rating, reason_code=reason_code,
        comment_ciphertext=cipher, comment_purge_after=comment_purge,
        purge_after=retention.purge_after("feedback")))
    # Structured signal only — the comment never enters audit/analytics (PRD §16).
    audit(db, request, "feedback", session_id=session.session_id, target_type=target_type,
          rating=rating, reason_code=reason_code, has_comment=cipher is not None)
