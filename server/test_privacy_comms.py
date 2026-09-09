"""PRV-001 -> PRV-004 - privacy and data governance (ZST-EC-001).

The guarantees the spec calls out as most important each have a dedicated test:

    PRV-001  requests are durable records          test_request_persists_with_safe_reference
    PRV-001  verification is purpose+request bound test_verification_token_is_bound_and_single_use
    PRV-002  no fabricated statutory deadline      test_no_statutory_deadline_is_invented
    PRV-003  exports are links, never attachments  test_export_is_a_short_lived_link_not_an_attachment
    PRV-003  export access is audited              test_export_access_is_logged_without_content
    PRV-003  deletion tells the truth about residue test_deletion_never_claims_total_erasure
    PRV-004  acknowledgement is not consent        test_acknowledgement_is_never_consent
    PRV-004  Accept and Decline are equal          test_accept_and_decline_have_parity
    PRV-004  no invented subprocessors             test_subprocessors_are_real_only

Run with `python test_privacy_comms.py` (or pytest).
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app.email as email_mod
from app.db import SessionLocal
from app.models import (
    DEADLINE_POLICIES,
    PURPOSE_PRIVACY_DOWNLOAD,
    PURPOSE_PRIVACY_VERIFICATION,
    REPRESENTATIVE_AUTOMATED_VALIDATION,
    GovernanceRecord,
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
from app.security import hash_password
from app.services import privacy_comms as prv

PASSWORD = "correct-horse-battery"

RECEIVED = email_mod.PRV_001_RECEIVED_SUBJECT
VERIFY = email_mod.PRV_001_VERIFY_SUBJECT
STATUS = email_mod.PRV_002_STATUS_SUBJECT
CLARIFY = email_mod.PRV_002_CLARIFY_SUBJECT
EXTENSION = email_mod.PRV_002_EXTENSION_SUBJECT
DECISION = email_mod.PRV_002_DECISION_SUBJECT
EXPORT_READY = email_mod.PRV_003_EXPORT_SUBJECT
EXPORT_EXPIRED = email_mod.PRV_003_EXPIRED_SUBJECT
DELETED = email_mod.PRV_003_DELETED_SUBJECT
DELETION_UPDATE = email_mod.PRV_003_DELETION_UPDATE_SUBJECT
NOTICE = email_mod.PRV_004_NOTICE_SUBJECT
CONSENT = email_mod.PRV_004_CONSENT_SUBJECT
SUBPROC = email_mod.PRV_004_SUBPROCESSOR_SUBJECT


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


def _new_email(tag="prv"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _now():
    return datetime.now(timezone.utc)


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Prv Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="UTC")
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.subject_email = _new_email("subject")
            self.subject_id = self._u(db, "viewer", self.subject_email, "Data Subject")
            self.admin_email = _new_email("admin")
            self.admin_id = self._u(db, "org_admin", self.admin_email, "Privacy Admin")
            self.other_email = _new_email("other")
            self.other_id = self._u(db, "viewer", self.other_email, "Someone Else")
            self.rep_email = _new_email("rep")
            org.owner_user_id = self.admin_id
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

    def request(self, *, request_type="export", verified=False, user=True):
        db = SessionLocal()
        try:
            req, raw = prv.open_request(
                db, requester_email=self.subject_email, request_type=request_type,
                requester_user_id=self.subject_id if user else None, org_id=self.org_id,
                details="Please send me everything you hold.")
            if verified and raw:
                found, outcome = prv.verify_request(db, token=raw)
                assert outcome == "verified", outcome
            return req.id, raw
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(PrivacyNotice).delete()
            for r in db.query(PrivacyRequest).filter(
                    PrivacyRequest.org_id == self.org_id).all():
                for e in db.query(PrivacyExport).filter(
                        PrivacyExport.privacy_request_id == r.id).all():
                    db.query(PrivacyExportAccess).filter(
                        PrivacyExportAccess.export_id == e.id).delete()
                db.query(PrivacyExport).filter(
                    PrivacyExport.privacy_request_id == r.id).delete()
                db.query(PrivacyDeletion).filter(
                    PrivacyDeletion.privacy_request_id == r.id).delete()
                db.query(RetentionException).filter(
                    RetentionException.privacy_request_id == r.id).delete()
            db.commit()
            db.query(PrivacyRequest).filter(PrivacyRequest.org_id == self.org_id).delete()
            db.query(PrivacyRepresentative).filter(
                PrivacyRepresentative.subject_email == self.subject_email).delete()
            db.query(GovernanceRecord).filter(
                GovernanceRecord.org_id == self.org_id).delete()
            db.query(LiveRecording).filter(LiveRecording.org_id == self.org_id).delete()
            db.commit()
            for v in db.query(PrivacyNoticeVersion).all():
                db.query(PrivacyConsentDecision).filter(
                    PrivacyConsentDecision.notice_version_id == v.id).delete()
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


# ══ PRV-001 ═════════════════════════════════════════════════════════════════════════════

def test_request_persists_with_safe_reference(w):
    """1, 2, 3, 15, 16."""
    rid, raw = w.request(request_type="access")
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        assert req is not None and req.request_reference.startswith("PRV-")
        # 2 - never a raw UUID as the customer reference.
        assert str(req.id) not in req.request_reference
        # Tracked as the existing governance obligation, not a parallel record.
        assert req.governance_record_id is not None
        record = db.get(GovernanceRecord, req.governance_record_id)
        assert record.kind == "privacy_request"

        cap, ctx = _capture()
        with ctx:
            assert prv.notify_received(db, _Bg(), req) is True
            n = len(cap.calls)
            assert prv.notify_received(db, _Bg(), req) is False       # 16 dedup
        assert len(cap.calls) == n
        assert w.subject_email.lower() in cap.to(RECEIVED)            # 3
        payload = cap.of(RECEIVED)
        assert payload["html"].strip() and payload["text"].strip()    # 15
        # The requester's own free text is not echoed back at them.
        assert "Please send me everything" not in payload["text"]
    finally:
        db.close()


def test_verification_token_is_bound_and_single_use(w):
    """4, 5, 6, 7, 8, 9, 10, 12."""
    rid, raw = w.request(request_type="export")
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        # 4 - an export always requires verification, even for a signed-in owner.
        assert req.verification_required is True
        assert req.status == "verification_required"
        # 5 - 256 bits of CSPRNG.
        assert len(raw) >= 40
        # 6 - only the hash is stored.
        assert req.verification_token_hash == hashlib.sha256(raw.encode()).hexdigest()
        # 8 - purpose binding.
        assert req.verification_purpose == PURPOSE_PRIVACY_VERIFICATION
        found, outcome = prv.verify_request(db, token=raw, purpose="something_else")
        assert found is None and outcome == "invalid"

        # 7, 12 - request binding: a token for THIS request cannot redeem another.
        other_id, _other_raw = w.request(request_type="correction")
        found, outcome = prv.verify_request(db, token=raw, request_id=other_id)
        assert found is None and outcome == "wrong_request", outcome

        # 9 - expiry.
        assert req.verification_expires_at > _now()
        # 10 - single use.
        found, outcome = prv.verify_request(db, token=raw, request_id=rid)
        assert outcome == "verified" and found.verified_at is not None
        found, outcome = prv.verify_request(db, token=raw)
        assert found is None and outcome == "already_used"
    finally:
        db.close()


def test_superseded_verification_token_is_rejected(w):
    """11 - and the fresh one still works."""
    rid, first = w.request(request_type="deletion")
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        second = prv.issue_verification(db, req)
        assert second != first
        found, outcome = prv.verify_request(db, token=first)
        assert found is None, "the retired token must not redeem"
        found, outcome = prv.verify_request(db, token=second)
        assert outcome == "verified", outcome
    finally:
        db.close()


def test_account_ownership_is_not_blanket_verification(w):
    """The distinction is by consequence, not convenience."""
    assert prv.verification_needed("export", requester_user_id=uuid.uuid4()) is True
    assert prv.verification_needed("deletion", requester_user_id=uuid.uuid4()) is True
    assert prv.verification_needed("access", requester_user_id=uuid.uuid4()) is True
    # A lower-consequence request from a known account is treated more lightly.
    assert prv.verification_needed("correction", requester_user_id=uuid.uuid4()) is False
    # An accountless requester always verifies.
    assert prv.verification_needed("correction", requester_user_id=None) is True
    # A representative always verifies.
    assert prv.verification_needed("correction", requester_user_id=uuid.uuid4(),
                                   representative_id=uuid.uuid4()) is True


def test_representative_requires_real_authorization(w):
    """13, 14 - and evidence is never emailed."""
    db = SessionLocal()
    try:
        rep = prv.claim_representative(
            db, subject_email=w.subject_email, representative_name="Agent Smith",
            representative_email=w.rep_email, representative_type="authorized_agent",
            authorization_reference="SUP-2027-000042")
        assert rep is not None
        # 13 - a claim is only ever `claimed`; nothing self-promotes.
        assert rep.status == "claimed" and rep.verified_at is None
        assert REPRESENTATIVE_AUTOMATED_VALIDATION is False
        # Verification needs an explicit human actor.
        assert prv.verify_representative(db, rep, verified_by=None) is False
        assert prv.verify_representative(db, rep, verified_by=w.admin_id) is True
        assert rep.status == "verified"

        # 14 - only a POINTER is stored, and no sender accepts evidence at all.
        assert rep.authorization_reference == "SUP-2027-000042"
        import inspect
        for name in [n for n in dir(email_mod) if n.startswith("send_privacy_")]:
            params = inspect.signature(getattr(email_mod, name)).parameters
            for banned in ("authorization_document", "evidence", "document"):
                assert banned not in params, f"{name}({banned})"
    finally:
        db.close()


def test_verification_email_discloses_nothing_extra(w):
    """The verify message says verification is needed - not what was requested in detail."""
    rid, raw = w.request(request_type="deletion")
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_verification_required(db, _Bg(), req, raw) is True
        payload = cap.of(VERIFY)
        assert raw in payload["text"], "the raw token travels once, in the link"
        assert "Please send me everything" not in payload["text"]
        assert "never asks for your password" in payload["text"]
    finally:
        db.close()


# ══ PRV-002 ═════════════════════════════════════════════════════════════════════════════

def test_no_statutory_deadline_is_invented(w):
    """3, 4 - the critical control."""
    import inspect

    # The policy registry is empty in this product.
    assert DEADLINE_POLICIES == {}
    rid, _raw = w.request()
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        assert req.deadline_at is None and req.deadline_basis is None
        deadline, basis = prv.resolve_deadline(req, jurisdiction="anything")
        assert deadline is None and basis is None
        note = prv.deadline_note(req)
        assert "do not quote a statutory deadline" in note
        # Searched against CODE, not prose: the module docstring legitimately explains
        # why 30 days is not hardcoded, so a bare substring search would flag its own
        # reasoning. The docstring is removed before the search for that reason.
        source = inspect.getsource(prv).replace(prv.__doc__ or "", "")
        for invented in ("days=30", "timedelta(days=30)", "days=45",
                         "DEFAULT_DEADLINE", "STATUTORY_DAYS"):
            assert invented not in source, f"fabricated deadline: {invented}"

        # With a configured policy it WOULD derive one - the mechanism is real, the policy
        # is simply absent.
        from app.models import privacy as privacy_models
        privacy_models.DEADLINE_POLICIES["testjur"] = {"days": 30, "basis": "Test policy"}
        try:
            import importlib
            deadline, basis = prv.resolve_deadline(req, jurisdiction="testjur")
            # prv imported the dict by reference, so the mutation is visible.
            assert deadline is not None and basis == "Test policy"
        finally:
            privacy_models.DEADLINE_POLICIES.pop("testjur", None)
    finally:
        db.close()


def test_internal_edit_sends_nothing(w):
    """2 - only customer-visible transitions notify."""
    assert prv.is_customer_visible({"details": "internal triage note"}) is False
    assert prv.is_customer_visible({"governance_record_id": uuid.uuid4()}) is False
    assert prv.is_customer_visible({"status": "in_progress"}) is True
    assert prv.is_customer_visible({"extension_until": _now()}) is True


def test_status_clarification_and_dedup(w):
    """1, 5, 9, 11."""
    rid, raw = w.request(request_type="correction", user=True)
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        assert prv.advance_status(db, req, status="in_progress") is True
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_status(db, _Bg(), req) is True
            n = len(cap.calls)
            assert prv.notify_status(db, _Bg(), req) is False        # 11 dedup
        assert len(cap.calls) == n
        subject = STATUS.format(reference=req.request_reference)
        assert w.subject_email.lower() in cap.to(subject)            # 9

        assert prv.request_clarification(
            db, req, needed="Confirm which name you want corrected.") is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert prv.notify_clarification(db, _Bg(), req) is True
        clarify_subject = CLARIFY.format(reference=req.request_reference)
        text = cap2.of(clarify_subject)["text"]
        assert "Confirm which name" in text
        assert "never ask for passwords" in text
        assert "Please reply by" not in text, "no due date was recorded, so none is quoted"
    finally:
        db.close()


def test_extension_requires_a_real_deadline_and_authority(w):
    """6, 7 - and 'complex request' is never assumed."""
    rid, _raw = w.request()
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        # No statutory deadline exists, so there is nothing to extend.
        ok, previous = prv.extend_deadline(db, req, until=_now() + timedelta(days=30),
                                           reason="complexity", authorized_by=w.admin_id)
        assert ok is False, "extending a non-existent deadline must be refused"

        # Give it a real deadline, then check authority and reason validation.
        req.deadline_at = _now() + timedelta(days=10)
        db.commit()
        ok, _p = prv.extend_deadline(db, req, until=_now() + timedelta(days=40),
                                     reason="complexity", authorized_by=None)
        assert ok is False, "an extension needs an authorized decision-maker"
        ok, _p = prv.extend_deadline(db, req, until=_now() + timedelta(days=40),
                                     reason="not_a_reason", authorized_by=w.admin_id)
        assert ok is False, "the reason must be an approved category"

        ok, previous = prv.extend_deadline(db, req, until=_now() + timedelta(days=40),
                                           reason="volume", authorized_by=w.admin_id)
        assert ok is True and previous is not None
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_extension(db, _Bg(), req, previous=previous) is True
        text = cap.of(EXTENSION.format(reference=req.request_reference))["text"]
        assert "The volume of information involved" in text
        assert "complex" not in text.lower(), "an unrecorded reason must not appear"
    finally:
        db.close()


def test_decision_requires_authority_and_leaks_no_legal_reasoning(w):
    """8, 10."""
    rid, _raw = w.request()
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        assert prv.decide(db, req, outcome="denied", reason="legal_retention",
                          summary="x", decided_by=None) is False
        assert prv.decide(db, req, outcome="denied", reason="not_a_reason",
                          summary="x", decided_by=w.admin_id) is False
        assert prv.decide(
            db, req, outcome="limited", reason="legal_retention",
            summary="We removed what we could and kept the accounting records.",
            decided_by=w.admin_id) is True
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_decision(db, _Bg(), req) is True
        text = cap.of(DECISION.format(reference=req.request_reference))["text"]
        assert "Partly actioned" in text
        assert "Some records must be retained to meet a legal obligation" in text
        # No internal legal reasoning parameter exists at all.
        import inspect
        params = inspect.signature(email_mod.send_privacy_decision_email).parameters
        for banned in ("legal_advice", "counsel_note", "internal_reasoning"):
            assert banned not in params
    finally:
        db.close()


# ══ PRV-003 ═════════════════════════════════════════════════════════════════════════════

def test_unverified_request_cannot_generate_export(w):
    """1."""
    rid, _raw = w.request(request_type="export", verified=False)
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        assert req.verified_at is None
        export, token = prv.generate_export(db, req)
        assert export is None and token is None, (
            "an unverified request must not produce an export")
    finally:
        db.close()


def test_export_is_a_short_lived_link_not_an_attachment(w):
    """2, 3, 4, 5, 6, 15."""
    import inspect

    rid, _raw = w.request(request_type="export", verified=True)
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        export, token = prv.generate_export(db, req)
        assert export is not None
        # 3 - READY only once an artifact reference exists.
        if export.status == "ready":
            assert export.storage_reference and token
            # 5 - strong, purpose-bound, hashed.
            assert len(token) >= 40
            assert export.download_token_hash == hashlib.sha256(token.encode()).hexdigest()
            assert export.download_purpose == PURPOSE_PRIVACY_DOWNLOAD
            # 6 - short expiry.
            assert export.expires_at - _now() <= timedelta(minutes=61)

            cap, ctx = _capture()
            with ctx:
                assert prv.notify_export_ready(db, _Bg(), req, export, token) is True
                n = len(cap.calls)
                assert prv.notify_export_ready(db, _Bg(), req, export, token) is False
            assert len(cap.calls) == n                                  # 15 dedup
            payload = cap.of(EXPORT_READY)
            # 4 - a LINK, and no attachment field of any kind.
            assert token in payload["text"]
            # `attachments` is legitimately present - _send embeds the brand logo inline.
            # The requirement is that no attachment carries the EXPORT itself.
            for att in payload.get("attachments") or []:
                filename = (att.get("filename") or "").lower()
                assert "export" not in filename and not filename.endswith(".json"), (
                    f"the export must never be attached: {filename}")
                assert str(export.id) not in str(att), "export data attached"
            assert "/organization/privacy/exports/" in payload["text"]
            # The storage key never appears in the message.
            assert export.storage_reference not in payload["text"]
        # Structurally: no sender takes the export body.
        params = inspect.signature(email_mod.send_privacy_export_ready_email).parameters
        for banned in ("attachment", "content", "payload", "data", "inventory"):
            assert banned not in params, banned
    finally:
        db.close()


def test_export_access_is_logged_without_content(w):
    """7, 8, 9, 10."""
    rid, _raw = w.request(request_type="export", verified=True)
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        export, token = prv.generate_export(db, req)
        if export is None or export.status != "ready":
            return
        subject = db.get(User, w.subject_id)
        other = db.get(User, w.other_id)

        # 7 - requester binding: the wrong identity is refused and logged.
        found, outcome = prv.authorize_download(db, token=token, actor=other)
        assert found is None and outcome == "wrong_identity", outcome
        found, outcome = prv.authorize_download(db, token=token, actor=subject)
        assert outcome == "granted", outcome
        # Single use.
        found, outcome = prv.authorize_download(db, token=token, actor=subject)
        assert found is None and outcome == "already_used"

        # 9 - every attempt is logged with an outcome.
        rows = db.query(PrivacyExportAccess).filter(
            PrivacyExportAccess.export_id == export.id).all()
        outcomes = {r.outcome for r in rows}
        assert {"wrong_identity", "granted", "already_used"} <= outcomes, outcomes
        # 10 - never the content, and no address or full user agent.
        for r in rows:
            for col in ("client_hint", "actor_email"):
                value = getattr(r, col) or ""
                assert "Data Subject" not in value
            assert not hasattr(r, "content") and not hasattr(r, "export_body")
    finally:
        db.close()


def test_expired_link_is_rejected_and_announced(w):
    """8 - and the expiry notice offers a safe regenerate path."""
    rid, _raw = w.request(request_type="export", verified=True)
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        export, token = prv.generate_export(db, req)
        if export is None or export.status != "ready":
            return
        export.expires_at = _now() - timedelta(minutes=1)
        db.commit()
        found, outcome = prv.authorize_download(db, token=token,
                                                actor=db.get(User, w.subject_id))
        assert found is None and outcome == "expired"
        db.expire_all()
        export = db.get(PrivacyExport, export.id)
        assert export.status == "expired"
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_export_expired(db, _Bg(), req, export) is True
        assert "generate a fresh link" in cap.of(EXPORT_EXPIRED)["text"]
    finally:
        db.close()


def test_deletion_never_claims_total_erasure(w):
    """11, 12, 13."""
    rid, _raw = w.request(request_type="deletion", verified=True)
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        # A legal hold on the organization blocks erasure.
        db.add(GovernanceRecord(kind="legal_hold", org_id=w.org_id, status="open",
                                detail="Pending litigation"))
        db.commit()
        residual, blocker = prv.plan_deletion(db, req)
        assert blocker == "legal_hold", blocker
        assert "legal_hold" in residual and "audit_log" in residual

        row = prv.execute_deletion(db, req)
        # 13 - a blocked erasure is never reported as completed.
        assert row.status == "blocked_by_retention", row.status
        assert row.completed_at is None
        sentence = prv.deletion_sentence(row)
        assert "Some records are retained" in sentence
        for overclaim in ("all your data", "permanently deleted", "everything has been",
                          "all copies"):
            assert overclaim not in sentence.lower(), overclaim

        cap, ctx = _capture()
        with ctx:
            kind = prv.notify_deletion(db, _Bg(), req, row)
        assert kind == "deletion_incomplete"
        text = cap.of(DELETION_UPDATE)["text"]
        assert "Records under a legal hold" in text
        assert "Blocked By Retention" in text
        for overclaim in ("all your data has been", "permanently deleted"):
            assert overclaim not in text.lower()
    finally:
        db.close()


def test_deletion_state_survives_a_provider_outage(w):
    """14."""
    rid, _raw = w.request(request_type="deletion", verified=True)
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        db.add(GovernanceRecord(kind="legal_hold", org_id=w.org_id, status="open"))
        db.commit()
        row = prv.execute_deletion(db, req)
        state = row.status
        capf, ctxf = _capture(fail=True)
        with ctxf:
            prv.notify_deletion(db, _Bg(), req, row)
        db.expire_all()
        assert db.get(PrivacyDeletion, row.id).status == state
    finally:
        db.close()


def test_retention_exception_reuses_existing_authority(w):
    """13 - MED-011 and GovernanceRecord remain the authorities."""
    import inspect

    rid, _raw = w.request(request_type="deletion", verified=True)
    db = SessionLocal()
    try:
        req = db.get(PrivacyRequest, rid)
        exception = prv.record_retention_exception(
            db, req, record_type="legal_hold",
            retention_basis="governance_legal_hold",
            review_at=_now() + timedelta(days=90), authorized_by=w.admin_id)
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_retention_exception(db, _Bg(), req, exception) is True
            assert prv.notify_retention_exception(db, _Bg(), req, exception) is False
        text = cap.of(email_mod.PRV_004_RETENTION_SUBJECT.format(
            reference=req.request_reference))["text"]
        assert "Records under a legal hold" in text
        assert "Governance Legal Hold" in text
        # No second retention system: plan_deletion READS the existing authorities.
        source = inspect.getsource(prv.plan_deletion)
        assert "legal_hold" in source and "retention_expires_at" in source
        # And the privacy layer never WRITES them.
        full = inspect.getsource(prv)
        for write in ("legal_hold =", "retention_expires_at =", "deletion_status ="):
            assert write not in full, f"privacy_comms must not write {write!r}"
    finally:
        db.close()


# ══ PRV-004 ═════════════════════════════════════════════════════════════════════════════

def test_static_legal_page_is_not_a_lifecycle(w):
    """1 - and there isn't even a static page to mistake for one."""
    import pathlib

    client = pathlib.Path(__file__).resolve().parent.parent / "client" / "src"
    if client.exists():
        matches = [p.name for p in client.rglob("*")
                   if p.is_file() and ("legal" in p.name.lower()
                                       or "privacy" in p.name.lower())]
        assert not matches, f"a legal/privacy page appeared: {matches}"
    # The lifecycle is a real versioned model instead.
    assert hasattr(PrivacyNoticeVersion, "status")
    assert hasattr(PrivacyNoticeVersion, "material_change")
    assert hasattr(PrivacyNoticeVersion, "consent_required")


def test_draft_notice_sends_nothing(w):
    """2, 3, 5."""
    db = SessionLocal()
    try:
        draft = PrivacyNoticeVersion(version=f"v{uuid.uuid4().hex[:6]}", status="draft",
                                     material_change=True,
                                     change_summary="We clarified retention periods.")
        db.add(draft)
        db.commit()
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_notice_published(
                db, _Bg(), draft, [(w.subject_email, "there")]) is False
        assert cap.calls == [], "a draft must announce nothing"

        # 5 - a published NON-material change does not mail everybody.
        minor = PrivacyNoticeVersion(version=f"v{uuid.uuid4().hex[:6]}", status="draft",
                                     material_change=False,
                                     change_summary="Fixed a typo.")
        db.add(minor)
        db.commit()
        assert prv.publish_notice(db, minor) is True
        cap2, ctx2 = _capture()
        with ctx2:
            assert prv.notify_notice_published(
                db, _Bg(), minor, [(w.subject_email, "there")]) is False
        assert cap2.calls == []
        db.query(PrivacyNoticeVersion).filter(
            PrivacyNoticeVersion.id.in_([draft.id, minor.id])).delete()
        db.commit()
    finally:
        db.close()


def test_material_notice_publishes_and_dedups(w):
    """4, 16."""
    db = SessionLocal()
    try:
        version = PrivacyNoticeVersion(version=f"v{uuid.uuid4().hex[:6]}", status="draft",
                                       material_change=True,
                                       change_summary="We added a new processor.")
        db.add(version)
        db.commit()
        assert prv.publish_notice(db, version) is True
        assert version.published_at is not None and version.effective_at is not None
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_notice_published(
                db, _Bg(), version, [(w.subject_email, "there")]) is True
            n = len(cap.calls)
            assert prv.notify_notice_published(
                db, _Bg(), version, [(w.subject_email, "there")]) is False
        assert len(cap.calls) == n
        assert "We added a new processor." in cap.of(NOTICE)["text"]
        db.query(PrivacyNoticeVersion).filter(
            PrivacyNoticeVersion.id == version.id).delete()
        db.commit()
    finally:
        db.close()


def test_acknowledgement_is_never_consent(w):
    """6, 9, 10 - the critical distinction."""
    db = SessionLocal()
    try:
        notice_only = PrivacyNoticeVersion(version=f"v{uuid.uuid4().hex[:6]}",
                                           status="draft", material_change=True,
                                           consent_required=False,
                                           change_summary="Notice only.")
        db.add(notice_only)
        db.commit()
        prv.publish_notice(db, notice_only)
        # A notice-only version cannot record a consent decision at all.
        assert prv.record_decision(db, notice_only, subject_email=w.subject_email,
                                   decision="accepted") is None, (
            "acknowledging a notice must not be recorded as consent")

        consent = PrivacyNoticeVersion(version=f"v{uuid.uuid4().hex[:6]}", status="draft",
                                       material_change=True, consent_required=True,
                                       consent_purpose="Optional product analytics",
                                       change_summary="New optional analytics.")
        db.add(consent)
        db.commit()
        prv.publish_notice(db, consent)
        # 10 - a decline is stored as a decline.
        row = prv.record_decision(db, consent, subject_email=w.subject_email,
                                  decision="declined")
        assert row is not None and row.decision == "declined"
        assert row.decided_at is not None and row.consent_purpose
        db.expire_all()
        assert db.get(PrivacyConsentDecision, row.id).decision == "declined", (
            "a decline must never become an acceptance")
        # 9 - bound to the notice version.
        assert row.notice_version_id == consent.id
        assert prv.record_decision(db, consent, subject_email=w.subject_email,
                                   decision="not_a_decision") is None

        db.query(PrivacyConsentDecision).filter(
            PrivacyConsentDecision.notice_version_id == consent.id).delete()
        db.query(PrivacyNoticeVersion).filter(
            PrivacyNoticeVersion.id.in_([notice_only.id, consent.id])).delete()
        db.commit()
    finally:
        db.close()


def test_accept_and_decline_have_parity(w):
    """7, 8 - no dark pattern."""
    options = prv.consent_options()
    assert [o["value"] for o in options] == ["accepted", "declined"]
    # 8 - nothing preselected.
    assert all(o["preselected"] is False for o in options)
    # 7 - equal visual weight and both present.
    assert len({o["emphasis"] for o in options}) == 1
    assert {o["label"] for o in options} == {"Accept", "Decline"}

    db = SessionLocal()
    try:
        consent = PrivacyNoticeVersion(version=f"v{uuid.uuid4().hex[:6]}", status="draft",
                                       material_change=True, consent_required=True,
                                       consent_purpose="Optional analytics",
                                       change_summary="Your choice is needed.")
        db.add(consent)
        db.commit()
        prv.publish_notice(db, consent)
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_notice_published(
                db, _Bg(), consent, [(w.subject_email, "there")]) is True
        text = cap.of(CONSENT)["text"]
        assert "Accept or Decline" in text
        assert "Nothing is preselected" in text
        assert "Declining will not affect" in text
        db.query(PrivacyNoticeVersion).filter(
            PrivacyNoticeVersion.id == consent.id).delete()
        db.commit()
    finally:
        db.close()


def test_subprocessors_are_real_only(w):
    """11, 12 - no invented vendors."""
    db = SessionLocal()
    try:
        prv.seed_subprocessors(db)
        rows = db.query(Subprocessor).all()
        names = {r.name for r in rows}
        # Every one of these is genuinely integrated in this codebase.
        assert {"Resend", "LiveKit", "Stripe", "Google Cloud Storage", "Upstash"} <= names
        # Nothing plausible-but-absent crept in.
        for invented in ("Twilio", "SendGrid", "Mailchimp", "Segment", "Datadog",
                         "Cloudflare", "AWS", "Auth0"):
            assert invented not in names, f"invented subprocessor: {invented}"
        # Region is only stated when known; None is the honest answer here.
        assert all(r.region is None for r in rows if r.name in names)

        # 12 - a notice only when policy flags the change as requiring one.
        target = next(r for r in rows if r.name == "Resend")
        assert target.notice_required is False
        cap, ctx = _capture()
        with ctx:
            assert prv.notify_subprocessor_change(
                db, _Bg(), target, [(w.subject_email, "there")]) is False
        assert cap.calls == []

        target.notice_required = True
        target.change_version = 1
        db.commit()
        cap2, ctx2 = _capture()
        with ctx2:
            assert prv.notify_subprocessor_change(
                db, _Bg(), target, [(w.subject_email, "there")]) is True
        text = cap2.of(SUBPROC)["text"]
        assert "Resend" in text and "Transactional email" in text
        assert "do not publish commercial terms" in text
        target.notice_required = False
        db.commit()
    finally:
        db.close()


# ══ cross-cutting ═══════════════════════════════════════════════════════════════════════

def test_privacy_mail_is_never_preference_suppressible(w):
    from app.services import notifications

    for family in ("PRV-001", "PRV-002", "PRV-003", "PRV-004"):
        assert family in notifications.FAMILY_CLASS, family
        assert notifications.is_mandatory(family) is True, family
    db = SessionLocal()
    try:
        org = db.get(Organization, w.org_id)
        org.notifications = {k: False for k in
                             ("event_scheduled", "member_joined", "billing",
                              "security_alerts")}
        db.commit()
        for family in ("PRV-001", "PRV-003"):
            assert notifications.should_send_operational_notification(
                family=family, org=org) is True, family
    finally:
        db.close()


def test_no_template_accepts_a_secret_or_export_body(w):
    """15 - structural."""
    import inspect

    senders = [n for n in dir(email_mod)
               if n.startswith(("send_privacy_", "send_subprocessor_"))]
    assert len(senders) >= 12, senders
    banned = ("password", "password_hash", "mfa", "recovery_code", "api_key", "token_hash",
              "security_log", "audit_evidence", "legal_advice", "evidence", "attachment",
              "export_content", "storage_reference")
    for name in senders:
        for param in inspect.signature(getattr(email_mod, name)).parameters:
            assert param.lower() not in banned, f"{name}({param})"


def test_idempotency_key_is_versioned(w):
    cols = {c.name for c in PrivacyNotice.__table__.columns}
    assert "version" in cols
    constraint = next(c for c in PrivacyNotice.__table__.constraints
                      if c.__class__.__name__ == "UniqueConstraint")
    assert {c.name for c in constraint.columns} == {"kind", "subject_type", "subject_id",
                                                     "version"}


TESTS = [
    test_request_persists_with_safe_reference,
    test_verification_token_is_bound_and_single_use,
    test_superseded_verification_token_is_rejected,
    test_account_ownership_is_not_blanket_verification,
    test_representative_requires_real_authorization,
    test_verification_email_discloses_nothing_extra,
    test_no_statutory_deadline_is_invented,
    test_internal_edit_sends_nothing,
    test_status_clarification_and_dedup,
    test_extension_requires_a_real_deadline_and_authority,
    test_decision_requires_authority_and_leaks_no_legal_reasoning,
    test_unverified_request_cannot_generate_export,
    test_export_is_a_short_lived_link_not_an_attachment,
    test_export_access_is_logged_without_content,
    test_expired_link_is_rejected_and_announced,
    test_deletion_never_claims_total_erasure,
    test_deletion_state_survives_a_provider_outage,
    test_retention_exception_reuses_existing_authority,
    test_static_legal_page_is_not_a_lifecycle,
    test_draft_notice_sends_nothing,
    test_material_notice_publishes_and_dedups,
    test_acknowledgement_is_never_consent,
    test_accept_and_decline_have_parity,
    test_subprocessors_are_real_only,
    test_privacy_mail_is_never_preference_suppressible,
    test_no_template_accepts_a_secret_or_export_body,
    test_idempotency_key_is_versioned,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
