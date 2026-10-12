"""Bootstrap, session lifecycle, binding and error contract (API §3–7, API-AT-01..05/15/19/20)."""
import uuid
from datetime import timedelta

from sqlalchemy import func, select, text

from app.guide import crypto, registry
from app.guide.models import GuideAuditEvent, GuideSession, GuideSessionContextVersion
from app.guide.sessions import COOKIE_NAME

from conftest import BASE, ORIGIN, create_session, page_context


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


# ── Bootstrap ──────────────────────────────────────────────────────────────────────────────

def test_bootstrap_creates_no_session(client, db):
    res = client.get(f"{BASE}/bootstrap", params={"page_id": "pricing",
                                                 "page_type": "pricing"})
    assert res.status_code == 200
    assert "set-cookie" not in res.headers
    body = res.json()
    assert body["enabled"] is True
    assert body["assistant"] == {"name": "Guide", "descriptor": "Public AI Assistant"}
    assert body["suggested_intents"][0]["intent_id"] == "I-PRICING"
    assert body["limits"]["max_message_chars"] == 4000
    assert res.headers["cache-control"] == "public, max-age=60"
    assert _count(db, GuideSession) == 0


def test_bootstrap_etag_revalidates(client):
    first = client.get(f"{BASE}/bootstrap")
    again = client.get(f"{BASE}/bootstrap", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_viewer_page_suggestions_are_viewer_help(client):
    body = client.get(f"{BASE}/bootstrap", params={"page_type": "viewer"}).json()
    assert [i["intent_id"] for i in body["suggested_intents"]][:2] == ["I-VIEWER",
                                                                       "I-PLAYBACK"]


# ── Creation ───────────────────────────────────────────────────────────────────────────────

def test_create_session_sets_httponly_cookie_and_stores_only_hashes(client, db):
    res = client.post(f"{BASE}/sessions", json={
        "client_session_nonce": str(uuid.uuid4()), "page_context": page_context()},
        headers={"Origin": ORIGIN})
    assert res.status_code == 201
    cookie = res.headers["set-cookie"]
    assert f"{COOKIE_NAME}=" in cookie and "HttpOnly" in cookie
    assert "Path=/api/v1/public-guide" in cookie and "SameSite=lax" in cookie
    body = res.json()
    assert body["session_state"] == "ACTIVE" and body["csrf_token"]
    assert res.headers["cache-control"] == "no-store"

    raw_cap = client.cookies.get(COOKIE_NAME)
    row = db.get(GuideSession, uuid.UUID(body["session_id"]))
    assert row.capability_hash == crypto.keyed_hash(raw_cap, purpose="session-capability")
    dump = db.execute(text("SELECT row_to_json(s)::text FROM guide_sessions s")).scalar()
    assert raw_cap not in dump and body["csrf_token"] not in dump  # DB-AT-01


def test_session_is_pseudonymous_no_identity_columns(guide, db):
    row = db.get(GuideSession, uuid.UUID(guide.session_id))
    cols = set(GuideSession.__table__.columns.keys())
    assert not cols & {"user_id", "org_id", "account_id", "tenant_id", "email", "ip"}
    assert row.state == "ACTIVE"


def test_nonce_replay_is_a_conflict_not_a_second_session(client, db):
    payload = {"client_session_nonce": str(uuid.uuid4()), "page_context": page_context()}
    assert client.post(f"{BASE}/sessions", json=payload).status_code == 201
    res = client.post(f"{BASE}/sessions", json=payload)
    assert res.status_code == 409 and res.json()["code"] == "GUIDE_STATE_CONFLICT"
    assert _count(db, GuideSession) == 1


def test_unknown_field_is_rejected(client, db):  # API-AT-03
    ctx = page_context()
    ctx["org_id"] = "123"
    res = client.post(f"{BASE}/sessions", json={"client_session_nonce": str(uuid.uuid4()),
                                                 "page_context": ctx})
    assert res.status_code == 400
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "GUIDE_BAD_REQUEST"
    assert _count(db, GuideSession) == 0


def test_absolute_path_rejected_and_query_stripped(client, db):
    bad = client.post(f"{BASE}/sessions", json={"client_session_nonce": str(uuid.uuid4()),
                                                "page_context": page_context(
                                                    path="https://evil.example/x")})
    assert bad.status_code == 422
    g = create_session(client, page_id="viewer", page_type="viewer",
                       path="/events/1/watch?reg=SECRETCRED#t")
    row = db.scalar(select(GuideSessionContextVersion).where(
        GuideSessionContextVersion.session_id == uuid.UUID(g.session_id)))
    assert row.path == "/events/1/watch"


# ── Binding (cookie + CSRF + path + origin) ────────────────────────────────────────────────

def test_context_update_appends_a_version(guide, db):
    res = guide.patch("/context", {"page_context": page_context(page_id="pricing",
                                                                page_type="pricing")})
    assert res.status_code == 200 and res.json()["context_version"] == 2
    versions = db.scalars(select(GuideSessionContextVersion.context_version).where(
        GuideSessionContextVersion.session_id == uuid.UUID(guide.session_id))).all()
    assert sorted(versions) == [1, 2]  # DB-AT-02


def test_missing_csrf_is_rejected_without_side_effect(guide, client, db):  # API-AT-05
    res = client.patch(f"{BASE}/sessions/{guide.session_id}/context",
                       json={"page_context": page_context(page_type="pricing")},
                       headers={"Origin": ORIGIN})
    assert res.status_code == 401 and res.json()["code"] == "GUIDE_SESSION_INVALID"
    assert _count(db, GuideSessionContextVersion) == 1


def test_wrong_csrf_is_rejected(guide, client):
    res = client.patch(f"{BASE}/sessions/{guide.session_id}/context",
                       json={"page_context": page_context()},
                       headers={"Origin": ORIGIN, "X-ZS-CSRF": "not-the-token"})
    assert res.status_code == 401


def test_path_session_must_match_cookie(client):  # API-AT-04
    a = create_session(client)
    b_res = client.post(f"{BASE}/sessions", json={
        "client_session_nonce": str(uuid.uuid4()), "page_context": page_context()})
    b_id = b_res.json()["session_id"]
    # The jar now holds B's cookie; A's id + A's CSRF must fail, as must B's id + A's CSRF.
    assert a.patch("/context", {"page_context": page_context()}).status_code == 401
    res = client.patch(f"{BASE}/sessions/{b_id}/context", json={"page_context": page_context()},
                       headers={"X-ZS-CSRF": a.csrf})
    assert res.status_code == 401


def test_unknown_session_and_missing_cookie_look_identical(guide, client):
    unknown = guide.c.patch(f"{BASE}/sessions/{uuid.uuid4()}/context",
                            json={"page_context": page_context()}, headers=guide.headers())
    client.cookies.clear()
    no_cookie = guide.patch("/context", {"page_context": page_context()})
    assert unknown.status_code == no_cookie.status_code == 401
    assert unknown.json()["detail"] == no_cookie.json()["detail"]


def test_unapproved_origin_is_rejected_before_side_effect(guide, client, db):  # API-AT-19
    res = client.patch(f"{BASE}/sessions/{guide.session_id}/context",
                       json={"page_context": page_context(page_type="pricing")},
                       headers={"Origin": "https://evil.example", "X-ZS-CSRF": guide.csrf})
    assert res.status_code == 403 and res.json()["code"] == "GUIDE_PUBLIC_BOUNDARY_DENIED"
    assert _count(db, GuideSessionContextVersion) == 1


# ── Lifecycle ──────────────────────────────────────────────────────────────────────────────

def test_end_invalidates_capability_immediately(guide, db):
    assert guide.post("/end").status_code == 204
    assert guide.patch("/context", {"page_context": page_context()}).status_code == 401
    row = db.get(GuideSession, uuid.UUID(guide.session_id))
    assert row.state == "ENDED" and row.end_reason == "user_ended"
    assert row.purge_after <= row.ended_at + timedelta(hours=24)


def test_idle_expiry_ends_the_session(guide, db):
    db.execute(text("UPDATE guide_sessions SET last_activity_at = now() - interval '31 minutes'"))
    db.commit()
    res = guide.patch("/context", {"page_context": page_context()})
    assert res.status_code == 401
    db.expire_all()
    assert db.get(GuideSession, uuid.UUID(guide.session_id)).end_reason == "idle_expiry"


def test_absolute_expiry_ends_the_session(guide, db):
    db.execute(text("UPDATE guide_sessions SET expires_at = now() - interval '1 second'"))
    db.commit()
    assert guide.patch("/context", {"page_context": page_context()}).status_code == 401


def test_audit_records_carry_no_content(guide, db):
    events = db.scalars(select(GuideAuditEvent)).all()
    assert {e.event_type for e in events} == {"session_created"}
    for e in events:
        assert set(e.metadata_) <= {"page_type", "entry_point", "event_context",
                                    "retention_policy"}


# ── Kill switch, rate limit, error secrecy ─────────────────────────────────────────────────

def test_env_kill_switch(client, monkeypatch, db):
    from app.guide.settings import get_settings
    monkeypatch.setenv("GUIDE_ENABLED", "false")
    get_settings.cache_clear()
    body = client.get(f"{BASE}/bootstrap").json()
    assert body["enabled"] is False and body["suggested_intents"] == []
    res = client.post(f"{BASE}/sessions", json={"client_session_nonce": str(uuid.uuid4()),
                                                "page_context": page_context()})
    assert res.status_code == 503 and res.json()["code"] == "GUIDE_UNAVAILABLE"
    assert _count(db, GuideSession) == 0


def test_database_kill_switch(client, db):  # ARCH-AT-18
    db.execute(text("UPDATE guide_runtime_config SET config_value='{\"enabled\": false}' "
                    "WHERE config_key='guide.enabled'"))
    db.commit()
    registry.invalidate()
    assert client.get(f"{BASE}/bootstrap").json()["enabled"] is False


def test_user_can_end_session_even_when_disabled(guide, db):
    db.execute(text("UPDATE guide_runtime_config SET config_value='{\"enabled\": false}' "
                    "WHERE config_key='guide.enabled'"))
    db.commit()
    registry.invalidate()
    assert guide.post("/end").status_code == 204


def test_session_create_rate_limit(client, monkeypatch):  # API-AT-15
    from app.guide.settings import get_settings
    monkeypatch.setenv("GUIDE_RL_SESSION_CREATE_PER_MIN", "2")
    get_settings.cache_clear()
    codes = [client.post(f"{BASE}/sessions", json={
        "client_session_nonce": str(uuid.uuid4()), "page_context": page_context()}).status_code
        for _ in range(3)]
    assert codes == [201, 201, 429]
    res = client.post(f"{BASE}/sessions", json={
        "client_session_nonce": str(uuid.uuid4()), "page_context": page_context()})
    assert res.json()["code"] == "GUIDE_RATE_LIMITED"
    assert int(res.headers["retry-after"]) > 0
    assert res.headers["ratelimit-remaining"] == "0"


def test_standard_headers_present(guide, client):
    res = client.get(f"{BASE}/bootstrap", headers={"X-Request-Id": "req_abcdef123"})
    assert res.headers["x-request-id"] == "req_abcdef123"
    assert res.headers["x-correlation-id"].startswith("cor_")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["content-language"] == "en-US"


def test_errors_never_leak_internals(guide, monkeypatch):  # API-AT-20
    from app.guide import sessions

    def boom(*a, **k):
        raise RuntimeError("psycopg2 connect host=db-internal.prod password=hunter2")
    monkeypatch.setattr(sessions, "update_context", boom)
    res = guide.patch("/context", {"page_context": page_context()})
    assert res.status_code == 500
    text_ = res.text
    for leak in ("psycopg2", "db-internal", "hunter2", "Traceback", "RuntimeError"):
        assert leak not in text_
    assert res.json()["code"] == "GUIDE_INTERNAL_ERROR"


def test_oversized_body_is_413(client):
    res = client.post(f"{BASE}/sessions", content=b"x" * (70 * 1024),
                      headers={"Content-Type": "application/json"})
    assert res.status_code == 413 and res.json()["code"] == "GUIDE_PAYLOAD_TOO_LARGE"


def test_dev_allows_any_localhost_port_but_production_does_not(monkeypatch):
    from app.guide.settings import get_settings
    s = get_settings()
    assert s.origin_allowed("http://localhost:5175")
    assert not s.origin_allowed("https://evil.example")
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    assert not get_settings().origin_allowed("http://localhost:5175")
