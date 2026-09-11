"""The recovery challenge's shape, and the properties the sign-in UI has to agree with.

A stale client asked for a "4-digit code" while this backend had already moved to six, so
the code people were reading out of their inbox could not be entered at all. These pin the
format itself — six digits, zero-padded, compared as a STRING — and the policy the endpoint
now publishes, so a client can no longer hold a private opinion about either.

Runs under pytest against the suite's own database (see conftest.py's guard).
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import email as email_mod
from app import ratelimit
from app.crud import recovery as recovery_crud
from app.db import SessionLocal
from app.main import app
from app.models import ACCOUNT_RECOVERY, IdentityChallenge, Organization, User
from app.security import hash_password, verify_password

client = TestClient(app)

OLD = "OldPassword123!"
NEW = "BrandNewPassword456!"
# The bug report's real code was 071487. It is NOT reused as a literal here:
# IdentityChallenge.token_hash carries a GLOBAL unique index (models/identity.py), so one
# fixed value can only ever be minted once in a database — a second test, or a second run
# against the same database, would collide on the index rather than test anything. What
# matters is the shape, so each test mints its own fresh code that starts with a zero.
def zero_led_code():
    """A fresh 6-digit code whose leading digit is 0 — the digit a numeric round-trip eats."""
    return "0" + f"{secrets.randbelow(10 ** 5):05d}"

VERIFY_SUBJECT = email_mod.IDN_007_SUBJECT_VERIFICATION


class Captured:
    """Collects outbound mail instead of sending it."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append(json or {})
        class _Resp:
            status_code = 200
            text = "{}"
            def raise_for_status(self):
                return None
        return _Resp()

    def of(self, subject):
        hits = [c for c in self.calls if c.get("subject") == subject]
        assert hits, "no message with subject %r; got %r" % (
            subject, [c.get("subject") for c in self.calls])
        return hits[-1]


def capture():
    cap = Captured()
    return cap, patch.object(email_mod.httpx, "post", cap)


@pytest.fixture(autouse=True)
def fresh_rate_limit():
    """Each test is a separate caller. The limiter is a per-process dict keyed by client IP
    and TestClient presents one IP, so without this the recovery scopes drain across the
    file and a later test reads 429 where it asserts a refusal."""
    ratelimit._HITS.clear()
    yield


@pytest.fixture
def account():
    db = SessionLocal()
    org = Organization(name=f"RecOrg {uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()
    user = User(
        email=f"rec-{uuid.uuid4().hex[:12]}@example.com",
        username=f"rec{uuid.uuid4().hex[:8]}",
        full_name="Recovery Tester",
        password_hash=hash_password(OLD),
        role="org_admin",
        org_id=org.id,
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user

    # Best-effort: recovery writes rows that reference the user, and chasing every dependent
    # table here would make this fixture a second, worse copy of the schema. Each test mints
    # a unique account and the suite runs against a throwaway database.
    try:
        db.query(User).filter(User.id == user.id).delete()
        db.query(Organization).filter(Organization.id == org.id).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def forgot(email):
    """Start recovery and return (response, emailed_code)."""
    cap, ctx = capture()
    with ctx:
        resp = client.post("/api/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200, resp.text
    code = None
    for row in cap.of(VERIFY_SUBJECT)["text"].splitlines():
        if row.startswith("Verification code:"):
            code = row.split(":", 1)[1].strip()
    return resp, code, cap


def login(email, password):
    return client.post("/api/auth/login", json={"identifier": email, "password": password})


def stored_hash(user_id):
    db = SessionLocal()
    try:
        return db.get(User, user_id).password_hash
    finally:
        db.close()


# ── the format itself ──────────────────────────────────────────────────────────────────

def test_generated_codes_are_always_six_digits():
    """Including the ones that start with zero — which is precisely why the value is
    formatted rather than stringified from an int."""
    codes = [recovery_crud._new_code() for _ in range(2000)]
    assert all(len(c) == 6 and c.isdigit() for c in codes), "not all codes were 6 digits"
    # With 2000 draws, P(no code starts with 0) is (0.9)^2000 — indistinguishable from zero.
    # If this ever fires, zero-padding has been lost.
    assert any(c.startswith("0") for c in codes), "zero-padding appears to be gone"
    assert recovery_crud.CODE_DIGITS == 6


def test_the_schema_refuses_anything_that_is_not_six_digits(account):
    """The old 4-digit client would be refused at the door rather than silently mis-matched."""
    forgot(account.email)
    for bad in ("0714", "07148", "0714879", "07148a", "", "  071487  ", "71487"):
        r = client.post("/api/auth/verify-otp", json={"email": account.email, "otp": bad})
        assert r.status_code == 422, "%r should not have passed validation: %s" % (bad, r.text)


# ── the policy the endpoint publishes ──────────────────────────────────────────────────

def test_forgot_password_reports_the_real_policy(account):
    resp, _code, _cap = forgot(account.email)
    body = resp.json()
    assert body["expires_in_minutes"] == recovery_crud.RECOVERY_TTL_MINUTES == 15
    assert body["code_length"] == recovery_crud.CODE_DIGITS == 6


def test_the_policy_is_reported_for_an_unknown_address_too(account):
    """It is policy, not account state — answering differently would hand back exactly the
    enumeration oracle the constant message exists to deny."""
    known = client.post("/api/auth/forgot-password", json={"email": account.email})
    unknown = client.post("/api/auth/forgot-password",
                          json={"email": f"nobody-{uuid.uuid4().hex[:10]}@example.com"})
    assert known.json() == unknown.json()


# ── the email ──────────────────────────────────────────────────────────────────────────

def test_the_email_carries_all_six_digits_and_no_hash(account):
    minted = zero_led_code()
    with patch.object(recovery_crud, "_new_code", lambda: minted):
        _resp, code, cap = forgot(account.email)

    assert code == minted, "the emailed code lost its leading zero"
    assert len(code) == 6 and code.startswith("0")
    payload = cap.of(VERIFY_SUBJECT)
    for part in (payload["text"], payload["html"]):
        assert minted in part
    # The raw code is the only secret these templates may render. The stored form must not
    # appear anywhere in the message.
    db = SessionLocal()
    try:
        challenge = db.scalar(
            select(IdentityChallenge).where(
                IdentityChallenge.user_id == account.id,
                IdentityChallenge.purpose == ACCOUNT_RECOVERY,
            ).order_by(IdentityChallenge.expires_at.desc())
        )
        token_hash = challenge.token_hash
    finally:
        db.close()
    for part in (payload["text"], payload["html"]):
        assert token_hash not in part, "the stored token hash leaked into the email"


# ── end to end, on the code that exposed the bug ───────────────────────────────────────

def test_a_leading_zero_code_verifies_and_resets(account):
    minted = zero_led_code()
    with patch.object(recovery_crud, "_new_code", lambda: minted):
        _resp, code, _cap = forgot(account.email)
    assert code == minted and code.startswith("0")
    before = stored_hash(account.id)

    # Verify does not spend the code — the reset step still needs it.
    assert client.post("/api/auth/verify-otp",
                       json={"email": account.email, "otp": code}).status_code == 200

    with capture()[1]:
        r = client.post("/api/auth/reset-password",
                        json={"email": account.email, "otp": code, "password": NEW})
    assert r.status_code == 200, r.text

    after = stored_hash(account.id)
    assert after != before
    assert verify_password(NEW, after)
    assert not verify_password(OLD, after)
    assert login(account.email, OLD).status_code == 401
    assert login(account.email, NEW).status_code == 200


def test_the_numeric_form_of_the_same_code_is_rejected(account):
    """`071487` and `71487` are different strings and hash differently. This is the exact
    failure a Number()/parseInt() anywhere in the chain would produce."""
    minted = zero_led_code()
    with patch.object(recovery_crud, "_new_code", lambda: minted):
        forgot(account.email)

    # str(int("071487")) == "71487" — the exact mangling a Number()/parseInt() produces.
    r = client.post("/api/auth/verify-otp",
                    json={"email": account.email, "otp": str(int(minted))})
    assert r.status_code == 422  # 5 digits never reaches the comparison at all
    assert verify_password(OLD, stored_hash(account.id))


# ── refusals ───────────────────────────────────────────────────────────────────────────

def test_a_wrong_code_is_rejected_and_changes_nothing(account):
    minted = zero_led_code()
    with patch.object(recovery_crud, "_new_code", lambda: minted):
        forgot(account.email)
    before = stored_hash(account.id)

    r = client.post("/api/auth/reset-password",
                    json={"email": account.email, "otp": "999999", "password": NEW})
    assert r.status_code == 400
    assert stored_hash(account.id) == before
    assert login(account.email, OLD).status_code == 200


def test_an_expired_code_is_rejected(account):
    _resp, code, _cap = forgot(account.email)

    db = SessionLocal()
    try:
        challenge = db.scalar(
            select(IdentityChallenge).where(
                IdentityChallenge.user_id == account.id,
                IdentityChallenge.purpose == ACCOUNT_RECOVERY,
                IdentityChallenge.consumed_at.is_(None),
            )
        )
        challenge.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    r = client.post("/api/auth/reset-password",
                    json={"email": account.email, "otp": code, "password": NEW})
    assert r.status_code == 400
    assert verify_password(OLD, stored_hash(account.id))


def test_a_code_cannot_be_used_twice(account):
    _resp, code, _cap = forgot(account.email)
    with capture()[1]:
        assert client.post("/api/auth/reset-password",
                           json={"email": account.email, "otp": code,
                                 "password": NEW}).status_code == 200

    # Single-use: the same code must not drive a second reset.
    second = "SecondReset789!"
    r = client.post("/api/auth/reset-password",
                    json={"email": account.email, "otp": code, "password": second})
    assert r.status_code == 400
    assert not verify_password(second, stored_hash(account.id))
    assert verify_password(NEW, stored_hash(account.id))


def test_resending_supersedes_the_previous_code(account):
    _resp, first, _cap = forgot(account.email)
    _resp, second, _cap = forgot(account.email)
    assert first != second

    assert client.post("/api/auth/verify-otp",
                       json={"email": account.email, "otp": first}).status_code == 400
    assert client.post("/api/auth/verify-otp",
                       json={"email": account.email, "otp": second}).status_code == 200


def test_too_many_wrong_codes_locks_the_recovery(account):
    forgot(account.email)
    codes = [f"{n:06d}" for n in range(1, recovery_crud.RECOVERY_MAX_ATTEMPTS + 2)]
    statuses = [
        client.post("/api/auth/verify-otp",
                    json={"email": account.email, "otp": c}).status_code
        for c in codes
    ]
    # verify_code increments, then locks when the budget is spent, then reports LOCKED in
    # the same call — so the FINAL wrong attempt is itself answered 429, not 400.
    limit = recovery_crud.RECOVERY_MAX_ATTEMPTS
    assert statuses[:limit - 1] == [400] * (limit - 1), statuses
    assert statuses[limit - 1] == 429, statuses
    assert statuses[-1] == 429, statuses
    # And the lockout is not walkable by simply asking for a new code: the attempt budget
    # lives on the recovery, not on the challenge.
    _resp, fresh, _cap = forgot(account.email)
    assert client.post("/api/auth/verify-otp",
                       json={"email": account.email, "otp": fresh}).status_code == 429
    assert verify_password(OLD, stored_hash(account.id))
