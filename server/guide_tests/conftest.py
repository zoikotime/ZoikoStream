"""Guide test harness.

Refuses to run unless GUIDE_TEST_DATABASE_URL is set and differs from GUIDE_DATABASE_URL —
the same hard-refusal posture as server/conftest.py, for the same reason: these tests TRUNCATE
Guide tables. The swap into GUIDE_DATABASE_URL happens at module scope, before anything
constructs GuideSettings.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


def _dotenv(path: Path) -> dict:
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


_file = _dotenv(ROOT_ENV)
_test_url = os.environ.get("GUIDE_TEST_DATABASE_URL") or _file.get("GUIDE_TEST_DATABASE_URL")
_dev_url = os.environ.get("GUIDE_DATABASE_URL") or _file.get("GUIDE_DATABASE_URL")
if not _test_url:
    sys.exit("GUIDE TESTS REFUSED: set GUIDE_TEST_DATABASE_URL to a dedicated test database.")
if _test_url == _dev_url:
    sys.exit("GUIDE TESTS REFUSED: GUIDE_TEST_DATABASE_URL equals GUIDE_DATABASE_URL.")
if (os.environ.get("ENVIRONMENT") or _file.get("ENVIRONMENT", "")).lower() in ("production",
                                                                               "prod"):
    sys.exit("GUIDE TESTS REFUSED: ENVIRONMENT is production.")

os.environ["GUIDE_DATABASE_URL"] = _test_url
os.environ["GUIDE_ENABLED"] = "true"
os.environ["ENVIRONMENT"] = "test"
os.environ["GUIDE_KEY_PROVIDER"] = "local"
os.environ.setdefault("GUIDE_LOCAL_MASTER_KEY", "a" * 64)
os.environ["GUIDE_EVENT_CONTEXT_KEY"] = "test-event-context-signing-key-0123456789"
os.environ["GUIDE_ALLOWED_ORIGINS"] = "https://www.zoikostream.test"
os.environ["GUIDE_ALLOW_MISSING_ORIGIN"] = "true"
os.environ["GUIDE_LLM_PROVIDER"] = "mock"
os.environ["GUIDE_MAINTENANCE_ENABLED"] = "false"  # tests drive purge/reconcile directly
os.environ["GUIDE_STATUS_ENDPOINT"] = ""  # D1 is mocked per test
os.environ["GUIDE_EMBEDDING_PROFILE"] = "hash:v1"  # deterministic, no model download
os.environ["GUIDE_CONTACT_ENDPOINT"] = "http://contact.invalid/api/contact"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.guide import (  # noqa: E402
    adapters, breaker, crypto, db as guide_db, llm, metrics, orchestrator, ratelimit, registry,
)
from app.guide.knowledge import embeddings, ingest  # noqa: E402
from app.guide.asgi import MOUNT_PATH, create_app  # noqa: E402
from app.guide.settings import get_settings  # noqa: E402

SERVER = Path(__file__).resolve().parents[1]
BASE = MOUNT_PATH
ORIGIN = "https://www.zoikostream.test"

# Session-local tables; config tables (destinations/runtime config/knowledge) are kept.
_LOCAL_TABLES = [
    "guide_lead_field_values", "guide_support_step_events", "guide_message_sources",
    "guide_message_actions", "guide_handoffs", "guide_external_operations",
    "guide_lead_flows", "guide_consents", "guide_feedback", "guide_support_flows",
    "guide_messages", "guide_session_context_versions", "guide_public_event_contexts",
    "guide_audit_events", "guide_sessions",
]


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _reset_caches():
    get_settings.cache_clear()
    crypto.provider.cache_clear()
    llm.get_provider.cache_clear()
    embeddings.get_embedder.cache_clear()
    registry.invalidate()
    ratelimit.reset_all()
    orchestrator._inflight.clear()
    adapters.set_lead_adapter(None)
    breaker.reset()
    metrics.reset()


def _truncate_existing_local_tables():
    """A run that stopped mid-way can leave session rows behind; seed-data downgrades then
    fail on their foreign keys. Clears whichever local tables exist (none on a fresh DB)."""
    with guide_db.get_engine().begin() as conn:
        existing = [t for t in _LOCAL_TABLES
                    if conn.execute(text("SELECT to_regclass(:t)"), {"t": t}).scalar()]
        if existing:
            conn.execute(text("TRUNCATE " + ", ".join(existing) + " CASCADE"))


def ingest_corpus(directory: Path = FIXTURES / "knowledge"):
    """Build + activate a release from fixture sources (hash embeddings)."""
    db = guide_db.new_session()
    try:
        return ingest.build_release(db, ingest.load_sources(directory))
    finally:
        db.close()


def reset_knowledge():
    with guide_db.get_engine().begin() as conn:
        conn.execute(text("TRUNCATE guide_rag.rag_knowledge_units, guide_rag.rag_index_releases,"
                          " guide_message_sources CASCADE"))
        conn.execute(text("DELETE FROM guide_knowledge_source_versions"))
        conn.execute(text("DELETE FROM guide_knowledge_sources"))


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    _reset_caches()
    guide_db.reset_engine()
    cfg = Config(str(SERVER / "guide_alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER / "app/guide/migrations"))
    _truncate_existing_local_tables()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    _reset_caches()
    ingest_corpus()
    yield
    _truncate_existing_local_tables()
    guide_db.reset_engine()


@pytest.fixture(autouse=True)
def clean_tables(migrated_db):
    _reset_caches()
    with guide_db.get_engine().begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(_LOCAL_TABLES) + " CASCADE"))
        conn.execute(text("UPDATE guide_runtime_config SET config_value = "
                          "'{\"enabled\": true}' WHERE config_key = 'guide.enabled'"))
        conn.execute(text("DELETE FROM guide_runtime_config WHERE config_key LIKE "
                          "'guide.feature.%'"))
    yield
    _reset_caches()


@pytest.fixture
def client():
    host = FastAPI()
    host.mount(MOUNT_PATH, create_app())
    with TestClient(host, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def db():
    s = guide_db.new_session()
    yield s
    s.close()


def page_context(**overrides) -> dict:
    ctx = {"page_id": "home", "page_type": "home", "path": "/", "locale": "en-US",
           "device_class": "desktop", "entry_point": "launcher"}
    ctx.update(overrides)
    return ctx


class GuideClient:
    """A browser-like caller: holds the cookie (TestClient jar) and the CSRF token."""

    def __init__(self, client: TestClient, body: dict):
        self.c = client
        self.session_id = body["session_id"]
        self.csrf = body["csrf_token"]
        self.body = body

    def headers(self, **extra):
        return {"X-ZS-CSRF": self.csrf, "Origin": ORIGIN, **extra}

    def post(self, path: str, json=None, **kw):
        return self.c.post(f"{BASE}/sessions/{self.session_id}{path}", json=json,
                           headers=self.headers(**kw.pop("headers", {})), **kw)

    def patch(self, path: str, json=None):
        return self.c.patch(f"{BASE}/sessions/{self.session_id}{path}", json=json,
                            headers=self.headers())


def create_session(client: TestClient, **ctx) -> GuideClient:
    res = client.post(f"{BASE}/sessions", json={
        "client_session_nonce": str(uuid.uuid4()), "page_context": page_context(**ctx)},
        headers={"Origin": ORIGIN})
    assert res.status_code == 201, res.text
    return GuideClient(client, res.json())


@pytest.fixture
def guide(client) -> GuideClient:
    return create_session(client)
