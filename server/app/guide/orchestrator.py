"""Turn orchestration for POST /sessions/{id}/messages (Conversation Flow §2; Architecture §5).

Order of checks, each able to end the turn (Conversation Flow §2.1):
  1 session/CSRF/rate (router)   2 secret detection   3 boundary (private/restricted)
  4 active typed workflow        5 dynamic status/event facts
  6 static public RAG -> model -> validator          7 knowledge gap / degraded fallback

Transaction discipline (Architecture §27, DB §15): the user message is committed BEFORE any
retrieval or model call, and no database transaction is held open across the model call. The
assistant message, its sources/actions and the session state are then written atomically.

One generation per session (API §15) is a Postgres advisory lock, so it holds across workers.
Generation is cancellable (Architecture §21.1, ARCH-AT-15): a cancelled turn writes no
assistant row, and a retry with the same client_message_id regenerates the answer for the
already-stored user message (or replays it once answered).

Content minimisation: the user's text is redacted before it is persisted, logged or sent to
the model; stored conversation text is ciphertext only; audit rows carry classes and ids.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from fastapi import Request
from sqlalchemy import Integer, func, select, text as sql_text
from sqlalchemy.orm import Session

from . import (
    breaker, conversation, crypto, event_answers, llm, metrics, policy, prompts, registry,
    retention, validator,
)
from . import db as guide_db
from . import status as public_status
from .errors import GuideProblem
from .knowledge import retrieval
from .knowledge.retrieval import EvidencePack
from .models import (
    GuideAuditEvent, GuideMessage, GuideMessageAction, GuideMessageSource, GuideSession,
    GuideSessionContextVersion,
)
from .secrets_detect import detect
from .sessions import audit, current_event_claims
from .settings import get_settings
from .support.flows import _start_time

log = logging.getLogger(__name__)

CONTENT_CONTEXT = "guide_messages.content"
HISTORY_TURNS = 3

# ── Canonical copy (Prompt Appendix A; Conversation Flow §28) ──────────────────────────────
# The live text comes from the ACTIVE policy release (prompt_versions/, prompts.active_policy);
# these module names are the baseline release, kept for callers and tests that compare text.
_BASE = prompts.load_policy(prompts.manifest()["default_policy"])
GAP = _BASE.t("gap")
# What visitors actually see on a gap: the canonical sentence, then what Guide CAN do.
GAP_REPLY = f"{GAP} {conversation.GAP_FOLLOW_UP}"
CONFLICT = _BASE.t("conflict")
STATUS_SOURCE = {"source_id": "public-status", "title": "ZoikoStream public status page",
                 "source_type": "status", "version": None, "updated_at": None,
                 "canonical_path": "/status"}
STATUS_UNKNOWN = _BASE.t("status_unknown")
DEGRADED = _BASE.t("degraded")
DISCOUNT = _BASE.t("discount")
PROMPT_EXTRACTION = _BASE.t("prompt_extraction")
PRICING_QUOTE = _BASE.t("pricing_quote")
CLARIFICATION = _BASE.t("clarification")
BUDGET_EXCEEDED = _BASE.t("budget_exceeded")
UNSUPPORTED_LOCALE = _BASE.t("unsupported_locale")
RESTRICTED = _BASE.restricted
SECRET_WARNINGS = _BASE.secret_warnings

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
DIRECT_ROUTES = ["help-center", "contact-support", "contact-sales"]

# Knowledge-gap topic codes (analytics; enum only, never the visitor's words).
MISSING_TOPIC = {"PRODUCT_QA": "product", "DISCOVERY": "use_case", "PRICING": "pricing",
                 "PRIVACY": "privacy", "SECURITY_TRUST": "security", "LEGAL": "legal",
                 "DEVELOPER": "developer", "EVENT_CONTEXT": "event_details",
                 "CAPTIONS": "captions", "REPLAY": "replay", "STATUS": "status"}

# C2 progress stages, in order.
PROGRESS_STAGES = ("retrieving", "drafting", "checking")


class GenerationCancelled(Exception):
    """The caller cancelled the turn (client disconnected). Nothing was persisted for it."""


# ── One active generation per session (API §15) ────────────────────────────────────────────
# In-process fast path + a Postgres advisory lock so the rule holds across workers/instances.
# The lock is transaction-scoped on a DEDICATED pooled connection (never the request's work
# session, which must not stay open across the model call): releasing it is just ending that
# transaction, so a crashed request or a connection returned to the pool can never leak it.
_inflight: set[uuid.UUID] = set()
_inflight_lock = threading.Lock()
_LOCK_NAMESPACE = b"zoikostream-guide:generation:"


def _advisory_key(session_id: uuid.UUID) -> int:
    digest = hashlib.sha256(_LOCK_NAMESPACE + session_id.bytes).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _busy() -> GuideProblem:
    return GuideProblem("GUIDE_STATE_CONFLICT", "Guide is still answering your previous message.")


@contextmanager
def generation_slot(session_id: uuid.UUID):
    with _inflight_lock:
        if session_id in _inflight:
            raise _busy()
        _inflight.add(session_id)
    conn = None
    try:
        engine = guide_db.get_engine()
        if engine.dialect.name == "postgresql":
            conn = engine.connect()
            got = conn.execute(sql_text("SELECT pg_try_advisory_xact_lock(:k)"),
                               {"k": _advisory_key(session_id)}).scalar()
            if not got:
                raise _busy()
        yield
    finally:
        if conn is not None:
            try:
                conn.rollback()  # ends the transaction => releases the advisory lock
            finally:
                conn.close()
        with _inflight_lock:
            _inflight.discard(session_id)


# ── Progress / cancellation (C2; Architecture §21.1 "abort generation when client
#    disconnects where safe") ─────────────────────────────────────────────────────────────

_model_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="guide-model")


class _Control:
    def __init__(self, on_progress: Callable[[str], None] | None,
                 cancel_event: threading.Event | None):
        self.on_progress = on_progress
        self.cancel_event = cancel_event
        self._emitted: set[str] = set()

    def check(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise GenerationCancelled()

    def stage(self, name: str) -> None:
        """Report a stage once, in PROGRESS_STAGES order. A callback raising
        GenerationCancelled cancels the turn; any other callback error is logged and ignored
        (progress is advisory and must never break an answer)."""
        self.check()
        if name in self._emitted or self.on_progress is None:
            return
        self._emitted.add(name)
        try:
            self.on_progress(name)
        except GenerationCancelled:
            raise
        except Exception:
            log.exception("guide on_progress callback failed")


def _generate(provider, prompt, ctl: _Control, model_breaker) -> llm.ModelResult:
    """One model call. Without a cancel signal it runs inline; with one it runs on a worker
    thread so a cancellation returns immediately (the abandoned call finishes in the
    background, its result discarded and only its breaker outcome kept)."""
    def report(result: llm.ModelResult) -> None:
        if result.status in ("error", "timeout"):
            model_breaker.failure()
        else:
            model_breaker.success()

    def call() -> llm.ModelResult:
        try:
            result = provider.generate(prompt)
        except Exception:
            log.exception("guide model provider raised")
            result = llm.ModelResult("error")
        result.profile = result.profile or getattr(provider, "profile", "")
        return result

    if ctl.cancel_event is None:
        result = call()
        report(result)
        return result
    future = _model_pool.submit(call)
    while True:
        try:
            result = future.result(timeout=0.1)
            break
        except FutureTimeout:
            if ctl.cancel_event.is_set():
                future.add_done_callback(lambda f: report(f.result()))
                raise GenerationCancelled() from None
    report(result)
    return result


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
    # C4: one canonical clarifying question with 2-4 option chips (CF §6.1, §29).
    clarification: dict | None = None
    # Viewer free text started a deterministic flow: its first step (CF §10.1).
    support_flow: dict | None = None
    # internal (never returned)
    intent: str = "UNKNOWN"
    boundary: str = "allowed_public"
    reason: str | None = None
    source_version_ids: list[str] = field(default_factory=list)
    model_profile: str | None = None
    rag_release_id: str | None = None
    validation_failures: list[str] = field(default_factory=list)
    prompt_version: str | None = None
    route_profile: str | None = None
    evidence_unit_ids: list[str] = field(default_factory=list)
    tokens_input: int = 0
    tokens_output: int = 0
    locale_detected: str = "en"

    def public(self, message_id: str) -> dict:
        body = {"message_id": message_id, "text": self.text, "answer_class": self.answer_class,
                "intent_class": self.intent.lower(),
                "grounded": self.grounded, "sources": self.sources, "actions": self.actions,
                "uncertainty": {"state": self.uncertainty,
                                "message": GAP if self.uncertainty == "gap"
                                and not self.text.startswith(GAP) else None}}
        if self.answer_class == "knowledge_gap":
            # Stable topic code for gap analytics — derived from the intent, never user text.
            body["missing_topic_code"] = MISSING_TOPIC.get(self.intent, "other")
        if self.notice:
            body["notice"] = self.notice
        if self.resume:
            body["resume"] = self.resume
        if self.suggestions:
            body["suggestions"] = self.suggestions
        if self.clarification:
            body["clarification"] = self.clarification
        if self.support_flow:
            body["support_flow"] = self.support_flow
        return body


@dataclass
class _Turn:
    """Per-turn inputs the routing steps share."""
    request: Request | None
    client_message_id: uuid.UUID
    policy: prompts.PolicyRelease
    ctl: _Control


# ── Helpers ────────────────────────────────────────────────────────────────────────────────

def _actions(db: Session, ids: list[str]) -> list[dict]:
    return [a for a in (registry.action_for(db, i) for i in ids) if a]


def _viewer_action(db: Session, flow_type: str) -> list[dict]:
    a = registry.action_for(db, "viewer-help", label=VIEWER_LABELS.get(flow_type))
    if not a:
        return []
    a["flow_type"] = flow_type
    return [a]


def _gap_reply(pol: prompts.PolicyRelease) -> str:
    return f"{pol.t('gap')} {conversation.GAP_FOLLOW_UP}"


def _degraded(db: Session, pol: prompts.PolicyRelease, intent: str, reason: str,
              **extra) -> TurnResult:
    return TurnResult(pol.t("degraded"), "degraded", "DEGRADED", intent=intent, reason=reason,
                      actions=_viewer_action(db, "viewer_other")
                      + _actions(db, ["help-center", "contact-support"]), **extra)


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
            src = {"source_id": e.source_id, "title": e.title,
                   "source_type": e.source_type, "version": e.version,
                   "updated_at": e.updated_at, "canonical_path": e.canonical_path}
            if _past_review(getattr(e, "review_due_at", None)):
                src["freshness"] = "stale"  # past its freshness window (still eligible)
            out.append(src)
    return out


def _past_review(due) -> bool:
    if not due:
        return False
    if isinstance(due, str):
        try:
            due = datetime.fromisoformat(due.replace("Z", "+00:00"))
        except ValueError:
            return False
    if isinstance(due, datetime) and due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if not isinstance(due, datetime):  # a bare date
        due = datetime(due.year, due.month, due.day, tzinfo=timezone.utc)
    return due < datetime.now(timezone.utc)


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


def _page_context(db: Session, session: GuideSession) -> tuple[str, str | None]:
    row = db.execute(select(GuideSessionContextVersion.page_type,
                            GuideSessionContextVersion.path).where(
        GuideSessionContextVersion.session_id == session.session_id,
        GuideSessionContextVersion.context_version == session.current_context_version)).first()
    return (row[0], row[1]) if row else ("home", None)


def _page_type(db: Session, session: GuideSession) -> str:
    return _page_context(db, session)[0]


def _suggestions(db: Session, session: GuideSession) -> list[dict]:
    """Approved quick-reply topics for this page (the same catalog as the welcome screen)."""
    return [{"intent_id": i["intent_id"], "label": i["label"]}
            for i in registry.suggested_intents(_page_type(db, session))][:4]


def _has_prior_answers(db: Session, session: GuideSession) -> bool:
    return db.scalar(select(GuideMessage.message_id).where(
        GuideMessage.session_id == session.session_id,
        GuideMessage.role == "assistant").limit(1)) is not None


def _last_exchange(db: Session, session: GuideSession) -> tuple[dict | None, str | None]:
    """(public payload of the latest assistant reply, the user text it answered)."""
    reply = db.scalar(select(GuideMessage).where(
        GuideMessage.session_id == session.session_id, GuideMessage.role == "assistant")
        .order_by(GuideMessage.created_at.desc()).limit(1))
    if reply is None or reply.content_ciphertext is None:
        return None, None
    try:
        public = json.loads(crypto.decrypt(reply.content_ciphertext,
                                           context=CONTENT_CONTEXT)).get("public")
    except (ValueError, AttributeError):
        return None, None
    question = None
    if reply.in_reply_to:
        asked = db.get(GuideMessage, reply.in_reply_to)
        if asked is not None and asked.content_ciphertext is not None:
            question = crypto.decrypt(asked.content_ciphertext, context=CONTENT_CONTEXT)
    return public, question


def _session_tokens(db: Session, session_id: uuid.UUID) -> int:
    """Model tokens this session has spent so far (summed from its own audit trail)."""
    total = GuideAuditEvent.metadata_["tokens_total"].astext.cast(Integer)
    return int(db.scalar(select(func.coalesce(func.sum(total), 0)).where(
        GuideAuditEvent.session_id == session_id,
        GuideAuditEvent.event_type == "message_answered")) or 0)


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
           secret_classes: list[str], turn: _Turn) -> TurnResult:
    pol = turn.policy
    claims = current_event_claims(db, session)

    # 2. Secrets: warn, offer the safe route, and do not forward to the model.
    if secret_classes:
        primary = secret_classes[0]
        actions = (_viewer_action(db, "viewer_access")
                   if primary in ("event_pin", "password", "secret_link")
                   else _actions(db, ["contact-support"]))
        return TurnResult(pol.secret_warnings[primary], "restricted", "RESTRICTED",
                          actions=actions, boundary="restricted", reason=f"secret:{primary}",
                          intent="RESTRICTED_PRIVATE")

    # 3. Boundary (on the normalised text: confusables/zero-width cannot hide a rule).
    boundary = policy.classify_boundary(text)
    if boundary.state != "allowed_public":
        reason = boundary.reason or "restricted"
        if reason == "prompt_extraction":
            msg, ids = pol.t("prompt_extraction"), ["help-center"]
        else:
            msg = pol.restricted.get(reason, pol.restricted["internal"])
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

    # 3c. Unsupported locale (PG-AT-28): en-US is the only approved locale pack, so no
    # localized legal/commercial answer is generated; the visitor gets the direct routes.
    locale = policy.detect_locale(text)
    if not locale.supported:
        return TurnResult(pol.t("unsupported_locale"), "routing", "ACTIVE",
                          actions=_actions(db, DIRECT_ROUTES), intent="UNSUPPORTED_LOCALE",
                          reason=f"locale:{locale.detected}", locale_detected=locale.detected)

    # 5a. Questions about the event being watched, answered from its verified context (D2).
    event_reply = event_answers.answer(text, claims)
    if event_reply and event_reply.grounded:
        return TurnResult(event_reply.text, "factual", "ACTIVE", grounded=True,
                          sources=[dict(event_answers.SOURCE)], intent="EVENT_CONTEXT")
    if event_reply:
        return TurnResult(event_reply.text, "knowledge_gap", "KNOWLEDGE_GAP",
                          intent="EVENT_CONTEXT", uncertainty="gap",
                          actions=_viewer_action(db, "viewer_access"))

    # A chip answer to the previous clarification continues that discovery question.
    followup = _clarification_followup(db, session, text)
    if followup:
        text, intent = followup, "DISCOVERY"
    else:
        intent = policy.classify_intent(text, intent_hint)
        if intent == "PRODUCT_QA" and len(text.split()) <= 8 \
                and policy.STATUS_FOLLOWUP.search(text):
            _prev, asked = _last_exchange(db, session)
            if asked and policy.classify_intent(asked) == "STATUS":
                intent = "STATUS"

    # 5. Dynamic facts: public status (D1) and validated event context (D2).
    if intent == "STATUS":
        # D1: the live public status feed, with freshness; never a guess (CF §17).
        db.commit()  # no transaction held across the network call
        live = public_status.current()
        if live is not None:
            return TurnResult(live.summary(), "factual", "ACTIVE", grounded=True,
                              sources=[dict(STATUS_SOURCE, updated_at=live.checked_at.isoformat()
                                            .replace("+00:00", "Z"))],
                              actions=_actions(db, ["status-page"]), intent=intent)
        return TurnResult(pol.t("status_unknown"), "routing", "ACTIVE",
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
        return _viewer_turn(db, session, intent, turn)
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
        return TurnResult(pol.t("discount"), "pricing", "ACTIVE",
                          actions=_actions(db, ["contact-sales", "request-demo"]),
                          intent=intent)
    if intent == "PRICING" and policy.is_quote_request(text):
        # CF §28 canonical pricing-quote copy: deterministic, never an estimate.
        return TurnResult(pol.t("pricing_quote"), "pricing", "ACTIVE",
                          actions=_actions(db, ["request-demo", "contact-sales"]),
                          intent=intent, reason="quote_required")
    if intent == "DISCOVERY" and not followup and policy.needs_clarification(text) \
            and not _clarification_pending(db, session):
        # CF §6.1: one material unknown blocks routing -> exactly ONE canonical question,
        # offered as chips (C4). Never a lead form, never a second question.
        return TurnResult(pol.t("clarification"), "discovery", "ACTIVE", intent=intent,
                          reason="clarification",
                          clarification={"question": pol.t("clarification"),
                                         "options": [dict(o) for o in
                                                     pol.clarification_options[:4]]})

    # 6. Static public RAG.
    return _grounded_answer(db, session, text, intent, claims, turn, boundary.state)


def _clarification_pending(db: Session, session: GuideSession) -> bool:
    public, _q = _last_exchange(db, session)
    return bool(public and public.get("clarification"))


def _clarification_followup(db: Session, session: GuideSession, text: str) -> str | None:
    """When the previous reply asked the canonical clarification and this message is one of
    its option labels, continue the ORIGINAL discovery question with that choice."""
    if len(text.split()) > 4:  # chip labels are short; skip the lookup for real questions
        return None
    public, question = _last_exchange(db, session)
    if not public or not public.get("clarification") or not question:
        return None
    labels = {o.get("label", "").strip().lower(): o.get("label")
              for o in public["clarification"].get("options", [])}
    choice = labels.get(text.strip().rstrip(".!").lower())
    return f"{question} ({choice})" if choice else None


def _viewer_turn(db: Session, session: GuideSession, intent: str, turn: _Turn) -> TurnResult:
    """CF §10.1: viewer free text starts the matching deterministic flow (ACTIVE ->
    VIEWER_FLOW) and returns its first step. Idempotent on client_message_id, so a retried
    message gets the same flow back. An already-active typed flow is never replaced from
    free text (C3 — the visitor confirms that explicitly); it gets the offer instead."""
    flow_type = VIEWER_FLOW_FOR_INTENT[intent]
    actions = _viewer_action(db, flow_type)
    msg = turn.policy.t("viewer_flow_started")
    if session.state not in ("VIEWER_FLOW", "LEAD_FLOW") and actions:
        from .support import engine as support_engine  # deferred: engine imports sessions
        try:
            first = support_engine.start_flow(db, turn.request, session, flow_type,
                                              client_request_id=turn.client_message_id)
        except GuideProblem:
            db.rollback()
        else:
            return TurnResult(msg, "routing", "VIEWER_FLOW", actions=actions, intent=intent,
                              support_flow=first)
    return TurnResult(msg, "routing",
                      session.state if session.state == "VIEWER_FLOW" else "ACTIVE",
                      actions=actions, intent=intent)


def _grounded_answer(db: Session, session: GuideSession, text: str, intent: str,
                     claims: dict | None, turn: _Turn, boundary_state: str) -> TurnResult:
    pol, ctl = turn.policy, turn.ctl
    ctl.stage("retrieving")
    try:
        pack = retrieval.retrieve(db, text, intent, locale="en-US")
    except Exception:  # G2: retrieval down -> no factual answer from model memory
        log.exception("guide retrieval failed")
        db.rollback()
        return TurnResult(pol.t("degraded"), "degraded", "DEGRADED", intent=intent,
                          reason="retrieval_error",
                          actions=_actions(db, ["help-center", "status-page",
                                                "contact-support"]))
    # End the read transaction: nothing may be held open across the model call.
    db.commit()
    ctl.check()

    gap_actions = _actions(db, GAP_ACTIONS.get(intent, ["help-center", "contact-sales"]))
    if pack.answerability in ("NO_SOURCE", "STALE_HIGH_RISK", "SOURCE_CONFLICT"):
        return TurnResult(pol.t("conflict") if pack.answerability == "SOURCE_CONFLICT"
                          else _gap_reply(pol),
                          "knowledge_gap", "KNOWLEDGE_GAP", actions=gap_actions,
                          intent=intent, uncertainty="gap", rag_release_id=pack.release_id,
                          reason=pack.answerability, suggestions=_suggestions(db, session))

    allowed = _actions(db, RAG_ACTIONS.get(intent, RAG_ACTIONS["PRODUCT_QA"]))
    if not registry.feature_enabled(db, "generation"):
        # G1: model generation off — deterministic flows and direct routes keep working.
        return _degraded(db, pol, intent, "generation_disabled", rag_release_id=pack.release_id)
    budget = get_settings().GUIDE_SESSION_TOKEN_BUDGET
    if budget > 0 and _session_tokens(db, session.session_id) >= budget:
        # Architecture §21.1 / Security §21: the session's model budget is spent. Generated
        # answers stop; viewer help and the direct routes remain (CF §23 limit notice).
        return TurnResult(pol.t("budget_exceeded"), "degraded", "RATE_LIMITED", intent=intent,
                          reason="session_token_budget",
                          actions=_viewer_action(db, "viewer_other")
                          + _actions(db, ["help-center", "contact-support"]),
                          rag_release_id=pack.release_id)
    model_breaker = breaker.get("model")
    if not model_breaker.allow():
        # Architecture §17.1: the model is known to be failing — degrade immediately.
        metrics.incr("guide_model_errors_total", provider="gateway", kind="circuit_open")
        return _degraded(db, pol, intent, "model_circuit_open", rag_release_id=pack.release_id)

    high_risk = intent in retrieval.HIGH_RISK_INTENTS
    history = _history(db, session)
    page_type, path = _page_context(db, session)
    workflow = {"support": "support", "lead": "lead"}.get(session.active_flow_kind or "",
                                                          "none")
    db.commit()
    release = prompts.active_prompt(db)
    profile = PROFILE_FOR_INTENT.get(intent, "P-QA")
    try:
        provider = llm.get_provider()
    except ValueError:
        log.exception("guide model provider unavailable")
        model_breaker.success()  # a configuration problem, not a provider outage
        return _degraded(db, pol, intent, "model_unavailable", rag_release_id=pack.release_id)
    common = dict(rag_release_id=pack.release_id, prompt_version=release.version,
                  evidence_unit_ids=[e.unit_id for e in pack.evidence])
    feedback, failures = None, []
    tokens_in = tokens_out = 0
    first = True
    for _attempt in range(2):  # §17.1: one regeneration with the failure summary
        if not first and not model_breaker.allow():
            break
        first = False
        prompt = prompts.build(
            user_text=text, history=history, intent=intent, profile=profile, pack=pack,
            allowed_actions=allowed, page_type=prompts.prompt_page_type(page_type, path),
            event_claims=claims if intent in ("CAPTIONS", "REPLAY") else None,
            validation_feedback=feedback, boundary_state=boundary_state,
            workflow_state=workflow, release=release, policy=pol)
        common["route_profile"] = prompt.route.name
        ctl.stage("drafting")
        result = _generate(provider, prompt, ctl, model_breaker)
        llm.record(result)
        tokens_in += result.input_tokens
        tokens_out += result.output_tokens
        ctl.check()
        if result.status != "ok":
            log.warning("guide model %s (%s ms)", result.status, result.latency_ms)
            return _degraded(db, pol, intent, f"model_{result.status}",
                             model_profile=result.profile, tokens_input=tokens_in,
                             tokens_output=tokens_out, **common)
        ctl.stage("checking")
        v = validator.validate(result.data, pack=pack,
                               allowed_action_ids=[a["action_id"] for a in allowed],
                               high_risk=high_risk, max_actions=pol.max_actions,
                               max_questions=pol.max_clarifying_questions)
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
                model_profile=result.profile, validation_failures=failures,
                suggestions=_suggestions(db, session) if gap else [],
                tokens_input=tokens_in, tokens_output=tokens_out, **common)
        for f in v.failures:
            metrics.incr("guide_validation_failures_total", check=f.split(" ")[0])
        failures.extend(v.failures)
        feedback = "; ".join(v.failures)
        log.info("guide validator rejected draft: %s", feedback)

    # Template fallback: never expose an unvalidated draft.
    return TurnResult(_gap_reply(pol), "knowledge_gap", "KNOWLEDGE_GAP", actions=gap_actions,
                      uncertainty="gap", intent=intent, reason="validation_failed",
                      validation_failures=failures, suggestions=_suggestions(db, session),
                      model_profile=result.profile, tokens_input=tokens_in,
                      tokens_output=tokens_out, **common)


# ── Entry point ────────────────────────────────────────────────────────────────────────────

def _reply_to(db: Session, user_msg: GuideMessage) -> GuideMessage | None:
    return db.scalar(select(GuideMessage).where(GuideMessage.in_reply_to == user_msg.message_id,
                                                GuideMessage.role == "assistant"))


def _replay(user_msg: GuideMessage, reply: GuideMessage) -> dict:
    if reply.content_ciphertext is None:
        raise GuideProblem("GUIDE_STATE_CONFLICT",
                           "This message is still being answered. Please wait a moment.")
    stored = json.loads(crypto.decrypt(reply.content_ciphertext, context=CONTENT_CONTEXT))
    return {"user_message_id": str(user_msg.message_id),
            "assistant_message": stored["public"], "session_state": stored["session_state"],
            "replayed": True}


def handle_message(db: Session, request: Request, session: GuideSession, *,
                   client_message_id: uuid.UUID, text: str, intent_hint: str | None,
                   on_progress: Callable[[str], None] | None = None,
                   cancel_event: threading.Event | None = None) -> dict:
    """Answer one visitor message.

    on_progress (C2): called with "retrieving", "drafting", "checking" (in that order, each
    at most once) on the generated-answer path; deterministic turns finish without stages.
    It runs on the calling thread; raise GenerationCancelled from it to cancel.
    cancel_event: set it (from any thread) to cancel; the call then raises
    GenerationCancelled promptly, even mid model call, and persists no assistant row.
    """
    ctl = _Control(on_progress, cancel_event)
    session_id = session.session_id
    existing = db.scalar(select(GuideMessage).where(
        GuideMessage.session_id == session_id,
        GuideMessage.client_message_id == client_message_id))
    if existing is not None:
        reply = _reply_to(db, existing)
        if reply is not None:
            result = _replay(existing, reply)
            db.commit()
            return result

    # The slot is taken BEFORE the user turn is written: a 409 leaves nothing behind.
    with generation_slot(session_id):
        if existing is not None:
            # Same client_message_id, never answered (a cancelled or crashed turn): answer
            # the stored, already-redacted user turn now — unless it was answered meanwhile.
            db.refresh(existing)
            reply = _reply_to(db, existing)
            if reply is not None:
                result = _replay(existing, reply)
                db.commit()
                return result
            user_msg = existing
            redacted = crypto.decrypt(existing.content_ciphertext, context=CONTENT_CONTEXT)
            classes = list(existing.security_flags or [])
        else:
            detection = detect(text)
            redacted, classes = detection.text, detection.classes
            user_msg = GuideMessage(
                session_id=session_id, client_message_id=client_message_id, role="user",
                content_ciphertext=crypto.encrypt(redacted, context=CONTENT_CONTEXT),
                content_digest=crypto.keyed_hash(redacted,
                                                 purpose=crypto.PURPOSE_MESSAGE_CONTENT),
                security_flags=classes or None,
                purge_after=retention.purge_after("conversation"))
            db.add(user_msg)
            if classes:
                # Security §7: a pasted credential-like value rotates the session capability.
                from .sessions import on_secret_detected
                on_secret_detected(db, request, session, classes)
        db.commit()  # user turn durable before any slow work; session row lock released

        started = time.monotonic()
        session = db.get(GuideSession, session_id)
        resume = _resume_offer(session)
        turn = _Turn(request, client_message_id, prompts.active_policy(db), ctl)
        try:
            result = _route(db, session, redacted, intent_hint, classes, turn)
        except (GuideProblem, GenerationCancelled):
            db.rollback()
            raise
        except Exception:
            log.exception("guide turn failed")
            db.rollback()
            result = TurnResult(turn.policy.t("degraded"), "degraded", "DEGRADED",
                                reason="turn_error",
                                actions=_actions(db, ["help-center", "status-page",
                                                      "contact-support"]))
        if resume and result.intent not in VIEWER_FLOW_FOR_INTENT:
            result.resume = resume
        ctl.check()  # last point a cancel can stop the turn: nothing below is half-written

        latency = int((time.monotonic() - started) * 1000)
        session = db.get(GuideSession, session_id, with_for_update=True)
        db.refresh(session)
        if session.state == "ENDED":
            raise GuideProblem("GUIDE_SESSION_INVALID")
        assistant = GuideMessage(
            session_id=session_id, in_reply_to=user_msg.message_id, role="assistant",
            intent_class=result.intent[:32], answer_class=result.answer_class,
            boundary_state=result.boundary, grounded=result.grounded,
            prompt_version=result.prompt_version if result.model_profile else None,
            model_profile=(result.model_profile or None) and result.model_profile[:32],
            rag_release_id=result.rag_release_id,
            purge_after=retention.purge_after("conversation"))
        db.add(assistant)
        db.flush()
        public = result.public(str(assistant.message_id))
        assistant.content_ciphertext = crypto.encrypt(
            json.dumps({"text": result.text, "public": public,
                        "session_state": result.session_state}),
            context=CONTENT_CONTEXT)
        assistant.content_digest = crypto.keyed_hash(result.text,
                                                     purpose=crypto.PURPOSE_MESSAGE_CONTENT)
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
        user_msg_row.intent_class = result.intent[:32]
        user_msg_row.boundary_state = result.boundary
        # A paused typed flow keeps its state; the answer carries the resume offer instead.
        if (session.state not in ("VIEWER_FLOW", "LEAD_FLOW")
                or result.intent in VIEWER_FLOW_FOR_INTENT):
            session.state = result.session_state
        session.last_activity_at = retention.now()
        tokens_total = result.tokens_input + result.tokens_output
        audit(db, request, "message_answered", session_id=session_id,
              intent=result.intent, answer_class=result.answer_class,
              boundary=result.boundary, reason=result.reason, grounded=result.grounded,
              source_count=len(result.sources), action_ids=[a["action_id"]
                                                            for a in result.actions],
              latency_bucket=_bucket(latency), model_profile=result.model_profile,
              route_profile=result.route_profile,
              prompt_version=result.prompt_version if result.model_profile else None,
              policy_version=turn.policy.version,
              rag_release_id=result.rag_release_id,
              # RAG §15.1: the exact evidence units supplied to the model (internal ids).
              evidence_unit_ids=result.evidence_unit_ids or None,
              tokens_input=result.tokens_input or None,
              tokens_output=result.tokens_output or None,
              tokens_total=tokens_total or None,
              locale_class="en-US" if result.locale_detected == "en" else "unsupported",
              locale_detected=result.locale_detected,
              validation_failures=[f.split(" ")[0] for f in result.validation_failures],
              secret_classes=classes or None)
        db.commit()
        metrics.incr("guide_answers_total", state=result.session_state.lower())
        return {"user_message_id": str(user_msg.message_id), "assistant_message": public,
                "session_state": session.state}


def _bucket(ms: int) -> str:
    for limit in (500, 1000, 2500, 5000, 8000, 15000):
        if ms <= limit:
            return f"<={limit}ms"
    return ">15000ms"
