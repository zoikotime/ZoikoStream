"""Step-up authentication and the high-risk operations it gates (ZST-EC-001 ORG-003/008).

Both families audited PARTIAL because the canonical control list requires step-up and this
codebase had none. These tests assert the mechanism itself, then assert that the two
high-risk operations genuinely refuse without it.

Run with `python test_stepup.py` (or pytest).
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
    STEP_UP_HIGH_RISK_ROLE_GRANT,
    STEP_UP_OWNERSHIP_TRANSFER,
    AccountRecovery,
    AuditLog,
    ElevationSession,
    IdentityChallenge,
    Organization,
    OrgMembershipEvent,
    OrgOperationalEvent,
    OwnershipTransfer,
    SignInEvent,
    StepUpGrant,
    SupportAccessRequest,
    User,
)
from app.security import hash_password
from app.services import org_comms
from app.services import stepup as stepup_svc

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


def _new_email(tag="su"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class Org:
    def __init__(self):
        self.emails = []
        db = SessionLocal()
        try:
            org = Organization(name=f"SU Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            db.add(org)
            db.flush()
            self.org_id, self.org_name = org.id, org.name
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "org_admin", self.owner_email, "Owner")
            org.owner_user_id = self.owner_id
            self.member_email = _new_email("member")
            self.member_id = self._u(db, "viewer", self.member_email, "Member")
            db.commit()
        finally:
            db.close()

    def _u(self, db, role, email, name):
        user = User(org_id=self.org_id, full_name=name, role=role, is_active=True,
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
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def step_up(self, client, headers, purpose, password=PASSWORD):
        _reset_limits()
        return client.post("/api/auth/step-up", headers=headers,
                           json={"password": password, "purpose": purpose})

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(AuditLog).filter(AuditLog.org_id == self.org_id).delete()
            db.query(OwnershipTransfer).filter(
                OwnershipTransfer.org_id == self.org_id).delete()
            db.query(SupportAccessRequest).filter(
                SupportAccessRequest.org_id == self.org_id).delete()
            db.query(OrgMembershipEvent).filter(
                OrgMembershipEvent.org_id == self.org_id).delete()
            db.query(OrgOperationalEvent).filter(
                OrgOperationalEvent.org_id == self.org_id).delete()
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


# ══ the mechanism ═══════════════════════════════════════════════════════════════

def test_1_jwt_alone_is_insufficient_for_a_high_risk_action():
    """1 — a valid session must not authorize an administrative grant."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        r = client.patch(f"/api/organization/users/{o.member_id}",
                         json={"role": "org_admin"}, headers=headers)
        assert r.status_code == 403, \
            f"a session alone must not grant admin rights; got {r.status_code}"
        assert r.json()["detail"]["code"] == "STEP_UP_REQUIRED"
        db = SessionLocal()
        try:
            assert db.get(User, o.member_id).role == "viewer", "and nothing changed"
        finally:
            db.close()
    finally:
        o.cleanup()


def test_2_reauth_issues_a_short_lived_grant():
    """2 — a successful password re-check mints a purpose-bound grant."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        r = o.step_up(client, headers, STEP_UP_HIGH_RISK_ROLE_GRANT)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["purpose"] == STEP_UP_HIGH_RISK_ROLE_GRANT
        assert body["expires_in_minutes"] <= 15, "step-up must be short-lived"
        assert len(body["reference"]) >= 20

        # The raw reference is never stored.
        db = SessionLocal()
        try:
            rows = db.query(StepUpGrant).filter(StepUpGrant.user_id == o.owner_id).all()
            assert rows and all(r2.token_hash != body["reference"] for r2 in rows), \
                "the reference must be stored only as a hash"
        finally:
            db.close()

        # A wrong password mints nothing.
        bad = o.step_up(client, headers, STEP_UP_HIGH_RISK_ROLE_GRANT, password="wrong-pass")
        assert bad.status_code == 401, "step-up must actually verify the credential"
    finally:
        o.cleanup()


def test_3_expired_step_up_is_rejected():
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        ref = o.step_up(client, headers, STEP_UP_HIGH_RISK_ROLE_GRANT).json()["reference"]
        db = SessionLocal()
        try:
            g = db.query(StepUpGrant).filter(StepUpGrant.user_id == o.owner_id,
                                             StepUpGrant.consumed_at.is_(None)).one()
            g.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        finally:
            db.close()
        _reset_limits()
        r = client.patch(f"/api/organization/users/{o.member_id}",
                         json={"role": "org_admin"},
                         headers={**headers, "X-Step-Up": ref})
        assert r.status_code == 403, "an expired step-up must be refused"
    finally:
        o.cleanup()


def test_4_wrong_purpose_is_rejected():
    """4 — a grant minted for a transfer must not authorize a role change."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        ref = o.step_up(client, headers, STEP_UP_OWNERSHIP_TRANSFER).json()["reference"]
        _reset_limits()
        r = client.patch(f"/api/organization/users/{o.member_id}",
                         json={"role": "org_admin"},
                         headers={**headers, "X-Step-Up": ref})
        assert r.status_code == 403, "purpose binding must hold"
        assert "different action" in r.json()["detail"]["message"]
    finally:
        o.cleanup()


def test_5_grant_is_single_use():
    """5 — reuse is unsafe, so a spent grant is dead."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        ref = o.step_up(client, headers, STEP_UP_HIGH_RISK_ROLE_GRANT).json()["reference"]

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            first = client.patch(f"/api/organization/users/{o.member_id}",
                                 json={"role": "org_admin"},
                                 headers={**headers, "X-Step-Up": ref})
        assert first.status_code == 200, first.text

        # host is not administrative, so this one is not high-risk and needs no step-up.
        # Wrapped because it legitimately fires ORG-003.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            again = client.patch(f"/api/organization/users/{o.member_id}",
                                 json={"role": "host"},
                                 headers={**headers, "X-Step-Up": ref})
        assert again.status_code == 200, again.text

        # But a second ADMINISTRATIVE grant on the spent reference must fail.
        _reset_limits()
        third = client.patch(f"/api/organization/users/{o.member_id}",
                             json={"role": "org_admin"},
                             headers={**headers, "X-Step-Up": ref})
        assert third.status_code == 403, "a spent step-up must not authorize again"
        assert "already been used" in third.json()["detail"]["message"]
    finally:
        o.cleanup()


# ══ ORG-003 ═════════════════════════════════════════════════════════════════════

def test_org003_high_risk_grant_succeeds_with_step_up_and_notifies():
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        ref = o.step_up(client, headers, STEP_UP_HIGH_RISK_ROLE_GRANT).json()["reference"]
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/organization/users/{o.member_id}",
                             json={"role": "org_admin"},
                             headers={**headers, "X-Step-Up": ref})
        assert r.status_code == 200, r.text
        assert email_mod.ORG_003_SUBJECT in cap.subjects, "ORG-003 still fires after commit"
        db = SessionLocal()
        try:
            assert db.get(User, o.member_id).role == "org_admin"
        finally:
            db.close()
    finally:
        o.cleanup()


def test_org003_low_risk_change_does_not_require_step_up():
    """A demotion or lateral move must not train people to type their password blindly."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/organization/users/{o.member_id}",
                             json={"role": "host"}, headers=headers)
        assert r.status_code == 200, f"low-risk change must not need step-up: {r.text}"
        assert email_mod.ORG_003_SUBJECT in cap.subjects
    finally:
        o.cleanup()


def test_high_risk_definition_uses_the_real_role_model():
    assert org_comms.is_high_risk_grant("viewer", "org_admin") is True
    assert org_comms.is_high_risk_grant("host", "org_admin") is True
    assert org_comms.is_high_risk_grant("org_admin", "viewer") is False, \
        "a demotion is not a high-risk GRANT"
    assert org_comms.is_high_risk_grant("viewer", "host") is False
    assert org_comms.is_high_risk_grant("org_admin", "org_admin") is False
    from app.models.user import ROLES
    for role in org_comms.ADMINISTRATIVE_ROLES:
        assert role in ROLES, f"{role} must exist in the real role model"


# ══ ORG-008 ═════════════════════════════════════════════════════════════════════

def test_org008_confirmation_requires_step_up_and_ownership_does_not_move_without_it():
    o = Org()
    client = TestClient(m.app)
    try:
        owner_headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            init = client.post("/api/organization/ownership-transfer",
                               json={"proposed_owner_email": o.member_email},
                               headers=owner_headers)
        assert init.status_code in (200, 201), init.text
        transfer_id = init.json()["id"]

        db = SessionLocal()
        try:
            assert db.get(Organization, o.org_id).owner_user_id == o.owner_id, \
                "initiating must not move ownership"
        finally:
            db.close()

        # Confirmation without step-up is refused.
        _reset_limits()
        no_su = client.post(f"/api/organization/ownership-transfer/{transfer_id}/confirm",
                            headers=owner_headers)
        assert no_su.status_code == 403, "confirmation must require step-up"
        assert no_su.json()["detail"]["code"] == "STEP_UP_REQUIRED"

        # With step-up, the first confirmation records but does not complete.
        ref = o.step_up(client, owner_headers,
                        STEP_UP_OWNERSHIP_TRANSFER).json()["reference"]
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            first = client.post(
                f"/api/organization/ownership-transfer/{transfer_id}/confirm",
                headers={**owner_headers, "X-Step-Up": ref})
        assert first.status_code == 200, first.text
        db = SessionLocal()
        try:
            assert db.get(Organization, o.org_id).owner_user_id == o.owner_id, \
                "one confirmation is not enough"
        finally:
            db.close()

        # Second party confirms, with their own step-up.
        member_headers = o.token(o.member_email)
        ref2 = o.step_up(client, member_headers,
                         STEP_UP_OWNERSHIP_TRANSFER).json()["reference"]
        _reset_limits()
        cap3, ctx3 = _capture()
        with ctx3:
            second = client.post(
                f"/api/organization/ownership-transfer/{transfer_id}/confirm",
                headers={**member_headers, "X-Step-Up": ref2})
        assert second.status_code == 200, second.text
        db = SessionLocal()
        try:
            assert db.get(Organization, o.org_id).owner_user_id == o.member_id, \
                "ownership moves only after both confirmations"
        finally:
            db.close()
        assert email_mod.ORG_008_COMPLETED_SUBJECT in cap3.subjects
    finally:
        o.cleanup()


def test_org008_copy_matches_the_enforcement():
    from app.services import org_governance as gov
    note = gov.STEP_UP_NOTE
    assert "re-enter their password" in note, "the copy must describe the real control"
    assert "not currently required" not in note, "the old PARTIAL wording must be gone"
    assert "carries no confirmation link" in note


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
