"""Turn orchestration for POST /sessions/{id}/messages (Conversation Flow §2; Architecture §5).

Order of checks, each able to end the turn (Conversation Flow §2.1):
  1 session/CSRF/rate (router)   2 secret detection   3 boundary (private/restricted)
  4 active typed workflow        5 dynamic status/event facts
  6 static public RAG -> model -> validator          7 knowledge gap / degraded fallback

Transaction discipline (Architecture §27, DB §15): the user message is committed BEFORE any
retrieval or model call, and no database transaction is held open across the model call. The
assistant message, its sources/actions and the session state are then written atomically.

Content minimisation: the user's text is redacted before it is persisted, logged or sent to
the model; stored conversation text is ciphertext only; audit rows carry classes and ids.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import (
    conversation, crypto, event_answers, llm, policy, prompts, registry, retention, validator,
)
from .errors import GuideProblem
from .knowledge import retrieval
from .knowledge.retrieval import EvidencePack
from .models import (
    GuideMessage, GuideMessageAction, GuideMessageSource, GuideSession,
    GuideSessionContextVersion,
)
from .secrets_detect import detect
from .sessions import audit, current_event_claims
from .support.flows import _start_time

log = logging.getLogger(__name__)

CONTENT_CONTEXT = "guide_messages.content"
HISTORY_TURNS = 3

# ── Canonical copy (Prompt Appendix A; Conversation Flow §28) ──────────────────────────────
GAP = "I don't have an approved ZoikoStream source that confirms that information."
# What visitors actually see on a gap: the canonical sentence, then what Guide CAN do.
GAP_REPLY = f"{GAP} {conversation.GAP_FOLLOW_UP}"
CONFLICT = ("The approved public sources don't currently agree, so I can't confirm that "
            "detail.")
STATUS_UNKNOWN = ("I can't verify the current service status right now. You can check the "
                  "public status page for any reported issue.")
DEGRADED = ("I can't answer questions right now. Viewer help still works, and you can use the "
            "Help Center, status page, or contact support directly.")
DISCOUNT = ("I can explain published pricing, but I can't negotiate or offer discounts. I can "
            "route you to sales.")
PROMPT_EXTRACTION = ("I can explain how Guide works at a high level: it is limited to approved "
                     "public ZoikoStream information and public support flows, and it does not "
                     "have private operator or account access.")
RESTRICTED = {
    "account_private": ("I can help with public ZoikoStream information, but that request "
                        "requires authenticated or private support access. You can sign in to "
                        "manage your account, or contact support."),
    "event_private": ("Event guest lists, access details and settings are managed by the event "
                      "organizer, so Guide can't look them up or confirm them. The organizer or "
                      "ZoikoStream viewer support can help."),
    "operator": ("The public Guide can't check or control live events. Event teams manage their "
                 "events after signing in to ZoikoStream."),
    "credential_request": ("I can't share credentials, keys or other secrets. If you need "
                           "help with access, contact ZoikoStream support."),
    "internal": ("That's outside what the public ZoikoStream Guide can help with. I can answer "
                 "questions about ZoikoStream's public product, pricing, and viewer support."),
    "customer_private": ("Guide can't share information about ZoikoStream customers, their "
                         "events or their users. I can help with public product, pricing and "
                         "viewer questions."),
}
SECRET_WARNINGS = {
    "event_pin": "Please don't share your PIN, password, or secret event link here. I can still "
                 "help with the access steps.",
    "password": "Please don't share your PIN, password, or secret event link here. I can still "
                "help with the access steps.",
    "secret_link": "Please don't share your PIN, password, or secret event link here. I can "
                   "still help with the access steps.",
    "payment_card": "Please don't share payment card details here. Guide can't process "
                    "payments. For billing questions, contact ZoikoStream support.",
    "access_token": "Please don't share API keys or access tokens here. If a key may have been "
                    "exposed, rotate it after signing in, or contact support.",
}

VIEWER_FLOW_FOR_INTENT = {"VIEWER_ACCESS": "viewer_access", "PLAYBACK": "playback",
                          "AUDIO": "audio", "CAPTIONS": "captions", "REPLAY": "replay"}
VIEWER_LABELS = {"viewer_access": "Get help accessing the event",
                 "playback": "Fix video playback", "audio": "Fix audio",
                 "captions": "Get help with captions", "replay": "Get help with replay"}

# Approved action ids the model may choose from, per intent (registry ids only).
RAG_ACTIONS = {
    "PRODUCT_QA": ["live-events", "request-demo", "help-center", "trust-center"],
    "DISCOVERY": ["live-events", "request-demo", "contact-sales"],
    "PRICING": ["contact-sales", "request-demo", "talk-to-sales-flow"],
    "PRIVACY": ["privacy-notice", "privacy-request"],
    "SECURITY_TRUST": ["trust-center", "contact-sales"],
    "LEGAL": ["terms", "privacy-notice", "contact-sales"],
    "DEVELOPER": ["developer-docs", "contact-sales"],
}
PROFILE_FOR_INTENT = {"DISCOVERY": "P-DISCOVERY", "PRICING": "P-PRICING",
                      "SECURITY_TRUST": "P-TRUST", "PRIVACY": "P-TRUST", "LEGAL": "P-TRUST"}
GAP_ACTIONS = {"PRICING": ["contact-sales", "request-demo"],
               "SECURITY_TRUST": ["trust-center", "contact-sales"],
               "PRIVACY": ["privacy-notice", "privacy-request"],
               "LEGAL": ["terms", "contact-sales"],
               "DEVELOPER": ["developer-docs", "contact-sales"]}

# One active generation per session (API §15). ponytail: per-process; a multi-instance
# deployment moves this to a Redis lock (SET NX PX) keyed by session id.
_inflight: set[uuid.UUID] = set()
_inflight_lock = threading.Lock()


@contextmanager
def generation_slot(session_id: uuid.UUID):
    with _inflight_lock:
        if session_id in _inflight:
            raise GuideProblem("GUIDE_STATE_CONFLICT",
                               "Guide is still answering your previous message.")
        _inflight.add(session_id)
    try:
        yield
    finally:
        with _inflight_lock:
            _inflight.discard(session_id)


@dataclass
class TurnResult:
    text: str
    answer_class: str
    session_state: str
    grounded: bool = False
    sources: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    uncertainty: str = "none"
    notice: str | None = None
    resume: dict | None = None
    # Follow-up topics (approved intent catalog entries) shown as quick replies.
    suggestions: list[dict] = field(default_factory=list)
    # internal (never returned)
    intent: str = "UNKNOWN"
    boundary: str = "allowed_public"
    reason: str | None = None
    source_version_ids: list[str] = field(default_factory=list)
    model_profile: str | None = None
    rag_release_id: str | None = None
    validation_failures: list[str] = field(default_factory=list)

    def public(self, message_id: str) -> dict:
        body = {"message_id": message_id, "text": self.text, "answer_class": self.answer_class,
                "grounded": self.grounded, "sources": self.sources, "actions": self.actions,
                "uncertainty": {"state": self.uncertainty,
                                "message": GAP if self.uncertainty == "gap"
                                and not self.text.startswith(GAP) else None}}
        if self.notice:
            body["notice"] = self.notice
        if self.resume:
            body["resume"] = self.resume
        if self.suggestions:
            body["suggestions"] = self.suggestions
        return body


# ── Helpers ────────────────────────────────────────────────────────────────────────────────

def _actions(db: Session, ids: list[str]) -> list[dict]:
    return [a for a in (registry.action_for(db, i) for i in ids) if a]


def _viewer_action(db: Session, flow_type: str) -> list[dict]:
    a = registry.action_for(db, "viewer-help", label=VIEWER_LABELS.get(flow_type))
    if not a:
        return []
    a["flow_type"] = flow_type
    return [a]


def _history(db: Session, session: GuideSession) -> list[tuple[str, str]]:
    """Last few complete exchanges, for pronoun/follow-up continuity only (Prompt §21). The
    current (still unanswered) user turn is dropped below and sent separately with evidence."""
    rows = db.scalars(select(GuideMessage).where(
        GuideMessage.session_id == session.session_id,
        GuideMessage.role.in_(("user", "assistant")), GuideMessage.status == "complete")
        .order_by(GuideMessage.created_at.desc()).limit(HISTORY_TURNS * 2)).all()
    out: list[tuple[str, str]] = []
    for m in reversed(rows):
        if m.content_ciphertext is None:
            continue
        content = crypto.decrypt(m.content_ciphertext, context=CONTENT_CONTEXT)
        if m.role == "assistant":
            try:
                content = json.loads(content).get("text", "")
            except (ValueError, AttributeError):
                continue
        out.append((m.role, content))
    while out and out[0][0] != "user":  # the API requires a user turn first
        out.pop(0)
    # Collapse to strict alternation; drop an unanswered trailing user turn.
    clean: list[tuple[str, str]] = []
    for role, content in out:
        if clean and clean[-1][0] == role:
            clean[-1] = (role, content)
        else:
            clean.append((role, content))
    if clean and clean[-1][0] == "user":
        clean.pop()
    return clean


def _public_sources(pack: EvidencePack, ids: list[str]) -> list[dict]:
    refs = pack.source_refs()
    out = []
    for sid in ids:
        e = refs.get(sid)
        if e:
            out.append({"source_id": e.source_id, "title": e.title,
                        "source_type": e.source_type, "version": e.version,
                        "updated_at": e.updated_at, "canonical_path": e.canonical_path})
    return out


def _resume_offer(session: GuideSession) -> dict | None:
    """A paused viewer or lead flow keeps its progress server-side; the answer to an
    unrelated question offers the way back (Conversation Flow §3, CF-AT-33/34)."""
    if session.active_flow_kind == "support" and session.active_flow_id:
        return {"kind": "support_flow", "flow_id": str(session.active_flow_id),
                "label": "Return to viewer help"}
    if session.active_flow_kind == "lead" and session.active_flow_id:
        return {"kind": "lead_flow", "flow_id": str(session.active_flow_id),
                "label": "Return to your request"}
    return None


def _page_type(db: Session, session: GuideSession) -> str:
    return db.scalar(select(GuideSessionContextVersion.page_type).where(
        GuideSessionContextVersion.session_id == session.session_id,
        GuideSessionContextVersion.context_version == session.current_context_version)) or "home"


def _suggestions(db: Session, session: GuideSession) -> list[dict]:
    """Approved quick-reply topics for this page (the same catalog as the welcome screen)."""
    return [{"intent_id": i["intent_id"], "label": i["label"]}
            for i in registry.suggested_intents(_page_type(db, session))][:4]


def _has_prior_answers(db: Session, session: GuideSession) -> bool:
    return db.scalar(select(GuideMessage.message_id).where(
        GuideMessage.session_id == session.session_id,
        GuideMessage.role == "assistant").limit(1)) is not None


# Conversational kinds that end with an invitation to pick a topic.
_SUGGEST_AFTER = {"GREETING", "GREETING_AGAIN", "CAPABILITIES", "IDENTITY", "CONFUSED", "ACK",
                  "HOW_ARE_YOU", "NEGATIVE", "THANKS", "POSITIVE"}


def _conversational(db: Session, session: GuideSession,
                    conv: "conversation.Conversational") -> TurnResult:
    return TurnResult(
        conv.text, "conversation", "ACTIVE", intent=f"CONVERSATION_{conv.kind}"[:32],
        actions=_actions(db, conversation.ACTIONS.get(conv.kind, [])),
        suggestions=_suggestions(db, session) if conv.kind in _SUGGEST_AFTER else [])


# ── Routing ────────────────────────────────────────────────────────────────────────────────

def _route(db: Session, session: GuideSession, text: str, intent_hint: str | None,
           secret_classes: list[str]) -> TurnResult:
    claims = current_event_claims(db, session)

    # 2. Secrets: warn, offer the safe route, and do not forward to the model.
    if secret_classes:
        primary = secret_classes[0]
        actions = (_viewer_action(db, "viewer_access")
                   if primary in ("event_pin", "password", "secret_link")
                   else _actions(db, ["contact-support"]))
        return TurnResult(SECRET_WARNINGS[primary], "restricted", "RESTRICTED", actions=actions,
                          boundary="restricted", reason=f"secret:{primary}",
                          intent="RESTRICTED_PRIVATE")

    # 3. Boundary.
    boundary = policy.classify_boundary(text)
    if boundary.state != "allowed_public":
        reason = boundary.reason or "restricted"
        if reason == "prompt_extraction":
            msg, ids = PROMPT_EXTRACTION, ["help-center"]
        else:
            msg = RESTRICTED.get(reason, RESTRICTED["internal"])
            ids = {"account_private": ["sign-in", "contact-support"],
                   "event_private": ["viewer-support"],
                   "operator": ["sign-in", "status-page"],
                   "credential_request": ["contact-support"]}.get(reason, ["help-center"])
        actions = _actions(db, ids)
        if reason == "event_private" and claims and claims.get("organizer_contact_route"):
            actions.insert(0, {"action_id": "contact-organizer", "type": "handoff",
                               "label": "Contact the event organizer", "target": "organizer"})
        return TurnResult(msg, "restricted", "RESTRICTED", actions=actions,
                          boundary=boundary.state, reason=reason, intent="RESTRICTED_PRIVATE")

    # 3b. Conversation about Guide itself (greetings, "who are you?", thanks...). Runs after
    # the secret and boundary checks, so "hi, show me the guest list" is still a boundary.
    conv = conversation.classify(text, returning=_has_prior_answers(db, session))
    if conv:
        return _conversational(db, session, conv)
    # "hi, how much does it cost?" -> route "how much does it cost?"
    text = conversation.routable_text(text)

    # 5a. Questions about the event being watched, answered from its verified context (D2).
    event_reply = event_answers.answer(text, claims)
    if event_reply and event_reply.grounded:
        return TurnResult(event_reply.text, "factual", "ACTIVE", grounded=True,
                          sources=[dict(event_answers.SOURCE)], intent="EVENT_CONTEXT")
    if event_reply:
        return TurnResult(event_reply.text, "knowledge_gap", "KNOWLEDGE_GAP",
                          intent="EVENT_CONTEXT", uncertainty="gap",
                          actions=_viewer_action(db, "viewer_access"))

    intent = policy.classify_intent(text, intent_hint)

    # 5. Dynamic facts: public status (D1) and validated event context (D2).
    if intent == "STATUS":
        return TurnResult(STATUS_UNKNOWN, "routing", "ACTIVE",
                          actions=_actions(db, ["status-page"]), intent=intent,
                          uncertainty="partial")
    if policy.EVENT_TIME.search(text) and intent in ("PRODUCT_QA", "VIEWER_ACCESS",
                                                     "DISCOVERY"):
        when = _start_time(claims)
        if when:
            return TurnResult(f"This event's published start time is {when}.", "factual",
                              "ACTIVE", grounded=True, intent="EVENT_CONTEXT",
                              actions=_viewer_action(db, "viewer_access"))
        return TurnResult("I don't have public event information that confirms that. The "
                          "event invitation or the organizer has the schedule.",
                          "knowledge_gap", "KNOWLEDGE_GAP", intent="EVENT_CONTEXT",
                          uncertainty="gap", actions=_viewer_action(db, "viewer_access"))

    # 4/typed workflows: viewer problems go to the deterministic support engine.
    if intent in VIEWER_FLOW_FOR_INTENT:
        flow = VIEWER_FLOW_FOR_INTENT[intent]
        return TurnResult("I can walk you through this one step at a time.", "routing",
                          session.state if session.state == "VIEWER_FLOW" else "ACTIVE",
                          actions=_viewer_action(db, flow), intent=intent)
    if intent in ("DEMO", "SALES"):
        ids = ["request-demo", "contact-sales"] if intent == "DEMO" else ["talk-to-sales-flow",
                                                                         "contact-sales"]
        msg = ("I can help you request a demo. I'll ask only for what the team needs to reply, "
               "and show you how it's used first." if intent == "DEMO" else
               "I can connect you with ZoikoStream sales. I'll ask only for what the team needs "
               "to reply, and show you how it's used first.")
        return TurnResult(msg, "routing", "ACTIVE", actions=_actions(db, ids), intent=intent)
    if intent == "HANDOFF":
        return TurnResult("Here's how to reach a person at ZoikoStream.", "routing",
                          "SUPPORT_HANDOFF",
                          actions=_actions(db, ["contact-support", "contact-sales",
                                                "viewer-support"]), intent=intent)
    if intent == "PRICING" and policy.is_discount_request(text):
        return TurnResult(DISCOUNT, "pricing", "ACTIVE",
                          actions=_actions(db, ["contact-sales", "request-demo"]),
                          intent=intent)

    # 6. Static public RAG.
    return _grounded_answer(db, session, text, intent, claims)


def _grounded_answer(db: Session, session: GuideSession, text: str, intent: str,
                     claims: dict | None) -> TurnResult:
    try:
        pack = retrieval.retrieve(db, text, intent, locale="en-US")
    except Exception:  # G2: retrieval down -> no factual answer from model memory
        log.exception("guide retrieval failed")
        db.rollback()
        return TurnResult(DEGRADED, "degraded", "DEGRADED", intent=intent,
                          actions=_actions(db, ["help-center", "status-page",
                                                "contact-support"]))
    # End the read transaction: nothing may be held open across the model call.
    db.commit()

    gap_actions = _actions(db, GAP_ACTIONS.get(intent, ["help-center", "contact-sales"]))
    if pack.answerability in ("NO_SOURCE", "STALE_HIGH_RISK", "SOURCE_CONFLICT"):
        return TurnResult(CONFLICT if pack.answerability == "SOURCE_CONFLICT" else GAP_REPLY,
                          "knowledge_gap", "KNOWLEDGE_GAP", actions=gap_actions,
                          intent=intent, uncertainty="gap", rag_release_id=pack.release_id,
                          reason=pack.answerability, suggestions=_suggestions(db, session))

    allowed = _actions(db, RAG_ACTIONS.get(intent, RAG_ACTIONS["PRODUCT_QA"]))
    high_risk = intent in retrieval.HIGH_RISK_INTENTS
    history = _history(db, session)
    db.commit()
    provider = llm.get_provider()
    feedback, failures = None, []
    for _attempt in range(2):  # §17.1: one regeneration with the failure summary
        prompt = prompts.build(
            user_text=text, history=history, intent=intent,
            profile=PROFILE_FOR_INTENT.get(intent, "P-QA"), pack=pack,
            allowed_actions=allowed, page_type="public",
            event_claims=claims if intent in ("CAPTIONS", "REPLAY") else None,
            validation_feedback=feedback)
        result = provider.generate(prompt)
        if result.status != "ok":
            log.warning("guide model %s (%s ms)", result.status, result.latency_ms)
            return TurnResult(DEGRADED, "degraded", "DEGRADED", intent=intent,
                              actions=_viewer_action(db, "viewer_other")
                              + _actions(db, ["help-center", "contact-support"]),
                              model_profile=result.profile, rag_release_id=pack.release_id,
                              reason=f"model_{result.status}")
        v = validator.validate(result.data, pack=pack,
                               allowed_action_ids=[a["action_id"] for a in allowed],
                               high_risk=high_risk)
        if v.ok:
            refs = pack.source_refs()
            gap = v.uncertainty == "gap"
            state = "KNOWLEDGE_GAP" if gap else "ACTIVE"
            answer = v.answer_text
            if gap and conversation.GAP_FOLLOW_UP not in answer:
                answer = f"{answer} {conversation.GAP_FOLLOW_UP}"
            return TurnResult(
                answer, "knowledge_gap" if gap else v.answer_class,
                state, grounded=v.grounded, sources=_public_sources(pack, v.source_ids),
                actions=[a for a in allowed if a["action_id"] in v.action_ids]
                or (gap_actions if v.uncertainty == "gap" else []),
                uncertainty=v.uncertainty, intent=intent,
                source_version_ids=[refs[s].source_version_id for s in v.source_ids],
                model_profile=result.profile, rag_release_id=pack.release_id,
                validation_failures=failures,
                suggestions=_suggestions(db, session) if gap else [])
        failures.extend(v.failures)
        feedback = "; ".join(v.failures)
        log.info("guide validator rejected draft: %s", feedback)

    # Template fallback: never expose an unvalidated draft.
    return TurnResult(GAP_REPLY, "knowledge_gap", "KNOWLEDGE_GAP", actions=gap_actions,
                      intent=intent, uncertainty="gap", rag_release_id=pack.release_id,
                      reason="validation_failed", validation_failures=failures,
                      suggestions=_suggestions(db, session))


# ── Entry point ────────────────────────────────────────────────────────────────────────────

def _replay(db: Session, user_msg: GuideMessage) -> dict:
    reply = db.scalar(select(GuideMessage).where(GuideMessage.in_reply_to == user_msg.message_id,
                                                 GuideMessage.role == "assistant"))
    if reply is None or reply.content_ciphertext is None:
        raise GuideProblem("GUIDE_STATE_CONFLICT",
                           "This message is still being answered. Please wait a moment.")
    stored = json.loads(crypto.decrypt(reply.content_ciphertext, context=CONTENT_CONTEXT))
    return {"user_message_id": str(user_msg.message_id),
            "assistant_message": stored["public"], "session_state": stored["session_state"],
            "replayed": True}


def handle_message(db: Session, request: Request, session: GuideSession, *,
                   client_message_id: uuid.UUID, text: str,
                   intent_hint: str | None) -> dict:
    existing = db.scalar(select(GuideMessage).where(
        GuideMessage.session_id == session.session_id,
        GuideMessage.client_message_id == client_message_id))
    if existing is not None:
        result = _replay(db, existing)
        db.commit()
        return result

    detection = detect(text)
    redacted = detection.text
    user_msg = GuideMessage(
        session_id=session.session_id, client_message_id=client_message_id, role="user",
        content_ciphertext=crypto.encrypt(redacted, context=CONTENT_CONTEXT),
        content_digest=crypto.digest(redacted), security_flags=detection.classes or None,
        purge_after=retention.purge_after("conversation"))
    db.add(user_msg)
    session_id = session.session_id
    db.commit()  # user turn durable before any slow work; session row lock released

    started = time.monotonic()
    with generation_slot(session_id):
        session = db.get(GuideSession, session_id)
        resume = _resume_offer(session)
        try:
            result = _route(db, session, redacted, intent_hint, detection.classes)
        except GuideProblem:
            raise
        except Exception:
            log.exception("guide turn failed")
            db.rollback()
            result = TurnResult(DEGRADED, "degraded", "DEGRADED", reason="turn_error",
                                actions=_actions(db, ["help-center", "status-page",
                                                      "contact-support"]))
        if resume and result.intent not in VIEWER_FLOW_FOR_INTENT:
            result.resume = resume

        latency = int((time.monotonic() - started) * 1000)
        session = db.get(GuideSession, session_id, with_for_update=True)
        if session.state == "ENDED":
            raise GuideProblem("GUIDE_SESSION_INVALID")
        assistant = GuideMessage(
            session_id=session_id, in_reply_to=user_msg.message_id, role="assistant",
            intent_class=result.intent, answer_class=result.answer_class,
            boundary_state=result.boundary, grounded=result.grounded,
            prompt_version=prompts.PROMPT_VERSION if result.model_profile else None,
            model_profile=result.model_profile, rag_release_id=result.rag_release_id,
            purge_after=retention.purge_after("conversation"))
        db.add(assistant)
        db.flush()
        public = result.public(str(assistant.message_id))
        assistant.content_ciphertext = crypto.encrypt(
            json.dumps({"text": result.text, "public": public,
                        "session_state": result.session_state}),
            context=CONTENT_CONTEXT)
        assistant.content_digest = crypto.digest(result.text)
        for i, svid in enumerate(result.source_version_ids):
            db.add(GuideMessageSource(message_id=assistant.message_id, ordinal=i,
                                      source_version_id=uuid.UUID(svid)))
        dests = registry.destinations(db)
        for i, a in enumerate(result.actions):
            if a["action_id"] in dests:
                db.add(GuideMessageAction(message_id=assistant.message_id, ordinal=i,
                                          action_id=a["action_id"], action_type=a["type"],
                                          destination_id=a["action_id"]))
        user_msg_row = db.get(GuideMessage, user_msg.message_id)
        user_msg_row.intent_class = result.intent
        user_msg_row.boundary_state = result.boundary
        # A paused typed flow keeps its state; the answer carries the resume offer instead.
        if (session.state not in ("VIEWER_FLOW", "LEAD_FLOW")
                or result.intent in VIEWER_FLOW_FOR_INTENT):
            session.state = result.session_state
        session.last_activity_at = retention.now()
        audit(db, request, "message_answered", session_id=session_id,
              intent=result.intent, answer_class=result.answer_class,
              boundary=result.boundary, reason=result.reason, grounded=result.grounded,
              source_count=len(result.sources), action_ids=[a["action_id"]
                                                            for a in result.actions],
              latency_bucket=_bucket(latency), model_profile=result.model_profile,
              prompt_version=prompts.PROMPT_VERSION if result.model_profile else None,
              rag_release_id=result.rag_release_id,
              validation_failures=[f.split(" ")[0] for f in result.validation_failures],
              secret_classes=detection.classes or None)
        db.commit()
        return {"user_message_id": str(user_msg.message_id), "assistant_message": public,
                "session_state": session.state}


def _bucket(ms: int) -> str:
    for limit in (500, 1000, 2500, 5000, 8000, 15000):
        if ms <= limit:
            return f"<={limit}ms"
    return ">15000ms"
