"""IDN-002 — Account ready (ZST-EC-001 v2.0).

Twenty checks covering the trigger, the gating, the message and the duplicate behaviour.

The defect this template previously had was a TRIGGER defect, not a copy defect: the old
welcome email fired at registration and asserted an active account against an unverified
address. Most of the checks below therefore assert on *when* the message is and is not
sent, and only a few on what it says.

Same harness as test_email_verification.py: the Resend call is intercepted at `httpx.post`
so the message is proven to travel the real integration, and no network request is made.

Run with `python test_account_ready.py` (or pytest).
"""
import re
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
from app.models import IdentityChallenge, Organization, SignInEvent, User

IDN_001 = email_mod.IDN_001_SUBJECT
IDN_002 = email_mod.IDN_002_SUBJECT


# ── harness (mirrors test_email_verification.py) ────────────────────────────────

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
    return f"idn002-{uuid.uuid4().hex[:12]}@example.com"


def _register(client, email):
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        resp = client.post("/api/auth/register", json={
            "full_name": "Jane Doe",
            "organization_name": f"IDN002 Org {uuid.uuid4().hex[:6]}",
            "email": email,
            "password": "correct-horse-battery",
        })
    assert resp.status_code == 202, resp.text
    token = re.search(r"https?://\S+\?token=(\S+)", cap.calls[0]["payload"]["text"]).group(1)
    return token, cap


def _verify(client, token, fail=False):
    _reset_limits()
    cap, ctx = _capture(fail=fail)
    with ctx:
        resp = client.post("/api/auth/verify-email", json={"token": token})
    return resp, cap


def _load(email):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email.lower()).one_or_none()
    finally:
        db.close()


def _cleanup(email):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email.lower()).one_or_none()
        if user is None:
            return
        # Sign-in history now exists for any test that logs in (IDN-003/004) and is
        # FK-bound to the user, so it must go first. Production never hard-deletes a
        # user -- deletion there is a soft delete -- so this is test hygiene only.
        db.query(SignInEvent).filter(SignInEvent.user_id == user.id).delete()
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


# ══ template shape (pure) ═══════════════════════════════════════════════════════

def test_09_subject_is_canonical():
    assert email_mod.IDN_002_SUBJECT == "Your Zoiko Steam access is ready"


def test_10_and_11_html_and_plain_text_both_exist_with_canonical_copy():
    url = "https://app.zoikostream.com/login"
    html = email_mod._account_ready_html(url)
    text = email_mod._account_ready_text(url)

    assert html and "<" in html, "HTML body must exist"
    assert text and "<div" not in text, "plain-text body must exist and not be markup"

    for body in (html, text):
        assert "Welcome to Zoiko Steam." in body
        assert "Your identity has been verified and your Zoiko Steam access is active." in body
        assert "determined by the access granted to you" in body
        assert "Open Zoiko Steam" in body
        assert url in body
    assert "Sign in and complete your account setup." in html, "preheader must be present"
    assert "Sign in and complete your account setup." in text
    # Accessibility floor applied to new templates.
    assert "font-size:16px" in html


def test_14_no_secrets_tokens_or_pii_in_the_message():
    url = "https://app.zoikostream.com/login"
    for body in (email_mod._account_ready_html(url), email_mod._account_ready_text(url)):
        low = body.lower()
        for banned in ("token=", "password", "api_key", "apikey", "secret",
                       "stream_key", "bearer ", "utm_", "pixel", "beacon"):
            assert banned not in low, f"{banned!r} must not appear in IDN-002"
    # The CTA is a bare route: no query string at all.
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "https://app.zoikostream.com"):
        assert email_mod.account_ready_url() == "https://app.zoikostream.com/login"
        assert "?" not in email_mod.account_ready_url()


def test_12_cta_uses_the_configured_base_url():
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "https://tenant.example.org"):
        assert email_mod.account_ready_url() == "https://tenant.example.org/login"


def test_13_no_localhost_in_production_configuration():
    for bad in ("http://localhost:5173", "http://app.zoikostream.com", "https://127.0.0.1:5173"):
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "APP_URL", bad):
            try:
                email_mod.account_ready_url()
            except email_mod.UnsafeLinkError:
                pass
            else:
                raise AssertionError(f"{bad!r} must be refused in production")


def test_08_sender_display_name_is_zoiko_steam_not_security():
    with patch.object(settings, "MAIL_FROM", "ZoikoStream <info@zoikostream.com>"):
        sender = email_mod._sender_identity(email_mod.SENDER_DEFAULT)
    assert sender == "Zoiko Steam <info@zoikostream.com>"
    assert "Security" not in sender, "IDN-002 is Class C — not the security identity"


def test_old_welcome_template_is_gone():
    """The defective template must not remain reachable under any name."""
    assert not hasattr(email_mod, "send_welcome_email")
    assert not hasattr(email_mod, "_welcome_html")

    # Comment lines are stripped first: email.py documents WHY the old copy was removed and
    # quotes it to do so. What matters is that the phrase survives in no string literal —
    # i.e. that nothing can still render it.
    src = open(email_mod.__file__, encoding="utf-8").read()
    code = chr(10).join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "active and ready to go" not in code, "old copy must be deleted, not just unused"
    assert "Welcome to ZoikoStream" not in code, "old subject line must be gone"

    # And it cannot be rendered by the template that replaced it.
    url = "https://app.zoikostream.com/login"
    for body in (email_mod._account_ready_html(url), email_mod._account_ready_text(url)):
        assert "active and ready to go" not in body


# ══ trigger and gating (real DB) ════════════════════════════════════════════════

def test_01_registration_does_not_send_idn_002():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _token, cap = _register(client, email)
        assert cap.subjects == [IDN_001], f"registration must send IDN-001 only, got {cap.subjects}"
        assert _load(email).account_ready_sent_at is None
    finally:
        _cleanup(email)


def test_02_unverified_users_do_not_receive_idn_002():
    email = _new_email()
    client = TestClient(m.app)
    try:
        _register(client, email)
        # Resending verification, and a failed login, must not produce IDN-002.
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            client.post("/api/auth/resend-verification", json={"email": email})
            client.post("/api/auth/login", json={
                "identifier": email, "password": "correct-horse-battery"})
        assert IDN_002 not in cap.subjects, f"got {cap.subjects}"
        assert _load(email).email_verified is False
    finally:
        _cleanup(email)


def test_03_successful_verification_triggers_idn_002():
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, _ = _register(client, email)
        resp, cap = _verify(client, token)
        assert resp.status_code == 200, resp.text
        assert cap.subjects == [IDN_002], f"expected exactly IDN-002, got {cap.subjects}"

        user = _load(email)
        assert user.email_verified is True
        assert user.account_ready_sent_at is not None, "send must be recorded"
    finally:
        _cleanup(email)


def test_07_correct_recipient_receives_the_email():
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, _ = _register(client, email)
        _resp, cap = _verify(client, token)
        payload = cap.of(IDN_002)
        assert payload["to"] == [email.lower()], "only the account holder is addressed"
        assert len(payload["to"]) == 1
        assert payload["from"].startswith("Zoiko Steam <"), payload["from"]
        assert payload["html"] and payload["text"], "both parts must ship"
    finally:
        _cleanup(email)


def test_04_failed_verification_does_not_trigger_idn_002():
    client = TestClient(m.app)
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        resp = client.post("/api/auth/verify-email", json={"token": "x" * 44})
    assert resp.status_code == 400
    assert cap.calls == [], "an invalid token must send nothing"


def test_05_expired_token_does_not_trigger_idn_002():
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, _ = _register(client, email)
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email.lower()).one()
            c = db.query(IdentityChallenge).filter(IdentityChallenge.user_id == user.id).one()
            c.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()

        resp, cap = _verify(client, token)
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "TOKEN_EXPIRED"
        assert cap.calls == [], "an expired token must send nothing"
        assert _load(email).account_ready_sent_at is None
    finally:
        _cleanup(email)


def test_06_reused_token_does_not_send_duplicate_idn_002():
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, _ = _register(client, email)
        first, cap1 = _verify(client, token)
        assert first.status_code == 200
        assert cap1.subjects == [IDN_002]
        stamped = _load(email).account_ready_sent_at

        # Same link again, twice.
        for _ in range(2):
            again, cap2 = _verify(client, token)
            assert again.status_code == 400
            assert again.json()["detail"]["code"] == "TOKEN_ALREADY_USED"
            assert cap2.calls == [], "a reused link must not resend IDN-002"

        assert _load(email).account_ready_sent_at == stamped, "claim must not move"
    finally:
        _cleanup(email)


def test_19_email_is_not_sent_on_every_login():
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, _ = _register(client, email)
        _verify(client, token)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            for _ in range(3):
                r = client.post("/api/auth/login", json={
                    "identifier": email, "password": "correct-horse-battery"})
                assert r.status_code == 200, r.text

        # Narrowed when IDN-003 landed. This test's subject is IDN-002, and the original
        # "no email at all" assertion became wrong for the right reason: a first sign-in
        # from an unseen context now legitimately sends IDN-003. What must never happen is
        # an "access is ready" message on a login.
        assert IDN_002 not in cap.subjects, f"login must never send IDN-002, got {cap.subjects}"
        assert cap.subjects.count(email_mod.IDN_003_SUBJECT) <= 1, \
            "at most one new-context notice across repeated logins from the same device"
    finally:
        _cleanup(email)


def test_16_existing_login_flow_still_works():
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, _ = _register(client, email)
        _verify(client, token)
        _reset_limits()
        ok = client.post("/api/auth/login", json={
            "identifier": email, "password": "correct-horse-battery", "remember": False})
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["access_token"] and body["user"]["email"] == email.lower()
    finally:
        _cleanup(email)


def test_18_disabled_account_gets_no_false_access_ready_message():
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, _ = _register(client, email)
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email.lower()).one()
            user.is_active = False
            db.commit()
        finally:
            db.close()

        resp, cap = _verify(client, token)
        # Verification itself still succeeds — identity was proven.
        assert resp.status_code == 200, resp.text
        assert _load(email).email_verified is True
        # But the claim that access is active would be false, so nothing is sent.
        assert cap.calls == [], f"deactivated account must not be told access is ready: {cap.subjects}"
        assert _load(email).account_ready_sent_at is None
    finally:
        _cleanup(email)


def test_18b_suspended_organization_gets_no_access_ready_message():
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, _ = _register(client, email)
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email.lower()).one()
            org = db.get(Organization, user.org_id)
            org.status = "suspended"
            db.commit()
        finally:
            db.close()

        resp, cap = _verify(client, token)
        assert resp.status_code == 200, resp.text
        assert cap.calls == [], "a suspended tenant must not be told access is ready"
        assert _load(email).account_ready_sent_at is None
    finally:
        _cleanup(email)


def test_20_resend_failure_does_not_roll_back_verification():
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, _ = _register(client, email)
        # The provider raises on send. Verification must still stand.
        resp, cap = _verify(client, token, fail=True)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "verified"

        user = _load(email)
        assert user.email_verified is True, "a mail outage must not undo identity verification"
        assert user.email_verified_at is not None
        assert cap.calls, "a send was attempted"
    finally:
        _cleanup(email)


def test_17_existing_users_are_not_emailed_by_migration_or_startup():
    """Applying the schema must not mail anyone, and must not look like it did."""
    db = SessionLocal()
    try:
        verified_legacy = db.query(User).filter(
            User.email_verified.is_(True),
            User.account_ready_sent_at.is_(None),
        ).count()
        # A send marker may only ever exist on a verified account. If the migration had
        # stamped rows, or anything sent outside the verification path, this would be > 0.
        claimed_but_unverified = db.query(User).filter(
            User.account_ready_sent_at.isnot(None),
            User.email_verified.is_(False),
        ).count()
    finally:
        db.close()

    assert claimed_but_unverified == 0, \
        "an IDN-002 send marker exists on an unverified account — it can only follow verification"

    # Pre-existing accounts are verified (grandfathered by the IDN-001 migration) but must
    # carry no IDN-002 send marker — the column is never backfilled.
    assert verified_legacy > 0, "expected grandfathered accounts to exist"

    # The migration statements are inspected rather than executed. Re-running ensure_schema()
    # here would take an AccessExclusiveLock on shared tables and deadlock against the other
    # open sessions in this suite — and it would prove nothing that reading the statements
    # does not, since the claim is about what the SQL does, not that it can run twice.
    import create_tables
    stmts = " ".join(create_tables._USER_COLUMNS + create_tables._USER_BACKFILL).lower()
    assert "account_ready_sent_at timestamptz" in stmts, "column must be added"
    assert "update users set account_ready_sent_at" not in stmts,         "the migration must never stamp a send marker onto existing rows"
    assert "account_ready_sent_at" not in " ".join(create_tables._USER_BACKFILL).lower(),         "the backfill must not touch the IDN-002 marker"

    # Nothing in the module can send mail at import or schema time. Checked against actual
    # send paths rather than the substring "email" — the file legitimately names columns
    # like support_email and email_verified_at.
    src = open(create_tables.__file__, encoding="utf-8").read()
    for send_path in ("app.email", "from .email", "import email",
                      "send_account_ready_email", "send_email_verification_email",
                      "_send(", "resend", "httpx"):
        assert send_path not in src, \
            f"create_tables must not reference the send path {send_path!r}"



def test_15_existing_idn_001_flow_still_passes():
    """IDN-001 must be unweakened: unverified login still blocked, link still single-use."""
    email = _new_email()
    client = TestClient(m.app)
    try:
        token, cap1 = _register(client, email)
        assert cap1.subjects == [IDN_001]

        _reset_limits()
        blocked = client.post("/api/auth/login", json={
            "identifier": email, "password": "correct-horse-battery"})
        assert blocked.status_code == 403
        assert blocked.json()["detail"]["code"] == "EMAIL_VERIFICATION_REQUIRED"

        first, _ = _verify(client, token)
        assert first.status_code == 200
        second, _ = _verify(client, token)
        assert second.status_code == 400, "token must remain single-use"
    finally:
        _cleanup(email)


def test_claim_is_atomic_under_concurrency():
    """Two racing claims: exactly one wins. This is what prevents the duplicate."""
    email = _new_email()
    client = TestClient(m.app)
    try:
        _register(client, email)
        user = _load(email)
        db_a, db_b = SessionLocal(), SessionLocal()
        try:
            ua = db_a.get(User, user.id)
            ub = db_b.get(User, user.id)
            results = [
                identity_crud.claim_account_ready(db_a, ua),
                identity_crud.claim_account_ready(db_b, ub),
            ]
        finally:
            db_a.close()
            db_b.close()
        assert results.count(True) == 1, f"exactly one claim may win, got {results}"
    finally:
        _cleanup(email)


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
