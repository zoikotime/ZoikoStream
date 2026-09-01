"""Authorized support access lifecycle (ZST-EC-001 ORG-009).

The security property this module exists to create: a scoped support session against a
customer tenant cannot become ACTIVE unless an authorized approver in THAT organization
approved the exact terms, and the terms have not moved since.

    REQUESTED --approve--> APPROVED --start--> ACTIVE --end/expire--> ENDED / EXPIRED
              \\--deny---> DENIED

`approval_fingerprint` is what makes the approval specific. It hashes the case reference,
engineer, scope, sorted allowed actions and duration. `start()` recomputes it and refuses to
activate on a mismatch, so widening scope or extending duration after approval invalidates
the approval instead of silently inheriting it.

Enforcement lives in services/tenant_access.py. Every admin route that reads or changes
tenant data calls `SupportContext.authorize(...)`, which requires a live session from THIS
module, held by THIS engineer, carrying the capability the route names - and writes the
audit row itself, so attribution cannot depend on a handler remembering to log.

`super_admin` alone therefore no longer reaches tenant data. What it still reaches is
platform-global operations that expose no customer data, and adverse platform enforcement
(suspend / restrict / delete an Organization), which is deliberately NOT customer-approved:
requiring the customer's consent to restrict the customer would make that control useless.
Those paths are audited and announced through ORG-010 instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    SUPPORT_ACTIVE,
    SUPPORT_APPROVED,
    SUPPORT_DENIED,
    SUPPORT_ENDED,
    SUPPORT_EXPIRED,
    SUPPORT_EXPIRING_WARNING_MINUTES,
    SUPPORT_MAX_MINUTES,
    SUPPORT_REQUESTED,
    AuditLog,
    ElevationSession,
    Organization,
    SupportAccessRequest,
    User,
)
from . import org_comms

log = logging.getLogger(__name__)

TICKER_INTERVAL_SECONDS = 60.0

# Outcomes reported by start()/approve() so routers can map them to status codes without
# re-deriving policy.
OK = "ok"
NOT_APPROVED = "not_approved"
TERMS_CHANGED = "terms_changed"
ALREADY_ACTIVE = "already_active"
NO_SECOND_AUTHORIZER = "no_second_authorizer"
SELF_AUTHORIZED = "self_authorized"
EXPIRED = "expired"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── terms fingerprint ───────────────────────────────────────────────────────────────────

def actions_list(raw: str | None) -> list[str]:
    """allowed_actions is stored newline-joined so it stays readable in the audit trail."""
    return [a.strip() for a in (raw or "").splitlines() if a.strip()]


def fingerprint(req: SupportAccessRequest) -> str:
    """Hash of the exact terms an approver signs off on.

    Sorted actions so a reordering is not treated as a change, but any addition, removal,
    scope edit, duration edit or engineer swap produces a different digest.
    """
    parts = [
        str(req.org_id),
        (req.case_reference or "").strip().lower(),
        str(req.engineer_id or ""),
        (req.requested_scope or "").strip().lower(),
        "|".join(sorted(a.lower() for a in actions_list(req.allowed_actions))),
        str(int(req.requested_minutes or 0)),
        "emergency" if req.emergency else "normal",
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# ── recipients ──────────────────────────────────────────────────────────────────────────

def approver_recipients(db: Session, org_id) -> list[str]:
    """The organization's approvers: the recorded owner plus its administrators.

    Deduplicated by address. Deliberately never includes platform staff - this family exists
    so the CUSTOMER finds out, and adding staff to the recipient list would let a staff
    inbox stand in for a customer approval in someone's mental model.
    """
    org = db.get(Organization, org_id)
    owner = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    admins = org_comms.org_admins(db, org_id)
    return org_comms.recipients(owner, *admins)


# ── lifecycle ───────────────────────────────────────────────────────────────────────────

def create_request(db: Session, *, org_id, case_reference: str, reason_category: str,
                   engineer: User, engineer_display: str, requested_scope: str,
                   allowed_actions: list[str], minutes: int,
                   emergency: bool = False, emergency_reason: str | None = None,
                   emergency_authorizer: User | None = None) -> SupportAccessRequest:
    """Record a support-access request. Grants nothing on its own.

    Duration is clamped to SUPPORT_MAX_MINUTES in the domain rather than trusted from the
    caller, because "no open-ended support access" has to hold even if a request handler is
    wrong.
    """
    minutes = max(1, min(int(minutes), SUPPORT_MAX_MINUTES))
    req = SupportAccessRequest(
        org_id=org_id,
        case_reference=case_reference.strip(),
        reason_category=reason_category,
        engineer_id=engineer.id if engineer else None,
        engineer_display=engineer_display.strip(),
        requested_scope=requested_scope.strip(),
        allowed_actions="\n".join(a.strip() for a in allowed_actions if a.strip()),
        requested_minutes=minutes,
        status=SUPPORT_REQUESTED,
        emergency=bool(emergency),
        emergency_reason=(emergency_reason or "").strip() or None,
        emergency_authorizer_id=emergency_authorizer.id if emergency_authorizer else None,
        emergency_authorizer_email=emergency_authorizer.email if emergency_authorizer else None,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


def approve(db: Session, req: SupportAccessRequest, approver: User) -> str:
    """Customer approval of the exact current terms."""
    if req.status != SUPPORT_REQUESTED:
        return req.status
    req.status = SUPPORT_APPROVED
    req.approved_by_id = approver.id
    req.approved_by_email = approver.email
    req.approved_at = _now()
    # Bind the approval to the terms as they stand right now.
    req.approval_fingerprint = fingerprint(req)
    db.commit()
    db.refresh(req)
    return OK


def countersign_emergency(db: Session, req: SupportAccessRequest,
                          authorizer: User) -> str:
    """Record the second, independent privileged authorization for break-glass access.

    Separate from approve(): this is a PLATFORM control, not a customer decision. It exists
    so emergency access still requires two people, and it explicitly refuses to let the
    requesting engineer countersign their own request.
    """
    if not req.emergency:
        return "not_emergency"
    if req.status not in (SUPPORT_REQUESTED,):
        return req.status
    if authorizer.id == req.engineer_id:
        return SELF_AUTHORIZED
    req.emergency_authorizer_id = authorizer.id
    req.emergency_authorizer_email = authorizer.email
    db.commit()
    db.refresh(req)
    return OK


def deny(db: Session, req: SupportAccessRequest, approver: User) -> str:
    if req.status != SUPPORT_REQUESTED:
        return req.status
    req.status = SUPPORT_DENIED
    req.approved_by_id = approver.id
    req.approved_by_email = approver.email
    req.denied_at = _now()
    db.commit()
    db.refresh(req)
    return OK


def amend_terms(db: Session, req: SupportAccessRequest, **changes) -> SupportAccessRequest:
    """Change scope/duration/actions after the fact.

    Any amendment drops the request back to REQUESTED and clears the approval. This is the
    enforcement of "changing scope or duration after approval invalidates the approval" -
    the approval is not merely stale, it is gone, and a new one must be obtained.
    """
    for field in ("requested_scope", "requested_minutes", "case_reference"):
        if field in changes and changes[field] is not None:
            setattr(req, field, changes[field])
    if changes.get("allowed_actions") is not None:
        req.allowed_actions = "\n".join(a.strip() for a in changes["allowed_actions"] if a.strip())
    if req.requested_minutes:
        req.requested_minutes = max(1, min(int(req.requested_minutes), SUPPORT_MAX_MINUTES))
    if req.status in (SUPPORT_APPROVED, SUPPORT_REQUESTED):
        req.status = SUPPORT_REQUESTED
        req.approval_fingerprint = None
        req.approved_at = None
        req.approved_by_id = None
        req.approved_by_email = None
    db.commit()
    db.refresh(req)
    return req


def start(db: Session, req: SupportAccessRequest) -> tuple[str, ElevationSession | None]:
    """Begin an approved session. This is the gate.

    Emergency requests may start without a prior approval - that is what makes them
    emergencies - but they are announced immediately and carry a post-use review obligation.
    Everything else must be APPROVED, with the terms still matching what was approved.
    """
    if req.status == SUPPORT_ACTIVE:
        return ALREADY_ACTIVE, None
    if req.emergency and req.status == SUPPORT_REQUESTED:
        # Break-glass: starts without the CUSTOMER's approval, because operational safety
        # can require access before an approver is reachable. It is not, however,
        # self-service. A second independent privileged authorizer must already be recorded,
        # and they may not be the requesting engineer. Without that this was a one-field
        # bypass of the entire approval gate.
        if req.emergency_authorizer_id is None:
            return NO_SECOND_AUTHORIZER, None
        if req.emergency_authorizer_id == req.engineer_id:
            return SELF_AUTHORIZED, None
    elif req.status != SUPPORT_APPROVED:
        return NOT_APPROVED, None
    elif req.approval_fingerprint != fingerprint(req):
        # The terms moved after approval. Refuse, and drop the approval so the only way
        # forward is a fresh customer decision.
        req.status = SUPPORT_REQUESTED
        req.approval_fingerprint = None
        req.approved_at = None
        db.commit()
        return TERMS_CHANGED, None

    now = _now()
    req.status = SUPPORT_ACTIVE
    req.starts_at = now
    req.expires_at = now + timedelta(minutes=req.requested_minutes)
    if req.emergency:
        req.approval_fingerprint = req.approval_fingerprint or fingerprint(req)

    # The elevation and its authorization become one linked record.
    elevation = ElevationSession(
        user_id=req.engineer_id,
        scope=f"Support case {req.case_reference}",
        scopes=actions_list(req.allowed_actions),
        reason=f"{req.reason_category} / org {req.org_id}",
        expires_at=req.expires_at,
    )
    db.add(elevation)
    db.commit()
    db.refresh(elevation)
    req.elevation_session_id = elevation.id
    db.commit()
    db.refresh(req)
    return OK, elevation


def end(db: Session, req: SupportAccessRequest, *, expired: bool = False) -> bool:
    """End an active session and close its elevation. Idempotent."""
    if req.status not in (SUPPORT_ACTIVE,):
        return False
    now = _now()
    req.status = SUPPORT_EXPIRED if expired else SUPPORT_ENDED
    req.ended_at = now
    if req.emergency:
        # The review obligation opens when the access closes, not when it started.
        req.post_use_review_at = req.post_use_review_at or now
    if req.elevation_session_id:
        elevation = db.get(ElevationSession, req.elevation_session_id)
        if elevation is not None and elevation.ended_at is None:
            elevation.ended_at = now
    db.commit()
    db.refresh(req)
    return True


def active_for(db: Session, org_id) -> SupportAccessRequest | None:
    """The live approved session for one tenant, if any. Expiry is evaluated, not assumed."""
    now = _now()
    return db.scalar(
        select(SupportAccessRequest).where(
            SupportAccessRequest.org_id == org_id,
            SupportAccessRequest.status == SUPPORT_ACTIVE,
            SupportAccessRequest.expires_at > now,
        ).order_by(SupportAccessRequest.starts_at.desc())
    )


def session_is_live(db: Session, req: SupportAccessRequest) -> bool:
    """True only while the session is ACTIVE and inside its window."""
    return bool(req.status == SUPPORT_ACTIVE and req.expires_at and req.expires_at > _now())


# ── privileged-action attribution ───────────────────────────────────────────────────────

def record_action(db: Session, req: SupportAccessRequest, *, actor: User | None, action: str,
                  target_type: str | None = None, target_id=None, meta: dict | None = None,
                  ip: str | None = None) -> None:
    """Attribute one privileged action to this support session.

    Reuses the existing AuditLog rather than opening a parallel trail, and stamps the
    support session and engineer into meta so every row answers "under whose authorization".
    """
    payload = dict(meta or {})
    payload.update({
        "support_request_id": str(req.id),
        "support_case": req.case_reference,
        "engineer": req.engineer_display,
        "elevation_session_id": str(req.elevation_session_id or ""),
    })
    db.add(AuditLog(
        actor_id=actor.id if actor else req.engineer_id,
        actor_email=actor.email if actor else None,
        action=action, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        org_id=req.org_id, meta=payload, ip=ip,
    ))
    db.commit()


def action_count(db: Session, req: SupportAccessRequest) -> int:
    rows = db.scalars(
        select(AuditLog).where(AuditLog.org_id == req.org_id)
    ).all()
    return sum(1 for r in rows if (r.meta or {}).get("support_request_id") == str(req.id))


# ── notifications ───────────────────────────────────────────────────────────────────────

def _claim(db: Session, req: SupportAccessRequest, column: str) -> bool:
    col = getattr(SupportAccessRequest, column)
    updated = db.execute(
        update(SupportAccessRequest)
        .where(SupportAccessRequest.id == req.id, col.is_(None))
        .values(**{column: _now()})
    ).rowcount
    db.commit()
    return bool(updated)


def _facts(db: Session, req: SupportAccessRequest) -> dict:
    org = db.get(Organization, req.org_id)
    actions = actions_list(req.allowed_actions)
    return {
        "org": org,
        "org_name": org.name if org else "your Organization",
        "case_reference": req.case_reference,
        "engineer": req.engineer_display,
        "scope": req.requested_scope,
        "allowed_actions": ", ".join(actions) if actions else "Read-only inspection",
        "duration": f"{req.requested_minutes} minutes",
    }


def _queue(background, send, addresses, **kwargs) -> None:
    try:
        for address in addresses:
            background.add_task(send, address, **kwargs)
    except UnsafeLinkError:
        log.exception("ORG-009 notice not queued: APP_URL unsafe for this environment")


def notify_requested(db: Session, background, req: SupportAccessRequest) -> None:
    if not _claim(db, req, "requested_notified_at"):
        return
    f = _facts(db, req)
    _queue(background, email_mod.send_support_access_requested_email,
           approver_recipients(db, req.org_id),
           org_name=f["org_name"], case_reference=f["case_reference"], engineer=f["engineer"],
           scope=f["scope"], allowed_actions=f["allowed_actions"], duration=f["duration"],
           reason_category=_reason_label(req.reason_category))


def notify_started(db: Session, background, req: SupportAccessRequest) -> None:
    if not _claim(db, req, "started_notified_at"):
        return
    f = _facts(db, req)
    _queue(background, email_mod.send_support_access_started_email,
           approver_recipients(db, req.org_id),
           org_name=f["org_name"], case_reference=f["case_reference"], engineer=f["engineer"],
           scope=f["scope"], allowed_actions=f["allowed_actions"],
           started_display=org_comms.org_timestamp(f["org"], req.starts_at),
           expires_display=org_comms.org_timestamp(f["org"], req.expires_at),
           approved_by=req.approved_by_email or "Emergency authorization")


def notify_expiring(db: Session, background, req: SupportAccessRequest) -> None:
    if not _claim(db, req, "expiring_notified_at"):
        return
    f = _facts(db, req)
    left = max(0, int((req.expires_at - _now()).total_seconds() // 60)) if req.expires_at else 0
    _queue(background, email_mod.send_support_access_expiring_email,
           approver_recipients(db, req.org_id),
           org_name=f["org_name"], case_reference=f["case_reference"], engineer=f["engineer"],
           expires_display=org_comms.org_timestamp(f["org"], req.expires_at),
           minutes_left=left)


def notify_ended(db: Session, background, req: SupportAccessRequest) -> None:
    if not _claim(db, req, "ended_notified_at"):
        return
    f = _facts(db, req)
    _queue(background, email_mod.send_support_access_ended_email,
           approver_recipients(db, req.org_id),
           org_name=f["org_name"], case_reference=f["case_reference"], engineer=f["engineer"],
           ended_display=org_comms.org_timestamp(f["org"], req.ended_at),
           outcome="Expired automatically" if req.status == SUPPORT_EXPIRED
           else "Ended by Zoiko Steam Support",
           action_count=action_count(db, req))


def notify_emergency(db: Session, background, req: SupportAccessRequest) -> None:
    """Announced as soon as emergency access starts, not after it finishes."""
    if not _claim(db, req, "emergency_notified_at"):
        return
    f = _facts(db, req)
    # Truthful dual-authorization status. There is no dual-authorization workflow in this
    # codebase, so this reports whether a second privileged authorizer was named on the
    # record - and says plainly when none was.
    # Always a real second person now: start() refuses break-glass without an independent
    # authorizer, so this line can no longer read "none recorded".
    dual = (f"Independently authorized by {req.emergency_authorizer_email}"
            if req.emergency_authorizer_email
            else "Authorization record unavailable")
    _queue(background, email_mod.send_support_access_emergency_email,
           approver_recipients(db, req.org_id),
           org_name=f["org_name"], case_reference=f["case_reference"], engineer=f["engineer"],
           started_display=org_comms.org_timestamp(f["org"], req.starts_at),
           expires_display=org_comms.org_timestamp(f["org"], req.expires_at),
           emergency_reason=req.emergency_reason or "Not recorded",
           dual_authorization=dual,
           review_note="This access is subject to review by Zoiko Steam and by your "
                       "Organization.")


_REASON_LABELS = {
    "customer_reported_issue": "Customer-reported issue",
    "billing_investigation": "Billing investigation",
    "platform_incident": "Platform incident",
    "security_review": "Security review",
}


def _reason_label(code: str | None) -> str:
    return _REASON_LABELS.get(code or "", "Support request")


# ── expiry / expiring ticker ────────────────────────────────────────────────────────────

class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a ticker must not die on one bad send
            log.exception("Support-access notification failed")


def sweep(db: Session, background=None) -> dict:
    """One pass: warn sessions nearing expiry, close sessions past it."""
    background = background or _Bg()
    now = _now()
    warned = closed = 0

    soon = now + timedelta(minutes=SUPPORT_EXPIRING_WARNING_MINUTES)
    for req in db.scalars(
        select(SupportAccessRequest).where(
            SupportAccessRequest.status == SUPPORT_ACTIVE,
            SupportAccessRequest.expires_at > now,
            SupportAccessRequest.expires_at <= soon,
            SupportAccessRequest.expiring_notified_at.is_(None),
        )
    ).all():
        notify_expiring(db, background, req)
        warned += 1

    for req in db.scalars(
        select(SupportAccessRequest).where(
            SupportAccessRequest.status == SUPPORT_ACTIVE,
            SupportAccessRequest.expires_at <= now,
        )
    ).all():
        if end(db, req, expired=True):
            notify_ended(db, background, req)
            closed += 1

    return {"warned": warned, "closed": closed}


async def run_support_access_sweeper(interval: float = TICKER_INTERVAL_SECONDS) -> None:
    """Background ticker started from the app lifespan.

    ponytail: single-ticker assumption, like the others in main.py. The per-transition
    notification claims mean a second worker running this loop cannot duplicate mail.
    """
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
            log.exception("Support-access sweep failed")
