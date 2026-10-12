"""Operator Guide test harness.

These tests exercise Guide against the REAL platform: real models, real auth sessions, real
platform endpoints for writes. They therefore need a platform database — a dedicated one.

Hard refusals (before anything imports app.db):
  * OPGUIDE_TEST_DATABASE_URL must be set,
  * it must differ from DATABASE_URL,
  * ENVIRONMENT must not be production.
The suite truncates every table in that database at session start.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
_test_url = os.environ.get("OPGUIDE_TEST_DATABASE_URL") or _file.get("OPGUIDE_TEST_DATABASE_URL")
_dev_url = os.environ.get("DATABASE_URL") or _file.get("DATABASE_URL")
if not _test_url:
    sys.exit("OPERATOR GUIDE TESTS REFUSED: set OPGUIDE_TEST_DATABASE_URL to a dedicated "
             "test database.")
if _test_url == _dev_url:
    sys.exit("OPERATOR GUIDE TESTS REFUSED: OPGUIDE_TEST_DATABASE_URL equals DATABASE_URL.")
if (os.environ.get("ENVIRONMENT") or _file.get("ENVIRONMENT", "")).lower() in ("production",
                                                                               "prod"):
    sys.exit("OPERATOR GUIDE TESTS REFUSED: ENVIRONMENT is production.")

os.environ["DATABASE_URL"] = _test_url
# "development", not "test": platform email links refuse http APP_URLs outside development,
# and confirmed actions run the platform's real endpoints, notifications included.
os.environ["ENVIRONMENT"] = "development"
os.environ["OPGUIDE_LLM_PROVIDER"] = "mock"
os.environ["OPGUIDE_ENABLED"] = "true"
os.environ["OPGUIDE_MESSAGES_PER_MINUTE"] = "1000"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import create_tables  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.event import Event, EventAssignment  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.user import User  # noqa: E402
from app.operator_guide import llm as og_llm  # noqa: E402
from app.operator_guide import router as og_router  # noqa: E402
from app.operator_guide.config import get_settings  # noqa: E402
from app.operator_guide.schema import OpGuideBase, create_schema  # noqa: E402
from app.security import create_access_token  # noqa: E402

BASE = "/api/operator-guide"


@pytest.fixture(scope="session", autouse=True)
def _database():
    create_tables.ensure_schema()
    create_schema(engine)
    names = list(Base.metadata.tables) + list(OpGuideBase.metadata.tables)  # CASCADE orders
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " CASCADE"))
    yield


@pytest.fixture(autouse=True)
def _fresh_caches():
    get_settings.cache_clear()
    og_llm.provider.cache_clear()
    og_router._schema_ok = False
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


class World:
    """Factories for organizations, users and events — each test builds its own tenant, so
    tests never see each other's rows."""

    def __init__(self, db):
        self.db = db

    def org(self, name: str | None = None, **fields) -> Organization:
        org = Organization(name=name or f"Org {uuid.uuid4().hex[:6]}", **fields)
        self.db.add(org)
        self.db.commit()
        return org

    def user(self, org: Organization, role: str = "org_admin", **fields) -> User:
        tag = uuid.uuid4().hex[:10]
        u = User(org_id=org.id, full_name=fields.pop("full_name", f"{role} {tag}"),
                 email=f"{tag}@opguide.test", username=f"u{tag}", password_hash="x",
                 role=role, is_active=True, email_verified=True, **fields)
        self.db.add(u)
        self.db.commit()
        return u

    def event(self, org: Organization, creator: User, **fields) -> Event:
        values = dict(title=f"Event {uuid.uuid4().hex[:6]}", status="published",
                      visibility="public", start_time=datetime.now(timezone.utc) +
                      timedelta(days=2), chat_enabled=True, qa_enabled=False,
                      polls_enabled=False, recording_enabled=False,
                      registration_required=False, waiting_room_enabled=False)
        values.update(fields)
        ev = Event(org_id=org.id, created_by=creator.id, **values)
        self.db.add(ev)
        self.db.commit()
        return ev

    def assign(self, event: Event, user: User, role: str = "host") -> None:
        self.db.add(EventAssignment(event_id=event.id, user_id=user.id, role=role))
        self.db.commit()

    def headers(self, user: User) -> dict:
        token = create_access_token(user, False, db=self.db)
        self.db.commit()
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db):
    return World(db)


@pytest.fixture
def ask(client):
    """POST a message; returns the parsed response (asserting 200 unless told otherwise)."""
    def _ask(headers: dict, text: str = "", *, status: int = 200, **body):
        res = client.post(f"{BASE}/messages", headers=headers, json={"text": text, **body})
        assert res.status_code == status, res.text
        return res.json()
    return _ask


def blocks_of(reply: dict, kind: str) -> list[dict]:
    return [b for b in reply["message"]["payload"]["blocks"] if b.get("type") == kind]
