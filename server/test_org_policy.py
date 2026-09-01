"""Organization security-policy enforcement (ZST-EC-001 Phase 10).

The audit listed three settings that were stored, displayed and read by nothing:
`min_password_length`, `session_timeout` and `allowed_domains`. These tests assert each is
now actually enforced, and — just as importantly — that a tenant setting can only ever make
the platform baseline STRICTER, never weaker.

Run with `python test_org_policy.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.config import settings
from app.db import SessionLocal
from app.models import (
    AccountRecovery,
    AuditLog,
    ElevationSession,
    IdentityChallenge,
    Invitation,
    Organization,
    OrgMembershipEvent,
    SignInEvent,
    StepUpGrant,
    User,
)
from app.security import ALGORITHM, hash_password
from app.services import org_policy

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"


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


def _new_email(tag="pol"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class Org:
    def __init__(self, security=None):
        db = SessionLocal()
        try:
            org = Organization(name=f"Pol Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata", security=security or {})
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.admin_email = _new_email("admin")
            self.admin_id = self._u(db, "org_admin", self.admin_email)
            org.owner_user_id = self.admin_id
            db.commit()
        finally:
            db.close()

    def _u(self, db, role, email):
        user = User(org_id=self.org_id, full_name="Person", role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=datetime.now(timezone.utc))
        db.add(user)
        db.flush()
        return user.id

    def token(self):
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": self.admin_email, "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["access_token"]

    def org_row(self):
        db = SessionLocal()
        try:
            return db.get(Organization, self.org_id)
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(AuditLog).filter(AuditLog.org_id == self.org_id).delete()
            db.query(Invitation).filter(Invitation.org_id == self.org_id).delete()
            db.query(OrgMembershipEvent).filter(
                OrgMembershipEvent.org_id == self.org_id).delete()
            db.commit()
            org = db.get(Organization, self.org_id)
            if org is not None:
                org.owner_user_id = None
            db.commit()
            for user in db.query(User).filter(User.org_id == self.org_id).all():
                for model in (SignInEvent, IdentityChallenge, AccountRecovery,
                              ElevationSession, StepUpGrant):
                    db.query(model).filter(model.user_id == user.id).delete()
                db.delete(user)
            db.commit()
            org = db.get(Organization, self.org_id)
            if org is not None:
                db.delete(org)
                db.commit()
        finally:
            db.close()


# ══ min_password_length ═════════════════════════════════════════════════════════

def test_password_floor_is_enforced_not_just_stored():
    o = Org(security={"min_password_length": 16})
    client = TestClient(m.app)
    try:
        # Recovery code, then a password that satisfies the platform baseline (8) but not
        # this Organization's floor (16).
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            client.post("/api/auth/forgot-password", json={"email": o.admin_email})
        code = None
        for c in cap.calls:
            if c["payload"]["subject"] == email_mod.IDN_007_SUBJECT_VERIFICATION:
                for row in c["payload"]["text"].splitlines():
                    if row.startswith("Verification code:"):
                        code = row.split(":", 1)[1].strip()
        assert code, "recovery code not delivered"

        _reset_limits()
        short = client.post("/api/auth/reset-password",
                            json={"email": o.admin_email, "otp": code,
                                  "password": "shortpw1"})
        assert short.status_code == 422, \
            f"a password below the Organization floor must be refused; got {short.status_code}"
        assert "at least 16 characters" in short.text

        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            ok = client.post("/api/auth/reset-password",
                             json={"email": o.admin_email, "otp": code,
                                   "password": "a-long-enough-password"})
        assert ok.status_code == 200, ok.text
    finally:
        o.cleanup()


def test_password_floor_cannot_go_below_the_platform_baseline():
    """A tenant setting may tighten the product, never weaken it."""
    weak = Org(security={"min_password_length": 4})
    try:
        assert org_policy.min_password_length(weak.org_row()) == \
            org_policy.BASELINE_MIN_PASSWORD_LENGTH, \
            "an Organization must not be able to lower the platform minimum"
        assert org_policy.password_violation(weak.org_row(), "12345678") is None
        assert org_policy.password_violation(weak.org_row(), "1234") is not None
    finally:
        weak.cleanup()

    strict = Org(security={"min_password_length": 20})
    try:
        assert org_policy.min_password_length(strict.org_row()) == 20
    finally:
        strict.cleanup()

    # Unset and garbage both fall back to the baseline rather than being honoured.
    plain = Org()
    try:
        assert org_policy.min_password_length(plain.org_row()) == 8
    finally:
        plain.cleanup()
    junk = Org(security={"min_password_length": "not-a-number"})
    try:
        assert org_policy.min_password_length(junk.org_row()) == 8
    finally:
        junk.cleanup()


# ══ session_timeout ═════════════════════════════════════════════════════════════

def test_session_timeout_shortens_the_real_token():
    o = Org(security={"session_timeout": "1 hour"})
    try:
        _headers, token = o.token()
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        life = datetime.fromtimestamp(claims["exp"], tz=timezone.utc) \
            - datetime.now(timezone.utc)
        assert life <= timedelta(hours=1, minutes=1), \
            f"the configured 1-hour timeout must shorten the token; got {life}"
        assert life > timedelta(minutes=50), "and must not be absurdly short"
    finally:
        o.cleanup()


def test_session_timeout_cannot_extend_beyond_the_platform_default():
    o = Org(security={"session_timeout": "24 hours"})
    try:
        default = timedelta(hours=settings.ACCESS_TOKEN_HOURS)
        got = org_policy.session_lifetime(o.org_row(), remember=False)
        assert got <= default, \
            "an Organization must not be able to hold a token longer than the platform allows"
    finally:
        o.cleanup()

    junk = Org(security={"session_timeout": "forever"})
    try:
        assert org_policy.session_lifetime(junk.org_row()) == \
            timedelta(hours=settings.ACCESS_TOKEN_HOURS), \
            "an unrecognized value must fall back, never be honoured as written"
        assert org_policy.session_timeout_label(junk.org_row()).endswith("hours")
    finally:
        junk.cleanup()


# ══ allowed_domains ═════════════════════════════════════════════════════════════

def test_allowed_domains_gates_invitations():
    o = Org(security={"allowed_domains": "acme.com, partner.co.uk"})
    client = TestClient(m.app)
    try:
        headers, _ = o.token()

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            bad = client.post("/api/organization/invitations",
                              json={"email": "outsider@evil.com", "role": "viewer"},
                              headers=headers)
        assert bad.status_code == 422, \
            f"an address outside the allowlist must be refused; got {bad.status_code}"
        assert "acme.com" in bad.text
        assert cap.calls == [], "and nothing may be sent for a refused invitation"

        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            good = client.post("/api/organization/invitations",
                               json={"email": "newhire@acme.com", "role": "viewer"},
                               headers=headers)
        assert good.status_code == 201, good.text
        assert email_mod.ORG_001_SUBJECT.format(org=o.org_row().name) in cap2.subjects
    finally:
        o.cleanup()


def test_allowed_domains_matching_is_not_a_suffix_trick():
    o = Org(security={"allowed_domains": "example.com"})
    try:
        org = o.org_row()
        assert org_policy.domain_violation(org, "a@example.com") is None
        assert org_policy.domain_violation(org, "a@eu.example.com") is None, \
            "a subdomain of an allowed domain is allowed"
        assert org_policy.domain_violation(org, "a@notexample.com") is not None, \
            "a lookalike domain must not pass a naive suffix check"
        assert org_policy.domain_violation(org, "a@example.com.evil.net") is not None
    finally:
        o.cleanup()

    plain = Org()
    try:
        assert org_policy.domain_violation(plain.org_row(), "anyone@anywhere.com") is None, \
            "an empty allowlist means no restriction"
    finally:
        plain.cleanup()


def test_allowed_domains_does_not_lock_out_existing_members():
    """Sign-in is deliberately not gated: an address predating the policy must still work."""
    o = Org()
    try:
        db = SessionLocal()
        try:
            db.get(Organization, o.org_id).security = {"allowed_domains": "acme.com"}
            db.commit()
        finally:
            db.close()
        headers, _ = o.token()          # admin_email is @example.com, outside the allowlist
        assert headers["Authorization"].startswith("Bearer "), \
            "an existing member must not be locked out by a later domain policy"
    finally:
        o.cleanup()


try:
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
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
