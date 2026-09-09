"""Security, abuse and restriction communications (ZST-EC-001 SEC-001 -> SEC-006).

**What already existed and is reused, not rebuilt.** The break-glass foundation was already
correct before this family: `services/support_access.countersign_emergency` requires a
second, independent privileged authorization and explicitly refuses
`authorizer.id == req.engineer_id`, and `services/ops.current_elevation` enforces expiry by
selecting on `expires_at > now()` with `ended_at IS NULL`. SEC-002 therefore adds only the
customer-facing disclosure and the post-access review deadline; there is no parallel
emergency-access subsystem here, and `authorize_breakglass()` below delegates to the
existing enforcement rather than re-deciding it.

**A 403 is not a violation.** `confirm_violation()` is the only writer of
`AccessPolicyViolation`, it requires a `VIOLATION_SOURCES` member, and no authorization
helper calls it. An expired role, a stale session or a wrong page produces a denial and
nothing else - which is the distinction SEC-006 exists to draw.

**Detected is not confirmed.** `alert()` refuses any `SecurityEvent` below `confirmed`. A
detector that has merely fired sits at `detected` and communicates nothing.

**Contained is not resolved, and neither is inferred.** `containment_note()` reads
`contained_at` only. A password change, a revoked token or a forced sign-out does not set it,
so the containment sentence cannot appear without an operator committing to the fact.

**Verified contacts only.** `security_recipients()` returns VERIFIED
`OrganizationSecurityContact` rows. Pending, expired and revoked contacts are excluded, and
billing contacts, commercial contacts and "all org admins" are never substituted.

**Reporter identity is never disclosed.** `reported_party_view()` is the only projection of
an abuse report for the reported side, and it omits every reporter field. No restriction
message carries a complainant.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    ABUSE_CATEGORIES,
    ABUSE_CLOSURE_LABELS,
    ABUSE_CLOSURE_NOTES,
    ABUSE_SUBJECT_TYPES,
    CONTACT_TOKEN_TTL_HOURS,
    DISCLOSURE_STATUS_LABELS,
    EVENT_ALERTABLE,
    PURPOSE_SECURITY_CONTACT,
    RESTRICTED_CONTENT_TYPES,
    RESTRICTION_TYPE_LABELS,
    RESTRICTION_TYPES,
    SEC_EVENT_CATEGORIES,
    SEC_EVENT_CATEGORY_LABELS,
    VIOLATION_CATEGORIES,
    VIOLATION_CATEGORY_LABELS,
    VIOLATION_SOURCES,
    AbuseReport,
    AccessPolicyViolation,
    ContentRestriction,
    Incident,
    Organization,
    OrganizationSecurityContact,
    RestrictionAppeal,
    SecurityEvent,
    SecurityIncidentDisclosure,
    SecurityNotice,
    SupportAccessRequest,
    User,
)

log = logging.getLogger(__name__)

# Same token convention as crud/identity.py: 256 bits from the OS CSPRNG, sha256 at rest.
_TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _may_send(family: str, org) -> bool:
    from . import notifications

    return notifications.should_send_operational_notification(family=family, org=org)


def security_url() -> str:
    """The customer-facing Security Center. Never an admin path."""
    return f"{email_mod.public_base_url()}/organization/security"


def _claim(db: Session, *, kind: str, subject_type: str, subject_id, version: int = 0,
           org_id=None, detail: str | None = None) -> bool:
    """Durable, single-shot claim keyed on (kind, subject, VERSION).

    The version is what lets an incident issue many updates and a restriction be reapplied,
    while still making each individual transition single-shot.
    """
    existing = db.scalar(
        select(SecurityNotice).where(SecurityNotice.kind == kind,
                                     SecurityNotice.subject_type == subject_type,
                                     SecurityNotice.subject_id == subject_id,
                                     SecurityNotice.version == version))
    if existing is not None:
        return False
    try:
        db.add(SecurityNotice(kind=kind, subject_type=subject_type, subject_id=subject_id,
                              version=version, org_id=org_id, detail=detail))
        db.commit()
        return True
    except Exception:  # noqa: BLE001 - a lost uniqueness race IS a successful dedup
        db.rollback()
        log.info("Security notice %s/%s v%s already claimed", kind, subject_id, version)
        return False


def _queue(background, send, people, **kwargs) -> None:
    """Queue one message per recipient.

    Wrapped so a provider failure can never propagate into the caller's transaction: a
    security event, a break-glass session, an incident, a restriction and an appeal must all
    survive Resend being unavailable.
    """
    try:
        for address, name in people:
            background.add_task(send, address, name=name, **kwargs)
    except UnsafeLinkError:
        log.exception("Security notice not queued: APP_URL unsafe for this environment")


class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a notice must never break committed security state
            log.exception("Security notice failed")


def _reference(prefix: str, db: Session, model, column) -> str:
    """A customer-safe sequential reference like SEC-2026-000123."""
    year = _now().year
    stem = f"{prefix}-{year}-"
    used = db.scalar(select(func.count(model.id)).where(column.like(f"{stem}%"))) or 0
    return f"{stem}{used + 1:06d}"


# ══ SEC-006 — security contacts ═════════════════════════════════════════════════════════

def nominate_contact(db: Session, org: Organization, *, email: str,
                     display_name: str | None = None, created_by=None,
                     user_id=None) -> tuple[OrganizationSecurityContact | None, str | None]:
    """Nominate a security contact and mint its verification token.

    Returns (contact, raw_token). Re-nominating an existing address SUPERSEDES its previous
    token, so an older verification link stops working.
    """
    address = (email or "").strip().lower()
    if not address:
        return None, None
    contact = db.scalar(
        select(OrganizationSecurityContact).where(
            OrganizationSecurityContact.org_id == org.id,
            OrganizationSecurityContact.email == address))
    if contact is None:
        contact = OrganizationSecurityContact(org_id=org.id, email=address)
        db.add(contact)

    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    # Overwriting `verification_token_hash` is what actually retires the previous token: its
    # hash is no longer stored, so it cannot be looked up. `verification_superseded_at` is
    # cleared here because it describes THIS issuance, and leaving it set would have made
    # the freshly minted token unredeemable - which is exactly the bug the supersede test
    # caught. It stays populated only for a revoked contact, where no new token follows.
    contact.verification_superseded_at = None
    contact.display_name = display_name or contact.display_name
    contact.user_id = user_id if user_id is not None else contact.user_id
    contact.status = "pending"
    contact.verification_purpose = PURPOSE_SECURITY_CONTACT
    contact.verification_token_hash = _hash(raw)
    contact.verification_expires_at = _now() + timedelta(hours=CONTACT_TOKEN_TTL_HOURS)
    contact.verification_consumed_at = None
    contact.verified_at = None
    contact.revoked_at = None
    contact.created_by = created_by or contact.created_by
    contact.verification_version = (contact.verification_version or 0) + 1
    db.commit()
    db.refresh(contact)
    return contact, raw


def verify_contact(db: Session, *, token: str,
                   purpose: str = PURPOSE_SECURITY_CONTACT
                   ) -> tuple[OrganizationSecurityContact | None, str]:
    """Redeem a verification token. Single-use, purpose-bound and expiring."""
    if not token:
        return None, "invalid"
    contact = db.scalar(
        select(OrganizationSecurityContact).where(
            OrganizationSecurityContact.verification_token_hash == _hash(token)))
    if contact is None or contact.verification_purpose != purpose:
        return None, "invalid"
    if contact.verification_consumed_at is not None:
        return None, "already_used"
    if contact.verification_superseded_at is not None:
        return None, "superseded"
    if (contact.verification_expires_at is None
            or contact.verification_expires_at < _now()):
        contact.status = "expired"
        db.commit()
        return None, "expired"
    contact.verification_consumed_at = _now()
    contact.verified_at = _now()
    contact.status = "verified"
    db.commit()
    db.refresh(contact)
    return contact, "verified"


def revoke_contact(db: Session, contact: OrganizationSecurityContact) -> bool:
    if contact.status == "revoked":
        return False
    contact.status = "revoked"
    contact.revoked_at = _now()
    # A revoked contact's outstanding token must not remain redeemable.
    contact.verification_superseded_at = (contact.verification_superseded_at or _now())
    db.commit()
    return True


def security_recipients(db: Session, org_id) -> list[tuple[str, str]]:
    """VERIFIED security contacts only.

    Pending, expired and revoked are excluded. Billing contacts, commercial contacts and
    "every organization admin" are never substituted - which is the whole reason SEC-006 is
    foundational to SEC-001 and SEC-003.
    """
    if not org_id:
        return []
    rows = db.scalars(
        select(OrganizationSecurityContact).where(
            OrganizationSecurityContact.org_id == org_id,
            OrganizationSecurityContact.status == "verified",
            OrganizationSecurityContact.revoked_at.is_(None))).all()
    seen, out = set(), []
    for row in rows:
        key = row.email.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append((row.email, row.display_name or "there"))
    return out


CONTACT_SCOPE_NOTE = (
    "A verified security contact receives confirmed high-risk security alerts, emergency "
    "access disclosures, and security incident notices for this organization."
)


def notify_contact_verification(db: Session, background,
                                contact: OrganizationSecurityContact,
                                raw_token: str) -> bool:
    if contact.status != "pending" or not raw_token:
        return False
    if not _claim(db, kind="contact_verification", subject_type="security_contact",
                  subject_id=contact.id, version=contact.verification_version,
                  org_id=contact.org_id):
        return False
    org = db.get(Organization, contact.org_id)
    # Deliberately NOT preference-gated: an unverified contact has no preferences, and this
    # is the message that establishes the channel every other security notice depends on.
    _queue(background, email_mod.send_security_contact_verify_email,
           [(contact.email, contact.display_name or "there")],
           org_name=org.name if org else "your Organization",
           reason=("Your organization nominated this address to receive security "
                   "notifications."),
           scope=CONTACT_SCOPE_NOTE,
           expires_at=email_mod.billing_date(contact.verification_expires_at),
           verify_url=f"{security_url()}/contacts/verify?t={raw_token}")
    return True


# ══ SEC-006 — access-policy violations ══════════════════════════════════════════════════

def confirm_violation(db: Session, *, org_id, affected_user_id, category: str, source: str,
                      safe_summary: str, severity: str = "medium",
                      remediation_required: str | None = None,
                      access_restricted: bool = False,
                      confirmed_by=None) -> AccessPolicyViolation | None:
    """Record a CONFIRMED violation.

    The ONLY writer of this table. `source` must be an authoritative control from
    VIOLATION_SOURCES - `authorization_denial` is deliberately not a member, so an ordinary
    403 has no route here. No authorization helper in this codebase calls this function.
    """
    if category not in VIOLATION_CATEGORIES or source not in VIOLATION_SOURCES:
        return None
    row = AccessPolicyViolation(
        org_id=org_id, affected_user_id=affected_user_id, category=category,
        severity=severity, status="confirmed", confirmed_at=_now(), source=source,
        confirmed_by=confirmed_by, safe_summary=(safe_summary or "")[:300] or None,
        remediation_required=(remediation_required or "")[:300] or None,
        access_restricted=bool(access_restricted))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def notify_violation(db: Session, background, violation: AccessPolicyViolation) -> bool:
    """Notify the affected user and the organization's VERIFIED security contacts."""
    if violation.status != "confirmed":
        return False
    if violation.notified_at is not None:
        return False
    if not _claim(db, kind="violation_confirmed", subject_type="violation",
                  subject_id=violation.id, org_id=violation.org_id):
        return False
    violation.notified_at = _now()
    db.commit()
    org = db.get(Organization, violation.org_id) if violation.org_id else None
    if not _may_send("SEC-006", org):
        return False

    people = list(security_recipients(db, violation.org_id))
    affected = (db.get(User, violation.affected_user_id)
                if violation.affected_user_id else None)
    if affected is not None and affected.email:
        people.insert(0, (affected.email, affected.full_name or "there"))
    if not people:
        return False

    _queue(background, email_mod.send_security_violation_email, people,
           account=(affected.email if affected else "An account in your organization"),
           org_name=org.name if org else "your Organization",
           occurred_at=email_mod.billing_date(violation.confirmed_at),
           # A coarse approved category. No rule id, no threshold, no signature.
           category=VIOLATION_CATEGORY_LABELS[violation.category],
           summary=violation.safe_summary or "A security policy condition was met.",
           access_restricted=bool(violation.access_restricted),
           remediation=(violation.remediation_required
                        or "Review your recent activity and contact your administrator."),
           security_url=security_url())
    return True


# ══ SEC-001 — urgent security alert ═════════════════════════════════════════════════════

def open_event(db: Session, *, org_id, affected_user_id, category: str,
               severity: str = "high", summary: str | None = None) -> SecurityEvent | None:
    """Record a DETECTED event. Detection alone communicates nothing."""
    if category not in SEC_EVENT_CATEGORIES:
        return None
    row = SecurityEvent(org_id=org_id, affected_user_id=affected_user_id, category=category,
                        severity=severity, status="detected", detected_at=_now(),
                        customer_safe_summary=(summary or "")[:500] or None)
    row.reference = _reference("SEC", db, SecurityEvent, SecurityEvent.reference)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def confirm_event(db: Session, event: SecurityEvent, *, summary: str,
                  remediation: str | None = None, access_state: str | None = None,
                  confirmed_by=None) -> bool:
    """Promote a detected event to CONFIRMED. Only this makes SEC-001 sendable."""
    if event.status not in ("detected", "under_review"):
        return False
    event.status = "confirmed"
    event.confirmed_at = _now()
    event.confirmed_by = confirmed_by
    event.customer_safe_summary = (summary or "")[:500] or None
    event.remediation_required = (remediation or "")[:300] or None
    event.access_state = (access_state or "")[:120] or None
    db.commit()
    return True


def contain_event(db: Session, event: SecurityEvent, *,
                  access_state: str | None = None) -> bool:
    """Record authoritative CONTAINMENT.

    Nothing infers this. A password change, a revoked token, a blocked address or a forced
    sign-out does not set `contained_at`, so the containment sentence cannot appear until an
    operator asserts the fact.
    """
    if event.status != "confirmed":
        return False
    event.status = "contained"
    event.contained_at = _now()
    if access_state:
        event.access_state = access_state[:120]
    db.commit()
    return True


def resolve_event(db: Session, event: SecurityEvent, *, summary: str | None = None) -> bool:
    """RESOLVED is distinct from CONTAINED: containment stops the bleeding, resolution ends
    the matter, and the domain keeps them apart."""
    if event.status not in ("confirmed", "contained"):
        return False
    event.status = "resolved"
    event.resolved_at = _now()
    if summary:
        event.customer_safe_summary = summary[:500]
    db.commit()
    return True


def containment_note(event: SecurityEvent) -> str:
    """The only source of containment wording. Reads `contained_at`, nothing else."""
    if event.contained_at is not None:
        return "The issue has been contained."
    return "Our team is actively working to contain this."


def _event_people(db: Session, event: SecurityEvent) -> list[tuple[str, str]]:
    """Affected holder + VERIFIED security contacts. Nobody else.

    Never every organization member, an attendee, a contributor or a billing contact.
    """
    people = list(security_recipients(db, event.org_id))
    affected = db.get(User, event.affected_user_id) if event.affected_user_id else None
    if affected is not None and affected.email:
        people.insert(0, (affected.email, affected.full_name or "there"))
    seen, out = set(), []
    for address, name in people:
        key = address.strip().lower()
        if key not in seen:
            seen.add(key)
            out.append((address, name))
    return out


def alert(db: Session, background, event: SecurityEvent) -> str | None:
    """Announce a CONFIRMED high-risk event, or its containment/resolution.

    Refuses anything below `confirmed`, so an unverified detector score cannot page a
    customer. One notice per governed transition.
    """
    if event.status not in EVENT_ALERTABLE:
        return None
    kind = {"confirmed": "urgent_alert", "contained": "event_contained",
            "resolved": "event_resolved"}[event.status]
    if not _claim(db, kind=kind, subject_type="security_event", subject_id=event.id,
                  org_id=event.org_id, detail=event.category):
        return None
    org = db.get(Organization, event.org_id) if event.org_id else None
    if not _may_send("SEC-001", org):
        return None
    people = _event_people(db, event)
    if not people:
        return None

    affected = db.get(User, event.affected_user_id) if event.affected_user_id else None
    _queue(background, email_mod.send_security_alert_email, people,
           variant=event.status,
           reference=event.reference or "Not assigned",
           account=(affected.email if affected else "Your organization"),
           org_name=org.name if org else "your Organization",
           category=SEC_EVENT_CATEGORY_LABELS[event.category],
           confirmed_at=email_mod.billing_date(event.confirmed_at),
           contained_at=(email_mod.billing_date(event.contained_at)
                         if event.contained_at else None),
           resolved_at=(email_mod.billing_date(event.resolved_at)
                        if event.resolved_at else None),
           # Reads contained_at only - never inferred from a token revocation.
           containment=containment_note(event),
           access_state=event.access_state or "Under review",
           summary=event.customer_safe_summary or "A high-risk security event was confirmed.",
           remediation=(event.remediation_required
                        or "Sign in to the Security Center and review recent activity."),
           security_url=security_url(),
           recovery_note=("If you cannot sign in, use account recovery rather than "
                          "replying to this message."))
    return kind


# ══ SEC-002 — break-glass lifecycle ═════════════════════════════════════════════════════

def authorize_breakglass(db: Session, req: SupportAccessRequest) -> tuple[bool, str]:
    """Whether this emergency session may currently act.

    Delegates entirely to what already exists: the independent countersign recorded on the
    request by `support_access.countersign_emergency` (which refuses self-approval), and the
    elevation's own expiry, which `services/ops.current_elevation` enforces with
    `expires_at > now()`. Nothing is re-decided here.
    """
    from ..models import ElevationSession

    if not req.emergency:
        return False, "not an emergency request"
    if req.emergency_authorizer_id is None:
        return False, "no independent authorization was recorded"
    if req.emergency_authorizer_id == req.engineer_id:
        # Defence in depth: countersign_emergency already refuses this.
        return False, "the requester cannot authorize their own emergency access"
    if req.elevation_session_id is None:
        return False, "no elevation session was opened"
    session = db.get(ElevationSession, req.elevation_session_id)
    if session is None:
        return False, "the elevation session no longer exists"
    if session.ended_at is not None:
        return False, "the elevation session has ended"
    if session.expires_at is None or session.expires_at <= _now():
        return False, "the elevation session has expired"
    return True, "active"


def _breakglass_people(db: Session, req: SupportAccessRequest) -> list[tuple[str, str]]:
    """Organization owner + VERIFIED security contacts.

    The owner is included because emergency access to their tenant is theirs to know about
    whether or not they hold a security-contact role.
    """
    people = list(security_recipients(db, req.org_id))
    org = db.get(Organization, req.org_id) if req.org_id else None
    owner = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    if owner is not None and owner.email:
        people.insert(0, (owner.email, owner.full_name or "there"))
    seen, out = set(), []
    for address, name in people:
        key = address.strip().lower()
        if key not in seen:
            seen.add(key)
            out.append((address, name))
    return out


def _breakglass_shared(db: Session, req: SupportAccessRequest, org) -> dict:
    from . import support_access

    authorizer = (db.get(User, req.emergency_authorizer_id)
                  if req.emergency_authorizer_id else None)
    return {
        "org_name": org.name if org else "your Organization",
        "reference": str(req.id)[:8].upper(),
        "reason": (req.reason_category or "operational").replace("_", " ").title(),
        "scope": ", ".join(support_access.actions_list(req.allowed_actions))
                 or "Not recorded",
        "operator": req.engineer_display or "A Zoiko Steam engineer",
        # The independent approval is stated because it is what makes the access legitimate.
        "approval": (f"Independently authorized by {authorizer.email}" if authorizer
                     else "Independent authorization not recorded"),
        "security_url": security_url(),
    }


def notify_breakglass_started(db: Session, background,
                              req: SupportAccessRequest) -> bool:
    """Disclose emergency access that has actually become active."""
    active, _why = authorize_breakglass(db, req)
    if not active:
        return False
    if req.breakglass_started_notified_at is not None:
        return False
    if not _claim(db, kind="breakglass_started", subject_type="support_access",
                  subject_id=req.id, org_id=req.org_id):
        return False
    req.breakglass_started_notified_at = _now()
    db.commit()
    org = db.get(Organization, req.org_id) if req.org_id else None
    if not _may_send("SEC-002", org):
        return False
    people = _breakglass_people(db, req)
    if not people:
        return False

    from ..models import ElevationSession

    session = db.get(ElevationSession, req.elevation_session_id)
    _queue(background, email_mod.send_breakglass_started_email, people,
           started_at=email_mod.billing_date(session.granted_at if session else None),
           # A real, enforced expiry - current_elevation() refuses the session past it.
           expires_at=email_mod.billing_date(session.expires_at if session else None),
           **_breakglass_shared(db, req, org))
    return True


def end_breakglass(db: Session, req: SupportAccessRequest, *, reason: str,
                   review_due_at: datetime | None = None) -> bool:
    """Close an emergency session and, if given, record when its review is due."""
    from ..models import ElevationSession

    session = (db.get(ElevationSession, req.elevation_session_id)
               if req.elevation_session_id else None)
    if session is None:
        return False
    if session.ended_at is None:
        session.ended_at = _now()
    # Only ever set from an explicit argument. Nothing derives a review deadline.
    if review_due_at is not None:
        req.review_due_at = review_due_at
    db.commit()
    return True


def notify_breakglass_ended(db: Session, background, req: SupportAccessRequest, *,
                            end_reason: str) -> bool:
    if req.breakglass_ended_notified_at is not None:
        return False
    from ..models import ElevationSession

    session = (db.get(ElevationSession, req.elevation_session_id)
               if req.elevation_session_id else None)
    if session is None or session.ended_at is None:
        return False
    if not _claim(db, kind="breakglass_ended", subject_type="support_access",
                  subject_id=req.id, org_id=req.org_id, detail=end_reason):
        return False
    req.breakglass_ended_notified_at = _now()
    db.commit()
    org = db.get(Organization, req.org_id) if req.org_id else None
    if not _may_send("SEC-002", org):
        return False
    people = _breakglass_people(db, req)
    if not people:
        return False

    duration = "Unknown"
    if session.granted_at and session.ended_at:
        seconds = max(0, int((session.ended_at - session.granted_at).total_seconds()))
        duration = (f"{seconds // 60} minutes" if seconds < 3600
                    else f"{seconds // 3600}h {(seconds % 3600) // 60}m")
    _queue(background, email_mod.send_breakglass_ended_email, people,
           started_at=email_mod.billing_date(session.granted_at),
           ended_at=email_mod.billing_date(session.ended_at),
           duration=duration,
           end_reason=end_reason,
           # Access ending is not a finding about what was done. Stated plainly.
           review_note=("A review of what was accessed is pending."
                        if req.post_use_review_at is None else
                        "The post-access review is complete."),
           **_breakglass_shared(db, req, org))
    return True


def review_overdue(req: SupportAccessRequest, now: datetime | None = None) -> bool:
    """Strictly: a stored deadline has passed and no review has been recorded."""
    if req.review_due_at is None:
        return False
    if req.post_use_review_at is not None:
        return False
    return req.review_due_at < (now or _now())


def notify_review_overdue(db: Session, background, req: SupportAccessRequest) -> bool:
    if not review_overdue(req):
        return False
    if req.review_overdue_notified_at is not None:
        return False
    if not _claim(db, kind="breakglass_review_overdue", subject_type="support_access",
                  subject_id=req.id, org_id=req.org_id):
        return False
    req.review_overdue_notified_at = _now()
    db.commit()
    org = db.get(Organization, req.org_id) if req.org_id else None
    if not _may_send("SEC-002", org):
        return False
    people = _breakglass_people(db, req)
    if not people:
        return False

    _queue(background, email_mod.send_breakglass_review_email, people,
           due_at=email_mod.billing_date(req.review_due_at),
           **_breakglass_shared(db, req, org))
    return True


# ══ SEC-003 — organization security incident ════════════════════════════════════════════

def open_disclosure(db: Session, incident: Incident, org: Organization, *,
                    affected_service: str, customer_impact: str,
                    recommended_action: str | None = None,
                    next_update_at: datetime | None = None
                    ) -> SecurityIncidentDisclosure:
    """Create the customer-facing face of an internal incident.

    Only these fields cross the boundary. `Incident.detail`, `Incident.commander` and its
    severity are never copied, so an internal note cannot reach a customer by accident.
    """
    row = SecurityIncidentDisclosure(
        incident_id=incident.id, org_id=org.id, reference=incident.ref, status="open",
        affected_service=affected_service, customer_impact=customer_impact,
        recommended_action=recommended_action, next_update_at=next_update_at,
        opened_at=_now(), disclosure_version=1, published_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def publish_disclosure_update(db: Session, disclosure: SecurityIncidentDisclosure, *,
                              status: str | None = None,
                              customer_impact: str | None = None,
                              recommended_action: str | None = None,
                              next_update_at: datetime | None = None) -> bool:
    """Publish a meaningful customer-visible change.

    Bumping the version is what makes it a communication event. An internal incident edit
    that never calls this announces nothing.
    """
    changed = False
    for field, value in (("status", status), ("customer_impact", customer_impact),
                         ("recommended_action", recommended_action)):
        if value is not None and getattr(disclosure, field) != value:
            setattr(disclosure, field, value)
            changed = True
    if next_update_at is not None:
        disclosure.next_update_at = next_update_at
        changed = True
    if not changed:
        return False
    if status == "contained" and disclosure.contained_at is None:
        disclosure.contained_at = _now()
    if status == "resolved" and disclosure.resolved_at is None:
        disclosure.resolved_at = _now()
    disclosure.disclosure_version += 1
    disclosure.published_at = _now()
    db.commit()
    return True


def disclosure_next_update(disclosure: SecurityIncidentDisclosure) -> str:
    """Stored commitment or the honest fallback. Nothing derives a time."""
    if disclosure.next_update_at is None:
        return "We will update you when we have more information."
    return f"Next update by {email_mod.billing_date(disclosure.next_update_at)}."


def notify_disclosure(db: Session, background,
                      disclosure: SecurityIncidentDisclosure) -> str | None:
    """Announce one disclosure version to VERIFIED security contacts only."""
    kind = {"open": "incident_opened", "investigating": "incident_update",
            "contained": "incident_contained",
            "resolved": "incident_resolved"}.get(disclosure.status)
    if kind is None:
        return None
    if not _claim(db, kind=kind, subject_type="incident_disclosure",
                  subject_id=disclosure.id, version=disclosure.disclosure_version,
                  org_id=disclosure.org_id):
        return None
    org = db.get(Organization, disclosure.org_id)
    if not _may_send("SEC-003", org):
        return None
    people = security_recipients(db, disclosure.org_id)
    if not people:
        return None

    _queue(background, email_mod.send_security_incident_email, people,
           variant=kind,
           reference=disclosure.reference,
           org_name=org.name if org else "your Organization",
           status=DISCLOSURE_STATUS_LABELS.get(disclosure.status, "Under review"),
           opened_at=email_mod.billing_date(disclosure.opened_at),
           contained_at=(email_mod.billing_date(disclosure.contained_at)
                         if disclosure.contained_at else None),
           resolved_at=(email_mod.billing_date(disclosure.resolved_at)
                        if disclosure.resolved_at else None),
           affected_service=disclosure.affected_service or "Under assessment",
           impact=disclosure.customer_impact or "Under assessment",
           action=disclosure.recommended_action or "No action is needed from you right now.",
           resolution=disclosure.resolution_summary,
           # Only mentioned when an approved artifact genuinely exists.
           report_available=bool(disclosure.report_available),
           next_update=disclosure_next_update(disclosure),
           # Evidence is never attached or quoted; it is viewed behind authentication.
           evidence_note=("Secure incident details are available in your Security Center. "
                          "We never ask for passwords, recovery codes or API keys by email."),
           security_url=security_url())
    return kind


# ══ SEC-004 — abuse reports ═════════════════════════════════════════════════════════════

def file_report(db: Session, *, category: str, subject_type: str, subject_id=None,
                description: str | None = None, reporter_id=None,
                reporter_contact: str | None = None, reporter_name: str | None = None,
                org_id=None) -> AbuseReport | None:
    if category not in ABUSE_CATEGORIES or subject_type not in ABUSE_SUBJECT_TYPES:
        return None
    row = AbuseReport(category=category, subject_type=subject_type, subject_id=subject_id,
                      description=description, reporter_id=reporter_id,
                      reporter_contact=(reporter_contact or "").strip().lower() or None,
                      reporter_name=reporter_name, org_id=org_id, status="received")
    row.reference = _reference("ABR", db, AbuseReport, AbuseReport.reference)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def reported_party_view(report: AbuseReport) -> dict:
    """The ONLY projection of a report for the reported side.

    Every reporter field is omitted: identity, contact, name, and the free-text description
    (which frequently identifies the reporter implicitly). A reported organization that can
    read this learns what was alleged, never who alleged it.
    """
    return {
        "reference": report.reference,
        "category": report.category,
        "subject_type": report.subject_type,
        "status": report.status,
        "created_at": report.created_at,
    }


def triage_report(db: Session, report: AbuseReport, *, status: str = "under_review") -> bool:
    if report.status not in ("received", "triaged"):
        return False
    report.status = status if status in ("triaged", "under_review") else "triaged"
    report.triaged_at = report.triaged_at or _now()
    db.commit()
    return True


def close_report(db: Session, report: AbuseReport, *, closure_note: str) -> bool:
    """Close a report with an outcome-free closure note."""
    if closure_note not in ABUSE_CLOSURE_NOTES:
        return False
    if report.status == "closed":
        return False
    report.status = "closed"
    report.closed_at = _now()
    report.closure_note = closure_note
    db.commit()
    return True


def _reporter_people(report: AbuseReport, db: Session) -> list[tuple[str, str]]:
    if report.reporter_contact:
        return [(report.reporter_contact, report.reporter_name or "there")]
    reporter = db.get(User, report.reporter_id) if report.reporter_id else None
    if reporter is not None and reporter.email:
        return [(reporter.email, reporter.full_name or "there")]
    return []


# Deliberately outcome-free. Nothing here promises suspension, removal, a ban, legal action
# or a refund - SEC-004 forbids the platform acting as an enforcement oracle.
REVIEW_PROMISE = "We will review the report under our policies."


def notify_report_received(db: Session, background, report: AbuseReport) -> bool:
    if report.status != "received":
        return False
    if report.received_notified_at is not None:
        return False
    if not _claim(db, kind="abuse_received", subject_type="abuse_report",
                  subject_id=report.id, org_id=report.org_id):
        return False
    report.received_notified_at = _now()
    db.commit()
    people = _reporter_people(report, db)
    if not people:
        return False
    _queue(background, email_mod.send_abuse_received_email, people,
           reference=report.reference,
           received_at=email_mod.billing_date(report.created_at),
           category=report.category.replace("_", " ").title(),
           promise=REVIEW_PROMISE,
           # Reporting is operational processing, not a marketing signup.
           consent_note=("We use your contact details only to handle this report. It does "
                         "not subscribe you to marketing or any mailing list."),
           security_url=security_url())
    return True


def notify_report_closed(db: Session, background, report: AbuseReport) -> bool:
    if report.status != "closed" or not report.closure_note:
        return False
    if report.closed_notified_at is not None:
        return False
    if not _claim(db, kind="abuse_closed", subject_type="abuse_report",
                  subject_id=report.id, org_id=report.org_id):
        return False
    report.closed_notified_at = _now()
    db.commit()
    people = _reporter_people(report, db)
    if not people:
        return False
    _queue(background, email_mod.send_abuse_closed_email, people,
           reference=report.reference,
           closed_at=email_mod.billing_date(report.closed_at),
           # One of four approved notes, none of which names an enforcement action.
           outcome=ABUSE_CLOSURE_LABELS[report.closure_note],
           security_url=security_url())
    return True


# ══ SEC-005 — restriction and appeal ════════════════════════════════════════════════════

def propose_restriction(db: Session, org: Organization, *, content_type: str,
                        restriction_type: str, content_id=None,
                        content_label: str | None = None,
                        customer_safe_reason: str | None = None,
                        appeal_allowed: bool = True,
                        appeal_deadline: datetime | None = None,
                        created_by=None) -> ContentRestriction | None:
    """Create a PROPOSED restriction. A proposal communicates nothing."""
    if (content_type not in RESTRICTED_CONTENT_TYPES
            or restriction_type not in RESTRICTION_TYPES):
        return None
    row = ContentRestriction(
        org_id=org.id, content_type=content_type, content_id=content_id,
        content_label=content_label, status="proposed", restriction_type=restriction_type,
        customer_safe_reason=(customer_safe_reason or "")[:500] or None,
        appeal_allowed=bool(appeal_allowed), appeal_deadline=appeal_deadline,
        created_by=created_by, cycle=0)
    row.reference = _reference("RST", db, ContentRestriction, ContentRestriction.reference)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def activate_restriction(db: Session, restriction: ContentRestriction) -> bool:
    """Commit and enforce the restriction. Only now is a notice owed."""
    if restriction.status == "active":
        return False
    restriction.status = "active"
    restriction.effective_at = _now()
    restriction.removed_at = None
    restriction.cycle = (restriction.cycle or 0) + 1
    db.commit()
    return True


def remove_restriction(db: Session, restriction: ContentRestriction, *,
                       reason: str | None = None) -> bool:
    if restriction.status != "active":
        return False
    restriction.status = "removed"
    restriction.removed_at = _now()
    restriction.removed_reason = (reason or "")[:300] or None
    db.commit()
    return True


def removal_position(db: Session, restriction: ContentRestriction) -> tuple[bool, str]:
    """Whether a removal claim may be made, and exactly what may be said.

    Reuses MED-011's authoritative retention state: a recording under legal hold, or with a
    retention window still running, has NOT been deleted - and no message may say it was.
    Nothing here ever claims every copy everywhere is gone.
    """
    from ..models import LiveRecording

    if restriction.content_type not in ("recording", "replay") or not restriction.content_id:
        if restriction.content_deleted_at is None:
            return False, "no removal has been recorded"
        return True, ("The content was removed from the platform. Audit and security records "
                      "for it are retained under their own policies.")
    recording = db.get(LiveRecording, restriction.content_id)
    if recording is None:
        return False, "the content record no longer exists"
    if recording.legal_hold:
        return False, "the content is under legal hold and cannot be removed"
    if recording.retention_expires_at and recording.retention_expires_at > _now():
        return False, "a retention window is still running on this content"
    if (recording.deletion_status or "") != "deleted":
        return False, "storage removal has not completed"
    return True, ("The media was deleted from storage. Backup copies may persist until their "
                  "own retention expires, and audit records are retained under policy.")


def _restriction_people(db: Session, restriction: ContentRestriction) -> list[tuple[str, str]]:
    """Affected owner + authorized publishers. Never a complainant."""
    from . import replay_comms

    org = db.get(Organization, restriction.org_id)
    people = []
    owner = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    if owner is not None and owner.email:
        people.append((owner.email, owner.full_name or "there"))
    for user in replay_comms.publishers(db, restriction.org_id):
        if user.email:
            people.append((user.email, user.full_name or "there"))
    seen, out = set(), []
    for address, name in people:
        key = address.strip().lower()
        if key not in seen:
            seen.add(key)
            out.append((address, name))
    return out


def notify_restriction_active(db: Session, background,
                              restriction: ContentRestriction) -> bool:
    if restriction.status != "active":
        return False
    if not _claim(db, kind="restriction_active", subject_type="restriction",
                  subject_id=restriction.id, version=restriction.cycle,
                  org_id=restriction.org_id):
        return False
    org = db.get(Organization, restriction.org_id)
    if not _may_send("SEC-005", org):
        return False
    people = _restriction_people(db, restriction)
    if not people:
        return False

    _queue(background, email_mod.send_content_restriction_email, people,
           reference=restriction.reference,
           org_name=org.name if org else "your Organization",
           content=restriction.content_label or restriction.content_type.title(),
           restriction_type=RESTRICTION_TYPE_LABELS[restriction.restriction_type],
           effective_at=email_mod.billing_date(restriction.effective_at),
           # Operator-authored. The complainant is never named.
           reason=restriction.customer_safe_reason or "A policy review of this content.",
           appeal_allowed=bool(restriction.appeal_allowed),
           appeal_deadline=(email_mod.billing_date(restriction.appeal_deadline)
                            if restriction.appeal_deadline else None),
           security_url=security_url())
    return True


def submit_appeal(db: Session, restriction: ContentRestriction, *, submitted_by=None,
                  submitted_by_email: str | None = None,
                  grounds: str | None = None) -> RestrictionAppeal | None:
    """Submit an appeal. Refused when the restriction does not permit one."""
    if restriction.status != "active" or not restriction.appeal_allowed:
        return None
    if restriction.appeal_deadline and restriction.appeal_deadline < _now():
        return None
    existing = db.scalar(
        select(RestrictionAppeal).where(
            RestrictionAppeal.restriction_id == restriction.id,
            RestrictionAppeal.status.in_(("submitted", "under_review"))))
    if existing is not None:
        return existing
    row = RestrictionAppeal(restriction_id=restriction.id, org_id=restriction.org_id,
                            submitted_by=submitted_by,
                            submitted_by_email=submitted_by_email,
                            submitted_at=_now(), grounds=grounds, status="submitted")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def decide_appeal(db: Session, appeal: RestrictionAppeal,
                  restriction: ContentRestriction, *, granted: bool,
                  decision: str, decided_by=None) -> bool:
    """Decide an appeal.

    A GRANTED appeal lifts the restriction in the same commit, so the granted notice can
    never precede the enforcement change it announces.
    """
    if appeal.status in ("upheld", "granted"):
        return False
    appeal.status = "granted" if granted else "upheld"
    appeal.decided_at = _now()
    appeal.decided_by = decided_by
    appeal.customer_safe_decision = (decision or "")[:500] or None
    if granted:
        restriction.status = "removed"
        restriction.removed_at = _now()
        restriction.removed_reason = "Appeal granted"
    db.commit()
    return True


def _appeal_people(db: Session, appeal: RestrictionAppeal,
                   restriction: ContentRestriction) -> list[tuple[str, str]]:
    if appeal.submitted_by_email:
        return [(appeal.submitted_by_email, "there")]
    return _restriction_people(db, restriction)


def notify_appeal(db: Session, background, appeal: RestrictionAppeal,
                  restriction: ContentRestriction) -> str | None:
    kind = {"submitted": "appeal_received", "upheld": "appeal_upheld",
            "granted": "appeal_granted"}.get(appeal.status)
    if kind is None:
        return None
    if kind == "appeal_granted" and restriction.status != "removed":
        # The enforcement change must already be committed before it is announced.
        return None
    if not _claim(db, kind=kind, subject_type="appeal", subject_id=appeal.id,
                  org_id=appeal.org_id):
        return None
    org = db.get(Organization, appeal.org_id)
    if not _may_send("SEC-005", org):
        return None
    people = _appeal_people(db, appeal, restriction)
    if not people:
        return None

    _queue(background, email_mod.send_restriction_appeal_email, people,
           variant=kind,
           reference=restriction.reference,
           org_name=org.name if org else "your Organization",
           content=restriction.content_label or restriction.content_type.title(),
           submitted_at=email_mod.billing_date(appeal.submitted_at),
           decided_at=(email_mod.billing_date(appeal.decided_at)
                       if appeal.decided_at else None),
           decision=appeal.customer_safe_decision or "Our review is complete.",
           current_state=("The restriction has been lifted." if appeal.status == "granted"
                          else "The restriction remains in effect."
                          if appeal.status == "upheld" else
                          "The restriction remains in effect while we review."),
           next_step=("No further action is needed." if appeal.status == "granted"
                      else "Contact us through the Security Center if you have more "
                           "information."),
           security_url=security_url())
    return kind


def notify_removal_completed(db: Session, background,
                             restriction: ContentRestriction) -> bool:
    """Announce a removal only when storage state and retention policy both permit it."""
    allowed, sentence = removal_position(db, restriction)
    if not allowed:
        return False
    if not _claim(db, kind="restriction_removed", subject_type="restriction",
                  subject_id=restriction.id, version=restriction.cycle,
                  org_id=restriction.org_id):
        return False
    org = db.get(Organization, restriction.org_id)
    if not _may_send("SEC-005", org):
        return False
    people = _restriction_people(db, restriction)
    if not people:
        return False

    _queue(background, email_mod.send_content_removed_email, people,
           reference=restriction.reference,
           org_name=org.name if org else "your Organization",
           content=restriction.content_label or restriction.content_type.title(),
           completed_at=email_mod.billing_date(restriction.content_deleted_at
                                               or restriction.removed_at),
           # Never "all copies permanently deleted": the sentence states exactly what the
           # retention and storage state support.
           residual=sentence,
           security_url=security_url())
    return True


# ══ sweeper ═════════════════════════════════════════════════════════════════════════════

def sweep(db: Session, background=None) -> dict:
    """Deadline-driven security obligations, on the existing leader-elected ticker."""
    background = background or _Bg()
    counts = {"review_overdue": 0}
    try:
        for req in db.scalars(
            select(SupportAccessRequest).where(
                SupportAccessRequest.review_due_at.isnot(None),
                SupportAccessRequest.post_use_review_at.is_(None),
                SupportAccessRequest.review_overdue_notified_at.is_(None),
                SupportAccessRequest.review_due_at < _now())).all():
            if notify_review_overdue(db, background, req):
                counts["review_overdue"] += 1
    except Exception:  # noqa: BLE001 - a bad sweep must not kill the ticker
        log.exception("SEC-002 break-glass review sweep failed")
    return counts
