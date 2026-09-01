"""ORG-006 .. ORG-010 — tenant administration and governance (ZST-EC-001).

Harness matches the earlier suites: Resend is intercepted at `httpx.post`, so every message
is proven to travel the real integration, and a `_deny` baseline fails any test whose send
escapes its capture instead of reaching the provider.

ORG-006's tests assert that NOTHING is sent. There is no SCIM subsystem — no /scim/v2, no
provisioning job, no sync state, no token lifecycle — so the only correct ORG-006 behaviour
is silence, and these tests are what stop a later change from shipping a provisioning claim
off an unrelated settings flag.

Run with `python test_org_governance.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.config import settings
from app.db import SessionLocal
from app.models import (
    AccessReviewEscalation,
    StepUpGrant,
    ORG_STATE_DELETED,
    SUPPORT_ACTIVE,
    SUPPORT_APPROVED,
    SUPPORT_DENIED,
    SUPPORT_ENDED,
    SUPPORT_EXPIRED,
    SUPPORT_MAX_MINUTES,
    SUPPORT_REQUESTED,
    AccessReview,
    AccessReviewAssignment,
    AccountRecovery,
    AccountStateEvent,
    AuditLog,
    ElevationSession,
    IdentityChallenge,
    Invitation,
    Organization,
    OrgMembershipEvent,
    OrgOperationalEvent,
    OwnershipTransfer,
    SignInEvent,
    SupportAccessRequest,
    User,
)
from app.security import hash_password
from app.services import org_comms
from app.services import org_governance as governance
from app.services import support_access as support_svc

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

ORG_007 = email_mod.ORG_007_SUBJECT
ORG_007_REMINDER = email_mod.ORG_007_REMINDER_SUBJECT
ORG_007_OVERDUE = email_mod.ORG_007_OVERDUE_SUBJECT
ORG_007_DONE = email_mod.ORG_007_COMPLETED_SUBJECT
ORG_008 = email_mod.ORG_008_SUBJECT
ORG_008_DONE = email_mod.ORG_008_COMPLETED_SUBJECT
ORG_008_EXPIRED = email_mod.ORG_008_EXPIRED_SUBJECT
ORG_008_CANCELED = email_mod.ORG_008_CANCELED_SUBJECT
ORG_009 = email_mod.ORG_009_SUBJECT
ORG_009_STARTED = email_mod.ORG_009_STARTED_SUBJECT
ORG_009_EXPIRING = email_mod.ORG_009_EXPIRING_SUBJECT
ORG_009_ENDED = email_mod.ORG_009_ENDED_SUBJECT
ORG_009_EMERGENCY = email_mod.ORG_009_EMERGENCY_SUBJECT
ORG_010 = email_mod.ORG_010_SUBJECT
ORG_010_REACTIVATED = email_mod.ORG_010_REACTIVATED_SUBJECT


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

    def to(self, subject):
        return sorted(c["payload"]["to"][0]
                      for c in self.calls if c["payload"]["subject"] == subject)


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


def _new_email(tag="orgg"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class Fixture:
    """One tenant (owner + admin + member) plus a platform super admin."""

    def __init__(self, tz="Asia/Kolkata"):
        self.emails: list[str] = []
        db = SessionLocal()
        try:
            org = Organization(name=f"Gov Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone=tz)
            db.add(org)
            db.flush()
            self.org_id, self.org_name = org.id, org.name

            self.owner_email = _new_email("owner")
            self.owner_id = self._add(db, "org_admin", self.owner_email, "Owner Person")
            self.admin_email = _new_email("admin")
            self.admin_id = self._add(db, "org_admin", self.admin_email, "Admin Person")
            self.member_email = _new_email("member")
            self.member_id = self._add(db, "viewer", self.member_email, "Member Person")
            org.owner_user_id = self.owner_id

            # Platform staff live in their own organization.
            staff_org = Organization(name=f"Zoiko Staff {uuid.uuid4().hex[:6]}",
                                     status="active")
            db.add(staff_org)
            db.flush()
            self.staff_org_id = staff_org.id
            self.staff_email = _new_email("staff")
            staff = User(org_id=staff_org.id, full_name="Platform Engineer",
                         role="super_admin", is_active=True, email=self.staff_email.lower(),
                         username=f"u{uuid.uuid4().hex[:10]}",
                         password_hash=hash_password(PASSWORD), email_verified=True,
                         email_verified_at=datetime.now(timezone.utc))
            db.add(staff)
            db.flush()
            self.staff_id = staff.id
            self.emails.append(self.staff_email)

            # A SECOND, independent platform operator. ZST-EC-001 ORG-009 emergency access
            # now requires a countersignature from someone other than the requesting
            # engineer, so the fixture has to be able to supply that other person.
            self.staff2_email = _new_email("staff2")
            staff2 = User(org_id=staff_org.id, full_name="Second Platform Engineer",
                          role="super_admin", is_active=True,
                          email=self.staff2_email.lower(),
                          username=f"u{uuid.uuid4().hex[:10]}",
                          password_hash=hash_password(PASSWORD), email_verified=True,
                          email_verified_at=datetime.now(timezone.utc))
            db.add(staff2)
            db.flush()
            self.staff2_id = staff2.id
            self.emails.append(self.staff2_email)
            db.commit()
        finally:
            db.close()

    def _add(self, db, role, email, name):
        user = User(org_id=self.org_id, full_name=name, role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=datetime.now(timezone.utc))
        db.add(user)
        db.flush()
        self.emails.append(email)
        return user.id

    def add_member(self, role="viewer", name="Extra Person"):
        email = _new_email("extra")
        db = SessionLocal()
        try:
            uid = self._add(db, role, email, name)
            db.commit()
        finally:
            db.close()
        return uid, email

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

    @property
    def tenant_recipients(self):
        return sorted({self.owner_email.lower(), self.admin_email.lower()})

    def cleanup(self):
        db = SessionLocal()
        try:
            for org_id in (self.org_id, self.staff_org_id):
                db.query(OrgOperationalEvent).filter(
                    OrgOperationalEvent.org_id == org_id).delete()
                db.query(OrgMembershipEvent).filter(
                    OrgMembershipEvent.org_id == org_id).delete()
                db.query(AuditLog).filter(AuditLog.org_id == org_id).delete()
                db.query(Invitation).filter(Invitation.org_id == org_id).delete()
                db.query(SupportAccessRequest).filter(
                    SupportAccessRequest.org_id == org_id).delete()
                db.query(OwnershipTransfer).filter(
                    OwnershipTransfer.org_id == org_id).delete()
                for review in db.query(AccessReview).filter(
                        AccessReview.org_id == org_id).all():
                    # Escalations FK-reference assignments, so they go first.
                    db.query(AccessReviewEscalation).filter(
                        AccessReviewEscalation.review_id == review.id).delete()
                    db.query(AccessReviewAssignment).filter(
                        AccessReviewAssignment.review_id == review.id).delete()
                    db.delete(review)
            for email in self.emails:
                db.query(AccountStateEvent).filter(
                    AccountStateEvent.email == email.lower()).delete()
                db.query(OrgMembershipEvent).filter(
                    OrgMembershipEvent.email == email.lower()).delete()
            db.commit()
            for org_id in (self.org_id, self.staff_org_id):
                org = db.get(Organization, org_id)
                if org is not None:
                    org.owner_user_id = None
            db.commit()
            for org_id in (self.org_id, self.staff_org_id):
                for user in db.query(User).filter(User.org_id == org_id).all():
                    # StepUpGrant FK-references users; ORG-003/008 now mint one per
                    # high-risk operation, so teardown has to clear it too.
                    for model in (SignInEvent, IdentityChallenge, AccountRecovery,
                                  ElevationSession, StepUpGrant):
                        db.query(model).filter(model.user_id == user.id).delete()
                    db.delete(user)
                db.commit()
                org = db.get(Organization, org_id)
                if org is not None:
                    db.delete(org)
                    db.commit()
        finally:
            db.close()


def _req_row(req_id):
    db = SessionLocal()
    try:
        return db.get(SupportAccessRequest,
                      uuid.UUID(req_id) if isinstance(req_id, str) else req_id)
    finally:
        db.close()


def _second_staff_headers(client, fx):
    """Token for the independent operator who countersigns emergency access."""
    return fx.token(fx.staff2_email)


def _step_up(client, headers, purpose="ownership_transfer"):
    """Mint a step-up reference.

    ZST-EC-001 ORG-003/008 now require a fresh password re-verification for high-risk
    operations (services/stepup.py). These tests therefore re-authenticate the way a real
    client must; the mechanism itself is covered in test_stepup.py.
    """
    _reset_limits()
    r = client.post("/api/auth/step-up", headers=headers,
                    json={"password": PASSWORD, "purpose": purpose})
    assert r.status_code == 200, r.text
    return {**headers, "X-Step-Up": r.json()["reference"]}


def _support_request(client, fx, staff_headers, *, minutes=60, emergency=False,
                     scope="Event delivery logs for case triage",
                     actions=("tenant.events.read", "tenant.read")):
    body = {
        "org_id": str(fx.org_id), "case_reference": f"CASE-{uuid.uuid4().hex[:6].upper()}",
        "reason_category": "customer_reported_issue",
        "engineer_display": "R. Iyer, Zoiko Steam Support Engineer",
        "requested_scope": scope, "allowed_actions": list(actions), "minutes": minutes,
        "emergency": emergency,
    }
    if emergency:
        body["emergency_reason"] = "Live event outage blocking all viewers"
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        r = client.post("/api/admin/support-access", json=body, headers=staff_headers)
    assert r.status_code == 201, r.text
    return r.json(), cap


# ══ shared Class A controls ═════════════════════════════════════════════════════

def test_shared_sender_links_and_no_secrets():
    with patch.object(settings, "MAIL_FROM", "ZoikoStream <info@zoikostream.com>"):
        assert email_mod._sender_identity(email_mod.SENDER_SECURITY) == \
            "Zoiko Steam Security <info@zoikostream.com>"
    for bad in ("http://localhost:5173", "https://127.0.0.1:5173"):
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "APP_URL", bad):
            for fn in (email_mod.support_access_url, email_mod.access_review_url,
                       email_mod.ownership_transfer_url, email_mod.organization_status_url):
                try:
                    fn()
                except email_mod.UnsafeLinkError:
                    pass
                else:
                    raise AssertionError(f"{bad!r} must be refused in production")


def test_shared_customer_ctas_never_point_at_super_admin():
    """ORG-009's hard rule: no customer email may link a tenant to /admin."""
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "https://app.zoikostream.com"):
        for fn in (email_mod.support_access_url, email_mod.access_review_url,
                   email_mod.ownership_transfer_url, email_mod.organization_status_url):
            url = fn()
            assert "/admin" not in url, f"{url} exposes a Super Admin surface to a customer"
            assert url.startswith("https://app.zoikostream.com/organization")


# ══ ORG-006 — proof of absence ══════════════════════════════════════════════════

def test_006_no_scim_subsystem_exists():
    import pathlib
    hits = []
    for path in pathlib.Path("app").rglob("*.py"):
        code = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        for marker in ("/scim/v2", "scim_token", "SCIMUser", "scim_sync",
                       "quarantined_changes", "provisioning_job"):
            if marker in code:
                hits.append(f"{path}:{marker}")
    assert not hits, f"a SCIM subsystem appeared; ORG-006 must be revisited: {hits}"


def test_006_no_scim_templates_exist():
    code = "\n".join(l for l in open(email_mod.__file__, encoding="utf-8").read().splitlines()
                     if not l.lstrip().startswith("#"))
    for claim in ("SCIM configuration changed", "SCIM token created", "SCIM token rotated",
                  "SCIM synchronization needs attention", "SCIM synchronization recovered",
                  "token fingerprint"):
        assert claim not in code, \
            f"{claim!r} must not exist without a real SCIM provisioning domain"


def test_006_settings_changes_generate_no_scim_mail():
    """No cosmetic/unrelated settings flag may manufacture an ORG-006 message."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.admin_email)
        for path, body in (("/api/organization/security",
                            {"enforce_sso": True, "require_2fa": True}),
                           ("/api/organization/notifications", {"member_joined": True})):
            _reset_limits()
            cap, ctx = _capture()
            with ctx:
                r = client.patch(path, json=body, headers=headers)
            assert r.status_code in (200, 422), r.text
            assert cap.calls == [], f"{path} produced mail: {cap.subjects}"
    finally:
        fx.cleanup()


# ══ ORG-009 — security-critical ═════════════════════════════════════════════════

def test_009_request_is_durable_notifies_approvers_and_grants_nothing():
    """1 (cannot silently start), 2 (durable), 3/4 (approver + admins), 5 (case),
    6 (engineer), 7 (scope), 8 (actions), 9 (duration), 25 (no admin links),
    26 (no secrets), 28 (HTML+text)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        body, cap = _support_request(client, fx, staff)

        row = _req_row(body["id"])
        assert row is not None and row.status == SUPPORT_REQUESTED, "request must be durable"
        assert row.starts_at is None and row.expires_at is None, "a request grants nothing"
        assert row.approval_fingerprint is None, "nothing approved yet"
        db = SessionLocal()
        try:
            assert db.query(ElevationSession).filter(
                ElevationSession.user_id == fx.staff_id).count() == 0, \
                "no elevation may exist before approval"
        finally:
            db.close()

        payload = cap.of(ORG_009)
        assert cap.to(ORG_009) == fx.tenant_recipients, \
            f"owner + admins, deduplicated; got {cap.to(ORG_009)}"
        assert payload["from"].startswith("Zoiko Steam Security <"), "Class A sender"
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert body["case_reference"] in text, "case reference"
        assert "R. Iyer, Zoiko Steam Support Engineer" in text, "engineer identity"
        assert "Event delivery logs for case triage" in text, "scope"
        assert "tenant.events.read" in text, "allowed actions"
        assert "60 minutes" in text, "duration"
        for banned in ("/admin", "password_hash", "bearer ", "access_token", "api_key",
                       str(fx.staff_id)):
            assert banned.lower() not in text.lower(), f"{banned!r} must not appear"
    finally:
        fx.cleanup()


def test_009_start_is_refused_without_customer_approval():
    """10 — approval required before normal access starts. The core gate."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        body, _ = _support_request(client, fx, staff)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post(f"/api/admin/support-access/{body['id']}/start", headers=staff)
        assert r.status_code == 403, f"unapproved start must be refused, got {r.status_code}"
        assert cap.calls == [], "a refused start must send nothing"
        row = _req_row(body["id"])
        assert row.status == SUPPORT_REQUESTED and row.starts_at is None
    finally:
        fx.cleanup()


def test_009_approval_then_start_notifies_with_exact_window():
    """12 (started email), 13 (exact start/expiry), 19 (no open-ended), 29 (dedup)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        approver = fx.token(fx.owner_email)
        body, _ = _support_request(client, fx, staff)

        _reset_limits()
        cap_a, ctx_a = _capture()
        with ctx_a:
            a = client.post(f"/api/organization/support-access/{body['id']}/decision",
                            json={"approve": True}, headers=approver)
        assert a.status_code == 200, a.text
        row = _req_row(body["id"])
        assert row.status == SUPPORT_APPROVED and row.approval_fingerprint, \
            "approval must bind to the terms"
        assert row.approved_by_email == fx.owner_email.lower()

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post(f"/api/admin/support-access/{body['id']}/start", headers=staff)
        assert r.status_code == 200, r.text
        row = _req_row(body["id"])
        assert row.status == SUPPORT_ACTIVE
        assert row.starts_at and row.expires_at, "a session must be time-bound"
        assert (row.expires_at - row.starts_at) == timedelta(minutes=60)
        assert row.elevation_session_id, "the elevation and its authorization are linked"

        payload = cap.of(ORG_009_STARTED)
        assert cap.to(ORG_009_STARTED) == fx.tenant_recipients
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "IST" in text, "timezone-aware exact timestamps"
        assert org_comms.org_timestamp(
            type("O", (), {"timezone": "Asia/Kolkata", "id": None})(), row.expires_at) in text
        assert fx.owner_email.lower() in text, "approver is disclosed"

        # A second start does not re-announce.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            client.post(f"/api/admin/support-access/{body['id']}/start", headers=staff)
        assert ORG_009_STARTED not in cap2.subjects, "one notice per transition"
    finally:
        fx.cleanup()


def test_009_changing_scope_invalidates_approval():
    """11 — the control that makes 'approved' mean 'approved THESE terms'."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        approver = fx.token(fx.owner_email)
        body, _ = _support_request(client, fx, staff)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            client.post(f"/api/organization/support-access/{body['id']}/decision",
                        json={"approve": True}, headers=approver)
        assert _req_row(body["id"]).status == SUPPORT_APPROVED

        # Widen the scope and extend the duration after approval.
        _reset_limits()
        amend = client.patch(f"/api/admin/support-access/{body['id']}",
                             json={"requested_scope": "All organization data",
                                   "minutes": 480}, headers=staff)
        assert amend.status_code == 200, amend.text
        row = _req_row(body["id"])
        assert row.status == SUPPORT_REQUESTED, "amendment must revoke the approval"
        assert row.approval_fingerprint is None and row.approved_at is None

        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            r = client.post(f"/api/admin/support-access/{body['id']}/start", headers=staff)
        assert r.status_code == 403, "widened terms must not inherit the old approval"
        assert cap2.calls == []
        assert _req_row(body["id"]).starts_at is None
    finally:
        fx.cleanup()


def test_009_expiry_actually_ends_the_session_and_notifies():
    """14 (expiring), 16 (ended), 20 (expiry disables), 15 (extension needs new approval)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        approver = fx.token(fx.owner_email)
        body, _ = _support_request(client, fx, staff, minutes=30)
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            client.post(f"/api/organization/support-access/{body['id']}/decision",
                        json={"approve": True}, headers=approver)
            client.post(f"/api/admin/support-access/{body['id']}/start", headers=staff)

        # Pull expiry into the warning window.
        db = SessionLocal()
        try:
            row = db.get(SupportAccessRequest, uuid.UUID(body["id"]))
            row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            db.commit()
        finally:
            db.close()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                assert support_svc.sweep(db)["warned"] == 1
            finally:
                db.close()
        assert ORG_009_EXPIRING in cap.subjects, f"got {cap.subjects}"
        assert "new request" in cap.of(ORG_009_EXPIRING)["text"].lower(), \
            "extension must require a new approved request"

        # Now push it past expiry.
        db = SessionLocal()
        try:
            row = db.get(SupportAccessRequest, uuid.UUID(body["id"]))
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
            elevation_id = row.elevation_session_id
        finally:
            db.close()
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                assert support_svc.sweep(db)["closed"] == 1
            finally:
                db.close()

        row = _req_row(body["id"])
        assert row.status == SUPPORT_EXPIRED and row.ended_at is not None
        db = SessionLocal()
        try:
            elevation = db.get(ElevationSession, elevation_id)
            assert elevation.ended_at is not None, "expiry must close the elevation too"
            assert not support_svc.session_is_live(db, row), "session must not be live"
        finally:
            db.close()
        assert ORG_009_ENDED in cap2.subjects
        assert "Expired automatically" in cap2.of(ORG_009_ENDED)["text"]
    finally:
        fx.cleanup()


def test_009_privileged_actions_are_attributable():
    """18 — every privileged action attributable, via the existing audit log."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        approver = fx.token(fx.owner_email)
        body, _ = _support_request(client, fx, staff)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            client.post(f"/api/organization/support-access/{body['id']}/decision",
                        json={"approve": True}, headers=approver)
            client.post(f"/api/admin/support-access/{body['id']}/start", headers=staff)

        db = SessionLocal()
        try:
            row = db.get(SupportAccessRequest, uuid.UUID(body["id"]))
            staff_user = db.get(User, fx.staff_id)
            support_svc.record_action(db, row, actor=staff_user,
                                      action="delivery.inspect",
                                      target_type="event", target_id=uuid.uuid4())
            assert support_svc.action_count(db, row) == 1
            entry = db.query(AuditLog).filter(
                AuditLog.action == "delivery.inspect").one()
            meta = entry.meta or {}
            assert meta["support_request_id"] == str(row.id), "attributable to the session"
            assert meta["support_case"] == row.case_reference
            assert meta["engineer"] == row.engineer_display
            assert entry.org_id == fx.org_id, "attributable to the Organization"
            assert entry.actor_email == fx.staff_email.lower()
        finally:
            db.close()
    finally:
        fx.cleanup()


def test_009_emergency_is_identified_announced_and_truthful_about_dual_auth():
    """21 (identified), 22 (notified), 23 (post-use review), 24 (dual-auth truthful)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        body, _ = _support_request(client, fx, staff, emergency=True, minutes=30)
        row = _req_row(body["id"])
        assert row.emergency and row.emergency_reason, "emergency must be explicit"

        # Break-glass still starts without the CUSTOMER's approval — that is what makes it
        # break-glass. What it is no longer is self-service: an independent privileged
        # operator must countersign first (services/support_access.countersign_emergency).
        # Enforcement, including the refusal to self-countersign, is proven in
        # test_tenant_access.py::test_8.
        _reset_limits()
        refused = client.post(f"/api/admin/support-access/{body['id']}/start",
                              headers=staff)
        assert refused.status_code == 403, \
            "emergency must not start without an independent second authorizer"

        _reset_limits()
        signed = client.post(f"/api/admin/support-access/{body['id']}/countersign",
                             headers=_second_staff_headers(client, fx))
        assert signed.status_code == 200, signed.text

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post(f"/api/admin/support-access/{body['id']}/start", headers=staff)
        assert r.status_code == 200, "emergency access starts once countersigned"

        assert ORG_009_EMERGENCY in cap.subjects, "emergency must be announced immediately"
        payload = cap.of(ORG_009_EMERGENCY)
        assert cap.to(ORG_009_EMERGENCY) == fx.tenant_recipients
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "without prior approval" in text, "must not disguise itself as approved"
        assert "Live event outage blocking all viewers" in text, "declared reason"
        # Dual authorization now genuinely exists: start() refuses break-glass without
        # an independent countersignature and refuses self-countersigning
        # (services/support_access.countersign_emergency). The copy therefore names the
        # real second authorizer instead of reporting its absence.
        assert "Independently authorized by" in text, \
            "dual-authorization status must be truthful"
        assert "No second authorizer was recorded" not in text, \
            "the old no-dual-auth wording must be gone"
        for false_claim in ("dual authorization complete", "approved by two",
                            "second approver confirmed"):
            assert false_claim.lower() not in text.lower()

        # Post-use review obligation opens when the access closes.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            client.post(f"/api/admin/support-access/{body['id']}/end", headers=staff)
        row = _req_row(body["id"])
        assert row.status == SUPPORT_ENDED and row.post_use_review_at is not None, \
            "emergency access must carry a post-use review obligation"
    finally:
        fx.cleanup()


def test_009_emergency_without_reason_is_refused_and_duration_is_capped():
    """No open-ended access, and no undeclared emergency."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/admin/support-access", headers=staff, json={
                "org_id": str(fx.org_id), "case_reference": "CASE-X1",
                "reason_category": "platform_incident",
                "engineer_display": "A. Engineer",
                "requested_scope": "everything", "allowed_actions": ["tenant.read"],
                "minutes": 30, "emergency": True})
        assert r.status_code == 422, "an emergency needs a declared reason"
        assert cap.calls == []

        # Duration is clamped by the domain, not trusted from the caller.
        db = SessionLocal()
        try:
            staff_user = db.get(User, fx.staff_id)
            req = support_svc.create_request(
                db, org_id=fx.org_id, case_reference="CASE-CAP",
                reason_category="platform_incident", engineer=staff_user,
                engineer_display="A. Engineer", requested_scope="logs",
                allowed_actions=["tenant.read"], minutes=100000)
            assert req.requested_minutes == SUPPORT_MAX_MINUTES, "no open-ended session"
        finally:
            db.close()
    finally:
        fx.cleanup()


def test_009_denied_request_cannot_start_and_scoping_is_enforced():
    """A denial is final, and one tenant cannot see or decide another's requests."""
    fx = Fixture()
    other = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        body, _ = _support_request(client, fx, staff)

        # An admin of a DIFFERENT organization cannot decide this request.
        foreign = other.token(other.admin_email)
        _reset_limits()
        r = client.post(f"/api/organization/support-access/{body['id']}/decision",
                        json={"approve": True}, headers=foreign)
        assert r.status_code == 404, "cross-tenant approval must not be possible"

        approver = fx.token(fx.owner_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            d = client.post(f"/api/organization/support-access/{body['id']}/decision",
                            json={"approve": False}, headers=approver)
        assert d.status_code == 200, d.text
        assert _req_row(body["id"]).status == SUPPORT_DENIED

        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            s = client.post(f"/api/admin/support-access/{body['id']}/start", headers=staff)
        assert s.status_code == 403, "a denied request must never start"
        assert cap2.calls == []
    finally:
        fx.cleanup()
        other.cleanup()


def test_009_outage_does_not_grant_or_extend_access():
    """30 — a Resend failure must not change what is authorized."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        approver = fx.token(fx.owner_email)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.post("/api/admin/support-access", headers=staff, json={
                "org_id": str(fx.org_id), "case_reference": "CASE-OUT",
                "reason_category": "security_review",
                "engineer_display": "A. Engineer", "requested_scope": "logs",
                "allowed_actions": ["tenant.read"], "minutes": 30})
        assert r.status_code == 201, "a mail outage must not fail the request"
        assert cap.calls, "a send was attempted through Resend"
        req_id = r.json()["id"]
        assert _req_row(req_id).status == SUPPORT_REQUESTED, "still unapproved"

        _reset_limits()
        cap2, ctx2 = _capture(fail=True)
        with ctx2:
            client.post(f"/api/organization/support-access/{req_id}/decision",
                        json={"approve": True}, headers=approver)
        assert _req_row(req_id).status == SUPPORT_APPROVED, \
            "approval remains authoritative despite delivery failure"
    finally:
        fx.cleanup()


def test_009_class_a_preferences_cannot_suppress():
    """27 — ordinary notification preferences must not disable this family."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, fx.org_id)
            org.notifications = {"security_alerts": False, "member_joined": False}
            db.commit()
        finally:
            db.close()
        staff = fx.token(fx.staff_email)
        _body, cap = _support_request(client, fx, staff)
        assert ORG_009 in cap.subjects, "Class A ignores notification preferences"
    finally:
        fx.cleanup()


# ══ ORG-010 ═════════════════════════════════════════════════════════════════════

def test_010_unrelated_update_silent_suspend_and_reactivate_notify():
    """1 (unrelated silent), 2 (suspend), 4 (reactivate), 5/6 (recipients), 7 (time),
    8 (capabilities), 9 (coarse reason), 10 (no enforcement logic), 11 (recovery),
    16 (HTML+text), 17 (no tracking), 19 (dedup)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)

        # A metadata edit is not an operational-state change.
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            r0 = client.patch(f"/api/admin/organizations/{fx.org_id}",
                              json={"name": f"{fx.org_name} Renamed"}, headers=staff)
        assert r0.status_code == 200, r0.text
        assert cap0.calls == [], f"a rename must send nothing; got {cap0.subjects}"

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/admin/organizations/{fx.org_id}",
                             json={"status": "suspended",
                                   "reason_category": "billing_commercial_requirement"},
                             headers=staff)
        assert r.status_code == 200, r.text

        subject = ORG_010.format(state="suspended")
        assert cap.to(subject) == fx.tenant_recipients, \
            f"owner + security admins; got {cap.to(subject)}"
        payload = cap.of(subject)
        assert payload["from"].startswith("Zoiko Steam Security <")
        assert payload["html"] and payload["text"]
        low = payload["html"].lower()
        assert 'width="1"' not in low and '<img src="http' not in low, "no tracking"
        text = payload["text"]
        assert "Suspended" in text and "IST" in text, "state and exact effective time"
        # The old copy claimed sign-in was blocked while nothing checked organization
        # state — the reason ORG-010 audited INCORRECT. services/org_state.py now genuinely
        # refuses operational routes, and this sentence is generated from that module.
        # Enforcement proof lives in test_org_state.py.
        assert "Operational access to this Organization is refused" in text, \
            "capability claim must match what org_state actually enforces"
        assert "New sign-in to this Organization is blocked" not in text, \
            "the unenforced sign-in claim must be gone"
        assert "Billing / commercial requirement" in text, "coarse category"
        assert "Resolve the outstanding billing requirement." in text, "recovery criteria"
        assert "Contact Zoiko Steam Support" in text, "support route"
        for leak in ("fraud", "risk score", "detection", "rule", "investigation",
                     "billing_commercial_requirement", fx.staff_email):
            assert leak.lower() not in text.lower(), f"{leak!r} must not be disclosed"

        events = SessionLocal()
        try:
            rows = events.query(OrgOperationalEvent).filter(
                OrgOperationalEvent.org_id == fx.org_id).all()
            assert len(rows) == 1 and rows[0].notified_at is not None
            assert rows[0].previous_state == "active" and rows[0].state == "suspended"
            event_id = rows[0].id
        finally:
            events.close()

        # Re-notifying the recorded transition is a no-op.
        _reset_limits()
        cap_dup, ctx_dup = _capture()
        with ctx_dup:
            db = SessionLocal()
            try:
                governance.notify_org_state(
                    db, _Bg(), db.get(OrgOperationalEvent, event_id))
            finally:
                db.close()
        assert cap_dup.calls == [], "duplicate transition must not duplicate mail"

        # Reactivation is its own variant.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            r2 = client.patch(f"/api/admin/organizations/{fx.org_id}",
                              json={"status": "active"}, headers=staff)
        assert r2.status_code == 200, r2.text
        assert ORG_010_REACTIVATED in cap2.subjects, f"got {cap2.subjects}"
        rtext = cap2.of(ORG_010_REACTIVATED)["text"]
        assert "Restored capabilities" in rtext and "Remaining restrictions" in rtext
    finally:
        fx.cleanup()


def test_010_restricted_is_distinct_from_suspended():
    """3 — restrict sends ORG-010, and says what restriction actually does here."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        staff = fx.token(fx.staff_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/admin/organizations/{fx.org_id}",
                             json={"status": "restricted",
                                   "reason_category": "security_requirement"},
                             headers=staff)
        assert r.status_code == 200, r.text
        subject = ORG_010.format(state="restricted")
        text = cap.of(subject)["text"]
        assert "Restricted" in text
        assert "Security requirement" in text
        # A restricted tenant IS now cut off from operational routes, with an explicit
        # preserved allowlist (billing / export / privacy / security / support).
        assert "Operational access to this Organization is refused" in text
        assert ("billing, export, privacy, security settings and support remain available"
                in text.lower()), "the preserved surfaces must be named"
    finally:
        fx.cleanup()


def test_010_outage_does_not_revert_state():
    """18/20 — preferences cannot suppress, and a mail outage keeps the state."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, fx.org_id)
            org.notifications = {"security_alerts": False}
            db.commit()
        finally:
            db.close()
        staff = fx.token(fx.staff_email)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.patch(f"/api/admin/organizations/{fx.org_id}",
                             json={"status": "suspended",
                                   "reason_category": "policy_compliance_requirement"},
                             headers=staff)
        assert r.status_code == 200, r.text
        assert cap.calls, "Class A ignores preferences and still attempts the send"
        db = SessionLocal()
        try:
            assert db.get(Organization, fx.org_id).status == "suspended", \
                "a mail outage must not revert the state"
        finally:
            db.close()
    finally:
        fx.cleanup()


# ══ ORG-008 ═════════════════════════════════════════════════════════════════════

def test_008_initiation_does_not_move_ownership_and_notifies_all_parties():
    """1 (no immediate change), 2/3/4 (recipients), 19 (HTML+text), 8 (step-up truthful)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.owner_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/ownership-transfer",
                            json={"proposed_owner_email": fx.admin_email}, headers=headers)
        assert r.status_code == 201, r.text

        db = SessionLocal()
        try:
            assert db.get(Organization, fx.org_id).owner_user_id == fx.owner_id, \
                "ownership must NOT move on initiation"
        finally:
            db.close()

        got = cap.to(ORG_008)
        for who in (fx.owner_email.lower(), fx.admin_email.lower()):
            assert who in got, f"{who} must receive the request"
        assert len(got) == len(set(got)), "recipients deduplicate"
        payload = cap.of(ORG_008)
        assert payload["from"].startswith("Zoiko Steam Security <")
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Both current and proposed owner" in text, "dual confirmation stated"
        assert "never completed from an email link" in text, "no link-click authentication"
        # Step-up now exists (services/stepup.py) and BOTH confirmations require it, so
        # the copy states the real control instead of its former absence. Enforcement is
        # proven in test_stepup.py.
        assert "re-enter their password at the moment of confirmation" in text, \
            "step-up status must be truthful — it is now required"
        assert "not currently required" not in text, "the old PARTIAL wording must be gone"
        assert "token=" not in text, "the transfer mail carries no token at all"
    finally:
        fx.cleanup()


def test_008_both_confirmations_required_then_completes():
    """5/6 (persisted), 7 (both required), 9 (completes only then), 10 (completed email),
    11 (effective time), 12 (resulting permissions), 18 (duplicate confirmation safe)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        owner = fx.token(fx.owner_email)
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            r = client.post("/api/organization/ownership-transfer",
                            json={"proposed_owner_email": fx.admin_email}, headers=owner)
        tid = r.json()["id"]

        # First confirmation only.
        _reset_limits()
        cap1, ctx1 = _capture()
        with ctx1:
            c1 = client.post(f"/api/organization/ownership-transfer/{tid}/confirm",
                             headers=_step_up(client, owner))
        assert c1.status_code == 200, c1.text
        assert c1.json()["status"] == "current_owner_confirmed"
        assert ORG_008_DONE not in cap1.subjects, "one confirmation is not enough"
        db = SessionLocal()
        try:
            t = db.get(OwnershipTransfer, uuid.UUID(tid))
            assert t.current_owner_confirmed_at is not None
            assert t.proposed_owner_confirmed_at is None
            assert db.get(Organization, fx.org_id).owner_user_id == fx.owner_id
        finally:
            db.close()

        # A repeat of the same side is safe and still not enough.
        _reset_limits()
        cap_dup, ctx_dup = _capture()
        with ctx_dup:
            again = client.post(f"/api/organization/ownership-transfer/{tid}/confirm",
                                headers=_step_up(client, owner))
        assert again.status_code == 200 and again.json()["status"] == "current_owner_confirmed"
        assert ORG_008_DONE not in cap_dup.subjects

        # Second party completes it.
        proposed = fx.token(fx.admin_email)
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            c2 = client.post(f"/api/organization/ownership-transfer/{tid}/confirm",
                             headers=_step_up(client, proposed))
        assert c2.status_code == 200, c2.text
        assert c2.json()["status"] == "completed"

        db = SessionLocal()
        try:
            assert db.get(Organization, fx.org_id).owner_user_id == fx.admin_id, \
                "ownership moves only after both confirmations"
        finally:
            db.close()
        payload = cap2.of(ORG_008_DONE)
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "IST" in text, "exact effective time with timezone"
        assert "New owner holds Administrator rights" in text, "resulting permissions"
        assert fx.admin_email.lower() in text.lower()
    finally:
        fx.cleanup()


def test_008_expired_transfer_cannot_complete():
    """13 (cannot complete), 14 (expired email says ownership did not change)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        owner = fx.token(fx.owner_email)
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            r = client.post("/api/organization/ownership-transfer",
                            json={"proposed_owner_email": fx.admin_email}, headers=owner)
        tid = r.json()["id"]
        _reset_limits()
        cap1, ctx1 = _capture()
        with ctx1:
            client.post(f"/api/organization/ownership-transfer/{tid}/confirm",
                       headers=_step_up(client, owner))

        db = SessionLocal()
        try:
            t = db.get(OwnershipTransfer, uuid.UUID(tid))
            t.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()

        proposed = fx.token(fx.admin_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            c = client.post(f"/api/organization/ownership-transfer/{tid}/confirm",
                            headers=_step_up(client, proposed))
        assert c.status_code == 409, "an expired transfer must not complete"
        db = SessionLocal()
        try:
            assert db.get(OwnershipTransfer, uuid.UUID(tid)).status == "expired"
            assert db.get(Organization, fx.org_id).owner_user_id == fx.owner_id, \
                "ownership must remain unchanged"
        finally:
            db.close()
        text = cap.of(ORG_008_EXPIRED)["text"]
        assert "Ownership did not change." in text

        # A retry cannot resurrect it.
        _reset_limits()
        retry = client.post(f"/api/organization/ownership-transfer/{tid}/confirm",
                            headers=_step_up(client, proposed))
        assert retry.status_code == 409
    finally:
        fx.cleanup()


def test_008_cancel_and_unauthorized_actor():
    """15 (cancel works), 16 (canceled email), 17 (unauthorized cannot transfer)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        owner = fx.token(fx.owner_email)
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            r = client.post("/api/organization/ownership-transfer",
                            json={"proposed_owner_email": fx.admin_email}, headers=owner)
        tid = r.json()["id"]

        # A plain member is neither a party nor an admin.
        member = fx.token(fx.member_email)
        _reset_limits()
        nope = client.post(f"/api/organization/ownership-transfer/{tid}/confirm",
                           headers=_step_up(client, member))
        assert nope.status_code == 403, "only a named party may confirm"
        _reset_limits()
        nope2 = client.post("/api/organization/ownership-transfer",
                            json={"proposed_owner_email": fx.member_email}, headers=member)
        assert nope2.status_code == 403, "a viewer cannot initiate an ownership transfer"

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            c = client.delete(f"/api/organization/ownership-transfer/{tid}", headers=owner)
        assert c.status_code == 200, c.text
        assert ORG_008_CANCELED in cap.subjects
        text = cap.of(ORG_008_CANCELED)["text"]
        assert "Ownership did not change." in text
        assert fx.owner_email.lower() in text.lower(), "canceling actor identified"
        db = SessionLocal()
        try:
            assert db.get(Organization, fx.org_id).owner_user_id == fx.owner_id
        finally:
            db.close()
    finally:
        fx.cleanup()


def test_008_outage_does_not_change_ownership_outcome():
    """20 — a Resend failure changes nothing about the outcome."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        owner = fx.token(fx.owner_email)
        proposed = fx.token(fx.admin_email)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.post("/api/organization/ownership-transfer",
                            json={"proposed_owner_email": fx.admin_email}, headers=owner)
            assert r.status_code == 201, r.text
            tid = r.json()["id"]
            client.post(f"/api/organization/ownership-transfer/{tid}/confirm",
                       headers=_step_up(client, owner))
            c = client.post(f"/api/organization/ownership-transfer/{tid}/confirm",
                            headers=_step_up(client, proposed))
        assert c.status_code == 200 and c.json()["status"] == "completed"
        assert cap.calls, "sends were attempted"
        db = SessionLocal()
        try:
            assert db.get(Organization, fx.org_id).owner_user_id == fx.admin_id
        finally:
            db.close()
    finally:
        fx.cleanup()


# ══ ORG-007 ═════════════════════════════════════════════════════════════════════

def test_007_open_persists_assigns_and_notifies():
    """1 (persists), 2 (reviewers), 3/4 (owner + reviewer), 5 (due_at), 6 (pending is not
    approval), 18 (HTML+text)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.admin_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/access-reviews",
                            json={"due_in_days": 14, "review_period": "2026 H2"},
                            headers=headers)
        assert r.status_code == 201, r.text
        review_id = r.json()["id"]
        assert r.json()["outstanding"] >= 3, "every active member gets an assignment"

        db = SessionLocal()
        try:
            rows = db.query(AccessReviewAssignment).filter(
                AccessReviewAssignment.review_id == uuid.UUID(review_id)).all()
            assert rows, "assignments must persist"
            assert all(a.decision == "pending" for a in rows), \
                "silence must leave every decision PENDING, never approved"
            assert all(a.reviewer_email for a in rows), "reviewers assigned"
            assert all(a.access_snapshot for a in rows), "access snapshot captured"
        finally:
            db.close()

        got = cap.to(ORG_007.format(org=fx.org_name))
        for who in (fx.owner_email.lower(), fx.admin_email.lower()):
            assert who in got, f"{who} must be notified"
        payload = cap.of(ORG_007.format(org=fx.org_name))
        assert payload["from"].startswith("Zoiko Steam Security <")
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "IST" in text, "due date with timezone"
        assert "No response is not approval" in text, "must state inaction is not approval"
        assert "2026 H2" in text
    finally:
        fx.cleanup()


def test_007_completion_refused_while_pending_then_counts_are_accurate():
    """11 (completed variant), 12-15 (counts), 16 (high-risk recorded), 6 (no implicit
    approval), 17 (dedup)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.admin_email)
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            r = client.post("/api/organization/access-reviews",
                            json={"due_in_days": 14}, headers=headers)
        review_id = r.json()["id"]

        _reset_limits()
        rows = client.get(f"/api/organization/access-reviews/{review_id}/assignments",
                          headers=headers).json()
        assert len(rows) >= 3

        # Completion is refused while anything is pending.
        _reset_limits()
        cap_bad, ctx_bad = _capture()
        with ctx_bad:
            bad = client.post(f"/api/organization/access-reviews/{review_id}/complete",
                              headers=headers)
        assert bad.status_code == 409, "a review must not close over pending decisions"
        assert "pending" in bad.text.lower()
        assert cap_bad.calls == [], "a refused completion sends nothing"

        # An exception without a reason is refused.
        admin_row = next(a for a in rows if a["member_email"] == fx.admin_email.lower())
        _reset_limits()
        no_reason = client.post(
            f"/api/organization/access-reviews/{review_id}/assignments/{admin_row['id']}",
            json={"decision": "exception"}, headers=headers)
        assert no_reason.status_code == 422, "an exception requires a recorded reason"

        decisions = {}
        for i, row in enumerate(rows):
            if row["member_email"] == fx.admin_email.lower():
                payload = {"decision": "exception", "reason": "Needed for release duty",
                           "exception_owner_email": fx.owner_email}
            elif row["member_email"] == fx.member_email.lower():
                payload = {"decision": "remove", "reason": "Left the team"}
            elif i == 0:
                payload = {"decision": "change_required", "reason": "Downgrade pending"}
            else:
                payload = {"decision": "approved"}
            _reset_limits()
            d = client.post(
                f"/api/organization/access-reviews/{review_id}/assignments/{row['id']}",
                json=payload, headers=headers)
            assert d.status_code == 200, d.text
            decisions[row["member_email"]] = payload["decision"]

        # The administrator exception is derived as high-risk, not self-declared.
        db = SessionLocal()
        try:
            ex = db.query(AccessReviewAssignment).filter(
                AccessReviewAssignment.review_id == uuid.UUID(review_id),
                AccessReviewAssignment.member_email == fx.admin_email.lower()).one()
            assert ex.high_risk is True, "an administrator exception is high-risk"
            assert ex.exception_owner_email == fx.owner_email.lower(), "accountable owner"
        finally:
            db.close()

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            done = client.post(f"/api/organization/access-reviews/{review_id}/complete",
                               headers=headers)
        assert done.status_code == 200, done.text

        payload = cap.of(ORG_007_DONE.format(org=fx.org_name))
        text = payload["text"]
        assert payload["html"] and payload["text"]
        expected_removed = sum(1 for v in decisions.values() if v == "remove")
        expected_changed = sum(1 for v in decisions.values() if v == "change_required")
        assert f"Removed: {expected_removed}" in text
        assert f"Change required: {expected_changed}" in text
        assert "Exceptions: 1" in text
        assert "High-risk exceptions: 1" in text

        db = SessionLocal()
        try:
            review = db.get(AccessReview, uuid.UUID(review_id))
            assert review.status == "completed" and review.completed_at is not None
            rid = review.id
        finally:
            db.close()

        _reset_limits()
        cap_dup, ctx_dup = _capture()
        with ctx_dup:
            db = SessionLocal()
            try:
                governance.notify_review_completed(db, _Bg(), db.get(AccessReview, rid))
            finally:
                db.close()
        assert cap_dup.calls == [], "duplicate transition must not duplicate mail"
    finally:
        fx.cleanup()


def test_007_reminder_and_overdue_escalation_state():
    """7 (reminder), 8 (outstanding count), 9 (overdue), 10 (escalation state),
    19 (preferences cannot suppress), 20 (outage does not corrupt the review)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, fx.org_id)
            org.notifications = {"security_alerts": False}
            db.commit()
        finally:
            db.close()
        headers = fx.token(fx.admin_email)
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            r = client.post("/api/organization/access-reviews",
                            json={"due_in_days": 14}, headers=headers)
        assert ORG_007.format(org=fx.org_name) in cap0.subjects, \
            "Class A ignores notification preferences"
        review_id = uuid.UUID(r.json()["id"])

        # Into the reminder window.
        db = SessionLocal()
        try:
            review = db.get(AccessReview, review_id)
            review.due_at = datetime.now(timezone.utc) + timedelta(hours=6)
            db.commit()
        finally:
            db.close()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                assert governance.sweep(db)["reminded"] == 1
            finally:
                db.close()
        assert ORG_007_REMINDER in cap.subjects
        rtext = cap.of(ORG_007_REMINDER)["text"]
        assert "Still pending" in rtext and "No response is not approval" in rtext

        # Past due — an outage here must not corrupt the review state.
        db = SessionLocal()
        try:
            review = db.get(AccessReview, review_id)
            review.due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()
        _reset_limits()
        cap2, ctx2 = _capture(fail=True)
        with ctx2:
            db = SessionLocal()
            try:
                assert governance.sweep(db)["overdue"] == 1
            finally:
                db.close()
        assert cap2.calls, "a send was attempted"
        db = SessionLocal()
        try:
            review = db.get(AccessReview, review_id)
            assert review.status == "overdue", "the overdue state must stand"
            assert review.escalated_at is not None, "escalation state recorded"
            # Still not approved by silence.
            assert all(a.decision == "pending" for a in db.query(AccessReviewAssignment)
                       .filter(AccessReviewAssignment.review_id == review_id).all())
        finally:
            db.close()
    finally:
        fx.cleanup()


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
