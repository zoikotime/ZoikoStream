from sqlalchemy import text
from app.db import engine


def add_columns():

    with engine.connect() as conn:

        # org_id backfills from channels.owner_id -> users.org_id for any streams that
        # already exist, so the NOT NULL constraint can be added safely afterward.
        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);"))
        conn.execute(
            text(
                """
                UPDATE streams
                SET org_id = users.org_id
                FROM channels
                JOIN users ON users.id = channels.owner_id
                WHERE streams.channel_id = channels.id
                  AND streams.org_id IS NULL;
                """
            )
        )
        orphans = conn.execute(text("SELECT count(*) FROM streams WHERE org_id IS NULL;")).scalar()
        if orphans:
            print(f"WARNING: {orphans} stream(s) have no resolvable org_id (orphaned channel/owner) "
                  "— leaving org_id nullable. Fix those rows, then rerun this script to add the NOT NULL constraint.")
        else:
            conn.execute(text("ALTER TABLE streams ALTER COLUMN org_id SET NOT NULL;"))

        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS host_id UUID REFERENCES users(id);"))
        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS moderator_id UUID REFERENCES users(id);"))

        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'draft';"))
        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'public';"))
        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS registration_required BOOLEAN NOT NULL DEFAULT false;"))

        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS scheduled_date DATE;"))
        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS start_time VARCHAR(5);"))
        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS end_time VARCHAR(5);"))
        conn.execute(text("ALTER TABLE streams ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) NOT NULL DEFAULT 'UTC';"))

        # Backfill status for any pre-existing rows so already-live/finished streams
        # don't show up as "draft" in the Events list.
        conn.execute(text("UPDATE streams SET status = 'live' WHERE is_live = true AND status = 'draft';"))
        conn.execute(text("UPDATE streams SET status = 'completed' WHERE ended_at IS NOT NULL AND status = 'draft';"))

        conn.commit()

    print("Columns added successfully")


if __name__ == "__main__":
    add_columns()
