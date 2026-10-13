"""Public Guide HTTP surface, mounted at /api/v1/public-guide (API §2).

Phase 0–2 operations: bootstrap, session create / context / end, and the deterministic
viewer-support flows. Message, consent, lead, handoff and feedback operations are added by
later phases; until then they do not exist (404), they are never stubbed to claim success.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from . import handoffs, leads, orchestrator, purge, ratelimit, registry, sessions
from .db import get_guide_db
from .errors import GuideProblem
from .models import GuideSession
from .schemas import (
    ConsentPurpose, ConsentPut, ContextUpdate, FeedbackSubmit, HandoffRequest, LeadFlowStart,
    LeadFlowUpdate, LeadSubmit, MessageSubmit, PageType, SessionCreate, SupportFlowStart,
    SupportStepSubmit,
)
from .settings import get_settings
from .support import engine

log = logging.getLogger(__name__)

router = APIRouter()

API_VERSION = "1.0"
ASSISTANT = {"name": "Guide", "descriptor": "Public AI Assistant"}
WELCOME = ("Welcome to ZoikoStream Guide. I can help with product questions, pricing and "
           "demos, or viewer support.")
# Routes the degraded/unavailable UI shows; they work without Guide (PR-041, WF-15).
DIRECT_ROUTE_IDS = ("help-center", "status-page", "contact-support", "sign-in")


# ── Availability (kill switch) ─────────────────────────────────────────────────────────────

def availability(db: Session | None) -> tuple[bool, str | None]:
    s = get_settings()
    if not s.GUIDE_ENABLED:
        return False, "disabled"
    if s.production_problems():
        return False, "misconfigured"
    if db is not None:
        try:
            if registry.db_kill_switch_engaged(db):
                return False, "disabled"
        except Exception as exc:  # DB unreachable: Guide is degraded, the site is not.
            log.warning("guide runtime config unavailable: %s", type(exc).__name__)
            db.rollback()
            return False, "degraded"
    return True, None


def require_available(db: Session = Depends(get_guide_db)) -> Session:
    ok, _ = availability(db)
    if not ok:
        raise GuideProblem("GUIDE_UNAVAILABLE",
                           "Guide is unavailable right now. You can still use the Help Center, "
                           "status page, or contact support directly.")
    return db


def _direct_routes(db: Session | None) -> list[dict]:
    if db is None:
        return []
    try:
        return [a for a in (registry.action_for(db, d) for d in DIRECT_ROUTE_IDS) if a]
    except Exception:
        db.rollback()
        return []


# ── Bootstrap ──────────────────────────────────────────────────────────────────────────────

@router.get("/bootstrap", operation_id="getBootstrap", tags=["bootstrap"])
def bootstrap(request: Request,
              page_id: str = Query("home", max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$"),
              page_type: PageType = Query("home"),
              locale: str = Query("en-US", max_length=16, pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$"),
              db: Session = Depends(get_guide_db)):
    """Public presentation config. Creates NO session, cookie, record or profile
    (API §6 "NO SILENT SESSION", API-AT-01)."""
    s = get_settings()
    ratelimit.enforce(request, "bootstrap", ratelimit.client_key(request),
                      s.GUIDE_RL_BOOTSTRAP_PER_MIN)
    enabled, reason = availability(db)
    body = {
        "api_version": API_VERSION,
        "assistant": ASSISTANT,
        "enabled": enabled,
        "locale": "en-US",  # launch locale pack (PRD §14); other locales fall back
        "welcome": WELCOME if enabled else None,
        "suggested_intents": registry.suggested_intents(page_type) if enabled else [],
        "limits": {"max_message_chars": s.GUIDE_MAX_MESSAGE_CHARS,
                   "max_concurrent_requests": 1},
        "privacy": {"privacy_notice_href": "/privacy", "terms_href": "/trust"},
        "direct_routes": _direct_routes(db) if reason != "degraded" else [],
    }
    if not enabled:
        body["unavailable_reason"] = "degraded" if reason == "degraded" else "disabled"
    etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    # Short public caching is allowed for bootstrap only (API §4).
    return JSONResponse(body, headers={"ETag": etag, "Cache-Control": "public, max-age=60"})


# ── Sessions ───────────────────────────────────────────────────────────────────────────────

def _session_body(db: Session, session: GuideSession) -> dict:
    s = get_settings()
    claims = sessions.current_event_claims(db, session)
    event = None
    if claims:
        # Only what the viewer card shows (WF-10 "Public context": event, status, public start
        # time); the rest stays server-side for support flows.
        event = {k: claims[k] for k in ("event_display_name", "public_status", "public_start_at",
                                        "public_timezone") if k in claims}
    return {
        "session_id": str(session.session_id),
        "session_state": session.state,
        "expires_at": session.expires_at.isoformat().replace("+00:00", "Z"),
        "idle_timeout_seconds": s.GUIDE_SESSION_IDLE_MINUTES * 60,
        "context_version": session.current_context_version,
        "event_context": event,
    }


@router.post("/sessions", status_code=201, operation_id="createSession", tags=["sessions"])
def create_session(body: SessionCreate, request: Request, response: Response,
                   background: BackgroundTasks, db: Session = Depends(require_available)):
    s = get_settings()
    sessions.check_origin(request)
    ratelimit.enforce(request, "session_create", ratelimit.client_key(request),
                      s.GUIDE_RL_SESSION_CREATE_PER_MIN)
    session, capability, csrf = sessions.create_session(db, request, body)
    sessions.set_session_cookie(response, capability)
    background.add_task(purge.run_if_due)  # at most hourly per process; after the response
    page_type = body.page_context.page_type
    return {
        **_session_body(db, session),
        "csrf_token": csrf,
        "assistant": ASSISTANT,
        "welcome": WELCOME,
        "suggested_intents": registry.suggested_intents(page_type),
        "privacy": {"privacy_notice_href": "/privacy"},
    }


@router.patch("/sessions/{session_id}/context", operation_id="updateSessionContext",
              tags=["sessions"])
def update_context(session_id: uuid.UUID, body: ContextUpdate, request: Request,
                   db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    sessions.update_context(db, request, session, body.page_context)
    db.commit()
    return {**_session_body(db, session),
            "suggested_intents": registry.suggested_intents(body.page_context.page_type)}


@router.post("/sessions/{session_id}/end", status_code=204, operation_id="endSession",
             tags=["sessions"])
def end_session(session_id: uuid.UUID, request: Request,
                db: Session = Depends(get_guide_db)):
    # Deliberately NOT gated on availability: a visitor must always be able to end their
    # conversation, kill switch or not.
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    sessions.end_session(db, request, session, "user_ended")
    db.commit()
    resp = Response(status_code=204)
    sessions.clear_session_cookie(resp)
    return resp


# ── Messages ───────────────────────────────────────────────────────────────────────────────

def _sse(events: list[tuple[str, dict]]):
    for name, data in events:
        yield f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _chunks(text: str, size: int = 48) -> list[str]:
    """Word-boundary chunks for text deltas. The answer was validated in full before the
    first byte is sent (Prompt §17.1), so streaming never exposes an unvalidated draft."""
    out, current = [], ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if len(candidate) > size and current:
            out.append(current + " ")
            current = word
        else:
            current = candidate
    if current:
        out.append(current)
    return out


@router.post("/sessions/{session_id}/messages", operation_id="submitMessage",
             tags=["messages"])
def submit_message(session_id: uuid.UUID, body: MessageSubmit, request: Request,
                   db: Session = Depends(require_available)):
    s = get_settings()
    if len(body.text) > s.GUIDE_MAX_MESSAGE_CHARS:
        raise GuideProblem("GUIDE_PAYLOAD_TOO_LARGE",
                           f"Messages can be up to {s.GUIDE_MAX_MESSAGE_CHARS} characters.")
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    ratelimit.enforce(request, "messages", str(session.session_id), s.GUIDE_RL_MESSAGES_PER_MIN)
    result = orchestrator.handle_message(db, request, session,
                                         client_message_id=body.client_message_id,
                                         text=body.text, intent_hint=body.intent_hint)
    payload = {"request_id": request.state.guide_request_id, "session_id": str(session_id),
               **result}
    if "text/event-stream" not in request.headers.get("accept", ""):
        return payload

    msg = result["assistant_message"]
    events: list[tuple[str, dict]] = [("response.started", {
        "request_id": request.state.guide_request_id,
        "assistant_message_id": msg["message_id"]})]
    events += [("response.text.delta", {"delta": d}) for d in _chunks(msg["text"])]
    events += [("response.source", src) for src in msg["sources"]]
    events += [("response.action", a) for a in msg["actions"]]
    events.append(("response.completed", {
        "grounded": msg["grounded"], "answer_class": msg["answer_class"],
        "uncertainty": msg["uncertainty"], "session_state": result["session_state"],
        "notice": msg.get("notice"), "resume": msg.get("resume"),
        "suggestions": msg.get("suggestions", [])}))
    return StreamingResponse(_sse(events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ── Consent, leads, handoffs, feedback ─────────────────────────────────────────────────────

@router.put("/sessions/{session_id}/consents/{purpose}", operation_id="putConsent",
            tags=["consent"])
def put_consent(session_id: uuid.UUID, purpose: ConsentPurpose, body: ConsentPut,
                request: Request, db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    consent = leads.record_consent(db, request, session, purpose, status=body.status,
                                   notice_version=body.notice_version, source=body.source)
    db.commit()
    return leads.consent_body(consent)


@router.post("/sessions/{session_id}/lead-flows", status_code=201,
             operation_id="startLeadFlow", tags=["leads"])
def start_lead_flow(session_id: uuid.UUID, body: LeadFlowStart, request: Request,
                    db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    flow = leads.start_flow(db, request, session, body.flow_kind, body.source_intent)
    db.commit()
    return leads.render(db, flow)


@router.patch("/sessions/{session_id}/lead-flows/{flow_id}", operation_id="updateLeadFlow",
              tags=["leads"])
def update_lead_flow(session_id: uuid.UUID, flow_id: uuid.UUID, body: LeadFlowUpdate,
                     request: Request, db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    flow = leads.load_flow(db, session, flow_id)
    if body.cancel:
        leads.cancel_flow(db, request, session, flow)
        db.commit()
        return {"flow_id": str(flow.flow_id), "status": flow.status}
    errors = leads.update_fields(db, request, session, flow, body.fields)
    db.commit()
    rendered = leads.render(db, flow)
    if errors:
        raise GuideProblem("GUIDE_VALIDATION_ERROR", "One or more fields need attention.",
                           field_errors=errors, extra={"current": rendered})
    return rendered


@router.post("/sessions/{session_id}/lead-flows/{flow_id}/submit",
             operation_id="submitLeadFlow", tags=["leads"])
def submit_lead_flow(session_id: uuid.UUID, flow_id: uuid.UUID, body: LeadSubmit,
                     request: Request, idempotency_key: str = Header("", alias="Idempotency-Key"),
                     db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    status, payload = leads.submit(db, request, session, flow_id,
                                   idempotency_key=idempotency_key,
                                   consent_receipt_id=body.consent_receipt_id)
    return JSONResponse(payload, status_code=status)


@router.post("/sessions/{session_id}/handoffs", operation_id="createHandoff",
             tags=["handoffs"])
def create_handoff(session_id: uuid.UUID, body: HandoffRequest, request: Request,
                   idempotency_key: str = Header("", alias="Idempotency-Key"),
                   db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    result = handoffs.resolve_handoff(db, request, session, target=body.target,
                                      reason_code=body.reason_code,
                                      idempotency_key=idempotency_key)
    db.commit()
    return result


@router.post("/sessions/{session_id}/feedback", status_code=202,
             operation_id="submitFeedback", tags=["feedback"])
def submit_feedback(session_id: uuid.UUID, body: FeedbackSubmit, request: Request,
                    db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    handoffs.record_feedback(db, request, session, client_feedback_id=body.client_feedback_id,
                             target_type=body.target_type, target_id=body.target_id,
                             rating=body.rating, reason_code=body.reason_code,
                             comment=body.comment)
    db.commit()
    return Response(status_code=202)


# ── Viewer support (deterministic) ─────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/support-flows", status_code=201,
             operation_id="startSupportFlow", tags=["support"])
def start_support_flow(session_id: uuid.UUID, body: SupportFlowStart, request: Request,
                       db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    return engine.start_flow(db, request, session, body.flow_type)


@router.post("/sessions/{session_id}/support-flows/{flow_id}/steps",
             operation_id="submitSupportStep", tags=["support"])
def submit_support_step(session_id: uuid.UUID, flow_id: uuid.UUID, body: SupportStepSubmit,
                        request: Request, db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    return engine.submit_step(db, request, session, flow_id,
                              client_step_id=body.client_step_id, step_id=body.step_id,
                              option_id=body.option_id, command=body.command)
