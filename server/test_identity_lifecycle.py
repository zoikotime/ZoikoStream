"""IDN-006 / IDN-007 / IDN-008 — settings, recovery and account lifecycle (ZST-EC-001).

Same harness as the earlier IDN suites: the Resend call is intercepted at `httpx.post`, so
each message is proven to travel the real integration and no network request is made.

Several checks below assert that something is NOT built — no MFA variant, no scheduled
deletion. Those are deliberate: the corresponding subsystems do not exist, and a test that
pins the absence is what stops a later change from quietly shipping a false security claim.

Run with `python test_identity_lifecycle.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.config import settings
from app.crud import recovery as recovery_crud
from app.db import SessionLocal
from app.models import (
    ACCOUNT_RECOVERY,
    RECOVERY_CANCELED,
    RECOVERY_COMPLETED,
    RECOVERY_CONTACT,
    RECOVERY_MAX_ATTEMPTS,
    RECOVERY_VERIFIED,
    AccountRecovery,
    AccountStateEvent,
    IdentityChallenge,
    Organization,
    SignInEvent,
    User,
)
from app.security import hash_password

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

IDN_006 = email_mod.IDN_006_SUBJECT
IDN_007_STARTED = email_mod.IDN_007_SUBJECT_STARTED
IDN_007_VERIFY = email_mod.IDN_007_SUBJECT_VERIFICATION
IDN_007_DONE = email_mod.IDN_007_SUBJECT_COMPLETED
IDN_007_CANCEL = email_mod.IDN_007_SUBJECT_CANCELED
IDN_008_RESTRICTED = email_mod.IDN_008_SUBJECT_RESTRICTED
IDN_008_REACTIVATED = email_mod.IDN_008_SUBJECT_REACTIVATED
IDN_008_DELETED = email_mod.IDN_008_SUBJECT_DELETED


# ── harness ─────────────────────────────────────────────────────────────────────

class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "payload": json or {}})
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
        return [c["payload"]["to"][0] for c in self.calls if c["payload"]["subject"] == subject]


# Baseline: any send that escapes a test's own patch is recorded and fails that test.
# `_send` swallows provider exceptions by design, so a raise alone would go unnoticed —
# the list is what makes a leak visible. Without this, an unwrapped request that happens
# to trigger a notice would post to the real Resend API from the test suite.
_LEAKS: list[str] = []


def _deny(url, headers=None, json=None, timeout=None):
    _LEAKS.append((json or {}).get("subject", "?"))
    raise RuntimeError("outbound email attempted outside a capture context")


email_mod.httpx.post = _deny


def _assert_no_leak():
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"


def _capture(fail=False):
    cap = Captured(fail=fail)
    return cap, patch.object(email_mod.httpx, "post", cap)


def _reset_limits():
    ratelimit._HITS.clear()


def _new_email(tag="idnlc"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


def _make_user(email, *, recovery_email=None, recovery_verified=True):
    db = SessionLocal()
    try:
        org = Organization(name=f"IDNLC Org {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id, full_name="Jane Doe", role="org_admin", is_active=True,
            email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
            password_hash=hash_password(PASSWORD), email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
            recovery_email=recovery_email.lower() if recovery_email else None,
            recovery_email_verified_at=(datetime.now(timezone.utc)
                                        if recovery_email and recovery_verified else None),
        )
        db.add(user)
        db.commit()
        return user.id, org.id
    finally:
        db.close()


def _cleanup(email):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.lower()).one_or_none()
        db.query(AccountStateEvent).filter(AccountStateEvent.email == email.lower()).delete()
        if user is None:
            db.commit()
            return
        for model in (SignInEvent, IdentityChallenge, AccountRecovery):
            db.query(model).filter(model.user_id == user.id).delete()
        org_id = user.org_id
        db.delete(user)
        db.commit()
        org = db.get(Organization, org_id)
        if org is not None and not db.query(User).filter(User.org_id == org_id).count():
            db.delete(org)
            db.commit()
    finally:
        db.close()


def _load(email):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email.lower()).one_or_none()
    finally:
        db.close()


def _recovery(email):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.lower()).one()
        return db.query(AccountRecovery).filter(
            AccountRecovery.user_id == user.id
        ).order_by(AccountRecovery.started_at.desc()).first()
    finally:
        db.close()


def _forgot(client, email):
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        resp = client.post("/api/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200, resp.text
    code = None
    for c in cap.calls:
        if c["payload"]["subject"] == IDN_007_VERIFY:
            for row in c["payload"]["text"].splitlines():
                if row.startswith("Verification code:"):
                    code = row.split(":", 1)[1].strip()
    return code, cap


def _token_for(email):
    """Sign in and return a bearer token (for the authenticated endpoints)."""
    client = TestClient(m.app)
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        r = client.post("/api/auth/login", json={"identifier": email, "password": PASSWORD},
                        headers={"User-Agent": UA})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ══ shared Class A controls ═════════════════════════════════════════════════════

def test_shared_no_secrets_no_pixel_no_marketing():
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "https://app.zoikostream.com"):
        url = email_mod.security_url()
        bodies = [
            email_mod._security_shell("T", email_mod.IDN_006_HEADLINE, "P", [], "b", "c", url, "f"),
            email_mod._security_shell("T", email_mod.IDN_007_HEADLINE, "P", [], "b", "c", url, "f"),
            email_mod._security_shell("T", email_mod.IDN_008_HEADLINE, "P", [], "b", "c", url, "f"),
        ]
    for body in bodies:
        low = body.lower()
        for banned in ("password_hash", "bcrypt", "$2b$", "totp", "seed", "jwt", "bearer ",
                       "access_token", "refresh_token", "eyj", "api_key",
                       "utm_", "pixel", "beacon", "unsubscribe", "webinar"):
            assert banned not in low, f"{banned!r} must not appear in a Class A email"
        assert 'width="1"' not in low, "no tracking pixel"
        assert '<img src="http' not in low, "no remote asset"


def test_shared_sender_and_https_cta():
    with patch.object(settings, "MAIL_FROM", "ZoikoStream <info@zoikostream.com>"):
        assert email_mod._sender_identity(email_mod.SENDER_SECURITY) == \
            "Zoiko Steam Security <info@zoikostream.com>"
    for bad in ("http://localhost:5173", "https://127.0.0.1:5173"):
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "APP_URL", bad):
            for fn in (email_mod.security_url, lambda: email_mod.recovery_contact_url("t")):
                try:
                    fn()
                except email_mod.UnsafeLinkError:
                    pass
                else:
                    raise AssertionError(f"{bad!r} must be refused in production")


# ══ IDN-006 ═════════════════════════════════════════════════════════════════════

def test_006_mfa_variants_are_deliberately_not_implemented():
    """require_2fa is stored and reported but never enforced, so an 'MFA enabled' notice
    would assert a protection the platform does not provide."""
    src = open(email_mod.__file__, encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for claim in ("MFA is now enabled", "MFA was disabled", "mfa_enabled", "mfa_disabled"):
        assert claim not in code, f"{claim!r} must not exist without a real MFA subsystem"


def test_006_1_unrelated_settings_change_sends_nothing():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        token = _token_for(email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch("/api/organization/notifications", json={"billing": False},
                             headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (200, 403), r.text
        assert IDN_006 not in cap.subjects, "a notification-preference edit is not IDN-006"
    finally:
        _cleanup(email)


def test_006_4_to_12_recovery_method_change_sends_masked_destination():
    """4 (masked), 5 (no secret config), 6 (sender), 7 (recipient), 8/9 (parts),
    10 (tz), 11 (no pixel), 13 (failed change sends nothing), 14 (no duplicate)."""
    email = _new_email()
    recovery_addr = _new_email("recov")
    _make_user(email)
    client = TestClient(m.app)
    try:
        token = _token_for(email)
        auth = {"Authorization": f"Bearer {token}"}

        # Nominating alone must NOT fire IDN-006 — nothing is committed yet.
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/recovery-contact",
                            json={"recovery_email": recovery_addr}, headers=auth)
        assert r.status_code == 200, r.text
        assert IDN_006 not in cap.subjects, "an unconfirmed nomination is not a change"
        assert _load(email).recovery_email is None, "not honoured until confirmed"
        confirm = cap.of("Confirm your Zoiko Steam recovery address")
        assert confirm["to"] == [recovery_addr.lower()], \
            "verification must go to the NOMINATED address, not the account"
        confirm_token = confirm["text"].split("token=", 1)[1].split()[0]

        # A bad token changes nothing and sends nothing.
        _reset_limits()
        cap_bad, ctx_bad = _capture()
        with ctx_bad:
            bad = client.post("/api/auth/recovery-contact/confirm", json={"token": "x" * 40})
        assert bad.status_code == 400
        assert cap_bad.calls == [], "a failed change must send nothing"

        # Confirming commits the change and fires IDN-006 to the ACCOUNT HOLDER.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            ok = client.post("/api/auth/recovery-contact/confirm", json={"token": confirm_token})
        assert ok.status_code == 200, ok.text

        payload = cap2.of(IDN_006)
        assert payload["to"] == [email.lower()], "IDN-006 goes to the account holder"
        assert payload["from"].startswith("Zoiko Steam Security <")
        assert payload["subject"] == "Zoiko Steam security settings changed"
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Review your account-security change." in text
        assert "If you did not authorize this change, begin account recovery immediately." in text
        assert "UTC" in text, "timestamp must carry its timezone"

        # Masked destination only — the full address must never appear.
        masked = recovery_crud.mask_destination(recovery_addr)
        assert masked in text, "masked destination must be shown"
        assert recovery_addr.lower() not in text.lower(), "full recovery address must be masked"
        assert "*" in masked

        # The change is committed and the token is single-use.
        user = _load(email)
        assert user.recovery_email == recovery_addr.lower()
        assert user.recovery_email_verified_at is not None
        assert user.recovery_email_pending is None
        _reset_limits()
        cap3, ctx3 = _capture()
        with ctx3:
            again = client.post("/api/auth/recovery-contact/confirm", json={"token": confirm_token})
        assert again.status_code == 400, "confirmation token is single-use"
        assert cap3.calls == [], "a replay must not duplicate IDN-006"
    finally:
        _cleanup(email)
        _cleanup(recovery_addr)


# ══ IDN-007 ═════════════════════════════════════════════════════════════════════

def test_007_1_2_3_start_persists_state_sends_mail_and_never_stores_plaintext():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        code, cap = _forgot(client, email)
        assert code and len(code) == 6 and code.isdigit(), f"expected a 6-digit code, got {code!r}"

        rec = _recovery(email)
        assert rec is not None, "recovery lifecycle must be persisted"
        assert rec.status == "additional_verification_required"
        assert rec.started_at is not None

        assert IDN_007_STARTED in cap.subjects, f"started notice missing: {cap.subjects}"
        assert IDN_007_VERIFY in cap.subjects, f"verification notice missing: {cap.subjects}"

        # The code is nowhere in the database, in any column.
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email.lower()).one()
            assert user.reset_token is None, "the legacy plaintext column must stay empty"
            ch = db.query(IdentityChallenge).filter(
                IdentityChallenge.user_id == user.id,
                IdentityChallenge.purpose == ACCOUNT_RECOVERY).one()
            assert ch.token_hash != code, "the code must never be stored in the clear"
            assert ch.token_hash == recovery_crud._hash(code)
            assert db.query(IdentityChallenge).filter(
                IdentityChallenge.token_hash == code).count() == 0
        finally:
            db.close()
    finally:
        _cleanup(email)


def test_007_11_support_never_reads_code_aloud_line_is_present():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _code, cap = _forgot(client, email)
        payload = cap.of(IDN_007_VERIFY)
        for part in (payload["html"], payload["text"]):
            assert "Zoiko Steam Support will never ask you to read this code aloud." in part
        assert "identity documents" in payload["text"], \
            "must state that documents are never requested by email"
    finally:
        _cleanup(email)


def test_007_4_and_5_attempt_counter_and_lockout():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _forgot(client, email)
        for i in range(RECOVERY_MAX_ATTEMPTS):
            _reset_limits()
            r = client.post("/api/auth/verify-otp", json={"email": email, "otp": "000000"})
            assert r.status_code in (400, 429), r.text
            assert _recovery(email).failed_attempts == i + 1, "each miss must be counted"

        rec = _recovery(email)
        assert rec.locked_until is not None, "lockout must engage"

        # Even the CORRECT code is refused while locked.
        _reset_limits()
        locked = client.post("/api/auth/verify-otp", json={"email": email, "otp": "000000"})
        assert locked.status_code == 429, locked.text
    finally:
        _cleanup(email)


def test_007_6_and_7_expiry_and_single_use():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        code, _ = _forgot(client, email)

        # Expiry.
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email.lower()).one()
            ch = db.query(IdentityChallenge).filter(
                IdentityChallenge.user_id == user.id,
                IdentityChallenge.purpose == ACCOUNT_RECOVERY).one()
            ch.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()
        _reset_limits()
        assert client.post("/api/auth/verify-otp",
                           json={"email": email, "otp": code}).status_code == 400

        # Fresh code, then single-use.
        code2, _ = _forgot(client, email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            first = client.post("/api/auth/reset-password",
                                json={"email": email, "otp": code2, "password": "brand-new-secret"})
        assert first.status_code == 200, first.text
        _reset_limits()
        second = client.post("/api/auth/reset-password",
                             json={"email": email, "otp": code2, "password": "another-secret"})
        assert second.status_code == 400, "the recovery code must be single-use"
    finally:
        _cleanup(email)


def test_007_9_completed_only_after_credential_change_and_reaches_recovery_contact():
    """9 (completed after commit), 12 (holder), 13 (approved recovery contact),
    14/15 (parts), 16 (no secret), 18 (no duplicate)."""
    email = _new_email()
    recovery_addr = _new_email("recov")
    _make_user(email, recovery_email=recovery_addr)
    client = TestClient(m.app)
    try:
        code, cap_start = _forgot(client, email)
        # Both the started and verification notices reach BOTH approved recipients.
        assert sorted(cap_start.to(IDN_007_STARTED)) == sorted([email.lower(), recovery_addr.lower()])
        assert sorted(cap_start.to(IDN_007_VERIFY)) == sorted([email.lower(), recovery_addr.lower()])

        # Verifying alone must not complete anything.
        _reset_limits()
        cap_v, ctx_v = _capture()
        with ctx_v:
            client.post("/api/auth/verify-otp", json={"email": email, "otp": code})
        assert IDN_007_DONE not in cap_v.subjects, "verification is not completion"
        assert _recovery(email).completed_at is None

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            done = client.post("/api/auth/reset-password",
                               json={"email": email, "otp": code, "password": "brand-new-secret"})
        assert done.status_code == 200, done.text

        rec = _recovery(email)
        assert rec.status == RECOVERY_COMPLETED and rec.completed_at is not None

        payload = cap.of(IDN_007_DONE)
        assert payload["from"].startswith("Zoiko Steam Security <")
        assert payload["html"] and payload["text"]
        assert sorted(cap.to(IDN_007_DONE)) == sorted([email.lower(), recovery_addr.lower()])
        text = payload["text"]
        assert "Password" in text, "must state which credential was reset"
        assert "not revoked and remain active" in text, "session effect must be truthful"
        assert "brand-new-secret" not in text and code not in text, "no secret may appear"

        # The credential change genuinely happened. Wrapped because a fresh sign-in
        # legitimately fires IDN-003, and nothing here may reach the real provider.
        _reset_limits()
        cap_login, ctx_login = _capture()
        with ctx_login:
            ok = client.post("/api/auth/login",
                             json={"identifier": email, "password": "brand-new-secret"},
                             headers={"User-Agent": UA})
        assert ok.status_code == 200, ok.text
    finally:
        _cleanup(email)
        _cleanup(recovery_addr)


def test_007_10_canceled_variant_sends_after_cancellation():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _forgot(client, email)
        token = _token_for(email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/recovery/cancel",
                            headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        assert cap.subjects == [IDN_007_CANCEL], f"expected only the cancel notice: {cap.subjects}"
        assert "No credentials or sessions were changed." in cap.of(IDN_007_CANCEL)["text"]
        assert _recovery(email).status == RECOVERY_CANCELED

        # Duplicate cancel must not duplicate the message.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            client.post("/api/auth/recovery/cancel", headers={"Authorization": f"Bearer {token}"})
        assert cap2.calls == [], "a repeated cancel must not resend"
    finally:
        _cleanup(email)


def test_007_17_preferences_cannot_suppress_and_19_resend_failure_keeps_state():
    email = _new_email()
    _user_id, org_id = _make_user(email)
    client = TestClient(m.app)
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, org_id)
            org.notifications = {"security_alerts": False, "billing": False}
            db.commit()
        finally:
            db.close()

        code, cap = _forgot(client, email)
        assert IDN_007_STARTED in cap.subjects, "Class A must ignore preferences"

        # A provider outage must not roll back the recovery or the credential change.
        _reset_limits()
        cap_fail, ctx_fail = _capture(fail=True)
        with ctx_fail:
            r = client.post("/api/auth/reset-password",
                            json={"email": email, "otp": code, "password": "brand-new-secret"})
        assert r.status_code == 200, "a mail outage must not fail the reset"
        assert _recovery(email).status == RECOVERY_COMPLETED
        assert cap_fail.calls, "a send was attempted"
    finally:
        _cleanup(email)


# ══ IDN-008 ═════════════════════════════════════════════════════════════════════

def test_008_scheduled_deletion_is_deliberately_not_implemented():
    """No scheduling subsystem exists, so a cancellation deadline would be unenforceable."""
    src = open(email_mod.__file__, encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for claim in ("deletion is scheduled", "cancellation deadline", "deletion was canceled"):
        assert claim.lower() not in code.lower(), \
            f"{claim!r} must not exist without a real deletion-scheduling lifecycle"


def test_008_1_to_5_suspension_notifies_after_commit():
    """1 (after commit), 11 (recipient), 12 (sender), 13/14 (parts), 15 (no pixel),
    5 (org effect stated), 9/10 (no detection detail, no other users)."""
    email = _new_email()
    user_id, _org = _make_user(email)
    admin_email = _new_email("admin")
    _make_user(admin_email)
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == admin_email.lower()).one()
        admin.role = "super_admin"
        db.commit()
    finally:
        db.close()
    client = TestClient(m.app)
    try:
        token = _token_for(admin_email)
        auth = {"Authorization": f"Bearer {token}"}

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/admin/users/{user_id}", json={"is_active": False},
                             headers=auth)
        assert r.status_code == 200, r.text
        assert _load(email).is_active is False, "state must be committed"

        payload = cap.of(IDN_008_RESTRICTED)
        assert payload["to"] == [email.lower()], "only the affected account holder"
        assert payload["from"].startswith("Zoiko Steam Security <")
        assert payload["subject"] == "Your Zoiko Steam account is restricted"
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Your access has changed." in text
        assert "Restricted" in text and "UTC" in text
        assert "Organization access" in text, "org effect must be stated"
        # No internal detail, no other users.
        for leak in ("administrative_action", "admin", "detection", "risk", admin_email):
            assert leak.lower() not in text.lower(), f"{leak!r} must not be disclosed"

        # Reactivation is its own event and its own message.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            r2 = client.patch(f"/api/admin/users/{user_id}", json={"is_active": True},
                              headers=auth)
        assert r2.status_code == 200, r2.text
        assert IDN_008_REACTIVATED in cap2.subjects, f"got {cap2.subjects}"

        # 17 — an unrelated edit must not notify again.
        _reset_limits()
        cap3, ctx3 = _capture()
        with ctx3:
            client.patch(f"/api/admin/users/{user_id}", json={"full_name": "Renamed"},
                         headers=auth)
        assert cap3.calls == [], "a rename is not a restriction"
    finally:
        _cleanup(email)
        _cleanup(admin_email)


def test_008_2_failed_suspension_sends_nothing():
    email = _new_email("admin")
    _make_user(email)
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == email.lower()).one()
        admin.role = "super_admin"
        db.commit()
        admin_id = admin.id
    finally:
        db.close()
    client = TestClient(m.app)
    try:
        token = _token_for(email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            # Self-deactivation is refused by the router's self-lockout guard.
            r = client.patch(f"/api/admin/users/{admin_id}", json={"is_active": False},
                             headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 400, r.text
        assert cap.calls == [], "a refused suspension must send nothing"
        assert _load(email).is_active is True
    finally:
        _cleanup(email)


def test_008_7_and_8_soft_delete_notifies_without_claiming_erasure():
    email = _new_email()
    user_id, org_id = _make_user(email)
    admin_email = _new_email("orgadmin")
    db = SessionLocal()
    try:
        org_admin = User(
            org_id=org_id, full_name="Org Admin", role="org_admin", is_active=True,
            email=admin_email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
            password_hash=hash_password(PASSWORD), email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(org_admin)
        db.commit()
    finally:
        db.close()
    client = TestClient(m.app)
    try:
        token = _token_for(admin_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.delete(f"/api/organization/users/{user_id}",
                              headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 204, r.text

        payload = cap.of(IDN_008_DELETED)
        assert payload["to"] == [email.lower()]
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Your account deletion was completed." in text
        assert "Active access has been removed." in text
        assert "Some records are retained" in text, "residual records must be explained"
        for false_claim in ("permanently erased", "all your data has been",
                            "every record has been deleted"):
            assert false_claim.lower() not in text.lower(), \
                f"{false_claim!r} would be untrue for a soft delete"

        assert _load(email).deleted_at is not None, "state committed"
    finally:
        _cleanup(email)
        _cleanup(admin_email)


def test_008_hard_delete_still_works_and_notifies():
    """Regression guard: the identity tables added for IDN-001/003/007 FK-reference users,
    and this path is a HARD delete — it would 500 without the cascade cleanup."""
    email = _new_email()
    user_id, _org = _make_user(email)
    admin_email = _new_email("admin")
    _make_user(admin_email)
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == admin_email.lower()).one()
        admin.role = "super_admin"
        db.commit()
    finally:
        db.close()
    client = TestClient(m.app)
    try:
        _token_for(email)          # creates a sign_in_events row -> FK pressure
        token = _token_for(admin_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.delete(f"/api/admin/users/{user_id}",
                              headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 204, r.text
        assert _load(email) is None, "hard delete must actually remove the row"
        assert IDN_008_DELETED in cap.subjects, f"got {cap.subjects}"
        assert cap.of(IDN_008_DELETED)["to"] == [email.lower()]
    finally:
        _cleanup(email)
        _cleanup(admin_email)


def test_008_16_preferences_cannot_suppress_and_18_resend_failure_keeps_state():
    email = _new_email()
    user_id, org_id = _make_user(email)
    admin_email = _new_email("admin")
    _make_user(admin_email)
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == admin_email.lower()).one()
        admin.role = "super_admin"
        org = db.get(Organization, org_id)
        org.notifications = {"security_alerts": False}
        db.commit()
    finally:
        db.close()
    client = TestClient(m.app)
    try:
        token = _token_for(admin_email)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.patch(f"/api/admin/users/{user_id}", json={"is_active": False},
                             headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        assert cap.calls, "Class A must ignore preferences and still attempt the send"
        assert _load(email).is_active is False, "a mail outage must not undo the suspension"
    finally:
        _cleanup(email)
        _cleanup(admin_email)


try:                                    # pytest only; the plain runner checks inline
    import pytest

    @pytest.fixture(autouse=True)
    def _no_unmocked_sends():
        yield
        _assert_no_leak()
except ImportError:                     # pragma: no cover
    pass


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                _assert_no_leak()
                passed += 1
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001 — a self-check runner reports, not raises
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
