"""TRU-001 -> TRU-003 - Trust Center (ZST-EC-001).

The guarantees the spec calls out as most important each have a dedicated test:

    TRU-001  a draft advisory mails nobody            test_draft_advisory_sends_nothing
    TRU-001  severity is recorded, never derived      test_severity_is_authoritative
    TRU-001  affected != "the organization exists"    test_affected_customer_requires_evidence
    TRU-001  updates append, never overwrite          test_update_appends_and_never_rewrites
    TRU-001  remediation only when it exists          test_remediation_requires_a_real_fix
    TRU-001  no manufactured urgency                  test_no_upgrade_immediately_without_policy
    TRU-001  closure is about the advisory            test_closure_does_not_claim_customers_patched
    TRU-002  every access dimension is bound          test_access_is_bound_on_every_dimension
    TRU-002  expiry and revocation actually stop it   test_expiry_and_revocation_stop_access
    TRU-002  the evidence is never emailed            test_evidence_is_never_emailed
    TRU-002  both outcomes are logged                 test_access_is_logged_both_ways
    TRU-003  reporter identity stays protected        test_reporter_identity_is_protected
    TRU-003  no bounty or credit is promised          test_acknowledgement_promises_nothing
    TRU-003  coordination only when real              test_coordination_only_when_recorded

Run with `python test_trust_center.py` (or pytest).
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app.db import SessionLocal
from app.models import (
    ADVISORY_SEVERITIES,
    IMPACT_BASES,
    AdvisoryImpact,
    MarketingSubscription,
    Organization,
    OrganizationSecurityContact,
    SecurityAdvisory,
    SecurityAdvisoryVersion,
    TrustEvidenceAccess,
    TrustEvidenceDocument,
    TrustEvidenceRequest,
    TrustNotice,
    User,
    VulnerabilityReport,
    VulnerabilityReportUpdate,
)
from app.security import hash_password
from app.services import security_comms as sc
from app.services import trust_center as tc
from app.services import vuln_disclosure as vd

PASSWORD = "correct-horse-battery"

PUBLISHED = email_mod.TRU_001_PUBLISHED_SUBJECT
UPDATED = email_mod.TRU_001_UPDATED_SUBJECT
REMEDIATION = email_mod.TRU_001_REMEDIATION_SUBJECT
CLOSED = email_mod.TRU_001_CLOSED_SUBJECT
EV_RECEIVED = email_mod.TRU_002_RECEIVED_SUBJECT
EV_APPROVED = email_mod.TRU_002_APPROVED_SUBJECT
EV_DENIED = email_mod.TRU_002_DENIED_SUBJECT
EV_EXPIRED = email_mod.TRU_002_EXPIRED_SUBJECT
EV_REVOKED = email_mod.TRU_002_REVOKED_SUBJECT
VULN_RECEIVED = email_mod.TRU_003_RECEIVED_SUBJECT

# The document's "contents". If this string ever appears in an outbound message the evidence
# leaked, so every advisory/evidence test greps the whole capture for it.
SECRET_DOCUMENT = ("CONFIDENTIAL SOC 2 REPORT BODY - control CC6.1 exception noted, "
                   "remediation tracked internally as FINDING-4471")
# Exploit detail, planted so the leak assertions have something real to catch.
EXPLOIT = ("PoC: POST /api/x with payload <script>alert(1)</script> bypasses the check at "
           "auth.py:812. Detector SIG-9001, CVE-2027-4242.")


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "payload": json or {}})
        if self.fail:
            import httpx
            raise httpx.ConnectError("simulated Resend outage")
        return _Resp()

    @property
    def subjects(self):
        return [c["payload"]["subject"] for c in self.calls]

    def of(self, subject):
        hits = [c["payload"] for c in self.calls if c["payload"]["subject"] == subject]
        assert hits, f"no message with subject {subject!r}; got {self.subjects}"
        return hits[-1]

    def to(self, subject):
        return sorted(c["payload"]["to"][0].lower()
                      for c in self.calls if c["payload"]["subject"] == subject)

    def count(self, subject):
        return sum(1 for s in self.subjects if s == subject)

    def blob(self):
        return "".join(c["payload"].get("text", "") + c["payload"].get("html", "")
                       for c in self.calls)


_LEAKS: list[str] = []


def _deny(url, headers=None, json=None, timeout=None):
    _LEAKS.append((json or {}).get("subject", "?"))
    raise RuntimeError("outbound email attempted outside a capture context")


email_mod.httpx.post = _deny


def _capture(fail=False):
    cap = Captured(fail=fail)
    return cap, patch.object(email_mod.httpx, "post", cap)


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def _new_email(tag="tru"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    """Two organizations, three security contacts with deliberately different standing."""

    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Trust Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="UTC", region="EU West")
            other = Organization(name=f"Other Co {uuid.uuid4().hex[:6]}", status="active",
                                 timezone="UTC", region="APAC")
            db.add_all([org, other])
            db.flush()
            self.org_id, self.other_org_id = org.id, other.id

            self.admin_email = _new_email("admin")
            self.admin_id = self._u(db, org.id, "super_admin", self.admin_email)
            self.approver_email = _new_email("approver")
            self.approver_id = self._u(db, org.id, "super_admin", self.approver_email)
            self.member_email = _new_email("member")
            self.member_id = self._u(db, org.id, "org_admin", self.member_email)
            self.outsider_email = _new_email("outsider")
            self.outsider_id = self._u(db, other.id, "org_admin", self.outsider_email)
            org.owner_user_id = self.admin_id
            other.owner_user_id = self.outsider_id
            db.commit()

            # A VERIFIED, advisory-subscribed contact at the affected org.
            self.verified_email = self._contact(db, org.id, verified=True)
            # A VERIFIED contact at the OTHER org - subscribed, so it still gets advisories,
            # but must never be told the advisory applies to its organization.
            self.other_verified_email = self._contact(db, other.id, verified=True)
            # A verified contact that opted OUT of advisory routing.
            self.unsubscribed_email = self._contact(db, org.id, verified=True,
                                                    subscribed=False)
            # A PENDING contact: must be excluded from every advisory.
            self.pending_email = self._contact(db, org.id, verified=False)
        finally:
            db.close()

    def _u(self, db, org_id, role, email):
        user = User(org_id=org_id, full_name="Trust Person", role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.flush()
        return user.id

    def _contact(self, db, org_id, *, verified: bool, subscribed: bool = True):
        address = _new_email("seccontact")
        contact = OrganizationSecurityContact(
            org_id=org_id, email=address, display_name="Security", status="pending",
            advisory_subscribed=subscribed, verification_version=0)
        db.add(contact)
        db.commit()
        if verified:
            contact.status = "verified"
            contact.verified_at = _now()
            db.commit()
        return address

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(TrustNotice).delete()
            db.query(TrustEvidenceAccess).delete()
            db.query(TrustEvidenceRequest).delete()
            db.query(TrustEvidenceDocument).delete()
            db.query(VulnerabilityReportUpdate).delete()
            db.query(VulnerabilityReport).delete()
            db.query(AdvisoryImpact).delete()
            db.query(SecurityAdvisoryVersion).delete()
            db.query(SecurityAdvisory).delete()
            db.commit()
            for org_id in (self.org_id, self.other_org_id):
                db.query(OrganizationSecurityContact).filter(
                    OrganizationSecurityContact.org_id == org_id).delete()
                db.query(User).filter(User.org_id == org_id).delete()
            db.commit()
            for org_id in (self.org_id, self.other_org_id):
                org = db.get(Organization, org_id)
                if org is not None:
                    org.owner_user_id = None
                    db.commit()
                    db.delete(org)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


RESULTS = []


def run(fn):
    world = World()
    try:
        fn(world)
        RESULTS.append((fn.__name__, None))
        print(f"ok  {fn.__name__}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((fn.__name__, exc))
        print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    finally:
        world.cleanup()


def _advisory(db, w, **kw):
    kw.setdefault("title", "Session token not rotated on privilege change")
    kw.setdefault("severity", "high")
    kw.setdefault("summary", "A session token was not rotated when a role changed.")
    kw.setdefault("affected_components", ["authentication"])
    kw.setdefault("customer_impact", "A user whose role was reduced could retain access.")
    kw.setdefault("immediate_mitigation", "Ask affected users to sign out and back in.")
    kw.setdefault("affected_versions", "API v1 before 2026-09-01")
    kw.setdefault("created_by", w.admin_id)
    return tc.create_advisory(db, **kw)


def _document(db, w, *, classification="nda_required",
              purposes=("vendor_security_review",), scopes=("organization",)):
    return tc.create_document(
        db, title="SOC 2 Type II report", document_type="soc2_type2", version="2026",
        classification=classification, allowed_purposes=list(purposes),
        allowed_scopes=list(scopes), content=SECRET_DOCUMENT, created_by=w.admin_id)


# ══ TRU-001 ═════════════════════════════════════════════════════════════════════════════

def test_draft_advisory_sends_nothing(w):
    """1 - a draft has no audience, by construction."""
    db = SessionLocal()
    try:
        advisory = _advisory(db, w)
        assert advisory is not None and advisory.status == "draft"
        assert advisory.published_at is None
        assert tc.published_versions(db, advisory) == []
        cap, ctx = _capture()
        with ctx:
            assert tc.notify_advisory(db, _Bg(), advisory) is None
        assert cap.calls == [], "a draft advisory must reach nobody"
        # And publishing requires a named approver, so approval cannot be fabricated.
        assert tc.publish(db, advisory, approved_by=None) is False
        assert advisory.status == "draft"
    finally:
        db.close()


def test_published_advisory_is_persisted_and_versioned(w):
    """2, 8."""
    db = SessionLocal()
    try:
        advisory = _advisory(db, w)
        assert tc.publish(db, advisory, approved_by=w.approver_id) is True
        db.expire_all()
        row = db.get(SecurityAdvisory, advisory.id)
        assert row.status == "published" and row.published_at is not None
        assert row.approved_by == w.approver_id and row.approved_at is not None
        versions = tc.published_versions(db, row)
        assert len(versions) == 1 and versions[0].version == 1
        assert versions[0].version_type == "publication"
        assert row.public_reference.startswith("ZSA-")
    finally:
        db.close()


def test_severity_is_authoritative(w):
    """3 - recorded from the approved list, and never computed."""
    import inspect

    db = SessionLocal()
    try:
        assert _advisory(db, w, severity="catastrophic") is None
        assert _advisory(db, w, severity="9.8") is None
        for level in ADVISORY_SEVERITIES:
            advisory = _advisory(db, w, severity=level)
            assert advisory is not None and advisory.severity == level
            # No CVSS is invented: the field stays null unless a human records one.
            assert advisory.cvss_vector is None

        # Nothing in the email layer derives a severity or a score.
        source = inspect.getsource(email_mod.send_security_advisory_email)
        for banned in ("cvss_score", "score =", "float(", "if severity ==", "* 10"):
            assert banned not in source, f"severity derived in email code: {banned}"
    finally:
        db.close()


def test_affected_scope_is_authoritative(w):
    """4 - an advisory must name a real component, and versions are never guessed."""
    db = SessionLocal()
    try:
        assert _advisory(db, w, affected_components=[]) is None
        assert _advisory(db, w, affected_components=["not_a_component"]) is None
        advisory = _advisory(db, w, affected_components=["authentication", "nonsense"])
        assert advisory.affected_components == ["authentication"]

        # No affected_versions recorded -> the message defers instead of claiming "all".
        bare = _advisory(db, w, affected_versions=None)
        tc.publish(db, bare, approved_by=w.approver_id)
        cap, ctx = _capture()
        with ctx:
            tc.notify_advisory(db, _Bg(), bare)
        text = cap.of(PUBLISHED.format(reference=bare.public_reference))["text"]
        assert "Stated in the advisory" in text
        assert "all versions" not in text.lower()
    finally:
        db.close()


def test_subscribed_verified_contacts_are_notified(w):
    """5, 6 - and nobody else."""
    db = SessionLocal()
    try:
        advisory = _advisory(db, w)
        tc.publish(db, advisory, approved_by=w.approver_id)
        cap, ctx = _capture()
        with ctx:
            kind = tc.notify_advisory(db, _Bg(), advisory)
        assert kind == "advisory_published"
        got = cap.to(PUBLISHED.format(reference=advisory.public_reference))
        assert w.verified_email.lower() in got
        assert w.other_verified_email.lower() in got
        # 6 - a PENDING contact and an advisory-unsubscribed contact are excluded.
        assert w.pending_email.lower() not in got, "unverified contact was mailed"
        assert w.unsubscribed_email.lower() not in got, "unsubscribed contact was mailed"
        # And no ordinary member or admin: a security contact is the audience, not the org.
        assert w.member_email.lower() not in got
        assert w.admin_email.lower() not in got
    finally:
        db.close()


def test_affected_customer_requires_evidence(w):
    """7 - the critical requirement. Existence is never impact."""
    db = SessionLocal()
    try:
        advisory = _advisory(db, w)
        tc.publish(db, advisory, approved_by=w.approver_id)
        # No impact mapping yet: nobody is told the advisory applies to them.
        assert tc.affected_organizations(db, advisory) == []
        cap, ctx = _capture()
        with ctx:
            tc.notify_advisory(db, _Bg(), advisory)
        subject = PUBLISHED.format(reference=advisory.public_reference)
        for call in [c["payload"] for c in cap.calls if c["payload"]["subject"] == subject]:
            assert "our records show your organization is affected" not in call["text"], (
                "an organization was called affected with no impact record")
            assert "Not determined" in call["text"]

        # A bad basis is refused, and so is an unknown organization.
        assert tc.record_impact(db, advisory, org_id=w.org_id,
                                basis="organization_exists") is None
        assert tc.record_impact(db, advisory, org_id=uuid.uuid4(),
                                basis="configuration_observed") is None

        # Now record real impact for ONE org only.
        impact = tc.record_impact(db, advisory, org_id=w.org_id,
                                  basis="configuration_observed",
                                  evidence_note="role-change rotation setting recorded",
                                  recorded_by=w.admin_id)
        assert impact is not None and impact.basis in IMPACT_BASES
        reasons = dict(tc.advisory_recipients(db, advisory))
        assert reasons[w.verified_email.lower()] == "affected_customer"
        assert reasons[w.other_verified_email.lower()] == "security_contact", (
            "an unaffected organization's contact must not be told it is affected")
    finally:
        db.close()


def test_update_appends_and_never_rewrites(w):
    """8 - nothing published is ever overwritten."""
    import inspect

    db = SessionLocal()
    try:
        advisory = _advisory(db, w)
        tc.publish(db, advisory, approved_by=w.approver_id)
        original = tc.published_versions(db, advisory)[0]
        original_summary, original_severity = original.summary, original.severity

        # A cosmetic no-op change sends nothing.
        assert tc.publish_update(db, advisory, changes={"summary": original_summary},
                                 change_summary="typo") is None
        cap0, ctx0 = _capture()
        with ctx0:
            pass
        assert cap0.calls == []

        version = tc.publish_update(
            db, advisory, changes={"severity": "critical",
                                   "summary": "Wider than first assessed."},
            change_summary="Severity raised to critical after further analysis.",
            published_by=w.approver_id)
        assert version is not None and version.version == 2
        assert version.version_type == "update"
        assert sorted(version.changed_fields) == ["severity", "summary"]

        history = tc.published_versions(db, advisory)
        assert len(history) == 2
        # The first version is byte-identical to what was published.
        assert history[0].summary == original_summary
        assert history[0].severity == original_severity
        assert advisory.status == "updated"

        # Structural: the service has no UPDATE path into a version row.
        source = inspect.getsource(tc)
        for banned in ("version.summary =", "row.summary =", "existing.summary =",
                       "version.severity ="):
            assert banned not in source, f"published version mutated via {banned!r}"

        cap, ctx = _capture()
        with ctx:
            assert tc.notify_advisory(db, _Bg(), advisory,
                                      version=version) == "advisory_updated"
        text = cap.of(UPDATED.format(reference=advisory.public_reference))["text"]
        assert "Severity raised to critical" in text
        assert "Severity" in text and "Summary" in text
        assert "remain published" in text, (
            "the message must point at the preserved earlier versions")
    finally:
        db.close()


def test_remediation_requires_a_real_fix(w):
    """9, 10, 11."""
    db = SessionLocal()
    try:
        advisory = _advisory(db, w)
        # 9 - cannot claim remediation on a draft, nor with no steps.
        assert tc.mark_remediation_available(db, advisory,
                                             remediation_steps="x") is False
        tc.publish(db, advisory, approved_by=w.approver_id)
        assert tc.mark_remediation_available(db, advisory, remediation_steps="") is False
        assert advisory.status == "published"

        assert tc.mark_remediation_available(
            db, advisory, fixed_version="API v1 2026-09-02",
            remediation_steps="Upgrade to the 2026-09-02 build and restart your workers.",
            published_by=w.approver_id) is True
        assert advisory.status == "remediation_available"
        assert advisory.remediation_available_at is not None
        # 11 - no deadline supplied, so none exists.
        assert advisory.remediation_deadline is None

        cap, ctx = _capture()
        with ctx:
            assert tc.notify_advisory(db, _Bg(), advisory) == "advisory_remediation"
        text = cap.of(REMEDIATION.format(reference=advisory.public_reference))["text"]
        assert "2026-09-02" in text
        assert "No deadline has been set" in text
        # 10 - no workaround was recorded, so the message says so plainly.
        assert "No workaround is available." in text
    finally:
        db.close()


def test_workaround_claims_are_truthful(w):
    """10 - a claimed workaround must say what it is."""
    db = SessionLocal()
    try:
        # Claiming one without describing it is refused outright.
        assert _advisory(db, w, workaround_available=True,
                         workaround_summary=None) is None
        assert _advisory(db, w, workaround_available=True,
                         workaround_summary="   ") is None

        advisory = _advisory(db, w, workaround_available=True,
                             workaround_summary="Disable role caching in Settings.")
        tc.publish(db, advisory, approved_by=w.approver_id)
        cap, ctx = _capture()
        with ctx:
            tc.notify_advisory(db, _Bg(), advisory)
        text = cap.of(PUBLISHED.format(reference=advisory.public_reference))["text"]
        assert "Disable role caching" in text
        assert "No workaround is available." not in text
    finally:
        db.close()


def test_no_upgrade_immediately_without_policy(w):
    """The imperative sentence is gated on a recorded policy flag, not on severity."""
    db = SessionLocal()
    try:
        advisory = _advisory(db, w, severity="critical")
        tc.publish(db, advisory, approved_by=w.approver_id)
        tc.mark_remediation_available(
            db, advisory, fixed_version="v2",
            remediation_steps="Upgrade to v2.", action_mandatory=False,
            published_by=w.approver_id)
        cap, ctx = _capture()
        with ctx:
            tc.notify_advisory(db, _Bg(), advisory)
        text = cap.of(REMEDIATION.format(reference=advisory.public_reference))["text"]
        # Critical severity alone does not license a mandate.
        assert "This update is required" not in text
        assert "normal change process" in text

        # With the policy flag AND a real deadline, the wording changes - and only then.
        other = _advisory(db, w, severity="low")
        tc.publish(db, other, approved_by=w.approver_id)
        tc.mark_remediation_available(
            db, other, fixed_version="v2", remediation_steps="Upgrade to v2.",
            remediation_deadline=_now() + timedelta(days=14), action_mandatory=True,
            published_by=w.approver_id)
        cap2, ctx2 = _capture()
        with ctx2:
            tc.notify_advisory(db, _Bg(), other)
        text2 = cap2.of(REMEDIATION.format(reference=other.public_reference))["text"]
        assert "This update is required" in text2
        assert "Please complete it by" in text2
    finally:
        db.close()


def test_closure_does_not_claim_customers_patched(w):
    """12 - closure is a statement about the advisory."""
    db = SessionLocal()
    try:
        advisory = _advisory(db, w)
        # 12 - a draft cannot be closed, and closure needs a note.
        assert tc.close_advisory(db, advisory, closure_note="") is False
        tc.publish(db, advisory, approved_by=w.approver_id)
        assert tc.close_advisory(db, advisory,
                                 closure_note="All supported versions carry the fix.",
                                 published_by=w.approver_id) is True
        assert advisory.status == "closed" and advisory.closed_at is not None
        assert tc.close_advisory(db, advisory, closure_note="again") is False

        cap, ctx = _capture()
        with ctx:
            assert tc.notify_advisory(db, _Bg(), advisory) == "advisory_closed"
        text = cap.of(CLOSED.format(reference=advisory.public_reference))["text"]
        assert "does not confirm that the update has been applied" in text
        for overclaim in ("all customers have", "everyone has remediated",
                          "your organization is now secure"):
            assert overclaim not in text.lower(), overclaim
    finally:
        db.close()


def test_no_exploit_detail_leaks(w):
    """13 - and no internal correlation key either."""
    import inspect

    db = SessionLocal()
    try:
        internal_id = uuid.uuid4()
        report, _t = vd.submit(
            db, reporter_email=_new_email("researcher"), title="Token rotation bug",
            category="authentication", description=EXPLOIT, reproduction=EXPLOIT,
            reporter_name="Alex Researcher")
        advisory = _advisory(db, w, internal_incident_id=internal_id,
                             vulnerability_report_id=report.id)
        tc.publish(db, advisory, approved_by=w.approver_id)
        cap, ctx = _capture()
        with ctx:
            tc.notify_advisory(db, _Bg(), advisory)
        blob = cap.blob()
        for leak in ("CVE-2027-4242", "SIG-9001", "<script>", "auth.py:812", "PoC",
                     str(internal_id), str(report.id), report.reference,
                     "Alex Researcher", report.reporter_email):
            assert leak not in blob, f"leaked in an advisory: {leak}"

        # Structural: the sender has no parameter these could arrive through.
        params = inspect.signature(email_mod.send_security_advisory_email).parameters
        for banned in ("reporter", "reporter_name", "reporter_email", "reproduction",
                       "evidence", "internal_incident_id", "vulnerability_report_id",
                       "detail", "commander", "poc"):
            assert banned not in params, f"advisory sender accepts {banned}"
    finally:
        db.close()


def test_advisory_dedup_and_provider_failure(w):
    """14, plus: a delivery failure must not unpublish an advisory."""
    db = SessionLocal()
    try:
        advisory = _advisory(db, w)
        tc.publish(db, advisory, approved_by=w.approver_id)
        cap, ctx = _capture()
        with ctx:
            tc.notify_advisory(db, _Bg(), advisory)
            first = len(cap.calls)
            assert tc.notify_advisory(db, _Bg(), advisory) is None
        assert len(cap.calls) == first, "a second call must add no further messages"

        other = _advisory(db, w, title="Second advisory")
        tc.publish(db, other, approved_by=w.approver_id)
        capf, ctxf = _capture(fail=True)
        with ctxf:
            tc.notify_advisory(db, _Bg(), other)
        db.expire_all()
        row = db.get(SecurityAdvisory, other.id)
        assert row.status == "published" and row.published_at is not None, (
            "a mail failure must not unpublish an advisory")
        # The claim was released, so a retry is still possible.
        cap2, ctx2 = _capture()
        with ctx2:
            assert tc.notify_advisory(db, _Bg(), row) == "advisory_published"
        assert cap2.calls, "the retry should send"
    finally:
        db.close()


def test_advisory_is_class_a_and_not_marketing(w):
    from app.services import notifications

    assert notifications.message_class("TRU-001") == notifications.CLASS_A
    assert notifications.is_mandatory("TRU-001") is True
    assert notifications.is_marketing("TRU-001") is False
    # The advisory sender takes no unsubscribe link: there is nothing to unsubscribe from.
    import inspect
    params = inspect.signature(email_mod.send_security_advisory_email).parameters
    assert "unsubscribe_url" not in params
    assert "manage_url" not in params


# ══ TRU-002 ═════════════════════════════════════════════════════════════════════════════

def test_evidence_request_is_persisted_and_qualified(w):
    """1, 2, 9."""
    db = SessionLocal()
    try:
        doc = _document(db, w)
        assert doc is not None and doc.status == "available"
        # A document with no allowed purpose/scope fails closed at creation.
        assert tc.create_document(
            db, title="x", document_type="soc2_type2", version="1",
            classification="nda_required", allowed_purposes=[], allowed_scopes=[],
            content="x") is None

        # 2 - a member of a real organization qualifies as CHECKED.
        basis, verified = tc.qualify(db, requester_email=w.member_email,
                                     claimed_basis="customer_organization")
        assert basis == "customer_organization" and verified is True
        # A stranger claiming the same basis is NOT verified.
        basis, verified = tc.qualify(db, requester_email=_new_email("stranger"),
                                     claimed_basis="customer_organization")
        assert verified is False
        # An unknown basis is refused outright.
        assert tc.qualify(db, requester_email=w.member_email,
                          claimed_basis="i_am_important") == (None, False)
        # A basis nothing can check is accepted as a CLAIM, never auto-verified.
        basis, verified = tc.qualify(db, requester_email=w.member_email,
                                     claimed_basis="authorized_auditor")
        assert basis == "authorized_auditor" and verified is False

        request = tc.create_request(
            db, requester_email=w.member_email, document=doc,
            purpose="vendor_security_review", scope="organization",
            company_name="Acme")
        assert request is not None and request.reference.startswith("TEV-")
        # 2 - a verified qualification still goes to REVIEW. Qualification != approval.
        assert request.status == "under_review"
        assert request.access_token_hash is None, "no token before approval"

        # 9 - a purpose or scope the document does not allow is refused.
        assert tc.create_request(db, requester_email=w.member_email, document=doc,
                                 purpose="contract_negotiation",
                                 scope="organization") is None
        assert tc.create_request(db, requester_email=w.member_email, document=doc,
                                 purpose="vendor_security_review",
                                 scope="annual_review") is None
        cap, ctx = _capture()
        with ctx:
            assert tc.notify_request_received(db, _Bg(), request) is True
        text = cap.of(EV_RECEIVED)["text"]
        assert request.reference in text
        assert SECRET_DOCUMENT not in cap.blob()
    finally:
        db.close()


def test_access_is_bound_on_every_dimension(w):
    """3, 4, 5, 6 - the core of TRU-002."""
    db = SessionLocal()
    try:
        doc = _document(db, w)
        other_doc = tc.create_document(
            db, title="ISO cert", document_type="iso27001_certificate", version="2026",
            classification="customer_confidential",
            allowed_purposes=["vendor_security_review"], allowed_scopes=["organization"],
            content="ISO BODY", created_by=w.admin_id)
        request = tc.create_request(
            db, requester_email=w.member_email, document=doc,
            purpose="vendor_security_review", scope="organization")
        # Approval requires a named approver.
        assert tc.approve_request(db, request, approved_by=None) is None
        token = tc.approve_request(db, request, approved_by=w.approver_id)
        assert token and len(token) >= 40
        assert request.access_token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert request.access_expires_at is not None

        # 3 - recipient binding.
        found, outcome = tc.authorize_access(db, token=token,
                                             requester_email=w.outsider_email)
        assert found is None and outcome == "recipient_mismatch"
        # 6 - document binding.
        found, outcome = tc.authorize_access(db, token=token,
                                             document_id=other_doc.id)
        assert found is None and outcome == "document_mismatch"
        # 4 - purpose binding.
        found, outcome = tc.authorize_access(db, token=token,
                                             purpose="contract_negotiation")
        assert found is None and outcome == "purpose_mismatch"
        # A wrong token is refused.
        found, outcome = tc.authorize_access(db, token="not-a-real-token")
        assert found is None and outcome == "invalid_token"

        # Correct on every dimension.
        found, outcome = tc.authorize_access(
            db, token=token, requester_email=w.member_email, document_id=doc.id,
            purpose="vendor_security_review")
        assert outcome == "authorized" and found is not None

        # 5 - scope binding is re-checked against the DOCUMENT on every read, so narrowing
        # the document's allow-list kills an approval already in flight.
        doc.allowed_scopes = ["annual_review"]
        db.commit()
        found, outcome = tc.authorize_access(db, token=token)
        assert found is None and outcome == "scope_not_allowed"
    finally:
        db.close()


def test_expiry_and_revocation_stop_access(w):
    """7, 8."""
    db = SessionLocal()
    try:
        doc = _document(db, w)
        request = tc.create_request(db, requester_email=w.member_email, document=doc,
                                    purpose="vendor_security_review", scope="organization")
        token = tc.approve_request(db, request, approved_by=w.approver_id)
        found, outcome = tc.authorize_access(db, token=token)
        assert outcome == "authorized"

        # 7 - expiry. Backdate the window and try again.
        request.access_expires_at = _now() - timedelta(minutes=1)
        db.commit()
        found, outcome = tc.authorize_access(db, token=token)
        assert found is None and outcome == "expired"
        db.expire_all()
        row = db.get(TrustEvidenceRequest, request.id)
        assert row.status == "expired"
        # The token hash is CLEARED, so the old link cannot be resurrected.
        assert row.access_token_hash is None
        found, outcome = tc.authorize_access(db, token=token)
        assert found is None and outcome == "invalid_token"

        # 8 - revocation is immediate.
        second = tc.create_request(db, requester_email=w.member_email, document=doc,
                                    purpose="vendor_security_review", scope="organization")
        token2 = tc.approve_request(db, second, approved_by=w.approver_id)
        assert tc.authorize_access(db, token=token2)[1] == "authorized"
        assert tc.revoke_access(db, second, revoked_by=w.admin_id,
                                reason="Engagement ended") is True
        assert second.access_token_hash is None
        found, outcome = tc.authorize_access(db, token=token2)
        assert found is None and outcome == "invalid_token"

        cap, ctx = _capture()
        with ctx:
            assert tc.notify_access_ended(db, _Bg(), second) is True
        assert cap.count(EV_REVOKED) == 1
        assert SECRET_DOCUMENT not in cap.blob()
    finally:
        db.close()


def test_evidence_is_never_emailed(w):
    """13 - and the document is not public."""
    import inspect

    db = SessionLocal()
    try:
        doc = _document(db, w)
        # 10 - the contents live in private storage, not in a column.
        assert doc.storage_reference and doc.storage_reference.startswith("trust-evidence/")
        assert SECRET_DOCUMENT not in (doc.title + doc.document_type + doc.version)
        assert tc.load_document(doc) == SECRET_DOCUMENT

        request = tc.create_request(db, requester_email=w.member_email, document=doc,
                                    purpose="vendor_security_review", scope="organization")
        token = tc.approve_request(db, request, approved_by=w.approver_id)
        cap, ctx = _capture()
        with ctx:
            tc.notify_request_received(db, _Bg(), request)
            assert tc.notify_access_approved(db, _Bg(), request, token) is True
        blob = cap.blob()
        assert SECRET_DOCUMENT not in blob, "the evidence body was emailed"
        assert doc.storage_reference not in blob, "the storage key was emailed"
        # The link IS present, and it is bound and expiring.
        approved = cap.of(EV_APPROVED)
        assert token in approved["text"]
        assert "expires" in approved["text"].lower()
        assert "do not forward" in approved["text"].lower()
        # Every message carries the brand logo (email._logo_attachment). What matters is
        # that NO attachment carries the document: the assertion is on the content, not on
        # the presence of an attachments list.
        for attachment in approved.get("attachments") or []:
            assert "logo" in (attachment.get("filename") or "").lower(), (
                f"unexpected attachment on an evidence email: {attachment.get('filename')}")
            assert SECRET_DOCUMENT not in str(attachment.get("content") or "")

        # Structural: no evidence sender takes content or an attachment.
        for name in ("send_trust_access_approved_email", "send_trust_request_received_email",
                     "send_trust_request_decided_email"):
            params = inspect.signature(getattr(email_mod, name)).parameters
            for banned in ("content", "body_bytes", "attachment", "attachments", "file",
                           "storage_reference", "document_body"):
                assert banned not in params, f"{name}({banned})"
    finally:
        db.close()


def test_access_is_logged_both_ways(w):
    """12 - a refused attempt is the interesting half of an access log."""
    db = SessionLocal()
    try:
        doc = _document(db, w)
        request = tc.create_request(db, requester_email=w.member_email, document=doc,
                                    purpose="vendor_security_review", scope="organization")
        token = tc.approve_request(db, request, approved_by=w.approver_id)
        tc.authorize_access(db, token=token, client="curl/8", ip="203.0.113.5")
        tc.authorize_access(db, token=token, requester_email=w.outsider_email)
        entries = tc.access_log(db, request)
        outcomes = [e.outcome for e in entries]
        assert "authorized" in outcomes
        assert "recipient_mismatch" in outcomes, "a refusal must be logged too"
        entry = entries[0]
        assert entry.requester_email == w.member_email.lower()
        assert str(entry.document_id) == str(doc.id)
        assert entry.purpose == "vendor_security_review" and entry.at is not None
        # The log records the decision, never the document.
        for column in TrustEvidenceAccess.__table__.columns:
            value = getattr(entry, column.name)
            if isinstance(value, str):
                assert SECRET_DOCUMENT not in value
    finally:
        db.close()


def test_document_is_not_publicly_reachable(w):
    """10, 11 - the public API exposes metadata only."""
    db = SessionLocal()
    try:
        _document(db, w)
    finally:
        db.close()

    client = TestClient(m.app)
    r = client.get("/api/trust")
    assert r.status_code == 200, r.text
    assert SECRET_DOCUMENT not in r.text
    assert "storage_reference" not in r.text
    docs = r.json()["documents"]
    assert docs and docs[0]["requires_approval"] is True
    # There is no unauthenticated download: a bare reference without a token is refused.
    bad = client.get("/api/trust/evidence/TEV-2026-XXXXXX?t=nope")
    assert bad.status_code == 403, bad.text
    assert SECRET_DOCUMENT not in bad.text


def test_evidence_dedup(w):
    """14."""
    db = SessionLocal()
    try:
        doc = _document(db, w)
        request = tc.create_request(db, requester_email=w.member_email, document=doc,
                                    purpose="vendor_security_review", scope="organization")
        cap, ctx = _capture()
        with ctx:
            assert tc.notify_request_received(db, _Bg(), request) is True
            n = len(cap.calls)
            assert tc.notify_request_received(db, _Bg(), request) is False
        assert len(cap.calls) == n

        # A denial is its own transition and is NOT swallowed by the received notice.
        assert tc.deny_request(db, request, denied_by=w.admin_id,
                               decision_note="We share this report under NDA only.") is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert tc.notify_request_denied(db, _Bg(), request) is True
            n2 = len(cap2.calls)
            assert tc.notify_request_denied(db, _Bg(), request) is False
        assert len(cap2.calls) == n2
        assert "NDA only" in cap2.of(EV_DENIED)["text"]
        # A denial mints no token.
        assert request.access_token_hash is None
    finally:
        db.close()


def test_evidence_sweep_expires_access(w):
    db = SessionLocal()
    try:
        doc = _document(db, w)
        request = tc.create_request(db, requester_email=w.member_email, document=doc,
                                    purpose="vendor_security_review", scope="organization")
        tc.approve_request(db, request, approved_by=w.approver_id)
        request.access_expires_at = _now() - timedelta(minutes=5)
        db.commit()
        cap, ctx = _capture()
        with ctx:
            counts = tc.sweep(db, _Bg())
        assert counts["evidence_expired"] == 1
        assert cap.count(EV_EXPIRED) == 1
        db.expire_all()
        assert db.get(TrustEvidenceRequest, request.id).status == "expired"

        import inspect
        from app.services import event_planning
        assert "trust_center" in inspect.getsource(event_planning.sweep)
        assert not hasattr(tc, "run_trust_sweeper"), (
            "TRU must ride the leader-elected ticker, not add its own")
    finally:
        db.close()


# ══ TRU-003 ═════════════════════════════════════════════════════════════════════════════

def _report(db, **kw):
    kw.setdefault("reporter_email", _new_email("researcher"))
    kw.setdefault("title", "Token not rotated on role change")
    kw.setdefault("category", "authentication")
    kw.setdefault("description", EXPLOIT)
    kw.setdefault("reproduction", EXPLOIT)
    kw.setdefault("reporter_name", "Alex Researcher")
    return vd.submit(db, **kw)


def test_secure_report_is_persisted(w):
    """1, 11 - and it is not a support ticket."""
    from app.models import SupportTicket

    db = SessionLocal()
    try:
        before = db.query(SupportTicket).count()
        report, token = _report(db, evidence="screenshot bytes here")
        assert report is not None and report.reference.startswith("ZVR-")
        assert report.status == "received" and token and len(token) >= 40
        assert report.portal_token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert report.portal_expires_at > _now()
        # 11 - evidence goes to private storage, not a column.
        assert report.evidence_reference.startswith("vuln-evidence/")
        # It did NOT become a support ticket, which org admins and support staff can read.
        assert db.query(SupportTicket).count() == before

        # Bad category / bad address are refused.
        assert vd.submit(db, reporter_email="nope", title="t", category="authentication",
                         description="x" * 30)[0] is None
        assert vd.submit(db, reporter_email=_new_email(), title="t",
                         category="not_a_category", description="x" * 30)[0] is None
    finally:
        db.close()


def test_acknowledgement_promises_nothing(w):
    """2, 5, 6."""
    db = SessionLocal()
    try:
        report, token = _report(db)
        cap, ctx = _capture()
        with ctx:
            assert vd.notify_received(db, _Bg(), report, token) is True
        payload = cap.of(VULN_RECEIVED)
        text = payload["text"]
        assert report.reference in text
        assert token in text, "the reporter needs their portal handle"
        # 5, 6 - nothing is promised. The assertions target PROMISES, not the words: the
        # copy deliberately says "we do not operate a paid bug bounty programme", so a bare
        # search for "bounty" would flag the very disclaimer that makes this correct.
        low = text.lower()
        for promise in ("reward", "payout", "swag", "hall of fame",
                        "we will credit you", "we will fix this within",
                        "eligible for a bounty", "bounty award", "valid vulnerability",
                        "confirmed vulnerability", "we will pay"):
            assert promise not in low, f"promised: {promise}"
        assert "do not operate a paid bug bounty" in low
        assert "not an assessment of the report's validity" in text.lower()
        # Safe-handling guidance, including "do not send us secrets".
        assert "passwords, API keys or private keys" in text
    finally:
        db.close()


def test_reporter_identity_is_protected(w):
    """3, 4 - the critical requirement."""
    import inspect

    db = SessionLocal()
    try:
        report, _t = _report(db)
        super_admin = db.get(User, w.admin_id)
        org_admin = db.get(User, w.member_id)
        outsider = db.get(User, w.outsider_id)

        # A security-role holder can read it.
        identity = vd.reporter_identity(report, actor=super_admin)
        assert identity is not None and identity["email"] == report.reporter_email

        # 4 - an ORDINARY admin cannot, including at an affected customer.
        assert vd.reporter_identity(report, actor=org_admin) is None
        assert vd.reporter_identity(report, actor=outsider) is None
        assert vd.reporter_identity(report, actor=None) is None
        # Nor can they read the evidence.
        assert vd.load_evidence(report, actor=org_admin) is None

        # The public projection simply has no identity field.
        public = vd.public_projection(report)
        assert "reporter_email" not in public and "reporter_name" not in public
        assert report.reporter_email not in str(public)
        assert "Alex Researcher" not in str(public)
        # Nor the reproduction detail or evidence.
        assert "description" not in public and "reproduction" not in public
        assert "evidence_reference" not in public

        # 3 - no email template anywhere accepts a reporter identity.
        for name in [n for n in dir(email_mod) if n.startswith("send_")]:
            params = inspect.signature(getattr(email_mod, name)).parameters
            for banned in ("reporter_email", "reporter_identity", "researcher_email"):
                assert banned not in params, f"{name}({banned})"
    finally:
        db.close()


def test_reporter_portal_serves_only_the_safe_view(w):
    db = SessionLocal()
    try:
        report, token = _report(db)
        vd.acknowledge(db, report)
        reference = report.reference
    finally:
        db.close()

    client = TestClient(m.app)
    r = client.get(f"/api/trust/security/reports/{reference}?t={token}")
    assert r.status_code == 200, r.text
    body = r.text
    for leak in ("CVE-2027-4242", "SIG-9001", "auth.py:812", "<script>",
                 "Alex Researcher", "reporter_email", "evidence_reference"):
        assert leak not in body, f"the reporter portal exposed {leak}"
    assert r.json()["coordinated_disclosure_recorded"] is False
    # A bad handle, or the right handle against the wrong report, is refused.
    assert client.get(f"/api/trust/security/reports/{reference}?t=nope").status_code == 403
    assert client.get(f"/api/trust/security/reports/ZVR-2026-NOPE?t={token}"
                      ).status_code == 403


def test_researcher_updates_are_safe_and_staged(w):
    """7 - and internal triage is silent."""
    db = SessionLocal()
    try:
        report, token = _report(db)
        assert vd.acknowledge(db, report) is True
        history = vd.reporter_history(db, report)
        assert len(history) == 1 and history[0].stage == "acknowledged"
        cap, ctx = _capture()
        with ctx:
            assert vd.notify_reporter(db, _Bg(), report, history[-1]) is True

        # Triage and validation are INTERNAL: they append nothing and send nothing.
        assert vd.triage(db, report, triaged_by=w.admin_id) is True
        assert vd.start_validation(db, report) is True
        assert len(vd.reporter_history(db, report)) == 1, (
            "internal triage must not appear in the researcher's history")

        row = vd.request_clarification(
            db, report, question="Which account did you use for step 3?")
        assert row is not None and row.stage == "clarification_needed"
        cap2, ctx2 = _capture()
        with ctx2:
            assert vd.notify_reporter(db, _Bg(), report, row) is True
        blob = cap2.blob()
        assert "step 3" in blob
        for leak in ("CVE-2027-4242", "SIG-9001", "auth.py:812"):
            assert leak not in blob, leak
    finally:
        db.close()


def test_coordination_only_when_recorded(w):
    """8 - no invented embargo, date or credit."""
    db = SessionLocal()
    try:
        report, _t = _report(db)
        vd.acknowledge(db, report)
        # Cannot coordinate before triage.
        assert vd.start_coordination(db, report) is False
        vd.triage(db, report, triaged_by=w.admin_id)
        assert vd.start_coordination(db, report) is True
        # Nothing was supplied, so nothing is recorded.
        assert report.disclosure_date is None and report.embargo_until is None
        assert vd.coordination_recorded(report) is False

        cap, ctx = _capture()
        with ctx:
            vd.notify_reporter(db, _Bg(), report, vd.reporter_history(db, report)[-1])
        text = cap.blob()
        assert "We have not set a disclosure timeline" in text
        for invented in ("embargo until", "90 days", "disclosure date of",
                         "we will credit"):
            assert invented not in text.lower(), invented
    finally:
        db.close()


def test_remediation_and_closure_are_truthful(w):
    """9, 10."""
    db = SessionLocal()
    try:
        report, _t = _report(db)
        # 9 - remediation is refused from every state before coordination.
        assert vd.mark_remediated(db, report, safe_update="fixed") is False
        vd.acknowledge(db, report)
        assert vd.mark_remediated(db, report, safe_update="fixed") is False
        vd.triage(db, report, triaged_by=w.admin_id)
        assert vd.mark_remediated(db, report, safe_update="fixed") is False
        vd.start_coordination(db, report)
        assert vd.mark_remediated(db, report, safe_update="") is False
        assert vd.mark_remediated(
            db, report,
            safe_update="We rotated sessions on role change and shipped it today.") is True
        assert report.remediated_at is not None and report.resolution == "remediated"

        # 10 - closure carries a RECORDED resolution, and an unknown one is refused.
        assert vd.close_report(db, report, resolution="fixed_it",
                               safe_update="done") is False
        assert vd.close_report(db, report, resolution="remediated",
                               safe_update="Thanks again for the report.") is True
        assert report.status == "closed" and report.closed_at is not None
        assert vd.close_report(db, report, resolution="remediated",
                               safe_update="again") is False

        # A "not reproducible" closure is an outcome, not a verdict on the researcher.
        other, _t2 = _report(db)
        vd.acknowledge(db, other)
        vd.triage(db, other, triaged_by=w.admin_id)
        vd.start_coordination(db, other)
        assert vd.close_report(
            db, other, resolution="not_reproducible",
            safe_update="We could not reproduce this against current builds.") is True
        cap, ctx = _capture()
        with ctx:
            vd.notify_reporter(db, _Bg(), other, vd.reporter_history(db, other)[-1])
        text = cap.blob()
        assert "could not reproduce" in text
        for insult in ("invalid report", "spam", "wasted", "not a real"):
            assert insult not in text.lower(), insult
    finally:
        db.close()


def test_reporting_creates_no_marketing_enrolment(w):
    """12 - the critical separation. A researcher is not a lead."""
    db = SessionLocal()
    try:
        report, token = _report(db)
        vd.acknowledge(db, report)
        cap, ctx = _capture()
        with ctx:
            vd.notify_received(db, _Bg(), report, token)
            vd.notify_reporter(db, _Bg(), report, vd.reporter_history(db, report)[-1])
        assert db.query(MarketingSubscription).filter(
            MarketingSubscription.email == report.reporter_email).count() == 0, (
            "reporting a vulnerability must not create marketing consent")
        # And the messages carry no marketing footer or unsubscribe.
        blob = cap.blob().lower()
        for term in ("unsubscribe", "product updates", "newsletter", "webinar"):
            assert term not in blob, f"a security message carried {term}"
    finally:
        db.close()


def test_vuln_dedup(w):
    """13."""
    db = SessionLocal()
    try:
        report, token = _report(db)
        cap, ctx = _capture()
        with ctx:
            assert vd.notify_received(db, _Bg(), report, token) is True
            n = len(cap.calls)
            assert vd.notify_received(db, _Bg(), report, token) is False
        assert len(cap.calls) == n

        vd.acknowledge(db, report)
        update = vd.reporter_history(db, report)[-1]
        cap2, ctx2 = _capture()
        with ctx2:
            assert vd.notify_reporter(db, _Bg(), report, update) is True
            n2 = len(cap2.calls)
            assert vd.notify_reporter(db, _Bg(), report, update) is False
        assert len(cap2.calls) == n2

        constraint = next(c for c in TrustNotice.__table__.constraints
                          if c.__class__.__name__ == "UniqueConstraint")
        assert {c.name for c in constraint.columns} == {
            "kind", "subject_type", "subject_id", "version", "recipient"}
    finally:
        db.close()


def test_report_form_refuses_credentials(w):
    """The form cannot ask for a secret, and refuses one that arrives anyway."""
    client = TestClient(m.app)
    from app.schemas.trust import VulnerabilityReportIn

    for banned in ("password", "api_key", "secret", "private_key", "token"):
        assert banned not in VulnerabilityReportIn.model_fields, banned

    cap, ctx = _capture()
    with ctx:
        r = client.post("/api/trust/security/reports", json={
            "reporter_email": _new_email("researcher"),
            "title": "Leaked key in response",
            "category": "data_exposure",
            "description": "The response includes api_key=sk_live_abc123 for other users."})
    assert r.status_code == 422, r.text
    assert "passwords, API keys or private keys" in r.text
    assert cap.calls == [], "a refused report must send nothing"

    db = SessionLocal()
    try:
        assert db.query(VulnerabilityReport).filter(
            VulnerabilityReport.title == "Leaked key in response").count() == 0, (
            "a report containing a credential must not be stored")
    finally:
        db.close()


def test_public_report_endpoint_is_unauthenticated(w):
    """A researcher has no account here."""
    client = TestClient(m.app)
    address = _new_email("researcher")
    cap, ctx = _capture()
    with ctx:
        r = client.post("/api/trust/security/reports", json={
            "reporter_email": address, "title": "Rate limit bypass",
            "category": "configuration",
            "description": "The per-IP limit can be bypassed with a header, details follow."})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["reference"].startswith("ZVR-")
    assert body["bounty"] is False and body["public_credit"] is False
    assert cap.count(VULN_RECEIVED) == 1
    db = SessionLocal()
    try:
        row = db.scalar(__import__("sqlalchemy").select(VulnerabilityReport).where(
            VulnerabilityReport.reporter_email == address))
        assert row is not None
        db.query(VulnerabilityReportUpdate).filter(
            VulnerabilityReportUpdate.report_id == row.id).delete()
        db.query(TrustNotice).filter(TrustNotice.subject_id == row.id).delete()
        db.delete(row)
        db.commit()
    finally:
        db.close()


def test_existing_sec_families_still_work(w):
    """Do not weaken SEC-006: the security-contact flow this reuses must be intact."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        contact, raw = sc.nominate_contact(db, org, email=_new_email("newcontact"),
                                            display_name="New Contact",
                                            created_by=w.admin_id)
        assert contact is not None and raw
        found, outcome = sc.verify_contact(db, token=raw)
        assert outcome == "verified", outcome
        # A newly verified contact is advisory-subscribed by default, because verifying a
        # security contact IS the opt-in for security mail.
        assert found.advisory_subscribed is True
    finally:
        db.close()


TESTS = [
    test_draft_advisory_sends_nothing,
    test_published_advisory_is_persisted_and_versioned,
    test_severity_is_authoritative,
    test_affected_scope_is_authoritative,
    test_subscribed_verified_contacts_are_notified,
    test_affected_customer_requires_evidence,
    test_update_appends_and_never_rewrites,
    test_remediation_requires_a_real_fix,
    test_workaround_claims_are_truthful,
    test_no_upgrade_immediately_without_policy,
    test_closure_does_not_claim_customers_patched,
    test_no_exploit_detail_leaks,
    test_advisory_dedup_and_provider_failure,
    test_advisory_is_class_a_and_not_marketing,
    test_evidence_request_is_persisted_and_qualified,
    test_access_is_bound_on_every_dimension,
    test_expiry_and_revocation_stop_access,
    test_evidence_is_never_emailed,
    test_access_is_logged_both_ways,
    test_document_is_not_publicly_reachable,
    test_evidence_dedup,
    test_evidence_sweep_expires_access,
    test_secure_report_is_persisted,
    test_acknowledgement_promises_nothing,
    test_reporter_identity_is_protected,
    test_reporter_portal_serves_only_the_safe_view,
    test_researcher_updates_are_safe_and_staged,
    test_coordination_only_when_recorded,
    test_remediation_and_closure_are_truthful,
    test_reporting_creates_no_marketing_enrolment,
    test_vuln_dedup,
    test_report_form_refuses_credentials,
    test_public_report_endpoint_is_unauthenticated,
    test_existing_sec_families_still_work,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
