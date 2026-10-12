"""Support-flow state machine execution (API §10; DB §15 "Advance support flow").

Concurrency: the flow row is locked (SELECT ... FOR UPDATE) for the whole transition, the
submitted step_id must equal the server's current step, and (flow_id, client_step_id) is unique.
So a double-click replays the original result and an out-of-order submit is a 409 carrying the
authoritative current state for the client to re-render (API-AT-09, DB-AT-07).
"""
from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import registry, retention
from ..errors import GuideProblem
from ..models import GuideSession, GuideSupportFlow, GuideSupportStepEvent
from ..sessions import audit, current_event_claims
from . import flows as F

MAX_STEPS_PER_FLOW = 40
TERMINAL_STATUSES = ("resolved", "escalated", "cancelled")


# ── Presentation ───────────────────────────────────────────────────────────────────────────

def _resolve_step(flow_def: F.Flow, step_id: str, claims: dict | None) -> F.Step:
    """Follow skip rules so a context-dependent step without context is never shown."""
    seen = set()
    step = flow_def.steps[step_id]
    while step.applies is not None and not step.applies(claims):
        if step.step_id in seen or step.skip_to is None:
            break
        seen.add(step.step_id)
        step = flow_def.steps[step.skip_to]
    return step


def _escalation_actions(db: Session, claims: dict | None) -> list[dict]:
    actions = []
    if claims and claims.get("organizer_contact_route"):
        actions.append({"action_id": "contact-organizer", "type": "handoff",
                        "label": "Contact the event organizer", "target": "organizer"})
    for dest in ("viewer-support", "help-center"):
        a = registry.action_for(db, dest)
        if a:
            actions.append(a)
    return actions


def render(db: Session, flow: GuideSupportFlow, claims: dict | None) -> dict:
    flow_def = F.FLOWS[flow.flow_type]
    body: dict = {"flow_id": str(flow.flow_id), "flow_type": flow.flow_type,
                  "title": flow_def.title, "status": flow.status,
                  "resolution_code": flow.resolution_code, "step": None, "message": None,
                  "notice": None, "actions": []}
    if flow.status == "in_progress":
        step = flow_def.steps[flow.current_step_id]
        body["step"] = {
            "step_id": step.step_id,
            "prompt": step.render_prompt(claims),
            "input_type": step.input_type,
            "options": [{"option_id": o.option_id, "label": o.label} for o in step.options],
            "allow_skip": False,
            "allow_back": bool(flow.step_path),
        }
        if flow.flow_type == "viewer_access" and not flow.step_path:
            body["notice"] = F.SECRET_NOTICE
        body["actions"] = [a for a in (registry.action_for(db, d) for d in step.action_ids)
                           if a]
    elif flow.status == "resolved":
        body["message"] = F.RESOLVED_MESSAGE
    elif flow.status == "escalated":
        has_organizer = bool(claims and claims.get("organizer_contact_route"))
        body["message"] = F.ESCALATE_WITH_ORGANIZER if has_organizer else F.ESCALATE_GENERIC
        body["actions"] = _escalation_actions(db, claims)
    return body


# ── Lifecycle ──────────────────────────────────────────────────────────────────────────────

def _set_session_state(session: GuideSession, flow: GuideSupportFlow) -> None:
    if flow.status == "in_progress":
        session.state = "VIEWER_FLOW"
        session.active_flow_kind, session.active_flow_id = "support", flow.flow_id
    else:
        session.state = "SUPPORT_HANDOFF" if flow.status == "escalated" else "ACTIVE"
        if session.active_flow_id == flow.flow_id:
            session.active_flow_kind, session.active_flow_id = None, None


def _new_flow(db: Session, session: GuideSession, flow_type: str,
              claims: dict | None) -> GuideSupportFlow:
    flow_def = F.FLOWS[flow_type]
    first = _resolve_step(flow_def, flow_def.first_step, claims)
    flow = GuideSupportFlow(
        flow_id=uuid.uuid4(), session_id=session.session_id, flow_type=flow_type,
        status="in_progress", current_step_id=first.step_id, step_path=[], step_count=0,
        started_at=retention.now(), purge_after=retention.purge_after("support_flow"),
    )
    db.add(flow)
    db.flush()
    return flow


def _cancel_active(db: Session, request: Request, session: GuideSession, reason: str) -> None:
    active = db.scalars(select(GuideSupportFlow).where(
        GuideSupportFlow.session_id == session.session_id,
        GuideSupportFlow.status == "in_progress").with_for_update()).all()
    now = retention.now()
    for flow in active:
        flow.status, flow.resolution_code = "cancelled", reason
        flow.completed_at = flow.updated_at = now
        audit(db, request, "support_flow_cancelled", session_id=session.session_id,
              flow_id=str(flow.flow_id), flow_type=flow.flow_type, reason=reason)


def start_flow(db: Session, request: Request, session: GuideSession, flow_type: str) -> dict:
    claims = current_event_claims(db, session)
    # One active viewer flow per session. Starting another is an explicit user switch
    # (Conversation Flow §3), so the previous one is closed, not silently merged.
    _cancel_active(db, request, session, "switched_by_user")
    flow = _new_flow(db, session, flow_type, claims)
    _set_session_state(session, flow)
    audit(db, request, "support_flow_started", session_id=session.session_id,
          flow_id=str(flow.flow_id), flow_type=flow_type, step_id=flow.current_step_id,
          event_context=claims is not None)
    db.commit()
    return render(db, flow, claims)


def _load_flow(db: Session, session: GuideSession, flow_id: uuid.UUID) -> GuideSupportFlow:
    flow = db.scalar(select(GuideSupportFlow).where(
        GuideSupportFlow.flow_id == flow_id,
        GuideSupportFlow.session_id == session.session_id).with_for_update())
    if flow is None:
        raise GuideProblem("GUIDE_RESOURCE_NOT_FOUND")
    return flow


def _conflict(db: Session, flow: GuideSupportFlow, claims: dict | None, detail: str):
    return GuideProblem("GUIDE_STATE_CONFLICT", detail,
                        extra={"current": render(db, flow, claims)})


def submit_step(db: Session, request: Request, session: GuideSession, flow_id: uuid.UUID,
                *, client_step_id: uuid.UUID, step_id: str, option_id: str | None,
                command: str | None) -> dict:
    if (option_id is None) == (command is None):
        raise GuideProblem("GUIDE_VALIDATION_ERROR",
                           "Choose an option or a navigation command.",
                           field_errors=[{"field": "option_id", "code": "one_required",
                                          "message": "Provide option_id or command."}])
    claims = current_event_claims(db, session)
    flow = _load_flow(db, session, flow_id)

    prior = db.scalar(select(GuideSupportStepEvent).where(
        GuideSupportStepEvent.flow_id == flow.flow_id,
        GuideSupportStepEvent.client_step_id == client_step_id))
    if prior is not None:
        same = (prior.step_id == step_id
                and prior.answer_structured == _answer(option_id, command))
        if not same:
            raise _conflict(db, flow, claims, "This step id was already used for a different "
                                              "answer.")
        # Exact replay: one logical transition, report where the flow is now.
        return _render_after(db, flow, claims)

    if flow.status != "in_progress":
        raise _conflict(db, flow, claims, "This help flow has already finished.")
    if step_id != flow.current_step_id:
        raise _conflict(db, flow, claims, "That step is no longer current.")
    if flow.step_count >= MAX_STEPS_PER_FLOW:
        raise _conflict(db, flow, claims, "This help flow has too many steps. Please start "
                                          "again or contact support.")

    flow_def = F.FLOWS[flow.flow_type]
    step = flow_def.steps[flow.current_step_id]
    from_state = flow.current_step_id
    outcome_code = None
    switched: GuideSupportFlow | None = None
    now = retention.now()

    if command == "back":
        if not flow.step_path:
            raise _conflict(db, flow, claims, "There is no previous step.")
        path = list(flow.step_path)
        flow.current_step_id = path.pop()
        flow.step_path = path
    elif command == "restart":
        flow.current_step_id = _resolve_step(flow_def, flow_def.first_step, claims).step_id
        flow.step_path = []
    elif command == "cancel":
        flow.status, flow.resolution_code, flow.completed_at = "cancelled", "user_cancelled", now
        outcome_code = "user_cancelled"
    else:
        option = next((o for o in step.options if o.option_id == option_id), None)
        if option is None:
            raise GuideProblem("GUIDE_VALIDATION_ERROR", "That option isn't available for this "
                                                         "step.",
                               field_errors=[{"field": "option_id", "code": "invalid_option",
                                              "message": "Choose one of the listed options."}])
        target = option.to
        if isinstance(target, str):
            nxt = _resolve_step(flow_def, target, claims)
            flow.step_path = [*flow.step_path, flow.current_step_id]
            flow.current_step_id = nxt.step_id
        elif isinstance(target, F.Terminal):
            flow.status, flow.resolution_code = target.status, target.resolution_code
            flow.completed_at = now
            outcome_code = target.resolution_code
        else:  # Switch into another flow
            flow.status, flow.resolution_code = "cancelled", target.resolution_code
            flow.completed_at = now
            outcome_code = target.resolution_code
            switched = _new_flow(db, session, target.flow_type, claims)

    flow.step_count += 1
    flow.updated_at = now
    to_state = flow.status if flow.status != "in_progress" else flow.current_step_id
    db.add(GuideSupportStepEvent(
        flow_id=flow.flow_id, client_step_id=client_step_id, step_id=step_id,
        answer_structured=_answer(option_id, command), from_state=from_state,
        to_state=to_state, outcome_code=outcome_code,
        purge_after=retention.purge_after("support_flow"),
    ))
    _set_session_state(session, switched or flow)
    audit(db, request, "support_flow_step", session_id=session.session_id,
          flow_id=str(flow.flow_id), flow_type=flow.flow_type, step_id=step_id,
          to_state=to_state, outcome_code=outcome_code)
    if switched is not None:
        audit(db, request, "support_flow_started", session_id=session.session_id,
              flow_id=str(switched.flow_id), flow_type=switched.flow_type,
              step_id=switched.current_step_id, switched_from=str(flow.flow_id))
    db.commit()
    result = render(db, flow, claims)
    if switched is not None:
        result["next_flow"] = render(db, switched, claims)
    return result


def _render_after(db: Session, flow: GuideSupportFlow, claims: dict | None) -> dict:
    result = render(db, flow, claims)
    if flow.status == "cancelled" and flow.resolution_code and (
            flow.resolution_code.startswith("switched_to_")
            or flow.resolution_code.startswith("category_")):
        successor = db.scalar(select(GuideSupportFlow).where(
            GuideSupportFlow.session_id == flow.session_id,
            GuideSupportFlow.started_at > flow.started_at,
            GuideSupportFlow.flow_id != flow.flow_id,
        ).order_by(GuideSupportFlow.started_at).limit(1))
        if successor is not None:
            result["next_flow"] = render(db, successor, claims)
    db.commit()
    return result


def _answer(option_id: str | None, command: str | None) -> dict:
    return {"option_id": option_id} if option_id is not None else {"command": command}
