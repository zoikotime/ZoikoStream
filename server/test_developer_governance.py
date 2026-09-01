"""DEV-008 → DEV-012 — developer-platform governance (ZST-EC-001).

DEV-009 and DEV-011 assert absences. There is no API versioning, no SDK identification and
no per-application usage telemetry, so a retirement notice would have no observed users to
target; and there is no integration domain beyond webhook endpoints, which DEV-007 already
covers. Building either from email alone is what these tests exist to prevent.

Run with `python test_developer_governance.py` (or pytest).
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
    EXPORT_CREDENTIALS,
    EXPORT_EXPIRED,
    EXPORT_FAILED,
    EXPORT_READY,
    EXPORT_WEBHOOKS,
    RATE_LIMIT_EVENT_THRESHOLD,
    AccountRecovery,
    AuditLog,
    DeveloperDataExport,
    ElevationSession,
    IdentityChallenge,
    Organization,
    OrgMembershipEvent,
    RateLimitEvent,
    SignInEvent,
    StepUpGrant,
    SupportAccessRequest,
    User,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.security import hash_password
from app.services import api_usage, developer_export, signing_rotation
from app.services import webhooks as webhook_svc

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
PUBLIC_URL = "https://example.com/hooks/zoiko"

DEV_008_STARTED = email_mod.DEV_008_STARTED_SUBJECT
DEV_008_ENDING = email_mod.DEV_008_ENDING_SUBJECT
DEV_010 = email_mod.DEV_010_SUBJECT
DEV_012_READY = email_mod.DEV_012_READY_SUBJECT
DEV_012_EXPIRED = email_mod.DEV_012_EXPIRED_SUBJECT
DEV_012_FAILED = email_mod.DEV_012_FAILED_SUBJECT


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


def _new_email(tag="devg"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class Org:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"DevG Co {uuid.uuid4().hex[:6]}", status="active",
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

    def verified_endpoint(self, client, headers):
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/developer/webhooks",
                            json={"url": PUBLIC_URL, "label": "hook",
                                  "events": ["recording.ready"]}, headers=headers)
        assert r.status_code in (200, 201), r.text
        eid = r.json()["id"]
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            challenge = client.post(
                f"/api/organization/developer/webhooks/{eid}/verification",
                headers=headers).json()["challenge"]
        _reset_limits()
        client.post(f"/api/organization/developer/webhooks/{eid}/verify",
                    json={"challenge": challenge}, headers=headers)
        return uuid.UUID(eid)

    def endpoint(self, eid):
        db = SessionLocal()
        try:
            return db.get(WebhookEndpoint, eid)
        finally:
            db.close()

    def exports(self):
        db = SessionLocal()
        try:
            return db.query(DeveloperDataExport).filter(
                DeveloperDataExport.org_id == self.org_id).all()
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(DeveloperDataExport).filter(
                DeveloperDataExport.org_id == self.org_id).delete()
            db.query(RateLimitEvent).filter(RateLimitEvent.org_id == self.org_id).delete()
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


# ══ DEV-009 / DEV-011 — proof of absence ════════════════════════════════════════

def test_009_no_api_versioning_or_usage_attribution_exists():
    """No versioning and no per-application usage means no observed users to notify."""
    import pathlib
    hits = []
    for path in pathlib.Path("app").rglob("*.py"):
        code = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        for marker in ("class ApiRetirement", "api_version", "sdk_version",
                       "last_observed_use"):
            if marker in code:
                hits.append(f"{path}:{marker}")
    assert not hits, f"API governance appeared; DEV-009 must be revisited: {hits}"

    routes = [getattr(r, "path", "") for r in m.app.routes]
    assert not any(p.startswith("/api/v1") or p.startswith("/api/v2") for p in routes), \
        "versioned routes appeared; DEV-009 must be revisited"


def test_009_no_retirement_broadcast_template_exists():
    """A retirement blast to every Organization is exactly what must not be built."""
    code = "\n".join(l for l in open(email_mod.__file__, encoding="utf-8").read().splitlines()
                     if not l.lstrip().startswith("#"))
    for claim in ("is being retired", "migration guide", "SDK version"):
        assert claim not in code, f"{claim!r} must not exist without observed usage"


def test_011_no_general_integration_domain_exists():
    """Webhook endpoints are DEV-007's concern; there is no broader integration domain."""
    from app import models
    for name in ("Integration", "ConnectedAccount", "OAuthConnection", "ProviderAuth"):
        assert not hasattr(models, name), f"{name} exists; DEV-011 must be revisited"

    code = "\n".join(l for l in open(email_mod.__file__, encoding="utf-8").read().splitlines()
                     if not l.lstrip().startswith("#"))
    for claim in ("reconnect your Zoiko Steam integration", "integration health is degraded",
                  "Zoiko Steam integration recovered"):
        assert claim not in code, \
            f"{claim!r} must not exist without an authoritative integration domain"


# ══ DEV-008 ═════════════════════════════════════════════════════════════════════

def test_008_existing_signing_is_hmac_and_rotation_builds_on_it():
    """1 — the audit: signing already existed, so rotation extends it rather than inventing."""
    assert callable(webhook_svc.sign)
    assert webhook_svc.sign("s", 1, b"x") == webhook_svc.sign("s", 1, b"x")
    assert webhook_svc.sign("s", 1, b"x") != webhook_svc.sign("t", 1, b"x")
    # Backward-compatible header shape.
    assert webhook_svc.signature_header([(1, "s")], 100, b"{}").startswith("t=100,v1=")


def test_008_rotation_emits_both_signatures_during_the_window_only():
    """6/7/8 — the overlap is real: both secrets sign, then the old one stops."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        eid = o.verified_endpoint(client, headers)
        original = o.endpoint(eid).secret

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post(f"/api/organization/developer/webhooks/{eid}/rotate-secret",
                            headers=headers)
        assert r.status_code == 200, r.text
        new_secret = r.json()["secret"]
        assert new_secret != original, "3 — a genuinely new secret"

        ep = o.endpoint(eid)
        assert ep.previous_secret == original and ep.secret == new_secret
        assert ep.secret_version == 2

        # 7 — during the window BOTH secrets sign.
        active = webhook_svc.active_secrets(ep)
        assert len(active) == 2
        header = webhook_svc.signature_header(active, 100, b"{}")
        assert header.count("v1=") == 2
        assert webhook_svc.sign(original, 100, b"{}") in header, \
            "the old secret must still verify during the overlap"
        assert webhook_svc.sign(new_secret, 100, b"{}") in header

        # 8 — after the deadline the old secret stops signing.
        db = SessionLocal()
        try:
            row = db.get(WebhookEndpoint, eid)
            row.rotation_ends_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
            closed = signing_rotation.sweep(db, _Bg())
            db.refresh(row)
            assert closed["closed"] >= 1
            assert row.previous_secret is None, "the retired secret is cleared"
            after = webhook_svc.signature_header(webhook_svc.active_secrets(row), 100, b"{}")
            assert after.count("v1=") == 1
            assert webhook_svc.sign(original, 100, b"{}") not in after, \
                "the old secret must stop signing after the deadline"
        finally:
            db.close()
    finally:
        o.cleanup()


def test_008_emails_carry_fingerprints_never_secrets():
    """4/5/10/11 — fingerprints only, correct recipients, both parts."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        eid = o.verified_endpoint(client, headers)
        original = o.endpoint(eid).secret

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post(f"/api/organization/developer/webhooks/{eid}/rotate-secret",
                            headers=headers)
        new_secret = r.json()["secret"]

        payload = cap.of(DEV_008_STARTED)
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert new_secret not in text and original not in text, \
            "5 — neither secret may appear"
        assert signing_rotation.fingerprint(new_secret) in text, "4 — new fingerprint"
        assert signing_rotation.fingerprint(original) in text, "4 — old fingerprint"
        assert o.owner_email.lower() in cap.to(DEV_008_STARTED), "10 — owner notified"
        assert payload["from"].startswith("Zoiko Steam Developer Platform <")
        assert "IST" in text, "exact overlap end with timezone"

        # 9/13 — the ending notice fires once.
        db = SessionLocal()
        try:
            row = db.get(WebhookEndpoint, eid)
            row.rotation_ends_at = datetime.now(timezone.utc) + timedelta(hours=2)
            db.commit()
        finally:
            db.close()
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                signing_rotation.sweep(db, _Bg())
                signing_rotation.sweep(db, _Bg())
            finally:
                db.close()
        assert cap2.subjects.count(DEV_008_ENDING) == len(cap2.to(DEV_008_ENDING)), \
            "9/13 — one ending notice per recipient, not one per sweep"
        etext = cap2.of(DEV_008_ENDING)["text"]
        assert new_secret not in etext and original not in etext
    finally:
        o.cleanup()


# ══ DEV-010 ═════════════════════════════════════════════════════════════════════

def test_010_counter_backend_is_shared_and_atomic():
    """1/3 — the counter is Redis-backed, so workers share it and increments are atomic."""
    principal = f"test-{uuid.uuid4().hex[:8]}"
    try:
        assert api_usage.backend() == "redis", \
            "DEV-010 requires a shared backend; in-memory only would keep it PARTIAL"
        counts = [api_usage.record_refusal(principal, "login") for _ in range(5)]
        assert counts == [1, 2, 3, 4, 5], f"atomic increments expected; got {counts}"
        reset = api_usage.window_reset_at(principal, "login")
        assert reset > datetime.now(timezone.utc), "8 — reset time is in the future"
    finally:
        api_usage.clear(principal, "login")


def test_010_one_refusal_does_not_create_an_event():
    """4 — a single 429 must never alert."""
    o = Org()
    principal = f"test-{uuid.uuid4().hex[:8]}"
    try:
        db = SessionLocal()
        try:
            event = api_usage.open_event(db, principal=principal, rule="login",
                                         observed=1, org_id=o.org_id)
            assert event is None, "one refusal is not a governed event"
        finally:
            db.close()
    finally:
        api_usage.clear(principal, "login")
        o.cleanup()


def test_010_sustained_breach_creates_one_event_and_notifies_once():
    """5/6/7/9 — durable event, correct recipients, deduped, no internals leaked."""
    o = Org()
    principal = f"test-{uuid.uuid4().hex[:8]}"
    try:
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                event = api_usage.open_event(
                    db, principal=principal, rule="login",
                    observed=RATE_LIMIT_EVENT_THRESHOLD + 5, org_id=o.org_id)
                assert event is not None, "5 — a sustained breach creates a durable event"
                assert api_usage.notify(db, _Bg(), event) is True

                # 7 — a continuing breach reuses the open event and does not re-notify.
                again = api_usage.open_event(
                    db, principal=principal, rule="login",
                    observed=RATE_LIMIT_EVENT_THRESHOLD + 20, org_id=o.org_id)
                assert again.id == event.id, "one incident, not one per request"
                assert api_usage.notify(db, _Bg(), again) is False
            finally:
                db.close()

        payload = cap.of(DEV_010)
        assert payload["html"] and payload["text"]
        assert o.owner_email.lower() in cap.to(DEV_010), "6 — org admins notified"
        text = payload["text"]
        # 9 - no internal detection logic. Checked against the THRESHOLD ROW rather
        # than scanning the whole body for the digits: a timestamp legitimately
        # contains them, and an assertion that fails on "10:25 PM" tests nothing.
        threshold_row = [l for l in text.splitlines()
                         if l.startswith("Threshold category:")]
        assert threshold_row, "the threshold is reported as a category row"
        assert not any(ch.isdigit() for ch in threshold_row[0]), (
            f"the exact anti-abuse threshold must not be disclosed: {threshold_row[0]}")
        for leak in ("risk score", "detection rule", "sliding window", "_HITS"):
            assert leak not in text.lower()
        assert "Sustained rate-limited requests" in text, "a category, not a number"
        assert "IST" in text, "8 — reset time with timezone"
    finally:
        api_usage.clear(principal, "login")
        o.cleanup()


def test_010_email_failure_does_not_affect_rate_limiting():
    """10 — the limiter is independent of the notifier."""
    o = Org()
    principal = f"test-{uuid.uuid4().hex[:8]}"
    try:
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            db = SessionLocal()
            try:
                event = api_usage.open_event(db, principal=principal, rule="login",
                                             observed=RATE_LIMIT_EVENT_THRESHOLD + 1,
                                             org_id=o.org_id)
                api_usage.notify(db, _Bg(), event)
                db.refresh(event)
                assert event.status == "open", "the governed event stands"
            finally:
                db.close()
        assert cap.calls, "a send was attempted"
        assert api_usage.record_refusal(principal, "login") >= 1, "counting still works"
    finally:
        api_usage.clear(principal, "login")
        o.cleanup()


# ══ DEV-012 ═════════════════════════════════════════════════════════════════════

def test_012_unauthorized_member_cannot_export():
    """1 — bulk developer metadata is not a member-level read."""
    o = Org()
    client = TestClient(m.app)
    try:
        member = o.token(o.member_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/developer/exports",
                            json={"export_type": EXPORT_CREDENTIALS}, headers=member)
        assert r.status_code == 403, f"an ordinary member must be refused; got {r.status_code}"
        assert cap.calls == []
        assert o.exports() == []
    finally:
        o.cleanup()


def test_012_full_lifecycle_ready_download_and_expiry():
    """2/3/4/6/7/8/9/12/13/14 — the governed lifecycle end to end."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        o.verified_endpoint(client, headers)

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/developer/exports",
                            json={"export_type": EXPORT_WEBHOOKS}, headers=headers)
        assert r.status_code == 202, r.text
        export_id = r.json()["id"]

        exports = o.exports()
        assert len(exports) == 1, "2 — a server-side record exists"
        export = exports[0]
        assert export.status == EXPORT_READY, "4 — processed and stored"
        assert export.object_key and export.download_token_hash
        assert export.requested_by_email == o.owner_email.lower(), "13 — requester recorded"

        payload = cap.of(DEV_012_READY)
        assert payload["html"] and payload["text"], "14 — both parts"
        text = payload["text"]
        assert "IST" in text, "exact expiry with timezone"
        token = text.split("token=")[1].split()[0].strip()

        # 5 — no secrets in the exported content.
        content = developer_export.load(export)
        db = SessionLocal()
        try:
            ep = db.query(WebhookEndpoint).filter(
                WebhookEndpoint.org_id == o.org_id).first()
            assert ep.secret not in content, "the signing secret must never be exported"
        finally:
            db.close()
        assert "secret" not in content.lower().split("\n")[0], "no secret column"

        # 6/12 — the download works once and is logged.
        _reset_limits()
        got = client.get(
            f"/api/organization/developer/exports/{export_id}/download?token={token}",
            headers=headers)
        assert got.status_code == 200, got.text
        assert "endpoint_id" in got.text
        db = SessionLocal()
        try:
            row = db.get(DeveloperDataExport, uuid.UUID(export_id))
            assert row.download_count == 1 and row.access_log, "12 — the download is logged"
            assert row.last_downloaded_by == o.owner_id
        finally:
            db.close()

        # 7/8 — expiry genuinely stops the link.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            db = SessionLocal()
            try:
                row = db.get(DeveloperDataExport, uuid.UUID(export_id))
                row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
                db.commit()
                developer_export.sweep(db, _Bg())
                db.refresh(row)
                assert row.status == EXPORT_EXPIRED
                assert row.download_token_hash is None, \
                    "the old token is cleared, not resurrectable"
            finally:
                db.close()
        assert DEV_012_EXPIRED in cap2.subjects, "10 — the expiry notice"

        _reset_limits()
        dead = client.get(
            f"/api/organization/developer/exports/{export_id}/download?token={token}",
            headers=headers)
        assert dead.status_code == 403, "8 — an expired link must be rejected"
    finally:
        o.cleanup()


def test_012_bad_token_is_rejected_and_not_logged_as_a_download():
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/organization/developer/exports",
                            json={"export_type": EXPORT_CREDENTIALS}, headers=headers)
        export_id = r.json()["id"]

        _reset_limits()
        bad = client.get(
            f"/api/organization/developer/exports/{export_id}/download?token={'x' * 40}",
            headers=headers)
        assert bad.status_code == 403
        db = SessionLocal()
        try:
            row = db.get(DeveloperDataExport, uuid.UUID(export_id))
            assert row.download_count == 0, \
                "a rejected attempt is not a download and must not be logged as one"
        finally:
            db.close()
    finally:
        o.cleanup()


def test_012_storage_failure_marks_failed_not_ready():
    """11/16 — a generation failure never leaves an export claiming to be READY."""
    o = Org()
    client = TestClient(m.app)
    try:
        headers = o.token()
        _reset_limits()
        cap, ctx = _capture()
        with ctx, patch.object(developer_export, "_store",
                               side_effect=RuntimeError("storage down")):
            r = client.post("/api/organization/developer/exports",
                            json={"export_type": EXPORT_CREDENTIALS}, headers=headers)
        assert r.status_code == 202, r.text

        export = o.exports()[0]
        assert export.status == EXPORT_FAILED, "16 — never falsely READY"
        assert export.download_token_hash is None
        payload = cap.of(DEV_012_FAILED)
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Export storage was unavailable" in text, "a safe failure category"
        for leak in ("Traceback", "storage down", "developer-exports/", "RuntimeError"):
            assert leak not in text, f"{leak!r} must not be disclosed"
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
