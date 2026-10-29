"""Retention purge (ZST-DB-PUB-GUIDE-001 §12; Security/Privacy §17).

Deletes rows whose purge_after has passed, sensitive children first, in bounded batches with
SKIP LOCKED so concurrent runs never contend. Emits a non-content audit record per run.

How it runs: scheduler.py runs it hourly in a background thread (one process at a time via
an advisory lock), with stale-operation reconciliation and a backlog alert. `run_if_due()` is
still called opportunistically from session creation as a second trigger, and
`python -m app.guide.purge` runs it once by hand.

Every table is purged by ITS OWN purge_after. Rows whose retention outlives the session —
consent receipts (24 months), structured feedback (12 months), external operations under
reconciliation and audit metadata — reference the session with ON DELETE SET NULL (ERD §3.1,
migration 0005), so neither the normal nor the accelerated session purge removes them early.

Runs on the maintenance principal (db.new_maintenance_session: GUIDE_MAINTENANCE_DATABASE_URL
when set) and emits guide_purge_rows_total{table} / guide_purge_runs_total{outcome}.
"""
from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import text

from . import metrics
from .db import new_maintenance_session
from .sessions import audit

log = logging.getLogger(__name__)

PURGE_INTERVAL = 3600.0
BATCH = 500

# Order matters (DB §12.1): sensitive children, then workflow rows, then sessions.
_STEPS = [
    ("feedback_comment",
     "UPDATE guide_feedback SET comment_ciphertext = NULL, comment_purge_after = NULL "
     "WHERE feedback_id IN (SELECT feedback_id FROM guide_feedback WHERE comment_purge_after "
     "<= now() LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("lead_field_values", "DELETE FROM guide_lead_field_values WHERE (flow_id, field_id) IN "
     "(SELECT flow_id, field_id FROM guide_lead_field_values WHERE purge_after <= now() "
     "LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("handoffs", "DELETE FROM guide_handoffs WHERE handoff_id IN (SELECT handoff_id FROM "
     "guide_handoffs WHERE purge_after <= now() LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("support_step_events", "DELETE FROM guide_support_step_events WHERE event_id IN (SELECT "
     "event_id FROM guide_support_step_events WHERE purge_after <= now() LIMIT :n FOR UPDATE "
     "SKIP LOCKED)"),
    ("support_flows", "DELETE FROM guide_support_flows WHERE flow_id IN (SELECT flow_id FROM "
     "guide_support_flows WHERE purge_after <= now() LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("lead_flows", "DELETE FROM guide_lead_flows WHERE flow_id IN (SELECT flow_id FROM "
     "guide_lead_flows WHERE purge_after <= now() LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("external_operations", "DELETE FROM guide_external_operations WHERE operation_id IN "
     "(SELECT operation_id FROM guide_external_operations WHERE purge_after <= now() AND "
     "outcome <> 'in_progress' LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("messages", "DELETE FROM guide_messages WHERE message_id IN (SELECT message_id FROM "
     "guide_messages WHERE purge_after <= now() LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("context_versions", "DELETE FROM guide_session_context_versions WHERE (session_id, "
     "context_version) IN (SELECT session_id, context_version FROM "
     "guide_session_context_versions WHERE purge_after <= now() LIMIT :n FOR UPDATE SKIP "
     "LOCKED)"),
    ("event_contexts", "DELETE FROM guide_public_event_contexts WHERE event_context_id IN "
     "(SELECT event_context_id FROM guide_public_event_contexts WHERE purge_after <= now() "
     "LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("consents", "DELETE FROM guide_consents WHERE consent_id IN (SELECT consent_id FROM "
     "guide_consents WHERE purge_after <= now() LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("feedback", "DELETE FROM guide_feedback WHERE feedback_id IN (SELECT feedback_id FROM "
     "guide_feedback WHERE purge_after <= now() LIMIT :n FOR UPDATE SKIP LOCKED)"),
    # Deleting a session cascades to whatever session-local rows remain, which is how the
    # accelerated purge after "End conversation" (session purge_after = ended + 24h) works.
    ("sessions", "DELETE FROM guide_sessions WHERE session_id IN (SELECT session_id FROM "
     "guide_sessions WHERE purge_after <= now() LIMIT :n FOR UPDATE SKIP LOCKED)"),
    ("audit", "DELETE FROM guide_audit_events WHERE event_id IN (SELECT event_id FROM "
     "guide_audit_events WHERE purge_after <= now() LIMIT :n FOR UPDATE SKIP LOCKED)"),
]

_last_run = 0.0
_lock = threading.Lock()


def purge_once(batch: int = BATCH) -> dict[str, int]:
    counts: dict[str, int] = {}
    db = new_maintenance_session()
    try:
        with metrics.timer("guide_purge_duration_seconds"):
            for name, sql in _STEPS:
                total = 0
                while True:
                    n = db.execute(text(sql), {"n": batch}).rowcount or 0
                    db.commit()
                    total += n
                    if n < batch:
                        break
                if total:
                    counts[name] = total
                    metrics.incr("guide_purge_rows_total", total, table=name)
        if counts:
            audit(db, None, "retention_purge", **counts)
            db.commit()
    except Exception:
        metrics.incr("guide_purge_runs_total", outcome="failed")
        db.rollback()
        raise
    finally:
        db.close()
    metrics.incr("guide_purge_runs_total", outcome="ok")
    return counts


def run_if_due() -> None:
    global _last_run
    now = time.monotonic()
    with _lock:
        if now - _last_run < PURGE_INTERVAL:
            return
        _last_run = now
    try:
        counts = purge_once()
        if counts:
            log.info("guide retention purge: %s", counts)
    except Exception:
        log.exception("guide retention purge failed")  # alert source; never breaks a request


if __name__ == "__main__":
    print(purge_once())
