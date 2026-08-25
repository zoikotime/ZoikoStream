"""IDN-003 / IDN-004 / IDN-005 — identity security (ZST-EC-001 v2.0).

Same harness as the IDN-001/002 suites: the Resend call is intercepted at `httpx.post`, so
each message is proven to travel the real integration, and no network request is made.

Structure mirrors the three families. Pure checks (template shape, normalization, secrets)
run without a database; trigger and risk checks run against the real DB through TestClient.

Run with `python test_identity_security.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.config import settings
from app.crud import identity as identity_crud
from app.db import SessionLocal
from app.models import (
    AccountRecovery,
    IdentityChallenge,
    Organization,
    SignInEvent,
    User,
)
from app.security import hash_password
from app.services import identity_security as idsec

IDN_003 = email_mod.IDN_003_SUBJECT
IDN_004 = email_mod.IDN_004_SUBJECT
IDN_005 = email_mod.IDN_005_SUBJECT
PASSWORD = "correct-horse-battery"

CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
SAFARI_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605 Version/17 Safari/605"


# ── harness ─────────────────────────────────────────────────────────────────────

class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

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


def _capture(fail=False):
    cap = Captured(fail=fail)
    return cap, patch.object(email_mod.httpx, "post", cap)


def _reset_limits():
    ratelimit._HITS.clear()


def _new_email():
    return f"idnsec-{uuid.uuid4().hex[:12]}@example.com"


def _make_user(email, *, verified=True, active=True):
    """A ready-to-sign-in account, created directly so tests skip the IDN-001 flow."""
    db = SessionLocal()
    try:
        org = Organization(name=f"IDNSEC Org {uuid.uuid4().hex[:6]}", status="active")
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id, full_name="Jane Doe", role="org_admin", is_active=active,
            email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
            password_hash=hash_password(PASSWORD),
            email_verified=verified,
            email_verified_at=datetime.now(timezone.utc) if verified else None,
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
        if user is None:
            return
        db.query(SignInEvent).filter(SignInEvent.user_id == user.id).delete()
        db.query(IdentityChallenge).filter(IdentityChallenge.user_id == user.id).delete()
        # IDN-007 gave the reset flow a durable record; it FK-references users, so the
        # teardown has to clear it too or every reset test leaves an undeletable row.
        db.query(AccountRecovery).filter(AccountRecovery.user_id == user.id).delete()
        org_id = user.org_id
        db.delete(user)
        db.commit()
        org = db.get(Organization, org_id)
        if org is not None and not db.query(User).filter(User.org_id == org_id).count():
            db.delete(org)
            db.commit()
    finally:
        db.close()


def _login(client, email, ua=CHROME_WIN, password=PASSWORD, fail=False):
    _reset_limits()
    cap, ctx = _capture(fail=fail)
    with ctx:
        resp = client.post("/api/auth/login",
                           json={"identifier": email, "password": password},
                           headers={"User-Agent": ua})
    return resp, cap


def _events(email, outcome=None):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.lower()).one()
        q = db.query(SignInEvent).filter(SignInEvent.user_id == user.id)
        if outcome:
            q = q.filter(SignInEvent.outcome == outcome)
        return q.order_by(SignInEvent.occurred_at).all()
    finally:
        db.close()


def _seed_failures(email, n):
    """Push the account over the block threshold without hammering the endpoint."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.lower()).one()
        now = datetime.now(timezone.utc)
        for i in range(n):
            db.add(SignInEvent(
                user_id=user.id, session_reference=idsec.new_session_reference(),
                occurred_at=now - timedelta(seconds=n - i), outcome="failed",
                authentication_method="password", browser_family="Chrome",
                platform_family="Windows", is_new_context=False, risk_decision="allow",
            ))
        db.commit()
    finally:
        db.close()


# ══ shared pure checks ══════════════════════════════════════════════════════════

def test_shared_no_secrets_or_tracking_in_any_security_template():
    """13/14 (003), 13/14 (004), 14-18 (005) — no secrets, no pixel, no remote asset."""
    bodies = []
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "https://app.zoikostream.com"):
        url = email_mod.security_url()
        rows = [("Signed in at", "01 Jan 2026, 09:00 UTC"), ("Device", "Chrome on Windows")]
        for headline in (email_mod.IDN_003_HEADLINE, email_mod.IDN_004_HEADLINE,
                         email_mod.IDN_005_HEADLINE):
            bodies.append(email_mod._security_shell("T", headline, "P", rows, "B", "C", url, "F"))
            bodies.append(email_mod._security_text(headline, "P", rows, "B", "C", url, "F"))

    for body in bodies:
        low = body.lower()
        for banned in ("password:", "password_hash", "bcrypt", "$2b$", "otp", "jwt",
                       "bearer ", "access_token", "refresh_token", "eyj", "token=",
                       "utm_", "pixel", "beacon", "open.gif"):
            assert banned not in low, f"{banned!r} must not appear in a Class A email"
        assert "<img src=\"http" not in low, "no remote image"
        assert 'width="1"' not in low, "no 1x1 pixel"


def test_shared_sender_is_zoiko_steam_security():
    """003/6, 004/7, 005/7."""
    with patch.object(settings, "MAIL_FROM", "ZoikoStream <info@zoikostream.com>"):
        assert email_mod._sender_identity(email_mod.SENDER_SECURITY) == \
            "Zoiko Steam Security <info@zoikostream.com>"


def test_shared_cta_uses_configured_base_url_and_no_localhost_in_production():
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "https://app.zoikostream.com"):
        assert email_mod.security_url() == \
            "https://app.zoikostream.com/organization/settings?tab=security"
    for bad in ("http://localhost:5173", "http://app.zoikostream.com", "https://127.0.0.1:5173"):
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "APP_URL", bad):
            try:
                email_mod.security_url()
            except email_mod.UnsafeLinkError:
                pass
            else:
                raise AssertionError(f"{bad!r} must be refused in production")


def test_shared_device_and_location_normalization():
    """003/11-12, 004/11 — readable device, never a raw UA; location always labelled."""
    assert idsec.parse_user_agent(CHROME_WIN) == ("Chrome", "Windows")
    assert idsec.parse_user_agent(SAFARI_MAC) == ("Safari", "macOS")
    assert idsec.parse_user_agent(None) == (None, None)
    assert idsec.describe_device("Chrome", "Windows") == "Chrome on Windows"
    assert idsec.describe_device(None, None) == "Unrecognized device"

    # No geo provider exists, so location is unavailable rather than invented.
    assert idsec.approximate_location("8.8.8.8") is None
    assert idsec.describe_location(None) == "Approximate location unavailable"

    # Network context is coarse and never the full address.
    assert idsec.network_context("203.0.113.45") == "203.0.113.0/24"
    assert "203.0.113.45" not in idsec.network_context("203.0.113.45")
    assert idsec.network_context(None) is None


def test_004_does_not_disclose_detection_logic_or_thresholds():
    """004/12 — no rule, threshold, counter or window may appear in the message."""
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "https://app.zoikostream.com"):
        html = email_mod._security_shell("T", email_mod.IDN_004_HEADLINE,
                                         email_mod.IDN_004_PREHEADER, [], "b", "c",
                                         email_mod.security_url(), email_mod.IDN_004_RECOVERY)
    low = html.lower()
    for leak in ("threshold", "attempts", "rate limit", "10 ", "15 minutes", "window",
                 "counter", "risk score", "blocked because you"):
        assert leak not in low, f"{leak!r} discloses detection logic"


def test_005_session_effect_states_the_truth():
    """005/13 — JWTs here have no jti/version/store, so sessions are NOT revoked."""
    from app import security as sec
    import inspect
    src = inspect.getsource(sec.create_access_token) + inspect.getsource(sec.get_current_user)
    assert "jti" not in src and "token_version" not in src, \
        "if revocation was added, IDN-005 copy must be updated to match"
    assert "not revoked" in email_mod.SESSION_EFFECT_NOT_REVOKED
    assert "revoked and" not in email_mod.SESSION_EFFECT_NOT_REVOKED.replace("not revoked and", "")


# ══ IDN-003 ═════════════════════════════════════════════════════════════════════

def test_003_1_failed_login_sends_nothing():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        resp, cap = _login(client, email, password="wrong-password")
        assert resp.status_code == 401
        assert cap.calls == [], f"a failed login must send nothing, got {cap.subjects}"
        assert len(_events(email, "failed")) == 1, "the failure must still be recorded"
    finally:
        _cleanup(email)


def test_003_2_and_4_first_login_creates_history_and_sends_idn_003():
    """2 (history), 4 (new context triggers), 5 (recipient), 7 (subject), 8/9 (both parts)."""
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        resp, cap = _login(client, email)
        assert resp.status_code == 200, resp.text

        events = _events(email, "success")
        assert len(events) == 1, "successful login must create sign-in history"
        ev = events[0]
        assert ev.is_new_context is True, "first sign-in is a new context by policy"
        assert ev.browser_family == "Chrome" and ev.platform_family == "Windows"
        assert ev.notified_at is not None, "the send must be recorded on the event"

        payload = cap.of(IDN_003)
        assert payload["to"] == [email.lower()], "only the account holder"
        assert payload["from"].startswith("Zoiko Steam Security <")
        assert payload["subject"] == "New sign-in to your Zoiko Steam account"
        assert payload["html"] and payload["text"], "HTML and plain text both required"
        assert "A new sign-in was recorded." in payload["html"]
        assert "A new sign-in was recorded." in payload["text"]
    finally:
        _cleanup(email)


def test_003_3_known_context_does_not_email_again():
    """3 + 16 — repeat sign-ins from the same context are silent."""
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _login(client, email)                       # first: notified
        for _ in range(3):
            resp, cap = _login(client, email)       # same browser/platform
            assert resp.status_code == 200
            assert cap.calls == [], f"known context must not re-email, got {cap.subjects}"

        assert len(_events(email, "success")) == 4, "every sign-in is still recorded"
        notified = [e for e in _events(email, "success") if e.notified_at]
        assert len(notified) == 1, "exactly one notification across four sign-ins"
    finally:
        _cleanup(email)


def test_003_4b_changed_device_triggers_a_new_notice():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _login(client, email, ua=CHROME_WIN)
        resp, cap = _login(client, email, ua=SAFARI_MAC)
        assert resp.status_code == 200
        assert cap.subjects == [IDN_003], f"a genuinely new device must notify: {cap.subjects}"
        assert "Safari on macOS" in cap.of(IDN_003)["text"]
    finally:
        _cleanup(email)


def test_003_10_12_timestamp_device_and_location_render_safely():
    """10 (tz-aware), 11 (device), 12 (location labelled approximate), 13 (no raw token)."""
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _resp, cap = _login(client, email)
        payload = cap.of(IDN_003)
        text = payload["text"]

        assert "UTC" in text, "timestamp must carry its timezone"
        assert "Chrome on Windows" in text, "device must be the normalized family string"
        assert CHROME_WIN not in text and "Mozilla/5.0" not in text, "no raw User-Agent"
        assert "Approximate location" in text, "location must be labelled approximate"
        assert "Approximate location unavailable" in text, "no location provider: say so"

        ev = _events(email, "success")[0]
        assert ev.session_reference in text, "session reference is shown"
        # ...and it is a correlation id, not a credential.
        assert len(ev.session_reference) == 16 and "." not in ev.session_reference
    finally:
        _cleanup(email)


def test_003_15_preferences_cannot_suppress_it():
    email = _new_email()
    _user_id, org_id = _make_user(email)
    client = TestClient(m.app)
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, org_id)
            org.notifications = {"security_alerts": False, "billing": False, "mentions": False,
                                 "event_scheduled": False, "member_joined": False}
            db.commit()
        finally:
            db.close()

        _resp, cap = _login(client, email)
        assert cap.subjects == [IDN_003], "Class A must ignore notification preferences"
    finally:
        _cleanup(email)


def test_003_17_resend_failure_does_not_fail_the_login():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        resp, cap = _login(client, email, fail=True)
        assert resp.status_code == 200, "a mail outage must not break authentication"
        assert resp.json()["access_token"], "the session is still issued"
        assert cap.calls, "a send was attempted"
        assert len(_events(email, "success")) == 1, "the sign-in record still stands"
    finally:
        _cleanup(email)


# ══ IDN-004 ═════════════════════════════════════════════════════════════════════

def test_004_1_and_2_normal_login_and_single_wrong_password_are_not_blocked():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        ok, _ = _login(client, email)
        assert ok.status_code == 200, "a normal login must not be blocked"

        bad, cap = _login(client, email, password="wrong-password")
        assert bad.status_code == 401
        assert IDN_004 not in cap.subjects, \
            "one wrong password is not suspicious and must not send IDN-004"
    finally:
        _cleanup(email)


def test_004_3_to_10_sustained_failures_block_and_notify():
    """3 (blocks), 4 (no token), 5 (sends), 6 (recipient), 7 (sender), 8 (subject),
    9/10 (HTML + text)."""
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _seed_failures(email, 10)

        # Even the CORRECT password is refused inside the block window.
        resp, cap = _login(client, email, password=PASSWORD)
        assert resp.status_code == 401, "a blocked account must not authenticate"
        body = resp.json()
        assert "access_token" not in body, "no session may be issued on a block"
        assert body["detail"] == "Invalid credentials", \
            "a block must be indistinguishable from a bad credential"

        payload = cap.of(IDN_004)
        assert payload["to"] == [email.lower()]
        assert payload["from"].startswith("Zoiko Steam Security <")
        assert payload["subject"] == "Zoiko Steam blocked a suspicious sign-in"
        assert payload["html"] and payload["text"]
        assert "We blocked an attempt to access your account." in payload["text"]
        assert "Approximate location" in payload["text"]
        assert "No access was granted" in payload["text"]

        blocked = _events(email, "blocked")
        assert len(blocked) == 1 and blocked[0].risk_decision == "block_suspicious"
    finally:
        _cleanup(email)


def test_004_16_repeated_blocks_are_deduplicated():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _seed_failures(email, 10)
        first, cap1 = _login(client, email)
        assert first.status_code == 401
        assert cap1.subjects == [IDN_004]

        for _ in range(4):
            again, cap2 = _login(client, email)
            assert again.status_code == 401
            assert cap2.calls == [], "a burst of blocks is one security event to a human"

        assert len(_events(email, "blocked")) == 5, "every attempt is still recorded"
        notified = [e for e in _events(email, "blocked") if e.notified_at]
        assert len(notified) == 1, "exactly one notification for the episode"
    finally:
        _cleanup(email)


def test_004_15_preferences_cannot_suppress_it():
    email = _new_email()
    _user_id, org_id = _make_user(email)
    client = TestClient(m.app)
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, org_id)
            org.notifications = {"security_alerts": False}
            db.commit()
        finally:
            db.close()
        _seed_failures(email, 10)
        _resp, cap = _login(client, email)
        assert cap.subjects == [IDN_004], "Class A must ignore notification preferences"
    finally:
        _cleanup(email)


def test_004_17_resend_failure_does_not_grant_access():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _seed_failures(email, 10)
        resp, cap = _login(client, email, fail=True)
        assert resp.status_code == 401, "a mail outage must never turn a block into access"
        assert "access_token" not in resp.json()
        assert cap.calls, "a send was attempted"
    finally:
        _cleanup(email)


def test_004_unknown_account_does_not_leak_existence():
    """A failed attempt on an address with no account behaves identically and sends nothing."""
    client = TestClient(m.app)
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        resp = client.post("/api/auth/login",
                           json={"identifier": f"ghost-{uuid.uuid4().hex[:8]}@example.com",
                                 "password": "whatever"},
                           headers={"User-Agent": CHROME_WIN})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"
    assert cap.calls == [], "no mail may be sent for an address with no account"


# ══ IDN-005 ═════════════════════════════════════════════════════════════════════

def _request_otp(client, email):
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        resp = client.post("/api/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    # The code used to be readable from users.reset_token. IDN-007 stopped storing it in
    # the clear, so the only place it exists is the message itself -- which is the point.
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.email == email.lower()).one().reset_token is None,             "the recovery code must never be persisted in plaintext"
    finally:
        db.close()
    for call in cap.calls:
        if call["payload"]["subject"] == email_mod.IDN_007_SUBJECT_VERIFICATION:
            for row in call["payload"]["text"].splitlines():
                if row.startswith("Verification code:"):
                    return row.split(":", 1)[1].strip(), cap
    raise AssertionError(f"no verification code was delivered; got {cap.subjects}")


def test_005_1_forgot_password_request_alone_sends_no_idn_005():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _otp, cap = _request_otp(client, email)
        assert IDN_005 not in cap.subjects, "requesting a reset is not a credential change"
    finally:
        _cleanup(email)


def test_005_2_otp_verification_alone_sends_no_idn_005():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        otp, _ = _request_otp(client, email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            resp = client.post("/api/auth/verify-otp", json={"email": email, "otp": otp})
        assert resp.status_code == 200, resp.text
        assert cap.calls == [], "verifying the code changes no credential"
    finally:
        _cleanup(email)


def test_005_3_failed_reset_sends_no_idn_005():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        _request_otp(client, email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            resp = client.post("/api/auth/reset-password",
                               json={"email": email, "otp": "000000", "password": "brand-new-secret"})
        assert resp.status_code == 400, "a wrong OTP must not reset the password"
        assert cap.calls == [], "a failed reset must send nothing"
    finally:
        _cleanup(email)


def test_005_4_to_13_successful_reset_sends_idn_005():
    """4 (sends), 6 (recipient), 7 (sender), 8 (subject), 9/10 (parts), 11 (credential type),
    12 (tz), 13 (session effect), 21 (exactly one)."""
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        otp, _ = _request_otp(client, email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            resp = client.post("/api/auth/reset-password",
                               json={"email": email, "otp": otp, "password": "brand-new-secret"})
        assert resp.status_code == 200, resp.text

        # IDN-007 "recovery complete" now closes the same flow, so the invariant is one
        # IDN-005 -- not one message overall.
        assert cap.subjects.count(IDN_005) == 1, f"exactly one IDN-005, got {cap.subjects}"
        payload = cap.of(IDN_005)
        assert payload["to"] == [email.lower()]
        assert payload["from"].startswith("Zoiko Steam Security <")
        assert payload["subject"] == "Your Zoiko Steam sign-in method was changed"
        assert payload["html"] and payload["text"]

        text = payload["text"]
        assert "Your sign-in credentials were updated." in text
        assert "The password associated with your Zoiko Steam identity" in text
        assert "UTC" in text, "timestamp must carry its timezone"
        # The truth about this platform: stateless JWTs cannot be revoked.
        assert "Existing sessions were not revoked and remain active until they expire." in text
        assert "Review account security" in text

        # And the new credential really works. Wrapped because a fresh sign-in fires
        # IDN-003, which must not reach the real provider from a test.
        _reset_limits()
        _cap_login, ctx_login = _capture()
        with ctx_login:
            ok = client.post("/api/auth/login",
                             json={"identifier": email, "password": "brand-new-secret"},
                             headers={"User-Agent": CHROME_WIN})
        assert ok.status_code == 200, ok.text
    finally:
        _cleanup(email)


def test_005_14_to_18_no_password_hash_otp_or_token_in_the_message():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        otp, _ = _request_otp(client, email)
        db = SessionLocal()
        try:
            pw_hash = db.query(User).filter(User.email == email.lower()).one().password_hash
        finally:
            db.close()

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            client.post("/api/auth/reset-password",
                        json={"email": email, "otp": otp, "password": "brand-new-secret"})
        payload = cap.of(IDN_005)
        for part in (payload["html"], payload["text"]):
            assert "brand-new-secret" not in part, "the password must never appear"
            assert pw_hash not in part, "the password hash must never appear"
            assert otp not in part, "the OTP must never appear"
            assert "eyJ" not in part, "no JWT may appear"
            assert 'width="1"' not in part, "no tracking pixel"
    finally:
        _cleanup(email)


def test_005_19_preferences_cannot_suppress_it():
    email = _new_email()
    _user_id, org_id = _make_user(email)
    client = TestClient(m.app)
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, org_id)
            org.notifications = {"security_alerts": False}
            db.commit()
        finally:
            db.close()
        otp, _ = _request_otp(client, email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            client.post("/api/auth/reset-password",
                        json={"email": email, "otp": otp, "password": "brand-new-secret"})
        assert IDN_005 in cap.subjects, "Class A must ignore notification preferences"
    finally:
        _cleanup(email)


def test_005_20_resend_failure_does_not_roll_back_the_password_change():
    email = _new_email()
    _make_user(email)
    client = TestClient(m.app)
    try:
        otp, _ = _request_otp(client, email)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            resp = client.post("/api/auth/reset-password",
                               json={"email": email, "otp": otp, "password": "brand-new-secret"})
        assert resp.status_code == 200, "a mail outage must not fail the reset"
        assert cap.calls, "a send was attempted"

        # The new password is committed regardless.
        _reset_limits()
        ok = client.post("/api/auth/login",
                         json={"identifier": email, "password": "brand-new-secret"},
                         headers={"User-Agent": CHROME_WIN})
        assert ok.status_code == 200, "the password change stands"
    finally:
        _cleanup(email)


def test_005_5_no_authenticated_password_change_path_exists():
    """Documents a real gap rather than asserting a route we did not build."""
    import app.routers.auth as auth_router
    paths = {r.path for r in auth_router.router.routes}
    assert "/auth/change-password" not in paths, \
        "if an authenticated change path is added, it must also trigger IDN-005"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001 — a self-check runner reports, not raises
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
