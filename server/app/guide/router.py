"""Public Guide HTTP surface, mounted at /api/v1/public-guide (API §2).

Every operation of the v1 contract (API §2, App. A) plus the lead draft (C6) and the
operational health/metrics routes (C8, health.py). Nothing is stubbed to claim success.

OpenAPI (API §22): each operation declares its operationId, tags, request schema, every
documented success/error response with examples (openapi/docs.py), the session cookie as an
apiKey-in-cookie security scheme and X-ZS-CSRF as an explicit header parameter. The declared
cookie/header are documentation only — the binding itself is enforced in
sessions.load_session, so a missing header keeps answering 401, never a schema 400.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import secrets
import threading
import uuid

from fastapi import (
    APIRouter, BackgroundTasks, Body, Depends, Header, Query, Request, Response, Security,
)
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyCookie
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from . import (
    crypto, handoffs, health, lead_drafts, leads, metrics, orchestrator, purge, ratelimit,
    registry, secrets_detect, sessions,
)
from . import db as guide_db
from .db import get_guide_db
from .errors import CODES, GuideProblem
from .models import GuideSession
from .openapi import docs
from .schemas import (
    ConsentPurpose, ConsentPut, ContextUpdate, FeedbackSubmit, HandoffRequest, LeadFlowStart,
    LeadFlowUpdate, LeadSubmit, MessageSubmit, PageType, SessionCreate, SupportFlowStart,
    SupportStepSubmit,
)
from .settings import get_settings
from .support import engine

log = logging.getLogger(__name__)

router = APIRouter()
for _path, _endpoint in health.ROUTES:  # C8 operational routes; not part of the v1 contract
    router.add_api_route(_path, _endpoint, methods=["GET"], include_in_schema=False)

API_VERSION = "1.0"
ASSISTANT = {"name": "Guide", "descriptor": "Public AI Assistant"}
WELCOME = ("Welcome to ZoikoStream Guide. I can help with product questions, pricing and "
           "demos, or viewer support.")
# Routes the degraded/unavailable UI shows; they work without Guide (PR-041, WF-15).
DIRECT_ROUTE_IDS = ("help-center", "status-page", "contact-support", "sign-in")


# ── Session binding, as declared in the OpenAPI document (API §3, §22) ────────────────────

session_cookie = APIKeyCookie(
    name=sessions.COOKIE_NAME, auto_error=False, scheme_name="GuideSessionCookie",
    description="Opaque anonymous session capability set by POST /sessions (Secure, "
                "HttpOnly, SameSite=Lax). Not an identity; contains no PII.")


def session_binding(
        _cookie: str | None = Security(session_cookie),
        _csrf: str | None = Header(
            None, alias="X-ZS-CSRF",
            description="Per-session CSRF token returned by POST /sessions. Required on "
                        "every mutating session route.")) -> None:
    """Documentation-only dependency; sessions.load_session performs the actual check."""


SESSION = [Depends(session_binding)]
IDEMPOTENCY_KEY_DOC = "Required; 16-128 characters; unique per intended operation (API App. B)."


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


# Capabilities switched off at runtime (G3) answer with the routes that still work, which the
# widget shows verbatim (C7, PR-041). Lead requests always keep the contact-sales route.
PAUSED = {
    "leads": ("Requests through Guide are paused right now. You can still reach the "
              "ZoikoStream team through the contact page.",
              ("contact-sales", "contact-support")),
    "viewer_support": ("Viewer help in Guide is paused right now. Viewer support, the Help "
                       "Center and the status page are still available directly.",
                       ("viewer-support", "help-center", "status-page")),
}


def paused_payload(db: Session, feature: str) -> dict:
    message, route_ids = PAUSED[feature]
    routes = [a for a in (registry.action_for(db, d) for d in route_ids) if a]
    return {"feature": feature, "message": message, "routes": routes}


def require_feature(db: Session, feature: str) -> None:
    if not registry.feature_enabled(db, feature):
        paused = paused_payload(db, feature)
        raise GuideProblem("GUIDE_UNAVAILABLE", paused["message"], extra={"paused": paused})


# ── Bootstrap ──────────────────────────────────────────────────────────────────────────────

@router.get("/bootstrap", operation_id="getBootstrap", tags=["bootstrap"],
            responses=docs.responses("getBootstrap"))
def bootstrap(request: Request,
              page_id: str = Query("home", max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$"),
              page_type: PageType = Query("home"),
              locale: str = Query("en-US", max_length=16, pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$"),
              db: Session = Depends(get_guide_db)):
    """Public presentation config. Creates NO session, cookie, record or profile
    (API §6 "NO SILENT SESSION", API-AT-01).

    Kill switch: answers 200 with `enabled: false` plus the direct routes, not 503. API §6
    makes bootstrap the call that "determines whether Guide is enabled", and PR-041 needs
    the direct routes in that answer; App. A's 503 remains for a failure to answer at all."""
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


def _replayed_create(db: Session, request: Request, body: SessionCreate) -> tuple[
        GuideSession, str, str] | None:
    """API §16: "Repeated create may return same active session or safe duplicate result
    within nonce window." It is SAFE only for the client that already holds the session's
    capability cookie (its browser received the first response's Set-Cookie but lost the
    body): that caller gets the same session back with a freshly rotated CSRF token — the
    original raw token is never stored, so it cannot be re-sent. Anyone else replaying the
    nonce still gets 409, so knowing a nonce never grants a session."""
    capability = request.cookies.get(sessions.COOKIE_NAME)
    if not capability:
        return None
    nonce_hash = crypto.keyed_hash(str(body.client_session_nonce), purpose="session-nonce")
    session = db.scalar(select(GuideSession).where(
        GuideSession.client_nonce_hash == nonce_hash).with_for_update())
    if session is None or session.state == "ENDED" or not crypto.constant_time_equal(
            session.capability_hash,
            crypto.keyed_hash(capability, purpose="session-capability")):
        return None
    if sessions._expired(session):
        return None
    csrf = secrets.token_urlsafe(32)
    session.csrf_hash = crypto.keyed_hash(csrf, purpose="session-csrf")
    sessions.audit(db, request, "session_create_replayed", session_id=session.session_id)
    db.commit()
    return session, capability, csrf


@router.post("/sessions", status_code=201, operation_id="createSession", tags=["sessions"],
             responses=docs.responses("createSession"))
def create_session(body: SessionCreate, request: Request, response: Response,
                   background: BackgroundTasks, db: Session = Depends(require_available)):
    s = get_settings()
    sessions.check_origin(request)
    ratelimit.enforce(request, "session_create", ratelimit.client_key(request),
                      s.GUIDE_RL_SESSION_CREATE_PER_MIN)
    replay = _replayed_create(db, request, body)
    if replay is not None:
        session, capability, csrf = replay
    else:
        session, capability, csrf = sessions.create_session(db, request, body)
        background.add_task(purge.run_if_due)  # at most hourly per process; after response
    sessions.set_session_cookie(response, capability)
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
              tags=["sessions"], dependencies=SESSION,
              responses=docs.responses("updateSessionContext"))
def update_context(session_id: uuid.UUID, body: ContextUpdate, request: Request,
                   db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    sessions.update_context(db, request, session, body.page_context)
    db.commit()
    return {**_session_body(db, session),
            "suggested_intents": registry.suggested_intents(body.page_context.page_type)}


@router.post("/sessions/{session_id}/end", status_code=204, operation_id="endSession",
             tags=["sessions"], dependencies=SESSION, responses=docs.responses("endSession"))
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

def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


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


_COMPLETED_EXCLUDED = ("message_id", "text", "sources", "actions")


def _answer_events(payload: dict) -> list[str]:
    msg = payload["assistant_message"]
    out = [_event("response.text.delta", {"delta": d}) for d in _chunks(msg["text"])]
    out += [_event("response.source", src) for src in msg["sources"]]
    out += [_event("response.action", a) for a in msg["actions"]]
    completed = {"assistant_message_id": msg["message_id"],
                 "user_message_id": payload.get("user_message_id"),
                 "grounded": msg["grounded"], "answer_class": msg["answer_class"],
                 "uncertainty": msg["uncertainty"], "session_state": payload["session_state"],
                 "notice": msg.get("notice"), "resume": msg.get("resume"),
                 "suggestions": msg.get("suggestions", [])}
    # Optional answer fields (clarification C4, paused C7, ...) pass through unchanged.
    for key, value in msg.items():
        if key not in completed and key not in _COMPLETED_EXCLUDED:
            completed[key] = value
    if payload.get("replayed"):
        completed["replayed"] = True
    out.append(_event("response.completed", completed))
    return out


class _Cancelled(BaseException):
    """Fallback cancellation signal if the orchestrator has no GenerationCancelled. A
    BaseException so an `except Exception` degraded-answer fallback cannot store it."""


# C2: the orchestrator's own signal, which it handles cancellation-safely (no assistant row,
# the user turn kept so a retry with the same client_message_id is answered).
GenerationCancelled = getattr(orchestrator, "GenerationCancelled", _Cancelled)


def _turn_kwargs(on_progress, cancel_event: threading.Event) -> dict:
    """Pass the C2 hooks only if this orchestrator accepts them."""
    try:
        params = inspect.signature(orchestrator.handle_message).parameters
    except (TypeError, ValueError):
        return {}
    kwargs = {}
    if "on_progress" in params:
        kwargs["on_progress"] = on_progress
    if "cancel_event" in params:
        kwargs["cancel_event"] = cancel_event
    return kwargs


def _error_data(code: str) -> dict:
    return {"code": code, "retryable": CODES[code][3]}


async def _stream_answer(request: Request, session_id: uuid.UUID, body: MessageSubmit):
    """SSE contract (API §9; ARCH §18, §21.1; C2; API-AT-17).

    response.started goes out at once, then response.progress per orchestrator stage
    (retrieving / drafting / checking), an SSE comment heartbeat whenever a stage runs longer
    than GUIDE_SSE_HEARTBEAT_SECONDS, then the validated answer (text deltas, sources,
    actions, response.completed) — or exactly one response.error with a stable code. The
    assistant message id is known only once the answer is stored, so response.started
    carries null and response.completed carries the id.

    The turn runs in a worker thread with its own DB session. When the client disconnects,
    the orchestrator's cancel_event is set (and the progress hook raises
    GenerationCancelled): the turn stops — even mid model call — and stores no answer for a
    stream nobody reads, and a retry with the same client_message_id is answered normally
    (C2)."""
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()
    cancelled = threading.Event()
    rid = request.state.guide_request_id

    def emit(kind: str, value=None) -> None:
        try:
            loop.call_soon_threadsafe(events.put_nowait, (kind, value))
        except RuntimeError:  # loop already closed: the stream is gone
            pass

    def on_progress(stage: str) -> None:
        if cancelled.is_set():
            raise GenerationCancelled()
        emit("progress", stage)

    def work() -> None:
        db = guide_db.new_session()
        try:
            session = db.get(GuideSession, session_id)
            if session is None:
                raise GuideProblem("GUIDE_SESSION_INVALID")
            kwargs = _turn_kwargs(on_progress, cancelled)
            result = orchestrator.handle_message(
                db, request, session, client_message_id=body.client_message_id,
                text=body.text, intent_hint=body.intent_hint, **kwargs)
            emit("result", result)
        except GenerationCancelled:
            db.rollback()
            emit("cancelled")
        except GuideProblem as exc:
            db.rollback()
            emit("error", exc.code)
        except OperationalError:
            db.rollback()
            emit("error", "GUIDE_UNAVAILABLE")
        except Exception:
            log.exception("guide streamed turn failed")
            db.rollback()
            emit("error", "GUIDE_INTERNAL_ERROR")
        finally:
            db.close()

    yield _event("response.started", {"request_id": rid, "assistant_message_id": None})
    worker = threading.Thread(target=work, name="guide-turn", daemon=True)
    worker.start()
    heartbeat = max(0.05, get_settings().GUIDE_SSE_HEARTBEAT_SECONDS)
    poll = min(0.25, heartbeat)
    idle = 0.0
    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(events.get(), timeout=poll)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    cancelled.set()
                    metrics.incr("guide_sse_cancelled_total")
                    return
                idle += poll
                if idle >= heartbeat:
                    idle = 0.0
                    yield ": keep-alive\n\n"
                continue
            idle = 0.0
            if kind == "progress":
                yield _event("response.progress", {"stage": value})
            elif kind == "result":
                payload = {"request_id": rid, "session_id": str(session_id), **value}
                for chunk in _answer_events(payload):
                    yield chunk
                return
            else:
                code = value if kind == "error" else "GUIDE_UNAVAILABLE"
                metrics.incr("guide_sse_errors_total", code=code)
                yield _event("response.error", _error_data(code))
                return
    except (asyncio.CancelledError, GeneratorExit):
        cancelled.set()
        metrics.incr("guide_sse_cancelled_total")
        raise


@router.post("/sessions/{session_id}/messages", operation_id="submitMessage",
             tags=["messages"], dependencies=SESSION, responses=docs.responses("submitMessage"))
def submit_message(session_id: uuid.UUID, body: MessageSubmit, request: Request,
                   accept: str | None = Header(
                       None, description="application/json (default) or text/event-stream "
                                         "for the SSE contract (API §9)."),
                   db: Session = Depends(require_available)):
    s = get_settings()
    if len(body.text) > s.GUIDE_MAX_MESSAGE_CHARS:
        raise GuideProblem("GUIDE_PAYLOAD_TOO_LARGE",
                           f"Messages can be up to {s.GUIDE_MAX_MESSAGE_CHARS} characters.")
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    ratelimit.enforce(request, "messages", str(session.session_id), s.GUIDE_RL_MESSAGES_PER_MIN)
    # A pasted credential rotates the session capability (Security §7), and the new cookie can
    # only reach the browser on response headers — which SSE has already sent by the time the
    # worker runs. Such a turn is answered as JSON; the client accepts either.
    rotates = secrets_detect.warrants_rotation(secrets_detect.detect(body.text).classes)
    if "text/event-stream" in (accept or "") and not rotates:
        # Everything with an HTTP status of its own (401/403/413/429/503) was decided above;
        # from here on, failures travel as one response.error event.
        db.commit()  # release the session row lock; the worker re-reads the session
        return StreamingResponse(_stream_answer(request, session_id, body),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store",
                                          "X-Accel-Buffering": "no"})
    result = orchestrator.handle_message(db, request, session,
                                         client_message_id=body.client_message_id,
                                         text=body.text, intent_hint=body.intent_hint)
    return {"request_id": request.state.guide_request_id, "session_id": str(session_id),
            **result}


# ── Consent, leads, handoffs, feedback ─────────────────────────────────────────────────────

@router.put("/sessions/{session_id}/consents/{purpose}", operation_id="putConsent",
            tags=["consent"], dependencies=SESSION, responses=docs.responses("putConsent"))
def put_consent(session_id: uuid.UUID, purpose: ConsentPurpose, body: ConsentPut,
                request: Request, db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    consent = leads.record_consent(db, request, session, purpose, status=body.status,
                                   notice_version=body.notice_version, source=body.source)
    db.commit()
    return leads.consent_body(consent)


@router.post("/sessions/{session_id}/lead-flows", status_code=201,
             operation_id="startLeadFlow", tags=["leads"], dependencies=SESSION,
             responses=docs.responses("startLeadFlow"))
def start_lead_flow(session_id: uuid.UUID, body: LeadFlowStart, request: Request,
                    db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    require_feature(db, "leads")
    flow = leads.start_flow(db, request, session, body.flow_kind, body.source_intent)
    db.commit()
    return leads.render(db, flow)


@router.patch("/sessions/{session_id}/lead-flows/{flow_id}", operation_id="updateLeadFlow",
              tags=["leads"], dependencies=SESSION, responses=docs.responses("updateLeadFlow"))
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


@router.patch("/sessions/{session_id}/leads/{lead_flow_id}/draft", status_code=204,
              operation_id="saveLeadDraft", tags=["leads"], dependencies=SESSION,
              responses=docs.responses("saveLeadDraft"))
def save_lead_draft(session_id: uuid.UUID, lead_flow_id: uuid.UUID, request: Request,
                    fields: dict[str, str] = Body(
                        ..., description="Partial approved lead fields: field_id -> value.",
                        openapi_examples=docs.DRAFT_EXAMPLES),
                    db: Session = Depends(require_available)):
    """C6: hold what the visitor typed (saved on field blur) so a paused flow never re-asks
    it. No consent is needed to hold a draft; nothing leaves Guide until the consented
    submit. Values are encrypted like every lead value and purged with the session."""
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    require_feature(db, "leads")
    flow = leads.load_flow(db, session, lead_flow_id)
    lead_drafts.save_draft(db, request, session, flow, fields)
    db.commit()
    return Response(status_code=204)


@router.post("/sessions/{session_id}/lead-flows/{flow_id}/submit",
             operation_id="submitLeadFlow", tags=["leads"], dependencies=SESSION,
             responses=docs.responses("submitLeadFlow"))
def submit_lead_flow(session_id: uuid.UUID, flow_id: uuid.UUID, body: LeadSubmit,
                     request: Request,
                     idempotency_key: str = Header("", alias="Idempotency-Key",
                                                   description=IDEMPOTENCY_KEY_DOC),
                     db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    status, payload = leads.submit(db, request, session, flow_id,
                                   idempotency_key=idempotency_key,
                                   consent_receipt_id=body.consent_receipt_id)
    return JSONResponse(payload, status_code=status)


@router.post("/sessions/{session_id}/handoffs", operation_id="createHandoff",
             tags=["handoffs"], dependencies=SESSION, responses=docs.responses("createHandoff"))
def create_handoff(session_id: uuid.UUID, body: HandoffRequest, request: Request,
                   idempotency_key: str = Header("", alias="Idempotency-Key",
                                                 description=IDEMPOTENCY_KEY_DOC),
                   db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    result = handoffs.resolve_handoff(db, request, session, target=body.target,
                                      reason_code=body.reason_code,
                                      idempotency_key=idempotency_key)
    db.commit()
    return result


@router.post("/sessions/{session_id}/feedback", status_code=202,
             operation_id="submitFeedback", tags=["feedback"], dependencies=SESSION,
             responses=docs.responses("submitFeedback"))
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
             operation_id="startSupportFlow", tags=["support"], dependencies=SESSION,
             responses=docs.responses("startSupportFlow"))
def start_support_flow(session_id: uuid.UUID, body: SupportFlowStart, request: Request,
                       db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    require_feature(db, "viewer_support")
    return engine.start_flow(db, request, session, body.flow_type,
                             client_request_id=body.client_request_id,
                             confirm_replace=body.confirm_replace)


@router.post("/sessions/{session_id}/support-flows/{flow_id}/steps",
             operation_id="submitSupportStep", tags=["support"], dependencies=SESSION,
             responses=docs.responses("submitSupportStep"))
def submit_support_step(session_id: uuid.UUID, flow_id: uuid.UUID, body: SupportStepSubmit,
                        request: Request, db: Session = Depends(require_available)):
    session = sessions.load_session(db, request, session_id, mutating=True, lock=True)
    return engine.submit_step(db, request, session, flow_id,
                              client_step_id=body.client_step_id, step_id=body.step_id,
                              option_id=body.option_id, command=body.command)
