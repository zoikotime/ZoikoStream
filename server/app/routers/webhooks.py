"""LiveKit webhook receiver.

LiveKit signs each webhook with the same API key/secret already configured for
token-issuing and Egress calls (LIVEKIT_API_KEY / LIVEKIT_API_SECRET) -- there's no
separate webhook signing secret to set up.

To wire it up:
  - LiveKit Cloud: project Settings -> Webhooks -> add URL, e.g.
    https://your-public-host/webhooks/livekit
  - Self-hosted: add to livekit.yaml:
      webhook:
        api_key: <LIVEKIT_API_KEY>
        urls:
          - https://your-public-host/webhooks/livekit
  - Local dev: LiveKit (Cloud or self-hosted) must be able to reach this endpoint over
    the public internet, so run a tunnel (e.g. `ngrok http 8000`) and use its https URL
    above. Without a tunnel, recordings still finalize via the /refresh poll fallback
    (see routers/recordings.py) -- just not instantly.
"""
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from livekit.api import TokenVerifier, WebhookReceiver
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models.recording import Recording
from app.services.recording import apply_egress_update

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

_receiver: WebhookReceiver | None = None


def _get_receiver() -> WebhookReceiver:
    global _receiver
    if _receiver is None:
        _receiver = WebhookReceiver(TokenVerifier(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET))
    return _receiver


@router.post("/livekit")
async def livekit_webhook(request: Request, authorization: str = Header(...)):
    body = await request.body()
    try:
        event = _get_receiver().receive(body.decode(), authorization)
    except Exception:
        log.warning("Rejected LiveKit webhook with invalid signature")
        raise HTTPException(401, "Invalid webhook signature")

    if event.event == "egress_ended" and event.egress_info:
        with SessionLocal() as db:
            recording = db.scalar(select(Recording).where(Recording.egress_id == event.egress_info.egress_id))
            if recording:
                apply_egress_update(recording, event.egress_info)
                db.commit()
            else:
                log.warning("egress_ended webhook for unknown egress_id %s", event.egress_info.egress_id)

    return {"received": True}
