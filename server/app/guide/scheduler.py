"""Scheduled Guide maintenance (Security/Privacy §17.1, SP-AT-16/17; ARCH §12.1, ARCH-AT-10).

One background thread per process runs a maintenance tick every PURGE_INTERVAL:

  1. reconcile — external operations left `in_progress` past STALE_AFTER (the process died
     mid-call, or the downstream never answered) become `unknown`, and their lead flows move
     from `submitting` to `unknown`. Guide then tells the visitor completion is unconfirmed
     instead of "still sending" forever, and the rows become purgeable;
  2. purge — purge.purge_once(), the bounded SKIP LOCKED retention deletes;
  3. backlog check — any sensitive row more than BACKLOG_ALERT past its purge_after is logged
     at ERROR with the stable marker GUIDE_RETENTION_BACKLOG (alert on it in Cloud Logging);
     a failed tick logs GUIDE_MAINTENANCE_FAILED.

A Postgres advisory lock makes exactly one process do the work per tick, however many
instances or workers run; the others skip. The thread is a daemon and never touches the
platform: a failure here only delays retention, and is reported, never raised.

`python -m app.guide.purge` remains available for a manual or externally scheduled run.
"""
from __future__ import annotations

import logging
import random
import threading
from datetime import timedelta

from sqlalchemy import text

from . import purge
from .db import get_engine, new_session
from .settings import get_settings

log = logging.getLogger(__name__)

PURGE_INTERVAL = timedelta(hours=1)
FIRST_RUN_DELAY = timedelta(minutes=2)       # let the process finish starting first
STALE_AFTER = timedelta(minutes=15)
BACKLOG_ALERT = timedelta(hours=24)
ADVISORY_LOCK_KEY = 0x6775_6964  # "guid"

# Tables whose purge_after is the retention promise (Security §17).
_BACKLOG_TABLES = ("guide_messages", "guide_sessions", "guide_lead_field_values",
                   "guide_handoffs", "guide_session_context_versions",
                   "guide_public_event_contexts")

_started = False
_start_lock = threading.Lock()


def reconcile_stale_operations(db) -> int:
    """`in_progress` operations older than STALE_AFTER become `unknown` (never `confirmed`:
    an unanswered downstream call is not proof of delivery)."""
    cutoff = {"stale": STALE_AFTER}
    ops = db.execute(text(
        "UPDATE guide_external_operations SET outcome = 'unknown', reconciled_at = now() "
        "WHERE outcome = 'in_progress' AND created_at < now() - :stale "
        "RETURNING subject_id"), cutoff).scalars().all()
    if ops:
        db.execute(text(
            "UPDATE guide_lead_flows SET status = 'unknown', updated_at = now() "
            "WHERE flow_id = ANY(:ids) AND status = 'submitting'"), {"ids": list(ops)})
    db.commit()
    return len(ops)


def retention_backlog(db) -> dict[str, int]:
    out = {}
    for table in _BACKLOG_TABLES:
        n = db.execute(text(f"SELECT count(*) FROM {table} "  # noqa: S608 — fixed allowlist
                            "WHERE purge_after < now() - :late"),
                       {"late": BACKLOG_ALERT}).scalar() or 0
        if n:
            out[table] = n
    return out


def tick() -> dict:
    """One maintenance pass. Returns what it did ({} when another process holds the lock).

    The advisory lock lives on a dedicated connection held for the whole pass: a Session
    hands its connection back to the pool on commit, which would strand a session lock."""
    with get_engine().connect() as lock_conn:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                 {"k": ADVISORY_LOCK_KEY}).scalar():
            return {}
        try:
            db = new_session()
            try:
                reconciled = reconcile_stale_operations(db)
                purged = purge.purge_once()
                backlog = retention_backlog(db)
            finally:
                db.close()
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            lock_conn.commit()
    if reconciled:
        log.warning("guide maintenance: %d stale operation(s) marked unknown", reconciled)
    if purged:
        log.info("guide retention purge: %s", purged)
    if backlog:
        log.error("GUIDE_RETENTION_BACKLOG rows more than %s past purge_after: %s",
                  BACKLOG_ALERT, backlog)
    return {"reconciled": reconciled, "purged": purged, "backlog": backlog}


def _loop(stop: threading.Event) -> None:
    delay = FIRST_RUN_DELAY.total_seconds()
    while not stop.wait(delay):
        try:
            tick()
        except Exception:
            log.exception("GUIDE_MAINTENANCE_FAILED")
        # Jitter so many instances don't all wake on the same second.
        delay = PURGE_INTERVAL.total_seconds() * random.uniform(0.9, 1.1)


def start() -> bool:
    """Start the maintenance thread once per process (idempotent)."""
    global _started
    s = get_settings()
    if not (s.GUIDE_ENABLED and s.GUIDE_DATABASE_URL and s.GUIDE_MAINTENANCE_ENABLED):
        return False
    with _start_lock:
        if _started:
            return False
        threading.Thread(target=_loop, args=(threading.Event(),), name="guide-maintenance",
                         daemon=True).start()
        _started = True
    return True
