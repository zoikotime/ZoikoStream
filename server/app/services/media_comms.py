"""Media operations communications (ZST-EC-001 MED-001, MED-002, MED-004, MED-005).

Every message here reports committed media state, and the two families that could be noisy
are governed by thresholds rather than by whatever a poll happened to see:

  * **MED-002** works on the RAW LiveKit ingress status that `broadcast.record_ingress_status`
    already writes. A disconnect is not an interruption until it has persisted past
    SIGNAL_INTERRUPTION_SECONDS, and a reconnect is not a recovery until the signal has held
    for SIGNAL_RECOVERY_SECONDS. A one-second encoder blip therefore produces nothing at all.

  * **MED-005** reads `broadcast.health_of()` — the existing authoritative calculation — and
    never re-derives health. What this module adds is *transition* detection: the last
    governed level is stored on the session, so DEGRADED is announced when the level moves,
    not every time a poll returns DEGRADED again.

MED-003 is deliberately absent. LiveKit's webhook vocabulary has no authentication-failure
event, so there is no telemetry from which a repeated-auth-failure alert could be built.
Reported as a provider gap rather than invented.

**Audience separation.** MED-004 is an internal operator notice. This module imports no
audience-facing sender, and `test_media_ops` asserts that starting a broadcast session
reaches none of them — internal media state is not audience communication consent.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    SIGNAL_FLAP_THRESHOLD,
    SIGNAL_FLAP_WINDOW_SECONDS,
    SIGNAL_HEALTHY,
    SIGNAL_INTERMITTENT,
    SIGNAL_INTERRUPTED,
    SIGNAL_INTERRUPTION_SECONDS,
    SIGNAL_RECOVERY_SECONDS,
    BroadcastSession,
    Event,
    LiveIngressEndpoint,
    Organization,
    User,
)
from . import org_comms

log = logging.getLogger(__name__)

TICKER_INTERVAL_SECONDS = 30.0

# LiveKit ingress statuses that mean media is actually arriving. Anything else is a loss.
USABLE_STATES = frozenset({"active"})

PROTOCOL_LABELS = {"rtmp": "RTMP", "whip": "WHIP"}

# There is no per-input or per-session region setting, and no LIVEKIT_REGION config — only
# LIVEKIT_URL. LiveKit Cloud selects the ingest edge itself, so this is the honest answer
# rather than echoing the Organization's display region, which is unrelated to media routing.
REGION_LABEL = "Automatically selected by LiveKit"

# There is no Workspace entity; an Organization IS its single implicit workspace
# (services/org.py). Stated rather than invented.
WORKSPACE_LABEL = "Default (this Organization's only workspace)"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _queue(background, send, addresses, **kwargs) -> None:
    try:
        for address in addresses:
            background.add_task(send, address, **kwargs)
    except UnsafeLinkError:
        log.exception("Media notice not queued: APP_URL unsafe for this environment")


# ── recipients ──────────────────────────────────────────────────────────────────────────

def operators(db: Session, org_id, owner_id=None) -> list[str]:
    """Configured operations recipients for one Organization.

    There is no dedicated "operations recipients" setting in this product. Rather than
    emailing arbitrary members, the resolution is explicit and narrow: the resource owner,
    the recorded Organization owner, and org administrators as the documented fallback.
    That fallback is stated in the DEV/MED report rather than presented as a real
    operations-routing feature.
    """
    owner = db.get(User, owner_id) if owner_id else None
    org = db.get(Organization, org_id) if org_id else None
    holder = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    return org_comms.recipients(owner, holder, *org_comms.org_admins(db, org_id))


def _org_of(db: Session, event_id) -> tuple[Organization | None, Event | None]:
    event = db.get(Event, event_id) if event_id else None
    org = db.get(Organization, event.org_id) if event else None
    return org, event


def is_test_org(org: Organization | None) -> bool:
    """Authoritative test mode. `Organization.is_test` is the same stored flag
    services/ops.py uses to keep test tenants out of the live console — never inferred."""
    return bool(org is not None and getattr(org, "is_test", False))


def _claim(db: Session, row, column: str) -> bool:
    if getattr(row, column) is not None:
        return False
    setattr(row, column, _now())
    db.commit()
    return True


def _duration(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return "Unknown"
    seconds = max(0, int((end - start).total_seconds()))
    if seconds < 60:
        return f"{seconds} seconds"
    if seconds < 3600:
        return f"{seconds // 60} minutes"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


# ══ MED-001 ═════════════════════════════════════════════════════════════════════════════

def notify_input_created(db: Session, background, endpoint: LiveIngressEndpoint) -> bool:
    """Announce a committed live input. Called only after the row exists."""
    if not _claim(db, endpoint, "created_notified_at"):
        return False
    org, event = _org_of(db, endpoint.event_id)
    addresses = operators(db, org.id if org else None, endpoint.created_by)
    if not addresses:
        return False

    owner = db.get(User, endpoint.created_by) if endpoint.created_by else None
    # `enforced` is the platform's own honesty flag: False means LiveKit was unconfigured or
    # unreachable at creation, so the row records intent and nothing will actually arrive.
    # Saying "your input is ready" in that case would be untrue.
    enforced_note = ("Provisioned with LiveKit and ready to receive"
                     if endpoint.enforced
                     else "Recorded, but NOT provisioned with LiveKit — no media will "
                          "arrive until it is recreated")
    _queue(background, email_mod.send_live_input_created_email, addresses,
           event_title=event.title if event else "an event",
           input_name=endpoint.title,
           owner=(owner.email if owner else "An authorized operator"),
           org_name=org.name if org else "your Organization",
           workspace=WORKSPACE_LABEL,
           protocol=PROTOCOL_LABELS.get((endpoint.input_type or "").lower(),
                                        (endpoint.input_type or "Unknown").upper()),
           region=REGION_LABEL,
           mode="Test" if is_test_org(org) else "Production",
           created_at=org_comms.org_timestamp(org, endpoint.created_at),
           enforced_note=enforced_note,
           test_mode=is_test_org(org))
    return True


# ══ MED-002 ═════════════════════════════════════════════════════════════════════════════

def observe_signal(db: Session, endpoint: LiveIngressEndpoint) -> None:
    """Fold the current RAW ingress status into the loss clock. Announces nothing.

    Called from the ingress webhook, which is the only place the platform learns that the
    signal changed. Evaluation of whether that loss has persisted long enough to matter
    happens in `evaluate_signal`, on the ticker — because a threshold cannot be judged at
    the instant the signal drops.
    """
    usable = (endpoint.state or "").lower() in USABLE_STATES
    if usable:
        endpoint.last_signal_ok_at = _now()
        if endpoint.signal_lost_at is not None:
            endpoint.signal_lost_at = None
    elif endpoint.signal_lost_at is None:
        endpoint.signal_lost_at = _now()
    db.commit()


def evaluate_signal(db: Session, background, endpoint: LiveIngressEndpoint) -> str | None:
    """Apply the persistence thresholds. Returns the transition, or None.

    This is where a blip is separated from an outage. Nothing is announced until a loss has
    lasted SIGNAL_INTERRUPTION_SECONDS, and nothing is announced as recovered until the
    signal has held for SIGNAL_RECOVERY_SECONDS.
    """
    now = _now()
    usable = (endpoint.state or "").lower() in USABLE_STATES
    org, event = _org_of(db, endpoint.event_id)
    transition = None

    if not usable and endpoint.signal_lost_at is not None:
        down_for = (now - endpoint.signal_lost_at).total_seconds()
        if (down_for >= SIGNAL_INTERRUPTION_SECONDS
                and endpoint.signal_state == SIGNAL_HEALTHY):
            # A confirmed interruption. Count it inside the flap window so a repeating drop
            # can become INTERMITTENT rather than sending an interruption notice each time.
            window_start = endpoint.interruption_window_started_at
            if (window_start is None
                    or (now - window_start).total_seconds() > SIGNAL_FLAP_WINDOW_SECONDS):
                endpoint.interruption_window_started_at = now
                endpoint.interruption_count = 0
            endpoint.interruption_count += 1
            endpoint.signal_changed_at = now
            endpoint.recovered_notified_at = None

            if endpoint.interruption_count >= SIGNAL_FLAP_THRESHOLD:
                endpoint.signal_state = SIGNAL_INTERMITTENT
                transition = SIGNAL_INTERMITTENT
            else:
                endpoint.signal_state = SIGNAL_INTERRUPTED
                transition = SIGNAL_INTERRUPTED
            db.commit()

    elif usable and endpoint.signal_state in (SIGNAL_INTERRUPTED, SIGNAL_INTERMITTENT):
        stable_for = ((now - endpoint.last_signal_ok_at).total_seconds()
                      if endpoint.last_signal_ok_at else 0)
        if stable_for >= SIGNAL_RECOVERY_SECONDS:
            endpoint.signal_state = SIGNAL_HEALTHY
            endpoint.signal_changed_at = now
            # Re-arm the loss markers so a later outage is announced again.
            endpoint.interrupted_notified_at = None
            endpoint.intermittent_notified_at = None
            transition = "recovered"
            db.commit()

    if transition:
        notify_signal(db, background, endpoint, transition, org, event)
    return transition


def notify_signal(db: Session, background, endpoint: LiveIngressEndpoint, transition: str,
                  org=None, event=None) -> bool:
    """One notice per confirmed transition."""
    if org is None or event is None:
        org, event = _org_of(db, endpoint.event_id)
    addresses = operators(db, org.id if org else None, endpoint.created_by)
    if not addresses:
        return False
    shared = {"event_title": event.title if event else "an event",
              "input_name": endpoint.title,
              "org_name": org.name if org else "your Organization",
              "current_state": (endpoint.state or "unknown").title(),
              "test_mode": is_test_org(org)}

    if transition == SIGNAL_INTERRUPTED:
        if not _claim(db, endpoint, "interrupted_notified_at"):
            return False
        _queue(background, email_mod.send_input_interrupted_email, addresses,
               interrupted_at=org_comms.org_timestamp(org, endpoint.signal_lost_at),
               persistence=f"{SIGNAL_INTERRUPTION_SECONDS} seconds", **shared)
        return True

    if transition == SIGNAL_INTERMITTENT:
        if not _claim(db, endpoint, "intermittent_notified_at"):
            return False
        _queue(background, email_mod.send_input_intermittent_email, addresses,
               window=f"{SIGNAL_FLAP_WINDOW_SECONDS // 60} minutes",
               interruptions=endpoint.interruption_count, **shared)
        return True

    if transition == "recovered":
        if not _claim(db, endpoint, "recovered_notified_at"):
            return False
        _queue(background, email_mod.send_input_recovered_email, addresses,
               recovered_at=org_comms.org_timestamp(org, endpoint.signal_changed_at),
               outage=_duration(endpoint.signal_lost_at, endpoint.signal_changed_at),
               **shared)
        return True
    return False


# ══ MED-004 ═════════════════════════════════════════════════════════════════════════════

def notify_session_started(db: Session, background, session: BroadcastSession) -> bool:
    """Internal operator notice. Sent only once the session is authoritatively live."""
    if session.status != "live" or session.started_at is None:
        return False
    if not _claim(db, session, "started_notified_at"):
        return False
    org, event = _org_of(db, session.event_id)
    addresses = operators(db, org.id if org else None, session.created_by)
    if not addresses:
        return False

    active = db.scalar(
        select(LiveIngressEndpoint).where(
            LiveIngressEndpoint.event_id == session.event_id,
            LiveIngressEndpoint.state == "active")
    )
    _queue(background, email_mod.send_session_started_email, addresses,
           event_title=event.title if event else "an event",
           session_reference=str(session.id)[:8],
           started_at=org_comms.org_timestamp(org, session.started_at),
           active_input=active.title if active else "No input publishing yet",
           region=REGION_LABEL,
           mode="Test" if is_test_org(org) else "Production",
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def notify_session_ended(db: Session, background, session: BroadcastSession) -> bool:
    if session.ended_at is None:
        return False
    if not _claim(db, session, "ended_notified_at"):
        return False
    org, event = _org_of(db, session.event_id)
    addresses = operators(db, org.id if org else None, session.created_by)
    if not addresses:
        return False

    # Recording status is read from the recording rows, and only a completed capture is
    # described as available. A recording that is still processing is reported as such —
    # telling an operator a file is ready before it exists is the failure mode here.
    from ..models import LiveRecording

    recording = db.scalar(
        select(LiveRecording).where(LiveRecording.event_id == session.event_id)
        .order_by(LiveRecording.created_at.desc())
    )
    if recording is None:
        recording_status = "No recording was captured"
    elif recording.status == "stopped" and recording.file_url:
        recording_status = "Captured and available"
    elif recording.status == "failed":
        recording_status = "Recording failed"
    else:
        recording_status = f"Recording {recording.status} — not yet available"

    _queue(background, email_mod.send_session_ended_email, addresses,
           event_title=event.title if event else "an event",
           session_reference=str(session.id)[:8],
           started_at=org_comms.org_timestamp(org, session.started_at),
           ended_at=org_comms.org_timestamp(org, session.ended_at),
           duration=_duration(session.started_at, session.ended_at),
           final_state=(session.ended_reason or "ended").replace("_", " ").title(),
           recording_status=recording_status,
           peak_viewers=session.peak_viewers or 0,
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


# ══ MED-005 ═════════════════════════════════════════════════════════════════════════════

# The authoritative levels broadcast.health_of() returns, mapped to what an operator is
# told. The calculation is NOT reproduced here — only its output is interpreted.
LEVEL_TO_VARIANT = {"down": "failed", "warn": "degraded", "ok": "recovered"}
LEVEL_LABELS = {"down": "Failed", "warn": "Degraded", "ok": "Healthy"}
IMPACT = {
    "down": "No media is reaching the room; viewers cannot see or hear the session",
    "warn": "The session is live but one or more signals are degraded",
    "ok": "All observed signals are nominal",
}


def record_health(db: Session, background, session: BroadcastSession, health: dict) -> str | None:
    """Fold one governed health evaluation into the session. Returns the transition, if any.

    `health` is the dict returned by services/broadcast.health_of(). Repeated evaluations
    returning the same level produce nothing — only a CHANGE is a communication event, which
    is what stops a polling loop from mailing an operator every tick.
    """
    level = (health or {}).get("level")
    if level not in LEVEL_TO_VARIANT:
        return None
    previous = session.health_level
    if previous == level:
        return None

    now = _now()
    session.health_level = level
    session.health_issues = json.dumps((health or {}).get("issues") or [])
    incident_start = session.health_changed_at
    session.health_changed_at = now
    # Re-arm the opposite markers so a later transition is announced again.
    if level == "ok":
        session.health_failed_notified_at = None
        session.health_degraded_notified_at = None
    else:
        session.health_recovered_notified_at = None
    db.commit()
    db.refresh(session)

    # The very first evaluation establishes a baseline; there is no transition to announce.
    if previous is None:
        return None
    # Recovery is only meaningful if something was wrong before it.
    variant = LEVEL_TO_VARIANT[level]
    if variant == "recovered" and previous == "ok":
        return None

    notify_health(db, background, session, variant, previous, incident_start)
    return variant


def notify_health(db: Session, background, session: BroadcastSession, variant: str,
                  previous_level: str | None, incident_start: datetime | None) -> bool:
    marker = {"failed": "health_failed_notified_at",
              "degraded": "health_degraded_notified_at",
              "recovered": "health_recovered_notified_at"}[variant]
    if not _claim(db, session, marker):
        return False
    org, event = _org_of(db, session.event_id)
    addresses = operators(db, org.id if org else None, session.created_by)
    if not addresses:
        return False

    issues = json.loads(session.health_issues) if session.health_issues else []
    _queue(background, email_mod.send_session_health_email, addresses,
           variant=variant,
           event_title=event.title if event else "an event",
           session_reference=str(session.id)[:8],
           confirmed_at=org_comms.org_timestamp(org, session.health_changed_at),
           issues="; ".join(issues) if issues else "None",
           previous_state=LEVEL_LABELS.get(previous_level or "", "Unknown"),
           current_state=LEVEL_LABELS.get(session.health_level or "", "Unknown"),
           impact=IMPACT.get(session.health_level or "", "Impact not determined"),
           org_name=org.name if org else "your Organization",
           incident_duration=(_duration(incident_start, session.health_changed_at)
                              if variant == "recovered" else None),
           test_mode=is_test_org(org))
    return True


# ══ ticker ══════════════════════════════════════════════════════════════════════════════

class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — a ticker must not die on one bad send
            log.exception("Media notice failed")


def sweep(db: Session, background=None) -> dict:
    """One pass over inputs with an open loss clock or an unresolved signal state."""
    background = background or _Bg()
    transitions = 0
    for endpoint in db.scalars(
        select(LiveIngressEndpoint).where(
            (LiveIngressEndpoint.signal_lost_at.isnot(None))
            | (LiveIngressEndpoint.signal_state != SIGNAL_HEALTHY))
    ).all():
        if evaluate_signal(db, background, endpoint):
            transitions += 1
    return {"transitions": transitions}


async def run_media_sweeper(interval: float = TICKER_INTERVAL_SECONDS) -> None:
    """Background ticker started from the app lifespan."""
    import asyncio

    from ..db import SessionLocal

    while True:
        try:
            await asyncio.sleep(interval)
            db = SessionLocal()
            try:
                await asyncio.to_thread(sweep, db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Media sweep failed")
