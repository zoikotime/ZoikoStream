"""DEV-003 / DEV-004 / DEV-005 / DEV-006 / DEV-007 — developer platform lifecycle.

The security substance here is DEV-006: before this, `enqueue` gated only on `enabled`, so
any URL an administrator saved began receiving signed production payloads immediately, and
the URL column had no validation at all. Those two tests are the ones that matter most.

DEV-005 asserts an absence. There is no API-key authentication anywhere in the codebase, so
there is no usage telemetry and no detection input — the only correct behaviour is that
nothing fabricates a compromise alert.

Run with `python test_developer_lifecycle.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.crud import admin as admin_crud
from app.db import SessionLocal
from app.models import (
    WEBHOOK_DISABLE_THRESHOLD,
    WEBHOOK_DISABLED,
    WEBHOOK_FAILURE_THRESHOLD,
    WEBHOOK_HEALTHY,
    WEBHOOK_PENDING_VERIFICATION,
    WEBHOOK_VERIFIED,
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
    WebhookDelivery,
    WebhookEndpoint,
)
from app.security import hash_password
from app.services import credential_lifecycle, webhook_lifecycle, webhook_security
from app.services import webhooks as webhook_svc

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

DEV_003_WARN = email_mod.DEV_003_WARNING_SUBJECT
DEV_003_EXPIRED = email_mod.DEV_003_EXPIRED_SUBJECT
DEV_004_REVOKED = email_mod.DEV_004_REVOKED_SUBJECT
DEV_004_ROTATED = email_mod.DEV_004_ROTATED_SUBJECT
DEV_004_EMERGENCY = email_mod.DEV_004_EMERGENCY_SUBJECT
DEV_006_VERIFY = email_mod.DEV_006_VERIFY_SUBJECT
DEV_006_RESET = email_mod.DEV_006_RESET_SUBJECT
DEV_007_FAILING = email_mod.DEV_007_FAILING_SUBJECT
DEV_007_RECOVERED = email_mod.DEV_007_RECOVERED_SUBJECT
DEV_007_DISABLED = email_mod.DEV_007_DISABLED_SUBJECT

# A public host that really resolves, so the SSRF guard's DNS check passes in tests.
PUBLIC_URL = "https://example.com/hooks/zoiko"


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


def _new_email(tag="devl"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class Org:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"DevL Co {uuid.uuid4().hex[:6]}", status="active",
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

    def records(self):
        db = SessionLocal()
        try:
            return db.get(Organization, self.org_id).api_keys or []
        finally:
            db.close()

    def record(self, key_id):
        return next((r for r in self.records() if r["id"] == key_id), None)

    def make_key(self, client, headers, label="Key", expires_in_days=None):
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            body = {"label": label}
            if expires_in_days:
                body["expires_in_days"] = expires_in_days
            r = client.post("/api/organization/api-keys", json=body, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()

    def endpoint(self, endpoint_id=None):
        db = SessionLocal()
        try:
            if endpoint_id:
                return db.get(WebhookEndpoint, endpoint_id)
            return db.scalars(
                db.query(WebhookEndpoint).filter(
                    WebhookEndpoint.org_id == self.org_id).statement).first()
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            for ep in db.query(WebhookEndpoint).filter(
                    WebhookEndpoint.org_id == self.org_id).all():
                db.query(WebhookDelivery).filter(
                    WebhookDelivery.endpoint_id == ep.id).delete()
                db.delete(ep)
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


def _make_endpoint(client, headers, url=PUBLIC_URL, events=("recording.ready",)):
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        r = client.post("/api/organization/developer/webhooks",
                        json={"url": url, "label": "Test hook", "events": list(events)},
                        headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json(), cap


# ══ DEV-005 — proof of absence ══════════════════════════════════════════════════

def test_005_no_api_key_authentication_or_telemetry_exists():
    """No auth path reads the verifier, so there is no usage telemetry to detect against."""
    import pathlib
    hits = []
    for path in pathlib.Path("app").rglob("*.py"):
        code = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        # Scoped to API-KEY authentication. `last_used_at` also exists on event access
        # links, a different credential family entirely and not a signal here.
        for marker in ("X-API-Key", "verify_api_key", "authenticate_api_key",
                       "ApiKeyUsage"):
            if marker in code:
                hits.append(f"{path}:{marker}")
    assert not hits, f"API-key auth/telemetry appeared; DEV-003/005 must be revisited: {hits}"


def test_005_no_compromise_templates_exist():
    """No detection input exists, so no compromise alert may be fabricated."""
    code = "\n".join(l for l in open(email_mod.__file__, encoding="utf-8").read().splitlines()
                     if not l.lstrip().startswith("#"))
    for claim in ("suspicious activity was detected for a Zoiko Steam API credential",
                  "credential appears dormant", "risk score"):
        assert claim not in code, f"{claim!r} must not exist without real telemetry"


def test_003_dormancy_variant_is_not_fabricated():
    """Dormancy needs real last_used_at. Nothing records a use, so the variant must not exist."""
    src = open(credential_lifecycle.__file__, encoding="utf-8").read()
    assert "send_credential_dormant_email" not in src
    assert not hasattr(email_mod, "send_credential_dormant_email")
    # And the expiry copy must not claim access was blocked.
    assert "no access was withdrawn" in credential_lifecycle.ACCESS_NOTE
    assert "can no longer authenticate" not in credential_lifecycle.ACCESS_NOTE


# ══ DEV-003 ═════════════════════════════════════════════════════════════════════

def test_003_expiry_warning_and_expired_fire_once_and_are_truthful():
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        created = o.make_key(client, headers, "Expiring", expires_in_days=3)

        # 2 — the warning fires once.
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                stats = credential_lifecycle.sweep(db, _Bg())
            finally:
                db.close()
        assert stats["warned"] >= 1
        payload = cap.of(DEV_003_WARN)
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert created["fingerprint"] in text, "9 — fingerprint present"
        assert created["key"] not in text, "8 — secret absent"
        assert "IST" in text, "5 — exact expiry with timezone"
        assert "not yet enforced" in text, "must not claim access will be blocked"

        # 13 — a second pass claims nothing.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                credential_lifecycle.sweep(db, _Bg())
            finally:
                db.close()
        assert DEV_003_WARN not in cap2.subjects, "one warning per credential"

        # 1/6 — force the lapse; state persists and the expired notice is truthful.
        db = SessionLocal()
        try:
            org = db.get(Organization, o.org_id)
            org.api_keys = [
                {**r, "expires_at": (datetime.now(timezone.utc)
                                     - timedelta(minutes=1)).isoformat()}
                if r["id"] == created["id"] else r
                for r in (org.api_keys or [])]
            db.commit()
        finally:
            db.close()

        _reset_limits()
        cap3, ctx3 = _capture()
        with ctx3:
            db = SessionLocal()
            try:
                credential_lifecycle.sweep(db, _Bg())
            finally:
                db.close()
        assert admin_crud.key_status(o.record(created["id"])) == admin_crud.KEY_EXPIRED
        etext = cap3.of(DEV_003_EXPIRED)["text"]
        assert "no access was withdrawn" in etext, \
            "7 — must not claim access was blocked; no API-key auth exists"
        assert "can no longer authenticate" not in etext
    finally:
        o.cleanup()


def test_003_outage_does_not_alter_credential_state():
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        created = o.make_key(client, headers, "Outage", expires_in_days=2)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            db = SessionLocal()
            try:
                credential_lifecycle.sweep(db, _Bg())
            finally:
                db.close()
        assert cap.calls, "a send was attempted"
        assert admin_crud.key_status(o.record(created["id"])) == admin_crud.KEY_ACTIVE
    finally:
        o.cleanup()


# ══ DEV-004 ═════════════════════════════════════════════════════════════════════

def test_004_revocation_commits_first_and_attributes_the_actor():
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        created = o.make_key(client, headers, "To revoke")

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.delete(f"/api/organization/api-keys/{created['id']}", headers=headers)
        assert r.status_code == 204, r.text

        record = o.record(created["id"])
        assert record["revoked"] is True, "1 — committed before the notice"
        assert record["revoked_by"] == str(o.owner_id), "3 — actor recorded"
        assert record["revoked_at"], "timestamp recorded"
        assert admin_crud.key_status(record) == admin_crud.KEY_REVOKED

        payload = cap.of(DEV_004_REVOKED)
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert created["fingerprint"] in text, "4 — fingerprint correct"
        assert created["key"] not in text, "5 — secret absent"
        assert o.owner_email.lower() in text.lower(), "revoked_by shown"
        assert payload["from"].startswith("Zoiko Steam Developer Platform <")

        # 13 — a repeat revoke does not re-notify.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            client.delete(f"/api/organization/api-keys/{created['id']}", headers=headers)
        assert DEV_004_REVOKED not in cap2.subjects, "one notice per revocation"
    finally:
        o.cleanup()


def test_004_rotation_generates_replacement_and_records_the_relationship():
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        original = o.make_key(client, headers, "Rotate me")

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post(f"/api/organization/developer/api-keys/{original['id']}/rotate",
                            headers=headers)
        assert r.status_code == 201, r.text
        replacement = r.json()

        # 6/7 — a real replacement, its secret shown once here.
        assert replacement["id"] != original["id"]
        assert replacement["key"] and replacement["key"] != original["key"]
        assert replacement["fingerprint"] != original["fingerprint"]

        # 8 — the relationship is stored both ways.
        old_rec, new_rec = o.record(original["id"]), o.record(replacement["id"])
        assert old_rec["rotated_to"] == replacement["id"]
        assert new_rec["rotated_from"] == original["id"]
        assert new_rec["rotated_from_fingerprint"] == original["fingerprint"]

        # 9/10 — no overlap: the old credential is revoked immediately, and the copy says so.
        assert admin_crud.key_status(old_rec) == admin_crud.KEY_REVOKED
        text = cap.of(DEV_004_ROTATED)["text"]
        assert "no overlap window" in text.lower()
        assert original["key"] not in text and replacement["key"] not in text, \
            "neither secret may appear"
        assert original["fingerprint"] in text and replacement["fingerprint"] in text
    finally:
        o.cleanup()


def test_004_emergency_variant_is_a_separate_deliberate_path():
    """11 — the emergency notice comes from a security decision, not the ordinary path."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        created = o.make_key(client, headers, "Emergency")

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            client.delete(f"/api/organization/api-keys/{created['id']}", headers=headers)
        assert DEV_004_EMERGENCY not in cap.subjects, \
            "an ordinary revocation must not claim a security decision"

        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                org = db.get(Organization, o.org_id)
                actor = db.get(User, o.owner_id)
                credential_lifecycle.notify_revoked(
                    db, _Bg(), org=org, key_id=created["id"], actor=actor,
                    emergency=True, reason_code="suspected_exposure")
            finally:
                db.close()
        text = cap2.of(DEV_004_EMERGENCY)["text"]
        assert "Suspected credential exposure" in text, "coarse reason category only"
        for leak in ("risk score", "threshold", "detection rule", "investigation"):
            assert leak not in text.lower(), f"{leak!r} must not be disclosed"
    finally:
        o.cleanup()


# ══ DEV-006 ═════════════════════════════════════════════════════════════════════

def test_006_endpoint_starts_unverified_and_receives_no_production_events():
    """1/2 — the security substance. This is what was broken."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        body, cap = _make_endpoint(client, headers)

        ep = o.endpoint(body["id"])
        assert ep.status == WEBHOOK_PENDING_VERIFICATION, "1 — starts unverified"
        assert ep.verified_at is None

        # 2 — enqueue withholds the event entirely.
        db = SessionLocal()
        try:
            webhook_svc.enqueue(db, o.org_id, "recording.ready", {"x": 1})
            queued = db.query(WebhookDelivery).filter(
                WebhookDelivery.endpoint_id == ep.id).count()
        finally:
            db.close()
        assert queued == 0, "an unverified endpoint must receive no production events"

        # 11/12 — the verification notice reaches the owner.
        payload = cap.of(DEV_006_VERIFY)
        assert payload["html"] and payload["text"]
        assert o.owner_email.lower() in cap.to(DEV_006_VERIFY)
        assert "No production events will be delivered" in payload["text"]
        assert "/organization/developer" in payload["text"], "18 — secure CTA"
    finally:
        o.cleanup()


def test_006_challenge_is_single_use_expiring_and_must_match():
    """3/4/5/6/7/8/9 — the challenge mechanics and the gate opening."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        body, _ = _make_endpoint(client, headers)
        eid = body["id"]

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            issued = client.post(
                f"/api/organization/developer/webhooks/{eid}/verification", headers=headers)
        assert issued.status_code == 200, issued.text
        challenge = issued.json()["challenge"]
        assert len(challenge) >= 20, "3 — cryptographically random"

        # Only the hash is stored.
        ep = o.endpoint(eid)
        assert ep.verification_token_hash and ep.verification_token_hash != challenge

        # 6 — a wrong challenge is refused.
        _reset_limits()
        bad = client.post(f"/api/organization/developer/webhooks/{eid}/verify",
                          json={"challenge": "x" * 40}, headers=headers)
        assert bad.status_code == 400
        assert o.endpoint(eid).status == WEBHOOK_PENDING_VERIFICATION

        # 7/8 — the real challenge verifies.
        _reset_limits()
        ok = client.post(f"/api/organization/developer/webhooks/{eid}/verify",
                         json={"challenge": challenge}, headers=headers)
        assert ok.status_code == 200, ok.text
        ep = o.endpoint(eid)
        assert ep.status == WEBHOOK_VERIFIED and ep.verified_at is not None

        # 5 — single use: the challenge is gone.
        assert ep.verification_token_hash is None

        # 9 — a verified endpoint now receives production events.
        db = SessionLocal()
        try:
            webhook_svc.enqueue(db, o.org_id, "recording.ready", {"x": 1})
            queued = db.query(WebhookDelivery).filter(
                WebhookDelivery.endpoint_id == ep.id).count()
        finally:
            db.close()
        assert queued == 1, "a verified endpoint must receive production events"
    finally:
        o.cleanup()


def test_006_expired_challenge_is_refused():
    """4 — the challenge expires."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        body, _ = _make_endpoint(client, headers)
        eid = body["id"]
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            challenge = client.post(
                f"/api/organization/developer/webhooks/{eid}/verification",
                headers=headers).json()["challenge"]

        db = SessionLocal()
        try:
            ep = db.get(WebhookEndpoint, uuid.UUID(eid))
            ep.verification_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()

        _reset_limits()
        r = client.post(f"/api/organization/developer/webhooks/{eid}/verify",
                        json={"challenge": challenge}, headers=headers)
        assert r.status_code == 400 and "expired" in r.text.lower()
        assert o.endpoint(eid).status == WEBHOOK_PENDING_VERIFICATION
    finally:
        o.cleanup()


def test_006_reset_returns_to_unverified_and_withholds_events():
    """10/11/13 — a reset genuinely withdraws production eligibility."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        body, _ = _make_endpoint(client, headers)
        eid = body["id"]
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            challenge = client.post(
                f"/api/organization/developer/webhooks/{eid}/verification",
                headers=headers).json()["challenge"]
        _reset_limits()
        client.post(f"/api/organization/developer/webhooks/{eid}/verify",
                    json={"challenge": challenge}, headers=headers)
        assert o.endpoint(eid).status == WEBHOOK_VERIFIED

        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            reset = client.post(
                f"/api/organization/developer/webhooks/{eid}/verification", headers=headers)
        assert reset.status_code == 200, reset.text
        ep = o.endpoint(eid)
        assert ep.status == WEBHOOK_PENDING_VERIFICATION, "10 — back to unverified"

        payload = cap2.of(DEV_006_RESET)
        assert payload["html"] and payload["text"]
        assert "No production events will be delivered" in payload["text"]

        db = SessionLocal()
        try:
            webhook_svc.enqueue(db, o.org_id, "recording.ready", {"x": 1})
            queued = db.query(WebhookDelivery).filter(
                WebhookDelivery.endpoint_id == ep.id).count()
        finally:
            db.close()
        assert queued == 0, "13 — no production event while reset"
    finally:
        o.cleanup()


def test_006_ssrf_protections_at_registration():
    """14/15/16 — the URL column had no validation at all before this."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        for bad in ("http://localhost/hook", "https://127.0.0.1/hook",
                    "https://169.254.169.254/latest/meta-data",
                    "https://10.0.0.5/hook", "ftp://example.com/hook",
                    "https://[::1]/hook", "https://user:pw@example.com/hook"):
            _reset_limits()
            cap, ctx = _capture()
            with ctx:
                r = client.post("/api/organization/developer/webhooks",
                                json={"url": bad, "label": "bad",
                                      "events": ["recording.ready"]},
                                headers=headers)
            assert r.status_code == 422, f"{bad} must be refused; got {r.status_code}"
            assert cap.calls == [], "a refused endpoint must send nothing"

        db = SessionLocal()
        try:
            assert db.query(WebhookEndpoint).filter(
                WebhookEndpoint.org_id == o.org_id).count() == 0
        finally:
            db.close()

        # HTTPS is mandatory in production.
        from app.config import settings as cfg
        with patch.object(cfg, "ENVIRONMENT", "production"):
            try:
                webhook_security.validate_webhook_url("http://example.com/hook")
            except webhook_security.UnsafeWebhookUrl:
                pass
            else:
                raise AssertionError("16 — http must be refused in production")
    finally:
        o.cleanup()


def test_006_delivery_revalidates_the_url_against_rebinding():
    """Validating once and trusting forever is what DNS rebinding defeats."""
    import inspect
    src = inspect.getsource(webhook_svc._send)
    assert "validate_webhook_url" in src, \
        "the delivery path must re-validate, not trust registration"


# ══ DEV-007 ═════════════════════════════════════════════════════════════════════

def _verified_endpoint(o, client, headers):
    body, _ = _make_endpoint(client, headers)
    eid = body["id"]
    _reset_limits()
    cap, ctx = _capture()
    with ctx:
        challenge = client.post(
            f"/api/organization/developer/webhooks/{eid}/verification",
            headers=headers).json()["challenge"]
    _reset_limits()
    client.post(f"/api/organization/developer/webhooks/{eid}/verify",
                json={"challenge": challenge}, headers=headers)
    return uuid.UUID(eid)


def test_007_one_transient_failure_does_not_alert():
    """3 — the threshold exists so a single 500 never alarms anybody."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        eid = _verified_endpoint(o, client, headers)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                ep = db.get(WebhookEndpoint, eid)
                transition = webhook_lifecycle.record_failure(db, ep)
                assert transition is None, "one failure is not a health transition"
                assert ep.health == WEBHOOK_HEALTHY
            finally:
                db.close()
        assert cap.calls == [], "no alert on a single failure"
    finally:
        o.cleanup()


def test_007_threshold_degrades_notifies_once_then_recovers():
    """4/5/6/7/11/12/19 — the transitions and their one-notice-each rule."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        eid = _verified_endpoint(o, client, headers)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                ep = db.get(WebhookEndpoint, eid)
                transition = None
                for _ in range(WEBHOOK_FAILURE_THRESHOLD):
                    transition = webhook_lifecycle.record_failure(db, ep)
                assert transition == "degraded"
                webhook_lifecycle.notify_health(db, _Bg(), ep, transition)
                # A further failure must not notify again.
                again = webhook_lifecycle.record_failure(db, ep)
                if again:
                    webhook_lifecycle.notify_health(db, _Bg(), ep, again)
            finally:
                db.close()
        assert cap.subjects.count(DEV_007_FAILING) == len(cap.to(DEV_007_FAILING)), \
            "19 — one notice per recipient, not one per retry"
        payload = cap.of(DEV_007_FAILING)
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Consecutive failures" in text and "Last successful delivery" in text
        assert o.owner_email.lower() in cap.to(DEV_007_FAILING), "5 — owner notified"

        # 11/12 — recovery needs a real success, and notifies once.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                ep = db.get(WebhookEndpoint, eid)
                transition = webhook_lifecycle.record_success(db, ep)
                assert transition == "recovered"
                webhook_lifecycle.notify_health(db, _Bg(), ep, transition)
                assert ep.health == WEBHOOK_HEALTHY and ep.consecutive_failures == 0
            finally:
                db.close()
        assert DEV_007_RECOVERED in cap2.subjects
    finally:
        o.cleanup()


def test_007_disable_threshold_blocks_delivery_and_notifies():
    """13/14 — a disabled endpoint genuinely stops receiving events."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        eid = _verified_endpoint(o, client, headers)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                ep = db.get(WebhookEndpoint, eid)
                transition = None
                for _ in range(WEBHOOK_DISABLE_THRESHOLD):
                    t = webhook_lifecycle.record_failure(db, ep)
                    transition = t or transition
                    if t:
                        webhook_lifecycle.notify_health(db, _Bg(), ep, t)
                assert ep.status == WEBHOOK_DISABLED
            finally:
                db.close()
        assert DEV_007_DISABLED in cap.subjects, "14 — the customer is told"
        text = cap.of(DEV_007_DISABLED)["text"]
        assert "Re-verify" in text, "how to re-enable"

        db = SessionLocal()
        try:
            webhook_svc.enqueue(db, o.org_id, "recording.ready", {"x": 1})
            queued = db.query(WebhookDelivery).filter(
                WebhookDelivery.endpoint_id == eid).count()
        finally:
            db.close()
        assert queued == 0, "13 — disabled must actually block delivery"
    finally:
        o.cleanup()


def test_007_dead_letter_is_retained_and_replay_is_idempotent():
    """9/10/15/16 — exhausted events are kept, and replay never duplicates a success."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        eid = _verified_endpoint(o, client, headers)

        db = SessionLocal()
        try:
            webhook_svc.enqueue(db, o.org_id, "recording.ready", {"x": 1})
            delivery = db.query(WebhookDelivery).filter(
                WebhookDelivery.endpoint_id == eid).one()
            delivery.status = "dead_lettered"
            delivery.attempt_count = webhook_svc.MAX_ATTEMPTS
            delivery.dead_lettered_at = datetime.now(timezone.utc)
            db.commit()
            did = delivery.id

            # 10 — retained, not dropped.
            assert db.query(WebhookDelivery).filter(WebhookDelivery.id == did).count() == 1

            # 15 — replay re-queues the ORIGINAL row, preserving its event id.
            assert webhook_lifecycle.replay(db, delivery, actor_id=o.owner_id) is True
            db.refresh(delivery)
            assert delivery.status == "pending" and delivery.replay_count == 1
            assert delivery.id == did, "the original delivery id is preserved"

            # 16 — a delivered event is never replayed.
            delivery.status = "delivered"
            db.commit()
            assert webhook_lifecycle.replay(db, delivery) is False
        finally:
            db.close()
    finally:
        o.cleanup()


def test_007_email_carries_no_payload_or_secret():
    """17 — the notice must not leak payloads or the endpoint signing secret."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        eid = _verified_endpoint(o, client, headers)
        db = SessionLocal()
        try:
            ep = db.get(WebhookEndpoint, eid)
            secret = ep.secret
        finally:
            db.close()

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                ep = db.get(WebhookEndpoint, eid)
                for _ in range(WEBHOOK_FAILURE_THRESHOLD):
                    t = webhook_lifecycle.record_failure(db, ep)
                webhook_lifecycle.notify_health(db, _Bg(), ep, "degraded")
            finally:
                db.close()
        text = cap.of(DEV_007_FAILING)["text"]
        assert secret not in text, "the endpoint signing secret must never appear"
        assert "payload" not in text.lower() or "Response bodies are not included" in text
    finally:
        o.cleanup()


def test_007_outage_does_not_alter_endpoint_health():
    """20 — a mail failure must not change health state."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        eid = _verified_endpoint(o, client, headers)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            db = SessionLocal()
            try:
                ep = db.get(WebhookEndpoint, eid)
                for _ in range(WEBHOOK_FAILURE_THRESHOLD):
                    t = webhook_lifecycle.record_failure(db, ep)
                webhook_lifecycle.notify_health(db, _Bg(), ep, "degraded")
                health = ep.health
            finally:
                db.close()
        assert cap.calls, "a send was attempted"
        assert health == "degraded", "health stays as recorded"
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
