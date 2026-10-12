"""Public event context token verification (API §18; Conversation Flow §16).

A viewer page may hand Guide an opaque, short-lived signed token describing ONLY public-safe
facts about the event being watched. Guide never looks an event up by id — there is no
anonymous event directory to enumerate.

Validation: HS256 signature, issuer, audience, exp/iat, maximum lifetime, then a strict
per-claim schema. Unknown claims are dropped (never stored or propagated — ARCH-AT-06,
DB-AT-03). Any failure yields None and the caller falls back to generic viewer help; the
reason is logged by class only and never reflected to the client (API §18, Security §23).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from jose import JWTError, jwt

from . import crypto
from .settings import get_settings

log = logging.getLogger(__name__)

PUBLIC_STATUS_VOCAB = ("scheduled", "starting_soon", "live", "ended", "replay_available",
                       "cancelled", "postponed")
REPLAY_STATES = ("available", "unavailable", "pending", "unknown")
_LANG = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_ROUTE = re.compile(r"^[a-z0-9-]{1,48}$")


def _text(v, maxlen):
    return v.strip()[:maxlen] if isinstance(v, str) and v.strip() else None


def _iso(v):
    if not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _langs(v):
    if not isinstance(v, list):
        return None
    out = [x for x in v if isinstance(x, str) and _LANG.match(x)][:20]
    return out


# claim -> validator. Anything not listed here is discarded.
ALLOWED_CLAIMS = {
    "event_display_name": lambda v: _text(v, 160),
    "public_start_at": _iso,
    "public_timezone": lambda v: _text(v, 64),
    "public_status": lambda v: v if v in PUBLIC_STATUS_VOCAB else None,
    "caption_languages": _langs,
    "captions_available": lambda v: v if isinstance(v, bool) else None,
    "replay_public_state": lambda v: v if v in REPLAY_STATES else None,
    "organizer_contact_route": lambda v: v if isinstance(v, str) and _ROUTE.match(v) else None,
    "public_support_code": lambda v: _text(v, 32),
    "locale": lambda v: v if isinstance(v, str) and _LANG.match(v) else None,
}
_RESERVED = {"iss", "aud", "exp", "iat", "nbf", "jti", "sub"}


@dataclass
class VerifiedEventContext:
    safe_claims: dict
    fingerprint: bytes
    expires_at: datetime


def verify(token: str | None) -> VerifiedEventContext | None:
    if not token:
        return None
    s = get_settings()
    if not s.GUIDE_EVENT_CONTEXT_KEY:
        log.info("public event context token ignored: verifier key not configured")
        return None
    if len(token) > 4096:
        log.info("public event context rejected: oversized")
        return None
    try:
        claims = jwt.decode(
            token, s.GUIDE_EVENT_CONTEXT_KEY, algorithms=["HS256"],
            audience=s.GUIDE_EVENT_CONTEXT_AUDIENCE, issuer=s.GUIDE_EVENT_CONTEXT_ISSUER,
            options={"require_exp": True, "require_iat": True, "require_aud": True,
                     "require_iss": True},
        )
    except JWTError as exc:
        log.info("public event context rejected: %s", type(exc).__name__)
        return None
    exp = datetime.fromtimestamp(int(claims["exp"]), timezone.utc)
    iat = datetime.fromtimestamp(int(claims["iat"]), timezone.utc)
    if (exp - iat).total_seconds() > s.GUIDE_EVENT_CONTEXT_MAX_TTL_SECONDS:
        log.info("public event context rejected: lifetime exceeds maximum")
        return None
    safe: dict = {}
    dropped = 0
    for key, value in claims.items():
        if key in _RESERVED:
            continue
        validator = ALLOWED_CLAIMS.get(key)
        cleaned = validator(value) if validator else None
        if cleaned is None:
            dropped += 1
            continue
        safe[key] = cleaned
    if dropped:
        log.info("public event context: %d claim(s) dropped by allowlist", dropped)
    return VerifiedEventContext(safe_claims=safe, fingerprint=crypto.digest(token),
                                expires_at=exp)


def issue(claims: dict, *, ttl_seconds: int = 3600) -> str:
    """Mint a token. For the viewer page issuer and tests; the Guide itself never needs to."""
    s = get_settings()
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {**claims, "iss": s.GUIDE_EVENT_CONTEXT_ISSUER,
               "aud": s.GUIDE_EVENT_CONTEXT_AUDIENCE, "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, s.GUIDE_EVENT_CONTEXT_KEY, algorithm="HS256")
