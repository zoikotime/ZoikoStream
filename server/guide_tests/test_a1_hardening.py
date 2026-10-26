"""Security/privacy hardening (Agent 1): consent receipts outlive the session (SEC §16),
trusted client IP (SEC §21, ARCH §6.1), shared + runtime-tunable rate limits, RATE_LIMITED
state (CF §23, CF-AT-32), streamed body limit (SP-AT-32), security headers (SEC App. A),
purge backlog / dead-letter (SP-AT-16/17, ARCH §12.2), retention config, keyed hashing,
capability rotation (SEC §7), privacy-rights CLI (SP-AT-26), DB principals (ERD §19),
logging leak canary (SP-AT-14) and environment separation (RBAC §18)."""
import json
import logging
import socket
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError
from starlette.requests import Request

from app.guide import (
    adapters, crypto, leads, metrics, privacy, purge, ratelimit, registry, retention, scheduler,
    sessions,
)
from app.guide.db import get_engine
from app.guide.models import (
    GuideAuditEvent, GuideConsent, GuideExternalOperation, GuideLeadFieldValue, GuideLeadFlow,
    GuideMessage, GuideSession,
)
from app.guide.settings import get_settings
from conftest import BASE, ORIGIN, create_session, page_context
from test_leads import VALID, FakeAdapter, consent, fill, start, submit

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def rate_limits(db):
    """Set the runtime `guide.rate_limits` row; removed afterwards."""
    def set_(values: dict):
        db.execute(text("INSERT INTO guide_runtime_config (config_key, config_value, version) "
                        "VALUES ('guide.rate_limits', CAST(:v AS jsonb), 1) ON CONFLICT (config_key) DO "
                        "UPDATE SET config_value = EXCLUDED.config_value"),
                   {"v": json.dumps(values)})
        db.commit()
        registry.invalidate()
    yield set_
    db.execute(text("DELETE FROM guide_runtime_config WHERE config_key = 'guide.rate_limits'"))
    db.commit()
    registry.invalidate()


# ── 1. Consent receipts survive the session ────────────────────────────────────────────────

def test_consent_receipt_survives_accelerated_session_purge(guide, db):
    receipt = consent(guide)["receipt_id"]
    assert guide.post("/end").status_code == 204
    db.execute(text("UPDATE guide_sessions SET purge_after = now() - interval '1 hour'"))
    db.commit()
    counts = purge.purge_once()
    assert counts.get("sessions") == 1 and "consents" not in counts
    row = db.scalar(select(GuideConsent).where(GuideConsent.receipt_ref == receipt))
    assert row is not None and row.session_id is None  # de-linked, not deleted
    assert (row.purge_after - retention.now()).days >= 700  # 24-month baseline
    # ...and goes by its own deadline.
    db.execute(text("UPDATE guide_consents SET purge_after = now() - interval '1 hour'"))
    db.commit()
    assert purge.purge_once().get("consents") == 1


def test_long_lived_rows_are_set_null_not_cascade(db):
    rules = dict(db.execute(text(
        "SELECT cl.relname, c.confdeltype FROM pg_constraint c "
        "JOIN pg_class cl ON cl.oid = c.conrelid JOIN pg_class p ON p.oid = c.confrelid "
        "WHERE c.contype = 'f' AND p.relname = 'guide_sessions'")).all())
    # Outlive the 30-day session: consent 24m, feedback 12m, audit 12m, ops reconciliation.
    for table in ("guide_consents", "guide_feedback", "guide_audit_events",
                  "guide_external_operations"):
        assert rules[table] == "n", table
    for table in ("guide_messages", "guide_lead_flows", "guide_handoffs",
                  "guide_support_flows", "guide_session_context_versions",
                  "guide_public_event_contexts"):
        assert rules[table] == "c", table


# ── 2. Client IP ───────────────────────────────────────────────────────────────────────────

def _req(peer="198.51.100.9", xff=None):
    headers = [(b"x-forwarded-for", v.encode()) for v in ([xff] if isinstance(xff, str)
                                                         else xff or [])]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers,
                    "client": (peer, 1234), "query_string": b""})


def test_client_ip_ignores_forwarded_for_by_default():
    assert ratelimit.client_ip(_req(xff="203.0.113.5")) == "198.51.100.9"
    assert ratelimit.client_key(_req(xff="1.1.1.1")) == ratelimit.client_key(_req(xff="2.2.2.2"))


def test_client_ip_takes_nth_from_right_with_trusted_hops(monkeypatch):
    monkeypatch.setenv("GUIDE_TRUSTED_PROXY_HOPS", "1")
    get_settings.cache_clear()
    # The left-most entry is client-written and never trusted.
    assert ratelimit.client_ip(_req(xff="6.6.6.6, 203.0.113.5")) == "203.0.113.5"
    assert ratelimit.client_ip(_req(xff=["6.6.6.6", "203.0.113.5"])) == "203.0.113.5"
    monkeypatch.setenv("GUIDE_TRUSTED_PROXY_HOPS", "2")
    get_settings.cache_clear()
    assert ratelimit.client_ip(_req(xff="6.6.6.6, 203.0.113.5, 10.0.0.1")) == "203.0.113.5"
    # Fewer entries than proxies: the header did not come through them -> socket peer.
    assert ratelimit.client_ip(_req(xff="203.0.113.5")) == "198.51.100.9"
    # Not an IP (header injection, garbage) -> socket peer; never raw header text.
    assert ratelimit.client_ip(_req(xff="evil\r\nX: y, 10.0.0.1")) == "198.51.100.9"
    assert ratelimit.client_ip(_req(xff="[2001:db8::1]:443, 10.0.0.1")) == "2001:db8::1"


def test_lead_submit_forwards_only_the_validated_ip(guide):
    seen = {}

    class Capture(FakeAdapter):
        def submit(self, payload, *, visitor_ip, server=None):
            seen["ip"] = visitor_ip
            return super().submit(payload, visitor_ip=visitor_ip, server=server)
    adapters.set_lead_adapter(Capture("confirmed"))
    flow = start(guide)
    fill(guide, flow, VALID)
    res = guide.post(f"/lead-flows/{flow['flow_id']}/submit",
                     {"consent_receipt_id": consent(guide)["receipt_id"]},
                     headers={"Idempotency-Key": str(uuid.uuid4()),
                              "X-Forwarded-For": "6.6.6.6"})
    assert res.status_code == 200
    assert seen["ip"] is None  # TestClient peer "testclient" is not an IP; XFF untrusted


# ── 3. Shared + runtime-changeable limits ──────────────────────────────────────────────────

def _redis_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 6379), timeout=0.2):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _redis_up(), reason="no local Redis")
def test_redis_backend_is_shared_across_workers(monkeypatch):
    monkeypatch.setenv("GUIDE_REDIS_URL", "redis://127.0.0.1:6379/15")
    get_settings.cache_clear()
    ratelimit.reset_all()
    key = f"test:{uuid.uuid4()}"
    assert ratelimit.backend() == "redis"
    assert [ratelimit.hit(key, 2).allowed for _ in range(2)] == [True, True]
    ratelimit._BUCKETS.clear()  # another worker: no local memory, same Redis counters
    verdict = ratelimit.hit(key, 2)
    assert not verdict.allowed and verdict.reset_after >= 1


def test_redis_outage_falls_back_to_memory_with_metric(monkeypatch):
    monkeypatch.setenv("GUIDE_REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens
    monkeypatch.setenv("GUIDE_REDIS_TIMEOUT_SECONDS", "0.1")
    get_settings.cache_clear()
    ratelimit.reset_all()
    before = metrics.snapshot_counter("guide_ratelimit_fallback_total", reason="redis_error")
    key = f"test:{uuid.uuid4()}"
    assert [ratelimit.hit(key, 1).allowed for _ in range(2)] == [True, False]
    assert metrics.snapshot_counter("guide_ratelimit_fallback_total",
                                    reason="redis_error") == before + 1
    assert ratelimit.backend() == "memory"  # backing off; no per-request timeouts


def test_runtime_config_overrides_env_limit(client, rate_limits):
    rate_limits({"session_create": 1})
    codes = [client.post(f"{BASE}/sessions", json={
        "client_session_nonce": str(uuid.uuid4()), "page_context": page_context()},
        headers={"Origin": ORIGIN}).status_code for _ in range(2)]
    assert codes == [201, 429]


# ── 4. RATE_LIMITED session state ──────────────────────────────────────────────────────────

def test_rate_limited_state_and_recovery(guide, db, rate_limits):  # CF-AT-32
    rate_limits({"session": 2})
    ctx = {"page_context": page_context()}
    assert [guide.patch("/context", ctx).status_code for _ in range(3)] == [200, 200, 429]
    db.expire_all()
    assert db.get(GuideSession, uuid.UUID(guide.session_id)).state == "RATE_LIMITED"
    assert db.scalar(select(func.count()).select_from(GuideAuditEvent).where(
        GuideAuditEvent.event_type == "session_rate_limited")) == 1
    ratelimit.reset_all()  # the window opens
    assert guide.patch("/context", ctx).status_code == 200
    db.expire_all()
    assert db.get(GuideSession, uuid.UUID(guide.session_id)).state == "ACTIVE"


def test_rate_limit_recovery_returns_to_paused_flow(guide, db, rate_limits):
    start(guide)  # session -> LEAD_FLOW
    rate_limits({"session": 1})
    ctx = {"page_context": page_context()}
    assert guide.patch("/context", ctx).status_code == 429
    ratelimit.reset_all()
    assert guide.patch("/context", ctx).status_code == 200
    db.expire_all()
    assert db.get(GuideSession, uuid.UUID(guide.session_id)).state == "LEAD_FLOW"


# ── 5/6. Body limit and headers ────────────────────────────────────────────────────────────

def test_chunked_body_over_limit_is_413_without_content_length(client):  # SP-AT-32
    def chunks():
        for _ in range(80):
            yield b"x" * 1024
    res = client.post(f"{BASE}/sessions", content=chunks(),
                      headers={"Content-Type": "application/json", "Origin": ORIGIN})
    assert res.status_code == 413 and res.json()["code"] == "GUIDE_PAYLOAD_TOO_LARGE"
    assert res.headers["x-request-id"].startswith("req_")  # envelope still applied


def test_chunked_body_under_limit_still_reaches_the_app(client):
    body = json.dumps({"client_session_nonce": str(uuid.uuid4()),
                       "page_context": page_context()}).encode()
    res = client.post(f"{BASE}/sessions", content=iter([body[:10], body[10:]]),
                      headers={"Content-Type": "application/json", "Origin": ORIGIN})
    assert res.status_code == 201, res.text


def test_security_headers_on_every_response(client, monkeypatch):
    for res in (client.get(f"{BASE}/bootstrap"),
                client.post(f"{BASE}/sessions", json={"bad": 1}, headers={"Origin": ORIGIN})):
        h = res.headers
        assert h["referrer-policy"] == "no-referrer"
        assert h["x-frame-options"] == "DENY"
        assert h["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert "camera=()" in h["permissions-policy"]
        assert h["cross-origin-resource-policy"] == "same-site"
        assert h["x-content-type-options"] == "nosniff"
        assert "strict-transport-security" not in h  # plain http, not production
    assert client.post(f"{BASE}/sessions", json={"bad": 1},
                       headers={"Origin": ORIGIN}).headers["cache-control"] == "no-store"
    assert "max-age=" in client.get(
        f"https://testserver{BASE}/bootstrap").headers["strict-transport-security"]


# ── 7. Purge backlog, failures and dead-letter ─────────────────────────────────────────────

def test_backlog_covers_every_sensitive_table():
    tables = {t for t, _, _ in scheduler.BACKLOG_CHECKS.values()}
    for t in ("guide_support_step_events", "guide_feedback", "guide_consents",
              "guide_external_operations", "guide_messages", "guide_lead_field_values",
              "guide_handoffs", "guide_audit_events", "guide_sessions"):
        assert t in tables


def test_purge_failure_still_alerts_on_backlog(guide, db, monkeypatch, caplog):  # SP-AT-16/17
    db.execute(text("UPDATE guide_messages SET purge_after = now() - interval '2 days'"))
    db.execute(text("UPDATE guide_sessions SET purge_after = now() - interval '25 hours'"))
    db.execute(text("UPDATE guide_session_context_versions "
                    "SET purge_after = now() - interval '2 hours'"))  # inside grace: no alert
    db.commit()

    def broken(*a, **k):
        raise RuntimeError("purge worker down")
    monkeypatch.setattr(purge, "purge_once", broken)
    before = metrics.snapshot_counter("guide_purge_backlog_alerts_total", table="sessions")
    with caplog.at_level(logging.ERROR):
        result = scheduler.tick()
    assert result["purge_failed"] is True
    assert result["backlog"] == {"guide_sessions": 1}
    assert any("GUIDE_RETENTION_BACKLOG" in r.getMessage() for r in caplog.records)
    assert any("GUIDE_PURGE_FAILED" in r.getMessage() for r in caplog.records)
    assert metrics.snapshot_counter("guide_purge_backlog_alerts_total",
                                    table="sessions") == before + 1


def test_purge_emits_run_metrics(guide, db):
    before = metrics.snapshot_counter("guide_purge_runs_total", outcome="ok")
    db.execute(text("UPDATE guide_sessions SET purge_after = now() - interval '1 hour'"))
    db.commit()
    purge.purge_once()
    assert metrics.snapshot_counter("guide_purge_runs_total", outcome="ok") == before + 1
    assert metrics.snapshot_counter("guide_purge_rows_total", table="sessions") >= 1


def test_unresolved_unknown_is_dead_lettered_and_visitor_still_sees_unknown(guide, db):
    a = FakeAdapter("unknown")
    adapters.set_lead_adapter(a)
    flow = start(guide)
    fill(guide, flow, VALID)
    receipt = consent(guide)["receipt_id"]
    key = str(uuid.uuid4())
    assert submit(guide, flow, receipt, key).status_code == 504
    db.execute(text("UPDATE guide_external_operations "
                    "SET last_attempt_at = now() - interval '25 hours'"))
    db.commit()
    assert scheduler.dead_letter_unknown_operations(db) == 1
    db.expire_all()
    assert db.scalar(select(GuideExternalOperation.outcome)) == "dead_letter"
    assert db.scalar(select(GuideLeadFlow.status)) == "unknown"
    assert db.scalar(select(func.count()).select_from(GuideAuditEvent).where(
        GuideAuditEvent.event_type == "external_operation_dead_lettered")) == 1
    replay = submit(guide, flow, receipt, key)  # same key: still "unconfirmed" + route
    assert replay.status_code == 504 and replay.json()["outcome"] == "unknown"
    assert replay.json()["actions"]
    assert submit(guide, flow, receipt).status_code == 409  # no blind resend
    assert len(a.calls) == 1
    assert scheduler.dead_letter_unknown_operations(db) == 0  # left the queue


def test_fresh_unknown_is_not_dead_lettered(guide, db):
    adapters.set_lead_adapter(FakeAdapter("unknown"))
    flow = start(guide)
    fill(guide, flow, VALID)
    submit(guide, flow, consent(guide)["receipt_id"])
    assert scheduler.dead_letter_unknown_operations(db) == 0


# ── 8. Retention configuration ─────────────────────────────────────────────────────────────

def test_confirmed_lead_window_is_configuration(guide, db, monkeypatch):  # SP-AT-15
    monkeypatch.setenv("GUIDE_RETENTION_LEAD_CONFIRMED_DAYS", "2")
    get_settings.cache_clear()
    adapters.set_lead_adapter(FakeAdapter("confirmed"))
    flow = start(guide)
    fill(guide, flow, VALID)
    assert submit(guide, flow, consent(guide)["receipt_id"]).status_code == 200
    latest = db.scalar(select(func.max(GuideLeadFieldValue.purge_after)))
    assert latest - retention.now() <= timedelta(days=2, minutes=1)


def test_accelerated_purge_window_is_configuration(guide, db, monkeypatch):
    monkeypatch.setenv("GUIDE_RETENTION_ACCELERATED_PURGE_HOURS", "3")
    get_settings.cache_clear()
    assert guide.post("/end").status_code == 204
    row = db.get(GuideSession, uuid.UUID(guide.session_id))
    assert row.purge_after <= row.ended_at + timedelta(hours=3, seconds=5)


# ── 9. Keyed hashing ───────────────────────────────────────────────────────────────────────

def test_keyed_hash_api():
    a = crypto.keyed_hash("hello", purpose=crypto.PURPOSE_MESSAGE_CONTENT)
    assert a == crypto.keyed_hash(b"hello", purpose=crypto.PURPOSE_MESSAGE_CONTENT)
    assert len(a) == 32 and a != crypto.digest("hello")
    assert a != crypto.keyed_hash("hello", purpose=crypto.PURPOSE_LEAD_EMAIL_LOOKUP)
    with pytest.raises(ValueError):
        crypto.keyed_hash("x", purpose="")


def test_lead_fingerprint_and_email_lookup_are_keyed(guide, db):
    flow = start(guide)
    fill(guide, flow, {**VALID, "work_email": "Jamie@Example.org"})
    row = db.get(GuideLeadFlow, uuid.UUID(flow["flow_id"]))
    assert row.email_lookup_hash == leads.email_lookup_hash(" jamie@example.ORG ")
    assert row.email_lookup_hash != crypto.digest("jamie@example.org")


# ── 10. Capability rotation ────────────────────────────────────────────────────────────────

def test_pasted_credential_rotates_capability(guide, client, db):
    flow = start(guide)
    old_csrf = guide.csrf
    res = fill(guide, flow, {"notes": "the api_key = sk_live_abcdefgh12345678"})
    assert res.status_code == 422
    new_csrf = res.headers.get(sessions.CSRF_ROTATED_HEADER)
    assert new_csrf and new_csrf != old_csrf
    assert sessions.COOKIE_NAME in res.headers.get("set-cookie", "")
    guide.csrf = new_csrf  # the browser adopts the rotated pair (cookie jar + header)
    assert guide.patch("/context", {"page_context": page_context()}).status_code == 200
    guide.csrf = old_csrf
    assert guide.patch("/context", {"page_context": page_context()}).status_code == 401
    audit = db.scalar(select(GuideAuditEvent.metadata_).where(
        GuideAuditEvent.event_type == "capability_rotated"))
    assert audit["reason"] == "secret_detected" and "sk_live" not in json.dumps(audit)


def test_ordinary_validation_error_does_not_rotate(guide):
    flow = start(guide)
    res = fill(guide, flow, {"work_email": "not-an-email"})
    assert res.status_code == 422 and sessions.CSRF_ROTATED_HEADER not in res.headers


def test_csrf_failure_burst_revokes_capability(guide, db, monkeypatch):
    monkeypatch.setenv("GUIDE_CSRF_FAILURE_LIMIT", "3")
    get_settings.cache_clear()
    good = guide.csrf
    guide.csrf = "wrong-token"
    for _ in range(4):
        assert guide.patch("/context", {"page_context": page_context()}).status_code == 401
    guide.csrf = good  # even the right token no longer works with the old cookie
    assert guide.patch("/context", {"page_context": page_context()}).status_code == 401
    audit = db.scalar(select(GuideAuditEvent.metadata_).where(
        GuideAuditEvent.event_type == "capability_rotated"))
    assert audit["reason"] == "csrf_failure_burst" and audit["delivered"] is False


# ── 11. Privacy-rights requests ────────────────────────────────────────────────────────────

def _lead_session(client, email="pat@example.org"):
    g = create_session(client)
    adapters.set_lead_adapter(FakeAdapter("confirmed"))
    flow = start(g)
    fill(g, flow, {**VALID, "work_email": email})
    receipt = consent(g)["receipt_id"]
    g.post("/messages", {"client_message_id": str(uuid.uuid4()), "text": "What is ZoikoStream?"})
    return g, receipt


def test_privacy_locate_by_email_receipt_and_session(client, db):
    g, receipt = _lead_session(client)
    create_session(client)  # someone else, never located
    for kw in ({"email": "PAT@example.org"}, {"receipt": receipt},
               {"session_id": g.session_id}):
        found = privacy.locate(db, **kw)
        assert found.session_ids == {uuid.UUID(g.session_id)}, kw
        assert len(found.consent_ids) == 1
    assert privacy.locate(db, email="nobody@example.org").empty
    assert privacy.locate(db, session_id="not-a-uuid").empty


def test_privacy_erase_is_dry_run_by_default_then_erases(client, db):  # SP-AT-26
    g, receipt = _lead_session(client)
    other = create_session(client)
    found = privacy.locate(db, email="pat@example.org")
    report = privacy.handle(db, "erase", found, case_ref="PR-42")
    assert report["dry_run"] and report["found"]["lead_field_values"] == len(VALID)
    assert db.scalar(select(func.count()).select_from(GuideLeadFieldValue)) == len(VALID)

    report = privacy.handle(db, "erase", privacy.locate(db, email="pat@example.org"),
                            execute=True, case_ref="PR-42")
    assert not report["dry_run"] and report["purged"].get("sessions") == 1
    db.expire_all()
    assert db.get(GuideSession, uuid.UUID(g.session_id)) is None
    assert db.get(GuideSession, uuid.UUID(other.session_id)) is not None
    assert db.scalar(select(func.count()).select_from(GuideLeadFieldValue)) == 0
    assert db.scalar(select(func.count()).select_from(GuideMessage).where(
        GuideMessage.session_id == uuid.UUID(g.session_id))) == 0
    # Consent evidence is retained (de-linked) unless explicitly included.
    assert db.scalar(select(GuideConsent).where(GuideConsent.receipt_ref == receipt))
    report = privacy.handle(db, "erase", privacy.locate(db, receipt=receipt), execute=True,
                            include_consents=True)
    assert report["purged"].get("consents") == 1
    events = db.scalars(select(GuideAuditEvent.metadata_).where(
        GuideAuditEvent.event_type == "privacy_request")).all()
    assert len(events) == 3
    dumped = json.dumps(events)
    assert "pat@example.org" not in dumped and receipt not in dumped
    assert g.patch("/context", {"page_context": page_context()}).status_code == 401


def test_privacy_cli(client, capsys):
    g, _ = _lead_session(client)
    assert privacy.main(["locate", "--session", g.session_id]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["found"]["sessions"] == 1 and out["action"] == "locate"
    assert privacy.main(["erase", "--email", "pat@example.org"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert privacy.main(["erase", "--email", "pat@example.org", "--case", "bad ref!"]) == 2


# ── 12. Least-privilege principals ─────────────────────────────────────────────────────────

def test_roles_and_grants_sql_is_idempotent_and_least_privilege():
    sql = (REPO / "deploy_sql" / "02_guide_roles_and_grants.sql").read_text(encoding="utf-8")
    with get_engine().connect() as conn:
        for _ in range(2):  # idempotent
            raw = conn.connection  # raw DBAPI: the file uses format('%I'), no bind params
            raw.cursor().execute(sql)
            raw.commit()

        def as_role(role, stmt):
            conn.exec_driver_sql(f"SET LOCAL ROLE {role}")
            try:
                conn.exec_driver_sql(stmt)
                return True
            except ProgrammingError:
                return False
            finally:
                conn.rollback()

        assert as_role("guide_runtime", "SELECT count(*) FROM guide_messages")
        assert as_role("guide_runtime", "SELECT count(*) FROM guide_rag.rag_index_releases")
        assert not as_role("guide_runtime", "CREATE TABLE guide_evil (x int)")
        assert not as_role("guide_runtime", "DELETE FROM guide_rag.rag_index_releases")
        assert not as_role("guide_runtime", "DELETE FROM guide_knowledge_sources")
        assert as_role("guide_maintenance", "DELETE FROM guide_messages WHERE false")
        assert not as_role("guide_maintenance", "INSERT INTO guide_messages (message_id) "
                                                "VALUES (gen_random_uuid())")
        assert as_role("guide_ingest", "DELETE FROM guide_rag.rag_index_releases WHERE false")
        assert not as_role("guide_ingest", "SELECT count(*) FROM guide_lead_field_values")
        assert as_role("guide_ingest", "INSERT INTO guide_audit_events (event_type, metadata, "
                                       "purge_after) VALUES ('index_activated', '{}', now())")
        assert as_role("guide_ingest", "SELECT count(*) FROM guide_runtime_config")
        assert not as_role("guide_ingest", "UPDATE guide_runtime_config SET version = version")
        assert not as_role("guide_ingest", "DELETE FROM guide_audit_events WHERE false")
        assert as_role("guide_readonly", "SELECT count(*) FROM guide_audit_events")
        assert not as_role("guide_readonly", "SELECT count(*) FROM guide_messages")
        assert as_role("guide_migrator", "CREATE TABLE guide_tmp_ddl (x int)")


def test_deploy_uses_migration_principal(monkeypatch):
    from app.guide import deploy
    monkeypatch.setenv("GUIDE_MIGRATION_DATABASE_URL", "postgresql://mig@db/guide")
    get_settings.cache_clear()
    assert deploy.migration_url() == "postgresql://mig@db/guide"
    assert deploy.ingest_url() == "postgresql://mig@db/guide"
    monkeypatch.setenv("GUIDE_INGEST_DATABASE_URL", "postgresql://ing@db/guide")
    get_settings.cache_clear()
    assert deploy.ingest_url() == "postgresql://ing@db/guide"


# ── 13. Logging leak canary (SP-AT-14) ─────────────────────────────────────────────────────

CANARY = "Zqxvcanary"


def test_canary_pii_never_reaches_logs_or_audit(client, db, caplog):
    caplog.set_level(logging.DEBUG)
    adapters.set_lead_adapter(FakeAdapter("confirmed"))
    g = create_session(client)
    msg = g.post("/messages", {"client_message_id": str(uuid.uuid4()),
                               "text": f"Hi, I'm {CANARY} from {CANARY} Org, my email is "
                                       f"{CANARY.lower()}@example.org"}).json()
    res = g.post("/messages", {"client_message_id": str(uuid.uuid4()),
                               "text": f"my password is {CANARY}Pw99"})
    g.csrf = res.headers.get(sessions.CSRF_ROTATED_HEADER, g.csrf)  # SEC §7 rotation
    flow = start(g)
    fill(g, flow, {**VALID, "full_name": f"{CANARY} Person",
                   "work_email": f"{CANARY.lower()}@example.org",
                   "notes": f"{CANARY} notes"})
    res = fill(g, flow, {"organization": "x", "notes": f"{CANARY} token is "
                                                        "ghp_abcdefghijklmnopqrstuvwx"})
    g.csrf = res.headers.get(sessions.CSRF_ROTATED_HEADER, g.csrf)
    submit(g, flow, consent(g)["receipt_id"])
    g.post("/feedback", {"client_feedback_id": str(uuid.uuid4()), "target_type": "answer",
                         "target_id": msg["assistant_message"]["message_id"],
                         "rating": "down", "comment": f"{CANARY} was unhappy"})
    g.post("/handoffs", {"target": "sales"}, headers={"Idempotency-Key": str(uuid.uuid4())})
    g.post("/end")

    assert caplog.records  # the capture is live
    for r in caplog.records:
        blob = r.getMessage() + repr(r.args) + (r.exc_text or "")
        assert CANARY.lower() not in blob.lower(), (r.name, r.levelname)
    audit = db.execute(text("SELECT metadata::text FROM guide_audit_events")).scalars().all()
    assert audit and not any(CANARY.lower() in a.lower() for a in audit)


# ── 14. Environment separation ─────────────────────────────────────────────────────────────

def _prod(monkeypatch, **env):
    base = {"ENVIRONMENT": "production", "GUIDE_LLM_PROVIDER": "anthropic",
            "GUIDE_ALLOWED_ORIGINS": "https://www.zoikostream.com",
            "GUIDE_KEY_PROVIDER": "gcp-kms", "GUIDE_KMS_KEY_NAME": "k", "GUIDE_KMS_KEYSET": "a",
            "GUIDE_DATABASE_URL": "postgresql://u@db/zoikostream_guide",
            "GUIDE_TEST_DATABASE_URL": "", "GUIDE_EMBEDDING_PROFILE": "fastembed:x",
            "GUIDE_ALLOW_MISSING_ORIGIN": "false"}
    for k, v in {**base, **env}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return get_settings().production_problems()


def test_production_refuses_test_wiring(monkeypatch):
    assert _prod(monkeypatch) == []
    assert any("test database" in p for p in _prod(
        monkeypatch, GUIDE_DATABASE_URL="postgresql://u@db/guide_test"))
    assert any("test database" in p for p in _prod(
        monkeypatch, GUIDE_TEST_DATABASE_URL="postgresql://u@db/zoikostream_guide"))
    assert any("embedding" in p for p in _prod(monkeypatch, GUIDE_EMBEDDING_PROFILE="hash:v1"))
    assert any("MISSING_ORIGIN" in p for p in _prod(monkeypatch,
                                                    GUIDE_ALLOW_MISSING_ORIGIN="true"))
    assert any("mock" in p for p in _prod(monkeypatch, GUIDE_LLM_PROVIDER="mock"))
    assert any("local" in p for p in _prod(monkeypatch, GUIDE_KEY_PROVIDER="local",
                                           GUIDE_LOCAL_MASTER_KEY="b" * 64))


# ── Follow-up: a refused knowledge rebuild never takes the serving index away ──────────────

def _refuse_rebuild(monkeypatch):
    from app.guide.knowledge import governance, ingest

    def refuse(db, docs, **kw):
        raise governance.GovernanceError("content approval refused: product-overview "
                                         "is pending-approval")
    monkeypatch.setattr(ingest, "is_current", lambda *a, **k: False)
    monkeypatch.setattr(ingest, "build_release", refuse)


def _active(db):
    return db.execute(text("SELECT release_id FROM guide_rag.rag_index_releases "
                           "WHERE status = 'active'")).scalar()


def test_refused_rebuild_keeps_the_active_release(db, monkeypatch, caplog):
    from app.guide import deploy
    before = _active(db)
    assert before
    _refuse_rebuild(monkeypatch)
    with caplog.at_level(logging.WARNING):
        out = deploy.refresh_knowledge()
    assert out.status == "kept_active" and out.active_release_id == before
    assert "NOT rebuilt" in out.line and before in out.line
    assert any("GUIDE_INDEX_REBUILD_REFUSED" in r.getMessage() for r in caplog.records)
    db.expire_all()
    assert _active(db) == before
    meta = db.scalar(select(GuideAuditEvent.metadata_).where(
        GuideAuditEvent.event_type == "index_rebuild_refused"))
    assert meta["kept_release_id"] == before and meta["degraded"] is False
    assert meta["error_class"] == "GovernanceError"


def test_no_active_release_starts_degraded_never_crashes(client, db, monkeypatch, capsys):
    from app.guide import deploy
    before = _active(db)
    db.execute(text("UPDATE guide_rag.rag_index_releases SET status = 'superseded' "
                    "WHERE release_id = :r"), {"r": before})
    db.commit()
    try:
        _refuse_rebuild(monkeypatch)
        assert deploy.main([]) == 2  # deploy completes; exit 2 flags the degraded Guide
        assert "NO active release" in capsys.readouterr().out
        g = create_session(client)
        res = g.post("/messages", {"client_message_id": str(uuid.uuid4()),
                                   "text": "What does ZoikoStream do?"})
        assert res.status_code == 200, res.text
        body = res.json()["assistant_message"]
        assert not body.get("sources")  # no generated, grounded claim without an index
        assert body.get("actions")      # direct routes remain
        assert client.get(f"{BASE}/bootstrap").status_code == 200
    finally:
        db.rollback()
        db.execute(text("UPDATE guide_rag.rag_index_releases SET status = 'active' "
                        "WHERE release_id = :r"), {"r": before})
        db.commit()


def test_strict_knowledge_flag_fails_only_on_request(monkeypatch):
    from app.guide import deploy
    _refuse_rebuild(monkeypatch)
    assert deploy.main([]) == 0
    assert deploy.main(["--strict-knowledge"]) == 3


def test_ingest_principal_can_build_a_release():
    """guide_ingest alone can run the gated build (registry, units, evaluation, audit)."""
    from sqlalchemy import create_engine, pool
    from sqlalchemy.orm import Session as OrmSession
    from app.guide.db import engine_url
    from app.guide.knowledge import ingest
    from conftest import FIXTURES

    sql = (REPO / "deploy_sql" / "02_guide_roles_and_grants.sql").read_text(encoding="utf-8")
    with get_engine().connect() as conn:
        raw = conn.connection
        raw.cursor().execute(sql)
        raw.commit()
    engine = create_engine(engine_url(get_settings().GUIDE_DATABASE_URL),
                           poolclass=pool.NullPool)
    built = None
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SET ROLE guide_ingest")
            conn.commit()
            db = OrmSession(bind=conn, autoflush=False, expire_on_commit=False)
            release = ingest.build_release(db, ingest.load_sources(FIXTURES / "knowledge"),
                                           activate=False)
            built = release.release_id
            assert release.status == "ready"
            db.close()
    finally:
        engine.dispose()
        if built:
            with get_engine().begin() as conn:
                conn.execute(text("DELETE FROM guide_rag.rag_knowledge_units "
                                  "WHERE release_id = :r"), {"r": built})
                conn.execute(text("DELETE FROM guide_rag.rag_index_releases "
                                  "WHERE release_id = :r"), {"r": built})
