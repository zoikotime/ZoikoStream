"""Secure viewer invitation links — hardening of the EXISTING invite and access-link flows.

ZST-SPEC-VAP-001 §5.1 asks for a non-PII path, a high-entropy secret in the URL FRAGMENT, and
a client that hands the secret over only on an explicit request and then removes it from the
address bar. This module is that and nothing more: it does not create accounts, sessions or
access policies, and everything after the exchange is the existing registration/viewer flow.

    {base}/events/{event_id}/watch#invite=<secret>   personal emailed invitation
    {base}/events/{event_id}/watch#link=<secret>     host-issued shareable access link

{base} is the organization's ACTIVE custom domain when it has one, else APP_URL
(services/public_urls.py).

The path carries only the event id, a random UUID that names nobody. The fragment is never
part of an HTTP request, so it cannot reach uvicorn's access log, a proxy or a Referer header.
The viewer page POSTs it once to POST /events/{id}/invitation and receives the credential the
existing flow already uses:

  * invite -> the registration token (security.create_registration_token), exactly what
    GET /watch and the live socket have always accepted as `reg`;
  * link   -> a link PASS, accepted wherever the raw access-link token is (`link`).

Why a pass rather than handing the raw link secret back: the secret then travels exactly once,
in a POST body, instead of on every /watch poll and in the socket URL. The pass is bound to
the link row AND its current token hash, so revoking the link refuses it immediately and
rotating the link invalidates every pass minted from the old secret. It also carries a random
per-browser value, so two people who open the same family link get two LiveKit identities
instead of one. LiveKit allows one connection per identity, and with a shared identity the
second viewer used to evict the first.

Both secrets are stateless MACs over the server's SECRET_KEY (a key derived for this purpose
only), so nothing new is stored and no migration is needed:
  * an invite secret = registration id (16 bytes) + expiry (5) + HMAC-SHA256 (32): 256 bits
    of MAC, no email, no name;
  * a link secret is unchanged: secrets.token_urlsafe(32) (256 bits), stored only as sha256.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from ..config import settings
from . import public_urls

INVITE_LIFETIME = timedelta(days=90)     # same as a registration token (security.py)
PASS_LIFETIME = timedelta(days=90)
PASS_PREFIX = "p."                        # distinguishes a pass from a raw link token
_MAC_BYTES = 32
_EXP_BYTES = 5                            # unix seconds; 5 bytes runs to the year 36812
_SUB_BYTES = 8                            # per-browser value inside a link pass


def _key() -> bytes:
    # A key derived for invitation links only, so a MAC from here can never be confused with
    # (or replayed as) a JWT signature made with SECRET_KEY directly.
    return hmac.new(settings.SECRET_KEY.encode(), b"zoikostream/viewer-invitation-links/v1",
                    hashlib.sha256).digest()


def _mac(*parts: bytes) -> bytes:
    return hmac.new(_key(), b"|".join(parts), hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        return None


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


# ── personal invitations (emailed) ───────────────────────────────────────────────────────

def mint_invitation_secret(registration, now: datetime | None = None) -> str:
    exp = int((_now(now) + INVITE_LIFETIME).timestamp()).to_bytes(_EXP_BYTES, "big")
    body = registration.id.bytes + exp
    return _b64(body + _mac(b"invite", registration.event_id.bytes, body))


def read_invitation_secret(secret: str, event_id: uuid.UUID, now: datetime | None = None) -> uuid.UUID | None:
    """The registration id an invitation secret names, or None when it is malformed, for
    another event, tampered with or expired. Constant-time on the MAC."""
    raw = _unb64(secret or "")
    if raw is None or len(raw) != 16 + _EXP_BYTES + _MAC_BYTES:
        return None
    body, mac = raw[:16 + _EXP_BYTES], raw[16 + _EXP_BYTES:]
    if not hmac.compare_digest(mac, _mac(b"invite", event_id.bytes, body)):
        return None
    if int.from_bytes(body[16:], "big") < _now(now).timestamp():
        return None
    return uuid.UUID(bytes=body[:16])


# ── access-link passes ───────────────────────────────────────────────────────────────────

def _pass_mac(event_id: uuid.UUID, token_hash: str, body: bytes) -> bytes:
    return _mac(b"link-pass", event_id.bytes, bytes.fromhex(token_hash), body)


def mint_link_pass(link, now: datetime | None = None) -> str:
    """A credential for one browser that opened `link`. Expires with the link, or after
    PASS_LIFETIME, whichever is sooner."""
    now = _now(now)
    expires = now + PASS_LIFETIME
    if link.expires_at is not None:
        expires = min(expires, link.expires_at)
    exp = int(expires.timestamp()).to_bytes(_EXP_BYTES, "big")
    body = link.id.bytes + secrets.token_bytes(_SUB_BYTES) + exp
    return PASS_PREFIX + _b64(body + _pass_mac(link.event_id, link.token_hash, body))


def is_link_pass(credential: str | None) -> bool:
    return bool(credential) and credential.startswith(PASS_PREFIX)


def parse_link_pass(credential: str) -> tuple[uuid.UUID, str, int] | None:
    """(link id, per-browser value, expiry) WITHOUT verifying the MAC — that needs the link
    row's token hash, see verify_link_pass. None when the shape is wrong."""
    raw = _unb64(credential[len(PASS_PREFIX):]) if is_link_pass(credential) else None
    if raw is None or len(raw) != 16 + _SUB_BYTES + _EXP_BYTES + _MAC_BYTES:
        return None
    return (uuid.UUID(bytes=raw[:16]), raw[16:16 + _SUB_BYTES].hex(),
            int.from_bytes(raw[16 + _SUB_BYTES:16 + _SUB_BYTES + _EXP_BYTES], "big"))


def verify_link_pass(credential: str, link, now: datetime | None = None) -> bool:
    raw = _unb64(credential[len(PASS_PREFIX):]) if is_link_pass(credential) else None
    if raw is None or len(raw) != 16 + _SUB_BYTES + _EXP_BYTES + _MAC_BYTES:
        return False
    body, mac = raw[:-_MAC_BYTES], raw[-_MAC_BYTES:]
    if body[:16] != link.id.bytes:
        return False
    if not hmac.compare_digest(mac, _pass_mac(link.event_id, link.token_hash, body)):
        return False
    return int.from_bytes(body[16 + _SUB_BYTES:], "big") >= _now(now).timestamp()


def link_identity(link_id, sub: str | None = None) -> str:
    """The viewer's LiveKit/presence identity for an access link. A raw link (legacy
    ?link= URLs) keeps the shared `guest-link-<id>`; a pass adds its per-browser value."""
    return f"guest-link-{link_id}-{sub}" if sub else f"guest-link-{link_id}"


# ── URLs ─────────────────────────────────────────────────────────────────────────────────

def invitation_url(event_id, secret: str, org=None) -> str:
    return f"{public_urls.event_watch_url(event_id, org)}#invite={secret}"


def access_link_url(event_id, secret: str, org=None) -> str:
    return f"{public_urls.event_watch_url(event_id, org)}#link={secret}"
