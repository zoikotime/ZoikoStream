"""Public Trust Center, security reporting, and marketing preferences.

ZST-EC-001 TRU-001 -> TRU-003 and MKT-000 -> MKT-004, public halves.

Unauthenticated by design, like routers/status.py and routers/contact.py: a security
researcher has no account here, a vendor-security reviewer at a prospect does not either,
and somebody unsubscribing from marketing must not have to log in to do it.

── WHAT IS *NOT* HERE ────────────────────────────────────────────────────────────────────
Publication and approval. Drafting an advisory, approving evidence access, triaging a
report, approving a digest or a feature announcement are all operator actions and live on
the super-admin router. This module only:

    reads PUBLISHED advisories (customer-safe projection, no internal fields)
    accepts evidence REQUESTS and serves APPROVED, bound, expiring downloads
    accepts vulnerability reports and serves the reporter their own safe status
    manages one address's marketing consent

Reporter identity is never served from this router at any path - `public_projection()`
omits it, and the only reader is `vuln_disclosure.reporter_identity()` behind a role.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi import status as http
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    EVIDENCE_PURPOSES,
    EVIDENCE_SCOPES,
    GUIDE_LABELS,
    MARKETING_TOPICS,
    SEVERITY_LABELS,
    TOPIC_LABELS,
    COMPONENT_LABELS,
    MarketingWebinar,
    SecurityAdvisory,
    TrustEvidenceDocument,
    TrustEvidenceRequest,
)
from ..ratelimit import client_ip, rate_limit
from ..schemas.trust import (
    EvidenceRequestIn,
    GuideRequestIn,
    MarketingPreferencesIn,
    MarketingSubscribeIn,
    MarketingTokenIn,
    VulnerabilityReportIn,
    WebinarRegistrationIn,
)
from ..services import marketing as mkt
from ..services import trust_center as tc
from ..services import vuln_disclosure as vd

router = APIRouter(prefix="/trust", tags=["trust"])

# Public and unauthenticated, so every write path is IP-limited. An evidence request is
# cheap for us and expensive to review, and a report form is a spam target.
_EVIDENCE_LIMIT = rate_limit("trust_evidence", limit=5, window=600.0)
_REPORT_LIMIT = rate_limit("trust_report", limit=5, window=600.0)
_MARKETING_LIMIT = rate_limit("marketing_subscribe", limit=6, window=600.0)


def _advisory_out(advisory: SecurityAdvisory, db: Session) -> dict:
    """The PUBLISHED projection.

    `internal_incident_id` and `vulnerability_report_id` are deliberately absent: they are
    operator correlation keys, and the second would tell a reader that an advisory came from
    a researcher's report. `cvss_vector` appears only if a human recorded a real one.
    """
    return {
        "reference": advisory.public_reference,
        "title": advisory.title,
        "severity": advisory.severity,
        "severity_label": SEVERITY_LABELS.get(advisory.severity, "Under assessment"),
        "status": advisory.status,
        "summary": advisory.summary,
        "customer_impact": advisory.customer_impact,
        "components": [COMPONENT_LABELS.get(c, c)
                       for c in (advisory.affected_components or [])],
        "affected_versions": advisory.affected_versions,
        "affected_scope_note": advisory.affected_scope_note,
        "cvss_vector": advisory.cvss_vector,
        "immediate_mitigation": advisory.immediate_mitigation,
        "workaround_available": advisory.workaround_available,
        "workaround_summary": advisory.workaround_summary,
        "fixed_version": advisory.fixed_version,
        "remediation_steps": advisory.remediation_steps,
        "remediation_available_at": tc.as_utc(advisory.remediation_available_at),
        "remediation_deadline": tc.as_utc(advisory.remediation_deadline),
        "action_mandatory": advisory.action_mandatory,
        "published_at": tc.as_utc(advisory.published_at),
        "closed_at": tc.as_utc(advisory.closed_at),
        "closure_note": advisory.closure_note,
        "version": advisory.version,
        # The append-only published history, so a reader can see what the advisory said
        # when they acted on it and what changed since.
        "history": [
            {"version": v.version, "type": v.version_type, "status": v.status,
             "severity": v.severity, "summary": v.summary,
             "customer_impact": v.customer_impact,
             "affected_versions": v.affected_versions,
             "change_summary": v.change_summary,
             "changed_fields": [tc.FIELD_LABELS.get(f, f) for f in (v.changed_fields or [])],
             "published_at": tc.as_utc(v.published_at)}
            for v in tc.published_versions(db, advisory)
        ],
    }


def _document_out(doc: TrustEvidenceDocument) -> dict:
    """A document's METADATA. `storage_reference` never crosses this boundary."""
    return {
        "id": str(doc.id),
        "title": doc.title,
        "document_type": doc.document_type,
        "version": doc.version,
        "classification": doc.classification,
        # Says plainly whether a request is needed, so nobody expects a download link.
        "requires_approval": doc.classification != "public",
        "allowed_purposes": list(doc.allowed_purposes or []),
        "allowed_scopes": list(doc.allowed_scopes or []),
        "available_from": tc.as_utc(doc.available_from),
    }


@router.get("")
def trust_center(db: Session = Depends(get_db)):
    """Everything the public Trust Center renders. Published records only."""
    from .. import email as email_mod

    advisories = db.scalars(
        select(SecurityAdvisory)
        .where(SecurityAdvisory.status != "draft",
               SecurityAdvisory.published_at.isnot(None))
        .order_by(SecurityAdvisory.published_at.desc()).limit(50)).all()
    documents = db.scalars(
        select(TrustEvidenceDocument)
        .where(TrustEvidenceDocument.status == "available")
        .order_by(TrustEvidenceDocument.document_type)).all()
    return {
        "advisories": [_advisory_out(a, db) for a in advisories],
        "documents": [_document_out(d) for d in documents],
        "purposes": list(EVIDENCE_PURPOSES),
        "scopes": list(EVIDENCE_SCOPES),
        "vulnerability_categories": list(vd.VULN_CATEGORIES),
        # Stated up front, as facts rather than omissions, so a researcher is not left
        # inferring a bounty or a credit programme that does not exist here.
        "disclosure_policy": {
            "bounty": False,
            "public_credit": False,
            "coordinated_disclosure_policy": False,
            "note": email_mod.VULN_NO_PROMISE,
            "safe_handling": email_mod.VULN_SAFE_HANDLING,
        },
    }


@router.get("/advisories/{reference}")
def read_advisory(reference: str, db: Session = Depends(get_db)):
    advisory = db.scalar(select(SecurityAdvisory).where(
        SecurityAdvisory.public_reference == reference,
        SecurityAdvisory.status != "draft",
        SecurityAdvisory.published_at.isnot(None)))
    if advisory is None:
        # A draft is indistinguishable from a nonexistent advisory here, which is the point.
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Advisory not found")
    return _advisory_out(advisory, db)


# ── TRU-002 evidence request and download ───────────────────────────────────────────────

@router.post("/evidence/requests", status_code=http.HTTP_202_ACCEPTED,
             dependencies=[_EVIDENCE_LIMIT])
def request_evidence(data: EvidenceRequestIn, background: BackgroundTasks,
                     db: Session = Depends(get_db)):
    """File a request for one document, one purpose, one scope.

    Never returns a download link. Approval is a human decision and arrives by email.
    """
    try:
        document = db.get(TrustEvidenceDocument, uuid.UUID(data.document_id))
    except (ValueError, AttributeError):
        document = None
    if document is None or document.status != "available":
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Document not found")

    request = tc.create_request(
        db, requester_email=data.requester_email, document=document,
        purpose=data.purpose, scope=data.scope, requester_name=data.requester_name,
        company_name=data.company_name, purpose_note=data.purpose_note,
        claimed_basis=data.qualification_basis)
    if request is None:
        raise HTTPException(
            http.HTTP_422_UNPROCESSABLE_ENTITY,
            "This document cannot be requested for that purpose and scope")
    tc.notify_request_received(db, background, request)
    return {"reference": request.reference, "status": request.status}


@router.get("/evidence/{reference}")
def download_evidence(reference: str, t: str, request: Request,
                      db: Session = Depends(get_db)):
    """Serve an approved document through the full access boundary.

    Every dimension is checked in `tc.authorize_access` - token, recipient, document,
    purpose, scope, expiry, revocation - and BOTH outcomes are logged. The bytes stream from
    private storage; there is no public URL anywhere in this flow.
    """
    approved, outcome = tc.authorize_access(
        db, token=t, client=(request.headers.get("user-agent") or "")[:160],
        ip=client_ip(request))
    if approved is None or outcome != "authorized":
        # One message for every failure mode: distinguishing "expired" from "wrong document"
        # to an unauthenticated caller would leak the state of somebody else's request.
        raise HTTPException(http.HTTP_403_FORBIDDEN,
                            "This access link is not valid, or it has expired")
    if approved.reference != reference:
        tc._log_access(db, approved, "reference_mismatch")  # noqa: SLF001
        raise HTTPException(http.HTTP_403_FORBIDDEN,
                            "This access link is not valid, or it has expired")

    document = db.get(TrustEvidenceDocument, approved.document_id)
    content = tc.load_document(document) if document else None
    if content is None:
        raise HTTPException(http.HTTP_503_SERVICE_UNAVAILABLE,
                            "This document is temporarily unavailable")
    filename = f"{document.document_type}-{document.version}"
    return Response(
        content=content, media_type=document.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 # Confidential evidence must not sit in a shared cache.
                 "Cache-Control": "no-store, private"})


# ── TRU-003 vulnerability disclosure ────────────────────────────────────────────────────

@router.post("/security/reports", status_code=http.HTTP_202_ACCEPTED,
             dependencies=[_REPORT_LIMIT])
def submit_vulnerability_report(data: VulnerabilityReportIn, background: BackgroundTasks,
                                db: Session = Depends(get_db)):
    """The protected reporting channel.

    Deliberately NOT a support ticket: a ticket is readable by org admins and support staff,
    and a vulnerability report must not be. Returns the reference and the reporter's own
    portal handle, and promises nothing about bounty, credit, validity or timing.
    """
    if vd.looks_like_a_secret(data.description) or vd.looks_like_a_secret(data.reproduction):
        # Refuse rather than store. We never need a credential to reproduce an issue, and
        # accepting one would put it in our register and our backups.
        raise HTTPException(
            http.HTTP_422_UNPROCESSABLE_ENTITY,
            "Please remove any passwords, API keys or private keys from your report - we "
            "never need them to reproduce an issue.")

    report, token = vd.submit(
        db, reporter_email=data.reporter_email, title=data.title, category=data.category,
        description=data.description, reproduction=data.reproduction,
        affected_service=data.affected_service, reporter_name=data.reporter_name,
        identity_visibility=data.identity_visibility, evidence=data.evidence)
    if report is None:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A valid report requires a title, category and description")
    vd.notify_received(db, background, report, token)
    return {"reference": report.reference, "status": report.status,
            "portal_token": token,
            "bounty": False, "public_credit": False}


@router.get("/security/reports/{reference}")
def read_own_report(reference: str, t: str, db: Session = Depends(get_db)):
    """The reporter portal. A researcher's read of their OWN report's safe view.

    Serves `public_projection()`, which has no identity field, no internal analysis and no
    evidence. The portal handle grants this and nothing else in the platform.
    """
    report = vd.resolve_portal(db, t)
    if report is None or report.reference != reference:
        raise HTTPException(http.HTTP_403_FORBIDDEN,
                            "This link is not valid, or it has expired")
    return {
        **vd.public_projection(report),
        "history": [{"version": u.version, "stage": u.stage, "body": u.body,
                     "published_at": vd.as_utc(u.published_at)}
                    for u in vd.reporter_history(db, report)],
        "coordinated_disclosure_recorded": vd.coordination_recorded(report),
    }


# ── MKT-000 marketing consent ───────────────────────────────────────────────────────────

@router.post("/marketing/subscribe", status_code=http.HTTP_202_ACCEPTED,
             dependencies=[_MARKETING_LIMIT])
def subscribe_to_marketing(data: MarketingSubscribeIn, background: BackgroundTasks,
                           request: Request, db: Session = Depends(get_db)):
    """Start a double opt-in marketing subscription for exactly the topics asked for.

    Returns 202 whatever the address's history is: saying "you're already subscribed" would
    make this an enumeration oracle. Nothing is sent until the address is confirmed.
    """
    from .. import email as email_mod

    subscription, token = mkt.subscribe(
        db, email=data.email, topics=data.topics, source=data.source,
        consent_ip=client_ip(request),
        consent_user_agent=request.headers.get("user-agent"))
    if subscription is None:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A valid address and at least one topic are required")
    if token:
        background.add_task(
            email_mod.send_marketing_verify_email,
            subscription.email,
            topics=[TOPIC_LABELS.get(t, t) for t in (subscription.topics or [])],
            expires_at=mkt.as_utc(subscription.verification_expires_at),
            confirm_url=f"{email_mod.public_base_url()}/preferences?confirm={token}")
    return {"status": "pending_verification"}


@router.post("/marketing/confirm")
def confirm_marketing(data: MarketingTokenIn, background: BackgroundTasks,
                      db: Session = Depends(get_db)):
    from .. import email as email_mod

    subscription, outcome, manage, unsub = mkt.confirm(db, token=data.token)
    if subscription is None:
        raise HTTPException(http.HTTP_400_BAD_REQUEST,
                            {"invalid": "This confirmation link is not valid.",
                             "already_used": "This link has already been used.",
                             "expired": "This confirmation link has expired."}[outcome])
    background.add_task(
        email_mod.send_marketing_confirmed_email, subscription.email,
        topics=[TOPIC_LABELS.get(t, t) for t in (subscription.topics or [])],
        manage_url=mkt.manage_url(manage), unsubscribe_url=mkt.unsubscribe_url(unsub))
    return {"status": subscription.status, "manage_token": manage,
            "topics": subscription.topics or []}


@router.get("/marketing/preferences")
def read_marketing_preferences(t: str, db: Session = Depends(get_db)):
    subscription = mkt.by_manage_token(db, t)
    if subscription is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "This preferences link is not valid")
    return {"email": subscription.email, "status": subscription.status,
            "topics": subscription.topics or [],
            "available_topics": [{"key": k, "label": TOPIC_LABELS[k]}
                                 for k in MARKETING_TOPICS]}


@router.patch("/marketing/preferences")
def update_marketing_preferences(t: str, data: MarketingPreferencesIn,
                                 background: BackgroundTasks,
                                 db: Session = Depends(get_db)):
    from .. import email as email_mod

    subscription = mkt.by_manage_token(db, t)
    if subscription is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "This preferences link is not valid")
    changed, previous = mkt.update_topics(db, subscription, data.topics)
    if changed:
        manage, unsub = mkt._links(db, subscription)  # noqa: SLF001
        if unsub:
            background.add_task(
                email_mod.send_marketing_preferences_email, subscription.email,
                previous_topics=[TOPIC_LABELS.get(x, x) for x in previous],
                current_topics=[TOPIC_LABELS.get(x, x)
                                for x in (subscription.topics or [])],
                manage_url=manage, unsubscribe_url=unsub)
    return {"changed": changed, "status": subscription.status,
            "topics": subscription.topics or []}


@router.post("/marketing/unsubscribe")
def unsubscribe_from_marketing(t: str, background: BackgroundTasks,
                               db: Session = Depends(get_db)):
    """One-click unsubscribe. Purpose-bound: this handle can do exactly this.

    Suppression is immediate and committed before the confirmation is queued. It stops
    MARKETING only - account, security, billing, privacy, support and status email are
    separate channels and are untouched.
    """
    from .. import email as email_mod

    subscription = mkt.by_unsubscribe_token(db, t)
    if subscription is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "This unsubscribe link is not valid")
    if not mkt.unsubscribe(db, subscription):
        return {"status": subscription.status}
    background.add_task(
        email_mod.send_marketing_unsubscribed_email, subscription.email,
        resubscribe_url=f"{email_mod.public_base_url()}/preferences")
    return {"status": subscription.status}


# ── MKT-004 guides and webinars ─────────────────────────────────────────────────────────

@router.post("/guides", status_code=http.HTTP_202_ACCEPTED,
             dependencies=[_MARKETING_LIMIT])
def request_guide(data: GuideRequestIn, background: BackgroundTasks, request: Request,
                  db: Session = Depends(get_db)):
    """Ask for one guide.

    The guide is fulfilled either way - that is transactional. The marketing opt-in is a
    SEPARATE affirmative choice with its own double opt-in, so ticking nothing gets you the
    guide and no campaign.
    """
    from .. import email as email_mod

    guide_request, token = mkt.request_guide(
        db, email=data.email, guide=data.guide, name=data.name,
        marketing_opt_in=data.marketing_opt_in, consent_ip=client_ip(request),
        consent_user_agent=request.headers.get("user-agent"))
    if guide_request is None:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A valid address and a known guide are required")
    mkt.notify_guide(db, background, guide_request)
    if token:
        background.add_task(
            email_mod.send_marketing_verify_email, guide_request.email,
            topics=[GUIDE_LABELS.get(data.guide, "Live Events education")],
            expires_at=None,
            confirm_url=f"{email_mod.public_base_url()}/preferences?confirm={token}")
    return {"guide": guide_request.guide, "status": guide_request.status,
            "marketing_subscription_pending": bool(token)}


@router.get("/webinars")
def list_webinars(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(MarketingWebinar)
        .where(MarketingWebinar.status.in_(("scheduled", "rescheduled")))
        .order_by(MarketingWebinar.starts_at_utc)).all()
    return [{"reference": w.reference, "title": w.title, "description": w.description,
             "starts_at_utc": mkt.as_utc(w.starts_at_utc),
             "duration_minutes": w.duration_minutes, "status": w.status}
            for w in rows]


@router.post("/webinars/{reference}/register", status_code=http.HTTP_202_ACCEPTED,
             dependencies=[_MARKETING_LIMIT])
def register_for_webinar(reference: str, data: WebinarRegistrationIn,
                         background: BackgroundTasks, db: Session = Depends(get_db)):
    """Register for one session.

    Registering is consent to be emailed ABOUT THIS SESSION. It creates no
    MarketingSubscription and enrols nobody in a campaign - the response says so.
    """
    webinar = db.scalar(select(MarketingWebinar).where(
        MarketingWebinar.reference == reference))
    if webinar is None or webinar.status in ("cancelled", "completed"):
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Session not found")
    registration = mkt.register_for_webinar(db, webinar, email=data.email, name=data.name)
    if registration is None:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A valid email address is required")
    mkt.notify_webinar(db, background, webinar, variant="confirmation")
    return {"reference": webinar.reference, "status": registration.status,
            "marketing_subscription_created": False}
