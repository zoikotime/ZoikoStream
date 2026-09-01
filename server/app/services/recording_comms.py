"""Recording health and finalization communications (ZST-EC-001 MED-007, MED-008).

Two families that are routinely conflated and must not be:

  * **MED-007** is about CAPTURE. Did the recorder start, is it still capturing on every
    path it is supposed to, did it stop.
  * **MED-008** is about the FILE. Once capture stopped, does an object actually exist, can
    it be read, does it contain the tracks and the duration it should.

A recording that stopped is not a recording that validated, and neither of them is a
published replay (that is MED-009). Every template in this family restates that boundary,
and `test_media_governance.py` asserts no MED-007 message ever claims replay availability.

**Where recording health comes from.** There is no health telemetry stream for recordings —
LiveKit reports egress start and egress end, and nothing in between. So "degraded" here is
not an invented signal-quality metric. It is the one degradation this platform can actually
prove from committed rows: *a capture that is supposed to be running on two independent
paths is running on fewer than two*. That situation is real, it is exactly what the
commercial service profile's dual-recording requirement exists to prevent, and it is
otherwise invisible to an operator until the event is over and one file is missing.

Everything else a recorder might do badly (dropped frames, silent audio, drift) is NOT
observable here and is therefore not reported. `evaluate_health` cannot return a degraded
verdict for any reason other than path loss, which is what keeps the family honest.

**Capture groups.** `_recording_start` creates one or two rows in the same call with an
identical `started_at`, so (event_id, started_at) identifies a capture exactly. Health is
governed on the group and stored on its lead row; the group's other row carries the raw
per-path state it always did.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..models import (
    FINALIZATION_FAILED,
    FINALIZATION_PARTIAL,
    FINALIZATION_READY,
    FINALIZATION_RECOVERED,
    REC_HEALTH_DEGRADED,
    REC_HEALTH_FAILED,
    REC_HEALTH_RECORDING,
    REC_HEALTH_RECOVERED,
    REC_HEALTH_STOPPED,
    VALIDATION_TO_FINALIZATION,
    Event,
    LiveRecording,
    MediaAssetEvent,
)
from . import media_comms, notifications, org_comms

log = logging.getLogger(__name__)

ACTIVE_STATUSES = ("recording", "paused")

# What validation.py genuinely does NOT check. Read from the evidence dict rather than
# hardcoded here where possible; this is the fallback wording for an evidence-free row.
NOT_CHECKED_DEFAULT = (
    "Frame-level gap and black-frame detection, caption quality, and cryptographic "
    "integrity verification are not performed by Zoiko Steam"
)

# Closed set. A provider exception string is never surfaced — cloud and encoder errors
# routinely embed bucket paths, object keys and request signatures.
FAILURE_CATEGORIES = {
    "object_missing": "The recorded object could not be found in storage",
    "unreadable": "The recorded object exists but could not be read",
    "no_capture": "The recorder never captured this path",
    "incomplete": "The recording did not contain the expected media tracks",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _claim(db: Session, row, column: str) -> bool:
    """Conditional claim of a notification marker. One notice per transition."""
    if getattr(row, column) is not None:
        return False
    setattr(row, column, _now())
    db.commit()
    return True


def _ledger(db: Session, recording: LiveRecording, family: str, transition: str,
            detail: str | None = None, actor_id=None) -> None:
    """Append to the asset-lifecycle ledger.

    Written before the email, and it survives the recording row's own deletion — which is
    the whole point: a retention audit asks what happened to an asset that no longer exists.
    """
    db.add(MediaAssetEvent(org_id=recording.org_id, event_id=recording.event_id,
                           recording_id=recording.id, family=family, transition=transition,
                           detail=detail, actor_id=actor_id))
    db.commit()


def reference(recording: LiveRecording) -> str:
    """Safe asset reference. Never the storage key, never a signed URL."""
    return str(recording.id)[:8]


# ── capture groups ──────────────────────────────────────────────────────────────────────

def capture_group(db: Session, recording: LiveRecording) -> list[LiveRecording]:
    """Every path of the capture this recording belongs to, primary first."""
    rows = db.scalars(
        select(LiveRecording).where(
            LiveRecording.event_id == recording.event_id,
            LiveRecording.started_at == recording.started_at)
    ).all()
    return sorted(rows, key=lambda r: (r.role != "primary", str(r.id)))


def lead(group: list[LiveRecording]) -> LiveRecording:
    return group[0]


def required_paths(db: Session, event_id) -> int:
    """How many INDEPENDENT recording paths this event is required to run.

    Authoritative: read from the event's commercial service profile, the same call
    `_recording_start` itself uses to decide how many egress jobs to launch. Never inferred
    from the event's importance or name.
    """
    from ..crud import commercial as commercial_crud

    event = db.get(Event, event_id)
    if event is None:
        return 1
    try:
        return 2 if commercial_crud.dual_recording_required(db, event) else 1
    except Exception:  # noqa: BLE001 — a profile lookup must not break the media path
        log.exception("dual_recording_required failed for event %s", event_id)
        return 1


def _capturing(group: list[LiveRecording]) -> int:
    """Paths that are genuinely capturing: LiveKit accepted the egress AND the row has not
    failed. `enforced` False means egress never started, so the row records intent only."""
    return sum(1 for r in group
               if r.enforced and r.status in ACTIVE_STATUSES + ("stopped",))


def independent_recording_label(db: Session, event_id, group: list[LiveRecording]) -> str:
    required = required_paths(db, event_id)
    if required < 2:
        return "Not required for this event — single recording path"
    return f"Required — {_capturing(group)} of {required} independent paths capturing"


# ── MED-007 health evaluation ───────────────────────────────────────────────────────────

def evaluate_health(db: Session, group: list[LiveRecording]) -> tuple[str, str | None]:
    """The governed health of one capture, and why. Reads committed rows only.

    Returns (state, reason). `reason` is None for a healthy capture.
    """
    head = lead(group)
    required = required_paths(db, head.event_id)
    capturing = _capturing(group)
    active = [r for r in group if r.status in ACTIVE_STATUSES]

    if not active:
        # Terminal. A capture where nothing was ever captured is a failure, not a stop.
        if capturing == 0:
            return REC_HEALTH_FAILED, "No path captured any media"
        return REC_HEALTH_STOPPED, None

    if capturing < required:
        lost = [r.role or "single" for r in group
                if not r.enforced or r.status == "failed"]
        component = (f"{', '.join(sorted(lost))} recording path"
                     if lost else "an independent recording path")
        return REC_HEALTH_DEGRADED, (
            f"{capturing} of {required} required independent paths are capturing "
            f"({component} is not)")
    return REC_HEALTH_RECORDING, None


def record_health(db: Session, background, recording: LiveRecording) -> str | None:
    """Fold one evaluation into the capture. Returns the transition, or None.

    Only a CHANGE is a communication event: re-evaluating a capture that is still degraded
    produces nothing, which is what stops a ticker from mailing an operator every pass.
    """
    group = capture_group(db, recording)
    if not group:
        return None
    head = lead(group)
    state, reason = evaluate_health(db, group)
    previous = head.health_state

    if previous == state:
        return None

    head.health_state = state
    head.health_reason = reason
    incident_start = head.health_changed_at
    head.health_changed_at = _now()
    # Re-arm the opposite markers so a later transition is announced again.
    if state == REC_HEALTH_RECORDING:
        head.degraded_notified_at = None
    elif state == REC_HEALTH_DEGRADED:
        head.recovered_notified_at = None
    db.commit()

    # A degraded capture that returns to full redundancy is a RECOVERED announcement, but
    # only if something was actually wrong first.
    if state == REC_HEALTH_RECORDING and previous == REC_HEALTH_DEGRADED:
        notify_recovered(db, background, head, incident_start)
        return REC_HEALTH_RECOVERED
    if state == REC_HEALTH_DEGRADED:
        notify_degraded(db, background, head, group)
        return REC_HEALTH_DEGRADED
    return None


# ── MED-007 notifications ───────────────────────────────────────────────────────────────

def _context(db: Session, recording: LiveRecording):
    org, event = media_comms._org_of(db, recording.event_id)
    addresses = media_comms.operators(db, org.id if org else None, recording.created_by)
    return org, event, addresses


def _owner_label(db: Session, recording: LiveRecording) -> str:
    from ..models import User

    owner = db.get(User, recording.created_by) if recording.created_by else None
    return owner.email if owner else "An authorized operator"


def notify_started(db: Session, background, recording: LiveRecording) -> bool:
    """MED-007 Started. Sent once per capture, from its lead row, after the row exists."""
    group = capture_group(db, recording)
    head = lead(group) if group else recording
    if head.status not in ACTIVE_STATUSES or head.started_at is None:
        return False
    if not _claim(db, head, "started_notified_at"):
        return False

    # Establish the health baseline at the same moment, so the first real transition is
    # detected against a known state rather than against None.
    state, reason = evaluate_health(db, group or [head])
    head.health_state, head.health_reason = state, reason
    head.health_changed_at = _now()
    db.commit()

    org, event, addresses = _context(db, head)
    if not addresses:
        return False

    required = required_paths(db, head.event_id)
    capturing = _capturing(group or [head])
    capture_status = ("Capturing" if capturing else
                      "NOT capturing — LiveKit egress was unavailable, so no file will be "
                      "produced until this is corrected")

    _ledger(db, head, "MED-007", "started")
    media_comms._queue(background, email_mod.send_recording_started_email, addresses,
                       event_title=event.title if event else "an event",
                       recording_reference=reference(head),
                       started_at=org_comms.org_timestamp(org, head.started_at),
                       owner=_owner_label(db, head),
                       org_name=org.name if org else "your Organization",
                       recording_mode=("Independent dual-path recording" if required > 1
                                       else "Single-path recording"),
                       quality=head.quality or "Not recorded",
                       redundancy=independent_recording_label(db, head.event_id,
                                                              group or [head]),
                       capture_status=capture_status,
                       test_mode=media_comms.is_test_org(org))

    # An event whose profile REQUIRES independent recording but is capturing on fewer paths
    # is its own escalation — the operator has a window to fix it while the event runs.
    if required > 1 and capturing < required:
        notify_redundancy_missing(db, background, head, group or [head], required, capturing)
    return True


def notify_redundancy_missing(db: Session, background, head: LiveRecording,
                              group: list[LiveRecording], required: int,
                              capturing: int) -> bool:
    if not _claim(db, head, "degraded_notified_at"):
        return False
    org, event, addresses = _context(db, head)
    if not addresses:
        return False
    _ledger(db, head, "MED-007", "redundancy_missing",
            f"{capturing} of {required} paths capturing")
    media_comms._queue(background, email_mod.send_recording_redundancy_email, addresses,
                       event_title=event.title if event else "an event",
                       recording_reference=reference(head),
                       detected_at=org_comms.org_timestamp(org, _now()),
                       required_paths=required, capturing_paths=capturing,
                       impact_class=(event.impact or "standard").replace("_", " ").title()
                       if event else "Standard",
                       org_name=org.name if org else "your Organization",
                       test_mode=media_comms.is_test_org(org))
    return True


def notify_degraded(db: Session, background, head: LiveRecording,
                    group: list[LiveRecording]) -> bool:
    if not _claim(db, head, "degraded_notified_at"):
        return False
    org, event, addresses = _context(db, head)
    if not addresses:
        return False
    _ledger(db, head, "MED-007", "degraded", head.health_reason)
    lost = [(r.role or "single").title() for r in group
            if not r.enforced or r.status == "failed"]
    media_comms._queue(background, email_mod.send_recording_degraded_email, addresses,
                       event_title=event.title if event else "an event",
                       recording_reference=reference(head),
                       degraded_at=org_comms.org_timestamp(org, head.health_changed_at),
                       affected_component=(", ".join(lost) + " recording path" if lost
                                           else "An independent recording path"),
                       capture_state=head.health_reason or "Degraded",
                       impact_class=(event.impact or "standard").replace("_", " ").title()
                       if event else "Standard",
                       operator_action=("Restore the affected recording path. The remaining "
                                        "path is still capturing."),
                       org_name=org.name if org else "your Organization",
                       test_mode=media_comms.is_test_org(org))
    return True


def notify_recovered(db: Session, background, head: LiveRecording,
                     incident_start: datetime | None) -> bool:
    if not _claim(db, head, "recovered_notified_at"):
        return False
    org, event, addresses = _context(db, head)
    if not addresses:
        return False
    _ledger(db, head, "MED-007", "recovered")
    media_comms._queue(background, email_mod.send_recording_recovered_email, addresses,
                       event_title=event.title if event else "an event",
                       recording_reference=reference(head),
                       recovered_at=org_comms.org_timestamp(org, head.health_changed_at),
                       incident_duration=media_comms._duration(incident_start,
                                                               head.health_changed_at),
                       capture_state="All required independent paths are capturing",
                       remaining_risk=("Media captured during the degraded window exists on "
                                       "fewer paths than required. Validation will report "
                                       "what each path actually contains."),
                       org_name=org.name if org else "your Organization",
                       test_mode=media_comms.is_test_org(org))
    return True


def notify_stopped(db: Session, background, recording: LiveRecording) -> bool:
    """MED-007 Stopped. Says explicitly that validation has not run — never "replay ready"."""
    group = capture_group(db, recording)
    head = lead(group) if group else recording
    if head.stopped_at is None:
        return False
    if any(r.status in ACTIVE_STATUSES for r in group):
        return False   # another path of the same capture is still running
    if not _claim(db, head, "stopped_notified_at"):
        return False

    state, reason = evaluate_health(db, group or [head])
    head.health_state, head.health_reason = state, reason
    head.health_changed_at = _now()
    db.commit()

    org, event, addresses = _context(db, head)
    if not addresses:
        return False
    captured = _capturing(group or [head])
    final_state = ("Captured on every required path" if state == REC_HEALTH_STOPPED
                   else reason or "Capture did not complete on every path")
    _ledger(db, head, "MED-007", "stopped", reason)
    media_comms._queue(background, email_mod.send_recording_stopped_email, addresses,
                       event_title=event.title if event else "an event",
                       recording_reference=reference(head),
                       stopped_at=org_comms.org_timestamp(org, head.stopped_at),
                       duration=media_comms._duration(head.started_at, head.stopped_at),
                       final_capture_state=final_state,
                       next_step=("Validation has not run yet. The recording is checked "
                                  "before it can be used, and the result is sent separately."
                                  if captured else
                                  "No media was captured, so there is nothing to validate."),
                       org_name=org.name if org else "your Organization",
                       test_mode=media_comms.is_test_org(org))
    return True


# ── MED-008 finalization ────────────────────────────────────────────────────────────────

def _validated_components(evidence: dict | None) -> str:
    """Render only the checks that genuinely ran, from validation.py's own evidence dict."""
    if not evidence:
        return "No validation evidence was recorded"
    ran: list[str] = []
    for label in ("primary", "secondary", "single"):
        probe = evidence.get(label)
        if not isinstance(probe, dict):
            continue
        if probe.get("error"):
            ran.append(f"{label}: file could not be read")
            continue
        parts = ["object exists", "file readable"]
        if probe.get("duration_seconds") is not None:
            parts.append("duration measured")
        if probe.get("has_video"):
            parts.append("video track present")
        if probe.get("has_audio"):
            parts.append("audio track present")
        ran.append(f"{label}: {', '.join(parts)}")
    if evidence.get("duration_delta_seconds") is not None:
        ran.append(f"duration comparison between paths "
                   f"({evidence['duration_delta_seconds']}s difference)")
    return "; ".join(ran) if ran else "No checks completed"


def _not_checked(evidence: dict | None) -> str:
    """The honest negative. validation.py records these as explicit 'not implemented'."""
    gaps = []
    if (evidence or {}).get("gap_detection") == "not implemented":
        gaps.append("frame-level gap and black-frame detection")
    if (evidence or {}).get("caption_qa") == "not implemented":
        gaps.append("caption quality")
    gaps.append("cryptographic integrity verification")
    return f"{', '.join(gaps).capitalize()} — these are not performed by Zoiko Steam"


def _missing_components(evidence: dict | None) -> str:
    missing = []
    for label in ("primary", "secondary", "single"):
        probe = (evidence or {}).get(label)
        if not isinstance(probe, dict):
            continue
        if probe.get("error"):
            missing.append(f"{label}: {probe['error']}")
            continue
        if not probe.get("has_video"):
            missing.append(f"{label}: no video track")
        if not probe.get("has_audio"):
            missing.append(f"{label}: no audio track")
        if probe.get("duration_seconds") is None:
            missing.append(f"{label}: duration could not be measured")
    delta = (evidence or {}).get("duration_delta_seconds")
    if delta is None and "duration_delta_seconds" in (evidence or {}):
        missing.append("the two paths could not be compared")
    return "; ".join(missing) if missing else "Not determined"


def _failure_category(evidence: dict | None) -> str:
    for label in ("primary", "secondary", "single"):
        probe = (evidence or {}).get(label)
        if isinstance(probe, dict) and probe.get("error"):
            error = probe["error"]
            if "download" in error.lower() or "not" in error.lower():
                return FAILURE_CATEGORIES["object_missing"]
            return FAILURE_CATEGORIES["unreadable"]
    return FAILURE_CATEGORIES["incomplete"]


def replay_status_of(db: Session, event_id) -> str:
    """What MED-008 may say about replay availability — read from the entitlement, never
    assumed. A validated recording whose replay was never published must say so."""
    from ..crud import commercial as commercial_crud

    try:
        entitlement = commercial_crud.get_replay_entitlement(db, event_id, scope="audience")
    except Exception:  # noqa: BLE001
        log.exception("replay entitlement lookup failed for event %s", event_id)
        entitlement = None
    if entitlement is None:
        return "No replay has been prepared for this event"
    state = entitlement.publish_state
    if state == "published":
        return "Published — this replay is available to its audience"
    if state == "ready_for_review":
        return ("Not published. The replay is prepared for review; publishing is a separate "
                "operator decision")
    if state == "withheld":
        return "Withdrawn — this replay is not available"
    if state == "expired":
        return "Expired — this replay is no longer available"
    return "Not available"


def notify_finalized(db: Session, background, recording: LiveRecording) -> str | None:
    """MED-008. Called after services/validation.py commits a validation verdict.

    Never called from a recording STOP: a stopped recording has not been checked, and this
    family exists precisely to report the check.
    """
    status = recording.validation_status
    variant = VALIDATION_TO_FINALIZATION.get(status or "")
    if variant is None:
        return None   # "captured"/"validating"/None — no verdict to report yet

    previous = recording.finalization_notified_state
    # A previously bad asset that is now valid is a RECOVERED announcement, not a second
    # READY. This is the only transition allowed to re-announce an already-announced row.
    if variant == FINALIZATION_READY and previous in (FINALIZATION_FAILED,
                                                      FINALIZATION_PARTIAL):
        variant = FINALIZATION_RECOVERED
    elif previous == variant:
        return None

    # The "recording ready" preference names exactly one thing: being told a recording is
    # ready. It governs that message only. A partial, failed or recovered asset is an
    # action-required operational alert and is not what that switch turns off.
    org, event, addresses = _context(db, recording)
    if variant == FINALIZATION_READY and not notifications.should_send_operational_notification(
            family="MED-008", org=org):
        recording.finalization_notified_state = variant
        recording.finalization_notified_at = _now()
        db.commit()
        return None

    recording.finalization_notified_state = variant
    recording.finalization_notified_at = _now()
    db.commit()
    if not addresses:
        return variant

    evidence = recording.validation_evidence or {}
    finalized_at = evidence.get("checked_at")
    try:
        finalized = (datetime.fromisoformat(finalized_at) if finalized_at
                     else recording.stopped_at)
    except ValueError:
        finalized = recording.stopped_at

    asset_status = {
        FINALIZATION_READY: "Validated — every check that runs passed",
        FINALIZATION_PARTIAL: "Validated with missing or mismatched media",
        FINALIZATION_FAILED: "Not usable — validation could not confirm a readable asset",
        FINALIZATION_RECOVERED: "Validated after an earlier failure",
    }[variant]

    _ledger(db, recording, "MED-008", variant)
    media_comms._queue(
        background, email_mod.send_recording_finalized_email, addresses,
        variant=variant,
        event_title=event.title if event else "an event",
        recording_reference=reference(recording),
        finalized_at=org_comms.org_timestamp(org, finalized),
        validated_components=_validated_components(evidence),
        not_checked=_not_checked(evidence),
        duration=media_comms._duration(recording.started_at, recording.stopped_at),
        asset_status=asset_status,
        replay_status=replay_status_of(db, recording.event_id),
        missing_components=(_missing_components(evidence)
                            if variant in (FINALIZATION_PARTIAL, FINALIZATION_FAILED)
                            else None),
        failure_category=(_failure_category(evidence)
                          if variant == FINALIZATION_FAILED else None),
        remediation=("Re-run validation once the storage object is available, or export the "
                     "path that did validate."
                     if variant in (FINALIZATION_PARTIAL, FINALIZATION_FAILED) else None),
        recovered_components=(_validated_components(evidence)
                              if variant == FINALIZATION_RECOVERED else None),
        org_name=org.name if org else "your Organization",
        test_mode=media_comms.is_test_org(org))
    return variant
