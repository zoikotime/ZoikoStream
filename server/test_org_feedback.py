"""POST /api/organization/feedback — the Developer Platform footer's Feedback dialog.

Driven through the real app (TestClient(app.main.app)) against the isolated test database
that conftest.py enforces, with real session-backed tokens. Nothing in sys.modules is
replaced: an earlier version of this file swapped `app.db` for a MagicMock at import time,
which handed every test collected after it a fake database (test_org_overview.py went from
20 passed to 19 failed in the same run).

What it pins:
  * only a signed-in member of an organization can submit, and the organization is the
    caller's own — a body cannot name another (extra fields are refused);
  * a restricted or suspended organization is refused by the organization-state gate;
  * the category is one of four values, and the message is 5–4,000 characters once
    surrounding whitespace is removed;
  * an oversized body is refused before it is read, whether or not it declares a length;
  * submissions are rate-limited per user;
  * and — because nothing stores or forwards console feedback yet — a valid submission is
    answered 503 FEEDBACK_DELIVERY_UNAVAILABLE, never with a receipt. The log records the
    attempt's metadata and never the message text.
"""
import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import app.main as main_module
from app import ratelimit
from app.db import SessionLocal
from app.models import (
    ORG_STATE_ACTIVE,
    ORG_STATE_RESTRICTED,
    ORG_STATE_SUSPENDED,
    Organization,
    User,
)
from app.models.auth_session import AuthSession
from app.routers.organization import FEEDBACK_LIMIT, FEEDBACK_MAX_BODY_BYTES
from app.security import create_access_token

URL = "/api/organization/feedback"
VALID = {"category": "documentation", "message": "The webhook signing guide needs an example."}


@pytest.fixture
def fx():
    """Two organizations with a member each (users.org_id is NOT NULL, so every user has one).
    Rate-limit windows are cleared around each test. Everything is deleted afterwards."""
    ratelimit._HITS.clear()
    db = SessionLocal()
    made = {}
    try:
        org = Organization(name=f"fb-org-{uuid.uuid4().hex[:8]}", status=ORG_STATE_ACTIVE)
        other = Organization(name=f"fb-other-{uuid.uuid4().hex[:8]}", status=ORG_STATE_ACTIVE)
        db.add_all([org, other])
        db.flush()

        def mk(o, role="host"):
            u = User(org_id=o.id, full_name=f"fb {role}", role=role, is_active=True,
                     email=f"fb-{role}-{uuid.uuid4().hex[:10]}@example.com",
                     username=f"fb{role}{uuid.uuid4().hex[:10]}", password_hash="x",
                     email_verified=True)
            db.add(u)
            return u

        member, colleague, outsider = mk(org), mk(org), mk(other)
        db.commit()
        made = {
            "org_id": org.id, "other_org_id": other.id,
            "user_ids": [member.id, colleague.id, outsider.id],
            "member_id": member.id,
            "member": {"Authorization": f"Bearer {create_access_token(member, True)}"},
            "colleague": {"Authorization": f"Bearer {create_access_token(colleague, True)}"},
        }
        yield made
    finally:
        if made:
            db.execute(delete(AuthSession).where(AuthSession.user_id.in_(made["user_ids"])))
            db.execute(delete(User).where(User.id.in_(made["user_ids"])))
            db.execute(delete(Organization).where(
                Organization.id.in_([made["org_id"], made["other_org_id"]])))
            db.commit()
        db.close()
        ratelimit._HITS.clear()


@pytest.fixture
def client():
    return TestClient(main_module.app)


def _set_org_status(org_id, value):
    db = SessionLocal()
    try:
        db.get(Organization, org_id).status = value
        db.commit()
    finally:
        db.close()


def _undelivered(res):
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "FEEDBACK_DELIVERY_UNAVAILABLE"


# ── who may submit ──────────────────────────────────────────────────────────────────────

def test_unauthenticated_request_is_refused(client, fx):
    assert client.post(URL, json=VALID).status_code == 401


def test_invalid_token_is_refused(client, fx):
    res = client.post(URL, json=VALID, headers={"Authorization": "Bearer not-a-token"})
    assert res.status_code == 401


def test_the_organization_comes_from_the_token_not_the_body(client, fx, caplog):
    res = client.post(URL, json={**VALID, "org_id": str(fx["other_org_id"])}, headers=fx["member"])
    assert res.status_code == 422          # extra fields are refused outright
    caplog.set_level(logging.INFO, logger="app.routers.organization")
    _undelivered(client.post(URL, json=VALID, headers=fx["member"]))
    logged = " ".join(r.getMessage() for r in caplog.records if "org feedback" in r.getMessage())
    assert f"org_id={fx['org_id']}" in logged
    assert str(fx["other_org_id"]) not in logged


@pytest.mark.parametrize("state", [ORG_STATE_RESTRICTED, ORG_STATE_SUSPENDED])
def test_restricted_or_suspended_organization_is_refused(client, fx, state):
    _set_org_status(fx["org_id"], state)
    res = client.post(URL, json=VALID, headers=fx["member"])
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "ORGANIZATION_RESTRICTED"


# ── what may be submitted ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("category", ["developer_platform", "documentation", "webhooks_ingest", "general"])
def test_each_supported_category_is_accepted(client, fx, category):
    _undelivered(client.post(URL, json={"category": category, "message": VALID["message"]},
                             headers=fx["member"]))


def test_category_defaults_to_general(client, fx, caplog):
    caplog.set_level(logging.INFO, logger="app.routers.organization")
    _undelivered(client.post(URL, json={"message": VALID["message"]}, headers=fx["member"]))
    assert any("category=general" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("category", ["api_sdks", "", "GENERAL", "general\ninjected log line", "x" * 61])
def test_unsupported_category_is_refused(client, fx, category):
    res = client.post(URL, json={"category": category, "message": VALID["message"]}, headers=fx["member"])
    assert res.status_code == 422


@pytest.mark.parametrize("message", ["", "abcd", "     ", "  abcd  ", "\n\t \n"])
def test_short_or_whitespace_only_message_is_refused(client, fx, message):
    res = client.post(URL, json={"category": "general", "message": message}, headers=fx["member"])
    assert res.status_code == 422


def test_missing_message_is_refused(client, fx):
    assert client.post(URL, json={"category": "general"}, headers=fx["member"]).status_code == 422


@pytest.mark.parametrize("message,status", [
    ("abcde", 503),                  # the minimum, once stripped
    ("x" * 4000, 503),               # the maximum
    ("  " + "x" * 4000 + "  ", 503), # surrounding whitespace does not count
    ("x" * 4001, 422),               # one over
])
def test_message_length_bounds(client, fx, message, status):
    res = client.post(URL, json={"category": "general", "message": message}, headers=fx["member"])
    assert res.status_code == status, res.text


# ── what happens to it ──────────────────────────────────────────────────────────────────

def test_valid_feedback_is_not_claimed_as_received(client, fx):
    res = client.post(URL, json=VALID, headers=fx["member"])
    _undelivered(res)
    body = res.json()
    assert "received" not in res.text.lower()
    assert "not sent" in body["detail"]["message"]


def test_the_message_text_is_never_logged(client, fx, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "my api key is zsk_live_abc123 please rotate"
    _undelivered(client.post(URL, json={"category": "general", "message": secret}, headers=fx["member"]))
    everything = " ".join(r.getMessage() for r in caplog.records)
    assert "zsk_live_abc123" not in everything
    assert f"chars={len(secret)}" in everything


# ── abuse limits ────────────────────────────────────────────────────────────────────────

def test_submissions_are_rate_limited_per_user(client, fx):
    for _ in range(FEEDBACK_LIMIT):
        _undelivered(client.post(URL, json=VALID, headers=fx["member"]))
    res = client.post(URL, json=VALID, headers=fx["member"])
    assert res.status_code == 429
    assert res.headers["Retry-After"]
    # A colleague in the same organization has a budget of their own.
    _undelivered(client.post(URL, json=VALID, headers=fx["colleague"]))


def test_declared_oversized_body_is_refused_before_it_is_read(client, fx):
    big = b'{"category":"general","message":"' + b"x" * FEEDBACK_MAX_BODY_BYTES + b'"}'
    res = client.post(URL, content=big, headers={**fx["member"], "Content-Type": "application/json"})
    assert res.status_code == 413


def test_undeclared_oversized_body_is_refused(client, fx):
    def chunks():                    # no Content-Length: sent chunked
        yield b'{"category":"general","message":"'
        for _ in range(FEEDBACK_MAX_BODY_BYTES // 1024 + 2):
            yield b"x" * 1024
        yield b'"}'
    res = client.post(URL, content=chunks(), headers={**fx["member"], "Content-Type": "application/json"})
    assert res.status_code == 413


def test_undeclared_body_within_the_cap_still_reaches_the_endpoint(client, fx):
    def chunks():
        yield b'{"category":"general",'
        yield b'"message":"chunked but small enough"}'
    res = client.post(URL, content=chunks(), headers={**fx["member"], "Content-Type": "application/json"})
    _undelivered(res)


def test_the_body_cap_leaves_other_routes_alone(client, fx):
    # Same organization router, not in the cap's table: a large body reaches normal validation.
    big = b'{"x":"' + b"y" * (FEEDBACK_MAX_BODY_BYTES * 2) + b'"}'
    res = client.post("/api/organization/abuse-reports", content=big,
                      headers={**fx["member"], "Content-Type": "application/json"})
    assert res.status_code != 413
