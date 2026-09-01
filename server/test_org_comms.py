"""ORG-001 / ORG-002 / ORG-003 / ORG-004 / ORG-005 — Organization administration (ZST-EC-001).

Same harness as the IDN suites: the Resend call is intercepted at `httpx.post`, so every
message is proven to travel the real integration and no network request is made. A baseline
`_deny` hook records any send that escapes a test's own patch, so a leak fails the test
instead of reaching the provider.

The ORG-005 tests assert that NOTHING is sent. That is the point: `enforce_sso` is a stored
boolean with no identity provider, no domain verification, no certificate lifecycle and no
enforcement engine behind it, and these tests are what stop a later change from shipping an
"SSO is enabled" claim off a cosmetic flag.

Run with `python test_org_comms.py` (or pytest).
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
    ORG_ACCESS_CHANGED,
    ORG_MEMBERSHIP_REMOVED,
    AccountRecovery,
    AccountStateEvent,
    IdentityChallenge,
    Invitation,
    Organization,
    OrgMembershipEvent,
    SignInEvent,
    StepUpGrant,
    User,
)
from app.security import hash_password
from app.services import org as org_svc
from app.services import org_comms

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

ORG_001 = email_mod.ORG_001_SUBJECT
ORG_001_REMINDER = email_mod.ORG_001_REMINDER_SUBJECT
ORG_001_EXPIRED = email_mod.ORG_001_EXPIRED_SUBJECT
ORG_001_REVOKED = email_mod.ORG_001_REVOKED_SUBJECT
ORG_002 = email_mod.ORG_002_SUBJECT
ORG_003 = email_mod.ORG_003_SUBJECT
ORG_004 = email_mod.ORG_004_SUBJECT

IDN_008_RESTRICTED = email_mod.IDN_008_SUBJECT_RESTRICTED
IDN_008_DELETED = email_mod.IDN_008_SUBJECT_DELETED


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


def _new_email(tag="orgc"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class Fixture:
    """One organization with an admin, plus whatever members a test adds."""

    def __init__(self, tz="Asia/Kolkata"):
        self.emails: list[str] = []
        self.admin_email = _new_email("admin")
        db = SessionLocal()
        try:
            org = Organization(name=f"ORG Co {uuid.uuid4().hex[:6]}", status="active", timezone=tz)
            db.add(org)
            db.flush()
            self.org_id, self.org_name = org.id, org.name
            self.admin_id = self._add(db, self.admin_email, "org_admin")
            db.commit()
        finally:
            db.close()
        self.emails.append(self.admin_email)

    def _add(self, db, email, role, full_name=None):
        user = User(
            org_id=self.org_id, full_name=full_name or "Admin Person", role=role,
            is_active=True, email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
            password_hash=hash_password(PASSWORD), email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
        return user.id

    def add_member(self, role="viewer", full_name="Member Person"):
        email = _new_email("member")
        db = SessionLocal()
        try:
            uid = self._add(db, email, role, full_name)
            db.commit()
        finally:
            db.close()
        self.emails.append(email)
        return uid, email

    def token(self, email=None):
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": email or self.admin_email, "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(OrgMembershipEvent).filter(OrgMembershipEvent.org_id == self.org_id).delete()
            db.query(Invitation).filter(Invitation.org_id == self.org_id).delete()
            for email in self.emails:
                db.query(AccountStateEvent).filter(AccountStateEvent.email == email.lower()).delete()
                db.query(OrgMembershipEvent).filter(
                    OrgMembershipEvent.email == email.lower()).delete()
            db.commit()
            for user in db.query(User).filter(User.org_id == self.org_id).all():
                # StepUpGrant FK-references users; ORG-003 administrative grants now mint
                # one per high-risk operation, so teardown has to clear it too.
                for model in (SignInEvent, IdentityChallenge, AccountRecovery, StepUpGrant):
                    db.query(model).filter(model.user_id == user.id).delete()
                db.delete(user)
            db.commit()
            org = db.get(Organization, self.org_id)
            if org is not None:
                db.delete(org)
                db.commit()
        finally:
            db.close()


def _step_up(client, headers, purpose="high_risk_role_grant"):
    """Re-verify the password and return headers carrying the step-up reference.

    ZST-EC-001 ORG-003: granting administrative control now requires proof the acting admin
    is present right now, not that they signed in earlier (services/stepup.py). The
    mechanism itself is covered in test_stepup.py; here it is just what a caller must do.
    """
    _reset_limits()
    r = client.post("/api/auth/step-up", headers=headers,
                    json={"password": PASSWORD, "purpose": purpose})
    assert r.status_code == 200, r.text
    return {**headers, "X-Step-Up": r.json()["reference"]}


def _invite(client, fx, headers, role="viewer", email=None):
    """Create an invitation; returns (invitation_id, raw_token, invitee_email, capture)."""
    invitee = email or _new_email("invitee")
    fx.emails.append(invitee)
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        r = client.post("/api/organization/invitations",
                        json={"email": invitee, "role": role}, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["invite_token"], invitee, cap


def _inv_row(inv_id):
    db = SessionLocal()
    try:
        return db.get(Invitation, uuid.UUID(inv_id) if isinstance(inv_id, str) else inv_id)
    finally:
        db.close()


def _events(org_id, kind=None):
    db = SessionLocal()
    try:
        q = db.query(OrgMembershipEvent).filter(OrgMembershipEvent.org_id == org_id)
        if kind:
            q = q.filter(OrgMembershipEvent.kind == kind)
        return q.all()
    finally:
        db.close()


# ══ shared ══════════════════════════════════════════════════════════════════════

def test_shared_senders_and_link_safety():
    """ORG-001/002 are Class C (Zoiko Steam); ORG-003/004 are Class A (Zoiko Steam Security)."""
    with patch.object(settings, "MAIL_FROM", "ZoikoStream <info@zoikostream.com>"):
        assert email_mod._sender_identity(email_mod.SENDER_DEFAULT) == \
            "Zoiko Steam <info@zoikostream.com>"
        assert email_mod._sender_identity(email_mod.SENDER_SECURITY) == \
            "Zoiko Steam Security <info@zoikostream.com>"
    for bad in ("http://localhost:5173", "https://127.0.0.1:5173"):
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "APP_URL", bad):
            for fn in (lambda: email_mod.invitation_url("tok"), email_mod.members_url,
                       email_mod.access_url, email_mod.organizations_url):
                try:
                    fn()
                except email_mod.UnsafeLinkError:
                    pass
                else:
                    raise AssertionError(f"{bad!r} must be refused in production")


def test_shared_no_secrets_or_tracking_in_org_templates():
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch.object(settings, "APP_URL", "https://app.zoikostream.com"):
        bodies = [
            email_mod._org_shell("T", "H", "P", [("Role", "Viewer")], "b", "c",
                                 email_mod.invitation_url("tok"), "f"),
            email_mod._org_shell("T", "H", "P", [], "b", "c", email_mod.access_url(), "f"),
        ]
    for body in bodies:
        low = body.lower()
        for banned in ("password_hash", "bcrypt", "$2b$", "jwt", "bearer ", "access_token",
                       "refresh_token", "eyj", "api_key", "utm_", "pixel", "beacon",
                       "unsubscribe", "webinar", "token_hash"):
            assert banned not in low, f"{banned!r} must not appear in an ORG template"
        assert 'width="1"' not in low, "no tracking pixel"
        assert '<img src="http' not in low, "no remote asset"


# ══ ORG-001 ═════════════════════════════════════════════════════════════════════

def test_001_base_carries_role_scope_exact_expiry_and_timezone():
    """1 (sends), 2 (role), 3 (workspace scope), 5 (exact expiry), 6 (timezone),
    7 (no membership implied), 8 (CTA), 15/16 (HTML+text), 17 (Class C sender)."""
    fx = Fixture(tz="Asia/Kolkata")
    client = TestClient(m.app)
    try:
        headers = fx.token()
        inv_id, raw, invitee, cap = _invite(client, fx, headers, role="host")

        payload = cap.of(ORG_001.format(org=fx.org_name))
        assert payload["to"] == [invitee.lower()]
        assert payload["from"].startswith("Zoiko Steam <"), "ORG-001 is Class C"
        assert not payload["from"].startswith("Zoiko Steam Security"), "not the security identity"
        assert payload["html"] and payload["text"], "HTML and plain text are both required"

        text = payload["text"]
        assert "Host" in text, "user-facing role name must appear"
        assert "host" not in text.split("Role:")[1].split("\n")[0].replace("Host", ""), \
            "raw role identifier must not leak"
        assert "production" in text, "workspace scope must appear"

        # Exact stored expiry, rendered in the organization's real timezone.
        row = _inv_row(inv_id)
        expected = org_comms.org_timestamp(
            type("O", (), {"timezone": "Asia/Kolkata", "id": None})(), row.expires_at)
        assert expected in text, f"exact expiry {expected!r} missing from:\n{text}"
        assert "IST" in expected and "IST" in text, "timezone label must appear"
        assert "This link expires soon" not in text, "vague expiry copy must be gone"

        # An offer, not membership.
        assert "does not create membership" in text
        for claim in ("you are now a member", "your membership is active", "welcome to the team"):
            assert claim.lower() not in text.lower()

        # CTA carries the opaque token and nothing else.
        assert f"/accept-invite?token={raw}" in text
        assert invitee.split("@")[0] not in text.split("accept-invite?token=")[1].split()[0]
    finally:
        fx.cleanup()


def test_001_mode_access_is_omitted_not_invented():
    """4 — mode access appears only if supported. It is not, so it must be absent."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        _id, _raw, _invitee, cap = _invite(client, fx, headers)
        text = cap.of(ORG_001.format(org=fx.org_name))["text"]
        assert "Mode access:" not in text, "no mode-access concept exists; it must not be faked"
        for invented in ("Read-only", "Production mode", "All workspaces"):
            assert invented not in text, f"{invented!r} would describe an unenforceable scope"
    finally:
        fx.cleanup()


def test_001_reminder_variant_and_single_fire():
    """9 (reminder renders), 19 (duplicate processing does not duplicate)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        inv_id, _raw, invitee, _cap = _invite(client, fx, headers)

        # Pull the deadline inside the reminder window; the ticker owns the trigger.
        db = SessionLocal()
        try:
            row = db.get(Invitation, uuid.UUID(inv_id))
            row.expires_at = datetime.now(timezone.utc) + timedelta(hours=6)
            db.commit()
            due = org_comms.due_reminders(db)
            assert any(str(d.id) == inv_id for d in due), "invitation should be reminder-due"
            expires_before = row.expires_at
        finally:
            db.close()

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                assert org_comms.send_due_reminders(db) >= 1
            finally:
                db.close()

        payload = cap.of(ORG_001_REMINDER)
        assert payload["to"] == [invitee.lower()]
        assert payload["from"].startswith("Zoiko Steam <")
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "IST" in text, "exact expiry with timezone"
        assert "does not create membership" in text, "must not imply membership exists"
        assert "/accept-invite?token=" in text

        # The reminder must not silently extend the deadline it is announcing.
        row = _inv_row(inv_id)
        assert row.expires_at == expires_before, "a reminder must not extend authorization"

        # Second pass claims nothing.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                assert org_comms.send_due_reminders(db) == 0
            finally:
                db.close()
        assert cap2.calls == [], "one reminder per invitation"
    finally:
        fx.cleanup()


def test_001_expired_variant_and_expired_cannot_be_accepted():
    """10 (expired cannot be accepted), 11 (expired variant renders)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        inv_id, raw, invitee, _cap = _invite(client, fx, headers)
        db = SessionLocal()
        try:
            row = db.get(Invitation, uuid.UUID(inv_id))
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/invitations/accept",
                            json={"token": raw, "full_name": "New Person",
                                  "password": "brand-new-secret"})
        assert r.status_code == 400, "an expired invitation must not be acceptable"
        assert _inv_row(inv_id).status == "expired"

        payload = cap.of(ORG_001_EXPIRED)
        assert payload["to"] == [invitee.lower()]
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "expired at" in text and "IST" in text, "exact expiry with timezone"
        assert "contact an authorized administrator" in text.lower(), "must route to an admin"
        assert "/accept-invite?token=" not in text, "expired mail must not offer an accept CTA"

        # No membership was created.
        db = SessionLocal()
        try:
            assert db.query(User).filter(User.email == invitee.lower()).count() == 0
        finally:
            db.close()

        # Refreshing the dead link does not re-mail.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            client.get(f"/api/organization/invitations/preview?token={raw}")
        assert ORG_001_EXPIRED not in cap2.subjects, "one expiry notice per invitation"
    finally:
        fx.cleanup()


def test_001_revoked_variant_and_revoked_cannot_be_accepted():
    """12 (revoked renders), 13 (revoked cannot be accepted), 18 (no reason disclosed)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        inv_id, raw, invitee, _cap = _invite(client, fx, headers)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/organization/invitations/{inv_id}",
                             json={"action": "cancel"}, headers=headers)
        assert r.status_code == 200, r.text
        assert _inv_row(inv_id).status == "cancelled"

        payload = cap.of(ORG_001_REVOKED)
        assert payload["to"] == [invitee.lower()]
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Effective:" in text and "IST" in text, "effective time with timezone"
        # No internal reason.
        for leak in ("administrative_action", "reason_category", fx.admin_email, "policy",
                     "investigation"):
            assert leak.lower() not in text.lower(), f"{leak!r} must not be disclosed"

        _reset_limits()
        r2 = client.post("/api/organization/invitations/accept",
                         json={"token": raw, "full_name": "New Person",
                               "password": "brand-new-secret"})
        assert r2.status_code == 400, "a revoked invitation must not be acceptable"
    finally:
        fx.cleanup()


def test_001_resend_grants_nothing_and_rotates():
    """14 — resend must not silently grant access."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        inv_id, raw, invitee, _cap = _invite(client, fx, headers, role="viewer")
        before = _inv_row(inv_id)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/organization/invitations/{inv_id}",
                             json={"action": "resend"}, headers=headers)
        assert r.status_code == 200, r.text
        after = _inv_row(inv_id)

        assert after.status == "pending", "still an offer"
        assert after.role == before.role, "resend must not change the granted role"
        assert after.accepted_at is None, "resend must not accept anything"
        assert after.token_hash != before.token_hash, "old link must stop working"
        db = SessionLocal()
        try:
            assert db.query(User).filter(User.email == invitee.lower()).count() == 0, \
                "no membership may exist before acceptance"
        finally:
            db.close()

        # The superseded token is genuinely dead.
        _reset_limits()
        dead = client.post("/api/organization/invitations/accept",
                           json={"token": raw, "full_name": "X", "password": "brand-new-secret"})
        assert dead.status_code == 400, "the rotated-away token must not work"
        assert ORG_001.format(org=fx.org_name) in cap.subjects, "the new offer is announced"
    finally:
        fx.cleanup()


def test_001_resend_failure_does_not_corrupt_invitation_state():
    """20 — a provider outage must leave the invitation exactly as committed."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        invitee = _new_email("invitee")
        fx.emails.append(invitee)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.post("/api/organization/invitations",
                            json={"email": invitee, "role": "viewer"}, headers=headers)
        assert r.status_code == 201, "a mail outage must not fail invitation creation"
        assert cap.calls, "a send was attempted through Resend"
        row = _inv_row(r.json()["id"])
        assert row.status == "pending" and row.expires_at is not None
    finally:
        fx.cleanup()


# ══ ORG-002 ═════════════════════════════════════════════════════════════════════

def test_002_acceptance_notifies_inviter_and_admins_deduplicated():
    """1 (membership created), 2 (nothing before commit), 3 (inviter), 4 (admins),
    5 (dedup), 6-10 (facts), 11 (unrelated members get nothing), 12/13 (parts),
    14 (sender), 16 (one set per acceptance)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        # A second admin (must be notified) and an unrelated viewer (must not be).
        _second_id, second_admin = fx.add_member(role="org_admin", full_name="Second Admin")
        _viewer_id, viewer_email = fx.add_member(role="viewer", full_name="Bystander")

        inv_id, raw, invitee, cap_create = _invite(client, fx, headers, role="host")
        assert ORG_002.format(member="x", org=fx.org_name) not in cap_create.subjects
        assert not [s for s in cap_create.subjects if "joined" in s], \
            "no ORG-002 before the invitation is accepted"

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/invitations/accept",
                            json={"token": raw, "full_name": "Newly Joined",
                                  "password": "brand-new-secret"})
        assert r.status_code == 200, r.text

        subject = ORG_002.format(member="Newly Joined", org=fx.org_name)
        got = cap.to(subject)
        assert got == sorted([fx.admin_email.lower(), second_admin.lower()]), \
            f"inviter + admins, deduplicated; got {got}"
        assert invitee.lower() not in got, "the joiner is not an ORG-002 recipient"
        assert viewer_email.lower() not in got, "unrelated members receive nothing"
        assert len(got) == len(set(got)), "no duplicate recipients"

        payload = cap.of(subject)
        assert payload["from"].startswith("Zoiko Steam <"), "ORG-002 is Class C"
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Newly Joined" in text and fx.org_name in text
        assert "Host" in text, "role summary"
        assert "production" in text, "workspace scope"
        assert "IST" in text, "accepted_at carries its timezone"

        row = _inv_row(inv_id)
        assert row.status == "accepted" and row.accepted_at is not None
        db = SessionLocal()
        try:
            assert db.query(User).filter(User.email == invitee.lower()).count() == 1
        finally:
            db.close()
    finally:
        fx.cleanup()


def test_002_class_c_preference_and_outage_do_not_undo_membership():
    """15 (member_joined preference behaviour), 17 (outage does not undo membership)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, fx.org_id)
            # ORG-002 is the one Class C family policy lets a preference govern. Since
            # ZST-EC-001 ORG-012 made the toggles effective, member_joined=false genuinely
            # suppresses it — before that it was inert and this could not be tested.
            org.notifications = {"member_joined": False, "security_alerts": False}
            db.commit()
        finally:
            db.close()

        headers = fx.token()
        _inv_id, raw, invitee, _c = _invite(client, fx, headers)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/invitations/accept",
                            json={"token": raw, "full_name": "Quiet Person",
                                  "password": "brand-new-secret"})
        assert r.status_code == 200, r.text
        suppressed = ORG_002.format(member="Quiet Person", org=fx.org_name)
        assert suppressed not in cap.subjects, \
            f"member_joined=false must suppress ORG-002; got {cap.subjects}"
        db = SessionLocal()
        try:
            assert db.query(User).filter(User.email == invitee.lower()).count() == 1, \
                "the membership still happened; only the notification was suppressed"
        finally:
            db.close()

        # And with the preference ON, a provider outage still must not undo the membership.
        db = SessionLocal()
        try:
            org = db.get(Organization, fx.org_id)
            org.notifications = {"member_joined": True}
            db.commit()
        finally:
            db.close()
        _inv_id2, raw2, invitee2, _c2 = _invite(client, fx, headers)
        _reset_limits()
        cap2, ctx2 = _capture(fail=True)
        with ctx2:
            r2 = client.post("/api/organization/invitations/accept",
                             json={"token": raw2, "full_name": "Outage Person",
                                   "password": "brand-new-secret"})
        assert r2.status_code == 200, "a mail outage must not fail acceptance"
        assert cap2.calls, "a send was attempted"
        db = SessionLocal()
        try:
            assert db.query(User).filter(User.email == invitee2.lower()).count() == 1, \
                "membership stays committed"
        finally:
            db.close()
    finally:
        fx.cleanup()


# ══ ORG-003 ═════════════════════════════════════════════════════════════════════

def test_003_unrelated_update_sends_nothing_and_role_change_sends():
    """1 (unrelated update silent), 2 (role change sends), 5/6 (previous/current),
    7 (effective time), 8 (member), 9 (admins), 10 (dedup), 11 (sender), 12/13 (parts),
    16 (no permission internals), 19 (duplicate transition)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        member_id, member_email = fx.add_member(role="viewer", full_name="Changing Person")

        # A rename is not an access change.
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            r0 = client.patch(f"/api/organization/users/{member_id}",
                              json={"full_name": "Renamed Person"}, headers=headers)
        assert r0.status_code == 200, r0.text
        assert ORG_003 not in cap0.subjects, "a rename must not send ORG-003"
        assert _events(fx.org_id, ORG_ACCESS_CHANGED) == [], "no transition recorded"

        # A role change is.
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/organization/users/{member_id}",
                             json={"role": "org_admin"},
                             headers=_step_up(client, headers))
        assert r.status_code == 200, r.text

        got = cap.to(ORG_003)
        assert member_email.lower() in got, "the affected member must be told"
        assert fx.admin_email.lower() in got, "relevant admins must be told"
        assert len(got) == len(set(got)), "recipients deduplicate"

        payload = cap.of(ORG_003)
        assert payload["from"].startswith("Zoiko Steam Security <"), "ORG-003 is Class A"
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Previous: Role: Viewer" in text, f"previous access wrong:\n{text}"
        assert "Current: Role: Administrator" in text, f"current access wrong:\n{text}"
        assert "IST" in text, "effective time carries its timezone"
        # User-facing names only.
        assert "org_admin" not in text and "viewer" not in text, \
            "raw role identifiers must not be exposed"
        for internal in ("_ROLE_RANK", "permission_bit", "scope_mask"):
            assert internal not in text

        events = _events(fx.org_id, ORG_ACCESS_CHANGED)
        assert len(events) == 1, f"exactly one transition recorded, got {len(events)}"
        assert events[0].notified_at is not None

        # Re-notifying the same recorded transition is a no-op.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                ev = db.get(OrgMembershipEvent, events[0].id)
                org_comms.notify_membership_event(db, _Bg(), ev)
            finally:
                db.close()
        assert cap2.calls == [], "a duplicate transition must not duplicate mail"
    finally:
        fx.cleanup()


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def test_003_high_risk_controls_are_not_claimed():
    """17 — the step-up / dual-approval control status must be truthful."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        member_id, _member_email = fx.add_member(role="viewer")
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            client.patch(f"/api/organization/users/{member_id}",
                         json={"role": "org_admin"},
                         headers=_step_up(client, headers))
        text = cap.of(ORG_003)["text"].lower()
        for false_claim in ("dual approval", "dual-approval", "step-up", "step up",
                            "re-authenticated", "second approver", "approved by two"):
            assert false_claim not in text, \
                f"{false_claim!r} must not be claimed - no such subsystem exists"
    finally:
        fx.cleanup()


def test_003_failed_update_sends_nothing_and_preferences_cannot_suppress():
    """15 (preferences cannot suppress), 18 (failed update sends nothing),
    20 (outage does not undo the access change)."""
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
        headers = fx.token()

        # Rejected update: invalid role never commits.
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            bad = client.patch(f"/api/organization/users/{fx.admin_id}",
                               json={"role": "super_admin"}, headers=headers)
        assert bad.status_code in (400, 403, 422), bad.text
        assert cap0.calls == [], "a failed update must send nothing"

        # Committed change still mails despite preferences off, and survives an outage.
        member_id, _e = fx.add_member(role="viewer")
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.patch(f"/api/organization/users/{member_id}",
                             json={"role": "host"}, headers=headers)
        assert r.status_code == 200, r.text
        assert ORG_003 in cap.subjects, "Class A ignores notification preferences"
        db = SessionLocal()
        try:
            assert db.get(User, member_id).role == "host", "outage must not undo the change"
        finally:
            db.close()
    finally:
        fx.cleanup()


# ══ ORG-004 ═════════════════════════════════════════════════════════════════════

def test_004_removal_notifies_member_and_admins_with_identity_line():
    """2 (committed removal sends), 3 (member), 4 (admins), 5 (org name), 6 (effective_at),
    7 (identity-preservation line), 8 (other orgs unaffected), 9/10 (no HR/disciplinary),
    11 (sender), 12/13 (parts), 16 (audit record), 17 (no duplicate)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        member_id, member_email = fx.add_member(role="host", full_name="Leaving Person")

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.delete(f"/api/organization/users/{member_id}", headers=headers)
        assert r.status_code == 204, r.text

        subject = ORG_004.format(org=fx.org_name)
        got = cap.to(subject)
        assert member_email.lower() in got, "the removed member must be told"
        assert fx.admin_email.lower() in got, "relevant admins must be told"
        assert len(got) == len(set(got)), "recipients deduplicate"

        member_copy = [c["payload"] for c in cap.calls
                       if c["payload"]["subject"] == subject
                       and c["payload"]["to"] == [member_email.lower()]][0]
        assert member_copy["from"].startswith("Zoiko Steam Security <"), "ORG-004 is Class A"
        assert member_copy["html"] and member_copy["text"]
        text = member_copy["text"]
        assert ("This does not delete your Zoiko identity or affect access to other "
                "Organizations.") in text, "the identity-preservation line is mandatory"
        assert fx.org_name in text and "IST" in text
        for leak in ("hr", "disciplinary", "investigation", "misconduct", "performance",
                     "organization_administrative_action", "reason_category"):
            assert leak not in text.lower(), f"{leak!r} must not be disclosed"

        # ORG-004 must not claim the identity ended; IDN-008 owns that and is separate.
        for confusion in ("your account was deleted", "your identity was deleted",
                          "permanently erased"):
            assert confusion not in text.lower()

        events = _events(fx.org_id, ORG_MEMBERSHIP_REMOVED)
        assert len(events) == 1 and events[0].notified_at is not None, "audit record persists"
        assert events[0].previous_access.startswith("Role: Host")
        assert events[0].current_access == "No access"

        # Both families fired, because both statements are true.
        assert IDN_008_DELETED in cap.subjects, "identity deletion is announced separately"

        # Re-notifying the recorded transition is a no-op.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                org_comms.notify_membership_event(
                    db, _Bg(), db.get(OrgMembershipEvent, events[0].id))
            finally:
                db.close()
        assert cap2.calls == [], "duplicate removal must not duplicate email"
    finally:
        fx.cleanup()


def test_004_failed_removal_sends_nothing():
    """1 — a refused removal must send nothing."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            # Self-deletion is refused by the router's self-lockout guard.
            r = client.delete(f"/api/organization/users/{fx.admin_id}", headers=headers)
        assert r.status_code == 400, r.text
        assert cap.calls == [], "a refused removal must send nothing"
        assert _events(fx.org_id, ORG_MEMBERSHIP_REMOVED) == [], "no transition recorded"
    finally:
        fx.cleanup()


def test_004_preferences_cannot_suppress_and_outage_does_not_restore_membership():
    """14 (preferences cannot suppress), 18 (outage does not restore membership)."""
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
        headers = fx.token()
        member_id, _member_email = fx.add_member(role="viewer")

        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.delete(f"/api/organization/users/{member_id}", headers=headers)
        assert r.status_code == 204, r.text
        assert ORG_004.format(org=fx.org_name) in cap.subjects, "Class A ignores preferences"
        db = SessionLocal()
        try:
            assert db.get(User, member_id).deleted_at is not None, \
                "a mail outage must not restore membership"
        finally:
            db.close()
    finally:
        fx.cleanup()


# ══ ORG-005 — proof of absence ══════════════════════════════════════════════════

def test_005_no_sso_subsystem_exists():
    """No IdP, no domain verification, no certificate lifecycle, no enforcement engine."""
    import pathlib
    root = pathlib.Path("app")
    hits = []
    for path in root.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
        for marker in ("SAMLResponse", "acs_url", "idp_entity_id", "idp_metadata",
                       "sso_certificate", "certificate_expires_at", "sso_test_mode"):
            if marker in code:
                hits.append(f"{path}:{marker}")
    assert not hits, f"an SSO subsystem appeared; ORG-005 must be revisited: {hits}"


def test_005_no_lifecycle_templates_exist_for_unbacked_state():
    """The five ORG-005 variants must not exist while nothing authoritative drives them."""
    body = open(email_mod.__file__, encoding="utf-8").read()
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    for claim in ("SSO configuration changed", "SSO domain verified",
                  "SSO enforcement is scheduled", "SSO certificate expires soon",
                  "SSO sign-in failures detected", "SSO sign-in recovered",
                  "Your identity provider is connected"):
        assert claim not in code, \
            f"{claim!r} must not exist without a real SSO subsystem behind it"


def test_005_enforce_sso_flag_sends_no_mail_and_is_not_reported_as_enforced():
    """Flipping the cosmetic boolean must change no sign-in policy and send nothing."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch("/api/organization/security",
                             json={"enforce_sso": True, "require_2fa": True},
                             headers=headers)
        assert r.status_code == 200, r.text
        assert cap.calls == [], f"a cosmetic flag must send no mail; got {cap.subjects}"

        # The posture the console renders must not call it an enforced control.
        db = SessionLocal()
        try:
            org = db.get(Organization, fx.org_id)
            posture = org_svc.security_support(db, org)
        finally:
            db.close()
        assert posture["sso_enforced"] is False, \
            "enforce_sso=true must not be reported as enforced - nothing enforces it"
        assert posture["two_factor_required"] is False, \
            "require_2fa=true must not be reported as enforced"
        assert posture["sso_requested"] is True, "the stored preference is still reported"
        assert posture["sso_available"] is False, "and it is reported as unavailable"
    finally:
        fx.cleanup()


def test_005_login_is_not_routed_through_sso_when_flag_is_set():
    """The strongest proof: with enforce_sso true, password sign-in still works unchanged,
    which is exactly why no 'SSO is enforced' message may ever be sent."""
    fx = Fixture()
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, fx.org_id)
            org.security = {"enforce_sso": True}
            db.commit()
        finally:
            db.close()
        headers = fx.token()  # a plain password login; would raise if it stopped working
        assert headers["Authorization"].startswith("Bearer ")
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
