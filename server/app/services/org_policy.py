"""Organization security-policy enforcement (ZST-EC-001 Phase 10).

The audit found three settings that were stored, displayed and never read:
`min_password_length`, `session_timeout` and `allowed_domains`. A settings page that claims
to enforce a password floor while the backend ignores it is worse than having no setting —
an administrator believes a control is in place that is not.

This module is where each of the three becomes real. Each function takes the organization
and returns an enforceable answer; the callers are the password paths, the token issuer and
the invitation path respectively.

One rule runs through all of it: an organization policy may only ever be STRICTER than the
platform baseline. A tenant cannot lower the global minimum — `max()` not `min()` — because
a per-tenant setting must not become a way to weaken the product.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from ..config import settings as app_settings
from ..models import Organization

log = logging.getLogger(__name__)

# Platform baseline. An organization may raise these, never lower them.
BASELINE_MIN_PASSWORD_LENGTH = 8
MAX_MIN_PASSWORD_LENGTH = 64

# Accepted session-timeout values, mapped to real token lifetimes. A free-text string cannot
# be enforced, so the vocabulary is fixed and anything unrecognized falls back to the
# platform default rather than being silently honoured as something it is not.
SESSION_TIMEOUTS = {
    "1 hour": timedelta(hours=1),
    "4 hours": timedelta(hours=4),
    "8 hours": timedelta(hours=8),
    "12 hours": timedelta(hours=12),
    "24 hours": timedelta(hours=24),
}


def _security(org: Organization | None) -> dict:
    return (getattr(org, "security", None) or {}) if org is not None else {}


# ── min_password_length ─────────────────────────────────────────────────────────────────

def min_password_length(org: Organization | None) -> int:
    """The effective minimum, never below the platform baseline."""
    raw = _security(org).get("min_password_length")
    try:
        requested = int(raw)
    except (TypeError, ValueError):
        return BASELINE_MIN_PASSWORD_LENGTH
    return max(BASELINE_MIN_PASSWORD_LENGTH, min(requested, MAX_MIN_PASSWORD_LENGTH))


def password_violation(org: Organization | None, password: str) -> str | None:
    """The refusal message, or None when the password satisfies the policy."""
    floor = min_password_length(org)
    if len(password or "") < floor:
        return f"Password must be at least {floor} characters for this Organization."
    return None


# ── session_timeout ─────────────────────────────────────────────────────────────────────

def session_lifetime(org: Organization | None, *, remember: bool = False) -> timedelta:
    """Token lifetime for this organization.

    Only applied when the organization's configured timeout is SHORTER than the platform
    default — the setting exists to tighten a tenant's sessions, not to let one tenant hold
    a token for longer than the product allows.

    `remember` keeps the long-lived path, but still clamps to the organization's policy when
    that policy is shorter, so "remember me" cannot be used to escape a tenant's own rule.
    """
    default = (timedelta(days=app_settings.REMEMBER_TOKEN_DAYS) if remember
               else timedelta(hours=app_settings.ACCESS_TOKEN_HOURS))
    configured = SESSION_TIMEOUTS.get((_security(org).get("session_timeout") or "").strip())
    if configured is None:
        return default
    return min(default, configured)


def session_timeout_label(org: Organization | None) -> str:
    """What the console may truthfully display."""
    raw = (_security(org).get("session_timeout") or "").strip()
    return raw if raw in SESSION_TIMEOUTS else f"{app_settings.ACCESS_TOKEN_HOURS} hours"


# ── allowed_domains ─────────────────────────────────────────────────────────────────────

_DOMAIN_SPLIT = re.compile(r"[,\s;]+")


def allowed_domains(org: Organization | None) -> list[str]:
    """Normalized domain allowlist. Empty means no restriction."""
    raw = (_security(org).get("allowed_domains") or "").strip()
    if not raw:
        return []
    out = []
    for part in _DOMAIN_SPLIT.split(raw):
        cleaned = part.strip().lower().lstrip("@")
        if cleaned and "." in cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def domain_violation(org: Organization | None, email: str) -> str | None:
    """Refusal message when an address is outside the organization's allowlist.

    Applied to INVITATION creation only. It deliberately does not gate sign-in: an existing
    member whose address predates the policy would otherwise be locked out of an
    organization they legitimately belong to, which is a support incident rather than a
    security improvement. Restricting who may be newly invited is the enforceable half.
    """
    domains = allowed_domains(org)
    if not domains:
        return None
    address = (email or "").strip().lower()
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    # A subdomain of an allowed domain is allowed; an unrelated domain that merely ends with
    # the same characters is not (so "notexample.com" never matches "example.com").
    for allowed in domains:
        if domain == allowed or domain.endswith("." + allowed):
            return None
    return (f"This Organization only allows members from: {', '.join(domains)}.")
