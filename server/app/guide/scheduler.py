"""Scheduled Guide maintenance (Security/Privacy §17, §17.1, SP-AT-16/17; ARCH §12.1/12.2,
ARCH-AT-10).

One background thread per process runs a maintenance tick every PURGE_INTERVAL:

  1. reconcile — external operations left `in_progress` past GUIDE_RECONCILE_STALE_MINUTES
     (the process died mid-call, or the downstream never answered) become `unknown`, and their
     lead flows move from `submitting` to `unknown`. Guide then tells the visitor completion
     is unconfirmed instead of "still sending" forever, and the rows become purgeable;
  2. dead-letter — an `unknown` operation still unresolved GUIDE_RECONCILE_DEAD_LETTER_HOURS
     after its last attempt cannot be resolved automatically (the contact flow offers no
     status lookup), so it leaves the reconciliation queue as `dead_letter` with an audit
     record (ARCH §12.2 "dead-letter or failure queues must exist"). The visitor's view does
     not change: still "unconfirmed", still the direct contact route, still no blind resend;
  3. purge — purge.purge_once(), the bounded SKIP LOCKED retention deletes. A failure is
     counted and logged (GUIDE_PURGE_FAILED) but does not hide the backlog check;
  4. backlog check — every sensitive table is measured for rows more than
     GUIDE_PURGE_GRACE_HOURS past purge_after; any such row is an operational breach
     (SP-AT-17), logged at ERROR with the stable marker GUIDE_RETENTION_BACKLOG and exported
     as guide_purge_backlog_rows{table}.

A failed tick logs GUIDE_MAINTENANCE_FAILED and the next attempt comes after RETRY_DELAY, not
an hour, so repeated failure alerts within the §17.1 15-minute target.

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

from . import metrics, purge, retention
from .db import get_maintenance_engine, new_maintenance_session
from .sessions import audit
from .settings import get_settings

log = logging.getLogger(__name__)

PURGE_INTERVAL = timedelta(hours=1)
FIRST_RUN_DELAY = timedelta(minutes=2)       # let the process finish starting first
RETRY_DELAY = timedelta(minutes=5)           # after a failed tick (§17.1: alert within 15m)
ADVISORY_LOCK_KEY = 0x6775_6964  # "guid"

# Every table holding sensitive or session-local data, with the deadline column and any
# rows that are legitimately not purgeable yet (Security/Privacy §17; ERD §12.1).
# name -> (table, deadline column, extra predicate)
BACKLOG_CHECKS: dict[str, tuple[str, str, str]] = {
    "lead_field_values": ("guide_lead_field_values", "purge_after", ""),
    "handoffs": ("guide_handoffs", "purge_after", ""),
    "feedback_comment": ("guide_feedback", "comment_purge_after",
                         "AND comment_ciphertext IS NOT NULL"),
    "messages": ("guide_messages", "purge_after", ""),
    "support_step_events": ("guide_support_step_events", "purge_after", ""),
    "support_flows": ("guide_support_flows", "purge_after", ""),
    "lead_flows": ("guide_lead_flows", "purge_after", ""),
    "external_operations": ("guide_external_operations", "purge_after",
                            "AND outcome <> 'in_progress'"),
    "context_versions": ("guide_session_context_versions", "purge_after", ""),
    "event_contexts": ("guide_public_event_contexts", "purge_after", ""),
    "consents": ("guide_consents", "purge_after", ""),
    "feedback": ("guide_feedback", "purge_after", ""),
    "sessions": ("guide_sessions", "purge_after", ""),
    "audit": ("guide_audit_events", "purge_after", ""),
}

_started = False
_start_lock = threading.Lock()


def stale_after() -> timedelta:
    return timedelta(minutes=get_settings().GUIDE_RECONCILE_STALE_MINUTES)


def dead_letter_after() -> timedelta:
    return timedelta(hours=get_settings().GUIDE_RECONCILE_DEAD_LETTER_HOURS)


def reconcile_stale_operations(db) -> int:
    """`in_progress` operations older than the stale window become `unknown` (never
    `confirmed`: an unanswered downstream call is not proof of delivery)."""
    ops = db.execute(text(
        "UPDATE guide_external_operations SET outcome = 'unknown', reconciled_at = now() "
        "WHERE outcome = 'in_progress' AND created_at < now() - :stale "
        "RETURNING subject_id"), {"stale": stale_after()}).scalars().all()
    if ops:
        db.execute(text(
            "UPDATE guide_lead_flows SET status = 'unknown', updated_at = now() "
            "WHERE flow_id = ANY(:ids) AND status = 'submitting'"), {"ids": list(ops)})
    db.commit()
    if ops:
        metrics.incr("guide_reconcile_total", len(ops), outcome="unknown")
    return len(ops)


def dead_letter_unknown_operations(db) -> int:
    """`unknown` operations unresolved past the dead-letter window -> `dead_letter`.

    The lead flow stays `unknown` on purpose: to the visitor the request is still unconfirmed,
    so leads.submit keeps refusing a blind resubmit and keeps offering the direct contact
    route, and the local copy is still purged by its own (failed/unknown) deadline."""
    rows = db.execute(text(
        "UPDATE guide_external_operations SET outcome = 'dead_letter', reconciled_at = now() "
        "WHERE outcome = 'unknown' AND COALESCE(last_attempt_at, created_at) < now() - :after "
        "RETURNING operation_id, operation_type"), {"after": dead_letter_after()}).all()
    for operation_id, operation_type in rows:
        audit(db, None, "external_operation_dead_lettered", operation_id=str(operation_id),
              operation_type=operation_type)
    db.commit()
    if rows:
        metrics.incr("guide_reconcile_total", len(rows), outcome="dead_letter")
    return len(rows)


def retention_backlog(db) -> dict[str, int]:
    """Tables with rows more than the purge grace past their deadline -> row counts."""
    out = {}
    grace = retention.purge_grace()
    for name, (table, column, extra) in BACKLOG_CHECKS.items():
        n = db.execute(text(f"SELECT count(*) FROM {table} "  # noqa: S608 — fixed allowlist
                            f"WHERE {column} < now() - :late {extra}"),
                       {"late": grace}).scalar() or 0
        metrics.observe("guide_purge_backlog_rows", float(n), table=name)
        if n:
            out[table] = n
            metrics.incr("guide_purge_backlog_alerts_total", table=name)
    return out


def tick() -> dict:
    """One maintenance pass. Returns what it did ({} when another process holds the lock).

    The advisory lock lives on a dedicated connection held for the whole pass: a Session
    hands its connection back to the pool on commit, which would strand a session lock."""
    purge_failed = False
    with get_maintenance_engine().connect() as lock_conn:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                 {"k": ADVISORY_LOCK_KEY}).scalar():
            return {}
        try:
            db = new_maintenance_session()
            try:
                reconciled = reconcile_stale_operations(db)
                dead_lettered = dead_letter_unknown_operations(db)
                try:
                    purged = purge.purge_once()
                except Exception:
                    # Reported, then the backlog is still measured: a broken purge must be
                    # visible as backlog (SP-AT-16), not just as a stack trace.
                    log.exception("GUIDE_PURGE_FAILED")
                    purged, purge_failed = {}, True
                    db.rollback()
                backlog = retention_backlog(db)
            finally:
                db.close()
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            lock_conn.commit()
    if reconciled:
        log.warning("guide maintenance: %d stale operation(s) marked unknown", reconciled)
    if dead_lettered:
        log.error("GUIDE_RECONCILE_DEAD_LETTER %d unknown operation(s) dead-lettered",
                  dead_lettered)
    if purged:
        log.info("guide retention purge: %s", purged)
    if backlog:
        log.error("GUIDE_RETENTION_BACKLOG rows more than %s past purge_after: %s",
                  retention.purge_grace(), backlog)
    metrics.incr("guide_maintenance_ticks_total",
                 outcome="purge_failed" if purge_failed else "ok")
    return {"reconciled": reconciled, "dead_lettered": dead_lettered, "purged": purged,
            "purge_failed": purge_failed, "backlog": backlog}


def _loop(stop: threading.Event) -> None:
    delay = FIRST_RUN_DELAY.total_seconds()
    while not stop.wait(delay):
        failed = False
        try:
            failed = bool(tick().get("purge_failed"))
        except Exception:
            log.exception("GUIDE_MAINTENANCE_FAILED")
            metrics.incr("guide_maintenance_ticks_total", outcome="failed")
            failed = True
        base = RETRY_DELAY if failed else PURGE_INTERVAL
        # Jitter so many instances don't all wake on the same second.
        delay = base.total_seconds() * random.uniform(0.9, 1.1)


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
