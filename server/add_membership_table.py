from sqlalchemy import text
from app.db import engine


def add_membership_table():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS memberships (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id),
                org_id UUID NOT NULL REFERENCES organizations(id),
                role VARCHAR(20) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_membership_user_org UNIQUE (user_id, org_id)
            );
        """))
        # Backfill one membership per existing user from their current org_id/role,
        # so accounts created before this table existed aren't left without one.
        conn.execute(text("""
            INSERT INTO memberships (user_id, org_id, role, is_active)
            SELECT id, org_id, role, is_active FROM users
            ON CONFLICT (user_id, org_id) DO NOTHING;
        """))
        conn.commit()

    print("memberships table created and backfilled successfully")


if __name__ == "__main__":
    add_membership_table()
