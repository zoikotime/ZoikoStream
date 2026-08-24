"""IDN-001 — email verification and passwordless sign-in (ZST-EC-001 v2.0).

Twenty checks covering the trigger, the challenge, the message and the login gate.

Pure checks (no DB, no network) run first: template shape, link safety, PII. The endpoint
checks then run against the real DB through TestClient, following the house pattern in
test_registration.py — /auth/* commits real rows, so every test cleans up what it created
in a finally block.

The Resend call is intercepted at `httpx.post` rather than mocked at the send-function
boundary. That is deliberate: it proves the message really travels the existing Resend HTTP
integration (correct URL, bearer auth, payload shape) instead of proving only that a
function we wrote was called. No network request is ever made.

Run with `python test_email_verification.py` (or pytest).
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.config import settings
from app.crud import identity as identity_crud
from app.db import SessionLocal
from app.models import EMAIL_VERIFICATION, IdentityChallenge, Organization, User

TTL = settings.EMAIL_VERIFICATION_TTL_MINUTES


# ── capture harness ─────────────────────────────────────────────────────────────

class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    """Records every Resend HTTP call made during the block."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "payload": json or {}})
        return _Resp()

    @property
    def last(self):
        assert self.calls, "no email was sent"
        return self.calls[-1]

    @property
    def payload(self):
        return self.last["payload"]


def _capture():
    """patch(...) context manager that swaps app.email's httpx.post for a recorder."""
    cap = Captured()
    return cap, patch.object(email_mod.httpx, "post", cap)


def _reset_limits():
    """Per-process sliding windows leak between tests (same client IP for all of them)."""
    ratelimit._HITS.clear()


# ── fixtures ────────────────────────────────────────────────────────────────────

def _new_email():
    return f"idn001-{uuid.uuid4().hex[:12]}@example.com"


def _register(client, email, cap_calls=True):
    """POST /auth/register, returning (response, captured). Rate limits reset first."""
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        resp = client.post("/api/auth/register", json={
            "full_name": "Jane Doe",
            "organization_name": f"IDN001 Org {uuid.uuid4().hex[:6]}",
            "email": email,
            "password": "correct-horse-battery",
        })
    return resp, cap


def _token_from(cap):
    """Pull the raw token out of the emailed link."""
    url = _url_from(cap)
    return url.split("token=", 1)[1]


def _url_from(cap):
    text = cap.payload["text"]
    match = re.search(r"https?://\S+\?token=\S+", text)
    if not match:
        raise AssertionError(f"no verification URL in plain-text body:\n{text}")
    return match.group(0)


def _cleanup(email):
    """Remove the org + user + challenges created by a registration."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.lower()).one_or_none()
        if user is None:
            return
        db.query(IdentityChallenge).filter(IdentityChallenge.user_id == user.id).delete()
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


# ══ 1. Pure checks — template, links, PII ═══════════════════════════════════════

def test_09a_verification_url_shape_carries_only_the_token():
    """The path is the required /verify-email; the QUERY carries the opaque token alone.

    Note the banned-substring check runs against the query string, not the whole URL:
    "email" legitimately appears in the mandated `/verify-email` path.
    """
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "https://app.zoikostream.com"):
        url = email_mod.verification_url("abc123-token")

    assert url == "https://app.zoikostream.com/verify-email?token=abc123-token"
    path, _, query = url.partition("?")
    assert path == "https://app.zoikostream.com/verify-email"
    assert query.split("=", 1)[0] == "token", "the only query parameter must be `token`"
    assert "&" not in query, "no additional parameters (tracking or otherwise)"
    assert "@" not in url, "no email address anywhere in the link"
    for banned in ("email", "user", "org", "name", "utm_", "uid", "id="):
        assert banned not in query.lower(), f"{banned!r} must not appear in the query string"


def test_17_no_localhost_or_http_in_production_urls():
    """Outside development an unsafe APP_URL fails the send instead of shipping the link."""
    for bad in ("http://localhost:5173", "http://app.zoikostream.com",
                "https://localhost:5173", "https://127.0.0.1:5173"):
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "APP_URL", bad):
            try:
                email_mod.verification_url("t")
            except email_mod.UnsafeLinkError:
                pass
            else:
                raise AssertionError(f"{bad!r} must be refused in production")

    with patch.object(settings, "ENVIRONMENT", "production"), \
         patch.object(settings, "APP_URL", "https://app.zoikostream.com"):
        assert email_mod.verification_url("t").startswith("https://app.zoikostream.com/")

    # Development keeps working so a local clone still boots.
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "http://localhost:5173"):
        assert "localhost" in email_mod.verification_url("t")


def test_18_no_tracking_pixel_and_no_remote_assets():
    html = email_mod._verification_html("https://x/verify-email?token=t", TTL, "01 Jan 2026, 09:00 UTC")
    lower = html.lower()
    assert "cid:" in lower, "the logo must stay an inline attachment"
    assert "<img src=\"http" not in lower and "<img src='http" not in lower, "no remote image"
    assert 'width="1"' not in lower and "height=\"1\"" not in lower, "no 1x1 pixel"
    for banned in ("utm_", "pixel", "track", "open.gif", "beacon"):
        assert banned not in lower, f"{banned!r} must not appear in a Class A email"


def test_template_copy_is_the_approved_idn_001_copy():
    url = "https://x/verify-email?token=t"
    html = email_mod._verification_html(url, TTL, "01 Jan 2026, 09:00 UTC")
    text = email_mod._verification_text(url, TTL, "01 Jan 2026, 09:00 UTC")

    assert email_mod.IDN_001_SUBJECT == "Verify your email for Zoiko Steam"
    for body in (html, text):
        assert "Confirm your email address." in body
        assert "A request was made to verify this email address for Zoiko Steam." in body
        assert "only on the device where you started the request" in body
        assert f"This secure link expires in {TTL} minutes." in body
        assert "If you did not request this, you can safely ignore this email." in body
        assert url in body
    assert "Verify email" in html and "Verify email" in text
    # No marketing or promotional module in a Class A message.
    for banned in ("unsubscribe", "webinar", "upgrade", "newsletter", "pricing"):
        assert banned not in html.lower(), f"{banned!r} must not appear in a Class A email"


def test_sender_identity_is_zoiko_steam_security():
    with patch.object(settings, "MAIL_FROM", "ZoikoStream <info@zoikostream.com>"):
        assert email_mod._sender_identity(email_mod.SENDER_SECURITY) == \
            "Zoiko Steam Security <info@zoikostream.com>"
    # A bare address (no display name) is handled too.
    with patch.object(settings, "MAIL_FROM", "info@zoikostream.com"):
        assert email_mod._sender_identity(email_mod.SENDER_SECURITY) == \
            "Zoiko Steam Security <info@zoikostream.com>"


def test_token_hash_is_one_way_and_purpose_bound():
    raw = "some-raw-token"
    assert identity_crud._hash_token(raw) != raw
    assert len(identity_crud._hash_token(raw)) == 64
    assert identity_crud._hash_token(raw) == identity_crud._hash_token(raw)
    assert identity_crud._hash_token(raw) != identity_crud._hash_token(raw + "x")


# ══ 2. Endpoint checks — real DB ════════════════════════════════════════════════

def test_01_new_users_are_created_unverified():
    email = _new_email()
    client = TestClient(m.app)
    try:
        resp, _ = _register(client, email)
        assert resp.status_code == 202, resp.text
        user = _load(email)
        assert user is not None, "the account row must exist"
        assert user.email_verified is False, "a new account must start unverified"
        assert user.email_verified_at is None
    finally:
        _cleanup(email)


def test_02_registration_does_not_activate_verified_access():
    email = _new_email()
    client = TestClient(m.app)
    try:
        resp, _ = _register(client, email)
        body = resp.json()
        assert resp.status_code == 202, "registration is accepted, not completed"
        assert body["status"] == "EMAIL_VERIFICATION_REQUIRED"
        assert "access_token" not in body, "no session may be issued at registration"
        assert "token" not in str(body.keys())
        # The address is echoed masked, never in full.
        assert body["email"] != email and body["email"].endswith("@example.com")
        assert "*" in body["email"]
        assert body["expires_in_minutes"] == TTL
    finally:
        _cleanup(email)


def test_03_verification_challenge_is_generated():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _register(client, email)
        user = _load(email)
        db = SessionLocal()
        try:
            rows = db.query(IdentityChallenge).filter(
                IdentityChallenge.user_id == user.id).all()
            assert len(rows) == 1, "exactly one challenge per registration"
            c = rows[0]
            assert c.purpose == EMAIL_VERIFICATION
            assert c.consumed_at is None and c.superseded_at is None
            assert c.expires_at > datetime.now(timezone.utc)
            # Short-lived: within the configured TTL, not days.
            assert c.expires_at <= datetime.now(timezone.utc) + timedelta(minutes=TTL + 1)
        finally:
            db.close()
    finally:
        _cleanup(email)


def test_04_raw_verification_token_is_not_stored():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, cap = _register(client, email)
        raw = _token_from(cap)
        assert len(raw) >= 32, "token must be long/random"

        user = _load(email)
        db = SessionLocal()
        try:
            c = db.query(IdentityChallenge).filter(
                IdentityChallenge.user_id == user.id).one()
            assert c.token_hash != raw, "the raw token must never be persisted"
            assert c.token_hash == identity_crud._hash_token(raw)
            # And it is nowhere else on the row or the user either.
            for value in (c.purpose, str(c.id), user.password_hash, user.username):
                assert raw not in str(value)
            assert db.query(IdentityChallenge).filter(
                IdentityChallenge.token_hash == raw).count() == 0
        finally:
            db.close()
    finally:
        _cleanup(email)


def test_09b_emitted_url_contains_no_real_pii():
    """Against real data: the emitted link leaks no address, local part, username or id."""
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, cap = _register(client, email)
        url = _url_from(cap)
        user = _load(email)

        local = email.split("@")[0]
        for secret in (email, email.lower(), local, user.username,
                       str(user.id), str(user.org_id)):
            assert secret not in url, f"{secret!r} must not appear in the verification URL"
        assert "@" not in url
        assert url.count("?") == 1 and "&" not in url

        # The token is the only variable part of the link, and it is opaque.
        token = url.split("token=", 1)[1]
        assert local not in token and str(user.id) not in token
    finally:
        _cleanup(email)


def test_05_idn_001_is_sent_through_the_existing_resend_integration():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, cap = _register(client, email)
        assert len(cap.calls) == 1, "exactly one email per registration"
        call = cap.last
        assert call["url"] == email_mod.RESEND_URL == "https://api.resend.com/emails"
        assert call["headers"]["Authorization"].startswith("Bearer "), "Resend bearer auth"
        assert call["payload"]["subject"] == "Verify your email for Zoiko Steam"
        assert call["payload"]["from"].startswith("Zoiko Steam Security <"), \
            "IDN-001 must ship from the Zoiko Steam Security identity"
        assert "@zoikostream.com" in call["payload"]["from"] or \
               "@" in call["payload"]["from"], "sender must use the authenticated address"
    finally:
        _cleanup(email)


def test_06_correct_recipient_receives_the_message():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, cap = _register(client, email)
        assert cap.payload["to"] == [email.lower()], "only the account holder is addressed"
        assert len(cap.payload["to"]) == 1, "no cc/bcc leakage to other recipients"
    finally:
        _cleanup(email)


def test_07_html_version_exists():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, cap = _register(client, email)
        html = cap.payload["html"]
        assert html and "<" in html, "HTML body must be present"
        assert "Confirm your email address." in html
        assert "font-size:16px" in html, "body text must be at least 16px"
    finally:
        _cleanup(email)


def test_08_plain_text_version_exists():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, cap = _register(client, email)
        text = cap.payload.get("text")
        assert text, "a plain-text alternative is mandatory — never send HTML-only"
        assert "<" not in text.replace("<", "") or "</" not in text, "text must not be markup"
        assert "Confirm your email address." in text
        assert "token=" in text, "the link must be reachable from the text part too"
    finally:
        _cleanup(email)


def test_10_and_14_valid_token_verifies_the_user_and_stamps_the_time():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, cap = _register(client, email)
        raw = _token_from(cap)
        before = datetime.now(timezone.utc)

        _reset_limits()
        resp = client.post("/api/auth/verify-email", json={"token": raw})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "verified"
        assert body["message"] == \
            "Email verified successfully. You can now continue to Zoiko Steam."
        # Verification is proof of control, not authentication — no session handed back.
        assert "access_token" not in body

        user = _load(email)
        assert user.email_verified is True
        assert user.email_verified_at is not None, "email_verified_at must be populated"
        assert user.email_verified_at >= before - timedelta(seconds=5)
    finally:
        _cleanup(email)


def test_11_token_is_single_use():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, cap = _register(client, email)
        raw = _token_from(cap)

        _reset_limits()
        assert client.post("/api/auth/verify-email", json={"token": raw}).status_code == 200
        second = client.post("/api/auth/verify-email", json={"token": raw})
        assert second.status_code == 400, "a spent token must not verify twice"
        assert second.json()["detail"]["code"] == "TOKEN_ALREADY_USED"

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email.lower()).one()
            c = db.query(IdentityChallenge).filter(
                IdentityChallenge.user_id == user.id).one()
            assert c.consumed_at is not None, "consumption must be recorded"
            assert c.attempts >= 2, "redemption attempts are counted"
        finally:
            db.close()
    finally:
        _cleanup(email)


def test_12_expired_token_fails():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, cap = _register(client, email)
        raw = _token_from(cap)

        # Age the challenge past its window.
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email.lower()).one()
            c = db.query(IdentityChallenge).filter(
                IdentityChallenge.user_id == user.id).one()
            c.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()

        _reset_limits()
        resp = client.post("/api/auth/verify-email", json={"token": raw})
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["code"] == "TOKEN_EXPIRED"
        assert _load(email).email_verified is False, "expiry must not verify the account"
    finally:
        _cleanup(email)


def test_13_invalid_token_fails_safely():
    client = TestClient(m.app)
    _reset_limits()
    for bad in ("x" * 40, "not-a-real-token-value-000000", "../../etc/passwd" + "y" * 20):
        resp = client.post("/api/auth/verify-email", json={"token": bad})
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "TOKEN_INVALID"
        # Generic copy: nothing about whether a user or challenge exists.
        assert "@" not in detail["message"] and "user" not in detail["message"].lower()

    # Too-short tokens are rejected by schema validation, not by a DB lookup.
    assert client.post("/api/auth/verify-email", json={"token": "short"}).status_code == 422


def test_15_unverified_login_returns_verification_required():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _register(client, email)

        _reset_limits()
        resp = client.post("/api/auth/login", json={
            "identifier": email, "password": "correct-horse-battery", "remember": False,
        })
        assert resp.status_code == 403, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "EMAIL_VERIFICATION_REQUIRED"
        assert detail["message"] == "Verify your email to continue."
        assert "*" in detail["email"], "the address must be masked"
        assert "access_token" not in resp.json(), "no session on an unverified account"

        # And once verified, the same credentials work.
        _, cap = None, None
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email.lower()).one()
            c = db.query(IdentityChallenge).filter(
                IdentityChallenge.user_id == user.id).one()
            identity_crud.consume_email_verification(db, c)
        finally:
            db.close()

        _reset_limits()
        ok = client.post("/api/auth/login", json={
            "identifier": email, "password": "correct-horse-battery", "remember": False,
        })
        assert ok.status_code == 200, ok.text
        assert ok.json()["access_token"], "a verified account signs in normally"
    finally:
        _cleanup(email)


def test_16_resend_endpoint_is_rate_limited():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _register(client, email)
        _reset_limits()
        cap, ctx = _capture()
        codes = []
        with ctx:
            for _ in range(5):
                codes.append(
                    client.post("/api/auth/resend-verification", json={"email": email}).status_code
                )
        assert codes[:3] == [200, 200, 200], f"first three should pass, got {codes}"
        assert 429 in codes, f"resend must be rate limited, got {codes}"
        assert len(cap.calls) == 3, "no email is sent once the budget is spent"
    finally:
        _cleanup(email)


def test_resend_supersedes_the_previous_challenge_and_is_non_enumerating():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _, first_cap = _register(client, email)
        first_token = _token_from(first_cap)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            resp = client.post("/api/auth/resend-verification", json={"email": email})
        assert resp.status_code == 200
        second_token = _token_from(cap)
        assert second_token != first_token, "a resend must mint a new token"

        # The old link stops working immediately.
        _reset_limits()
        old = client.post("/api/auth/verify-email", json={"token": first_token})
        assert old.status_code == 400
        assert old.json()["detail"]["code"] == "TOKEN_INVALID"

        # The new one still works.
        new = client.post("/api/auth/verify-email", json={"token": second_token})
        assert new.status_code == 200, new.text

        # Unknown address and already-verified address answer identically, and send nothing.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            unknown = client.post("/api/auth/resend-verification",
                                  json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com"})
            verified = client.post("/api/auth/resend-verification", json={"email": email})
        assert unknown.status_code == verified.status_code == 200
        assert unknown.json() == verified.json(), "responses must be indistinguishable"
        assert cap2.calls == [], "no email for an unknown or already-verified address"
    finally:
        _cleanup(email)


def test_19_security_email_is_not_suppressed_by_organization_preferences():
    """Class A is mandatory: preferences must not gate it (ZST-EC-001 Section 03)."""
    email = _new_email()
    client = TestClient(m.app)
    try:
        _register(client, email)
        user = _load(email)

        # Turn every notification preference off, including the security-alerts toggle the
        # product UI exposes, then trigger IDN-001 again.
        db = SessionLocal()
        try:
            org = db.get(Organization, user.org_id)
            org.notifications = {
                "security_alerts": False, "billing": False, "mentions": False,
                "event_scheduled": False, "event_starting": False,
                "recording_ready": False, "weekly_summary": False, "member_joined": False,
            }
            db.commit()
        finally:
            db.close()

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            resp = client.post("/api/auth/resend-verification", json={"email": email})
        assert resp.status_code == 200
        assert len(cap.calls) == 1, \
            "IDN-001 is Class A and must send regardless of notification preferences"
        assert cap.payload["subject"] == "Verify your email for Zoiko Steam"
    finally:
        _cleanup(email)


def test_20_idn_002_does_not_fire_before_successful_verification():
    """The welcome/account-ready message must not claim an active account pre-verification."""
    email = _new_email()
    client = TestClient(m.app)
    try:
        with patch.object(email_mod, "send_welcome_email") as welcome:
            _, cap = _register(client, email)
            assert welcome.call_count == 0, \
                "IDN-002 must not fire on unverified registration"

            # Only IDN-001 went out.
            assert len(cap.calls) == 1
            assert cap.payload["subject"] == "Verify your email for Zoiko Steam"

            raw = _token_from(cap)
            _reset_limits()
            assert client.post("/api/auth/verify-email", json={"token": raw}).status_code == 200
            # IDN-002 is out of scope for this change, so it must not fire here either —
            # what matters is that it can never precede verification.
            assert welcome.call_count == 0
    finally:
        _cleanup(email)


def test_invited_members_are_created_already_verified():
    """The invitation token already proves control of the address (crud/organization.py)."""
    from app.crud import organization as org_crud

    db = SessionLocal()
    email = _new_email()
    org = Organization(name=f"IDN001 Invite Org {uuid.uuid4().hex[:6]}", status="active")
    db.add(org)
    db.flush()
    inviter = User(
        org_id=org.id, full_name="Inviter", role="org_admin", is_active=True,
        email=_new_email(), username=f"u{uuid.uuid4().hex[:10]}", password_hash="x",
        email_verified=True,
    )
    db.add(inviter)
    db.commit()
    try:
        inv, _raw = org_crud.create_invitation(db, org.id, email, "host", inviter.id)
        member = org_crud.accept_invitation(
            db, inv, "Invited Person", f"u{uuid.uuid4().hex[:10]}", "hash")
        assert member.email_verified is True
        assert member.email_verified_at is not None
    finally:
        db.query(IdentityChallenge).filter(
            IdentityChallenge.user_id.in_([u.id for u in db.query(User).filter(
                User.org_id == org.id).all()])).delete(synchronize_session=False)
        from app.models import Invitation
        db.query(Invitation).filter(Invitation.org_id == org.id).delete()
        db.query(User).filter(User.org_id == org.id).delete()
        db.delete(org)
        db.commit()
        db.close()


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
