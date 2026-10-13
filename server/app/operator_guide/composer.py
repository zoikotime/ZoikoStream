"""Answer assembly (ZST-UX-GUIDE-001 §7, §8, §15).

Every answer follows the same pattern:
    direct answer  →  current state (cards)  →  why  →  next action  →  sources / freshness

The direct answer and the cards are deterministic — produced by tools from authoritative
state. The model may add a short "why" explanation over the same facts; that text is
validated (numbers must come from the facts, no claims of having acted, no credentials, no
links) and dropped on any doubt. Live Mode (§6.9) is terse: no model text, no suggestions.
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

from . import actions, attention, context as context_svc, errors, knowledge, llm, policy
from .intents import EVENT_TOOLS, Intent
from .policy import Actor
from .sources import Source, now
from .tools import REGISTRY, ToolContext, run as run_tool
from .tools.base import nav, notice
from .tools.events import _fmt_when, event_href

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
                "stale": any(s["stale"] for s in sources)}


# ── Entry point ──────────────────────────────────────────────────────────────────────

def compose(db: Session, actor: Actor, ctx: dict, text: str, intent: Intent, *,
            conversation_id=None) -> Composed:
    event = ctx.get("_event")
    try:
        if intent.kind == "chat":
            return _chat(intent, event)
        if intent.kind == "blocked":
            return _blocked(db, actor, ctx, intent, event)
        if intent.kind == "action":
            return _action(db, actor, ctx, intent, event, conversation_id)
        if intent.kind == "draft":
            return _draft(db, actor, intent, event, text)
        if intent.kind == "read":
            return _read(db, actor, ctx, intent, event, text)
        return _docs(db, actor, text, event)
    except errors.EntitlementBlocked as exc:
        return Composed(exc.message, intent.name, [notice("blocker", exc.message,
                                                          rule="Guide settings")],
                        authorization="capability_off", outcome="capability_off")
    except errors.Forbidden as exc:
        return Composed(exc.message, intent.name, [notice("blocker", exc.message,
                                                          rule="Platform permissions")],
                        authorization="denied", outcome="denied")
    except (errors.ValidationFailed, errors.NotFound, errors.Conflict) as exc:
        return Composed(exc.message, intent.name, outcome="needs_input")


# ── Conversation ─────────────────────────────────────────────────────────────────────

def suggestions_for(event, *, live: bool = False) -> list[str]:
    if event is not None:
        if live:
            return ["How is the broadcast doing?", "Why are viewers dropping?",
                    "Who can access this event?"]
        return ["Is this event ready?", "Who can access this event?", "Where is the replay?",
                "Draft a reminder email"]
    return ["What needs attention?", "Show upcoming events", "How did our events perform?",
            "What does Conditional readiness mean?"]


def _chat(intent: Intent, event) -> Composed:
    text = {
        "greeting": "Hello. " + ("What would you like to know about "
                                 f"{event.title}?" if event is not None else
                                 "What would you like to check?"),
        "identity": IDENTITY,
        "capabilities": CAPABILITIES,
        "thanks": "You're welcome. Anything else to check?",
        "empty": "Ask me about an event, your audience, recordings or how a feature works.",
    }[intent.name]
    return Composed(text, intent.name, suggestions=suggestions_for(event))


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
                    tools=[intent.action])


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

def _read(db, actor, ctx, intent: Intent, event, text: str) -> Composed:
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
    if intent.docs:
        _attach_docs(out, text)
    out.answer = " ".join(summaries[:2]) or (out.answer or GAP)
    if out.mode != "live" and facts:
        out.explanation, out.model_release = _explain(text, out.answer, facts)
    out.suggestions = [s for s in suggestions_for(event, live=out.mode == "live")
                       if s.rstrip("?").lower() not in text.lower()]
    return out


def _mode(event) -> str:
    return "live" if event is not None and event.status in ("live", "degraded") else "standard"


# ── Documentation ────────────────────────────────────────────────────────────────────

def _docs(db, actor, text: str, event) -> Composed:
    policy.require_capability(db, actor, "docs_qa")
    out = Composed("", "docs", mode=_mode(event))
    if not _attach_docs(out, text):
        out.answer, out.outcome = GAP, "gap"
        out.suggestions = suggestions_for(event)
        return out
    first = out.blocks[0]["items"][0]
    out.answer = _first_sentences(first["text"], 2)
    out.suggestions = suggestions_for(event)
    return out


def _attach_docs(out: Composed, text: str) -> bool:
    sections = knowledge.search(text, k=3)
    if not sections:
        return False
    out.blocks.append({"type": "docs", "title": "From ZoikoStream documentation",
                       "items": [{"heading": s.heading, "doc": s.doc_title, "text": s.text,
                                  "source_id": s.id, "path": s.path} for s in sections]})
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


def _explain(text: str, answer: str, facts: dict) -> tuple[str | None, str | None]:
    result = llm.generate("explain", text, {"direct_answer": answer, **facts})
    if result.status != "ok" or not result.data:
        return None, result.release or None
    return validate_explanation(result.data.get("explanation"), facts, answer), result.release
