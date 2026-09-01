"""ORG-009 tenant-data authorization boundary (ZST-EC-001).

These tests exist because the previous audit found the approval workflow was bookkeeping:
31 admin routes trusted `require_super_admin` alone, so a platform account could read and
change any tenant's data without the customer ever approving or being told. The assertions
below are about AUTHORIZATION, not email — an email family is only as true as the control
behind it.

Run with `python test_tenant_access.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.db import SessionLocal
from app.models import (
    SUPPORT_ACTIVE,
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
from app.services import support_access as support_svc
from app.services import tenant_access

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"


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


def _new_email(tag="ta"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class World:
    """A customer organization, plus two independent platform-staff accounts."""

    def __init__(self):
        self.emails = []
        db = SessionLocal()
        try:
            cust = Organization(name=f"Cust {uuid.uuid4().hex[:6]}", status="active",
                                timezone="Asia/Kolkata")
            staff = Organization(name=f"Staff {uuid.uuid4().hex[:6]}", status="active")
            db.add_all([cust, staff])
            db.flush()
            self.org_id, self.org_name = cust.id, cust.name
            self.staff_org_id = staff.id

            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, cust.id, "org_admin", self.owner_email, "Owner")
            cust.owner_user_id = self.owner_id

            self.member_email = _new_email("member")
            self.member_id = self._u(db, cust.id, "viewer", self.member_email, "Member")

            self.eng_email = _new_email("eng")
            self.eng_id = self._u(db, staff.id, "super_admin", self.eng_email, "Engineer One")
            self.eng2_email = _new_email("eng2")
            self.eng2_id = self._u(db, staff.id, "super_admin", self.eng2_email, "Engineer Two")
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

    def token(self, email):
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": email, "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def request_access(self, client, headers, *, caps=None, minutes=60,
                       emergency=False, scope="Billing records"):
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/admin/support-access", headers=headers, json={
                "org_id": str(self.org_id), "case_reference": f"CASE-{uuid.uuid4().hex[:6]}",
                "reason_category": "customer_reported_issue",
                "engineer_display": "Engineer One",
                "requested_scope": scope,
                "allowed_actions": caps or [tenant_access.CAP_TENANT_READ],
                "minutes": minutes, "emergency": emergency,
                "emergency_reason": "Production outage" if emergency else None,
            })
        assert r.status_code == 201, r.text
        return r.json()["id"], cap

    def approve(self, client, req_id):
        headers = self.token(self.owner_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post(f"/api/organization/support-access/{req_id}/decision",
                            headers=headers, json={"approve": True})
        assert r.status_code == 200, r.text
        return r.json()

    def start(self, client, headers, req_id):
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post(f"/api/admin/support-access/{req_id}/start", headers=headers)
        return r, cap

    def audit_for(self, req_id):
        db = SessionLocal()
        try:
            return [r for r in db.query(AuditLog).filter(AuditLog.org_id == self.org_id).all()
                    if (r.meta or {}).get("support_request_id") == str(req_id)]
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
                    # ElevationSession FK-references users and start() creates one per
                    # support session, so teardown has to clear it too.
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


# ══ the boundary ════════════════════════════════════════════════════════════════

def test_1_super_admin_alone_cannot_read_tenant_data():
    """1 — the SEV-1. Role alone must no longer reach a customer's records."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        _reset_limits()
        r = client.get(f"/api/admin/organizations/{w.org_id}", headers=headers)
        assert r.status_code == 403, \
            f"super_admin alone must not read tenant data; got {r.status_code}"
        assert "has not approved" in r.text

        _reset_limits()
        r2 = client.get(f"/api/admin/organizations/{w.org_id}/api-keys", headers=headers)
        assert r2.status_code == 403, "tenant credentials must be gated"

        _reset_limits()
        r3 = client.patch(f"/api/admin/users/{w.member_id}",
                          json={"full_name": "Renamed By Staff"}, headers=headers)
        assert r3.status_code == 403, "modifying a tenant's member must be gated"

        db = SessionLocal()
        try:
            assert db.get(User, w.member_id).full_name == "Member", "and nothing changed"
        finally:
            db.close()
    finally:
        w.cleanup()


def test_2_approved_active_session_allows_only_approved_scope():
    """2 — an approved session grants exactly what was approved, and no more."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        req_id, _ = w.request_access(client, headers,
                                     caps=[tenant_access.CAP_TENANT_READ])
        w.approve(client, req_id)
        r, _ = w.start(client, headers, req_id)
        assert r.status_code == 200, r.text

        # In scope.
        _reset_limits()
        ok = client.get(f"/api/admin/organizations/{w.org_id}", headers=headers)
        assert ok.status_code == 200, f"approved read must succeed: {ok.text}"

        # Out of scope — credentials were never approved.
        _reset_limits()
        denied = client.get(f"/api/admin/organizations/{w.org_id}/api-keys", headers=headers)
        assert denied.status_code == 403, "unapproved capability must be refused"
        assert "does not permit" in denied.text

        # Out of scope — member writes were never approved.
        _reset_limits()
        denied2 = client.patch(f"/api/admin/users/{w.member_id}",
                               json={"full_name": "Nope"}, headers=headers)
        assert denied2.status_code == 403
    finally:
        w.cleanup()


def test_3_expired_session_denies_access():
    """3 — expiry actually withdraws access."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        req_id, _ = w.request_access(client, headers)
        w.approve(client, req_id)
        r, _ = w.start(client, headers, req_id)
        assert r.status_code == 200

        _reset_limits()
        assert client.get(f"/api/admin/organizations/{w.org_id}",
                          headers=headers).status_code == 200

        db = SessionLocal()
        try:
            row = db.get(SupportAccessRequest, uuid.UUID(req_id))
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()

        _reset_limits()
        after = client.get(f"/api/admin/organizations/{w.org_id}", headers=headers)
        assert after.status_code == 403, "an expired session must deny access"
    finally:
        w.cleanup()


def test_4_wrong_engineer_denied():
    """4 — a colleague cannot ride someone else's approved session."""
    w = World()
    client = TestClient(m.app)
    try:
        h1 = w.token(w.eng_email)
        req_id, _ = w.request_access(client, h1)
        w.approve(client, req_id)
        r, _ = w.start(client, h1, req_id)
        assert r.status_code == 200

        h2 = w.token(w.eng2_email)
        _reset_limits()
        other = client.get(f"/api/admin/organizations/{w.org_id}", headers=h2)
        assert other.status_code == 403, \
            "the approval named one engineer; another must not inherit it"
    finally:
        w.cleanup()


def test_5_unapproved_request_denies_access():
    """5 — requesting is not receiving."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        req_id, _ = w.request_access(client, headers)
        # No approval.
        r, _ = w.start(client, headers, req_id)
        assert r.status_code == 403, "start must refuse without customer approval"
        _reset_limits()
        assert client.get(f"/api/admin/organizations/{w.org_id}",
                          headers=headers).status_code == 403
    finally:
        w.cleanup()


def test_6_changed_scope_invalidates_prior_approval():
    """6 — widening the terms after approval drops the approval."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        req_id, _ = w.request_access(client, headers,
                                     caps=[tenant_access.CAP_TENANT_READ])
        w.approve(client, req_id)

        _reset_limits()
        amend = client.patch(f"/api/admin/support-access/{req_id}",
                             json={"requested_scope": "Everything", "minutes": 480},
                             headers=headers)
        assert amend.status_code == 200, amend.text

        r, _ = w.start(client, headers, req_id)
        assert r.status_code in (403, 409), \
            f"an amended request must not start on the old approval; got {r.status_code}"
        _reset_limits()
        assert client.get(f"/api/admin/organizations/{w.org_id}",
                          headers=headers).status_code == 403
    finally:
        w.cleanup()


def test_7_legacy_elevation_cannot_bypass_the_gate():
    """7 — the weaker path must not exist alongside the strong one."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        _reset_limits()
        elev = client.post("/api/admin/elevation", headers=headers,
                           json={"scope": "Platform Operations", "minutes": 60,
                                 "reason": "poking around"})
        assert elev.status_code in (200, 201), elev.text

        # Elevation grants platform scope only — it must not open tenant data.
        _reset_limits()
        r = client.get(f"/api/admin/organizations/{w.org_id}", headers=headers)
        assert r.status_code == 403, \
            "legacy elevation must not grant tenant-data access"
        _reset_limits()
        r2 = client.get(f"/api/admin/organizations/{w.org_id}/api-keys", headers=headers)
        assert r2.status_code == 403
    finally:
        w.cleanup()


def test_8_emergency_requires_independent_second_authorizer():
    """8 — break-glass is not self-service."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        req_id, _ = w.request_access(client, headers, emergency=True)

        # No countersignature yet.
        r, _ = w.start(client, headers, req_id)
        assert r.status_code == 403, "emergency must not start without a second authorizer"
        assert "second authorizer" in r.text.lower()

        # The requester cannot countersign themselves.
        _reset_limits()
        self_sign = client.post(f"/api/admin/support-access/{req_id}/countersign",
                                headers=headers)
        assert self_sign.status_code == 403, "self-countersigning must be refused"

        # An independent operator can.
        h2 = w.token(w.eng2_email)
        _reset_limits()
        signed = client.post(f"/api/admin/support-access/{req_id}/countersign", headers=h2)
        assert signed.status_code == 200, signed.text

        r2, cap = w.start(client, headers, req_id)
        assert r2.status_code == 200, r2.text
        # 22 — the emergency notice goes out, naming the real second authorizer.
        payload = cap.of(email_mod.ORG_009_EMERGENCY_SUBJECT)
        assert w.eng2_email.lower() in payload["text"].lower(), \
            "the emergency notice must name the independent authorizer"
        assert "No second authorizer was recorded" not in payload["text"]
    finally:
        w.cleanup()


def test_9_every_privileged_action_creates_attribution():
    """9 — the ORG-009 email's audit claim must be backed by real rows."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        req_id, _ = w.request_access(
            client, headers,
            caps=[tenant_access.CAP_TENANT_READ, tenant_access.CAP_CREDENTIALS_READ])
        w.approve(client, req_id)
        r, _ = w.start(client, headers, req_id)
        assert r.status_code == 200

        _reset_limits()
        assert client.get(f"/api/admin/organizations/{w.org_id}",
                          headers=headers).status_code == 200
        _reset_limits()
        assert client.get(f"/api/admin/organizations/{w.org_id}/api-keys",
                          headers=headers).status_code == 200

        rows = w.audit_for(req_id)
        actions = {r.action for r in rows}
        assert f"support_access.{tenant_access.CAP_TENANT_READ}" in actions, \
            f"read not attributed; got {actions}"
        assert f"support_access.{tenant_access.CAP_CREDENTIALS_READ}" in actions, \
            f"credential read not attributed; got {actions}"
        for row in rows:
            meta = row.meta or {}
            assert meta["support_request_id"] == str(req_id)
            assert meta["engineer"], "engineer named"
            assert row.org_id == w.org_id, "attributed to the right tenant"
            assert row.actor_id == w.eng_id, "attributed to the acting engineer"

        # An out-of-scope attempt is recorded too — a refused reach is worth knowing about.
        _reset_limits()
        client.patch(f"/api/admin/users/{w.member_id}", json={"full_name": "X"},
                     headers=headers)
        denied = [r for r in w.audit_for(req_id)
                  if r.action == "support_access.denied_out_of_scope"]
        assert denied, "an out-of-scope attempt must be recorded"
    finally:
        w.cleanup()


def test_10_org_009_email_claims_match_behaviour():
    """10 — the audit-terms sentence is now true, and 11 — no Super Admin links."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        req_id, cap = w.request_access(client, headers)
        payload = cap.of(email_mod.ORG_009_SUBJECT)
        text, html = payload["text"], payload["html"]

        assert ("Every action taken during an approved session is attributed to the named "
                "engineer") in text, "the audit claim must still be present"
        # 11 — customer CTAs never point at Super Admin.
        for surface in (text, html):
            assert "/admin" not in surface, "no customer email may link into Super Admin"
            assert "/organization/support" in surface, "CTA is a tenant route"
        # 26 — no secrets.
        low = text.lower()
        for banned in ("password", "token", "api_key", "bearer", "secret", "jwt"):
            assert banned not in low, f"{banned!r} must not appear"
    finally:
        w.cleanup()


def test_12_resend_failure_does_not_grant_access():
    """12 — a provider outage must not open the gate."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        req_id, _ = w.request_access(client, headers)
        # Approval mail fails; the approval itself must still stand, and access must still
        # require the start step rather than being implied.
        _reset_limits()
        owner_headers = w.token(w.owner_email)
        cap, ctx = _capture(fail=True)
        with ctx:
            d = client.post(f"/api/organization/support-access/{req_id}/decision",
                            headers=owner_headers, json={"approve": True})
        assert d.status_code == 200, "a mail outage must not fail the decision"

        _reset_limits()
        assert client.get(f"/api/admin/organizations/{w.org_id}",
                          headers=headers).status_code == 403, \
            "approved but not started must still deny"
    finally:
        w.cleanup()


def test_capability_vocabulary_is_enforced_at_request_time():
    """Free text could never be checked at the boundary, so it is refused up front."""
    w = World()
    client = TestClient(m.app)
    try:
        headers = w.token(w.eng_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/admin/support-access", headers=headers, json={
                "org_id": str(w.org_id), "case_reference": "CASE-1",
                "reason_category": "customer_reported_issue",
                "engineer_display": "Engineer One",
                "requested_scope": "everything",
                "allowed_actions": ["look at their stuff"], "minutes": 60,
            })
        assert r.status_code == 422, "unenforceable free-text capabilities must be refused"
        assert cap.calls == [], "and nothing may be sent for a rejected request"
    finally:
        w.cleanup()


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
