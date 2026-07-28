from sqlalchemy import text

from app.db import engine, Base

# Import the models package so every model is registered on Base.metadata before create_all.
import app.models  # noqa: F401

# create_all only CREATES missing tables — it never alters existing ones. The admin module
# added columns to the pre-existing `organizations` table, so add them idempotently here.
# ADD COLUMN IF NOT EXISTS is Postgres-native and safe to re-run.
_ORG_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS domain VARCHAR(255)",
    "ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'",
    "ADD COLUMN IF NOT EXISTS region VARCHAR(40) DEFAULT 'US East'",
    "ADD COLUMN IF NOT EXISTS storage_used_gb DOUBLE PRECISION NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS bandwidth_gb DOUBLE PRECISION NOT NULL DEFAULT 0",
    # Org self-service settings (Phase 2 — /organization/*).
    "ADD COLUMN IF NOT EXISTS slug VARCHAR(140)",
    "ADD COLUMN IF NOT EXISTS website VARCHAR(255)",
    "ADD COLUMN IF NOT EXISTS description TEXT",
    "ADD COLUMN IF NOT EXISTS industry VARCHAR(80)",
    "ADD COLUMN IF NOT EXISTS company_size VARCHAR(40)",
    "ADD COLUMN IF NOT EXISTS support_email VARCHAR(255)",
    "ADD COLUMN IF NOT EXISTS timezone VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS country VARCHAR(80)",
    "ADD COLUMN IF NOT EXISTS logo_url VARCHAR(500)",
    "ADD COLUMN IF NOT EXISTS primary_color VARCHAR(20)",
    "ADD COLUMN IF NOT EXISTS secondary_color VARCHAR(20)",
    "ADD COLUMN IF NOT EXISTS theme VARCHAR(20)",
    "ADD COLUMN IF NOT EXISTS domain_verified BOOLEAN NOT NULL DEFAULT FALSE",
    "ADD COLUMN IF NOT EXISTS notifications JSONB",
    "ADD COLUMN IF NOT EXISTS security JSONB",
    "ADD COLUMN IF NOT EXISTS api_keys JSONB",
    "ADD COLUMN IF NOT EXISTS webhook_urls JSONB",
]

# Slug lookups are indexed; NULLs are allowed (many unset orgs), uniqueness is enforced in crud.
_ORG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_organizations_slug ON organizations (slug)",
]

# Soft-delete marker on the pre-existing users table (Phase 3). The `invitations` table is
# a brand-new model, so create_all() below builds it — only existing tables need ALTERs.
_USER_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
]


def ensure_schema():
    """Create any missing tables and add any missing columns. Idempotent — safe to re-run."""
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("Ensuring organization columns...")
    with engine.begin() as conn:
        for clause in _ORG_COLUMNS:
            conn.execute(text(f"ALTER TABLE organizations {clause}"))
        for stmt in _ORG_INDEXES:
            conn.execute(text(stmt))
        for clause in _USER_COLUMNS:
            conn.execute(text(f"ALTER TABLE users {clause}"))
    print("Schema ready!")


if __name__ == "__main__":
    ensure_schema()
