"""One-off schema fix: enforce at most one OPEN BroadcastSession per event.

Same additive, idempotent-via-IF-NOT-EXISTS pattern as update_stream_table.py (this project
has no Alembic — schema evolves through small scripts like this one, run once against
DATABASE_URL). A Postgres partial unique index, not a full one: BroadcastSession rows persist
after a broadcast ends (ended_at is set, never deleted), so a plain UNIQUE(event_id) would
reject every second-ever broadcast of the same event. Restricting it to `WHERE ended_at IS
NULL` only ever forbids a SECOND *open* session for the same event at once — exactly the race
the audit found: two near-simultaneous Go Live clicks could each see "no open session" and
both INSERT one. services/broadcast.py::_golive now catches the resulting IntegrityError and
retries against the winner's row instead of surfacing it to the loser's request.

Reversible: `DROP INDEX IF EXISTS uq_broadcast_sessions_open;` undoes this with no data loss.
"""

from sqlalchemy import text

from app.db import engine


def add_unique_index():
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_broadcast_sessions_open
                ON broadcast_sessions (event_id)
                WHERE ended_at IS NULL;
                """
            )
        )
        conn.commit()

    print("uq_broadcast_sessions_open created (or already existed)")


if __name__ == "__main__":
    add_unique_index()
