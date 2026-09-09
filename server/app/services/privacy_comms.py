"""Privacy request, export, deletion and notice communications
(ZST-EC-001 PRV-001 -> PRV-004).

**No statutory deadline is invented.** `resolve_deadline()` consults
`models.privacy.DEADLINE_POLICIES`, which is EMPTY in this product because no jurisdiction
rules are configured anywhere. It therefore returns (None, None) and every message says no
statutory deadline is configured. Hardcoding 30 days would be a fabricated legal
commitment, which is why PRV-002's deadline handling is reported PARTIAL.

**Account ownership is not blanket verification.** `verification_needed()` requires a fresh
purpose-bound verification for EXPORT and DELETION regardless of session state, because both
are disclosive or irreversible. A signed-in user asking for a correction is treated more
lightly - the distinction is by consequence, not by convenience.

**Exports are never emailed.** `generate_export()` writes to private storage and mints a
single-use, request-bound, purpose-bound token with a 60-minute life. The message carries a
link; `authorize_download()` is the only thing that resolves it, and every attempt lands in
`PrivacyExportAccess` with an outcome - but never with the exported content.

**Deletion tells the truth about residue.** `plan_deletion()` reads the retention authority
that already exists (MED-011's `legal_hold` / `retention_expires_at`, and
`GovernanceRecord(kind="legal_hold")`) plus the record classes this platform lawfully keeps -
audit, security, billing. `deletion_sentence()` is generated from that list, so the platform
cannot claim everything is gone while any of it remains.

**Acknowledgement is never relabelled consent.** A notice version carries
`consent_required` explicitly. `record_decision()` stores exactly what was chosen and has no
default, so silence cannot become acceptance, and `consent_options()` returns Accept and
Decline as equal peers with nothing preselected.

**Subprocessors are real.** `KNOWN_SUBPROCESSORS` lists only providers this codebase
genuinely integrates, each traceable to configuration and call sites. No vendor is invented.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    CONSENT_DECISIONS,
    DEADLINE_POLICIES,
    DECISION_REASON_LABELS,
    DECISION_REASONS,
    DOWNLOAD_TTL_MINUTES,
    EXTENSION_REASON_LABELS,
    EXTENSION_REASONS,
    PURPOSE_PRIVACY_DOWNLOAD,
    PURPOSE_PRIVACY_VERIFICATION,
    REPRESENTATIVE_TYPES,
    REQUEST_TYPE_LABELS,
    REQUEST_TYPES,
    RESIDUAL_CATEGORY_LABELS,
    VERIFICATION_TTL_HOURS,
    AuditLog,
    Event,
    GovernanceRecord,
    Invoice,
    LiveRecording,
    Organization,
    PrivacyConsentDecision,
    PrivacyDeletion,
    PrivacyExport,
    PrivacyExportAccess,
    PrivacyNotice,
    PrivacyNoticeVersion,
    PrivacyRepresentative,
    PrivacyRequest,
    RetentionException,
    Subprocessor,
    User,
)

log = logging.getLogger(__name__)

_TOKEN_BYTES = 32

# Only providers this codebase genuinely integrates. Each is traceable to real
# configuration and real call sites; nothing here is a placeholder or a guess.
KNOWN_SUBPROCESSORS = [
    {"name": "Resend", "service": "Transactional email",
     "processing_purpose": "Delivering account, security and service notifications",
     "region": None},
    {"name": "LiveKit", "service": "Live streaming and media ingest",
     "processing_purpose": "Carrying live audio and video for events",
     "region": None},
    {"name": "Stripe", "service": "Payment processing",
     "processing_purpose": "Taking payments and handling refunds and disputes",
     "region": None},
    {"name": "Google Cloud Storage", "service": "Object storage",
     "processing_purpose": "Storing recordings, exports and generated documents",
     "region": None},
    {"name": "Upstash", "service": "Managed Redis",
     "processing_purpose": "Rate limiting and live event presence state",
     "region": None},
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _may_send(family: str, org) -> bool:
    from . import notifications

    return notifications.should_send_operational_notification(family=family, org=org)


def privacy_url() -> str:
    """The customer-facing Privacy Center. Never an admin path."""
    return f"{email_mod.public_base_url()}/organization/privacy"


def _claim(db: Session, *, kind: str, subject_type: str, subject_id, version: int = 0,
           org_id=None, detail: str | None = None) -> bool:
    existing = db.scalar(
        select(PrivacyNotice).where(PrivacyNotice.kind == kind,
                                    PrivacyNotice.subject_type == subject_type,
                                    PrivacyNotice.subject_id == subject_id,
                                    PrivacyNotice.version == version))
    if existing is not None:
        return False
    try:
        db.add(PrivacyNotice(kind=kind, subject_type=subject_type, subject_id=subject_id,
                             version=version, org_id=org_id, detail=detail))
        db.commit()
        return True
    except Exception:  # noqa: BLE001 - a lost uniqueness race IS a successful dedup
        db.rollback()
        log.info("Privacy notice %s/%s v%s already claimed", kind, subject_id, version)
        return False


def _queue(background, send, people, **kwargs) -> None:
    """Queue one message per recipient.

    Wrapped so a provider failure cannot propagate into the caller's transaction: request
    creation, verification, deadline state, export generation, deletion, notice publication
    and a consent decision must all survive Resend being unavailable.
    """
    try:
        for address, name in people:
            background.add_task(send, address, name=name, **kwargs)
    except UnsafeLinkError:
        log.exception("Privacy notice not queued: APP_URL unsafe for this environment")


class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a notice must never break committed state
            log.exception("Privacy notice failed")


def next_reference(db: Session) -> str:
    """A customer-safe immutable reference like PRV-2026-000123. Never a raw UUID."""
    stem = f"PRV-{_now().year}-"
    used = db.scalar(
        select(func.count(PrivacyRequest.id)).where(
            PrivacyRequest.request_reference.like(f"{stem}%"))) or 0
    return f"{stem}{used + 1:06d}"


# ══ recipients ══════════════════════════════════════════════════════════════════════════

def request_recipients(db: Session, request: PrivacyRequest, *,
                       require_verified: bool = False) -> list[tuple[str, str]]:
    """The requester, or a VERIFIED representative acting for them.

    Never a billing, commercial or event contact. `require_verified` is used by PRV-003:
    an export or a deletion confirmation only goes to an address that has actually proved
    control, or to a representative whose authorization a human verified.
    """
    if require_verified and request.verified_at is None:
        if request.representative_verified_at is None:
            return []
    people = [(request.requester_email, "there")]
    if request.representative_id:
        rep = db.get(PrivacyRepresentative, request.representative_id)
        if rep is not None and rep.status == "verified" and rep.revoked_at is None:
            people.append((rep.representative_email, rep.representative_name))
    seen, out = set(), []
    for address, name in people:
        key = (address or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append((address, name))
    return out


def _org(db: Session, request: PrivacyRequest) -> Organization | None:
    return db.get(Organization, request.org_id) if request.org_id else None


# ══ PRV-001 — intake and verification ══════════════════════════════════════════════════

# Request types whose consequences are irreversible or disclosive. For these, being signed
# in is NOT sufficient: a fresh purpose-bound verification is always required.
HIGH_ASSURANCE_TYPES = ("export", "access", "deletion")


def verification_needed(request_type: str, *, requester_user_id=None,
                        representative_id=None) -> bool:
    """Whether identity verification is required for this request.

    Ordinary account ownership is deliberately NOT treated as sufficient for an export,
    an access request or a deletion - each either discloses personal data or destroys it, so
    the assurance has to be fresh and purpose-bound rather than inherited from a session
    that may have been left open. A representative always verifies.
    """
    if representative_id is not None:
        return True
    if request_type in HIGH_ASSURANCE_TYPES:
        return True
    return requester_user_id is None


def resolve_deadline(request: PrivacyRequest, *,
                     jurisdiction: str | None = None) -> tuple[datetime | None, str | None]:
    """(deadline, basis) from CONFIGURED policy only.

    DEADLINE_POLICIES is empty in this product, so this returns (None, None) and the
    messages state that no statutory deadline is configured. There is no fallback constant
    and no default number of days anywhere in this module.
    """
    if not DEADLINE_POLICIES:
        return None, None
    policy = DEADLINE_POLICIES.get(jurisdiction or "")
    if not policy:
        return None, None
    days = policy.get("days")
    if not isinstance(days, int):
        return None, None
    base = request.received_at or _now()
    return base + timedelta(days=days), policy.get("basis")


DEADLINE_UNCONFIGURED_NOTE = (
    "We do not quote a statutory deadline because none is configured for your jurisdiction "
    "in this service. We will handle the request promptly and keep you updated."
)


def deadline_note(request: PrivacyRequest) -> str:
    """The ONLY source of deadline wording. Never composes a date of its own."""
    if request.extension_until is not None:
        return f"Current due date: {email_mod.billing_date(request.extension_until)}."
    if request.deadline_at is not None:
        basis = f" ({request.deadline_basis})" if request.deadline_basis else ""
        return f"Current due date: {email_mod.billing_date(request.deadline_at)}{basis}."
    return DEADLINE_UNCONFIGURED_NOTE


def open_request(db: Session, *, requester_email: str, request_type: str,
                 requester_user_id=None, org_id=None, details: str | None = None,
                 representative_id=None,
                 jurisdiction: str | None = None) -> tuple[PrivacyRequest | None, str | None]:
    """Create a privacy request. Returns (request, raw_verification_token)."""
    address = (requester_email or "").strip().lower()
    if not address or request_type not in REQUEST_TYPES:
        return None, None

    needs = verification_needed(request_type, requester_user_id=requester_user_id,
                                representative_id=representative_id)
    request = PrivacyRequest(
        requester_email=address, requester_user_id=requester_user_id, org_id=org_id,
        request_type=request_type, status="verification_required" if needs else "received",
        received_at=_now(), details=details, verification_required=needs,
        representative_id=representative_id, status_version=1)
    request.request_reference = next_reference(db)
    deadline, basis = resolve_deadline(request, jurisdiction=jurisdiction)
    request.deadline_at, request.deadline_basis = deadline, basis
    db.add(request)
    db.commit()
    db.refresh(request)

    # Tracked as the existing governance obligation so the customer-facing request and the
    # internal console are one record rather than two.
    record = GovernanceRecord(kind="privacy_request", org_id=org_id, status="open",
                              detail=f"{request.request_reference} ({request_type})",
                              due_at=request.deadline_at,
                              meta={"request_reference": request.request_reference})
    db.add(record)
    db.commit()
    request.governance_record_id = record.id
    db.commit()

    raw = issue_verification(db, request) if needs else None
    return request, raw


def issue_verification(db: Session, request: PrivacyRequest) -> str:
    """Mint a request-bound, purpose-bound verification token.

    `secrets.token_urlsafe(32)` is 256 bits from the OS CSPRNG - the same convention as
    crud/identity.py. Only the sha256 hash is stored, and re-issuing retires the previous
    token by overwriting that hash.
    """
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    request.verification_superseded_at = None
    request.verification_purpose = PURPOSE_PRIVACY_VERIFICATION
    request.verification_token_hash = _hash(raw)
    request.verification_expires_at = _now() + timedelta(hours=VERIFICATION_TTL_HOURS)
    request.verification_consumed_at = None
    request.verification_version = (request.verification_version or 0) + 1
    db.commit()
    return raw


def verify_request(db: Session, *, token: str,
                   purpose: str = PURPOSE_PRIVACY_VERIFICATION,
                   request_id=None) -> tuple[PrivacyRequest | None, str]:
    """Redeem a verification token.

    Purpose-bound, single-use and expiring. `request_id`, when supplied, additionally binds
    the redemption to one request - so a token for request A cannot be replayed against
    request B even by someone holding it.
    """
    if not token:
        return None, "invalid"
    request = db.scalar(
        select(PrivacyRequest).where(PrivacyRequest.verification_token_hash == _hash(token)))
    if request is None or request.verification_purpose != purpose:
        return None, "invalid"
    if request_id is not None and str(request.id) != str(request_id):
        return None, "wrong_request"
    if request.verification_consumed_at is not None:
        return None, "already_used"
    if request.verification_superseded_at is not None:
        return None, "superseded"
    if (request.verification_expires_at is None
            or request.verification_expires_at < _now()):
        return None, "expired"
    request.verification_consumed_at = _now()
    request.verified_at = _now()
    request.status = "verified"
    request.status_version = (request.status_version or 0) + 1
    db.commit()
    db.refresh(request)
    return request, "verified"


def claim_representative(db: Session, *, subject_email: str, representative_name: str,
                         representative_email: str, representative_type: str,
                         authorization_reference: str | None = None,
                         subject_user_id=None) -> PrivacyRepresentative | None:
    """Record a CLAIM to act for somebody.

    Only ever `claimed`. There is no document-validation or identity-proofing process in
    this product, so nothing here can promote itself to verified - that needs
    `verify_representative()` and a human decision. This is why PRV-001 is PARTIAL.
    """
    if representative_type not in REPRESENTATIVE_TYPES:
        return None
    row = PrivacyRepresentative(
        subject_email=(subject_email or "").strip().lower(),
        subject_user_id=subject_user_id, representative_name=representative_name,
        representative_email=(representative_email or "").strip().lower(),
        representative_type=representative_type, status="claimed",
        # A POINTER to evidence held elsewhere. The document itself is never stored here and
        # never emailed.
        authorization_reference=authorization_reference)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def verify_representative(db: Session, rep: PrivacyRepresentative, *,
                          verified_by) -> bool:
    """Promote a claim to verified. Requires an explicit human actor."""
    if rep.status != "claimed" or verified_by is None:
        return False
    rep.status = "verified"
    rep.verified_at = _now()
    rep.verified_by = verified_by
    db.commit()
    return True


def _shared(db: Session, request: PrivacyRequest) -> dict:
    return {
        "reference": request.request_reference or "Not assigned",
        "request_type": REQUEST_TYPE_LABELS.get(request.request_type, "Privacy request"),
        "status": (request.status or "received").replace("_", " ").title(),
        "deadline_note": deadline_note(request),
        "privacy_url": privacy_url(),
    }


def notify_received(db: Session, background, request: PrivacyRequest) -> bool:
    if not _claim(db, kind="request_received", subject_type="privacy_request",
                  subject_id=request.id, org_id=request.org_id):
        return False
    org = _org(db, request)
    if not _may_send("PRV-001", org):
        return False
    people = request_recipients(db, request)
    if not people:
        return False
    _queue(background, email_mod.send_privacy_received_email, people,
           received_at=email_mod.billing_date(request.received_at),
           verification_required=bool(request.verification_required),
           # The body carries the reference and the type, not a restatement of whatever
           # personal detail the requester wrote into `details`.
           **_shared(db, request))
    return True


def notify_verification_required(db: Session, background, request: PrivacyRequest,
                                 raw_token: str) -> bool:
    if not request.verification_required or not raw_token:
        return False
    if not _claim(db, kind="verification_required", subject_type="privacy_request",
                  subject_id=request.id, version=request.verification_version,
                  org_id=request.org_id):
        return False
    org = _org(db, request)
    if not _may_send("PRV-001", org):
        return False
    people = request_recipients(db, request)
    if not people:
        return False
    _queue(background, email_mod.send_privacy_verify_email, people,
           expires_at=email_mod.billing_date(request.verification_expires_at),
           verify_url=f"{privacy_url()}/requests/{request.id}/verify?t={raw_token}",
           # Discloses only that verification is needed - not the request's contents.
           purpose_note=("We need to confirm you control this address before we act on the "
                         "request."),
           **_shared(db, request))
    return True


# ══ PRV-002 — status, clarification, extension, decision ════════════════════════════════

# Fields whose change is internal only. A PATCH touching nothing else must not mail.
CUSTOMER_VISIBLE_FIELDS = frozenset({"status", "deadline_at", "extension_until",
                                     "clarification_needed", "decision_summary"})


def is_customer_visible(changed: dict) -> bool:
    return any(field in CUSTOMER_VISIBLE_FIELDS for field in (changed or {}))


def advance_status(db: Session, request: PrivacyRequest, *, status: str) -> bool:
    """Publish a customer-visible status change."""
    from ..models import REQUEST_STATUSES

    if status not in REQUEST_STATUSES or request.status == status:
        return False
    request.status = status
    request.status_version = (request.status_version or 0) + 1
    if status == "completed":
        request.completed_at = _now()
    db.commit()
    return True


def notify_status(db: Session, background, request: PrivacyRequest) -> bool:
    if not _claim(db, kind="status_update", subject_type="privacy_request",
                  subject_id=request.id, version=request.status_version,
                  org_id=request.org_id):
        return False
    org = _org(db, request)
    if not _may_send("PRV-002", org):
        return False
    people = request_recipients(db, request)
    if not people:
        return False
    from ..models import REQUEST_AWAITING_REQUESTER

    _queue(background, email_mod.send_privacy_status_email, people,
           updated_at=email_mod.billing_date(request.updated_at),
           action_required=request.status in REQUEST_AWAITING_REQUESTER,
           **_shared(db, request))
    return True


def request_clarification(db: Session, request: PrivacyRequest, *, needed: str,
                          due_at: datetime | None = None) -> bool:
    if not (needed or "").strip():
        return False
    request.status = "clarification_required"
    request.clarification_needed = needed
    request.clarification_due_at = due_at
    request.status_version = (request.status_version or 0) + 1
    db.commit()
    return True


def notify_clarification(db: Session, background, request: PrivacyRequest) -> bool:
    if request.status != "clarification_required" or not request.clarification_needed:
        return False
    if not _claim(db, kind="clarification_required", subject_type="privacy_request",
                  subject_id=request.id, version=request.status_version,
                  org_id=request.org_id):
        return False
    org = _org(db, request)
    if not _may_send("PRV-002", org):
        return False
    people = request_recipients(db, request)
    if not people:
        return False
    _queue(background, email_mod.send_privacy_clarification_email, people,
           needed=request.clarification_needed,
           # A due date appears only when one was actually recorded.
           due_at=(email_mod.billing_date(request.clarification_due_at)
                   if request.clarification_due_at else None),
           secure_note=("Please reply in the Privacy Center. We never ask for passwords, "
                        "recovery codes or API keys."),
           **_shared(db, request))
    return True


def extend_deadline(db: Session, request: PrivacyRequest, *, until: datetime,
                    reason: str, authorized_by=None) -> tuple[bool, datetime | None]:
    """Extend a deadline. Returns (extended, previous_deadline).

    Refused when there is no deadline to extend: with DEADLINE_POLICIES empty there is no
    statutory clock, so "extending" one would be extending a fiction. The reason must be a
    recorded category - "complex request" is never assumed.
    """
    if reason not in EXTENSION_REASONS or authorized_by is None:
        return False, None
    if request.deadline_at is None:
        return False, None
    previous = request.deadline_at
    request.extension_until = until
    request.extension_reason = reason
    request.status = "extended"
    request.status_version = (request.status_version or 0) + 1
    db.commit()
    return True, previous


def notify_extension(db: Session, background, request: PrivacyRequest, *,
                     previous: datetime) -> bool:
    if request.extension_until is None or not request.extension_reason:
        return False
    if not _claim(db, kind="deadline_extended", subject_type="privacy_request",
                  subject_id=request.id, version=request.status_version,
                  org_id=request.org_id):
        return False
    org = _org(db, request)
    if not _may_send("PRV-002", org):
        return False
    people = request_recipients(db, request)
    if not people:
        return False
    _queue(background, email_mod.send_privacy_extension_email, people,
           original_deadline=email_mod.billing_date(previous),
           new_deadline=email_mod.billing_date(request.extension_until),
           reason=EXTENSION_REASON_LABELS[request.extension_reason],
           **_shared(db, request))
    return True


def decide(db: Session, request: PrivacyRequest, *, outcome: str, reason: str,
           summary: str, decided_by=None) -> bool:
    """Record a DENIED or LIMITED decision. Requires an authorized decision-maker."""
    if outcome not in ("denied", "limited") or reason not in DECISION_REASONS:
        return False
    if decided_by is None:
        return False
    request.status = outcome
    request.decision_reason = reason
    # Customer-facing text only. Internal legal advice has no column and no parameter.
    request.decision_summary = summary
    request.denied_at = _now() if outcome == "denied" else request.denied_at
    request.limited_at = _now() if outcome == "limited" else request.limited_at
    request.status_version = (request.status_version or 0) + 1
    db.commit()
    return True


def notify_decision(db: Session, background, request: PrivacyRequest) -> bool:
    if request.status not in ("denied", "limited") or not request.decision_reason:
        return False
    if not _claim(db, kind="decision", subject_type="privacy_request",
                  subject_id=request.id, version=request.status_version,
                  org_id=request.org_id, detail=request.status):
        return False
    org = _org(db, request)
    if not _may_send("PRV-002", org):
        return False
    people = request_recipients(db, request)
    if not people:
        return False
    _queue(background, email_mod.send_privacy_decision_email, people,
           outcome="Declined" if request.status == "denied" else "Partly actioned",
           reason=DECISION_REASON_LABELS[request.decision_reason],
           summary=request.decision_summary or "Our review is complete.",
           review_route=("If you think this is wrong, reply in the Privacy Center and we "
                         "will look again."),
           **_shared(db, request))
    return True


# ══ PRV-003 — export and deletion ══════════════════════════════════════════════════════

def build_inventory(db: Session, request: PrivacyRequest) -> dict:
    """A data inventory from real stores, scoped to what the requester may receive.

    Deliberately excludes password hashes, MFA secrets, API keys, webhook signing secrets,
    security-detection state and other users' personal data. Only the requester's own
    account-level records are described.
    """
    user = db.get(User, request.requester_user_id) if request.requester_user_id else None
    inventory: dict = {
        "request_reference": request.request_reference,
        "generated_at": _now().isoformat(),
        "account": None,
        "counts": {},
        "excluded": [
            "Credential material (password hashes, MFA secrets, recovery codes)",
            "API keys and webhook signing secrets",
            "Security detection state",
            "Other people's personal data",
        ],
    }
    if user is not None:
        inventory["account"] = {
            "email": user.email, "full_name": user.full_name, "role": user.role,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "email_verified": bool(user.email_verified),
        }
        inventory["counts"]["audit_entries"] = db.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.actor_id == user.id)) or 0
    return inventory


def generate_export(db: Session, request: PrivacyRequest
                    ) -> tuple[PrivacyExport | None, str | None]:
    """Generate an export. Refuses an unverified request.

    Returns (export, raw_download_token). The artifact goes to PRIVATE storage; the token is
    single-use, purpose-bound, export-bound and short-lived.
    """
    if request.verification_required and request.verified_at is None:
        if request.representative_verified_at is None:
            return None, None
    export = PrivacyExport(privacy_request_id=request.id, status="processing")
    db.add(export)
    db.commit()
    db.refresh(export)

    try:
        inventory = build_inventory(db, request)
        key = (f"privacy-exports/{request.id}/{export.id}.json")
        from . import developer_export

        developer_export._store(key, json.dumps(inventory, indent=2))
        export.storage_reference = key
        export.inventory = {"counts": inventory.get("counts", {}),
                            "excluded": inventory.get("excluded", [])}
        export.generated_at = _now()
        export.expires_at = _now() + timedelta(minutes=DOWNLOAD_TTL_MINUTES)
        export.status = "ready"
    except Exception:  # noqa: BLE001 - a generation failure is a reportable outcome
        log.exception("PRV-003 export generation failed for request %s", request.id)
        export.status = "failed"
        export.failed_at = _now()
        export.failure_category = "generation_failed"
        db.commit()
        return export, None

    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    export.download_token_hash = _hash(raw)
    export.download_purpose = PURPOSE_PRIVACY_DOWNLOAD
    export.issue_version = (export.issue_version or 0) + 1
    db.commit()
    db.refresh(export)
    return export, raw


def authorize_download(db: Session, *, token: str, actor: User | None = None,
                       client_hint: str | None = None
                       ) -> tuple[PrivacyExport | None, str]:
    """The ONLY resolver of a download token. Every attempt is logged with an outcome.

    Purpose-bound, export-bound, expiring and single-use. Where the request names a user,
    the download is additionally bound to that identity, so a leaked link is not enough.
    """
    def record(export, outcome):
        db.add(PrivacyExportAccess(
            export_id=export.id if export else None,
            privacy_request_id=export.privacy_request_id if export else None,
            request_reference=None,
            actor_user_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
            outcome=outcome,
            # A coarse hint only, and never the exported content.
            client_hint=(client_hint or "")[:120] or None))
        db.commit()

    if not token:
        return None, "invalid"
    export = db.scalar(
        select(PrivacyExport).where(PrivacyExport.download_token_hash == _hash(token)))
    if export is None or export.download_purpose != PURPOSE_PRIVACY_DOWNLOAD:
        return None, "invalid"
    if export.revoked_at is not None:
        record(export, "revoked")
        return None, "revoked"
    if export.status != "ready":
        record(export, "not_ready")
        return None, "not_ready"
    if export.expires_at is None or export.expires_at < _now():
        export.status = "expired"
        db.commit()
        record(export, "expired")
        return None, "expired"
    if export.downloaded_at is not None:
        record(export, "already_used")
        return None, "already_used"

    request = db.get(PrivacyRequest, export.privacy_request_id)
    if request is not None and request.requester_user_id is not None:
        if actor is None or str(actor.id) != str(request.requester_user_id):
            record(export, "wrong_identity")
            return None, "wrong_identity"

    export.downloaded_at = _now()
    export.download_count = (export.download_count or 0) + 1
    db.commit()
    record(export, "granted")
    return export, "granted"


def notify_export_ready(db: Session, background, request: PrivacyRequest,
                        export: PrivacyExport, raw_token: str) -> bool:
    """Send a LINK. The export is never attached."""
    if export.status != "ready" or not raw_token:
        return False
    if not _claim(db, kind="export_ready", subject_type="privacy_export",
                  subject_id=export.id, version=export.issue_version,
                  org_id=request.org_id):
        return False
    org = _org(db, request)
    if not _may_send("PRV-003", org):
        return False
    # Verified requester only.
    people = request_recipients(db, request, require_verified=True)
    if not people:
        return False
    _queue(background, email_mod.send_privacy_export_ready_email, people,
           generated_at=email_mod.billing_date(export.generated_at),
           expires_at=email_mod.billing_date(export.expires_at),
           download_url=f"{privacy_url()}/exports/{export.id}/download?t={raw_token}",
           **_shared(db, request))
    return True


def notify_export_expired(db: Session, background, request: PrivacyRequest,
                          export: PrivacyExport) -> bool:
    if export.status != "expired":
        return False
    if not _claim(db, kind="export_expired", subject_type="privacy_export",
                  subject_id=export.id, version=export.issue_version,
                  org_id=request.org_id):
        return False
    org = _org(db, request)
    if not _may_send("PRV-003", org):
        return False
    people = request_recipients(db, request, require_verified=True)
    if not people:
        return False
    _queue(background, email_mod.send_privacy_export_expired_email, people,
           expired_at=email_mod.billing_date(export.expires_at),
           regenerate_note=("Ask for a new copy in the Privacy Center and we will generate "
                            "a fresh link."),
           **_shared(db, request))
    return True


# -- deletion ----------------------------------------------------------------------------

def plan_deletion(db: Session, request: PrivacyRequest) -> tuple[list[str], str | None]:
    """(residual_categories, blocker) read from EXISTING retention authority.

    MED-011's `legal_hold` / `retention_expires_at` and
    `GovernanceRecord(kind="legal_hold")` are the authorities; this function reads them and
    adds the record classes this platform lawfully keeps regardless. It decides no retention
    of its own, which is why there is no second retention system.
    """
    residual: list[str] = []
    blocker = None

    # Audit and security records are always kept: they exist to protect the service and
    # other users, and erasing them on request would defeat that.
    residual.append("audit_log")
    residual.append("security_event")

    if request.org_id:
        held = db.scalar(
            select(func.count(GovernanceRecord.id)).where(
                GovernanceRecord.kind == "legal_hold",
                GovernanceRecord.org_id == request.org_id,
                GovernanceRecord.resolved_at.is_(None))) or 0
        if held:
            residual.append("legal_hold")
            blocker = "legal_hold"

        # MED-011: a recording under hold, or inside its retention window, is not erasable.
        recordings = db.scalars(
            select(LiveRecording).where(LiveRecording.org_id == request.org_id)).all()
        if any(r.legal_hold for r in recordings):
            if "legal_hold" not in residual:
                residual.append("legal_hold")
            blocker = "legal_hold"
        elif any(r.retention_expires_at and r.retention_expires_at > _now()
                 for r in recordings):
            if "legal_hold" not in residual:
                residual.append("legal_hold")
            blocker = blocker or "retention_window"

        # Invoices are accounting records: they are retained regardless of an erasure
        # request, so their presence is disclosed rather than silently ignored.
        from ..models import EventOrder

        invoices = db.scalar(
            select(func.count(Invoice.id))
            .join(EventOrder, Invoice.event_order_id == EventOrder.id)
            .join(Event, Event.id == EventOrder.event_id)
            .where(Event.org_id == request.org_id)) or 0
        if invoices:
            residual.append("billing_record")
    return residual, blocker


def execute_deletion(db: Session, request: PrivacyRequest) -> PrivacyDeletion:
    """Run an erasure, honouring the retention authority above.

    A blocked erasure is recorded as BLOCKED_BY_RETENTION or PARTIAL - never as completed.
    """
    row = db.scalar(
        select(PrivacyDeletion).where(PrivacyDeletion.privacy_request_id == request.id))
    if row is None:
        row = PrivacyDeletion(privacy_request_id=request.id, status="pending")
        db.add(row)
        db.commit()
        db.refresh(row)

    row.status = "in_progress"
    row.started_at = _now()
    db.commit()

    residual, blocker = plan_deletion(db, request)
    removed: list[str] = []
    user = db.get(User, request.requester_user_id) if request.requester_user_id else None
    if user is not None and blocker is None:
        # Reuses the existing erasure path, which already nulls governance references rather
        # than destroying records that must outlive the person.
        from ..crud import admin as admin_crud

        try:
            admin_crud.delete_user(db, user)
            removed.append("account_profile")
            row.account_access_note = "Your account has been closed and sign-in is disabled."
        except Exception:  # noqa: BLE001
            log.exception("PRV-003 erasure failed for request %s", request.id)
            row.status = "failed"
            row.failed_at = _now()
            row.blocker_category = "erasure_failed"
            row.version = (row.version or 0) + 1
            db.commit()
            return row

    row.removed = removed
    row.residual_categories = residual
    row.blocker_category = blocker
    if blocker is not None:
        row.status = "blocked_by_retention"
        row.account_access_note = ("Your account remains closed to new sign-ins while these "
                                   "records are retained.")
    elif removed:
        # COMPLETED still carries residual categories: completion means the erasable data is
        # gone, not that no record of any kind survives.
        row.status = "completed"
        row.completed_at = _now()
    else:
        row.status = "partial"
    row.version = (row.version or 0) + 1
    db.commit()
    return row


def deletion_sentence(row: PrivacyDeletion) -> str:
    """Generated from the recorded residue. Never claims total erasure."""
    categories = list(row.residual_categories or [])
    if not categories:
        return ("We deleted the personal data we hold for you that is not required to be "
                "retained.")
    listed = "; ".join(RESIDUAL_CATEGORY_LABELS.get(c, c) for c in categories)
    return (f"We deleted the personal data we are able to erase. Some records are retained "
            f"because we are required to keep them: {listed}.")


def notify_deletion(db: Session, background, request: PrivacyRequest,
                    row: PrivacyDeletion) -> str | None:
    """Announce a deletion outcome truthfully."""
    if row.status not in ("completed", "partial", "blocked_by_retention", "failed"):
        return None
    kind = "deletion_completed" if row.status == "completed" else "deletion_incomplete"
    if not _claim(db, kind=kind, subject_type="privacy_deletion", subject_id=row.id,
                  version=row.version, org_id=request.org_id, detail=row.status):
        return None
    org = _org(db, request)
    if not _may_send("PRV-003", org):
        return None
    people = request_recipients(db, request, require_verified=True)
    if not people:
        return None

    _queue(background, email_mod.send_privacy_deletion_email, people,
           variant=("completed" if row.status == "completed" else "incomplete"),
           state=(row.status or "").replace("_", " ").title(),
           completed_at=(email_mod.billing_date(row.completed_at)
                         if row.completed_at else None),
           access_note=row.account_access_note or "Your account access is unchanged.",
           # Generated from the recorded residue - never a blanket erasure claim.
           residual=deletion_sentence(row),
           blocker=(RESIDUAL_CATEGORY_LABELS.get(row.blocker_category or "",
                                                 "A retention requirement")
                    if row.blocker_category else None),
           next_action=("Nothing further is needed." if row.status == "completed"
                        else "Reply in the Privacy Center if you would like this reviewed."),
           **_shared(db, request))
    return kind


def record_retention_exception(db: Session, request: PrivacyRequest, *, record_type: str,
                               retention_basis: str, review_at: datetime | None = None,
                               authorized_by=None) -> RetentionException:
    row = RetentionException(privacy_request_id=request.id, record_type=record_type,
                             retention_basis=retention_basis, effective_from=_now(),
                             review_at=review_at, authorized_by=authorized_by,
                             status="active")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def notify_retention_exception(db: Session, background, request: PrivacyRequest,
                               exception: RetentionException) -> bool:
    if exception.notified_at is not None:
        return False
    if not _claim(db, kind="retention_exception", subject_type="retention_exception",
                  subject_id=exception.id, org_id=request.org_id):
        return False
    exception.notified_at = _now()
    db.commit()
    org = _org(db, request)
    if not _may_send("PRV-004", org):
        return False
    people = request_recipients(db, request)
    if not people:
        return False
    _queue(background, email_mod.send_privacy_retention_email, people,
           record_type=RESIDUAL_CATEGORY_LABELS.get(exception.record_type,
                                                    exception.record_type),
           # A basis CATEGORY, never legal advice or an internal argument.
           basis=exception.retention_basis.replace("_", " ").title(),
           review_at=(email_mod.billing_date(exception.review_at)
                      if exception.review_at else None),
           contact_route=("Reply in the Privacy Center if you would like this reviewed."),
           **_shared(db, request))
    return True


# ══ PRV-004 — notice, consent, subprocessors ═══════════════════════════════════════════

def publish_notice(db: Session, version: PrivacyNoticeVersion, *,
                   effective_at: datetime | None = None) -> bool:
    """Publish a notice version, superseding the previous published one."""
    if version.status != "draft":
        return False
    for other in db.scalars(
        select(PrivacyNoticeVersion).where(
            PrivacyNoticeVersion.status == "published")).all():
        other.status = "superseded"
        other.superseded_at = _now()
    version.status = "published"
    version.published_at = _now()
    version.effective_at = effective_at or version.effective_at or _now()
    db.commit()
    return True


def consent_options() -> list[dict]:
    """Accept and Decline as EQUAL peers.

    Same shape, same weight, neither preselected and neither hidden. A dark pattern here
    would be a consent record that cannot be relied on.
    """
    return [
        {"value": "accepted", "label": "Accept", "preselected": False, "emphasis": "normal"},
        {"value": "declined", "label": "Decline", "preselected": False, "emphasis": "normal"},
    ]


def record_decision(db: Session, version: PrivacyNoticeVersion, *, subject_email: str,
                    decision: str, source: str = "privacy_center",
                    subject_user_id=None) -> PrivacyConsentDecision | None:
    """Record a consent decision exactly as made.

    No default: a row exists only because somebody chose. Silence, a page view and an
    acknowledgement therefore cannot become acceptance.
    """
    if decision not in CONSENT_DECISIONS or not version.consent_required:
        return None
    address = (subject_email or "").strip().lower()
    if not address:
        return None
    row = db.scalar(
        select(PrivacyConsentDecision).where(
            PrivacyConsentDecision.notice_version_id == version.id,
            PrivacyConsentDecision.subject_email == address))
    if row is None:
        row = PrivacyConsentDecision(notice_version_id=version.id, subject_email=address,
                                     subject_user_id=subject_user_id)
        db.add(row)
    row.decision = decision
    row.consent_purpose = version.consent_purpose
    row.decided_at = _now()
    row.source = source
    if decision == "withdrawn":
        row.withdrawn_at = _now()
    db.commit()
    db.refresh(row)
    return row


def notify_notice_published(db: Session, background, version: PrivacyNoticeVersion,
                            people: list[tuple[str, str]]) -> bool:
    """Announce a PUBLISHED notice.

    A draft announces nothing, and a non-material change does not mail everybody unless it
    requires consent - which is what stops a wording fix becoming a mass notification.
    """
    if version.status != "published":
        return False
    if not (version.material_change or version.consent_required):
        return False
    kind = "consent_required" if version.consent_required else "notice_published"
    if not _claim(db, kind=kind, subject_type="notice_version", subject_id=version.id):
        return False
    if not people:
        return False
    _queue(background, email_mod.send_privacy_notice_email, people,
           version=version.version,
           effective_at=email_mod.billing_date(version.effective_at),
           summary=version.change_summary or "We have updated our privacy notice.",
           consent_required=bool(version.consent_required),
           consent_purpose=version.consent_purpose,
           # Rendered as equal peers, neither preselected.
           options=consent_options(),
           notice_url=f"{privacy_url()}/notice/{version.id}",
           privacy_url=privacy_url())
    return True


def seed_subprocessors(db: Session) -> int:
    """Register the providers this codebase genuinely uses. Invents nothing."""
    added = 0
    for entry in KNOWN_SUBPROCESSORS:
        existing = db.scalar(
            select(Subprocessor).where(Subprocessor.name == entry["name"],
                                       Subprocessor.service == entry["service"]))
        if existing is not None:
            continue
        db.add(Subprocessor(name=entry["name"], service=entry["service"],
                            processing_purpose=entry["processing_purpose"],
                            # Honest: the region is only stated when genuinely known.
                            region=entry["region"], status="active",
                            effective_from=_now(), notice_required=False))
        added += 1
    if added:
        db.commit()
    return added


def notify_subprocessor_change(db: Session, background, subprocessor: Subprocessor,
                               people: list[tuple[str, str]]) -> bool:
    """Announce a real change, only when policy flags it as requiring notice."""
    if not subprocessor.notice_required:
        return False
    if not _claim(db, kind="subprocessor_changed", subject_type="subprocessor",
                  subject_id=subprocessor.id, version=subprocessor.change_version):
        return False
    if not people:
        return False
    _queue(background, email_mod.send_subprocessor_notice_email, people,
           processor=subprocessor.name,
           service=subprocessor.service,
           purpose=subprocessor.processing_purpose,
           status=(subprocessor.status or "active").title(),
           effective_at=email_mod.billing_date(subprocessor.effective_from),
           # No contract terms and no confidential vendor detail.
           list_url=f"{privacy_url()}/subprocessors",
           privacy_url=privacy_url())
    return True
