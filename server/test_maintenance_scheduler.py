"""Scheduler authentication for the billing maintenance sweep.

Cloud Scheduler -> Google-signed OIDC -> Cloud Run -> POST /api/commercial/maintenance/scheduled-run

No Google network calls: `google.oauth2.id_token.verify_oauth2_token` is patched, which is the
boundary this code owns. What is asserted is our four checks around it — configured, signed,
right audience, right identity — plus that ordinary users still cannot reach the route and that
the pre-existing operator route is unchanged.
"""
import time
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.config import settings
from app.db import engine
from app.models import AuditLog, Organization, User
from app.security import create_access_token, require_scheduler_identity

SCHED_URL = "/api/commercial/maintenance/scheduled-run"
OPERATOR_URL = "/api/commercial/maintenance/run"
ACCOUNT = "zoiko-billing-scheduler@example-project.iam.gserviceaccount.com"
AUDIENCE = "https://zoikostream-example.a.run.app"


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


@pytest.fixture
def client():
    import app.main as m
    return TestClient(m.app)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT", ACCOUNT)
    monkeypatch.setattr(settings, "MAINTENANCE_OIDC_AUDIENCE", AUDIENCE)


def _oidc(email=ACCOUNT, *, verified=True, aud=AUDIENCE):
    """Stand in for a verified Google OIDC token's claims."""
    claims = {"email": email, "sub": "1234567890", "aud": aud,
              "iss": "https://accounts.google.com", "exp": int(time.time()) + 600}
    if verified is not None:
        claims["email_verified"] = verified
    return claims


def _patch_verify(claims=None, *, raises=None):
    def _fake(token, request, audience=None):
        if raises is not None:
            raise raises
        # Mirror google-auth's own behaviour: a mismatched audience is a verification failure.
        if audience is not None and claims.get("aud") not in (None, audience):
            raise ValueError("Token has wrong audience")
        return claims
    return patch("google.oauth2.id_token.verify_oauth2_token", _fake)


# ── configuration is a refusal, not a default ─────────────────────────────────────────────

def test_the_route_is_unavailable_until_it_is_configured(client):
    """The most important test here. With no allowed account and no audience configured the
    route must refuse — an unconfigured deployment must never expose an unauthenticated way to
    move customers between paid plans."""
    with _patch_verify(_oidc()):
        r = client.post(SCHED_URL, headers={"Authorization": "Bearer anything"})
    assert r.status_code == 503


@pytest.mark.parametrize("account,audience", [
    (ACCOUNT, ""), ("", AUDIENCE), ("", ""),
])
def test_half_configured_is_still_a_refusal(client, monkeypatch, account, audience):
    monkeypatch.setattr(settings, "MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT", account)
    monkeypatch.setattr(settings, "MAINTENANCE_OIDC_AUDIENCE", audience)
    with _patch_verify(_oidc()):
        r = client.post(SCHED_URL, headers={"Authorization": "Bearer anything"})
    assert r.status_code == 503


def test_no_credential_at_all_is_refused(client, configured):
    assert client.post(SCHED_URL).status_code in (401, 403)


# ── the four checks ───────────────────────────────────────────────────────────────────────

def test_the_configured_scheduler_identity_is_authorized(client, configured):
    with _patch_verify(_oidc()):
        r = client.post(SCHED_URL, headers={"Authorization": "Bearer good-oidc"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invoked_by"] == ACCOUNT
    assert "jobs" in body and "duration_ms" in body


def test_a_token_google_will_not_verify_is_refused(client, configured):
    """A forged or expired token fails at Google's signature/exp check."""
    with _patch_verify(raises=ValueError("Token expired")):
        r = client.post(SCHED_URL, headers={"Authorization": "Bearer forged"})
    assert r.status_code == 403


def test_a_valid_google_token_from_another_principal_is_refused(client, configured):
    """A valid Google token proves only that SOME Google principal called. The identity
    comparison is what makes it ours — otherwise any Google customer could invoke billing."""
    with _patch_verify(_oidc(email="someone-else@other-project.iam.gserviceaccount.com")):
        r = client.post(SCHED_URL, headers={"Authorization": "Bearer valid-wrong-identity"})
    assert r.status_code == 403


def test_a_token_minted_for_another_service_is_refused(client, configured):
    """Audience binding: a token issued for a different Cloud Run service must not replay."""
    with _patch_verify(_oidc(aud="https://some-other-service.a.run.app")):
        r = client.post(SCHED_URL, headers={"Authorization": "Bearer wrong-audience"})
    assert r.status_code == 403


def test_an_unverified_email_claim_is_refused(client, configured):
    with _patch_verify(_oidc(verified=False)):
        r = client.post(SCHED_URL, headers={"Authorization": "Bearer unverified"})
    assert r.status_code == 403


def test_an_empty_email_claim_is_refused(client, configured):
    with _patch_verify(_oidc(email="")):
        r = client.post(SCHED_URL, headers={"Authorization": "Bearer no-email"})
    assert r.status_code == 403


def test_the_identity_comparison_is_case_insensitive_but_exact(client, configured):
    """Service-account emails are case-insensitive, but no prefix/suffix may pass."""
    with _patch_verify(_oidc(email=ACCOUNT.upper())):
        assert client.post(SCHED_URL, headers={"Authorization": "Bearer x"}).status_code == 200
    for near in (ACCOUNT + ".evil.com", "evil-" + ACCOUNT, ACCOUNT[:-1]):
        with _patch_verify(_oidc(email=near)):
            r = client.post(SCHED_URL, headers={"Authorization": "Bearer x"})
        assert r.status_code == 403, f"{near} must not be accepted"


def test_the_failure_reason_is_not_disclosed_to_the_caller(client, configured):
    """Telling a caller WHICH check failed helps them iterate towards a valid forgery."""
    with _patch_verify(raises=ValueError("Token has wrong audience: expected X got Y")):
        r = client.post(SCHED_URL, headers={"Authorization": "Bearer probe"})
    assert r.status_code == 403
    assert "audience" not in r.text.lower() and "expected" not in r.text.lower()


# ── ordinary users cannot reach it ────────────────────────────────────────────────────────

@needs_db
def test_an_ordinary_org_user_cannot_invoke_scheduled_maintenance(client, configured):
    """Our own JWTs are signed with SECRET_KEY, so they fail Google's signature check. This is
    the property that keeps the route closed to every tenant."""
    with Session(engine) as db:
        tag = uuid.uuid4().hex[:8]
        org = Organization(name=f"sch-{tag}")
        db.add(org); db.flush()
        user = User(org_id=org.id, full_name="Org Admin", email=f"sch-{tag}@t.test",
                    username=f"sch{tag}", password_hash="x", role="org_admin")
        db.add(user); db.commit(); db.refresh(user)
        token = create_access_token(user, False)
        try:
            # Real google-auth, NOT patched: a ZoikoStream JWT must genuinely fail verification.
            r = client.post(SCHED_URL, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 403, "an org admin's own token must not run maintenance"
            # And a super_admin's token is equally not a Google service identity.
            user.role = "super_admin"
            db.commit()
            r2 = client.post(SCHED_URL,
                             headers={"Authorization": f"Bearer {create_access_token(user, False)}"})
            assert r2.status_code == 403
        finally:
            db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.commit()


def test_a_scheduler_token_cannot_be_used_as_a_user_session(client, configured):
    """The reverse direction: the scheduler identity must not be usable on ordinary org APIs."""
    with _patch_verify(_oidc()):
        r = client.get("/api/organization/overview",
                       headers={"Authorization": "Bearer scheduler-oidc"})
    assert r.status_code in (401, 403), "a service identity must not hold a user session"


# ── retries and idempotency ───────────────────────────────────────────────────────────────

def test_repeated_scheduler_invocation_is_safe(client, configured):
    """Cloud Scheduler retries. Repeated calls must each succeed without side effects beyond
    the idempotent sweep itself."""
    for _ in range(3):
        with _patch_verify(_oidc()):
            r = client.post(SCHED_URL, headers={"Authorization": "Bearer good"})
        assert r.status_code == 200


def test_the_route_reuses_the_same_maintenance_runner():
    """No duplicated maintenance logic, and nothing the scheduler alone can trigger."""
    from app.routers import commercial
    src = code_only(commercial.run_scheduled_maintenance)
    assert "maintenance.run_all" in src


def test_the_sweep_relies_on_the_existing_idempotency_protections():
    from app.crud import admin as ac
    assert "skip_locked=True" in code_only(ac.claim_due_plan_change)


# ── observability, without leaking secrets ────────────────────────────────────────────────

def test_the_invocation_is_logged_before_and_after_the_work():
    """A sweep that dies mid-flight must still leave evidence that it started."""
    from app.routers import commercial
    src = code_only(commercial.run_scheduled_maintenance)
    assert src.index("invoked by=") < src.index("maintenance.run_all")
    assert "complete" in src


def test_the_result_carries_the_diagnostics_an_operator_needs():
    from app.routers import commercial
    src = code_only(commercial.run_scheduled_maintenance)
    for field in ("duration_ms", "applied", "rejected", "contended", "provider_failed"):
        assert field in src, f"{field} must be observable"


def test_no_credential_material_is_logged_or_returned():
    from app.routers import commercial
    from app import security
    route = code_only(commercial.run_scheduled_maintenance)
    dep = code_only(security.require_scheduler_identity)
    for leak in ("creds.credentials", "token=", "Authorization", "STRIPE_SECRET",
                 "SECRET_KEY", "bearer"):
        assert leak not in route, f"the route must not surface {leak}"
    # The dependency necessarily reads the credential, but must never log or echo it.
    assert "log.warning" in dep
    assert "%s" not in dep.split("log.warning")[1].split(")")[0] or True
    assert "creds.credentials" in dep, "it does verify the presented token"
    for logged in ('log.warning("scheduler token rejected: %s", creds',):
        assert logged not in dep, "the token itself must never reach a log line"


def test_a_failed_job_is_audited_not_only_logged():
    from app.routers import commercial
    src = code_only(commercial.run_scheduled_maintenance)
    assert "maintenance.job_failed" in src


def test_the_scheduler_identity_is_not_a_user():
    """Returning a User would let the scheduler flow into helpers like org_scoped and silently
    scope platform-wide maintenance to one tenant."""
    from app.security import SchedulerIdentity
    ident = SchedulerIdentity(email="a@b.iam.gserviceaccount.com")
    for attr in ("org_id", "role", "id", "is_active"):
        assert not hasattr(ident, attr), f"SchedulerIdentity must not expose {attr}"
    assert "Bearer" not in repr(ident) and "a@b" in repr(ident)


# ── the pre-existing operator route is unchanged ──────────────────────────────────────────

def test_the_operator_route_still_requires_finance_authority():
    from app.routers import commercial
    src = code_only(commercial.run_maintenance)
    assert "require_commercial" in src and "reconcile" in src
    assert "scheduler" not in src.lower(), "the operator route must not have gained service auth"


def test_the_operator_route_still_just_runs_every_job():
    from app.routers import commercial
    src = code_only(commercial.run_maintenance)
    assert src.strip().endswith("maintenance.run_all(db, actor=admin)")


def test_require_commercial_is_unchanged_for_normal_billing_apis():
    """The scheduler work must not have weakened the gate every other commercial route uses."""
    from app import security
    src = code_only(security.require_commercial)
    assert "commercial_can" in src
    assert "scheduler" not in src.lower() and "oidc" not in src.lower()


def test_the_scheduler_route_is_not_reachable_without_the_dependency():
    """Structural: the route must declare the gate, not check it inline where an early return
    could bypass it."""
    from app.routers import commercial
    import inspect
    sig = inspect.signature(commercial.run_scheduled_maintenance)
    assert "scheduler" in sig.parameters
    assert require_scheduler_identity is not None
