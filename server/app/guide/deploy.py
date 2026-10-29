"""Deploy-time preparation of the public Guide's own database.

Called by server/create_tables.py, the step every deployment already runs before new code
serves (Cloud Build's `migrate` gate; the VM deploy). Brings GUIDE_DATABASE_URL to the
current code, idempotently:

  1. create the database if it does not exist yet (needs CREATEDB on that server; otherwise
     reports the one statement an administrator must run);
  2. apply the Guide's Alembic migrations (schema + seeded destinations/config);
  3. rebuild the knowledge index only when the approved sources changed. The rebuild is
     gated (content approvals + offline evaluation, knowledge/ingest.build_release); a
     refused or failed rebuild never fails the deploy and never removes the serving index:
     the currently active release keeps serving and the refusal is logged, audited
     (`index_rebuild_refused`) and reported. With no active release at all the Guide starts
     DEGRADED — no generated answers, direct routes only — and the platform is unaffected;
  4. re-apply the least-privilege grants (deploy_sql/02_guide_roles_and_grants.sql) when the
     guide_* roles exist, so tables added by a migration are granted at once.

Principals (ERD §19, RBAC §20 "migration identity: DDL only during controlled deployment;
not runtime"): steps 1–2 and 4 use GUIDE_MIGRATION_DATABASE_URL when set (role
guide_migrator), step 3 uses GUIDE_INGEST_DATABASE_URL (role guide_ingest) and falls back to
the migration URL; the running API only ever uses GUIDE_DATABASE_URL (role guide_runtime).
With none of them set, everything uses GUIDE_DATABASE_URL (single-credential development).

Nothing here touches the platform database, and it never imports platform code.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .db import engine_url
from .settings import get_settings

SERVER_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = SERVER_DIR / "guide_alembic.ini"
MIGRATIONS = Path(__file__).resolve().parent / "migrations"
GRANTS_SQL = SERVER_DIR.parent / "deploy_sql" / "02_guide_roles_and_grants.sql"


log = logging.getLogger(__name__)


class DeployError(Exception):
    pass


@dataclass
class KnowledgeOutcome:
    """What the knowledge step did. status: current | rebuilt | kept_active | no_active."""
    status: str
    line: str
    active_release_id: str | None = None

    @property
    def degraded(self) -> bool:
        return self.status == "no_active"


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


def migration_url() -> str:
    s = get_settings()
    return s.GUIDE_MIGRATION_DATABASE_URL or s.GUIDE_DATABASE_URL


def ingest_url() -> str:
    s = get_settings()
    return s.GUIDE_INGEST_DATABASE_URL or migration_url()


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS))  # independent of the cwd
    # migrations/env.py prefers `-x url=...` over GUIDE_DATABASE_URL: the DDL principal.
    cfg.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    command.upgrade(cfg, "head")


def _apply_grants(url: str) -> str:
    """Idempotent role grants. Skipped (not failed) when the roles were never created or this
    principal may not grant — an administrator then runs the SQL file once by hand."""
    if not GRANTS_SQL.exists():
        return "grants skipped: deploy_sql/02_guide_roles_and_grants.sql not found"
    engine = create_engine(engine_url(url), connect_args={"connect_timeout": 15})
    try:
        with engine.connect() as conn:
            if not conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = "
                                     "'guide_runtime'")).scalar():
                return "grants skipped: guide_* roles not created (see deploy_sql/)"
            raw = conn.connection  # raw DBAPI, no parameters: the file uses format('%I')
            try:
                raw.cursor().execute(GRANTS_SQL.read_text(encoding="utf-8"))
                raw.commit()
            except Exception as exc:
                raw.rollback()
                return f"grants skipped: {type(exc).__name__} (run the SQL file as an admin)"
        return "least-privilege grants applied"
    finally:
        engine.dispose()


def _active_release_id(db: Session) -> str | None:
    from .knowledge.models import RagIndexRelease
    return db.scalar(select(RagIndexRelease.release_id).where(
        RagIndexRelease.status == "active"))


def _reason(exc: Exception) -> str:
    """A short, content-free reason for logs/audit: the error class plus the gate's own
    message (source ids, approval states, failure codes — never source text or secrets)."""
    text_ = " ".join(str(exc).split())[:300]
    return f"{type(exc).__name__}: {text_}" if text_ else type(exc).__name__


def refresh_knowledge() -> KnowledgeOutcome:
    """Rebuild the index when sources changed; on refusal/failure keep what is serving."""
    from .knowledge import ingest
    from .sessions import audit

    profile = get_settings().GUIDE_EMBEDDING_PROFILE
    engine = create_engine(engine_url(ingest_url()), pool_pre_ping=True)
    db = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        try:
            docs = ingest.load_sources()
            if ingest.is_current(db, docs, profile):
                return KnowledgeOutcome("current", "knowledge index up to date",
                                        _active_release_id(db))
            release = ingest.build_release(db, docs)
            return KnowledgeOutcome(
                "rebuilt", f"knowledge index rebuilt: {release.unit_count} units from "
                           f"{release.source_count} sources ({release.release_id})",
                release.release_id)
        except Exception as exc:  # noqa: BLE001 — refusal is an outcome, not a deploy failure
            db.rollback()
            reason = _reason(exc)
            active = _active_release_id(db)
            if active:
                outcome = KnowledgeOutcome(
                    "kept_active", f"knowledge index NOT rebuilt ({reason}); still serving "
                                   f"active release {active}", active)
                log.warning("GUIDE_INDEX_REBUILD_REFUSED keeping %s: %s", active, reason)
            else:
                outcome = KnowledgeOutcome(
                    "no_active", f"knowledge index NOT built ({reason}); NO active release — "
                                 "the Guide starts degraded (direct routes only, no "
                                 "generated answers)")
                log.error("GUIDE_INDEX_UNAVAILABLE no active release: %s", reason)
            audit(db, None, "index_rebuild_refused", error_class=type(exc).__name__,
                  kept_release_id=active, degraded=active is None)
            db.commit()
            return outcome
    finally:
        db.close()
        engine.dispose()


def prepare_database(outcome: list | None = None) -> list[str]:
    """Run every step; returns a line per step. Raises DeployError (or the underlying error)
    only when the Guide DATABASE cannot be prepared (create/migrate). The knowledge step
    never raises; its KnowledgeOutcome is appended to `outcome` when a list is passed."""
    if not get_settings().GUIDE_DATABASE_URL:
        return ["skipped: GUIDE_DATABASE_URL is not set (public Guide database not configured)"]
    url = migration_url()
    report = [_ensure_database(url)]
    _migrate(url)
    report.append("migrations at head")
    knowledge = refresh_knowledge()
    if outcome is not None:
        outcome.append(knowledge)
    report.append(knowledge.line)
    report.append(_apply_grants(url))
    return report


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when the Guide can serve (index current, rebuilt, or the previous release kept);
    2 when there is no active release at all (the Guide runs degraded — a dedicated pipeline
    step can alert on it); with --strict-knowledge also 3 when a rebuild was refused."""
    p = argparse.ArgumentParser(prog="python -m app.guide.deploy")
    p.add_argument("--strict-knowledge", action="store_true",
                   help="exit 3 when a knowledge rebuild was refused (previous release kept)")
    args = p.parse_args(argv)
    outcome: list[KnowledgeOutcome] = []
    for line in prepare_database(outcome):
        print(line)
    if outcome and outcome[0].degraded:
        return 2
    if outcome and outcome[0].status == "kept_active" and args.strict_knowledge:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
