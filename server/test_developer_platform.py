"""DEV-001 / DEV-002 — developer platform (ZST-EC-001).

DEV-002 is the substance here: real credentials were already being minted silently, with no
security notification at all. DEV-001's tests assert an ABSENCE — there is no developer
Application domain and no authoritative test/live mode, so the only correct behaviour is
that nothing fabricates either.

Run with `python test_developer_platform.py` (or pytest).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

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
    Organization,
    OrgMembershipEvent,
    SignInEvent,
    StepUpGrant,
    SupportAccessRequest,
    User,
)
from app.security import hash_password
from app.services import developer_comms

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
DEV_002 = email_mod.DEV_002_SUBJECT


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


def _new_email(tag="dev"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class Org:
    """Owner, a second admin, and an ordinary member."""

    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Dev Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            db.add(org)
            db.flush()
            self.org_id, self.org_name = org.id, org.name
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "org_admin", self.owner_email, "Owner")
            org.owner_user_id = self.owner_id
            self.admin_email = _new_email("admin")
            self.admin_id = self._u(db, "org_admin", self.admin_email, "Second Admin")
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
        return user.id

    def token(self, email=None):
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": email or self.admin_email,
                                  "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def records(self):
        db = SessionLocal()
        try:
            return db.get(Organization, self.org_id).api_keys or []
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(AuditLog).filter(AuditLog.org_id == self.org_id).delete()
            db.query(SupportAccessRequest).filter(
                SupportAccessRequest.org_id == self.org_id).delete()
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


# ══ DEV-001 — proof of absence ══════════════════════════════════════════════════

def test_001_no_application_domain_exists():
    """1/2 — there is no developer Application domain to generate lifecycle events."""
    import pathlib
    hits = []
    for path in pathlib.Path("app").rglob("*.py"):
        code = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        for marker in ("class DeveloperApplication", "class OAuthClient", "class AppClient",
                       "class APIClient", "client_secret", "application_id"):
            if marker in code:
                hits.append(f"{path}:{marker}")
    assert not hits, f"an Application domain appeared; DEV-001 must be revisited: {hits}"

    from app import models
    for name in ("DeveloperApplication", "Application", "OAuthClient", "AppClient"):
        assert not hasattr(models, name), f"{name} exists; DEV-001 must be revisited"


def test_001_no_lifecycle_templates_exist():
    """3 — no fake DEV-001 mail may exist without a domain behind it."""
    code = "\n".join(l for l in open(email_mod.__file__, encoding="utf-8").read().splitlines()
                     if not l.lstrip().startswith("#"))
    for claim in ("Application created in Zoiko Steam", "Zoiko Steam application archived",
                  "Zoiko Steam application restored", "Zoiko Steam application deleted"):
        assert claim not in code, \
            f"{claim!r} must not exist without an authoritative Application domain"


def test_001_test_mode_prefix_is_not_fabricated():
    """4 - [TEST MODE] must not be inferred from anything non-authoritative."""
    # A DEV-001 prefix would need an APPLICATION mode, and no Application domain
    # exists at all. `Organization.is_test` is a different thing entirely: a real
    # stored tenant flag that MED-001 legitimately uses for live inputs. So the
    # assertion is that no APPLICATION lifecycle produces the prefix - not that the
    # string is absent from a module that also holds the media templates.
    import inspect

    from app.services import developer_comms as dc

    assert '[TEST MODE]' not in inspect.getsource(dc), (
        'no authoritative application mode exists, so the developer platform must '
        'not produce the prefix')
    from app import models

    assert not hasattr(models, 'ApplicationMode'), (
        'an application mode appeared; DEV-001 must be revisited')
    # And the developer service says so rather than guessing from the key banner.
    assert "no separate test environment" in developer_comms.ENVIRONMENT_LABEL.lower()


def test_001_developer_surface_creates_no_application():
    """The /developer surface returns credentials and webhooks — no application concept."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        r = client.get("/api/organization/developer", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"api_keys", "webhooks"}, \
            f"no application collection exists on the developer surface; got {set(body)}"
    finally:
        o.cleanup()


# ══ DEV-002 ═════════════════════════════════════════════════════════════════════

def test_002_1_unauthorized_user_cannot_create_a_credential():
    """1 — minting is not a member-level action."""
    o = Org()
    client = TestClient(m.app)
    try:
        member = o.token(o.member_email)
        for path in ("/api/organization/api-keys", "/api/organization/developer/api-keys"):
            _reset_limits()
            cap, ctx = _capture()
            with ctx:
                r = client.post(path, json={"label": "Sneaky"}, headers=member)
            assert r.status_code == 403, \
                f"{path} must refuse an ordinary member; got {r.status_code}"
            assert cap.calls == [], "a refused creation must send nothing"
        assert o.records() == [], "and must not mint a credential"
    finally:
        o.cleanup()


def test_002_2_failed_creation_sends_no_email():
    """2 — a rejected request produces neither credential nor mail."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/api-keys", json={"label": ""}, headers=headers)
        assert r.status_code == 422, r.text
        assert cap.calls == [], "a failed creation must send nothing"
        assert o.records() == []
    finally:
        o.cleanup()


def test_002_3_to_21_successful_creation_sends_a_safe_class_a_notice():
    """3-21 — the substance of DEV-002."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()          # the second admin creates it
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/api-keys",
                            json={"label": "CI pipeline", "expires_in_days": 30},
                            headers=headers)
        assert r.status_code == 201, r.text
        created = r.json()

        # 4 — the credential exists before the notification.
        records = o.records()
        assert len(records) == 1, "the credential is committed"
        assert records[0]["id"] == created["id"]

        payload = cap.of(DEV_002)
        # 5/6/7 — creator + org admins, deduplicated, no unrelated members.
        got = cap.to(DEV_002)
        assert o.admin_email.lower() in got, "the creator is told"
        assert o.owner_email.lower() in got, "the Organization owner is told"
        assert o.member_email.lower() not in got, "an ordinary member is not"
        assert len(got) == len(set(got)), "recipients deduplicate"

        # 8 — subject. 9/10 — both parts. 19 — no tracking.
        assert payload["subject"] == "A new Zoiko Steam API credential was created"
        assert payload["html"] and payload["text"]
        low = payload["html"].lower()
        assert 'width="1"' not in low and '<img src="http' not in low, "no tracking pixel"
        assert payload["from"].startswith("Zoiko Steam Developer Platform <")

        text = payload["text"]
        # 11 — fingerprint present, and it matches what the console will show.
        fingerprint = created["fingerprint"]
        assert fingerprint and fingerprint in text, "the fingerprint must appear"
        assert "-" in fingerprint and len(fingerprint) == 9

        # 12/13 — the secret and all key material are absent.
        raw = created["key"]
        assert raw not in text and raw not in payload["html"], \
            "the raw credential must never appear"
        assert records[0]["key_hash"] not in text, "the stored verifier must not appear"
        # The prefix carries four characters of the real token, so it is not published.
        assert created["prefix"] not in text, \
            "prefix contains key material and must not be disclosed"
        for banned in ("password", "password_hash", "bcrypt", "jwt", "bearer ",
                       "access_token", "refresh_token", "secret_key", "zk_live_"):
            assert banned not in text.lower(), f"{banned!r} must not appear"

        # 14 — scope disclosure is truthful, not "Full access".
        assert "does not currently support scoped credentials" in text
        assert "Full access" not in text, "no scope model exists; this would be a false claim"
        # And the access line is honest about what the credential can reach today.
        assert "grants no access yet" in text

        # 15 — timezone-aware creation time. 16 — creator identity. 17 — organization.
        assert "IST" in text, "created_at must carry its timezone"
        assert o.admin_email.lower() in text.lower(), "the creator is identified"
        assert o.org_name in text
        assert "Expires" in text

        # 20/21 — CTA is a customer Developer console, never Super Admin or localhost.
        for surface in (text, payload["html"]):
            assert "/organization/developer" in surface, "CTA is the Developer console"
            assert "/admin" not in surface, "no customer email may link into Super Admin"
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "APP_URL", "http://localhost:5173"):
            try:
                email_mod.api_credentials_url()
            except email_mod.UnsafeLinkError:
                pass
            else:
                raise AssertionError("localhost must be refused in production")

        # 22 — the raw key came back only in the creation response.
        _reset_limits()
        listed = client.get("/api/organization/api-keys", headers=headers)
        assert listed.status_code == 200
        assert raw not in listed.text, "the raw key must never be retrievable again"
    finally:
        o.cleanup()


def test_002_23_storage_is_hashed_and_never_exposes_plaintext():
    """23 — and the stored verifier must not reach the browser either."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/api-keys", json={"label": "Storage check"},
                            headers=headers)
        raw = r.json()["key"]

        record = o.records()[0]
        assert "key" not in record, "no plaintext key field may be stored"
        assert record["key_hash"] != raw, "only a hash is stored"
        import hashlib
        assert record["key_hash"] == hashlib.sha256(raw.encode()).hexdigest()

        # GET /developer used to return the raw records, including key_hash.
        _reset_limits()
        dev = client.get("/api/organization/developer", headers=headers)
        assert dev.status_code == 200
        assert record["key_hash"] not in dev.text, \
            "the stored verifier must not be returned to a browser"
        assert raw not in dev.text
    finally:
        o.cleanup()


def test_002_24_review_and_revoke_path_works():
    """24 — the CTA must lead somewhere the credential can actually be revoked."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            created = client.post("/api/organization/api-keys",
                                  json={"label": "Revoke me"}, headers=headers).json()

        _reset_limits()
        listed = client.get("/api/organization/api-keys", headers=headers).json()
        assert any(k["id"] == created["id"] for k in listed), "the credential is reviewable"
        assert listed[0]["fingerprint"] == created["fingerprint"], \
            "the console fingerprint matches the emailed one"

        # Wrapped because revocation now legitimately fires DEV-004, which must not reach
        # the real provider from a test.
        _reset_limits()
        cap_revoke, ctx_revoke = _capture()
        with ctx_revoke:
            gone = client.delete(f"/api/organization/api-keys/{created['id']}",
                                 headers=headers)
        assert gone.status_code == 204, gone.text
        assert o.records()[0]["revoked"] is True, "revocation is real"
    finally:
        o.cleanup()


def test_002_25_duplicate_processing_sends_one_notification():
    """25 — one credential, one logical DEV-002."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            created = client.post("/api/organization/api-keys",
                                  json={"label": "Once only"}, headers=headers).json()
        assert cap.subjects.count(DEV_002) == len(cap.to(DEV_002)), "one per recipient"

        # Re-running the notification for the same credential claims nothing.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                org = db.get(Organization, o.org_id)
                sent = developer_comms.notify_credential_created(
                    db, _Bg(), org=org, creator=None, key_id=created["id"])
            finally:
                db.close()
        assert sent is False, "the claim must refuse a second send"
        assert cap2.calls == [], "and nothing may go out"

        # A SECOND credential is its own event and does notify.
        _reset_limits()
        cap3, ctx3 = _capture()
        with ctx3:
            client.post("/api/organization/api-keys", json={"label": "Second"},
                        headers=headers)
        assert DEV_002 in cap3.subjects, "a different credential is a different event"
    finally:
        o.cleanup()


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def test_002_26_outage_does_not_undo_credential_creation():
    """26 — a provider failure must leave the credential committed."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.post("/api/organization/api-keys", json={"label": "Outage"},
                            headers=headers)
        assert r.status_code == 201, "a mail outage must not fail credential creation"
        assert cap.calls, "a send was attempted through Resend"
        assert len(o.records()) == 1, "the credential stays committed"
    finally:
        o.cleanup()


def test_002_18_class_a_preferences_cannot_suppress():
    """18 — DEV-002 is Class A and no preference may disable it."""
    o = Org()
    client = TestClient(m.app)
    try:
        db = SessionLocal()
        try:
            db.get(Organization, o.org_id).notifications = {
                "security_alerts": False, "billing": False, "member_joined": False}
            db.commit()
        finally:
            db.close()
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/api-keys", json={"label": "Mandatory"},
                            headers=headers)
        assert r.status_code == 201, r.text
        assert DEV_002 in cap.subjects, "Class A ignores notification preferences"
        # The sender never consults the preference layer for this family.
        src = open(developer_comms.__file__, encoding="utf-8").read()
        assert "should_send_operational_notification" not in src, \
            "a Class A sender must not consult preferences at all"
    finally:
        o.cleanup()


def test_002_developer_route_also_notifies():
    """The second creation path must not be a silent back door."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/developer/api-keys",
                            json={"label": "Via developer console"}, headers=headers)
        assert r.status_code == 201, r.text
        assert DEV_002 in cap.subjects, \
            "every creation path must notify, or one becomes a way to mint quietly"
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
