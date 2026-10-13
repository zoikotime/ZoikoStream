"""Governed actions (ZST-UX-GUIDE-001 §6.11, §10, §14): proposals, explicit confirmation,
in-process execution through the platform's own endpoints, receipts and reconciliation.

Risk ladder (§10):
  R2  drafts           — generated as drafts only; applying one is a separate R3 proposal.
  R3  low-risk change  — explicit confirmation, receipt.
  R4  material change  — permission check, explicit confirmation naming the impact, audited.
  R5  critical live    — never executed from chat; Guide opens the dedicated control.
  R6  destructive /    — refused; Guide points to the dedicated, audited workflow.
      security

Execution doctrine:
  * Guide never writes platform tables. A confirmed proposal becomes ONE request to an
    existing platform endpoint, made in-process with the user's own bearer token, so the
    platform's authorization, validation, audit log, notifications and webhooks all apply
    exactly as if the user had clicked the button themselves.
  * Confirmation is single-use and idempotent: the proposal moves proposed -> executing
    atomically; a repeated Idempotency-Key returns the original receipt.
  * Optimistic concurrency: if the target changed since the proposal was shown, the proposal
    is withdrawn instead of overwriting someone else's change.
  * Unknown outcome (timeout, transport failure, 5xx): Guide re-reads authoritative state
    before saying anything. If the change is visible it is reported as done; otherwise the
    outcome is UNKNOWN and Guide says so — it never retries on its own (§15).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.event import Event, EventAccessLink
from app.models.support_ticket import SupportTicket

from . import audit, errors, policy
from .config import get_settings
from .policy import Actor
from .schema import Proposal
from .sources import iso, now
from .tools.events import VISIBILITY_TEXT

log = logging.getLogger(__name__)

FEATURE_FIELDS = {"chat": ("chat_enabled", "chat"), "qa": ("qa_enabled", "Q&A"),
                  "polls": ("polls_enabled", "polls"),
                  "waiting_room": ("waiting_room_enabled", "the waiting room")}
SUPPORT_CATEGORIES = [
    ("live_event", re.compile(r"\b(live|stream|broadcast|event|viewers?|lag|buffer|freez)", re.I)),
    ("media_recording", re.compile(r"\b(record|replay|video|download|vod)", re.I)),
    ("developer_api", re.compile(r"\b(api|webhook|sdk|integration|key)\b", re.I)),
    ("billing", re.compile(r"\b(bill|invoice|payment|charge|plan|subscription)", re.I)),
    ("account_access", re.compile(r"\b(login|log in|sign in|password|access|account|2fa|mfa)",
                                  re.I)),
]


# ── Blocked operations (R5 / R6) ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Blocked:
    risk: str
    what: str          # the operation in plain words
    where: str         # the dedicated control or workflow
    reason: str


BLOCKED = {
    "go_live": Blocked("R5", "Starting a broadcast", "the Go Live control in the host console",
                       "Going live is a critical live control and stays in its dedicated "
                       "control, behind the platform's readiness gate."),
    "end_broadcast": Blocked("R5", "Ending a broadcast", "the End control in the host console",
                             "Ending a broadcast is a critical live control and is never "
                             "executed from a conversation."),
    "recording_control": Blocked("R5", "Starting or stopping recording",
                                 "the recording controls in the host console",
                                 "Live recording controls stay in the broadcast console."),
    "failover": Blocked("R5", "Failover and recovery output",
                        "the live input and recovery controls in the host console",
                        "Switching program output is a critical live control."),
    "override_gate": Blocked("R5", "Overriding readiness",
                             "the event's setup and readiness pages",
                             "Only the readiness service moves an event to Ready. Guide can "
                             "explain blockers but cannot convert Conditional or Blocked to "
                             "Ready."),
    "cancel_event": Blocked("R5", "Cancelling an event", "the event's page",
                            "Cancelling notifies your audience and cannot be quietly undone, "
                            "so it stays in the event's own controls."),
    "delete": Blocked("R6", "Deleting events, recordings or media",
                      "the dedicated delete workflow on the event or recording page",
                      "Destructive operations are never performed through Guide."),
    "secret": Blocked("R6", "Showing or rotating keys, secrets and stream keys",
                      "the developer platform or live input settings",
                      "Guide never displays credentials and never rotates them."),
    "roles": Blocked("R6", "Changing roles and access grants",
                     "Organization › Users and the Support Access workflow",
                     "Permission changes are security operations and are never made through "
                     "Guide."),
    "billing": Blocked("R6", "Billing and plan changes", "Organization › Billing",
                       "Billing changes are made only in the billing workflow."),
}


# ── Catalog (R3 / R4) ────────────────────────────────────────────────────────────────

@dataclass
class Plan:
    """A fully resolved proposal: what changes, from what to what, and the one request."""
    title: str
    confirm_label: str
    target_type: str
    target_id: str
    target_label: str
    change: dict                         # {"field", "from", "to"} — display + concurrency
    impact: list[str]
    method: str
    path: str
    body: dict | None
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Spec:
    id: str
    risk: str
    needs: str                                       # "edit_event" | "org_admin"
    plan: Callable[[Session, Actor, Event | None, dict], Plan]
    current: Callable[[Session, Actor, Proposal], object]   # re-read authoritative value


def _event_patch(ev: Event, field_name: str, value, *, title: str, confirm: str,
                 label: str, impact: list[str], shown_from, shown_to) -> Plan:
    return Plan(title=title, confirm_label=confirm, target_type="event", target_id=str(ev.id),
                target_label=ev.title or "Untitled event",
                change={"field": field_name, "label": label, "from": shown_from, "to": shown_to,
                        "value": value},
                impact=impact, method="PATCH", path=f"/api/events/{ev.id}",
                body={field_name: value}, params={"field": field_name, "value": value})


def _plan_feature(db, actor, ev, params) -> Plan:
    feature = params.get("feature")
    if feature not in FEATURE_FIELDS:
        raise errors.ValidationFailed("I can turn chat, Q&A, polls or the waiting room on or off.")
    column, words = FEATURE_FIELDS[feature]
    enabled = bool(params.get("enabled"))
    verb = "Turn on" if enabled else "Turn off"
    impact = [f"{words[0].upper() + words[1:]} will be {'available' if enabled else 'unavailable'}"
              " to viewers" + (" immediately." if ev.status in ("live", "degraded") else ".")]
    return _event_patch(ev, column, enabled, title=f"{verb} {words}", confirm=f"Confirm: {verb} "
                        f"{words}", label=words[0].upper() + words[1:],
                        impact=impact, shown_from="On" if getattr(ev, column) else "Off",
                        shown_to="On" if enabled else "Off")


def _plan_visibility(db, actor, ev, params) -> Plan:
    vis = params.get("visibility")
    if vis not in VISIBILITY_TEXT:
        raise errors.ValidationFailed("Visibility can be public, unlisted or private.")
    impact = [VISIBILITY_TEXT[vis]]
    if vis == "private" and ev.visibility != "private":
        impact.append("People who registered without an invitation or access link will no "
                      "longer be able to watch.")
    if ev.status in ("live", "degraded"):
        impact.append("This event is on air: the change applies to new joins immediately.")
    return _event_patch(ev, "visibility", vis, title=f"Make {ev.title or 'this event'} {vis}",
                        confirm=f"Confirm: Make event {vis}", label="Audience access",
                        impact=impact, shown_from=(ev.visibility or "public").title(),
                        shown_to=vis.title())


def _plan_registration(db, actor, ev, params) -> Plan:
    required = bool(params.get("required"))
    impact = (["Viewers will have to register before they can watch."] if required else
              ["Viewers will no longer need to register."
               + (" Public and unlisted events still ask viewers to register."
                  if ev.visibility in ("public", "unlisted") else "")])
    return _event_patch(ev, "registration_required", required,
                        title="Require registration" if required else "Stop requiring "
                        "registration", confirm="Confirm: " + ("Require registration" if required
                                                              else "Make registration optional"),
                        label="Registration", impact=impact,
                        shown_from="Required" if ev.registration_required else "Not required",
                        shown_to="Required" if required else "Not required")


def _plan_recording(db, actor, ev, params) -> Plan:
    enabled = bool(params.get("enabled"))
    impact = (["The broadcast will be recorded when it starts."] if enabled else
              ["The next broadcast will not be recorded, and no replay can be made from it.",
               "Recording is a readiness gate; turning it off can make this event Conditional "
               "or Blocked."])
    if ev.status in ("live", "degraded"):
        impact.append("This event is on air. Live recording is controlled from the host console;"
                      " this changes the event setting only.")
    return _event_patch(ev, "recording_enabled", enabled,
                        title="Enable recording" if enabled else "Disable recording",
                        confirm="Confirm: " + ("Enable recording" if enabled
                                               else "Disable recording"),
                        label="Recording", impact=impact,
                        shown_from="Enabled" if ev.recording_enabled else "Disabled",
                        shown_to="Enabled" if enabled else "Disabled")


def _plan_description(db, actor, ev, params) -> Plan:
    text = (params.get("text") or "").strip()
    if not text:
        raise errors.ValidationFailed("Tell me the new description, for example: set the "
                                      "description to: …")
    if len(text) > 5000:
        raise errors.ValidationFailed("That description is too long (5,000 characters max).")
    before = (ev.description or "").strip()
    return _event_patch(ev, "description", text, title="Update event description",
                        confirm="Confirm: Update description", label="Description",
                        impact=["The new description appears on the event page."],
                        shown_from=(before[:140] + "…") if len(before) > 140 else (before or
                                                                                    "(empty)"),
                        shown_to=(text[:140] + "…") if len(text) > 140 else text)


def _plan_revoke_link(db, actor, ev, params) -> Plan:
    try:
        link_id = uuid.UUID(str(params.get("link_id")))
    except (TypeError, ValueError):
        raise errors.ValidationFailed("Choose which access link to revoke.") from None
    link = db.scalar(select(EventAccessLink).where(
        EventAccessLink.id == link_id, EventAccessLink.event_id == ev.id,
        EventAccessLink.org_id == actor.org_id))
    if link is None:
        raise errors.NotFound("I can't find that access link on this event.")
    if link.revoked_at is not None:
        raise errors.Conflict("That access link is already revoked.")
    label = link.label or f"Link created {link.created_at:%d %b %Y}"
    return Plan(title=f"Revoke access link “{label}”", confirm_label="Confirm: Revoke access link",
                target_type="access_link", target_id=str(link.id), target_label=label,
                change={"field": "revoked", "label": "Access link", "from": "Active",
                        "to": "Revoked", "value": True},
                impact=["Anyone using this link will no longer be able to join "
                        f"{ev.title or 'the event'}.", f"The link has been used {link.uses} "
                        "time(s).", "This cannot be undone; you can create a new link later."],
                method="POST", path=f"/api/events/{ev.id}/access-links/{link.id}/revoke",
                body=None, params={"link_id": str(link.id), "event_id": str(ev.id)})


def _plan_support(db, actor, ev, params) -> Plan:
    raw = (params.get("text") or "").strip()
    detail = re.sub(r"^.*?\b(case|ticket)\b\s*(about|for|regarding|re)?\s*[:\-–]?\s*", "", raw,
                    count=1, flags=re.I).strip()
    if len(detail) < 8:
        raise errors.ValidationFailed("Describe the problem in a sentence, for example: open a "
                                      "support case: viewers see a black screen after 10 minutes.")
    category = next((c for c, p in SUPPORT_CATEGORIES if p.search(detail)), "other")
    subject = re.split(r"(?<=[.!?])\s", detail, maxsplit=1)[0][:120]
    description = detail + (f"\n\nEvent: {ev.title} ({ev.id})" if ev is not None else "")
    return Plan(title="Open a support case", confirm_label="Confirm: Open support case",
                target_type="organization", target_id=str(actor.org_id),
                target_label=actor.org.name,
                change={"field": "support_case", "label": "Support case", "from": None,
                        "to": subject, "value": subject},
                impact=[f"Category: {category.replace('_', ' ')} · Priority: normal",
                        "ZoikoStream support will be notified and you will get a case "
                        "reference."],
                method="POST", path="/api/organization/support-cases",
                body={"subject": subject, "description": description, "category": category,
                      "priority": "normal", "sensitivity": "standard"},
                params={"subject": subject, "category": category})


def _event_value(db, actor, p: Proposal):
    ev = db.scalar(select(Event).where(Event.id == uuid.UUID(p.target_id),
                                       Event.org_id == actor.org_id))
    if ev is None:
        return None
    db.refresh(ev)
    return getattr(ev, p.params["field"])


def _link_revoked(db, actor, p: Proposal):
    link = db.scalar(select(EventAccessLink).where(
        EventAccessLink.id == uuid.UUID(p.target_id), EventAccessLink.org_id == actor.org_id))
    if link is None:
        return None
    db.refresh(link)
    return link.revoked_at is not None


def _support_exists(db, actor, p: Proposal):
    return db.scalar(select(SupportTicket.case_reference).where(
        SupportTicket.org_id == actor.org_id, SupportTicket.requester_id == actor.user.id,
        SupportTicket.subject == p.params["subject"],
        SupportTicket.created_at >= p.confirmed_at - timedelta(seconds=5)).limit(1))


CATALOG: dict[str, Spec] = {s.id: s for s in (
    Spec("event.set_feature", "R3", "edit_event", _plan_feature, _event_value),
    Spec("event.update_description", "R3", "edit_event", _plan_description, _event_value),
    Spec("event.set_visibility", "R4", "edit_event", _plan_visibility, _event_value),
    Spec("event.set_registration", "R4", "edit_event", _plan_registration, _event_value),
    Spec("event.set_recording", "R4", "edit_event", _plan_recording, _event_value),
    Spec("access_link.revoke", "R4", "edit_event", _plan_revoke_link, _link_revoked),
    Spec("support.open_case", "R3", "org_admin", _plan_support, _support_exists),
)}


# ── Proposal lifecycle ───────────────────────────────────────────────────────────────

def _check_permission(db: Session, actor: Actor, spec: Spec, ev: Event | None) -> None:
    if spec.needs == "org_admin" and not policy.is_org_admin(actor.user):
        raise errors.Forbidden("Only an organization admin can do this. Your role is "
                               f"{actor.role.replace('_', ' ')}.")
    if spec.needs == "edit_event":
        if ev is None:
            raise errors.ValidationFailed("Select the event this change is for.")
        if not policy.can_edit_event(db, actor, ev):
            raise errors.Forbidden("You can change only events you created or are assigned to "
                                   "host. An organization admin can make this change.")


def card(p: Proposal) -> dict:
    """The proposal as the UI renders it (§6.11)."""
    s = p.summary or {}
    return {"type": "proposal", "id": str(p.id), "action_id": p.action_id, "risk": p.risk_class,
            "status": p.status, "title": s.get("title"), "target": s.get("target"),
            "change": s.get("change"), "impact": s.get("impact", []),
            "confirm_label": s.get("confirm_label"), "cancel_label": "Cancel",
            "expires_at": iso(p.expires_at), "receipt": p.receipt}


def propose(db: Session, actor: Actor, action_id: str, params: dict, *, event: Event | None,
            conversation_id=None, context: dict | None = None) -> dict:
    spec = CATALOG.get(action_id)
    if spec is None:
        raise errors.ValidationFailed("That is not an action Guide can perform.")
    policy.require_capability(db, actor, "actions")
    _check_permission(db, actor, spec, event)
    plan = spec.plan(db, actor, event, params)
    if plan.change.get("from") is not None and plan.change["from"] == plan.change["to"]:
        return {"type": "info", "text": f"{plan.change['label']} is already "
                                        f"{str(plan.change['to']).lower()}. Nothing to change."}
    p = Proposal(org_id=actor.org_id, actor_id=actor.user.id, conversation_id=conversation_id,
                 action_id=spec.id, risk_class=spec.risk, target_type=plan.target_type,
                 target_id=plan.target_id,
                 params={**plan.params, "method": plan.method, "path": plan.path,
                         "body": plan.body, "from_value": _raw_from(db, plan, event)},
                 summary={"title": plan.title, "confirm_label": plan.confirm_label,
                          "target": {"type": plan.target_type, "id": plan.target_id,
                                     "label": plan.target_label},
                          "change": {k: v for k, v in plan.change.items() if k != "value"},
                          "impact": plan.impact},
                 status="proposed", correlation_id=audit.new_id("cor"),
                 expires_at=now() + timedelta(minutes=get_settings().OPGUIDE_PROPOSAL_TTL_MINUTES))
    db.add(p)
    db.flush()
    audit.record(db, actor, kind="proposal", outcome="proposed", intent=spec.id,
                 context=context, authorization="allowed", correlation_id=p.correlation_id,
                 tool_plan=[{"tool": spec.id, "risk": spec.risk, "target": plan.target_id}],
                 response_ref=str(p.id))
    return card(p)


def _raw_from(db, plan: Plan, ev: Event | None):
    """The authoritative value at proposal time, for optimistic concurrency at confirm."""
    if plan.target_type == "event" and ev is not None:
        value = getattr(ev, plan.params["field"])
        return value if isinstance(value, (bool, int, str)) or value is None else str(value)
    return None


def _own(db: Session, actor: Actor, proposal_id) -> Proposal:
    try:
        pid = uuid.UUID(str(proposal_id))
    except (TypeError, ValueError):
        raise errors.NotFound("That proposal does not exist.") from None
    p = db.scalar(select(Proposal).where(Proposal.id == pid, Proposal.org_id == actor.org_id,
                                         Proposal.actor_id == actor.user.id))
    if p is None:
        raise errors.NotFound("That proposal does not exist.")
    return p


def _expire_if_due(db: Session, p: Proposal) -> None:
    if p.status == "proposed" and p.expires_at <= now():
        p.status = "expired"
        db.flush()


def cancel(db: Session, actor: Actor, proposal_id) -> dict:
    p = _own(db, actor, proposal_id)
    _expire_if_due(db, p)
    if p.status == "proposed":
        p.status = "cancelled"
        audit.record(db, actor, kind="action", outcome="cancelled", intent=p.action_id,
                     correlation_id=p.correlation_id, response_ref=str(p.id))
        db.flush()
    return card(p)


@dataclass
class PlatformResponse:
    status_code: int | None          # None = no response (timeout / transport failure)
    body: object = None
    error: str | None = None         # timeout | transport


Executor = Callable[[str, str, dict | None, dict], PlatformResponse]


def confirm(db: Session, actor: Actor, proposal_id, *, idempotency_key: str,
            execute: Executor) -> dict:
    """Confirm and execute one proposal. Returns the proposal card with its receipt."""
    key = (idempotency_key or "").strip()
    if not (8 <= len(key) <= 128):
        raise errors.ValidationFailed("An Idempotency-Key header (8–128 characters) is required.")
    replay = db.scalar(select(Proposal).where(Proposal.org_id == actor.org_id,
                                              Proposal.idempotency_key == key))
    if replay is not None:
        if str(replay.id) != str(proposal_id) or replay.actor_id != actor.user.id:
            raise errors.Conflict("That Idempotency-Key was already used for another request.")
        return {**card(replay), "replayed": True}

    p = _own(db, actor, proposal_id)
    _expire_if_due(db, p)
    if p.status == "expired":
        raise errors.Conflict("This proposal expired. Ask again and I will check the current "
                              "state first.")
    if p.status != "proposed":
        raise errors.Conflict(f"This proposal is already {p.status}.")
    spec = CATALOG[p.action_id]

    # Governance and permission are re-checked at confirmation, not trusted from proposal time.
    policy.require_capability(db, actor, "actions")
    event = None
    if p.target_type == "event" or spec.needs == "edit_event":
        event_id = p.target_id if p.target_type == "event" else p.params.get("event_id")
        event = policy.resolve_event(db, actor, event_id)
        db.refresh(event)
    _check_permission(db, actor, spec, event)
    if p.target_type == "event" and getattr(event, p.params["field"]) != p.params.get("from_value"):
        p.status = "expired"
        audit.record(db, actor, kind="action", outcome="withdrawn_stale", intent=p.action_id,
                     correlation_id=p.correlation_id, response_ref=str(p.id))
        db.commit()
        raise errors.Conflict("The event changed after I proposed this, so I withdrew the "
                              "proposal. Ask again and I will use the current state.")

    # Atomic claim: only one confirmation can move proposed -> executing.
    try:
        claimed = db.execute(update(Proposal).where(Proposal.id == p.id,
                                                    Proposal.status == "proposed")
                             .values(status="executing", idempotency_key=key,
                                     confirmed_by=actor.user.id, confirmed_at=now())).rowcount
        db.commit()
    except IntegrityError:
        db.rollback()
        raise errors.Conflict("That Idempotency-Key was already used for another request.") \
            from None
    if claimed != 1:
        raise errors.Conflict("This proposal is already being processed.")
    db.refresh(p)

    started = now()
    headers = {"X-Request-Id": p.correlation_id, "Idempotency-Key": key}
    try:
        response = execute(p.params["method"], p.params["path"], p.params.get("body"), headers)
    except Exception:
        log.exception("opguide action transport failed correlation_id=%s", p.correlation_id)
        response = PlatformResponse(None, error="transport")

    receipt = {"correlation_id": p.correlation_id, "action": p.action_id, "risk": p.risk_class,
               "target": (p.summary or {}).get("target"),
               "change": (p.summary or {}).get("change"), "requested_at": iso(started)}
    if response.status_code is not None and 200 <= response.status_code < 300:
        status, outcome = "succeeded", "succeeded"
        receipt["verified"], observed = _observe(db, actor, spec, p)
        receipt["reference"] = _reference(response.body) or _reference_from(p, observed)
        receipt["message"] = _success_text(p)
    elif response.status_code is not None and response.status_code < 500:
        status, outcome = "failed", f"failed_{response.status_code}"
        receipt["message"] = errors.TOOL_FAILED
        receipt["reason"] = _platform_reason(response.body)
    else:
        # Timeout, transport error or 5xx: reconcile against authoritative state (§15).
        applied, observed = _observe(db, actor, spec, p)
        if applied:
            status, outcome = "succeeded", "succeeded_reconciled"
            receipt.update(verified=True, reconciled=True, message=_success_text(p),
                           reference=_reference_from(p, observed))
        else:
            status, outcome = "unknown", "unknown"
            receipt["message"] = errors.OUTCOME_UNKNOWN
    receipt["completed_at"] = iso(now())
    p.status, p.receipt, p.executed_at = status, receipt, now()
    audit.record(db, actor, kind="action", outcome=outcome, intent=p.action_id,
                 authorization="allowed", confirmation_actor=actor.user.id,
                 correlation_id=p.correlation_id, response_ref=str(p.id),
                 tool_plan=[{"tool": p.action_id, "risk": p.risk_class, "target": p.target_id}],
                 receipts=[{k: receipt.get(k) for k in ("verified", "reconciled", "reference",
                                                        "completed_at")}
                           | {"http_status": response.status_code, "error": response.error}],
                 latency_ms=int((now() - started).total_seconds() * 1000))
    db.commit()
    return card(p)


def asgi_executor(app, *, host: str, token: str) -> Executor:
    """Executor that calls the platform app in-process, as the user, through its full
    middleware stack. Used from a sync endpoint (an AnyIO worker thread), so the request runs
    on the event loop while this thread waits — the platform's own sync handler gets its own
    worker thread."""
    import anyio
    import anyio.from_thread
    import httpx

    timeout = get_settings().OPGUIDE_ACTION_TIMEOUT_SECONDS

    async def call(method: str, path: str, body: dict | None, headers: dict) -> PlatformResponse:
        # A platform 500 comes back as a 500 response (then reconciled), not an exception.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}",
                                     timeout=timeout) as client:
            try:
                res = await client.request(method, path, json=body, headers={
                    **headers, "Authorization": f"Bearer {token}", "Host": host,
                    "Accept": "application/json"})
            except httpx.TimeoutException:
                return PlatformResponse(None, error="timeout")
            except httpx.HTTPError:
                return PlatformResponse(None, error="transport")
        try:
            data = res.json()
        except ValueError:
            data = None
        return PlatformResponse(res.status_code, data)

    def execute(method, path, body, headers) -> PlatformResponse:
        async def run():
            with anyio.fail_after(timeout + 1):
                return await call(method, path, body, headers)
        try:
            return anyio.from_thread.run(run)
        except TimeoutError:
            return PlatformResponse(None, error="timeout")
    return execute


def _observe(db: Session, actor: Actor, spec: Spec, p: Proposal) -> tuple[bool, object]:
    """Re-read authoritative state: is the requested change actually in effect? Returns
    (applied, the value read)."""
    try:
        current = spec.current(db, actor, p)
    except Exception:
        log.exception("opguide reconcile read failed correlation_id=%s", p.correlation_id)
        return False, None
    if p.target_type == "event":
        return current == p.params.get("value"), current
    return bool(current), current


def _reference_from(p: Proposal, observed) -> str | None:
    """The case reference a support-case reconciliation read found."""
    return observed if p.action_id == "support.open_case" and isinstance(observed, str) else None


def _reference(body) -> str | None:
    if isinstance(body, dict):
        return body.get("case_reference") or None
    return None


def _platform_reason(body) -> str | None:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail)[:300] if isinstance(detail, str) else None


def _success_text(p: Proposal) -> str:
    s = p.summary or {}
    change = s.get("change") or {}
    if p.action_id == "support.open_case":
        return "Support case opened."
    if p.action_id == "access_link.revoke":
        return f"Access link revoked on {s.get('target', {}).get('label', 'the event')}."
    return f"{change.get('label', 'Setting')} changed from {change.get('from')} to " \
           f"{change.get('to')}."
