"""PATCH /api/auth/password — the signed-in password change.

The properties that matter are the ones a mistake here would quietly break: the stored hash
actually changes, the OLD password stops working, the NEW one logs in, and nothing in the
response or the mail carries a password or a hash.

Runs under pytest against the suite's own database (see conftest.py's guard).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.db import SessionLocal
from app.main import app
from app.models import Organization, User
from app.security import hash_password, verify_password

client = TestClient(app)

OLD = "OldPassword123!"
NEW = "BrandNewPassword456!"


@pytest.fixture(autouse=True)
def fresh_rate_limit():
    """Give each test the budget a separate caller would really have.

    The limiter is a per-PROCESS dict keyed by client IP, and TestClient presents the same
    IP for every request in the file — so all eight tests below were sharing ONE 5-per-300s
    password-change budget. The sixth got a 429 instead of the refusal it asserts, and the
    seventh passed for the wrong reason (a 429 body also contains no password). Clearing the
    two scopes this file exercises restores per-test independence WITHOUT weakening the
    limiter: it is still mounted on the endpoint, and test_the_rate_limit_is_enforced below
    exhausts it on purpose to prove it.
    """
    for scope in ("password-change", "login"):
        for key in [k for k in ratelimit._HITS if k.startswith(f"{scope}:")]:
            ratelimit._HITS.pop(key, None)
    yield


@pytest.fixture
def account():
    """A verified, active user in a real organization, cleaned up afterwards."""
    db = SessionLocal()
    org = Organization(name=f"PwdOrg {uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()
    user = User(
        email=f"pwd-{uuid.uuid4().hex[:12]}@example.com",
        username=f"pwd{uuid.uuid4().hex[:8]}",
        full_name="Password Tester",
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

    # Best-effort cleanup. Signing in writes sign_in_events (and identity-security rows)
    # that reference the user, so a plain DELETE hits a foreign key — and chasing every
    # dependent table here would make this fixture a second, worse copy of the schema.
    # Each test mints a UNIQUE account, so a leftover row cannot reach another test, and
    # the suite runs against a throwaway database (conftest.py refuses anything else).
    try:
        db.query(User).filter(User.id == user.id).delete()
        db.query(Organization).filter(Organization.id == org.id).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def login(email, password):
    # LoginIn takes  (email OR username), not .
    return client.post("/api/auth/login", json={"identifier": email, "password": password})


def auth_header(email, password):
    r = login(email, password)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def stored_hash(user_id):
    db = SessionLocal()
    try:
        return db.get(User, user_id).password_hash
    finally:
        db.close()


# ── the happy path, and the three facts that prove it really happened ──────────────────

def test_authenticated_user_changes_password(account):
    before = stored_hash(account.id)
    r = client.patch(
        "/api/auth/password",
        json={"current_password": OLD, "new_password": NEW},
        headers=auth_header(account.email, OLD),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"message": "Password updated successfully"}

    # 1. the stored hash actually changed
    after = stored_hash(account.id)
    assert after != before
    # 2. and it is the NEW password that it now represents
    assert verify_password(NEW, after)
    assert not verify_password(OLD, after)


def test_old_password_stops_working_and_new_one_logs_in(account):
    client.patch(
        "/api/auth/password",
        json={"current_password": OLD, "new_password": NEW},
        headers=auth_header(account.email, OLD),
    )
    assert login(account.email, OLD).status_code == 401
    assert login(account.email, NEW).status_code == 200


# ── refusals ───────────────────────────────────────────────────────────────────────────

def test_wrong_current_password_is_rejected_and_changes_nothing(account):
    before = stored_hash(account.id)
    r = client.patch(
        "/api/auth/password",
        json={"current_password": "NotMyPassword999!", "new_password": NEW},
        headers=auth_header(account.email, OLD),
    )
    assert r.status_code == 400
    assert "current password" in r.json()["detail"].lower()
    assert stored_hash(account.id) == before
    # The real password still works — the failed attempt was inert.
    assert login(account.email, OLD).status_code == 200


def test_unauthenticated_request_is_rejected(account):
    before = stored_hash(account.id)
    r = client.patch("/api/auth/password",
                     json={"current_password": OLD, "new_password": NEW})
    assert r.status_code in (401, 403)
    assert stored_hash(account.id) == before


def test_password_below_the_schema_bound_is_rejected(account):
    before = stored_hash(account.id)
    r = client.patch(
        "/api/auth/password",
        json={"current_password": OLD, "new_password": "short"},
        headers=auth_header(account.email, OLD),
    )
    assert r.status_code == 422
    assert stored_hash(account.id) == before


def test_reusing_the_current_password_is_rejected(account):
    """A "change" that changes nothing would still send a credential-changed email, which
    is how people learn to ignore those."""
    before = stored_hash(account.id)
    r = client.patch(
        "/api/auth/password",
        json={"current_password": OLD, "new_password": OLD},
        headers=auth_header(account.email, OLD),
    )
    assert r.status_code == 422
    assert "different" in r.json()["detail"].lower()
    assert stored_hash(account.id) == before


# ── the protection stays mounted ───────────────────────────────────────────────────────

def test_the_rate_limit_is_enforced(account):
    """Guessing the current password is the attack this endpoint invites, so the limiter is
    part of the contract rather than an incidental decorator. Five wrong attempts are spent
    deliberately; the sixth must be refused as rate-limited, not merely as wrong."""
    headers = auth_header(account.email, OLD)
    codes = [
        client.patch(
            "/api/auth/password",
            json={"current_password": f"WrongGuess{n}!", "new_password": NEW},
            headers=headers,
        ).status_code
        for n in range(6)
    ]
    assert codes[:5] == [400] * 5, codes
    assert codes[5] == 429, codes
    # And the account is untouched by the whole burst.
    assert verify_password(OLD, stored_hash(account.id))


# ── disclosure ─────────────────────────────────────────────────────────────────────────

def test_response_never_carries_a_password_or_a_hash(account):
    r = client.patch(
        "/api/auth/password",
        json={"current_password": OLD, "new_password": NEW},
        headers=auth_header(account.email, OLD),
    )
    body = r.text
    for secret in (OLD, NEW, "password_hash", "$2b$", "$2a$"):
        assert secret not in body, f"{secret!r} leaked into the response"


def test_the_account_is_taken_from_the_token_not_the_body(account):
    """The endpoint accepts no email/user id, so it cannot be aimed at another account."""
    from app.schemas import ChangePasswordIn

    assert set(ChangePasswordIn.model_fields) == {"current_password", "new_password"}
