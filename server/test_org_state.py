"""ORG-010 organization operational-state enforcement (ZST-EC-001).

The previous audit marked ORG-010 INCORRECT because the email said access was restricted
while nothing in authorization checked organization state. These tests assert the control,
not the copy — and then assert that the copy matches the control.

Run with `python test_org_state.py` (or pytest).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.db import SessionLocal
from app.models import (
    ORG_STATE_ACTIVE,
    ORG_STATE_RESTRICTED,
    ORG_STATE_SUSPENDED,
    AccountRecovery,
    AuditLog,
    ElevationSession,
    IdentityChallenge,
    Organization,
    OrgMembershipEvent,
    OrgOperationalEvent,
    SignInEvent,
    SupportAccessRequest,
    User,
)
from app.security import hash_password
from app.services import org_state

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
ORG_010_REACTIVATED = email_mod.ORG_010_REACTIVATED_SUBJECT


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


def _new_email(tag="ost"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class Tenant:
    def __init__(self):
        self.emails = []
        db = SessionLocal()
        try:
            org = Organization(name=f"State Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            staff = Organization(name=f"Staff {uuid.uuid4().hex[:6]}", status="active")
            db.add_all([org, staff])
            db.flush()
            self.org_id, self.org_name = org.id, org.name
            self.staff_org_id = staff.id
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, org.id, "org_admin", self.owner_email, "Owner")
            org.owner_user_id = self.owner_id
            self.staff_email = _new_email("staff")
            self.staff_id = self._u(db, staff.id, "super_admin", self.staff_email, "Staff")
            db.commit()
        finally:
            db.close()

    def _u(self, db, org_id, role, email, name):
        user = User(org_id=org_id, full_name=name, role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=datetime.now(timezone.utc))
        db.add(user)
        db.flush()
        self.emails.append(email)
        return user.id

    def token(self, email=None):
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": email or self.owner_email,
                                  "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}, r

    def set_state(self, state):
        db = SessionLocal()
        try:
            db.get(Organization, self.org_id).status = state
            db.commit()
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            for oid in (self.org_id, self.staff_org_id):
                db.query(AuditLog).filter(AuditLog.org_id == oid).delete()
                db.query(SupportAccessRequest).filter(
                    SupportAccessRequest.org_id == oid).delete()
                db.query(OrgMembershipEvent).filter(OrgMembershipEvent.org_id == oid).delete()
                db.query(OrgOperationalEvent).filter(
                    OrgOperationalEvent.org_id == oid).delete()
            db.commit()
            for oid in (self.org_id, self.staff_org_id):
                org = db.get(Organization, oid)
                if org is not None:
                    org.owner_user_id = None
            db.commit()
            for oid in (self.org_id, self.staff_org_id):
                for user in db.query(User).filter(User.org_id == oid).all():
                    for model in (SignInEvent, IdentityChallenge, AccountRecovery,
                                  ElevationSession):
                        db.query(model).filter(model.user_id == user.id).delete()
                    db.delete(user)
            db.commit()
            for oid in (self.org_id, self.staff_org_id):
                org = db.get(Organization, oid)
                if org is not None:
                    db.delete(org)
            db.commit()
        finally:
            db.close()


# ══ enforcement ═════════════════════════════════════════════════════════════════

def test_1_active_org_allows_normal_access():
    t = Tenant()
    client = TestClient(m.app)
    try:
        headers, _ = t.token()
        _reset_limits()
        r = client.get("/api/organization/overview", headers=headers)
        assert r.status_code == 200, f"an active org must work normally: {r.text}"
    finally:
        t.cleanup()


def test_2_suspended_org_blocks_operational_routes():
    """2 — the claim that audited INCORRECT is now enforced."""
    t = Tenant()
    client = TestClient(m.app)
    try:
        headers, _ = t.token()
        t.set_state(ORG_STATE_SUSPENDED)
        _reset_limits()
        r = client.get("/api/organization/overview", headers=headers)
        assert r.status_code == 403, \
            f"a suspended org must lose operational access; got {r.status_code}"
        body = r.json()["detail"]
        assert body["code"] == "ORGANIZATION_RESTRICTED"
        assert body["state"] == "suspended"
    finally:
        t.cleanup()


def test_3_restricted_org_obeys_the_allowlist():
    """3 — restricted refuses operational routes but honours preserved surfaces."""
    t = Tenant()
    client = TestClient(m.app)
    try:
        headers, _ = t.token()
        t.set_state(ORG_STATE_RESTRICTED)

        _reset_limits()
        blocked = client.get("/api/organization/overview", headers=headers)
        assert blocked.status_code == 403, "operational route must be refused"

        # 5 — preserved surfaces still behave.
        for path in ("/api/organization/profile", "/api/organization/security",
                     "/api/organization/notifications", "/api/organization/support-access"):
            _reset_limits()
            allowed = client.get(path, headers=headers)
            assert allowed.status_code != 403, \
                f"{path} must stay reachable under restriction; got {allowed.status_code}"
    finally:
        t.cleanup()


def test_4_identity_is_not_globally_disabled():
    """4 — the restriction is organization-scoped, not an identity restriction."""
    t = Tenant()
    client = TestClient(m.app)
    try:
        t.set_state(ORG_STATE_SUSPENDED)
        # Sign-in still succeeds: IDN-008 owns identity restriction, not ORG-010.
        headers, login = t.token()
        assert login.status_code == 200, \
            "suspending an Organization must not disable the person's Zoiko identity"
        _reset_limits()
        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200, "identity routes stay available"
        db = SessionLocal()
        try:
            assert db.get(User, t.owner_id).is_active is True, \
                "the user row itself is untouched"
        finally:
            db.close()
    finally:
        t.cleanup()


def test_6_email_capability_claims_match_authorization():
    """6 — the sentence the customer receives is generated from the enforcing module."""
    from app.services import org_governance as gov

    for state in (ORG_STATE_RESTRICTED, ORG_STATE_SUSPENDED):
        claim = gov._capabilities(state)
        assert claim == org_state.capability_summary(state), \
            "ORG-010 copy must be sourced from the enforcement, not written separately"
        assert "refused" in claim, "the claim now describes a real refusal"
        assert "sign-in" not in claim.lower(), \
            "the old false sign-in claim must be gone — sign-in is not blocked"
        # Preserved surfaces named in the email are the ones the allowlist really keeps open.
        preserved = gov._preserved(state)
        for surface in ("Billing", "export", "privacy", "support"):
            assert surface.lower() in preserved.lower()
    for path in ("/api/organization/billing", "/api/organization/support",
                 "/api/organization/security"):
        assert org_state.is_preserved_path(path), f"{path} must be preserved"
    assert not org_state.is_preserved_path("/api/organization/overview"), \
        "an operational route must not be on the allowlist"


def test_7_reactivation_restores_access_and_notifies():
    """7 — returning to ACTIVE genuinely restores access and sends the approved variant."""
    t = Tenant()
    client = TestClient(m.app)
    try:
        headers, _ = t.token()
        t.set_state(ORG_STATE_SUSPENDED)
        _reset_limits()
        assert client.get("/api/organization/overview",
                          headers=headers).status_code == 403

        staff_headers, _ = t.token(t.staff_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/admin/organizations/{t.org_id}",
                             json={"status": "active"}, headers=staff_headers)
        assert r.status_code == 200, r.text

        _reset_limits()
        restored = client.get("/api/organization/overview", headers=headers)
        assert restored.status_code == 200, "reactivation must restore operational access"

        payload = cap.of(ORG_010_REACTIVATED)
        assert payload["html"] and payload["text"]
        assert "No restrictions apply" in payload["text"]
    finally:
        t.cleanup()


def test_platform_enforcement_is_not_customer_approved():
    """Suspending a tenant must NOT require that tenant's approval — otherwise the control
    is useless for non-payment or abuse. It stays super-admin-only and audited."""
    t = Tenant()
    client = TestClient(m.app)
    try:
        staff_headers, _ = t.token(t.staff_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/admin/organizations/{t.org_id}",
                             json={"status": "suspended"}, headers=staff_headers)
        assert r.status_code == 200, \
            f"platform enforcement must not need customer approval: {r.text}"
        db = SessionLocal()
        try:
            assert db.get(Organization, t.org_id).status == "suspended"
        finally:
            db.close()
    finally:
        t.cleanup()


def test_outage_does_not_revert_state():
    t = Tenant()
    client = TestClient(m.app)
    try:
        staff_headers, _ = t.token(t.staff_email)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.patch(f"/api/admin/organizations/{t.org_id}",
                             json={"status": "suspended"}, headers=staff_headers)
        assert r.status_code == 200, "a mail outage must not fail the state change"
        assert cap.calls, "a send was attempted"
        db = SessionLocal()
        try:
            assert db.get(Organization, t.org_id).status == "suspended", \
                "state stays committed"
        finally:
            db.close()
    finally:
        t.cleanup()


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
