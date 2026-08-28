"""One-off schema fix: remove events.auto_start_recording.

Confirmed dead during the live-streaming verification pass: no code path ever read this
column to actually start a recording (recording start is always a deliberate host action —
see services/broadcast.py's ACTIONS/_recording_start), and no frontend form ever exposed a
toggle for it. It WAS silently repurposed to drive an unrelated setting (auto_upload,
whether a captured file auto-uploads once recording stops) — that conflation was fixed in an
earlier pass; this finishes the cleanup by removing the misleading column entirely rather
than leaving inert dead configuration in the schema.

Same additive/scripted-migration pattern as this project's other migrate_*.py files (no
Alembic in use) — DROP COLUMN is not reversible via IF NOT EXISTS the way ADD COLUMN is, so
this one is a deliberate one-time cleanup, run once, not idempotent-by-design like the
others. Confirmed via grep before writing this that no other table/view depends on the
column, and the ORM model/pydantic schemas were removed in the same change so no code
references it after this runs.
"""

from sqlalchemy import text

from app.db import engine


def drop_column():
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE events DROP COLUMN IF EXISTS auto_start_recording;"))
        conn.commit()

    print("events.auto_start_recording dropped (or already absent)")


if __name__ == "__main__":
    drop_column()
