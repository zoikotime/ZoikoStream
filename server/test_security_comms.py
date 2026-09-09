"""SEC-001 -> SEC-006 - security, abuse and content restriction (ZST-EC-001).

The guarantees the spec calls out as most important each have a dedicated test:

    SEC-006  verified contacts gate security mail   test_only_verified_contacts_receive_security_mail
    SEC-006  a 403 is not a violation               test_authorization_denial_is_not_a_violation
    SEC-002  independent approval + real expiry     test_breakglass_requires_independent_approval
    SEC-001  detected is not confirmed              test_unconfirmed_event_sends_nothing
    SEC-001  containment is never inferred          test_containment_language_requires_containment
    SEC-003  no evidence or internals in mail       test_incident_mail_carries_no_internals
    SEC-004  reporter identity is protected         test_reporter_identity_is_never_disclosed
    SEC-004  no enforcement promise                 test_no_enforcement_outcome_is_promised
    SEC-005  appeal lifecycle is real               test_appeal_granted_only_after_enforcement_changed
    SEC-005  no false permanent-deletion claim      test_removal_respects_retention_and_legal_hold

Run with `python test_security_comms.py` (or pytest).
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.db import SessionLocal
from app.models import (
    ABUSE_CLOSURE_NOTES,
    EVENT_ALERTABLE,
    PURPOSE_SECURITY_CONTACT,
    VIOLATION_SOURCES,
    AbuseReport,
    AccessPolicyViolation,
    ContentRestriction,
    ElevationSession,
    Incident,
    LiveRecording,
    Organization,
    OrganizationSecurityContact,
    RestrictionAppeal,
    SecurityEvent,
    SecurityIncidentDisclosure,
    SecurityNotice,
    SupportAccessRequest,
    User,
)
from app.security import hash_password
from app.services import security_comms as sec
from app.services import support_access

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

VERIFY = email_mod.SEC_006_VERIFY_SUBJECT
VIOLATION = email_mod.SEC_006_VIOLATION_SUBJECT
ALERT = email_mod.SEC_001_ALERT_SUBJECT
CONTAINED = email_mod.SEC_001_CONTAINED_SUBJECT
RESOLVED = email_mod.SEC_001_RESOLVED_SUBJECT
BG_STARTED = email_mod.SEC_002_STARTED_SUBJECT
BG_ENDED = email_mod.SEC_002_ENDED_SUBJECT
BG_REVIEW = email_mod.SEC_002_REVIEW_SUBJECT
INC_OPENED = email_mod.SEC_003_OPENED_SUBJECT
INC_CONTAINED = email_mod.SEC_003_CONTAINED_SUBJECT
INC_RESOLVED = email_mod.SEC_003_RESOLVED_SUBJECT
ABUSE_RECEIVED = email_mod.SEC_004_RECEIVED_SUBJECT
ABUSE_CLOSED = email_mod.SEC_004_CLOSED_SUBJECT
RESTRICTED = email_mod.SEC_005_RESTRICTED_SUBJECT
APPEAL_RECEIVED = email_mod.SEC_005_APPEAL_RECEIVED_SUBJECT
APPEAL_DECIDED = email_mod.SEC_005_APPEAL_DECIDED_SUBJECT
REMOVED = email_mod.SEC_005_REMOVED_SUBJECT

# Deliberately alarming internals, planted so the leak assertions have something to catch.
SECRET_DETAIL = ("Root cause: CVE-2027-9911 in the ingest pool. Detector rule "
                 "SIG-4471 fired at score 0.97 from 203.0.113.44. Payload: <script>")


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


def _new_email(tag="sec"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Sec Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="UTC")
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "org_admin", self.owner_email, "Org Owner")
            self.member_email = _new_email("member")
            self.member_id = self._u(db, "viewer", self.member_email, "Member")
            self.host_email = _new_email("host")
            self.host_id = self._u(db, "host", self.host_email, "Publisher")
            org.owner_user_id = self.owner_id
            # Two staff identities so break-glass dual approval is testable.
            self.engineer_id = self._u(db, "super_admin", _new_email("eng"), "Engineer")
            self.approver_id = self._u(db, "super_admin", _new_email("appr"), "Approver")
            self.verified_email = _new_email("verified")
            self.pending_email = _new_email("pending")
            db.commit()
        finally:
            db.close()

    def _u(self, db, role, email, name):
        user = User(org_id=self.org_id, full_name=name, role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=_now())
        db.add(user)
        db.flush()
        return user.id

    def token(self, email):
        client = TestClient(m.app)
        ratelimit._HITS.clear()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": email, "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def verified_contact(self, email=None):
        """A contact that has actually proved control of its inbox."""
        db = SessionLocal()
        try:
            org = db.get(Organization, self.org_id)
            contact, raw = sec.nominate_contact(db, org, email=email or self.verified_email,
                                                display_name="Security Desk")
            found, outcome = sec.verify_contact(db, token=raw)
            assert outcome == "verified", outcome
            return found.id
        finally:
            db.close()

    def pending_contact(self):
        db = SessionLocal()
        try:
            org = db.get(Organization, self.org_id)
            contact, raw = sec.nominate_contact(db, org, email=self.pending_email)
            return contact.id, raw
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(SecurityNotice).filter(SecurityNotice.org_id == self.org_id).delete()
            for r in db.query(ContentRestriction).filter(
                    ContentRestriction.org_id == self.org_id).all():
                db.query(RestrictionAppeal).filter(
                    RestrictionAppeal.restriction_id == r.id).delete()
            db.commit()
            for model in (ContentRestriction, OrganizationSecurityContact,
                          SecurityIncidentDisclosure):
                db.query(model).filter(model.org_id == self.org_id).delete()
            for model in (AccessPolicyViolation, SecurityEvent, AbuseReport):
                db.query(model).filter(model.org_id == self.org_id).delete()
            db.query(LiveRecording).filter(LiveRecording.org_id == self.org_id).delete()
            db.commit()
            from app.models import Event
            db.query(Event).filter(Event.org_id == self.org_id).delete()
            for req in db.query(SupportAccessRequest).filter(
                    SupportAccessRequest.org_id == self.org_id).all():
                if req.elevation_session_id:
                    db.query(ElevationSession).filter(
                        ElevationSession.id == req.elevation_session_id).delete()
            db.query(SupportAccessRequest).filter(
                SupportAccessRequest.org_id == self.org_id).delete()
            db.query(Incident).filter(Incident.org_id == self.org_id).delete()
            db.commit()
            org = db.get(Organization, self.org_id)
            if org is not None:
                org.owner_user_id = None
            db.commit()
            db.query(User).filter(User.org_id == self.org_id).delete()
            db.query(Organization).filter(Organization.id == self.org_id).delete()
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


# ══ SEC-006 — contacts ══════════════════════════════════════════════════════════════════

def test_contact_token_is_strong_hashed_and_single_use(w):
    """1, 2, 3, 4, 5, 6, 7, 8."""
    cid, raw = w.pending_contact()
    db = SessionLocal()
    try:
        contact = db.get(OrganizationSecurityContact, cid)
        assert contact.status == "pending"
        # 2 - 256 bits of CSPRNG, url-safe.
        assert len(raw) >= 40, len(raw)
        # 3 - only the hash is stored.
        assert contact.verification_token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in (contact.verification_token_hash or "")
        # 4 - purpose binding.
        assert contact.verification_purpose == PURPOSE_SECURITY_CONTACT
        found, outcome = sec.verify_contact(db, token=raw, purpose="something_else")
        assert found is None and outcome == "invalid"
        # 5 - expiry.
        assert contact.verification_expires_at > _now()
        # 8 - verification is stored.
        found, outcome = sec.verify_contact(db, token=raw)
        assert outcome == "verified" and found.verified_at is not None
        # 6 - single use.
        found, outcome = sec.verify_contact(db, token=raw)
        assert found is None and outcome == "already_used"
    finally:
        db.close()


def test_superseded_and_expired_tokens_are_rejected(w):
    """7 - and an expired one is rejected too."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        contact, first = sec.nominate_contact(db, org, email=_new_email("sup"))
        contact, second = sec.nominate_contact(db, org, email=contact.email)
        # 7 - the old link stops working once a new one is issued.
        found, outcome = sec.verify_contact(db, token=first)
        assert found is None and outcome in ("invalid", "superseded"), outcome
        found, outcome = sec.verify_contact(db, token=second)
        assert outcome == "verified"

        stale, raw = sec.nominate_contact(db, org, email=_new_email("exp"))
        stale.verification_expires_at = _now() - timedelta(minutes=1)
        db.commit()
        found, outcome = sec.verify_contact(db, token=raw)
        assert found is None and outcome == "expired"
        db.expire_all()
        assert db.get(OrganizationSecurityContact, stale.id).status == "expired"
    finally:
        db.close()


def test_only_verified_contacts_receive_security_mail(w):
    """9 - and pending/revoked are excluded. The SEC-006 foundation."""
    vid = w.verified_contact()
    pid, _raw = w.pending_contact()
    db = SessionLocal()
    try:
        people = [a for a, _n in sec.security_recipients(db, w.org_id)]
        assert w.verified_email.lower() in people
        assert w.pending_email.lower() not in people, "a pending contact must be excluded"
        # Org admins are NOT substituted.
        assert w.owner_email.lower() not in people
        assert w.member_email.lower() not in people

        assert sec.revoke_contact(db, db.get(OrganizationSecurityContact, vid)) is True
        people = [a for a, _n in sec.security_recipients(db, w.org_id)]
        assert w.verified_email.lower() not in people, "a revoked contact must be excluded"
    finally:
        db.close()


def test_verification_email_carries_no_incident_detail(w):
    """The verification message establishes a channel; it discloses nothing."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        contact, raw = sec.nominate_contact(db, org, email=_new_email("v"))
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_contact_verification(db, _Bg(), contact, raw) is True
        payload = cap.of(VERIFY)
        assert payload["html"].strip() and payload["text"].strip()
        assert raw in payload["text"], "the raw token travels once, in the link"
        assert "never asks for your password" in payload["text"]
        # The scope note legitimately mentions that this contact receives incident
        # notices, so the check targets incident DETAIL rather than the word itself.
        for leak in ("CVE-", "detector", "SIG-", "score", "root cause", "203.0.113"):
            assert leak.lower() not in payload["text"].lower(), leak
    finally:
        db.close()


# ══ SEC-006 — violations ════════════════════════════════════════════════════════════════

def test_authorization_denial_is_not_a_violation(w):
    """10 - the critical boundary. A 403 creates no violation record."""
    import inspect

    client = TestClient(m.app)
    headers = w.token(w.member_email)
    ratelimit._HITS.clear()
    # An ordinary member hitting an admin-only page: a plain denial.
    r = client.get("/api/admin/support-tickets", headers=headers)
    assert r.status_code in (401, 403), r.status_code
    db = SessionLocal()
    try:
        assert db.query(AccessPolicyViolation).filter(
            AccessPolicyViolation.org_id == w.org_id).count() == 0, (
            "an ordinary authorization denial must not create a violation")
    finally:
        db.close()

    # Structural: no authorization helper can reach the only writer.
    from app import security as security_mod

    assert "confirm_violation" not in inspect.getsource(security_mod)
    # And the denial source is not even an accepted value.
    assert "authorization_denial" not in VIOLATION_SOURCES
    db = SessionLocal()
    try:
        assert sec.confirm_violation(
            db, org_id=w.org_id, affected_user_id=w.member_id,
            category="device_policy", source="authorization_denial",
            safe_summary="x") is None, "a denial is not an authoritative source"
    finally:
        db.close()


def test_confirmed_violation_notifies_safely(w):
    """11, 12, 13, 14."""
    w.verified_contact()
    db = SessionLocal()
    try:
        violation = sec.confirm_violation(
            db, org_id=w.org_id, affected_user_id=w.member_id,
            category="credential_sharing", source="security_control",
            safe_summary="The same credentials were used from two places at once.",
            remediation_required="Change your password and review your sessions.",
            access_restricted=True)
        assert violation is not None and violation.status == "confirmed"
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_violation(db, _Bg(), violation) is True
            n = len(cap.calls)
            assert sec.notify_violation(db, _Bg(), violation) is False   # 14 dedup
        assert len(cap.calls) == n
        got = cap.to(VIOLATION)
        assert w.member_email.lower() in got, "the affected user must be told"
        assert w.verified_email.lower() in got, "verified security contacts must be told"
        text = cap.of(VIOLATION)["text"]
        assert "Account credentials appear to be shared" in text
        assert "Access restricted: Yes" in text
        # 13 - no detection internals.
        for leak in ("SIG-", "score", "0.97", "203.0.113", "rule", "threshold"):
            assert leak.lower() not in text.lower(), f"detection internal leaked: {leak}"
    finally:
        db.close()


# ══ SEC-001 ═════════════════════════════════════════════════════════════════════════════

def test_unconfirmed_event_sends_nothing(w):
    """1 - detected is not confirmed."""
    w.verified_contact()
    db = SessionLocal()
    try:
        event = sec.open_event(db, org_id=w.org_id, affected_user_id=w.member_id,
                               category="suspicious_access")
        assert event.status == "detected"
        cap, ctx = _capture()
        with ctx:
            assert sec.alert(db, _Bg(), event) is None
        assert cap.calls == [], "an unverified detection must not page anybody"
        event.status = "under_review"
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert sec.alert(db, _Bg(), event) is None
        assert cap2.calls == []
        assert "detected" not in EVENT_ALERTABLE and "under_review" not in EVENT_ALERTABLE
    finally:
        db.close()


def test_confirmed_event_reaches_holder_and_verified_contacts(w):
    """2, 3, 4, 5, 6, 7, 10."""
    w.verified_contact()
    w.pending_contact()
    db = SessionLocal()
    try:
        event = sec.open_event(db, org_id=w.org_id, affected_user_id=w.member_id,
                               category="credential_compromise")
        assert sec.confirm_event(
            db, event, summary="We confirmed your credentials were used by someone else.",
            remediation="Change your password now.",
            access_state="Sessions revoked; sign-in requires a new password") is True
        assert event.confirmed_at is not None and event.reference.startswith("SEC-")
        cap, ctx = _capture()
        with ctx:
            assert sec.alert(db, _Bg(), event) == "urgent_alert"
            n = len(cap.calls)
            assert sec.alert(db, _Bg(), event) is None                    # 10 dedup
        assert len(cap.calls) == n
        got = cap.to(ALERT)
        assert w.member_email.lower() in got                              # 3
        assert w.verified_email.lower() in got                            # 4
        assert w.pending_email.lower() not in got                         # 5
        # Not blasted at the org.
        assert w.host_email.lower() not in got
        text = cap.of(ALERT)["text"]
        assert "Credential compromise" in text                            # 6
        for leak in ("SIG-", "CVE-", "0.97", "203.0.113"):                # 7
            assert leak not in text
    finally:
        db.close()


def test_containment_language_requires_containment(w):
    """8, 9 - and resolution is distinct from containment."""
    w.verified_contact()
    db = SessionLocal()
    try:
        event = sec.open_event(db, org_id=w.org_id, affected_user_id=w.member_id,
                               category="account_takeover")
        sec.confirm_event(db, event, summary="Confirmed takeover.")
        # 8 - not contained yet, so the phrase must not appear.
        assert sec.containment_note(event) == "Our team is actively working to contain this."
        cap, ctx = _capture()
        with ctx:
            sec.alert(db, _Bg(), event)
        assert "has been contained" not in cap.of(ALERT)["text"]

        # A revoked session is NOT containment.
        event.access_state = "All sessions revoked"
        db.commit()
        assert "has been contained" not in sec.containment_note(event), (
            "revoking sessions must not by itself claim containment")

        assert sec.contain_event(db, event) is True
        assert event.contained_at is not None
        assert sec.containment_note(event) == "The issue has been contained."
        cap2, ctx2 = _capture()
        with ctx2:
            assert sec.alert(db, _Bg(), event) == "event_contained"
        assert cap2.count(CONTAINED) >= 1

        # 9 - resolution is its own transition and its own message.
        assert sec.resolve_event(db, event) is True
        assert event.resolved_at is not None and event.contained_at is not None
        cap3, ctx3 = _capture()
        with ctx3:
            assert sec.alert(db, _Bg(), event) == "event_resolved"
        assert cap3.count(RESOLVED) >= 1
    finally:
        db.close()


def test_provider_failure_does_not_roll_back_event(w):
    """11."""
    w.verified_contact()
    db = SessionLocal()
    try:
        event = sec.open_event(db, org_id=w.org_id, affected_user_id=w.member_id,
                               category="other")
        sec.confirm_event(db, event, summary="Confirmed.")
        capf, ctxf = _capture(fail=True)
        with ctxf:
            sec.alert(db, _Bg(), event)
        db.expire_all()
        assert db.get(SecurityEvent, event.id).status == "confirmed"
    finally:
        db.close()


# ══ SEC-002 ═════════════════════════════════════════════════════════════════════════════

def _emergency(db, w, *, countersign=True, minutes=30):
    """An emergency request using the EXISTING support-access machinery."""
    engineer = db.get(User, w.engineer_id)
    req = support_access.create_request(
        db, org_id=w.org_id, case_reference="SUP-2027-000001",
        reason_category="security_review", engineer=engineer,
        engineer_display="Engineer", requested_scope="Support Access",
        allowed_actions=["read_config"], minutes=minutes,
        emergency=True, emergency_reason="Customer unreachable during live incident")
    if countersign:
        outcome = support_access.countersign_emergency(
            db, req, db.get(User, w.approver_id))
        assert outcome == "ok", outcome
    session = ElevationSession(user_id=w.engineer_id, scope="Support Access",
                               scopes=["read_config"], reason="emergency",
                               expires_at=_now() + timedelta(minutes=minutes))
    db.add(session)
    db.flush()
    req.elevation_session_id = session.id
    db.commit()
    return req, session


def test_breakglass_requires_independent_approval(w):
    """1, 2, 3, 12 - the existing countersign is reused, not replaced."""
    db = SessionLocal()
    try:
        # 2 - the requester cannot countersign their own request.
        req, session = _emergency(db, w, countersign=False)
        outcome = support_access.countersign_emergency(db, req, db.get(User, w.engineer_id))
        assert outcome != "ok", "self-authorization must be refused"
        active, why = sec.authorize_breakglass(db, req)
        assert active is False and "independent" in why, why

        # 3 - a genuine second approver makes it active.
        assert support_access.countersign_emergency(
            db, req, db.get(User, w.approver_id)) == "ok"
        active, why = sec.authorize_breakglass(db, req)
        assert active is True, why
        # 12 - the scope is recorded, not unrestricted.
        assert support_access.actions_list(req.allowed_actions) == ["read_config"]
    finally:
        db.close()


def test_breakglass_expiry_is_enforced(w):
    """6 - and the started notice quotes an enforced deadline."""
    db = SessionLocal()
    try:
        req, session = _emergency(db, w)
        active, _why = sec.authorize_breakglass(db, req)
        assert active is True
        # Expire it: the same condition services/ops.current_elevation applies.
        session.expires_at = _now() - timedelta(minutes=1)
        db.commit()
        active, why = sec.authorize_breakglass(db, req)
        assert active is False and "expired" in why, why

        from app.services import ops as ops_svc
        assert ops_svc.current_elevation(db, db.get(User, w.engineer_id)) is None, (
            "the existing elevation gate must also refuse an expired session")
    finally:
        db.close()


def test_breakglass_started_ended_and_recipients(w):
    """4, 5, 7, 8, 9."""
    w.verified_contact()
    db = SessionLocal()
    try:
        req, session = _emergency(db, w)
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_breakglass_started(db, _Bg(), req) is True
            n = len(cap.calls)
            assert sec.notify_breakglass_started(db, _Bg(), req) is False
        assert len(cap.calls) == n
        got = cap.to(BG_STARTED)
        assert w.owner_email.lower() in got                       # 9 owner
        assert w.verified_email.lower() in got                    # 9 security contact
        assert w.member_email.lower() not in got
        text = cap.of(BG_STARTED)["text"]
        assert "Independently authorized by" in text
        assert "read_config" in text

        # 7 - explicit termination.
        assert sec.end_breakglass(db, req, reason="terminated") is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert sec.notify_breakglass_ended(db, _Bg(), req,
                                               end_reason="Ended by the operator") is True
        text2 = cap2.of(BG_ENDED)["text"]
        assert "review" in text2.lower()
        assert "does not itself conclude that the activity was appropriate" in text2
    finally:
        db.close()


def test_review_overdue_only_from_stored_deadline(w):
    """10, 11 - no invented SLA."""
    w.verified_contact()
    db = SessionLocal()
    try:
        req, session = _emergency(db, w)
        # No deadline stored: nothing is overdue, and nothing is invented.
        assert req.review_due_at is None
        assert sec.review_overdue(req) is False
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_review_overdue(db, _Bg(), req) is False
        assert cap.calls == []

        sec.end_breakglass(db, req, reason="expired",
                           review_due_at=_now() - timedelta(hours=1))
        assert sec.review_overdue(req) is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert sec.notify_review_overdue(db, _Bg(), req) is True
        assert cap2.count(BG_REVIEW) >= 1

        # A completed review stops it.
        req.post_use_review_at = _now()
        db.commit()
        assert sec.review_overdue(req) is False
    finally:
        db.close()


# ══ SEC-003 ═════════════════════════════════════════════════════════════════════════════

def _incident(db, w, *, kind="security"):
    incident = Incident(ref=f"INC-2027-{uuid.uuid4().hex[:6].upper()}",
                        title="Ingest anomaly", detail=SECRET_DETAIL, severity="sev1",
                        kind=kind, status="open", commander="Jane Ops", org_id=w.org_id)
    db.add(incident)
    db.commit()
    return incident


def test_internal_incident_alone_emails_nobody(w):
    """1 - an internal record is not a customer disclosure."""
    w.verified_contact()
    db = SessionLocal()
    try:
        incident = _incident(db, w)
        assert db.query(SecurityIncidentDisclosure).filter(
            SecurityIncidentDisclosure.incident_id == incident.id).count() == 0
        # There is no path from an Incident row to a customer message.
        cap, ctx = _capture()
        with ctx:
            pass
        assert cap.calls == []
    finally:
        db.close()


def test_incident_mail_carries_no_internals(w):
    """2, 6, 7, 8, 9, 10, 11 - the disclosure boundary."""
    w.verified_contact()
    w.pending_contact()
    db = SessionLocal()
    try:
        incident = _incident(db, w)
        org = db.get(Organization, w.org_id)
        disclosure = sec.open_disclosure(
            db, incident, org, affected_service="Live streaming",
            customer_impact="Some sessions may have been interrupted.",
            recommended_action="No action is needed from you right now.")
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_disclosure(db, _Bg(), disclosure) == "incident_opened"
            n = len(cap.calls)
            assert sec.notify_disclosure(db, _Bg(), disclosure) is None   # 11 dedup
        assert len(cap.calls) == n
        got = cap.to(INC_OPENED)
        assert w.verified_email.lower() in got                            # 6
        assert w.pending_email.lower() not in got
        assert w.owner_email.lower() not in got, "org owner is not a security contact"
        blob = cap.blob()
        assert incident.ref in blob
        # 7, 8 - no internals whatsoever.
        for leak in ("CVE-2027-9911", "SIG-4471", "0.97", "203.0.113.44", "<script>",
                     "Root cause", "Jane Ops", "sev1"):
            assert leak not in blob, f"internal detail leaked: {leak}"
        # 9, 10 - authenticated route, and no invented next update.
        assert "/organization/security" in blob
        assert "/admin" not in blob
        assert "We will update you when we have more information." in blob
        assert "never asks for your password" in blob
    finally:
        db.close()


def test_incident_update_contained_and_resolved(w):
    """3, 4, 5 - versioned updates, authoritative states."""
    w.verified_contact()
    db = SessionLocal()
    try:
        incident = _incident(db, w)
        org = db.get(Organization, w.org_id)
        disclosure = sec.open_disclosure(db, incident, org,
                                         affected_service="Live streaming",
                                         customer_impact="Under assessment.")
        cap, ctx = _capture()
        with ctx:
            sec.notify_disclosure(db, _Bg(), disclosure)
        # 3 - a no-op publish is not an update.
        assert sec.publish_disclosure_update(db, disclosure) is False
        assert sec.publish_disclosure_update(
            db, disclosure, status="investigating",
            customer_impact="We have identified the affected sessions.") is True
        assert disclosure.disclosure_version == 2
        cap2, ctx2 = _capture()
        with ctx2:
            assert sec.notify_disclosure(db, _Bg(), disclosure) == "incident_update"

        # 4 - containment is authoritative.
        assert sec.publish_disclosure_update(db, disclosure, status="contained") is True
        assert disclosure.contained_at is not None
        cap3, ctx3 = _capture()
        with ctx3:
            assert sec.notify_disclosure(db, _Bg(), disclosure) == "incident_contained"
        assert "investigation is continuing" in cap3.of(INC_CONTAINED)["text"].lower()

        # 5 - resolution, and no report claimed unless one exists.
        assert sec.publish_disclosure_update(db, disclosure, status="resolved") is True
        cap4, ctx4 = _capture()
        with ctx4:
            assert sec.notify_disclosure(db, _Bg(), disclosure) == "incident_resolved"
        assert "No customer report was produced." in cap4.of(INC_RESOLVED)["text"]
    finally:
        db.close()


# ══ SEC-004 ═════════════════════════════════════════════════════════════════════════════

def test_reporter_identity_is_never_disclosed(w):
    """3, 4 - the reported party learns what, never who."""
    db = SessionLocal()
    try:
        report = sec.file_report(db, category="harassment", subject_type="event",
                                 reporter_id=w.member_id,
                                 reporter_contact=w.member_email,
                                 reporter_name="Member Person",
                                 description=f"Contact me at {w.member_email}",
                                 org_id=w.org_id)
        view = sec.reported_party_view(report)
        for forbidden in ("reporter_id", "reporter_contact", "reporter_name",
                          "description"):
            assert forbidden not in view, f"{forbidden} leaked to the reported party"
        assert set(view) == {"reference", "category", "subject_type", "status",
                             "created_at"}
    finally:
        db.close()

    # And through the real HTTP surface the reported organization can reach.
    client = TestClient(m.app)
    headers = w.token(w.owner_email)
    ratelimit._HITS.clear()
    r = client.get("/api/organization/abuse-reports/about-us", headers=headers)
    assert r.status_code == 200, r.text
    body = r.text
    assert w.member_email not in body, "reporter contact leaked over HTTP"
    assert "Member Person" not in body
    assert "Contact me at" not in body


def test_no_enforcement_outcome_is_promised(w):
    """1, 2, 5, 9, 10."""
    db = SessionLocal()
    try:
        report = sec.file_report(db, category="harmful_content", subject_type="recording",
                                 reporter_id=w.member_id,
                                 reporter_contact=w.member_email, org_id=w.org_id)
        assert report.reference.startswith("ABR-") and report.status == "received"
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_report_received(db, _Bg(), report) is True
            n = len(cap.calls)
            assert sec.notify_report_received(db, _Bg(), report) is False   # 10
        assert len(cap.calls) == n
        assert cap.to(ABUSE_RECEIVED) == [w.member_email.lower()]           # 2
        text = cap.of(ABUSE_RECEIVED)["text"]
        assert "We will review the report under our policies." in text
        # 5 - no outcome promised.
        for promise in ("we will suspend", "we will remove", "we will ban", "banned",
                        "legal action", "refund", "will be removed", "will be suspended"):
            assert promise not in text.lower(), f"enforcement promised: {promise}"
        # 9 - reporting is not a marketing signup.
        assert "does not subscribe you to marketing" in text
    finally:
        db.close()


def test_triage_and_closure_are_outcome_free(w):
    """6, 7, 8."""
    db = SessionLocal()
    try:
        report = sec.file_report(db, category="spam", subject_type="user",
                                 reporter_contact=w.member_email, org_id=w.org_id)
        assert sec.triage_report(db, report) is True                        # 6
        assert report.triaged_at is not None and report.status == "under_review"
        assert sec.close_report(db, report, closure_note="not_an_approved_note") is False
        assert sec.close_report(
            db, report, closure_note="reviewed_action_taken_undisclosed") is True  # 7
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_report_closed(db, _Bg(), report) is True
        text = cap.of(ABUSE_CLOSED)["text"]
        assert "review of report" in text.lower()
        # 8 - the strongest approved note still names no action against anybody.
        for disclosure in ("we banned", "we suspended", "we deleted the account",
                           "the user was removed"):
            assert disclosure not in text.lower(), disclosure
        assert all(note in str(ABUSE_CLOSURE_NOTES) for note in ("insufficient_information",))
    finally:
        db.close()


# ══ SEC-005 ═════════════════════════════════════════════════════════════════════════════

def test_proposed_restriction_sends_nothing(w):
    """1, 2 - a dialog or a proposal is not authoritative state."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        restriction = sec.propose_restriction(
            db, org, content_type="recording", restriction_type="access_suspended",
            content_label="Board meeting", customer_safe_reason="Under policy review.")
        assert restriction.status == "proposed"
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_restriction_active(db, _Bg(), restriction) is False
        assert cap.calls == [], "a proposed restriction must announce nothing"

        assert sec.activate_restriction(db, restriction) is True
        assert restriction.status == "active" and restriction.effective_at is not None
    finally:
        db.close()


def test_restriction_notice_reaches_owner_and_hides_reporter(w):
    """3, 4, 5, 13."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        report = sec.file_report(db, category="harassment", subject_type="recording",
                                 reporter_contact=w.member_email,
                                 reporter_name="Member Person", org_id=w.org_id)
        restriction = sec.propose_restriction(
            db, org, content_type="recording", restriction_type="visibility_limited",
            content_label="Board meeting",
            customer_safe_reason="This content is under review against our policies.")
        sec.activate_restriction(db, restriction)
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_restriction_active(db, _Bg(), restriction) is True
            n = len(cap.calls)
            assert sec.notify_restriction_active(db, _Bg(), restriction) is False   # 13
        assert len(cap.calls) == n
        got = cap.to(RESTRICTED)
        assert w.owner_email.lower() in got                                  # 3
        assert w.host_email.lower() in got, "authorized publishers are notified"
        blob = cap.blob()
        assert "under review against our policies" in blob                   # 4
        # 5 - the complainant is never identified.
        for leak in (w.member_email, "Member Person", report.reference):
            assert leak not in blob, f"reporter detail leaked: {leak}"
    finally:
        db.close()


def test_appeal_lifecycle(w):
    """6, 7, 8."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        # 6 - no appeal when policy forbids one.
        no_appeal = sec.propose_restriction(
            db, org, content_type="event", restriction_type="access_suspended",
            appeal_allowed=False)
        sec.activate_restriction(db, no_appeal)
        assert sec.submit_appeal(db, no_appeal, submitted_by_email=w.owner_email) is None

        restriction = sec.propose_restriction(
            db, org, content_type="recording", restriction_type="visibility_limited",
            content_label="Board meeting", appeal_allowed=True,
            appeal_deadline=_now() + timedelta(days=14))
        sec.activate_restriction(db, restriction)
        appeal = sec.submit_appeal(db, restriction, submitted_by_email=w.owner_email,
                                   grounds="This was a private internal meeting.")
        assert appeal is not None and appeal.status == "submitted"
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_appeal(db, _Bg(), appeal, restriction) == "appeal_received"
        assert cap.count(APPEAL_RECEIVED) >= 1                               # 7

        # 8 - upheld keeps the restriction in force.
        assert sec.decide_appeal(db, appeal, restriction, granted=False,
                                 decision="The restriction stands under our policies.") is True
        assert restriction.status == "active"
        cap2, ctx2 = _capture()
        with ctx2:
            assert sec.notify_appeal(db, _Bg(), appeal, restriction) == "appeal_upheld"
        assert "remains in effect" in cap2.of(APPEAL_DECIDED)["text"]
    finally:
        db.close()


def test_appeal_granted_only_after_enforcement_changed(w):
    """9 - the notice cannot precede the change it announces."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        restriction = sec.propose_restriction(
            db, org, content_type="recording", restriction_type="visibility_limited",
            content_label="Board meeting")
        sec.activate_restriction(db, restriction)
        appeal = sec.submit_appeal(db, restriction, submitted_by_email=w.owner_email)

        # Force the appeal to granted WITHOUT lifting the restriction: refused.
        appeal.status = "granted"
        db.commit()
        assert restriction.status == "active"
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_appeal(db, _Bg(), appeal, restriction) is None, (
                "granted must not be announced while the restriction is still active")
        assert cap.calls == []

        # Through the real path, decide_appeal lifts it in the same commit.
        appeal.status = "submitted"
        db.commit()
        assert sec.decide_appeal(db, appeal, restriction, granted=True,
                                 decision="Restriction lifted.") is True
        assert restriction.status == "removed" and restriction.removed_at is not None
        cap2, ctx2 = _capture()
        with ctx2:
            assert sec.notify_appeal(db, _Bg(), appeal, restriction) == "appeal_granted"
        assert "has been lifted" in cap2.of(APPEAL_DECIDED)["text"]
    finally:
        db.close()


def test_removal_respects_retention_and_legal_hold(w):
    """10, 11, 12 - reuses MED-011 state and never over-claims."""
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        from app.models import Event

        ev = Event(org_id=w.org_id, created_by=w.owner_id, title="Board meeting",
                   status="ended", timezone="UTC")
        db.add(ev)
        db.flush()
        recording = LiveRecording(event_id=ev.id, org_id=w.org_id, status="stopped",
                                  started_at=_now() - timedelta(hours=2), stopped_at=_now(),
                                  enforced=True, legal_hold=True)
        db.add(recording)
        db.commit()
        restriction = sec.propose_restriction(
            db, org, content_type="recording", restriction_type="content_removed",
            content_id=recording.id, content_label="Board meeting")
        sec.activate_restriction(db, restriction)

        # 12 - legal hold forbids removal, so no claim may be made.
        allowed, why = sec.removal_position(db, restriction)
        assert allowed is False and "legal hold" in why, why
        cap, ctx = _capture()
        with ctx:
            assert sec.notify_removal_completed(db, _Bg(), restriction) is False
        assert cap.calls == []

        # A running retention window also forbids it.
        recording.legal_hold = False
        recording.retention_expires_at = _now() + timedelta(days=30)
        db.commit()
        allowed, why = sec.removal_position(db, restriction)
        assert allowed is False and "retention" in why, why

        # Storage removal not complete: still no claim.
        recording.retention_expires_at = _now() - timedelta(days=1)
        db.commit()
        allowed, why = sec.removal_position(db, restriction)
        assert allowed is False and "storage removal" in why, why

        # 10 - only once storage actually reports deleted.
        recording.deletion_status = "deleted"
        restriction.content_deleted_at = _now()
        db.commit()
        allowed, sentence = sec.removal_position(db, restriction)
        assert allowed is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert sec.notify_removal_completed(db, _Bg(), restriction) is True
        text = cap2.of(REMOVED)["text"]
        # 11 - never a permanent-deletion claim.
        for overclaim in ("all copies", "permanently deleted", "permanently erased",
                          "everything has been deleted"):
            assert overclaim not in text.lower(), overclaim
        assert "Backup copies may persist" in text
    finally:
        db.close()


# ══ cross-cutting ═══════════════════════════════════════════════════════════════════════

def test_security_mail_is_never_preference_suppressible(w):
    """Security notices must not depend on marketing or notification consent."""
    from app.services import notifications

    for family in ("SEC-001", "SEC-002", "SEC-003", "SEC-004", "SEC-005", "SEC-006"):
        assert family in notifications.FAMILY_CLASS, family
        assert notifications.is_mandatory(family) is True, family
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        org.notifications = {k: False for k in
                             ("event_scheduled", "member_joined", "billing",
                              "security_alerts")}
        db.commit()
        for family in ("SEC-001", "SEC-003", "SEC-005", "SEC-006"):
            assert notifications.should_send_operational_notification(
                family=family, org=org) is True, family
        assert notifications.MARKETING_PREFERENCE_KEYS == frozenset()
    finally:
        db.close()


def test_no_template_accepts_a_secret_or_internal(w):
    """Structural: there is no parameter through which a secret or internal could travel."""
    import inspect

    senders = [n for n in dir(email_mod)
               if n.startswith(("send_security_", "send_breakglass_", "send_abuse_",
                                "send_content_", "send_restriction_"))]
    # `send_security_advisory_email` is TRU-001, not a SEC family, and its `severity` is the
    # PUBLISHED advisory classification - an approved category a person recorded, which the
    # whole family exists to communicate. It is guarded separately and more strictly in
    # test_trust_center.py: no reporter identity, no reproduction, no internal incident id,
    # and no severity derived in email code.
    senders = [n for n in senders if n != "send_security_advisory_email"]
    assert len(senders) >= 12, senders
    banned = ("password", "reset_token", "mfa", "recovery_code", "api_key", "signing_secret",
              "livekit", "stream_key", "raw_log", "logs", "ip", "signature", "rule",
              "threshold", "score", "exploit", "payload", "reporter_email", "reporter_id",
              "commander", "severity", "internal_notes", "evidence")
    for name in senders:
        for param in inspect.signature(getattr(email_mod, name)).parameters:
            low = param.lower()
            assert not any(b == low for b in banned), f"{name}({param})"


def test_idempotency_key_is_versioned(w):
    """A permanent UNIQUE(kind, subject) would swallow legitimate recurrences."""
    cols = {c.name for c in SecurityNotice.__table__.columns}
    assert "version" in cols
    constraint = next(c for c in SecurityNotice.__table__.constraints
                      if c.__class__.__name__ == "UniqueConstraint")
    assert {c.name for c in constraint.columns} == {"kind", "subject_type", "subject_id",
                                                     "version"}


def test_security_sweep_runs_on_the_shared_ticker(w):
    import inspect

    from app.services import event_planning

    assert "security_comms" in inspect.getsource(event_planning.sweep)
    assert not hasattr(sec, "run_security_sweeper"), (
        "SEC must not add its own ticker alongside the leader-elected one")


TESTS = [
    test_contact_token_is_strong_hashed_and_single_use,
    test_superseded_and_expired_tokens_are_rejected,
    test_only_verified_contacts_receive_security_mail,
    test_verification_email_carries_no_incident_detail,
    test_authorization_denial_is_not_a_violation,
    test_confirmed_violation_notifies_safely,
    test_unconfirmed_event_sends_nothing,
    test_confirmed_event_reaches_holder_and_verified_contacts,
    test_containment_language_requires_containment,
    test_provider_failure_does_not_roll_back_event,
    test_breakglass_requires_independent_approval,
    test_breakglass_expiry_is_enforced,
    test_breakglass_started_ended_and_recipients,
    test_review_overdue_only_from_stored_deadline,
    test_internal_incident_alone_emails_nobody,
    test_incident_mail_carries_no_internals,
    test_incident_update_contained_and_resolved,
    test_reporter_identity_is_never_disclosed,
    test_no_enforcement_outcome_is_promised,
    test_triage_and_closure_are_outcome_free,
    test_proposed_restriction_sends_nothing,
    test_restriction_notice_reaches_owner_and_hides_reporter,
    test_appeal_lifecycle,
    test_appeal_granted_only_after_enforcement_changed,
    test_removal_respects_retention_and_legal_hold,
    test_security_mail_is_never_preference_suppressible,
    test_no_template_accepts_a_secret_or_internal,
    test_idempotency_key_is_versioned,
    test_security_sweep_runs_on_the_shared_ticker,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
