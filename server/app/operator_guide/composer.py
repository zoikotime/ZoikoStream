"""Answer assembly (ZST-UX-GUIDE-001 §7, §8, §15).

Every answer follows the same pattern:
    direct answer  →  current state (cards)  →  why  →  next action  →  sources / freshness

The direct answer and the cards are deterministic — produced by tools from authoritative
state. The model may add a short "why" explanation over the same facts; that text is
validated (numbers must come from the facts, no claims of having acted, no credentials, no
links) and dropped on any doubt. Live Mode (§6.9) is terse: no model text, no suggestions.

Facts and interpretation stay separate (§6.3, §10 R1): an R1 tool result flagged as inference,
and any model explanation, add an ``inference`` source and the payload's ``inference`` flag,
so the UI labels that part "Guide interpretation" and never presents it as platform state.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.guide.secrets_detect import detect as detect_secrets
from app.models.event import EventAccessLink

from . import (
    actions, attention, context as context_svc, errors, governance, knowledge, llm, policy,
)
from .intents import EVENT_TOOLS, Intent
from .policy import Actor
from .sources import Source, now
from .tools import REGISTRY, ToolContext, run as run_tool
from .tools.base import escalation_card, nav, notice
from .tools.events import _fmt_when, event_href
from .tools.org import technical_context

log = logging.getLogger(__name__)

IDENTITY = ("I'm ZoikoStream Guide, the operating assistant in your console. I read your "
            "organization's events, readiness, access, recordings, live health and analytics "
            "with your permissions, explain what they mean, draft copy, and make low-risk "
            "changes only after you confirm them.")
CAPABILITIES = ("I can tell you whether an event is ready and why, who can access it, how a "
                "live broadcast is doing, where a recording or replay stands, how your events "
                "performed, and how ZoikoStream features work. I can draft descriptions and "
                "messages, and turn settings like chat or registration on or off after you "
                "confirm. Critical live controls such as Go Live stay in the host console.")
GAP = ("I don't have documentation or data that answers that. Try asking about an event's "
       "readiness, audience access, recording, live health or analytics.")


@dataclass
class Composed:
    answer: str
    intent: str
    blocks: list[dict] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    explanation: str | None = None
    mode: str = "standard"
    tools: list[str] = field(default_factory=list)
    model_release: str | None = None
    authorization: str = "allowed"
    outcome: str = "answered"
    inference: bool = False           # §6.3/§10 R1: the answer includes Guide interpretation
    technical: dict | None = None     # §6.15: API version + environment on technical answers
    correlation_id: str | None = None  # links the message audit row to a proposal it created

    def payload(self) -> dict:
        seen, sources = set(), []
        for s in self.sources:
            if s.id not in seen:
                seen.add(s.id)
                sources.append(s.public())
        return {"answer": self.answer, "explanation": self.explanation, "intent": self.intent,
                "mode": self.mode, "blocks": self.blocks, "sources": sources,
                "actions": self.actions,
                "suggestions": [] if self.mode == "live" else self.suggestions[:4],
                "stale": any(s["stale"] for s in sources),
                "inference": self.inference or any(s["class"] == "inference" for s in sources),
                "technical": self.technical}


# ── Entry point ──────────────────────────────────────────────────────────────────────

def compose(db: Session, actor: Actor, ctx: dict, text: str, intent: Intent, *,
            conversation_id=None, history: list[dict] | None = None) -> Composed:
    """``history``: the last few turns of this conversation (memory.history), used only to
    phrase the model's explanation of a follow-up — never as a source of facts."""
    event = ctx.get("_event")
    try:
        if intent.kind == "chat":
            return _chat(intent, event, ctx, actor)
        if intent.kind == "blocked":
            return _blocked(db, actor, ctx, intent, event)
        if intent.kind == "action":
            return _action(db, actor, ctx, intent, event, conversation_id)
        if intent.kind == "draft":
            return _draft(db, actor, intent, event, text)
        if intent.kind == "escalate":
            return _escalate(db, actor, event)
        if intent.kind == "read":
            return _read(db, actor, ctx, intent, event, text, history)
        return _docs(db, actor, ctx, text, event, intent)
    except errors.EntitlementBlocked as exc:
        return Composed(exc.message, intent.name, [notice("blocker", exc.message,
                                                          rule="Guide settings")],
                        authorization="capability_off", outcome="capability_off")
    except errors.Forbidden as exc:
        return Composed(exc.message, intent.name, [notice("blocker", exc.message,
                                                          rule="Platform permissions")],
                        authorization="denied", outcome="denied")
    except errors.Timeout as exc:
        return Composed(exc.message, intent.name, [notice("warning", exc.message,
                                                          rule="Tool timeout")],
                        outcome="timeout")
    except (errors.ValidationFailed, errors.NotFound, errors.Conflict) as exc:
        return Composed(exc.message, intent.name, outcome="needs_input")


# ── Conversation ─────────────────────────────────────────────────────────────────────

# Home prompts by console workspace (§6.1: "Prompt suggestions derive from page, role, event
# state, and outstanding work"). Every prompt routes deterministically (test_intents).
PAGE_SUGGESTIONS = {
    "Analytics": ["How did our events perform?", "How many viewers did we have?",
                  "What does watch time mean?", "What needs attention?"],
    "Developers": ["Show our API keys and webhooks", "How do API keys work?",
                   "How do webhooks work?", "What needs attention?"],
    "Recordings": ["Where is the replay?", "What does retention mean?",
                   "What does legal hold mean?", "What needs attention?"],
    "Support": ["Show open support cases", "Is ZoikoStream down?", "What needs attention?",
                "I need to talk to a human"],
}
EVENT_PAGE_FIRST = {"Recordings": "Where is the replay?", "Analytics": "How many viewers did we have?"}


def suggestions_for(event, *, live: bool = False, workspace: str = "",
                    admin: bool | None = None) -> list[str]:
    """Suggested questions for the current context. ``admin`` None = role not considered."""
    if event is not None:
        if live:
            return ["How is the broadcast doing?", "Why are viewers dropping?",
                    "Who can access this event?"]
        out = ["Is this event ready?", "Who can access this event?", "Where is the replay?",
               "Draft a reminder email"]
        first = EVENT_PAGE_FIRST.get(workspace)
        return [first] + [s for s in out if s != first][:3] if first else out
    if workspace in PAGE_SUGGESTIONS:
        return list(PAGE_SUGGESTIONS[workspace])
    if admin is False:  # hosts: their own events first; org-wide reports are for admins
        return ["What needs attention?", "Show my upcoming events",
                "What does Conditional readiness mean?", "How do access links work?"]
    out = ["What needs attention?", "Show upcoming events", "How did our events perform?",
           "What does Conditional readiness mean?"]
    if admin:
        out[3:] = ["Show open support cases"]
    return out


def suggestions_for_ctx(ctx: dict, actor: Actor) -> list[str]:
    event = ctx.get("_event")
    return suggestions_for(event, live=_mode(event) == "live",
                           workspace=ctx.get("workspace", ""),
                           admin=policy.is_org_admin(actor.user))


def _chat(intent: Intent, event, ctx: dict, actor: Actor) -> Composed:
    text = {
        "greeting": "Hello. " + ("What would you like to know about "
                                 f"{event.title}?" if event is not None else
                                 "What would you like to check?"),
        "identity": IDENTITY,
        "capabilities": CAPABILITIES,
        "thanks": "You're welcome. Anything else to check?",
        "empty": "Ask me about an event, your audience, recordings or how a feature works.",
    }[intent.name]
    return Composed(text, intent.name, suggestions=suggestions_for_ctx(ctx, actor))


# ── R5 / R6 ──────────────────────────────────────────────────────────────────────────

def _blocked(db, actor, ctx, intent: Intent, event) -> Composed:
    b = actions.BLOCKED[intent.name]
    answer = f"I can't do that from Guide. {b.what} is done in {b.where}."
    rule = f"{b.risk} · " + ("critical live control" if b.risk == "R5" else "restricted operation")
    blocks = [notice("blocker", b.reason, rule=rule)]
    out = Composed(answer, f"blocked.{intent.name}", blocks, authorization=f"blocked_{b.risk}",
                   outcome="blocked", mode=_mode(event))
    if event is not None:
        href = (f"/host/dashboard?event={event.id}" if b.risk == "R5" and
                intent.name in ("go_live", "end_broadcast", "recording_control", "failover")
                else event_href(actor, event))
        out.actions.append(nav("Open host console" if href.startswith("/host") else "Open event",
                               href))
        if intent.name in ("go_live", "override_gate"):
            # §10: R5 routes to the dedicated control WITH the current readiness snapshot.
            try:
                policy.require_capability(db, actor, "event_analysis")
                result = run_tool("event.readiness", db, actor, ToolContext(event=event))
                out.blocks += result.blocks
                out.sources += result.sources
                out.tools.append("event.readiness")
                out.answer += " " + result.summary
            except errors.GuideError:
                pass
    elif b.risk == "R6" and intent.name == "secret":
        out.actions.append(nav("Open developer platform", "/organization/developers"))
    return out


# ── R3 / R4 proposals ────────────────────────────────────────────────────────────────

def _action(db, actor, ctx, intent: Intent, event, conversation_id) -> Composed:
    spec = actions.CATALOG[intent.action]
    if spec.needs == "edit_event" and event is None:
        return _choose_event(db, actor, intent.name,
                             "Which event should I change? Pick one and ask again.")
    if intent.action == "access_link.revoke" and not intent.params.get("link_id"):
        return _choose_link(db, actor, event)
    proposal = actions.propose(db, actor, intent.action, intent.params, event=event,
                               conversation_id=conversation_id, context=context_svc.public(ctx))
    if proposal["type"] == "info":
        return Composed(proposal["text"], intent.name, outcome="no_change")
    risk_note = ("This is a material change; it is checked against your permissions again "
                 "when you confirm." if spec.risk == "R4" else "")
    return Composed(("Review this change. Nothing happens until you confirm. " + risk_note).strip(),
                    intent.name, [proposal], mode=_mode(event), outcome="proposed",
                    tools=[intent.action], correlation_id=proposal.get("correlation_id"))


def _choose_event(db, actor, intent_name: str, prompt: str) -> Composed:
    options = [{"label": e["title"], "detail": e["status"].replace("_", " ").title()
                + (f" · {e['start_time'][:10]}" if e["start_time"] else ""),
                "value": {"event_id": e["id"]}}
               for e in context_svc.selectable_events(db, actor, limit=8)]
    if not options:
        return Composed("Your organization has no events yet.", intent_name,
                        outcome="needs_input")
    return Composed(prompt, intent_name, [{"type": "choices", "kind": "event",
                                           "title": "Choose an event", "options": options,
                                           "resend": True}], outcome="needs_input")


def _choose_link(db, actor, event) -> Composed:
    links = db.scalars(select(EventAccessLink).where(
        EventAccessLink.event_id == event.id, EventAccessLink.org_id == actor.org_id,
        EventAccessLink.revoked_at.is_(None)).order_by(EventAccessLink.created_at.desc())).all()
    active = [lk for lk in links if lk.expires_at is None or lk.expires_at > now()]
    if not active:
        return Composed(f"{event.title} has no active access links.", "access_link.revoke",
                        outcome="no_change")
    options = [{"label": lk.label or f"Link created {lk.created_at:%d %b %Y}",
                "detail": f"Used {lk.uses} time(s)" + (f" · expires {lk.expires_at:%d %b %H:%M}"
                                                      if lk.expires_at else " · no expiry"),
                "value": {"action_id": "access_link.revoke", "params": {"link_id": str(lk.id)}}}
               for lk in active[:10]]
    return Composed("Which access link should I revoke?", "access_link.revoke",
                    [{"type": "choices", "kind": "proposal", "title": "Active access links",
                      "options": options, "resend": False}], outcome="needs_input")


# ── Escalation (§8 Escalation Card) ─────────────────────────────────────────────────

def escalation(db, actor: Actor, event, problem: str | None = None) -> dict:
    """The human routes this user is permitted. Nothing is contacted or created here: an org
    admin gets an R3 support-case PROPOSAL to review and confirm; a host is pointed to their
    organization admin (the support workflow is admin-only on the platform)."""
    admin = policy.is_org_admin(actor.user)
    routes: list[dict] = []
    if admin:
        if governance.settings_for(db, actor.org_id)["capabilities"].get("actions", True):
            detail = problem or "I need help from ZoikoStream support."
            routes.append({"kind": "proposal", "label": "Prepare a support case",
                           "action_id": "support.open_case", "params": {"text": detail},
                           "note": "You review the case before anything is sent."})
        routes.append({"kind": "navigate", **nav("Open Support & Status", "/organization/support")})
        text = ("I can prepare a support case for you to review, with this context attached. "
                "Nothing is sent until you confirm it.")
    else:
        routes.append({"kind": "contact", "label": "Contact your organization admin",
                       "text": "Organization admins open support cases, from Guide or from "
                               "Support & Status."})
        routes.append({"kind": "navigate", **nav("Check ZoikoStream status", "/status")})
        text = ("Support cases are opened by your organization admin. Share this context with "
                "them; Guide does not contact anyone on your behalf.")
    if event is not None and event.status in ("live", "degraded"):
        routes.append({"kind": "navigate", **nav("Open live console",
                                                 f"/host/dashboard?event={event.id}")})
    return escalation_card("Get human help", text, routes,
                           context={"event": event.title if event is not None else None,
                                    "summary": problem})


def _escalate(db, actor: Actor, event) -> Composed:
    card = escalation(db, actor, event,
                      f"Help needed with {event.title}." if event is not None else None)
    answer = ("I can't contact anyone myself. " +
              ("Here is how to reach ZoikoStream support." if policy.is_org_admin(actor.user)
               else "Your organization admin can open a support case for you."))
    return Composed(answer, "escalate", [card], mode=_mode(event), outcome="escalation_offered")


# ── R2 drafts ────────────────────────────────────────────────────────────────────────

_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")


def _draft(db, actor, intent: Intent, event, text: str) -> Composed:
    policy.require_capability(db, actor, "drafts")
    kind = intent.params.get("kind", "description")
    facts = {"kind": kind}
    if event is not None:
        facts.update(title=event.title, when=_fmt_when(event.start_time, event.timezone),
                     visibility=event.visibility, registration_required=event.registration_required)
    result = llm.generate("draft", text, facts)
    # A model outage still yields a usable, fact-only template draft.
    data = (result.data if result.status == "ok" and result.data
            else llm.MockProvider().generate("draft", text, facts).data)
    subject = _clean_draft(data.get("subject", ""))[:200]
    body = _clean_draft(data.get("body", ""))[:4000]
    if not body:
        return Composed("I couldn't produce a usable draft. Please try again.", intent.name,
                        outcome="failed")
    block = {"type": "draft", "kind": kind, "subject": subject or None, "body": body,
             "label": "Draft — not sent or saved"}
    if kind == "description" and event is not None and policy.can_edit_event(db, actor, event):
        block["apply"] = {"label": "Use as event description",
                          "action_id": "event.update_description", "params": {"text": body}}
    return Composed("Here is a draft. Review and edit it before you use it." +
                    ("" if event is not None else " Select an event to include its details."),
                    intent.name, [block], model_release=result.release or None,
                    outcome="drafted", tools=["draft." + kind])


_MD_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1")


def _clean_draft(text: str) -> str:
    """Drafts render as plain text: no links (placeholders instead), no credentials, no
    Markdown emphasis."""
    text = _URL.sub("[link]", text or "")
    text = _MD_EMPHASIS.sub(r"\2", text)
    text = detect_secrets(text).text
    return _brand(text).strip()


# ── R0 / R1 reads ────────────────────────────────────────────────────────────────────

def _read(db, actor, ctx, intent: Intent, event, text: str,
          history: list[dict] | None = None) -> Composed:
    tool_ids = [t for t in intent.tools if t in REGISTRY]
    if event is None and tool_ids and all(t in EVENT_TOOLS for t in tool_ids):
        return _choose_event(db, actor, intent.name, "Which event do you mean?")
    out = Composed("", intent.name, mode=_mode(event))
    summaries, facts = [], {}
    if intent.name == "attention":
        found = attention.items(db, actor)
        out.blocks.append({"type": "attention", "title": "Needs attention",
                           "items": found[:8]})
        summaries.append(f"{len(found)} item(s) need attention." if found else
                         "Nothing needs your attention right now.")
    for tool_id in tool_ids:
        tool = REGISTRY[tool_id]
        if tool.needs_event and event is None:
            continue
        policy.require_capability(db, actor, tool.capability)
        result = run_tool(tool_id, db, actor, ToolContext(event=event, route=ctx.get("route", "")))
        out.tools.append(tool_id)
        out.blocks += result.blocks
        out.sources += result.sources
        out.actions += [a for a in result.actions if a not in out.actions]
        if result.summary and intent.name != "attention":
            summaries.append(result.summary)
        facts[tool_id] = result.facts
        if result.mode == "live":
            out.mode = "live"
        if result.inference:
            # R1: interpretation is disclosed as its own source class, never as state.
            out.inference = True
            out.sources.append(Source(id=f"inference:{tool_id}", label="Guide interpretation",
                                      source_class="inference", owner="ZoikoStream Guide",
                                      updated_at=now(), detail={"tool": tool_id}))
        if tool_id == "org.developer" and not result.facts.get("restricted"):
            out.technical = technical_context(actor)
    if intent.docs:
        _attach_docs(out, text, actor)
    out.answer = " ".join(summaries[:2]) or (out.answer or GAP)
    triage = facts.get("event.incidents") or {}
    if event is not None and (triage.get("confirmed_incidents") or triage.get("detected")):
        out.blocks.append(escalation(db, actor, event, f"{event.title}: {summaries[0]}"
                                     if summaries else None))
    if out.mode != "live" and facts:
        out.explanation, out.model_release = _explain(text, out.answer, facts, history)
        if out.explanation:
            out.inference = True
            out.sources.append(Source(id="inference:explanation",
                                      label="Guide interpretation · model explanation",
                                      source_class="inference", owner="ZoikoStream Guide",
                                      updated_at=now(), version=out.model_release))
    out.suggestions = [s for s in suggestions_for_ctx(ctx, actor)
                       if s.rstrip("?").lower() not in text.lower()]
    return out


def _mode(event) -> str:
    return "live" if event is not None and event.status in ("live", "degraded") else "standard"


# ── Documentation ────────────────────────────────────────────────────────────────────

def _docs(db, actor, ctx: dict, text: str, event, intent: Intent | None = None) -> Composed:
    policy.require_capability(db, actor, "docs_qa")
    name = intent.name if intent is not None else "docs"
    out = Composed("", name, mode=_mode(event))
    out.suggestions = suggestions_for_ctx(ctx, actor)
    blocked = actions.BLOCKED.get((intent.params if intent is not None else {}).get("blocked"))
    found = _attach_docs(out, text, actor)
    if blocked is not None:
        # §25: a how-to question about an operation Guide may not perform gets the docs AND
        # an honest note — it is never refused as if it were the operation itself.
        note = f"Guide can't do this for you. {blocked.what} is done in {blocked.where}."
        out.blocks.insert(0, notice("info", note, rule=f"{blocked.risk} · Guide boundary"))
        out.answer = f"{blocked.what} is done in {blocked.where}. Guide can't do it for you."
        return out
    if not found:
        out.answer, out.outcome = GAP, "gap"
        return out
    first = next(b for b in out.blocks if b["type"] == "docs")["items"][0]
    out.answer = _first_sentences(first["text"], 2)
    return out


def _attach_docs(out: Composed, text: str, actor: Actor | None = None) -> bool:
    sections = knowledge.search(text, k=3)
    if not sections:
        return False
    block = {"type": "docs", "title": "From ZoikoStream documentation",
             "items": [{"heading": s.heading, "doc": s.doc_title, "text": s.text,
                        "source_id": s.id, "path": s.path, "version": s.version,
                        **({"api": s.api} if s.api else {})} for s in sections]}
    if actor is not None and sections[0].api:
        # §6.15: technical answers carry the API version and environment.
        out.technical = block["technical"] = technical_context(actor)
        if policy.is_org_admin(actor.user):
            out.actions.append(nav("Open developer platform", "/organization/developers"))
    out.blocks.append(block)
    out.sources += [s.source() for s in sections]
    out.tools.append("docs.search")
    return True


def _first_sentences(text: str, n: int) -> str:
    """The opening sentences of a documentation section, with list bullets flattened."""
    flat = re.sub(r"(^|\n)\s*-\s+", " ", text)
    flat = re.sub(r"\s+", " ", flat).strip()
    return " ".join(re.split(r"(?<=[.!?])\s+", flat)[:n])


# ── Model explanation + validator ────────────────────────────────────────────────────

_ACTED = re.compile(r"(?i)\b(i|i've|i have|we|guide)\s+(have\s+|has\s+|just\s+)?(changed|turned|"
                    r"enabled|disabled|started|stopped|ended|published|fixed|deleted|revoked|"
                    r"updated|resolved|restarted|switched|marked|set)\b|\bhas\s+been\s+(changed|"
                    r"fixed|resolved|restarted|published|deleted|enabled|disabled)\b")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_BRAND = re.compile(r"\b(?:Zoho\s*Stream|Zoiko\s+Stream|Zoikostream|ZOIKOSTREAM)\b")


def _brand(text: str) -> str:
    return _BRAND.sub("ZoikoStream", text)


def validate_explanation(text: str, facts: dict, answer: str) -> str | None:
    """The model's explanation, or None when it cannot be trusted verbatim."""
    if not isinstance(text, str):
        return None
    text = _brand(" ".join(text.split()))
    if not text or len(text) > 700:
        return None
    if _ACTED.search(text) or _URL.search(text) or detect_secrets(text).suspected:
        return None
    grounded = json.dumps(facts, default=str) + " " + answer
    for n in _NUMBER.findall(text):
        if n not in grounded and n.replace(",", "") not in grounded:
            return None
    if _restates(text, answer):
        return None
    return text


def _restates(text: str, answer: str) -> bool:
    """True when the "why" adds nothing: (almost) every word of it is already in the answer."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    known = set(re.findall(r"[a-z0-9]+", answer.lower()))
    return not words or sum(w in known for w in words) / len(words) >= 0.8


def _explain(text: str, answer: str, facts: dict,
             history: list[dict] | None = None) -> tuple[str | None, str | None]:
    result = llm.generate("explain", text, {"direct_answer": answer, **facts}, history=history)
    if result.status != "ok" or not result.data:
        return None, result.release or None
    return validate_explanation(result.data.get("explanation"), facts, answer), result.release
