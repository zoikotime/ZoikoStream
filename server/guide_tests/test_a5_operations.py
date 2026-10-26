"""Health/readiness/metrics (C8), handoff idempotency (API §13/§16), session-create replay
(API §16), paused routes (C7), lead drafts (C6) and the bootstrap kill-switch contract."""
import uuid

from sqlalchemy import func, select, text

from app.guide import breaker, metrics, registry
from app.guide.models import GuideExternalOperation, GuideHandoff, GuideLeadFieldValue, GuideSession
from app.guide.settings import get_settings

from conftest import BASE, ORIGIN, create_session, page_context


def _switch(db, feature, enabled):
    db.execute(text("INSERT INTO guide_runtime_config (config_key, config_value, version) "
                    "VALUES (:k, CAST(:v AS jsonb), 1) ON CONFLICT (config_key) DO UPDATE "
                    "SET config_value = EXCLUDED.config_value"),
               {"k": f"guide.feature.{feature}",
                "v": '{"enabled": %s}' % ("true" if enabled else "false")})
    db.commit()
    registry.invalidate()


# ── C8 health / readiness / metrics ────────────────────────────────────────────────────────

def test_liveness_always_ok(client):
    res = client.get(f"{BASE}/health/live")
    assert res.status_code == 200 and res.json() == {"status": "ok"}
    assert res.headers["cache-control"] == "no-store"


def test_readiness_checks_db_migrations_release_and_reports_breakers(client):
    breaker.reset()
    breaker.get("model").failure()
    res = client.get(f"{BASE}/health/ready")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "migrations": "ok", "rag_release": "ok"}
    assert "model" in body["breakers"]
    assert "postgresql" not in res.text and "localhost" not in res.text  # no secrets/hosts


def test_readiness_fails_without_an_active_rag_release(client, db):
    ids = db.scalars(text("UPDATE guide_rag.rag_index_releases SET status = 'superseded' "
                          "WHERE status = 'active' RETURNING release_id")).all()
    db.commit()
    try:
        res = client.get(f"{BASE}/health/ready")
        assert res.status_code == 503
        assert res.json()["checks"]["rag_release"] == "missing"
    finally:
        db.execute(text("UPDATE guide_rag.rag_index_releases SET status = 'active' "
                        "WHERE release_id = ANY(:ids)"), {"ids": list(ids)})
        db.commit()


def test_readiness_fails_when_schema_is_behind(client, monkeypatch):
    from app.guide import health
    monkeypatch.setattr(health, "migration_heads", lambda: frozenset({"9999"}))
    res = client.get(f"{BASE}/health/ready")
    assert res.status_code == 503 and res.json()["checks"]["migrations"] == "behind"


def test_metrics_require_the_configured_token(client, monkeypatch):
    assert client.get(f"{BASE}/internal/metrics").status_code == 404  # disabled when empty
    monkeypatch.setenv("GUIDE_METRICS_TOKEN", "metrics-token-for-tests-123")
    get_settings.cache_clear()
    metrics.incr("guide_test_counter_total")
    assert client.get(f"{BASE}/internal/metrics").status_code == 404
    assert client.get(f"{BASE}/internal/metrics",
                      headers={"X-Guide-Metrics-Token": "wrong"}).status_code == 404
    res = client.get(f"{BASE}/internal/metrics",
                     headers={"X-Guide-Metrics-Token": "metrics-token-for-tests-123"})
    assert res.status_code == 200 and "guide_test_counter_total" in res.text


# ── Handoff idempotency (API §13, §16) ─────────────────────────────────────────────────────

def test_same_idempotency_key_replays_original_handoff(guide, db):
    key = str(uuid.uuid4())
    first = guide.post("/handoffs", {"target": "support"}, headers={"Idempotency-Key": key})
    second = guide.post("/handoffs", {"target": "support"}, headers={"Idempotency-Key": key})
    assert first.status_code == second.status_code == 200
    a, b = first.json(), second.json()
    assert a["handoff_id"] == b["handoff_id"] and a["outcome"] == b["outcome"]
    assert a["replayed"] is False and b["replayed"] is True
    assert db.scalar(select(func.count()).select_from(GuideHandoff)) == 1
    op = db.scalar(select(GuideExternalOperation))
    assert op.operation_type == "handoff_submit"
    assert key.encode() not in op.idempotency_key_hash  # keyed hash only


def test_same_key_for_a_different_request_conflicts(guide):
    key = str(uuid.uuid4())
    assert guide.post("/handoffs", {"target": "support"},
                      headers={"Idempotency-Key": key}).status_code == 200
    res = guide.post("/handoffs", {"target": "sales"}, headers={"Idempotency-Key": key})
    assert res.status_code == 409 and res.json()["code"] == "GUIDE_STATE_CONFLICT"


def test_new_key_is_a_new_handoff(guide, db):
    for _ in range(2):
        guide.post("/handoffs", {"target": "status"},
                   headers={"Idempotency-Key": str(uuid.uuid4())})
    assert db.scalar(select(func.count()).select_from(GuideHandoff)) == 2


# ── Session-create replay (API §16) ────────────────────────────────────────────────────────

def test_create_replay_from_the_cookie_holder_returns_same_session(client, db):
    payload = {"client_session_nonce": str(uuid.uuid4()), "page_context": page_context()}
    first = client.post(f"{BASE}/sessions", json=payload, headers={"Origin": ORIGIN})
    again = client.post(f"{BASE}/sessions", json=payload, headers={"Origin": ORIGIN})
    assert first.status_code == again.status_code == 201
    assert again.json()["session_id"] == first.json()["session_id"]
    assert again.json()["csrf_token"] != first.json()["csrf_token"]  # rotated, never re-sent
    assert db.scalar(select(func.count()).select_from(GuideSession)) == 1
    # The rotated token works; the lost one no longer does.
    sid = first.json()["session_id"]
    ok = client.patch(f"{BASE}/sessions/{sid}/context", json={"page_context": page_context()},
                      headers={"Origin": ORIGIN, "X-ZS-CSRF": again.json()["csrf_token"]})
    assert ok.status_code == 200
    stale = client.patch(f"{BASE}/sessions/{sid}/context",
                         json={"page_context": page_context()},
                         headers={"Origin": ORIGIN, "X-ZS-CSRF": first.json()["csrf_token"]})
    assert stale.status_code == 401


def test_create_replay_without_the_cookie_is_still_a_conflict(client, db):
    payload = {"client_session_nonce": str(uuid.uuid4()), "page_context": page_context()}
    assert client.post(f"{BASE}/sessions", json=payload).status_code == 201
    client.cookies.clear()
    res = client.post(f"{BASE}/sessions", json=payload)
    assert res.status_code == 409 and res.json()["code"] == "GUIDE_STATE_CONFLICT"
    assert "csrf_token" not in res.text
    assert db.scalar(select(func.count()).select_from(GuideSession)) == 1


def test_create_replay_after_end_is_a_conflict(client):
    payload = {"client_session_nonce": str(uuid.uuid4()), "page_context": page_context()}
    body = client.post(f"{BASE}/sessions", json=payload).json()
    cookie = client.cookies.get("zs_guide_sid")
    client.post(f"{BASE}/sessions/{body['session_id']}/end",
                headers={"Origin": ORIGIN, "X-ZS-CSRF": body["csrf_token"]})
    client.cookies.set("zs_guide_sid", cookie, path="/api/v1/public-guide")
    assert client.post(f"{BASE}/sessions", json=payload).status_code == 409


# ── C7 paused routes ───────────────────────────────────────────────────────────────────────

def test_paused_leads_carry_contact_sales_route(guide, db):
    _switch(db, "leads", False)
    res = guide.post("/lead-flows", {"flow_kind": "demo"})
    assert res.status_code == 503
    paused = res.json()["paused"]
    assert paused["feature"] == "leads" and paused["message"] == res.json()["detail"]
    assert "contact-sales" in [r["action_id"] for r in paused["routes"]]


def test_paused_viewer_support_carries_direct_routes(guide, db):
    _switch(db, "viewer_support", False)
    res = guide.post("/support-flows", {"flow_type": "playback"})
    paused = res.json()["paused"]
    assert res.status_code == 503 and paused["feature"] == "viewer_support"
    ids = [r["action_id"] for r in paused["routes"]]
    assert "viewer-support" in ids and "status-page" in ids


# ── C6 lead drafts ─────────────────────────────────────────────────────────────────────────

def _lead(guide):
    res = guide.post("/lead-flows", {"flow_kind": "demo"})
    assert res.status_code == 201
    return res.json()["flow_id"]


def test_draft_is_held_encrypted_without_consent_and_returned_on_resume(guide, db):
    flow_id = _lead(guide)
    res = guide.patch(f"/leads/{flow_id}/draft", {"full_name": "Alex Example",
                                                   "work_email": "alex@exa"})
    assert res.status_code == 204, res.text
    rows = db.scalars(select(GuideLeadFieldValue)).all()
    assert [r.field_id for r in rows] == ["full_name"]  # half-typed email not held
    assert b"Alex Example" not in rows[0].value_ciphertext
    resumed = guide.post("/lead-flows", {"flow_kind": "demo"}).json()
    values = {f["field_id"]: f.get("value") for f in resumed["fields"]}
    assert values["full_name"] == "Alex Example"


def test_draft_rejects_unknown_fields_and_other_sessions(client, guide):
    flow_id = _lead(guide)
    res = guide.patch(f"/leads/{flow_id}/draft", {"org_id": "1"})
    assert res.status_code == 422 and res.json()["field_errors"][0]["code"] == "unknown_field"
    other = create_session(client)
    assert other.patch(f"/leads/{flow_id}/draft", {"full_name": "X Y"}).status_code == 404


def test_draft_requires_csrf(guide):
    flow_id = _lead(guide)
    res = guide.c.patch(f"{BASE}/sessions/{guide.session_id}/leads/{flow_id}/draft",
                        json={"full_name": "Alex Example"}, headers={"Origin": ORIGIN})
    assert res.status_code == 401


def test_draft_is_purged_with_the_session(guide, db):
    flow_id = _lead(guide)
    guide.patch(f"/leads/{flow_id}/draft", {"full_name": "Alex Example"})
    db.execute(text("DELETE FROM guide_sessions WHERE session_id = :s"),
               {"s": guide.session_id})
    db.commit()
    assert db.scalar(select(func.count()).select_from(GuideLeadFieldValue)) == 0


# ── Bootstrap under the kill switch (API §6, App. A) ───────────────────────────────────────

def test_bootstrap_kill_switch_is_200_disabled_with_direct_routes(client, db):
    db.execute(text("UPDATE guide_runtime_config SET config_value = '{\"enabled\": false}' "
                    "WHERE config_key = 'guide.enabled'"))
    db.commit()
    registry.invalidate()
    res = client.get(f"{BASE}/bootstrap")
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is False and body["unavailable_reason"] == "disabled"
    assert body["direct_routes"]
