"""Capacity protection for the viewer page: admission control that never evicts.

ZST-SPEC-VAP-001 §6.3 / §7.1: "New sessions wait; active sessions are never evicted", and
"Commercial plan limits must not create a viewer cap" for a live event. In this codebase that
means:

  * An infrastructure ceiling (platform_settings.viewer_admission_ceiling, unset by default)
    can hold a NEW viewer in a waiting state. GET /events/{id}/watch then answers
    admission="waiting" plus retry_after_seconds, with no LiveKit token, and the viewer page
    retries on a bounded backoff.
  * A viewer this broadcast has already admitted is ALWAYS admitted again. A reconnect, a
    refresh or a phone waking up keeps its place, however full the event is.
  * Nothing here, or anywhere it is called from, disconnects a LiveKit subscriber, revokes a
    token or ends a session. The only removals in the system remain the host's own explicit
    moderation actions.
  * Event staff (the event's org members, super admins) and a contributor's own return-feed
    monitor are never held and never take a viewer slot.

Occupancy counts admitted viewers seen within HOLD_SECONDS. The socket heartbeat (every 15s)
keeps an admitted viewer fresh. Someone who closes the tab stops being counted after
HOLD_SECONDS, but still keeps their priority if they come back.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from . import bus, platform_settings

# Comfortably past the socket's own idle reap (routers/live.py IDLE_TIMEOUT = 90s), so a viewer
# whose heartbeat is merely late is never counted as gone.
HOLD_SECONDS = 120
# What the waiting viewer is told. The client adds its own bounded backoff and jitter on
# top (client/src/pages/watch/EventWatch.jsx), so a full event is not hammered in lockstep.
RETRY_AFTER_SECONDS = 10


@dataclass(frozen=True)
class Decision:
    admitted: bool
    occupied: int | None
    ceiling: int | None
    retry_after_seconds: int | None = None


def decide(db: Session, event_id: uuid.UUID, identity: str, *, exempt: bool = False,
           now: float | None = None) -> Decision:
    """May `identity` be handed a stream token for this live event right now?"""
    if exempt:
        return Decision(admitted=True, occupied=None, ceiling=None)
    ceiling = platform_settings.viewer_admission_ceiling(db)
    admitted, occupied = bus.admission_decide_sync(
        event_id, identity, ceiling, HOLD_SECONDS, now=now if now is not None else time.time())
    return Decision(admitted=admitted, occupied=occupied, ceiling=ceiling,
                    retry_after_seconds=None if admitted else RETRY_AFTER_SECONDS)
