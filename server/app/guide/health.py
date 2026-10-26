"""Health, readiness and metrics (C8; ARCH §17.1 "Health endpoints must distinguish liveness
from readiness so one failing optional dependency does not cause restart storms"; ARCH §26
"Production migration status is part of readiness checks"; ARCH §20 observability).

  GET /health/live       200 whenever the process can answer. No dependency is touched.
  GET /health/ready      200 only when the Guide can serve: database reachable, schema at
                         the migration head, an active RAG release present. Circuit breaker
                         states are REPORTED, not gating: an open model/contact/status
                         breaker already maps to a degraded Guide state, and failing
                         readiness on it would restart healthy replicas.
  GET /internal/metrics  Prometheus text, only with X-Guide-Metrics-Token equal to
                         GUIDE_METRICS_TOKEN; otherwise 404, and always 404 when the setting is
                         empty — the route does not admit to existing.

None of these bodies carries a hostname, URL, version string of a dependency, exception text
or secret (API-AT-20). They are operational routes, outside the public v1 contract, so they
are excluded from the OpenAPI document (API §22: no internal-only routes in public docs).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

from . import breaker, crypto, metrics
from . import db as guide_db
from .errors import GuideProblem
from .settings import get_settings

log = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
VERSION_TABLE = "guide_alembic_version"
METRICS_HEADER = "x-guide-metrics-token"
_NO_STORE = {"Cache-Control": "no-store"}


@lru_cache
def migration_heads() -> frozenset[str]:
    """Head revision(s) of the Guide migration scripts shipped with this build."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return frozenset(ScriptDirectory.from_config(cfg).get_heads())


def _check_database() -> dict[str, str]:
    checks = {"database": "fail", "migrations": "unknown", "rag_release": "unknown"}
    db = None
    try:
        db = guide_db.new_session()
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
        applied = {r[0] for r in db.execute(text(f"SELECT version_num FROM {VERSION_TABLE}"))}
        checks["migrations"] = "ok" if applied and applied == set(migration_heads()) else "behind"
        active = db.execute(text("SELECT 1 FROM guide_rag.rag_index_releases "
                                 "WHERE status = 'active' LIMIT 1")).first()
        checks["rag_release"] = "ok" if active else "missing"
    except Exception as exc:  # never leak the reason; log the class only
        log.warning("guide readiness check failed: %s", type(exc).__name__)
        if db is not None:
            db.rollback()
    finally:
        if db is not None:
            db.close()
    return checks


def live():
    return JSONResponse({"status": "ok"}, headers=_NO_STORE)


def ready():
    checks = _check_database()
    ok = all(v == "ok" for v in checks.values())
    s = get_settings()
    body = {
        "status": "ready" if ok else "not_ready",
        "checks": checks,
        # Reported for operators; deliberately not part of the ready/not-ready decision.
        "breakers": breaker.states(),
        "guide_enabled": bool(s.GUIDE_ENABLED and not s.production_problems()),
    }
    metrics.incr("guide_readiness_checks_total", result="ready" if ok else "not_ready")
    return JSONResponse(body, status_code=200 if ok else 503, headers=_NO_STORE)


def internal_metrics(request: Request):
    expected = get_settings().GUIDE_METRICS_TOKEN
    supplied = request.headers.get(METRICS_HEADER, "")
    if not expected or not supplied or not crypto.constant_time_equal(
            supplied.encode(), expected.encode()):
        raise GuideProblem("GUIDE_RESOURCE_NOT_FOUND")
    return PlainTextResponse(metrics.render(), headers=_NO_STORE,
                             media_type="text/plain; version=0.0.4; charset=utf-8")


# (path, endpoint) — registered on the Guide router by router.py, outside the OpenAPI document.
ROUTES = (("/health/live", live), ("/health/ready", ready),
          ("/internal/metrics", internal_metrics))
