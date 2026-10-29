"""Retrieval-store connection pool (RAG §7.1 "service-only access", §28 "independent
connection-pool budgets from the operational Guide database").

Retrieval runs on its own engine, so a burst of retrieval queries cannot exhaust the pool that
session/message writes depend on (and vice versa). GUIDE_RAG_DATABASE_URL defaults to the
Guide database: at launch the retrieval store is the guide_rag schema in the same managed
PostgreSQL (RAG §7.1 "may share managed PostgreSQL HA cluster if isolation and connection
budgets are preserved"). It must reach the knowledge registry tables, because publication /
quarantine status is joined into every candidate query (RAG §11).

Created lazily, like db.py: importing this module never opens a connection.
"""
from __future__ import annotations

import threading

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..db import engine_url
from ..settings import get_settings

_lock = threading.Lock()
_engine: Engine | None = None
_sessionmaker: sessionmaker | None = None


def rag_database_url() -> str:
    s = get_settings()
    return s.GUIDE_RAG_DATABASE_URL or s.GUIDE_DATABASE_URL


def get_engine() -> Engine:
    global _engine, _sessionmaker
    if _engine is None:
        with _lock:
            if _engine is None:
                url = rag_database_url()
                if not url:
                    raise RuntimeError("retrieval store database is not configured")
                # Read-mostly, short queries: a small pool with a short checkout timeout so a
                # saturated retrieval store degrades the Guide (G2) instead of queueing turns.
                _engine = create_engine(engine_url(url), pool_pre_ping=True, pool_size=4,
                                        max_overflow=4, pool_recycle=1800, pool_timeout=3)
                _sessionmaker = sessionmaker(bind=_engine, autoflush=False,
                                             expire_on_commit=False)
    return _engine


def new_session() -> Session:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker()


def reset_engine() -> None:
    """Drop the cached engine (tests switch database URLs)."""
    global _engine, _sessionmaker
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine, _sessionmaker = None, None
