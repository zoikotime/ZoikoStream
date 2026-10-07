"""Drop the stale `events` / `event_assignments` tables (destructive, one-off maintenance).

No credentials live in this file. It used to hardcode a real database password and
connection string; that value is public in git history and must be rotated (or the old
database retired) regardless of this change.

The target comes ONLY from the DROP_TABLES_DATABASE_URL environment variable, deliberately
not from DATABASE_URL / .env, so whatever the app happens to point at is never dropped by
accident. The sanitized target is shown and the run needs an explicit confirmation flag.

    DROP_TABLES_DATABASE_URL=<url> python drop_tables.py --yes-drop-events-tables
"""
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

CONFIRM_FLAG = "--yes-drop-events-tables"


def main() -> int:
    url = os.environ.get("DROP_TABLES_DATABASE_URL", "").strip()
    if not url:
        print("Set DROP_TABLES_DATABASE_URL to the database to modify. Nothing was done.")
        return 2
    target = make_url(url)
    print(f"Target: host={target.host} port={target.port} database={target.database}")
    if CONFIRM_FLAG not in sys.argv[1:]:
        print(f"This DROPS the events and event_assignments tables. Re-run with {CONFIRM_FLAG} to proceed.")
        return 2

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            print("Dropping stale tables...")
            conn.execute(text("DROP TABLE IF EXISTS event_assignments CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS events CASCADE;"))
            conn.commit()
            print("Tables dropped successfully!")
    except Exception as exc:  # noqa: BLE001 - report the failure class, never connection details
        print(f"An error occurred: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
