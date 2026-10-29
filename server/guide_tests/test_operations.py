"""Production operations: KMS keys, scheduled maintenance, live public status (D1) and
independent capability switches (Security §9/§17, ARCH §12.1/§19, CF §17, FE-10)."""
import base64
import uuid
from datetime import timedelta

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

from app.guide import crypto, orchestrator, registry, retention, scheduler
from app.guide import status as public_status
from app.guide.db import get_engine
from app.guide.models import GuideExternalOperation
from app.guide.settings import get_settings
from conftest import create_session

KEY_NAME = "projects/p/locations/l/keyRings/r/cryptoKeys/guide"

# ── Cloud KMS key provider ───────────────────────────────────────────────────────────

FAKE_KMS_KEY = AESGCM(b"k" * 32)


def _fake_kms_encrypt(raw: bytes) -> str:
    nonce = b"n" * 12
    return base64.b64encode(nonce + FAKE_KMS_KEY.encrypt(nonce, raw, None)).decode()


def _fake_kms_decrypt(key_name, ciphertext):
    assert key_name == KEY_NAME
    return FAKE_KMS_KEY.decrypt(ciphertext[:12], ciphertext[12:], None)


@pytest.fixture
def kms(monkeypatch):
    monkeypatch.setattr(crypto, "_kms_decrypt", _fake_kms_decrypt)

    def use(current: bytes, previous=()):
        monkeypatch.setenv("GUIDE_KEY_PROVIDER", "gcp-kms")
        monkeypatch.setenv("GUIDE_KMS_KEY_NAME", KEY_NAME)
        monkeypatch.setenv("GUIDE_KMS_KEYSET", _fake_kms_encrypt(current))
        monkeypatch.setenv("GUIDE_KMS_PREVIOUS_KEYSETS",
                           ",".join(_fake_kms_encrypt(p) for p in previous))
        get_settings.cache_clear()
        crypto.provider.cache_clear()
    return use


def test_kms_round_trip_and_key_id(kms):
    kms(b"A" * 64)
    blob = crypto.encrypt("hello", context="guide_messages.content")
    assert b"hello" not in blob and b"kms-" in blob
    assert crypto.decrypt(blob, context="guide_messages.content") == "hello"


def test_kms_rotation_keeps_old_rows_readable(kms):
    kms(b"A" * 64)
    old = crypto.encrypt("before rotation", context="c")
    kms(b"B" * 64, previous=[b"A" * 64])
    assert crypto.decrypt(old, context="c") == "before rotation"
    new = crypto.encrypt("after rotation", context="c")
    kms(b"B" * 64)  # old key set retired: new rows still read, old rows no longer do
    assert crypto.decrypt(new, context="c") == "after rotation"
    with pytest.raises(crypto.CryptoUnavailable):
        crypto.decrypt(old, context="c")


def test_kms_misconfiguration_fails_closed(kms, monkeypatch):
    kms(b"short")
    with pytest.raises(crypto.CryptoUnavailable, match="wrong length"):
        crypto.encrypt("x", context="c")
    monkeypatch.setenv("GUIDE_KMS_KEYSET", "not base64 !!")
    get_settings.cache_clear()
    crypto.provider.cache_clear()
    with pytest.raises(crypto.CryptoUnavailable, match="could not be decrypted"):
        crypto.encrypt("x", context="c")


def test_production_accepts_kms_and_refuses_local(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("GUIDE_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GUIDE_ALLOWED_ORIGINS", "https://www.zoikostream.com")
    monkeypatch.setenv("GUIDE_KEY_PROVIDER", "local")
    get_settings.cache_clear()
    assert any("local is not permitted" in p for p in get_settings().production_problems())
    monkeypatch.setenv("GUIDE_KEY_PROVIDER", "gcp-kms")
    monkeypatch.setenv("GUIDE_KMS_KEY_NAME", KEY_NAME)
    monkeypatch.setenv("GUIDE_KMS_KEYSET", "Y2lwaGVydGV4dA==")
    get_settings.cache_clear()
    assert not any("key" in p.lower() for p in get_settings().production_problems())


# ── Scheduled maintenance ────────────────────────────────────────────────────────────

def _submitting_flow(g, db, age):
    flow = g.post("/lead-flows", {"flow_kind": "demo"}).json()
    fid = uuid.UUID(flow["flow_id"])
    db.execute(text("UPDATE guide_lead_flows SET status = 'submitting' WHERE flow_id = :f"),
               {"f": fid})
    db.add(GuideExternalOperation(
        session_id=uuid.UUID(g.session_id), operation_type="lead_submit", subject_id=fid,
        idempotency_key_hash=uuid.uuid4().bytes, request_fingerprint=b"f",
        outcome="in_progress", attempt_count=1,
        purge_after=retention.now() + timedelta(days=1)))
    db.commit()
    db.execute(text("UPDATE guide_external_operations SET created_at = now() - :a "
                    "WHERE subject_id = :f"), {"a": age, "f": fid})
    db.commit()
    return fid


def test_stale_in_progress_submission_becomes_unknown(guide, db):
    stale = _submitting_flow(guide, db, timedelta(hours=1))
    fresh = _submitting_flow(guide, db, timedelta(minutes=1))
    assert scheduler.reconcile_stale_operations(db) == 1
    status = dict(db.execute(text("SELECT flow_id, status FROM guide_lead_flows")).all())
    assert status[stale] == "unknown" and status[fresh] == "submitting"
    outcome = db.execute(text("SELECT outcome FROM guide_external_operations "
                              "WHERE subject_id = :f"), {"f": stale}).scalar()
    assert outcome == "unknown"  # never "confirmed": silence is not proof of delivery


def test_tick_runs_once_under_the_advisory_lock(guide, db):
    db.execute(text("UPDATE guide_sessions SET purge_after = now() - interval '1 hour'"))
    db.commit()
    result = scheduler.tick()
    assert result["purged"].get("sessions") == 1 and result["backlog"] == {}
    with get_engine().connect() as other:  # another process holds the lock -> this one skips
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": scheduler.ADVISORY_LOCK_KEY})
        try:
            assert scheduler.tick() == {}
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:k)"),
                          {"k": scheduler.ADVISORY_LOCK_KEY})
            other.commit()


def test_backlog_is_detected(guide, db):
    db.execute(text("UPDATE guide_sessions SET purge_after = now() - interval '2 days'"))
    db.commit()
    assert scheduler.retention_backlog(db) == {"guide_sessions": 1}


def test_scheduler_does_not_start_when_disabled():
    assert scheduler.start() is False  # GUIDE_MAINTENANCE_ENABLED=false in the test env


# ── D1 public status ─────────────────────────────────────────────────────────────────

FEED = {"overall": "degraded", "overall_label": "Degraded performance",
        "active_incidents": [{"title": "Playback delays in EU", "status": "identified",
                              "internal_incident_id": "should-never-surface"}],
        "scheduled_maintenance": []}


@pytest.fixture
def status_feed(monkeypatch):
    monkeypatch.setenv("GUIDE_STATUS_ENDPOINT", "http://status.invalid/api/status")
    get_settings.cache_clear()
    monkeypatch.setattr(public_status, "_cache", None)

    def serve(body=None, exc=None):
        def fake_get(url, **kw):
            if exc:
                raise exc
            return httpx.Response(200, json=body)
        monkeypatch.setattr(httpx, "get", fake_get)
    return serve


def _ask(g, text_):
    res = g.post("/messages", {"client_message_id": str(uuid.uuid4()), "text": text_})
    assert res.status_code == 200, res.text
    return res.json()["assistant_message"]


def test_status_answer_uses_the_live_public_feed(guide, status_feed):
    status_feed(FEED)
    msg = _ask(guide, "Is ZoikoStream down?")
    assert msg["text"].startswith("The public status page currently reports: "
                                  "Degraded performance")
    assert "Playback delays in EU (identified)" in msg["text"]
    assert "should-never-surface" not in msg["text"]
    assert msg["sources"][0]["source_id"] == "public-status"


def test_status_unreachable_says_it_cannot_verify(guide, status_feed):
    status_feed(exc=httpx.ConnectTimeout("t"))
    assert _ask(guide, "Is ZoikoStream down?")["text"] == orchestrator.STATUS_UNKNOWN


# ── Independent capability switches ──────────────────────────────────────────────────

def _switch(db, feature, enabled):
    db.execute(text("INSERT INTO guide_runtime_config (config_key, config_value, version) "
                    "VALUES (:k, CAST(:v AS jsonb), 1) ON CONFLICT (config_key) DO UPDATE "
                    "SET config_value = EXCLUDED.config_value"),
               {"k": f"guide.feature.{feature}",
                "v": '{"enabled": %s}' % ("true" if enabled else "false")})
    db.commit()
    registry.invalidate()


def test_leads_switch_hides_lead_actions_and_refuses_flows(guide, db):
    _switch(db, "leads", False)
    assert guide.post("/lead-flows", {"flow_kind": "demo"}).status_code == 503
    assert registry.action_for(db, "request-demo") is None
    assert registry.action_for(db, "contact-sales") is not None  # plain routes stay


def test_viewer_support_switch(client, db):
    _switch(db, "viewer_support", False)
    g = create_session(client, page_id="viewer", page_type="viewer", path="/watch")
    assert g.post("/support-flows", {"flow_type": "playback"}).status_code == 503
    assert registry.action_for(db, "viewer-help") is None


def test_env_switch_disables_a_feature(monkeypatch, db):
    monkeypatch.setenv("GUIDE_DISABLED_FEATURES", "generation, leads")
    get_settings.cache_clear()
    assert not registry.feature_enabled(db, "generation")
    assert not registry.feature_enabled(db, "leads")
    assert registry.feature_enabled(db, "viewer_support")
