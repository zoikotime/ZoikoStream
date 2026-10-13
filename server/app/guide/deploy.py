"""Deploy-time preparation of the public Guide's own database.

Called by server/create_tables.py, the step every deployment already runs before new code
serves (Cloud Build's `migrate` gate; the VM deploy). Brings GUIDE_DATABASE_URL to the
current code, idempotently:

  1. create the database if it does not exist yet (needs CREATEDB on that server; otherwise
     reports the one statement an administrator must run);
  2. apply the Guide's Alembic migrations (schema + seeded destinations/config);
  3. rebuild the knowledge index only when the approved sources changed.

Nothing here touches the platform database, and it never imports platform code.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from .db import engine_url
from .settings import get_settings

SERVER_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = SERVER_DIR / "guide_alembic.ini"
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


class DeployError(Exception):
    pass


def _ensure_database(url: str) -> str:
    """Create the Guide database when it is missing. Returns what happened."""
    target = make_url(engine_url(url))
    name = target.database
    # Connect to the server's maintenance database with the same credentials.
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT",
                          connect_args={"connect_timeout": 15})
    try:
        with admin.connect() as conn:
            if conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                            {"n": name}).scalar():
                return f"database {name} exists"
            try:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
            except Exception as exc:
                raise DeployError(
                    f'database "{name}" does not exist and this user cannot create it '
                    f"({type(exc).__name__}). An administrator must run once: "
                    f'CREATE DATABASE "{name}";') from None
            return f"database {name} created"
    finally:
        admin.dispose()


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS))  # independent of the cwd
    command.upgrade(cfg, "head")


def _refresh_knowledge() -> str:
    from .db import new_session
    from .knowledge import ingest

    docs = ingest.load_sources()
    profile = get_settings().GUIDE_EMBEDDING_PROFILE
    db = new_session()
    try:
        if ingest.is_current(db, docs, profile):
            return "knowledge index up to date"
        release = ingest.build_release(db, docs)
        return f"knowledge index rebuilt: {release.unit_count} units from {release.source_count} sources"
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def prepare_database() -> list[str]:
    """Run every step; returns a line per step. Raises DeployError (or the underlying error)
    when the Guide database cannot be prepared."""
    url = get_settings().GUIDE_DATABASE_URL
    if not url:
        return ["skipped: GUIDE_DATABASE_URL is not set (public Guide database not configured)"]
    report = [_ensure_database(url)]
    _migrate()
    report.append("migrations at head")
    report.append(_refresh_knowledge())
    return report


if __name__ == "__main__":
    for line in prepare_database():
        print(line)
