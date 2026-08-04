"""Inbound provider webhooks that are not scoped to one feature.

  POST /webhooks/resend   email delivery events -> Invitation.delivered_at / send_error

The LiveKit webhook lives in routers/live.py because it drives that module's presence and
bus; this one stands alone because email delivery touches invitations only.

Both follow the same rule: an UNSIGNED body is never trusted. With no secret configured the
endpoint returns 503 rather than accepting and ignoring, so a misconfiguration is loud.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import settings
from ..crud import organization as crud
from ..db import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Svix (which Resend uses) signs `{id}.{timestamp}.{body}`. Anything older than this is
# refused, which is what stops a captured request being replayed later.
TOLERANCE_SECONDS = 300


def _verify(secret: str, svix_id: str, svix_timestamp: str, svix_signature: str, body: str) -> bool:
    """Constant-time Svix signature check.

    Four things here are load-bearing, and each is a real failure mode:
      * the secret is base64 AFTER the `whsec_` prefix — HMAC-ing the literal prefixed string
        makes every signature mismatch;
      * the signed payload is the RAW body, so the caller must hand us bytes off the wire and
        never a re-serialized model (key order and unicode escaping differ);
      * the header can carry SEVERAL space-separated `v1,<sig>` entries during key rotation,
        so all of them must be tried;
      * comparison is hmac.compare_digest, never `==`.
    """
    try:
        drift = abs(time.time() - int(svix_timestamp))
    except (TypeError, ValueError):
        return False   # a non-numeric timestamp is a malformed request, not a 500
    if drift > TOLERANCE_SECONDS:
        return False

    try:
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except (binascii.Error, IndexError, ValueError):
        log.error("RESEND_WEBHOOK_SECRET is not a valid Svix secret")
        return False

    expected = base64.b64encode(
        hmac.new(key, f"{svix_id}.{svix_timestamp}.{body}".encode(), hashlib.sha256).digest()
    ).decode()
    for part in (svix_signature or "").split():
        version, _, signature = part.partition(",")
        if version == "v1" and hmac.compare_digest(signature, expected):
            return True
    return False


@router.post("/resend", include_in_schema=False)
async def resend_webhook(
    request: Request,
    svix_id: str = Header(None, alias="svix-id"),
    svix_timestamp: str = Header(None, alias="svix-timestamp"),
    svix_signature: str = Header(None, alias="svix-signature"),
    db: Session = Depends(get_db),
):
    """Record what happened to an invitation email after we handed it to Resend.

    This is the ONLY writer of `delivered_at`, which is why the console can honestly
    distinguish "we sent it" from "it arrived" — with no webhook configured an invitation
    stays `sent` rather than claiming a delivery nobody confirmed.

    Two deliberate constraints:
      * The invitation is matched on `provider_message_id` ALONE. Matching on the recipient
        address would flip every open invitation for that email across ALL organizations —
        an unauthenticated cross-tenant write, and the one place in this codebase where the
        org would not come from a JWT.
      * It writes TIMESTAMPS only, never `status`. An external system must not be able to
        move an invitation's lifecycle, and because every write here is idempotent, a replay
        inside the tolerance window is a no-op. (That property is why there is no svix-id
        replay cache — it would stop being true the moment this handler incremented a counter
        or triggered mail, so neither belongs here.)
    """
    if not settings.RESEND_WEBHOOK_SECRET:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Email delivery webhooks are not configured")
    body = (await request.body()).decode()
    if not _verify(settings.RESEND_WEBHOOK_SECRET, svix_id or "", svix_timestamp or "",
                   svix_signature or "", body):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed webhook body")

    kind = payload.get("type")
    message_id = ((payload.get("data") or {}).get("email_id")) or None
    if not message_id:
        return {"ignored": kind, "reason": "no email_id"}

    if kind == "email.delivered":
        return {"ok": True, "type": kind, "matched": crud.mark_delivered(db, message_id)}
    if kind in ("email.bounced", "email.delivery_delayed"):
        reason = (payload.get("data") or {}).get("reason") or kind
        return {"ok": True, "type": kind, "matched": crud.mark_send_failed(db, message_id, reason)}
    # `email.complained` is a reputation signal AFTER a successful delivery — recording it as
    # a send failure would tell the admin the mail never arrived, which is false.
    log.info("resend webhook: ignoring %s", kind)
    return {"ignored": kind}
