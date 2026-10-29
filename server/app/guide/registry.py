"""Intent catalog, suggested prompts, destination registry and runtime config.

Destinations are the ONLY source of trusted CTA hrefs (PR-008, API §8.3): the model and the
client name a destination_id; this module resolves it, and an unknown or disabled id resolves
to nothing. Rows live in guide_destination_registry (seeded by migration 0002) so marketing can
change a route without a code release.

Runtime config is read through a short TTL cache (ZST-ARCH-PUB-GUIDE-001 §13): a database
kill switch takes effect within CONFIG_TTL seconds on every instance.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GuideDestination, GuideRuntimeConfig
from .settings import get_settings

CONFIG_TTL = 30.0

# Conversation Flow Appendix A, plus the UI quick-action ids the API examples use.
INTENTS = {
    "I-PRODUCT": "PRODUCT_QA",
    "I-SOLUTION": "DISCOVERY",
    "I-PRICING": "PRICING",
    "I-DEMO": "DEMO",
    "I-SALES": "SALES",
    "I-VIEWER": "VIEWER_ACCESS",
    "I-VIEWER-ACCESS": "VIEWER_ACCESS",
    "I-PLAYBACK": "PLAYBACK",
    "I-AUDIO": "AUDIO",
    "I-CAPTIONS": "CAPTIONS",
    "I-REPLAY": "REPLAY",
    "I-STATUS": "STATUS",
    "I-PRIVACY": "PRIVACY",
    "I-SECURITY": "SECURITY_TRUST",
    "I-DEVELOPER": "DEVELOPER",
    "I-HUMAN": "HANDOFF",
}

_LABELS = {
    "I-PRODUCT": "Product overview",
    "I-SOLUTION": "Find the right solution",
    "I-PRICING": "Pricing & plans",
    "I-DEMO": "Request a demo",
    "I-SALES": "Talk to sales",
    "I-VIEWER": "Viewer help",
    "I-PLAYBACK": "Playback isn't working",
    "I-CAPTIONS": "Captions",
    "I-REPLAY": "Replay",
    "I-STATUS": "Service status",
    "I-PRIVACY": "Privacy",
    "I-SECURITY": "Security",
    "I-DEVELOPER": "Developer docs",
    "I-HUMAN": "Contact a person",
}

# PR-002: suggestions come from this approved catalog by public page type; nothing account-
# or event-specific is ever suggested in anonymous mode.
_SUGGESTIONS = {
    "default": ["I-PRODUCT", "I-PRICING", "I-DEMO", "I-VIEWER", "I-SALES"],
    "pricing": ["I-PRICING", "I-DEMO", "I-SALES", "I-PRODUCT"],
    "viewer": ["I-VIEWER", "I-PLAYBACK", "I-CAPTIONS", "I-REPLAY"],
    "event": ["I-VIEWER", "I-PLAYBACK", "I-CAPTIONS", "I-REPLAY"],
    "help": ["I-VIEWER", "I-PLAYBACK", "I-STATUS", "I-HUMAN"],
    "status": ["I-STATUS", "I-PLAYBACK", "I-HUMAN"],
    "trust": ["I-SECURITY", "I-PRIVACY", "I-PRODUCT", "I-SALES"],
    "privacy": ["I-PRIVACY", "I-SECURITY", "I-HUMAN"],
    "developer": ["I-DEVELOPER", "I-PRODUCT", "I-SALES"],
    "contact": ["I-DEMO", "I-SALES", "I-VIEWER", "I-PRODUCT"],
}


# Engineering Wireframe WF-10 button copy for the viewer/event page card.
_VIEWER_LABELS = {
    "I-VIEWER": "Join / access help",
    "I-PLAYBACK": "Playback help",
    "I-CAPTIONS": "Captions",
    "I-REPLAY": "Replay availability",
}


def suggested_intents(page_type: str) -> list[dict]:
    ids = _SUGGESTIONS.get(page_type, _SUGGESTIONS["default"])
    labels = {**_LABELS, **_VIEWER_LABELS} if page_type in ("viewer", "event") else _LABELS
    return [{"intent_id": i, "label": labels[i], "priority": 10 - n} for n, i in enumerate(ids)]


# Destination rows are seeded and corrected by migrations 0002/0004; edit the table, not code.


@dataclass(frozen=True)
class Destination:
    destination_id: str
    label: str
    action_type: str
    href: str | None


_cache_lock = threading.Lock()
_dest_cache: tuple[float, dict[str, Destination]] | None = None
_config_cache: tuple[float, dict[str, dict]] | None = None


def invalidate() -> None:
    global _dest_cache, _config_cache
    with _cache_lock:
        _dest_cache, _config_cache = None, None


def destinations(db: Session) -> dict[str, Destination]:
    global _dest_cache
    cached = _dest_cache
    if cached and time.monotonic() - cached[0] < CONFIG_TTL:
        return cached[1]
    rows = db.scalars(select(GuideDestination).where(GuideDestination.enabled.is_(True))).all()
    result = {}
    for r in rows:
        if r.href_kind == "relative" and not _is_safe_relative(r.href_value):
            continue  # a malformed row is dropped, never rendered (Figma §14 "Broken CTA")
        href = r.href_value if r.href_kind != "none" else None
        result[r.destination_id] = Destination(r.destination_id, r.label, r.action_type, href)
    with _cache_lock:
        _dest_cache = (time.monotonic(), result)
    return result


def resolve(db: Session, destination_id: str) -> Destination | None:
    return destinations(db).get(destination_id)


# Independently switchable capabilities (Engineering Wireframe FE-10; ARCH §19 G1–G3).
FEATURES = ("leads", "viewer_support", "generation")
# Actions that belong to a capability disappear while it is off.
_ACTION_FEATURE = {"start_lead_flow": "leads", "start_support_flow": "viewer_support"}


def feature_enabled(db: Session, feature: str) -> bool:
    """On unless switched off — by GUIDE_DISABLED_FEATURES (deploy-time) or by the runtime
    config row `guide.feature.<name>` = {"enabled": false} (takes effect within CONFIG_TTL,
    no deploy)."""
    if feature in get_settings().disabled_features():
        return False
    value = runtime_config(db).get(f"guide.feature.{feature}")
    return not (isinstance(value, dict) and value.get("enabled") is False)


def action_for(db: Session, destination_id: str, *, label: str | None = None) -> dict | None:
    """Public action payload for a registered destination, or None (caller omits it)."""
    d = resolve(db, destination_id)
    if d is None:
        return None
    feature = _ACTION_FEATURE.get(d.action_type)
    if feature and not feature_enabled(db, feature):
        return None
    action = {"action_id": d.destination_id, "type": d.action_type, "label": label or d.label}
    if d.href:
        action["href"] = d.href
    return action


def _is_safe_relative(href: str | None) -> bool:
    return bool(href) and href.startswith("/") and not href.startswith("//") and \
        "\\" not in href and ":" not in href.split("?")[0]


def runtime_config(db: Session) -> dict[str, dict]:
    global _config_cache
    cached = _config_cache
    if cached and time.monotonic() - cached[0] < CONFIG_TTL:
        return cached[1]
    rows = db.scalars(select(GuideRuntimeConfig)).all()
    result = {r.config_key: r.config_value for r in rows}
    with _cache_lock:
        _config_cache = (time.monotonic(), result)
    return result


def db_kill_switch_engaged(db: Session) -> bool:
    value = runtime_config(db).get("guide.enabled")
    return isinstance(value, dict) and value.get("enabled") is False
