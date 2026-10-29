"""The Guide's own relational data plane (ZST-DB-PUB-GUIDE-001 §1).

A separate engine on GUIDE_DATABASE_URL, created lazily: importing this module never opens a
connection, so an unreachable Guide database degrades the Guide (503) instead of failing the
platform's boot. There is no shared Base with the platform models and no cross-database join.
"""
from __future__ import annotations

import threading

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .settings import get_settings


class GuideBase(DeclarativeBase):
    pass


_lock = threading.Lock()
_engine: Engine | None = None
_sessionmaker: sessionmaker | None = None


def engine_url(url: str) -> str:
    # psycopg2 named explicitly: SQLAlchemy 2.1 defaults a bare postgresql:// URL to psycopg
    # (v3), which is not installed. Same reason as app/dburl.py, duplicated to keep this
    # package free of platform imports.
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


def get_engine() -> Engine:
    global _engine, _sessionmaker
    if _engine is None:
        with _lock:
            if _engine is None:
                url = get_settings().GUIDE_DATABASE_URL
                if not url:
                    raise RuntimeError("GUIDE_DATABASE_URL is not configured")
                # Small pool with its own budget: the Guide must not be able to exhaust the
                # platform's Postgres connections (ZST-ARCH-PUB-GUIDE-001 §16).
                _engine = create_engine(
                    engine_url(url), pool_pre_ping=True, pool_size=5, max_overflow=5,
                    pool_recycle=1800, pool_timeout=5,
                )
                _sessionmaker = sessionmaker(bind=_engine, autoflush=False,
                                             expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests switch GUIDE_DATABASE_URL)."""
    global _engine, _sessionmaker, _maint_engine, _maint_sessionmaker
    with _lock:
        if _engine is not None:
            _engine.dispose()
        if _maint_engine is not None:
            _maint_engine.dispose()
        _engine, _sessionmaker = None, None
        _maint_engine, _maint_sessionmaker = None, None


def new_session() -> Session:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker()


# ── Least-privilege principals (ERD §19, RBAC §20) ─────────────────────────────────────────
# The runtime API uses GUIDE_DATABASE_URL (role guide_runtime). Purge/reconcile may run as a
# separate guide_maintenance login (GUIDE_MAINTENANCE_DATABASE_URL); migrations and knowledge
# ingestion use their own URLs in deploy.py. Blank = fall back to the runtime engine, so a
# single-credential development setup keeps working.

_maint_engine: Engine | None = None
_maint_sessionmaker: sessionmaker | None = None


def get_maintenance_engine() -> Engine:
    global _maint_engine, _maint_sessionmaker
    url = get_settings().GUIDE_MAINTENANCE_DATABASE_URL
    if not url:
        return get_engine()
    if _maint_engine is None:
        with _lock:
            if _maint_engine is None:
                _maint_engine = create_engine(
                    engine_url(url), pool_pre_ping=True, pool_size=2, max_overflow=0,
                    pool_recycle=1800, pool_timeout=10)
                _maint_sessionmaker = sessionmaker(bind=_maint_engine, autoflush=False,
                                                   expire_on_commit=False)
    return _maint_engine


def new_maintenance_session() -> Session:
    if not get_settings().GUIDE_MAINTENANCE_DATABASE_URL:
        return new_session()
    get_maintenance_engine()
    assert _maint_sessionmaker is not None
    return _maint_sessionmaker()


def get_guide_db():
    db = new_session()
    try:
        yield db
    finally:
        db.close()
