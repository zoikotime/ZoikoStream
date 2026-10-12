"""Tool Gateway contract (ZST-UX-GUIDE-001 §14).

Every tool has a stable identifier, owning service, risk class, required capability and a
typed result. Read tools (R0/R1) return evidence BLOCKS — the operational card components of
§8 — plus the sources they came from. Tools never return credentials: each one builds its
output field by field from an allowlist, never by serialising a platform row.

There is deliberately no generic "call any API" tool (§14).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from ..policy import Actor
from ..sources import Source

RISK_CLASSES = ("R0", "R1", "R2", "R3", "R4", "R5", "R6")

# Row states shared by every card; the UI renders text + icon + colour, never colour alone.
STATES = ("ready", "review", "conditional", "blocked", "stable", "degraded", "down",
          "unknown", "active", "inactive", "info", "confirmed", "detected", "suspected")


@dataclass
class ToolContext:
    event: object | None = None          # resolved, tenant-checked Event or None
    range_key: str = "30d"
    route: str = ""


@dataclass
class ToolResult:
    tool: str
    blocks: list[dict] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    facts: dict = field(default_factory=dict)        # compact facts the model may explain
    summary: str = ""                                # deterministic direct answer
    actions: list[dict] = field(default_factory=list)  # navigation actions (never execution)
    mode: str = "standard"                           # standard | live
    inference: bool = False                          # R1: result includes interpretation

    def source_ids(self) -> list[str]:
        return [s.id for s in self.sources]


@dataclass(frozen=True)
class Tool:
    id: str
    title: str
    owner: str                     # owning platform service (§14)
    risk: str                      # R0 read | R1 analyze
    capability: str                # governance capability required
    needs_event: bool
    handler: Callable[[Session, Actor, ToolContext], ToolResult]


REGISTRY: dict[str, Tool] = {}


def register(*, id: str, title: str, owner: str, risk: str, capability: str,
             needs_event: bool = False):
    if risk not in ("R0", "R1"):
        raise ValueError("read tools are R0/R1; writes are governed actions (actions.py)")

    def deco(fn):
        REGISTRY[id] = Tool(id, title, owner, risk, capability, needs_event, fn)
        return fn
    return deco


# ── Block builders (§8 component system) ─────────────────────────────────────────────

def row(label: str, state: str, detail: str | None = None, value: str | None = None) -> dict:
    assert state in STATES, state
    out = {"label": label, "state": state}
    if detail:
        out["detail"] = detail
    if value is not None:
        out["value"] = value
    return out


def status_card(title: str, rows: list[dict], sources: list[Source], *,
                subtitle: str | None = None) -> dict:
    return {"type": "status", "title": title, "subtitle": subtitle, "rows": rows,
            "source_ids": [s.id for s in sources]}


def readiness_card(title: str, overall: str, overall_label: str, rows: list[dict],
                   sources: list[Source], *, note: str | None = None) -> dict:
    return {"type": "readiness", "title": title, "overall": {"state": overall,
                                                             "label": overall_label},
            "rows": rows, "note": note, "source_ids": [s.id for s in sources]}


def metric_card(title: str, metrics: list[dict], sources: list[Source], *, scope: str,
                note: str | None = None) -> dict:
    return {"type": "metric", "title": title, "metrics": metrics, "scope": scope,
            "note": note, "source_ids": [s.id for s in sources]}


def metric(label: str, value, *, definition: str, quality: str = "measured",
           unit: str | None = None) -> dict:
    return {"label": label, "value": value, "unit": unit, "definition": definition,
            "quality": quality}


def diagnostic_card(title: str, assessment: str, rows: list[dict], sources: list[Source]) -> dict:
    return {"type": "diagnostic", "title": title, "assessment": assessment, "rows": rows,
            "source_ids": [s.id for s in sources]}


def timeline_card(title: str, items: list[dict], sources: list[Source]) -> dict:
    return {"type": "timeline", "title": title, "items": items,
            "source_ids": [s.id for s in sources]}


def notice(kind: str, text: str, *, rule: str | None = None) -> dict:
    assert kind in ("warning", "blocker", "info")
    return {"type": kind, "text": text, "rule": rule}


def nav(label: str, href: str) -> dict:
    assert href.startswith("/") and not href.startswith("//")
    return {"type": "navigate", "label": label, "href": href}
