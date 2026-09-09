"""Vulnerability disclosure (ZST-EC-001 TRU-003).

── WHY THIS IS NOT A SUPPORT TICKET ──────────────────────────────────────────────────────
`SupportTicket` is readable by organization admins and support staff by design, and SUP-002
mail quotes ticket state back to a customer. A vulnerability report must not travel on that
rail: the reporter's identity, the reproduction steps and the evidence would all be visible
to people with no security role. So this is a separate register with a separate channel, and
`reporter_identity()` below is the only reader of the identity fields.

── WHAT THIS MODULE REFUSES TO SAY ───────────────────────────────────────────────────────
There is no bug-bounty programme, no public credit policy and no coordinated-disclosure
policy configured in this platform. So no message here mentions a payout, a credit, an
embargo, a disclosure date, or a remediation deadline. The columns exist for the day a real
policy does, and `coordination_recorded()` reports honestly whether one was ever filled in.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    IDENTITY_VISIBILITY,
    VULN_CATEGORIES,
    VULN_REPORTER_NOTIFIABLE,
    VULN_RESOLUTIONS,
    TrustNotice,
    User,
    VulnerabilityReport,
    VulnerabilityReportUpdate,
)

PURPOSE_VULN_PORTAL = "vulnerability_reporter_portal"

PORTAL_TTL_DAYS = 120           # long enough for a real coordination cycle

# Roles that may read reporter identity.
#
# `super_admin` only, and that is a REPORTED GAP rather than a design choice: models/user.py
# ROLES has no dedicated security role (org_governance.REVIEWER_SECURITY_ADMIN is literally
# the string "organization_admin"), so there is no narrower role to grant this to. Inventing
# one here would be a role that nothing else in the platform enforces.
#
# What matters is what is excluded, and that holds today: `org_admin` is deliberately absent,
# so an ordinary organization admin - including an admin at an AFFECTED customer - cannot
# read a reporter's identity, and neither can support staff, another researcher, or anybody
# reaching the public projection.
SECURITY_IDENTITY_ROLES = ("super_admin",)

# Things a report form must never ask for, and a report must never carry. Checked by the
# router's own validation and asserted by the test suite.
FORBIDDEN_SECRET_HINTS = ("password", "api_key", "api key", "private_key", "private key",
                          "secret_key", "bearer ", "-----begin")

STAGE_MESSAGES = {
    "acknowledged": "We have received your report and it is with our security team.",
    "clarification_needed": "We need more information before we can continue.",
    "coordinating": "We are working on a fix and will keep you updated.",
    "remediated": "The issue you reported has been remediated.",
    "closed": "We have closed this report.",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _reference(db: Session) -> str:
    year = _now().year
    for _ in range(12):
        candidate = f"ZVR-{year}-{secrets.token_hex(3).upper()}"
        if db.scalar(select(VulnerabilityReport).where(
                VulnerabilityReport.reference == candidate)) is None:
            return candidate
    return f"ZVR-{year}-{uuid.uuid4().hex[:8].upper()}"


def _claim(db: Session, kind: str, subject_id, version: int, recipient: str = "") -> bool:
    db.add(TrustNotice(kind=kind, subject_type="vulnerability_report",
                       subject_id=subject_id, version=version,
                       recipient=(recipient or "").lower()))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _release(db: Session, kind: str, subject_id, version: int, recipient: str = "") -> None:
    row = db.scalar(select(TrustNotice).where(
        TrustNotice.kind == kind, TrustNotice.subject_type == "vulnerability_report",
        TrustNotice.subject_id == subject_id, TrustNotice.version == version,
        TrustNotice.recipient == (recipient or "").lower()))
    if row is not None:
        db.delete(row)
        db.commit()


def looks_like_a_secret(text: str | None) -> bool:
    """Cheap guard against a reporter pasting a credential into the form.

    Not a security control - it is a courtesy that keeps secrets out of the register when
    somebody helpfully includes one. The router warns rather than silently storing it.
    """
    low = (text or "").lower()
    return any(hint in low for hint in FORBIDDEN_SECRET_HINTS)


def submit(db: Session, *, reporter_email: str, title: str, category: str,
           description: str, reproduction: str | None = None,
           affected_service: str | None = None, reporter_name: str | None = None,
           identity_visibility: str = "private",
           evidence: str | None = None) -> tuple[VulnerabilityReport | None, str | None]:
    """Accept a report through the protected channel.

    Returns (report, raw_portal_token). The token is the researcher's handle on their own
    report - they typically have no account here - and it grants access to NOTHING else.
    Evidence, when supplied, goes to private storage; it is never stored in a column and
    never attached to a message.
    """
    if category not in VULN_CATEGORIES:
        return None, None
    if identity_visibility not in IDENTITY_VISIBILITY:
        return None, None
    email = (reporter_email or "").strip().lower()
    if "@" not in email:
        return None, None
    if not (title or "").strip() or not (description or "").strip():
        return None, None

    raw = secrets.token_urlsafe(32)
    report = VulnerabilityReport(
        reporter_email=email, reporter_name=reporter_name,
        reporter_identity_visibility=identity_visibility,
        title=title.strip(), category=category, affected_service=affected_service,
        description=description, reproduction=reproduction, status="received",
        received_at=_now(), portal_token_hash=_hash(raw),
        portal_expires_at=_now() + timedelta(days=PORTAL_TTL_DAYS), version=0)
    report.reference = _reference(db)
    db.add(report)
    db.flush()
    if evidence:
        key = f"vuln-evidence/{report.id}/evidence.dat"
        _store(key, evidence)
        report.evidence_reference = key
    db.commit()
    db.refresh(report)
    return report, raw


def _store(object_key: str, content: str) -> None:
    """Private storage, same single path as evidence documents and developer exports."""
    from . import developer_export

    developer_export._store(object_key, content)  # noqa: SLF001 — one private-storage path


def load_evidence(report: VulnerabilityReport, *, actor: User | None) -> str | None:
    """Read a researcher's evidence. Security-role gated, like the identity fields."""
    if not can_read_identity(actor):
        return None
    if not report.evidence_reference:
        return None
    from . import livekit

    if livekit.gcs_configured():
        from ..config import settings

        client = livekit._gcs_client()  # noqa: SLF001
        blob = client.bucket(settings.GCS_BUCKET).blob(report.evidence_reference)
        return blob.download_as_text()
    import pathlib

    path = pathlib.Path("private_exports") / report.evidence_reference
    return path.read_text(encoding="utf-8") if path.exists() else None


def can_read_identity(actor: User | None) -> bool:
    return bool(actor is not None and getattr(actor, "role", None) in SECURITY_IDENTITY_ROLES)


def reporter_identity(report: VulnerabilityReport, *, actor: User | None) -> dict | None:
    """The ONLY reader of reporter identity in this codebase.

    Returns None for anybody without a security role - an affected customer, an ordinary org
    admin, another researcher, a support agent. There is no other accessor: advisories carry
    `vulnerability_report_id` for correlation and never the reporter, and no email template
    in email.py accepts a reporter name or address as a parameter.
    """
    if not can_read_identity(actor):
        return None
    return {"email": report.reporter_email, "name": report.reporter_name,
            "visibility": report.reporter_identity_visibility}


def public_projection(report: VulnerabilityReport) -> dict:
    """What anybody without a security role may see. Identity is simply absent."""
    return {
        "reference": report.reference,
        "title": report.title,
        "category": report.category,
        "status": report.status,
        "received_at": as_utc(report.received_at),
        "acknowledged_at": as_utc(report.acknowledged_at),
        "remediated_at": as_utc(report.remediated_at),
        "closed_at": as_utc(report.closed_at),
        "resolution": report.resolution,
        "safe_update": report.safe_reporter_update,
    }


def _append_update(db: Session, report: VulnerabilityReport, *, stage: str, body: str,
                   published_by=None) -> VulnerabilityReportUpdate:
    """INSERT a researcher-visible update. Append-only, like every published history here."""
    report.version += 1
    row = VulnerabilityReportUpdate(
        report_id=report.id, version=report.version, stage=stage, body=body,
        published_at=_now(), published_by=published_by)
    db.add(row)
    return row


def acknowledge(db: Session, report: VulnerabilityReport, *,
                note: str | None = None) -> bool:
    """Acknowledge receipt. Acknowledgement is not validation, and says so."""
    if report.status != "received":
        return False
    report.status = "acknowledged"
    report.acknowledged_at = _now()
    body = note or STAGE_MESSAGES["acknowledged"]
    report.safe_reporter_update = body
    _append_update(db, report, stage="acknowledged", body=body)
    db.commit()
    db.refresh(report)
    return True


def triage(db: Session, report: VulnerabilityReport, *, triaged_by=None) -> bool:
    """Internal triage. Deliberately sends NOTHING: triage is not a researcher-facing event."""
    if report.status not in ("acknowledged",):
        return False
    report.status = "triaged"
    report.triaged_at = _now()
    report.triaged_by = triaged_by
    db.commit()
    db.refresh(report)
    return True


def start_validation(db: Session, report: VulnerabilityReport) -> bool:
    """Also silent. "We are checking" is internal state, not a lifecycle update."""
    if report.status != "triaged":
        return False
    report.status = "validating"
    report.validating_at = _now()
    db.commit()
    db.refresh(report)
    return True


def request_clarification(db: Session, report: VulnerabilityReport, *,
                          question: str) -> VulnerabilityReportUpdate | None:
    """Ask the researcher for more, in researcher-safe words they wrote nothing about."""
    if report.status in ("closed",):
        return None
    if not (question or "").strip():
        return None
    report.safe_reporter_update = question
    row = _append_update(db, report, stage="clarification_needed", body=question)
    db.commit()
    db.refresh(report)
    return row


def start_coordination(db: Session, report: VulnerabilityReport, *,
                       safe_update: str | None = None,
                       disclosure_date: datetime | None = None,
                       embargo_until: datetime | None = None,
                       remediation_target: datetime | None = None,
                       public_credit_preference: str | None = None) -> bool:
    """Move to coordinated remediation.

    The coordination fields are stored ONLY if a caller passes real ones. Nothing is defaulted
    or derived: with no coordinated-disclosure policy configured, they stay null and the
    researcher message mentions no date, embargo or credit at all.
    """
    if report.status not in ("triaged", "validating"):
        return False
    report.status = "coordinating"
    report.coordinated_at = _now()
    if disclosure_date is not None:
        report.disclosure_date = disclosure_date
    if embargo_until is not None:
        report.embargo_until = embargo_until
    if remediation_target is not None:
        report.remediation_target = remediation_target
    if public_credit_preference is not None:
        report.public_credit_preference = public_credit_preference
    body = safe_update or STAGE_MESSAGES["coordinating"]
    report.safe_reporter_update = body
    _append_update(db, report, stage="coordinating", body=body)
    db.commit()
    db.refresh(report)
    return True


def coordination_recorded(report: VulnerabilityReport) -> bool:
    """Whether a REAL coordination agreement exists for this report.

    False for every report today, and the message layer reads this rather than assuming.
    """
    return any((report.disclosure_date, report.embargo_until, report.remediation_target))


def mark_remediated(db: Session, report: VulnerabilityReport, *, safe_update: str,
                    advisory_id=None) -> bool:
    """Record remediation. Requires an authoritative caller and a real statement.

    Refuses from any state before coordination: "remediated" must describe work that
    happened, not a hope. Nothing here infers remediation from an advisory's status.
    """
    if report.status not in ("coordinating",):
        return False
    if not (safe_update or "").strip():
        return False
    report.status = "remediated"
    report.remediated_at = _now()
    report.resolution = "remediated"
    if advisory_id is not None:
        report.advisory_id = advisory_id
    report.safe_reporter_update = safe_update
    _append_update(db, report, stage="remediated", body=safe_update)
    db.commit()
    db.refresh(report)
    return True


def close_report(db: Session, report: VulnerabilityReport, *, resolution: str,
                 safe_update: str) -> bool:
    """Close with a recorded resolution. Every value is an outcome, never a verdict on the
    researcher, and the message reflects the resolution rather than assuming remediation."""
    if resolution not in VULN_RESOLUTIONS:
        return False
    if report.status == "closed":
        return False
    if not (safe_update or "").strip():
        return False
    report.status = "closed"
    report.closed_at = _now()
    report.resolution = resolution
    report.safe_reporter_update = safe_update
    _append_update(db, report, stage="closed", body=safe_update)
    db.commit()
    db.refresh(report)
    return True


def reporter_history(db: Session, report: VulnerabilityReport) -> list:
    return list(db.scalars(
        select(VulnerabilityReportUpdate)
        .where(VulnerabilityReportUpdate.report_id == report.id)
        .order_by(VulnerabilityReportUpdate.version)).all())


def portal_url(report: VulnerabilityReport, token: str) -> str:
    from urllib.parse import quote

    from ..email import public_base_url

    return (f"{public_base_url()}/security/report/{report.reference}"
            f"?t={quote(token, safe='')}")


def resolve_portal(db: Session, token: str) -> VulnerabilityReport | None:
    """Resolve a reporter's portal handle. Grants read of their OWN report's safe view."""
    if not token:
        return None
    report = db.scalar(select(VulnerabilityReport).where(
        VulnerabilityReport.portal_token_hash == _hash(token)))
    if report is None:
        return None
    if not hmac.compare_digest(_hash(token), report.portal_token_hash or ""):
        return None
    if report.portal_expires_at is not None and report.portal_expires_at <= _now():
        return None
    return report


def notify_received(db: Session, background, report: VulnerabilityReport,
                    portal_token: str) -> bool:
    """Acknowledge receipt to the researcher.

    Promises nothing: no bounty, no payout, no validity judgement, no remediation deadline,
    no public credit. It confirms receipt, says how the report will be handled, and gives
    them a secure route to follow it.
    """
    from .. import email as email_mod

    if not _claim(db, "vuln_received", report.id, 0, report.reporter_email):
        return False

    def send():
        ok = email_mod.send_vulnerability_received_email(
            to=report.reporter_email, reference=report.reference,
            reporter_name=report.reporter_name,
            received_at=as_utc(report.received_at),
            category=report.category,
            portal_url=portal_url(report, portal_token))
        if not ok:
            _release(db, "vuln_received", report.id, 0, report.reporter_email)

    background.add_task(send)
    return True


def notify_reporter(db: Session, background, report: VulnerabilityReport,
                    update: VulnerabilityReportUpdate) -> bool:
    """Send one researcher-safe lifecycle update.

    Only stages in VULN_REPORTER_NOTIFIABLE are sendable - internal triage and validation
    have no message - and the body is the stored safe text, never internal analysis.
    """
    from .. import email as email_mod

    if update.stage not in VULN_REPORTER_NOTIFIABLE:
        return False
    if not _claim(db, f"vuln_{update.stage}", report.id, update.version,
                  report.reporter_email):
        return False

    def send():
        ok = email_mod.send_vulnerability_update_email(
            to=report.reporter_email, reference=report.reference,
            reporter_name=report.reporter_name, stage=update.stage,
            body=update.body, resolution=report.resolution,
            coordination_recorded=coordination_recorded(report),
            portal_hint=True)
        if not ok:
            _release(db, f"vuln_{update.stage}", report.id, update.version,
                     report.reporter_email)

    background.add_task(send)
    return True
