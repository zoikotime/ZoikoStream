"""Trust Center service (ZST-EC-001 TRU-001 -> TRU-002).

TRU-003 lives in services/vuln_disclosure.py, because reporter identity has a stricter
access rule than anything here and keeping it in its own module makes that boundary visible.

── THE THREE INVARIANTS THIS MODULE ENFORCES ─────────────────────────────────────────────
1. A DRAFT advisory has no audience. Nothing sends until `publish()` has committed.
2. A published statement is never rewritten. Every change appends a version.
3. "Affected customer" is a recorded fact (AdvisoryImpact), never an inference from the
   existence of an organization.

── ORDER OF OPERATIONS ───────────────────────────────────────────────────────────────────
Same shape as services/status_publication.py: the authoritative row commits, THEN the fan-out
runs as a background task whose failures are swallowed. A Resend outage must never unpublish
an advisory or un-approve an evidence request.
"""

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    ADVISORY_NOTIFIABLE,
    ADVISORY_SEVERITIES,
    AUTO_QUALIFIABLE_BASES,
    COMPONENT_KEYS,
    EVIDENCE_ACCESS_TTL_HOURS,
    EVIDENCE_CLASSIFICATIONS,
    EVIDENCE_DOCUMENT_TYPES,
    EVIDENCE_PURPOSES,
    EVIDENCE_SCOPES,
    IMPACT_BASES,
    QUALIFICATION_BASES,
    AdvisoryImpact,
    Organization,
    OrganizationSecurityContact,
    SecurityAdvisory,
    SecurityAdvisoryVersion,
    TrustEvidenceAccess,
    TrustEvidenceDocument,
    TrustEvidenceRequest,
    TrustNotice,
    User,
)

PURPOSE_TRUST_EVIDENCE = "trust_evidence_access"

# Fields whose change is material enough to be worth a customer's attention. A typo fix in
# the summary is not an "Update to advisory" email; a severity change is.
MATERIAL_FIELDS = ("severity", "summary", "customer_impact", "affected_components",
                   "affected_versions", "immediate_mitigation", "workaround_available",
                   "workaround_summary", "remediation_deadline", "fixed_version")

FIELD_LABELS = {
    "severity": "Severity",
    "summary": "Summary",
    "customer_impact": "Customer impact",
    "affected_components": "Affected components",
    "affected_versions": "Affected versions",
    "immediate_mitigation": "Immediate mitigation",
    "workaround_available": "Workaround availability",
    "workaround_summary": "Workaround",
    "remediation_deadline": "Remediation deadline",
    "fixed_version": "Fixed version",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(moment: datetime | None) -> datetime | None:
    """Render a stored timestamp in UTC.

    Same reason as status_publication.as_utc: the columns are `timestamptz`, so the instant is
    unambiguous, but psycopg renders it in the connection's timezone. A security advisory that
    states a time must state it in a zone the reader can trust.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _reference(prefix: str, db: Session, model, column) -> str:
    """A human-quotable reference. Collision-checked rather than assumed unique."""
    year = _now().year
    for _ in range(12):
        candidate = f"{prefix}-{year}-{secrets.token_hex(3).upper()}"
        if db.scalar(select(model).where(column == candidate)) is None:
            return candidate
    return f"{prefix}-{year}-{uuid.uuid4().hex[:8].upper()}"


def _claim(db: Session, kind: str, subject_type: str, subject_id, version: int,
           recipient: str = "") -> bool:
    """Claim one notification obligation. False means somebody already sent this one.

    Version- and recipient-keyed: an advisory published, updated twice and closed owes four
    notices to each recipient, and a coarser key would swallow most of them.
    """
    notice = TrustNotice(kind=kind, subject_type=subject_type, subject_id=subject_id,
                         version=version, recipient=(recipient or "").lower())
    db.add(notice)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _release(db: Session, kind: str, subject_type: str, subject_id, version: int,
             recipient: str = "") -> None:
    """Give a claim back when the send never happened, so a retry is still possible."""
    row = db.scalar(select(TrustNotice).where(
        TrustNotice.kind == kind, TrustNotice.subject_type == subject_type,
        TrustNotice.subject_id == subject_id, TrustNotice.version == version,
        TrustNotice.recipient == (recipient or "").lower()))
    if row is not None:
        db.delete(row)
        db.commit()


# ══════════════════════════════════════════════════════════════════════════════════════════
# TRU-001 — security advisory lifecycle
# ══════════════════════════════════════════════════════════════════════════════════════════

def create_advisory(db: Session, *, title: str, severity: str, summary: str,
                    affected_components: list[str], customer_impact: str | None = None,
                    immediate_mitigation: str | None = None,
                    affected_versions: str | None = None,
                    affected_scope_note: str | None = None,
                    cvss_vector: str | None = None,
                    workaround_available: bool = False,
                    workaround_summary: str | None = None,
                    internal_incident_id=None, vulnerability_report_id=None,
                    created_by=None) -> SecurityAdvisory | None:
    """Draft an advisory. Sends nothing - a draft has no audience by construction.

    `severity` must be one of the approved categories. It is recorded, not computed: there is
    no scanner feed or scoring workflow in this platform, so nothing here derives a severity
    from the report, the component, or anything else.
    """
    if severity not in ADVISORY_SEVERITIES:
        return None
    if not summary or not summary.strip():
        return None
    components = [c for c in (affected_components or []) if c in COMPONENT_KEYS]
    if not components:
        # An advisory that names nothing affected cannot tell a customer whether it applies.
        return None
    if workaround_available and not (workaround_summary or "").strip():
        # Claiming a workaround exists without saying what it is is worse than saying nothing.
        return None

    advisory = SecurityAdvisory(
        title=title, severity=severity, status="draft", summary=summary,
        customer_impact=customer_impact, immediate_mitigation=immediate_mitigation,
        affected_components=components, affected_versions=affected_versions,
        affected_scope_note=affected_scope_note, cvss_vector=cvss_vector,
        workaround_available=workaround_available, workaround_summary=workaround_summary,
        internal_incident_id=internal_incident_id,
        vulnerability_report_id=vulnerability_report_id, created_by=created_by, version=0)
    advisory.public_reference = _reference("ZSA", db, SecurityAdvisory,
                                           SecurityAdvisory.public_reference)
    db.add(advisory)
    db.commit()
    db.refresh(advisory)
    return advisory


def _append_version(db: Session, advisory: SecurityAdvisory, *, version_type: str,
                    change_summary: str | None = None,
                    changed_fields: list[str] | None = None,
                    published_by=None) -> SecurityAdvisoryVersion:
    """INSERT a published version. There is deliberately no update path.

    The snapshot is taken from the advisory as it stands, so a later edit to the parent row
    cannot rewrite what this version said.
    """
    advisory.version += 1
    row = SecurityAdvisoryVersion(
        advisory_id=advisory.id, version=advisory.version, version_type=version_type,
        severity=advisory.severity, status=advisory.status, summary=advisory.summary,
        customer_impact=advisory.customer_impact,
        affected_components=list(advisory.affected_components or []),
        affected_versions=advisory.affected_versions,
        change_summary=change_summary, changed_fields=list(changed_fields or []),
        published_at=_now(), published_by=published_by)
    db.add(row)
    return row


def publish(db: Session, advisory: SecurityAdvisory, *, approved_by=None) -> bool:
    """Publish a draft. Requires a named approver, and commits before anything is sent."""
    if advisory.status != "draft":
        return False
    if approved_by is None:
        # Publishing a security advisory is an approval decision. Refusing to fabricate one
        # is the whole reason this argument is mandatory.
        return False
    advisory.status = "published"
    advisory.published_at = _now()
    advisory.approved_by = approved_by
    advisory.approved_at = _now()
    _append_version(db, advisory, version_type="publication", published_by=approved_by)
    db.commit()
    db.refresh(advisory)
    return True


def material_changes(advisory: SecurityAdvisory, proposed: dict) -> list[str]:
    """Which MATERIAL_FIELDS the proposed values would actually change."""
    changed = []
    for field in MATERIAL_FIELDS:
        if field not in proposed:
            continue
        new = proposed[field]
        old = getattr(advisory, field)
        if isinstance(old, list) or isinstance(new, list):
            if sorted(old or []) != sorted(new or []):
                changed.append(field)
        elif old != new:
            changed.append(field)
    return changed


def publish_update(db: Session, advisory: SecurityAdvisory, *, changes: dict,
                   change_summary: str, published_by=None) -> SecurityAdvisoryVersion | None:
    """Materially update a published advisory by APPENDING a version.

    Returns None when nothing material changed, so a cosmetic edit does not mail anybody.
    """
    if advisory.status not in ("published", "updated", "remediation_available",
                               "remediated"):
        return None
    changed = material_changes(advisory, changes)
    if not changed:
        return None
    if not (change_summary or "").strip():
        # An update whose email cannot say what changed is not worth sending.
        return None

    for field in changed:
        value = changes[field]
        if field == "affected_components":
            value = [c for c in (value or []) if c in COMPONENT_KEYS]
            if not value:
                return None
        if field == "severity" and value not in ADVISORY_SEVERITIES:
            return None
        setattr(advisory, field, value)
    if advisory.workaround_available and not (advisory.workaround_summary or "").strip():
        db.rollback()
        return None

    # Status becomes "updated" only from the plain published state: an advisory already at
    # remediation_available must not regress to a weaker status by being edited.
    if advisory.status == "published":
        advisory.status = "updated"
    row = _append_version(db, advisory, version_type="update",
                          change_summary=change_summary, changed_fields=changed,
                          published_by=published_by)
    db.commit()
    db.refresh(advisory)
    return row


def mark_remediation_available(db: Session, advisory: SecurityAdvisory, *,
                              fixed_version: str | None = None,
                              remediation_steps: str,
                              remediation_deadline: datetime | None = None,
                              action_mandatory: bool = False,
                              published_by=None) -> bool:
    """Record that a fix or mitigating configuration actually exists.

    Refuses without remediation steps: "remediation available" with nothing to do is a claim
    the customer cannot act on. `action_mandatory` is the ONLY thing that unlocks imperative
    wording, and a deadline is stated only when a real one was recorded.
    """
    if advisory.status not in ("published", "updated"):
        return False
    if not (remediation_steps or "").strip():
        return False
    if not fixed_version and not remediation_steps.strip():
        return False
    advisory.status = "remediation_available"
    advisory.remediation_available_at = _now()
    advisory.fixed_version = fixed_version
    advisory.remediation_steps = remediation_steps
    advisory.remediation_deadline = remediation_deadline
    advisory.action_mandatory = bool(action_mandatory)
    _append_version(db, advisory, version_type="remediation", published_by=published_by)
    db.commit()
    db.refresh(advisory)
    return True


def close_advisory(db: Session, advisory: SecurityAdvisory, *, closure_note: str,
                   published_by=None) -> bool:
    """Close an advisory. Only from a state where closure is meaningful.

    Note the deliberate absence of any "all customers have remediated" flag: closure is a
    statement about the ADVISORY, and the message says so explicitly.
    """
    if advisory.status not in ("remediation_available", "remediated", "updated",
                               "published"):
        return False
    if advisory.status == "closed":
        return False
    if not (closure_note or "").strip():
        return False
    advisory.status = "closed"
    advisory.closed_at = _now()
    advisory.closure_note = closure_note
    if advisory.remediation_available_at and advisory.remediated_at is None:
        advisory.remediated_at = _now()
    _append_version(db, advisory, version_type="closure", published_by=published_by)
    db.commit()
    db.refresh(advisory)
    return True


def published_versions(db: Session, advisory: SecurityAdvisory) -> list:
    return list(db.scalars(
        select(SecurityAdvisoryVersion)
        .where(SecurityAdvisoryVersion.advisory_id == advisory.id)
        .order_by(SecurityAdvisoryVersion.version)).all())


def record_impact(db: Session, advisory: SecurityAdvisory, *, org_id, basis: str,
                  evidence_note: str | None = None, affected_versions: str | None = None,
                  recorded_by=None) -> AdvisoryImpact | None:
    """Record that one organization is affected, and why.

    This is the ONLY way an organization becomes an "affected customer". `basis` must name
    something recorded about that tenant - a configuration, a version, an operator's
    confirmation - never "the organization exists".
    """
    if basis not in IMPACT_BASES:
        return None
    if db.get(Organization, org_id) is None:
        return None
    existing = db.scalar(select(AdvisoryImpact).where(
        AdvisoryImpact.advisory_id == advisory.id, AdvisoryImpact.org_id == org_id))
    if existing is not None:
        existing.basis = basis
        existing.evidence_note = evidence_note
        existing.affected_versions = affected_versions
        existing.cleared_at = None
        db.commit()
        return existing
    row = AdvisoryImpact(advisory_id=advisory.id, org_id=org_id, basis=basis,
                         evidence_note=evidence_note, affected_versions=affected_versions,
                         recorded_by=recorded_by)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def affected_organizations(db: Session, advisory: SecurityAdvisory) -> list[uuid.UUID]:
    """Organizations with a live impact record. Empty when impact was never mapped."""
    return list(db.scalars(
        select(AdvisoryImpact.org_id).where(
            AdvisoryImpact.advisory_id == advisory.id,
            AdvisoryImpact.cleared_at.is_(None))).all())


def advisory_recipients(db: Session, advisory: SecurityAdvisory) -> list[tuple[str, str]]:
    """Who gets this advisory: (email, reason).

    Two populations, and only two:

      subscribed VERIFIED security contacts of ANY organization - the people who asked to
        receive advisories and proved they control the address, and

      subscribed VERIFIED security contacts of organizations with an AdvisoryImpact row.

    In practice the second is a subset of the first today, and that is the honest outcome:
    without a separate advisory mailing list, a verified security contact is the audience.
    What matters is what is NOT here - every org admin, every member, every status
    subscriber and every marketing subscriber. An unverified or unsubscribed contact is
    excluded, and an organization is never included merely for existing.
    """
    affected = set(affected_organizations(db, advisory))
    rows = db.scalars(
        select(OrganizationSecurityContact).where(
            OrganizationSecurityContact.status == "verified",
            OrganizationSecurityContact.verified_at.isnot(None),
            OrganizationSecurityContact.revoked_at.is_(None),
            OrganizationSecurityContact.advisory_subscribed.is_(True))).all()
    out: dict[str, str] = {}
    for contact in rows:
        reason = "affected_customer" if contact.org_id in affected else "security_contact"
        key = (contact.email or "").lower()
        if not key:
            continue
        # An affected-customer reason wins: it is the stronger statement, and the message
        # tells that reader the advisory applies to their organization specifically.
        if key not in out or reason == "affected_customer":
            out[key] = reason
    return sorted(out.items())


VERSION_TO_KIND = {
    "publication": "advisory_published",
    "update": "advisory_updated",
    "remediation": "advisory_remediation",
    "closure": "advisory_closed",
}


def notify_advisory(db: Session, background, advisory: SecurityAdvisory,
                    version: SecurityAdvisoryVersion | None = None) -> str | None:
    """Fan out the latest published version of an advisory.

    Returns the notification kind, or None when there is nothing to send. A DRAFT returns
    None: there is no code path from a draft to an outbound message.
    """
    from .. import email as email_mod

    if advisory.status == "draft" or advisory.published_at is None:
        return None
    if advisory.status not in ADVISORY_NOTIFIABLE:
        return None

    versions = published_versions(db, advisory)
    target = version or (versions[-1] if versions else None)
    if target is None:
        return None
    kind = VERSION_TO_KIND.get(target.version_type)
    if kind is None:
        return None

    changes = [FIELD_LABELS.get(f, f) for f in (target.changed_fields or [])]
    recipients = advisory_recipients(db, advisory)
    sent = 0
    for address, reason in recipients:
        if not _claim(db, kind, "advisory", advisory.id, target.version, address):
            continue
        sent += 1

        def send(addr=address, why=reason, ver=target.version):
            ok = email_mod.send_security_advisory_email(
                to=addr,
                reference=advisory.public_reference,
                variant=kind,
                title=advisory.title,
                severity=advisory.severity,
                summary=target.summary,
                customer_impact=target.customer_impact,
                components=list(target.affected_components or []),
                affected_versions=target.affected_versions,
                published_at=as_utc(target.published_at),
                immediate_mitigation=advisory.immediate_mitigation,
                fixed_version=advisory.fixed_version,
                remediation_steps=advisory.remediation_steps,
                remediation_deadline=as_utc(advisory.remediation_deadline),
                action_mandatory=advisory.action_mandatory,
                workaround_available=advisory.workaround_available,
                workaround_summary=advisory.workaround_summary,
                change_summary=target.change_summary,
                changed_fields=changes,
                closure_note=advisory.closure_note,
                affected_customer=(why == "affected_customer"),
                cvss_vector=advisory.cvss_vector,
            )
            if not ok:
                # Give the claim back so a later run can retry. The advisory stays published
                # either way - a mail failure is not a publication failure.
                _release(db, kind, "advisory", advisory.id, ver, addr)

        background.add_task(send)
    return kind if sent else None


# ══════════════════════════════════════════════════════════════════════════════════════════
# TRU-002 — trust evidence request and access
# ══════════════════════════════════════════════════════════════════════════════════════════

def create_document(db: Session, *, title: str, document_type: str, version: str,
                    classification: str, allowed_purposes: list[str],
                    allowed_scopes: list[str], content: str | None = None,
                    content_type: str = "application/pdf",
                    available_from: datetime | None = None,
                    expires_at: datetime | None = None,
                    created_by=None) -> TrustEvidenceDocument | None:
    """Register an evidence document and put its CONTENT in private storage.

    The bytes never land in a column and never reach an email. `storage_reference` is an
    object key resolved through the same private path developer exports use.
    """
    if document_type not in EVIDENCE_DOCUMENT_TYPES:
        return None
    if classification not in EVIDENCE_CLASSIFICATIONS:
        return None
    purposes = [p for p in (allowed_purposes or []) if p in EVIDENCE_PURPOSES]
    scopes = [s for s in (allowed_scopes or []) if s in EVIDENCE_SCOPES]
    if not purposes or not scopes:
        # Fails closed. An empty allow-list means "nothing is permitted", never "everything".
        return None

    doc = TrustEvidenceDocument(
        title=title, document_type=document_type, version=version,
        classification=classification, status="draft", content_type=content_type,
        allowed_purposes=purposes, allowed_scopes=scopes,
        available_from=available_from or _now(), expires_at=expires_at,
        created_by=created_by)
    db.add(doc)
    db.flush()
    if content is not None:
        key = f"trust-evidence/{doc.id}/{document_type}-{version}.dat"
        _store(key, content)
        doc.storage_reference = key
        doc.byte_size = len(content.encode())
        doc.status = "available"
    db.commit()
    db.refresh(doc)
    return doc


def _store(object_key: str, content: str) -> None:
    """Write to private storage, reusing the developer-export path.

    A bucket when one is configured, a private local directory otherwise. Neither is publicly
    reachable, and every read is authorized per request - there is no public URL to leak.
    """
    from . import developer_export

    developer_export._store(object_key, content)  # noqa: SLF001 — one private-storage path


def load_document(doc: TrustEvidenceDocument) -> str | None:
    from . import livekit

    if not doc.storage_reference:
        return None
    if livekit.gcs_configured():
        from ..config import settings

        client = livekit._gcs_client()  # noqa: SLF001
        blob = client.bucket(settings.GCS_BUCKET).blob(doc.storage_reference)
        return blob.download_as_text()
    import pathlib

    path = pathlib.Path("private_exports") / doc.storage_reference
    return path.read_text(encoding="utf-8") if path.exists() else None


def qualify(db: Session, *, requester_email: str, claimed_basis: str,
            requester_id=None) -> tuple[str | None, bool]:
    """Check a requester's qualification against what this platform actually records.

    Returns (basis, verified). `verified` True means the basis was CHECKED:

      customer_organization  the requester is an active user of an organization here.

    Everything else is accepted as a CLAIM and left for human review - there is no auditor
    register and no commercial-process record to check `authorized_auditor` or
    `approved_prospect` against, and inventing an eligibility rule for them would be worse
    than admitting the gap. Nothing is ever auto-APPROVED: qualification only decides whether
    a request goes straight to review or needs more from the requester.
    """
    if claimed_basis not in QUALIFICATION_BASES:
        return None, False
    if claimed_basis == "customer_organization":
        user = None
        if requester_id is not None:
            user = db.get(User, requester_id)
        if user is None:
            user = db.scalar(select(User).where(
                User.email == (requester_email or "").lower()))
        verified = bool(user and user.is_active and user.org_id
                        and db.get(Organization, user.org_id) is not None)
        return claimed_basis, verified
    return claimed_basis, False


def create_request(db: Session, *, requester_email: str, document: TrustEvidenceDocument,
                   purpose: str, scope: str, requester_name: str | None = None,
                   company_name: str | None = None, purpose_note: str | None = None,
                   claimed_basis: str = "customer_organization",
                   requester_id=None, organization_id=None) -> TrustEvidenceRequest | None:
    """Open an evidence request. Bound to one requester, document, purpose and scope.

    Anonymous unrestricted downloads are impossible by construction: there is no path from
    here to a document that does not go through an approval.
    """
    if purpose not in EVIDENCE_PURPOSES or scope not in EVIDENCE_SCOPES:
        return None
    if purpose not in (document.allowed_purposes or []):
        return None
    if scope not in (document.allowed_scopes or []):
        return None
    if document.status != "available":
        return None
    email = (requester_email or "").strip().lower()
    if "@" not in email:
        return None

    basis, verified = qualify(db, requester_email=email, claimed_basis=claimed_basis,
                              requester_id=requester_id)
    if basis is None:
        return None

    request = TrustEvidenceRequest(
        requester_email=email, requester_name=requester_name, requester_id=requester_id,
        organization_id=organization_id, company_name=company_name,
        document_id=document.id, purpose=purpose, scope=scope, purpose_note=purpose_note,
        # A verified qualification still goes to review: qualification is not approval.
        status="under_review" if verified else "requested",
        qualification_basis=basis, qualification_verified=verified, version=0)
    request.reference = _reference("TEV", db, TrustEvidenceRequest,
                                    TrustEvidenceRequest.reference)
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def approve_request(db: Session, request: TrustEvidenceRequest, *, approved_by,
                    ttl_hours: int = EVIDENCE_ACCESS_TTL_HOURS,
                    decision_note: str | None = None) -> str | None:
    """Approve a request and mint the bound, expiring access token.

    Returns the RAW token exactly once - only its sha256 is stored. Requires a named
    approver: there is no automatic approval path for confidential evidence.
    """
    if request.status not in ("requested", "under_review"):
        return None
    if approved_by is None:
        return None
    document = db.get(TrustEvidenceDocument, request.document_id)
    if document is None or document.status != "available":
        return None
    # Re-check the binding at approval time: the document's allow-lists may have narrowed
    # since the request was filed.
    if request.purpose not in (document.allowed_purposes or []):
        return None
    if request.scope not in (document.allowed_scopes or []):
        return None

    raw = secrets.token_urlsafe(32)
    request.status = "approved"
    request.approved_at = _now()
    request.approved_by = approved_by
    request.decision_note = decision_note
    request.access_token_hash = _hash(raw)
    request.access_expires_at = _now() + timedelta(hours=max(1, ttl_hours))
    request.version += 1
    db.commit()
    db.refresh(request)
    return raw


def deny_request(db: Session, request: TrustEvidenceRequest, *, denied_by,
                 decision_note: str) -> bool:
    """Deny a request with a customer-safe reason. No token is ever minted."""
    if request.status not in ("requested", "under_review"):
        return False
    if not (decision_note or "").strip():
        return False
    request.status = "denied"
    request.denied_at = _now()
    request.denied_by = denied_by
    request.decision_note = decision_note
    request.access_token_hash = None
    request.version += 1
    db.commit()
    db.refresh(request)
    return True


def revoke_access(db: Session, request: TrustEvidenceRequest, *, revoked_by=None,
                  reason: str | None = None) -> bool:
    """Revoke approved access. The token hash is CLEARED, so the old link dies immediately."""
    if request.status != "approved":
        return False
    request.status = "revoked"
    request.revoked_at = _now()
    request.access_token_hash = None
    request.decision_note = reason or request.decision_note
    request.version += 1
    db.commit()
    db.refresh(request)
    return True


def expire_access(db: Session, request: TrustEvidenceRequest) -> bool:
    """Expire approved access whose window has passed. Same token clearing as revocation."""
    if request.status != "approved":
        return False
    if request.access_expires_at is None or request.access_expires_at > _now():
        return False
    request.status = "expired"
    request.expired_at = _now()
    request.access_token_hash = None
    request.version += 1
    db.commit()
    db.refresh(request)
    return True


def _log_access(db: Session, request: TrustEvidenceRequest, outcome: str,
                *, client: str | None = None, ip: str | None = None) -> None:
    """Append one authorization decision. Both outcomes, never any document content."""
    db.add(TrustEvidenceAccess(
        request_id=request.id, document_id=request.document_id,
        requester_email=request.requester_email, requester_id=request.requester_id,
        purpose=request.purpose, scope=request.scope, outcome=outcome,
        client=(client or "")[:160], ip=(ip or "")[:64]))
    db.commit()


def authorize_access(db: Session, *, token: str, purpose: str | None = None,
                     document_id=None, requester_email: str | None = None,
                     client: str | None = None, ip: str | None = None
                     ) -> tuple[TrustEvidenceRequest | None, str]:
    """The full access boundary, in one place.

    Every dimension is checked, and every outcome is logged:

        token      -> the approved request (constant-time compare against the stored hash)
        recipient  -> the address the approval was issued for
        document   -> the document the approval names
        purpose    -> the purpose the approval names AND the document still allows
        scope      -> the scope the approval names AND the document still allows
        time       -> not expired; an expired approval is expired on the spot
        revocation -> a revoked approval fails immediately

    Returns (request, outcome). Only outcome "authorized" may be followed by a download.
    """
    if not token:
        return None, "invalid_token"
    request = db.scalar(select(TrustEvidenceRequest).where(
        TrustEvidenceRequest.access_token_hash == _hash(token)))
    if request is None:
        # Nothing to log against: no request was identified, so there is no subject.
        return None, "invalid_token"
    if not hmac.compare_digest(_hash(token), request.access_token_hash or ""):
        _log_access(db, request, "invalid_token", client=client, ip=ip)
        return None, "invalid_token"

    if request.status == "revoked":
        _log_access(db, request, "revoked", client=client, ip=ip)
        return None, "revoked"
    if request.status == "expired":
        _log_access(db, request, "expired", client=client, ip=ip)
        return None, "expired"
    if request.status != "approved":
        _log_access(db, request, "not_approved", client=client, ip=ip)
        return None, "not_approved"
    if request.access_expires_at is None or request.access_expires_at <= _now():
        expire_access(db, request)
        _log_access(db, request, "expired", client=client, ip=ip)
        return None, "expired"

    if requester_email is not None and \
            (requester_email or "").strip().lower() != request.requester_email:
        _log_access(db, request, "recipient_mismatch", client=client, ip=ip)
        return None, "recipient_mismatch"
    if document_id is not None and str(document_id) != str(request.document_id):
        _log_access(db, request, "document_mismatch", client=client, ip=ip)
        return None, "document_mismatch"
    if purpose is not None and purpose != request.purpose:
        _log_access(db, request, "purpose_mismatch", client=client, ip=ip)
        return None, "purpose_mismatch"

    document = db.get(TrustEvidenceDocument, request.document_id)
    if document is None or document.status != "available":
        _log_access(db, request, "document_unavailable", client=client, ip=ip)
        return None, "document_unavailable"
    if document.expires_at is not None and document.expires_at <= _now():
        _log_access(db, request, "document_expired", client=client, ip=ip)
        return None, "document_expired"
    # The document's own allow-lists are re-checked on every read, not just at approval:
    # narrowing them must take effect for approvals already in flight.
    if request.purpose not in (document.allowed_purposes or []):
        _log_access(db, request, "purpose_not_allowed", client=client, ip=ip)
        return None, "purpose_not_allowed"
    if request.scope not in (document.allowed_scopes or []):
        _log_access(db, request, "scope_not_allowed", client=client, ip=ip)
        return None, "scope_not_allowed"

    request.access_count += 1
    request.last_accessed_at = _now()
    db.commit()
    _log_access(db, request, "authorized", client=client, ip=ip)
    return request, "authorized"


def access_log(db: Session, request: TrustEvidenceRequest) -> list:
    return list(db.scalars(
        select(TrustEvidenceAccess)
        .where(TrustEvidenceAccess.request_id == request.id)
        .order_by(TrustEvidenceAccess.at)).all())


def access_url(request: TrustEvidenceRequest, token: str) -> str:
    from urllib.parse import quote

    from ..email import public_base_url

    # The API path, deliberately: this link is a FILE DOWNLOAD served straight from private
    # storage by routers/trust.download_evidence, not a page. Pointing it at the SPA would
    # hand the reviewer an app shell instead of the document.
    return (f"{public_base_url()}/api/trust/evidence/{request.reference}"
            f"?t={quote(token, safe='')}")


def notify_request_received(db: Session, background,
                            request: TrustEvidenceRequest) -> bool:
    from .. import email as email_mod

    if not _claim(db, "evidence_received", "evidence_request", request.id, 0,
                  request.requester_email):
        return False
    document = db.get(TrustEvidenceDocument, request.document_id)

    def send():
        ok = email_mod.send_trust_request_received_email(
            to=request.requester_email, reference=request.reference,
            requester_name=request.requester_name,
            document_title=document.title if document else None,
            document_type=document.document_type if document else None,
            purpose=request.purpose, scope=request.scope, status=request.status)
        if not ok:
            _release(db, "evidence_received", "evidence_request", request.id, 0,
                     request.requester_email)

    background.add_task(send)
    return True


def notify_access_approved(db: Session, background, request: TrustEvidenceRequest,
                           token: str) -> bool:
    """Send the short-lived access link. The document itself is never attached."""
    from .. import email as email_mod

    if request.status != "approved" or not token:
        return False
    if not _claim(db, "evidence_approved", "evidence_request", request.id,
                  request.version, request.requester_email):
        return False
    document = db.get(TrustEvidenceDocument, request.document_id)

    def send():
        ok = email_mod.send_trust_access_approved_email(
            to=request.requester_email, reference=request.reference,
            requester_name=request.requester_name,
            document_title=document.title if document else None,
            document_version=document.version if document else None,
            classification=document.classification if document else None,
            purpose=request.purpose, scope=request.scope,
            expires_at=as_utc(request.access_expires_at),
            url=access_url(request, token))
        if not ok:
            _release(db, "evidence_approved", "evidence_request", request.id,
                     request.version, request.requester_email)

    background.add_task(send)
    return True


def notify_request_denied(db: Session, background, request: TrustEvidenceRequest) -> bool:
    from .. import email as email_mod

    if request.status != "denied":
        return False
    if not _claim(db, "evidence_denied", "evidence_request", request.id, request.version,
                  request.requester_email):
        return False

    def send():
        ok = email_mod.send_trust_request_decided_email(
            to=request.requester_email, reference=request.reference,
            requester_name=request.requester_name, variant="denied",
            decision_note=request.decision_note)
        if not ok:
            _release(db, "evidence_denied", "evidence_request", request.id,
                     request.version, request.requester_email)

    background.add_task(send)
    return True


def notify_access_ended(db: Session, background, request: TrustEvidenceRequest) -> bool:
    """Tell the requester their access stopped working, and how to ask again."""
    from .. import email as email_mod

    if request.status not in ("expired", "revoked"):
        return False
    kind = f"evidence_{request.status}"
    if not _claim(db, kind, "evidence_request", request.id, request.version,
                  request.requester_email):
        return False

    def send():
        ok = email_mod.send_trust_request_decided_email(
            to=request.requester_email, reference=request.reference,
            requester_name=request.requester_name, variant=request.status,
            decision_note=request.decision_note)
        if not ok:
            _release(db, kind, "evidence_request", request.id, request.version,
                     request.requester_email)

    background.add_task(send)
    return True


def sweep(db: Session, background) -> dict:
    """Expire evidence access whose window has passed, and tell the requester.

    Rides the shared leader-elected ticker (services/event_planning.sweep) rather than adding
    another one - fourteen tickers is enough.
    """
    counts = {"evidence_expired": 0}
    stale = db.scalars(select(TrustEvidenceRequest).where(
        TrustEvidenceRequest.status == "approved",
        TrustEvidenceRequest.access_expires_at.isnot(None),
        TrustEvidenceRequest.access_expires_at <= _now())).all()
    for request in stale:
        if expire_access(db, request):
            counts["evidence_expired"] += 1
            notify_access_ended(db, background, request)
    return counts
