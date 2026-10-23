"""Audit receipts for actions taken on an event through the REST API.

The live console already records its actions (services/moderation.record -> AuditLog, with the
event in meta), and the commercial go-live gate records every escalation attempt
(crud.commercial.audit_golive_decision). What did NOT record anything was the management side:
creating and editing an event, moving it through its lifecycle, archiving it, changing its team,
inviting viewers and managing its access links. Those are exactly the changes the Event
Workspace's Activity & Audit view exists to show (ZST-ENG-DASH-001 EVT-07; engineering notes
"Every audited attempt and permitted state mutation includes organisation ID, event ID, actor,
action/resource, allow/deny decision, correlation/request ID, timestamp and authoritative
outcome").

Two rules this module keeps:

* A receipt follows the decision, it never stands in for it. `applied` is written only after
  the change has been committed by the caller; a refusal is written as `denied` / `rejected`
  BEFORE the HTTP error is raised, and changes nothing.
* Meta carries codes, counts and field NAMES — never values a viewer or member typed (no
  emails, no free text, no tokens). The reader (services/activity_log) whitelists what it shows
  on top of that, so a careless writer still cannot leak through the console.

Recording is best-effort with respect to the action it describes: the action has already been
committed when an `applied` receipt is written, so a failure to write the receipt is logged
loudly and swallowed rather than turned into a 500 that would tell the operator their committed
change did not happen.
"""
import logging

from ..crud.admin import create_audit_log

log = logging.getLogger(__name__)

# Why something was refused, as codes the reader turns into words (activity_log.REASONS).
REASON_NOT_EVENT_HOST = "not_event_host"
REASON_TRANSITION = "transition_not_allowed"
REASON_NOT_ON_AIR = "not_on_air"
REASON_INVALID_SCHEDULE = "invalid_schedule"


def record(db, *, actor, event, action: str, outcome: str = "applied", meta: dict | None = None) -> None:
    """One receipt for one action on `event`. See the module docstring for when to call it."""
    try:
        create_audit_log(
            db, actor=actor, action=action, target_type="event", target_id=event.id,
            org_id=event.org_id, event_id=event.id, outcome=outcome,
            meta={k: v for k, v in (meta or {}).items() if v is not None} or None,
        )
    except Exception:  # noqa: BLE001 - see module docstring: never fail the committed action
        db.rollback()
        log.exception("audit receipt not written: action=%s event=%s outcome=%s", action, event.id, outcome)


def deny(db, *, actor, event, action: str, reason: str, outcome: str = "denied", **meta) -> None:
    """Record a refused attempt. Call immediately before raising the refusal."""
    record(db, actor=actor, event=event, action=action, outcome=outcome, meta={"reason_code": reason, **meta})
