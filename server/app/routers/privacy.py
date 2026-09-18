"""The public HTTP surface for the privacy domain (PRV-001 -> PRV-004).

**Why this router exists.** The service layer (services/privacy_comms.py) is complete and
tested, but it had no HTTP surface at all — and every privacy email links a customer to
/organization/privacy, a page that did not exist in the client and had no API to back it.
A privacy right a customer cannot exercise from anywhere is not a right; this file is the
exercise surface.

**Routes, and why each is where it is:**

  POST /privacy/requests                intake (public, rate-limited)
  POST /privacy/requests/verify         verification redemption (public, unauthenticated
                                        by design — requesters frequently have no account)
  GET  /privacy/requests/{reference}    status (unauthenticated, reference IS the credential)
  GET  /privacy/exports/{id}/download   token-gated export download (public)
  GET  /privacy/notice                  the published privacy notice (public)
  POST /privacy/notice/consent          consent decision (public, tied to the notice)
  GET  /privacy/subprocessors           the real processor list (public)

**NOT here, deliberately:**

  * No operator routes. Approval, clarification, extension, deletion execution and
    retention exceptions stay staff-side operations on the service layer; a public router
    that can advance a request's own status is a liability, and PRV-002's status emails
    are driven by staff actions, not by the requester polling.
  * No `DELETE /privacy/requests/{id}`. A privacy request is a durable record; making it
    deletable over an unauthenticated endpoint would defeat the audit it exists for.

**The reference as bearer credential.** Status is readable with the reference alone.
That is a deliberate, bounded disclosure: the reference is a customer-safe, immutable,
quotable identifier that every email already carries, and the response discloses only
what those emails disclose — type, status, deadline note. The request's free-text
`details`, its internal decision reasoning, and its `requester_email` are read by NO
success path here. (A wrong reference is indistinguishable from no request at all.)

**Rate limiting** mirrors contact.py: an unauthenticated intake surface gets a per-IP
budget, and the status endpoint gets its own because a guessable-string endpoint must
not be scannable.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    PrivacyNoticeVersion,
    PrivacyRequest,
    Subprocessor,
)
from ..ratelimit import rate_limit
from ..schemas.privacy import (
    ExportDownloadOut,
    PrivacyConsentOut,
    PrivacyDecisionIn,
    PrivacyRequestIn,
    PrivacyRequestOut,
    PrivacyVerifyIn,
    PublicNoticeOut,
    SubprocessorOut,
)
from ..security import get_current_user_optional
from ..services import privacy_comms as prv

log = logging.getLogger(__name__)

router = APIRouter(prefix="/privacy", tags=["privacy"])

# Per-IP budgets on unauthenticated surfaces, matching contact.py's approach. Intake and
# verification both end in outbound mail, so each carries its own budget; status gets one
# because a reference-shaped string endpoint must not be a scanning target.
_INTAKE_LIMIT = rate_limit("privacy_intake", limit=5, window=3600.0)
_VERIFY_LIMIT = rate_limit("privacy_verify", limit=10, window=3600.0)
_STATUS_LIMIT = rate_limit("privacy_status", limit=30, window=3600.0)
_CONSENT_LIMIT = rate_limit("privacy_consent", limit=10, window=3600.0)

# The single honest answer for every failed lookup, verification and download. A distinct
# "expired" or "wrong request" for an unauthenticated caller would let someone holding a
# dead link learn that a request exists and was verified.
_NO_REQUEST = "No privacy request matches that reference."


def _status_out(db: Session, request: PrivacyRequest) -> PrivacyRequestOut:
    return PrivacyRequestOut(
        reference=request.request_reference,
        request_type=request.request_type,
        status=request.status,
        deadline_note=prv.deadline_note(request),
    )


@router.post("/requests", response_model=PrivacyRequestOut,
             status_code=status.HTTP_202_ACCEPTED, dependencies=[_INTAKE_LIMIT])
def submit_request(data: PrivacyRequestIn, background: BackgroundTasks,
                   request: Request,
                   user=Depends(get_current_user_optional),
                   db: Session = Depends(get_db)) -> PrivacyRequestOut:
    """Accept a privacy request (PRV-001).

    202, not 200: the request is logged and its acknowledgement is queued, but delivery is
    the mail provider's problem — contact.py's reasoning exactly. A signed-in caller's
    identity is recorded when present (`requester_user_id`), which is what binds a later
    export download to them; it is NOT treated as verification, because exports and
    deletions always need a fresh purpose-bound verification (privacy_comms.
    verification_needed).
    """
    # Idempotent-ish intake: a duplicate submission of the same type from the same address
    # while an earlier one is still open returns the existing request rather than opening a
    # second obligation. This also keeps a double-click from burning the rate budget on a
    # second acknowledgement email.
    existing = db.scalar(
        select(PrivacyRequest).where(
            PrivacyRequest.requester_email == str(data.email).strip().lower(),
            PrivacyRequest.request_type == data.request_type,
            PrivacyRequest.status.in_(
                ("received", "verification_required", "verified", "in_progress",
                 "clarification_required", "extended"))))
    if existing is not None:
        return _status_out(db, existing)

    # Normalized HERE, once, so the dedup lookup above and the row it opens key on the
    # same address by construction rather than by the service's own convention.
    address = str(data.email).strip().lower()
    row, raw_token = prv.open_request(
        db, requester_email=address, request_type=data.request_type,
        requester_user_id=user.id if user else None,
        details=(data.details or "").strip() or None)
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The request could not be accepted.")

    if raw_token:
        prv.notify_verification_required(db, background, row, raw_token)
    # A request that needs no verification is actionable on arrival, so its
    # acknowledgement is all that is owed.
    prv.notify_received(db, background, row)

    # Requested by nobody: the reference still exists in the audit trail via the
    # GovernanceRecord opened by open_request().
    log.info("privacy request accepted ref=%s type=%s ip=%s",
             row.request_reference, row.request_type,
             request.client.host if request.client else "-")
    return _status_out(db, row)


@router.post("/requests/verify", response_model=PrivacyRequestOut,
             dependencies=[_VERIFY_LIMIT])
def verify_request(data: PrivacyVerifyIn, db: Session = Depends(get_db)) -> PrivacyRequestOut:
    """Redeem a verification token (PRV-001).

    The verify URL in the email points at the Privacy Center, which POSTs the token here
    and renders the outcome. Purpose is fixed by the route — the body cannot mint a
    different token type.
    """
    row, outcome = prv.verify_request(db, token=data.token)
    if row is None:
        # One answer for invalid, expired, superseded and replayed alike. Which of those
        # it was is a fact about the token, and the holder already knows it failed.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _NO_REQUEST)
    return _status_out(db, row)


@router.get("/requests/{reference}", response_model=PrivacyRequestOut,
            dependencies=[_STATUS_LIMIT])
def request_status(reference: str, db: Session = Depends(get_db)) -> PrivacyRequestOut:
    """Status of one request (PRV-002), readable with the reference alone.

    The reference is what every email displays, so it is what the Privacy Center asks for.
    The response is the same identity block the emails carry — type, status, deadline
    note — and nothing else. `details`, the requester's address and any decision text are
    not exposed by any success path.
    """
    row = db.scalar(select(PrivacyRequest).where(
        PrivacyRequest.request_reference == reference.strip().upper()))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_REQUEST)
    return _status_out(db, row)


@router.get("/exports/{export_id}/download", response_model=None)
def export_download(export_id: str,
                    request: Request,
                    t: str = Query(min_length=10, max_length=200),
                    user=Depends(get_current_user_optional),
                    db: Session = Depends(get_db)) -> ExportDownloadOut | Response:
    """Resolve an export link (PRV-003).

    The email carries /organization/privacy/exports/{id}/download?t={token}; the Privacy
    Center sends that token here. authorize_download is the ONLY resolver of the token —
    single-use, purpose-bound, expiring, identity-bound when the request names a user —
    and every attempt is logged with an outcome. The artifact itself comes back as a
    fresh short-lived signed URL via the same storage path the developer export uses;
    it is never proxied through this process.

    A failed lookup is 404 whatever the reason, for the same _NO_REQUEST reason as
    verify: the outcome details are already in PrivacyExportAccess, which is the
    audit — not something a link-holder is told.
    """
    export, outcome = prv.authorize_download(
        db, token=t, actor=user,
        client_hint=(request.headers.get("user-agent", "") or "")[:120])
    if export is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "This download link is not valid or has expired.")
    key = export.storage_reference
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "This download link is not valid or has expired.")

    from ..services import developer_export as dev_export
    from ..services import livekit as livekit_service

    # GCS configured: a fresh short-lived signed URL, minted per request and never
    # persisted — deliveries.py's posture exactly. Not configured (local dev): serve the
    # stored JSON body from the private fallback directory, the same one
    # developer_export._store writes to. The artifact is the requester's own inventory
    # document, and authorize_download has just spent its single use getting here.
    if livekit_service.gcs_configured():
        url = livekit_service.signed_url(key, expires_minutes=10)
        if url is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Couldn't prepare the download right now — try again shortly.")
        return ExportDownloadOut(download_url=url)

    import pathlib

    path = pathlib.Path("private_exports") / key
    content = path.read_text(encoding="utf-8") if path.exists() else None
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "The export object is no longer stored.")
    return Response(
        content=content, media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="privacy-export-{export_id}.json"'})


@router.get("/notice", response_model=PublicNoticeOut)
def current_notice(db: Session = Depends(get_db)) -> PublicNoticeOut:
    """The published privacy notice (PRV-004). One row — the current one."""
    row = db.scalar(select(PrivacyNoticeVersion).where(
        PrivacyNoticeVersion.status == "published").order_by(
        PrivacyNoticeVersion.published_at.desc()))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No privacy notice has been published yet.")
    return row


@router.post("/notice/consent", response_model=PrivacyConsentOut,
             dependencies=[_CONSENT_LIMIT])
def record_consent(data: PrivacyDecisionIn,
                   user=Depends(get_current_user_optional),
                   db: Session = Depends(get_db)) -> PrivacyConsentOut:
    """Record a consent decision on a published, consent-required notice (PRV-004).

    record_decision is the store: no default, both decisions equal peers, and a decision
    is only ever recorded against a version whose consent_required is True. A draft or a
    plain-notice version accepts no decision and this route answers 404 for it — a
    consent box must not exist where no consent is owed.
    """
    version = db.get(PrivacyNoticeVersion, data.version_id)
    if version is None or version.status != "published" or not version.consent_required:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "That notice does not accept a consent decision.")
    row = prv.record_decision(db, version, subject_email=str(data.email),
                              decision=data.decision,
                              subject_user_id=user.id if user else None,
                              source="privacy_center")
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                             "That decision could not be recorded.")
    return PrivacyConsentOut(decision=row.decision, decided_at=row.decided_at,
                             source=row.source or "privacy_center")


@router.get("/subprocessors", response_model=list[SubprocessorOut])
def list_subprocessors(db: Session = Depends(get_db)) -> list[SubprocessorOut]:
    """The processor list (PRV-004).

    Seeded from KNOWN_SUBPROCESSORS — providers this codebase genuinely integrates. The
    /subprocessors URL in a subprocessor-change email lands here, so the list is served
    whether or not anyone remembered to seed it: an empty list is an honest answer, and
    seeding happens at startup so the honest answer is also the complete one.
    """
    try:
        prv.seed_subprocessors(db)
    except Exception:  # noqa: BLE001 — a seeding failure must not 500 a public read
        log.exception("subprocessor seeding failed; serving what is stored")
    rows = db.scalars(select(Subprocessor).where(Subprocessor.status != "removed")
                      .order_by(Subprocessor.name)).all()
    return rows
