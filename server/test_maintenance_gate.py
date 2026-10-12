"""The maintenance gate, when the database is unreachable or merely busy.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
`maintenance_gate` is the OUTERMOST middleware, and it opened a fresh SessionLocal() on every
gated request to read one boolean. Two consequences, both seen in production logs:

1. CONNECTION PRESSURE. Every console page load checked out an extra pooled connection before
   doing any work, which against a small connection allowance is the difference between
   fitting and not — the observed failure was
   `FATAL: remaining connection slots are reserved for roles with the SUPERUSER attribute`,
   which takes down every request rather than one.

2. AN UNREPORTABLE FAILURE. Being outermost, an exception raised here reaches Starlette's
   ServerErrorMiddleware — outside CORSMiddleware, outside the unhandled-error handler, and
   outside the ExceptionMiddleware that holds `app.exception_handler(OperationalError)`. None
   of the three could see it, so the browser received a 500 with no CORS headers, discarded
   it, and axios reported `Network Error`. A database blip presented as the API being
   unreachable, which is the wrong layer and sends whoever is debugging it to the network.

The gate now caches the flag and fails OPEN when it cannot be read: if the database really is
unavailable the request fails again further in, where the OperationalError handler turns it
into a clean 503 that keeps its CORS headers and says what is wrong.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app import main as main_app

ORIGIN = "http://localhost:5173"
# Gated, and cheap to reach: it needs no session, so what these tests observe is the gate.
GATED = "/api/organization/overview"


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The gate caches its answer; each test must start from a cold read."""
    main_app._maintenance_cache["at"] = -main_app._MAINTENANCE_TTL
    main_app._maintenance_cache["value"] = False
    yield
    main_app._maintenance_cache["at"] = -main_app._MAINTENANCE_TTL
    main_app._maintenance_cache["value"] = False


@pytest.fixture
def client():
    return TestClient(main_app.app, raise_server_exceptions=False)


def _db_down(monkeypatch):
    def boom(*_a, **_k):
        raise OperationalError("SELECT 1", {}, Exception(
            "FATAL: remaining connection slots are reserved for roles with the SUPERUSER attribute"))
    monkeypatch.setattr(main_app, "SessionLocal", boom)


def _flag(monkeypatch, value):
    calls = {"n": 0}

    class _Session:
        def close(self):
            pass

    monkeypatch.setattr(main_app, "SessionLocal", lambda: _Session())

    def read(_db):
        calls["n"] += 1
        return value

    monkeypatch.setattr(main_app.platform_settings, "maintenance_mode", read)
    return calls


# ── the unreportable failure ────────────────────────────────────────────────────────────

def test_a_database_outage_here_is_not_a_500(client, monkeypatch):
    """It must not answer from ServerErrorMiddleware, which is outside every handler."""
    _db_down(monkeypatch)
    assert client.get(GATED).status_code != 500


def test_the_response_still_carries_cors_headers(client, monkeypatch):
    """The whole point. Without this the browser discards the answer and the page reports a
    network failure for a request the server did answer."""
    _db_down(monkeypatch)
    r = client.get(GATED, headers={"Origin": ORIGIN})
    assert r.headers.get("access-control-allow-origin") == ORIGIN


def test_the_request_is_served_rather_than_refused(client, monkeypatch):
    """Fails OPEN: an unreadable flag is not evidence of planned maintenance, and refusing
    here would let a connection shortage masquerade as one."""
    _db_down(monkeypatch)
    r = client.get(GATED)
    # 401 — it reached the route and asked for a session, which is the gate standing aside.
    assert r.status_code == 401


def test_an_unreadable_flag_is_never_reported_as_maintenance(client, monkeypatch):
    _db_down(monkeypatch)
    body = client.get(GATED).text.lower()
    assert "under maintenance" not in body


# ── the connection pressure ─────────────────────────────────────────────────────────────

def test_the_flag_is_not_read_once_per_request(client, monkeypatch):
    """The fix for the connection exhaustion: a burst of console requests must cost one
    database connection, not one each."""
    calls = _flag(monkeypatch, False)
    for _ in range(5):
        client.get(GATED)
    assert calls["n"] == 1, f"read the maintenance flag {calls['n']} times for 5 requests"


def test_the_cache_expires_so_the_switch_still_takes_effect(client, monkeypatch):
    calls = _flag(monkeypatch, False)
    client.get(GATED)
    # Age the entry past its TTL rather than sleeping.
    main_app._maintenance_cache["at"] -= main_app._MAINTENANCE_TTL + 1
    client.get(GATED)
    assert calls["n"] == 2


# ── what must NOT have changed ──────────────────────────────────────────────────────────

def test_maintenance_mode_still_refuses_gated_traffic(client, monkeypatch):
    _flag(monkeypatch, True)
    r = client.get(GATED)
    assert r.status_code == 503
    assert "maintenance" in r.json()["detail"].lower()


def test_an_ungated_path_is_untouched_by_an_outage(client, monkeypatch):
    """The contact form is not behind the gate, so a database problem reading a platform flag
    must not stop a prospect sending an enquiry."""
    _db_down(monkeypatch)
    r = client.post("/api/contact", json={
        "first": "Ada", "last": "Lovelace", "email": "ada@example.com",
        "org": "", "country": "United Kingdom", "topic": "Other", "message": "Hello",
    })
    assert r.status_code == 202
