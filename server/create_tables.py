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
    # Command Center: test orgs are excluded from readiness, badges and attention counts
    # (that's what the console's "Include test mode" toggle switches back on).
    "ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE",
]

# Slug lookups are indexed; NULLs are allowed (many unset orgs), uniqueness is enforced in crud.
_ORG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_organizations_slug ON organizations (slug)",
]

# Soft-delete marker on the pre-existing users table (Phase 3). The `invitations` table is
# a brand-new model, so create_all() below builds it — only existing tables need ALTERs.
_USER_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    # Operating team an actor belongs to — shown against privileged activity in the console.
    "ADD COLUMN IF NOT EXISTS department VARCHAR(80)",
    # Commercial RBAC (ZST-LE-COM-001 Section 25) — scopes a super_admin down to one staff
    # sub-role for commercial actions; NULL keeps every existing account's unrestricted
    # behavior unchanged. See models/user.py STAFF_COMMERCIAL_ROLES, security.commercial_can.
    "ADD COLUMN IF NOT EXISTS staff_commercial_role VARCHAR(20)",
]

# Blast-radius class for an event. Drives which readiness gates are mandatory and which
# events surface on the Command Center's high-impact list.
#
# The billing_* / risk_tier / service_profile_id / commercial_account_id columns are the
# commercial layer (ZST-LE-COM-001, models/commercial.py). Every existing event defaults to
# billing_classification='internal' so nothing already in the database silently becomes
# billable the moment this migration runs (doc S1).
_EVENT_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS impact VARCHAR(20) NOT NULL DEFAULT 'standard'",
    "ADD COLUMN IF NOT EXISTS billing_classification VARCHAR(20) NOT NULL DEFAULT 'internal'",
    "ADD COLUMN IF NOT EXISTS billing_source VARCHAR(30) NOT NULL DEFAULT 'direct_zoikostream'",
    "ADD COLUMN IF NOT EXISTS risk_tier VARCHAR(4) NOT NULL DEFAULT 'r0'",
    "ADD COLUMN IF NOT EXISTS service_profile_id UUID REFERENCES service_profiles(id)",
    "ADD COLUMN IF NOT EXISTS commercial_account_id UUID REFERENCES commercial_accounts(id)",
]

# Commercial recording fields (doc Section 14/J — R2/R3 independent dual recording).
_LIVE_RECORDING_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS role VARCHAR(16)",
    "ADD COLUMN IF NOT EXISTS validation_status VARCHAR(16)",
    "ADD COLUMN IF NOT EXISTS retention_policy_version VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS retention_expires_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN NOT NULL DEFAULT FALSE",
]

# Columns whose models gained fields after the table already existed. Without these the
# SELECT that lists them fails outright ("column does not exist") — /admin/feature-flags
# and /admin/support-tickets were both returning 500s because of this drift.
_FEATURE_FLAG_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()",
    "ADD COLUMN IF NOT EXISTS updated_by VARCHAR(255)",
]
_SUPPORT_TICKET_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ",
]

# NULL = self-serve registration; set = host-initiated invite (routers/events.py
# invite_viewers), which also doubles as the access grant into a PRIVATE event.
# claim_token_hash/claimed_at: one-device claim on a private event's personal invite link —
# see models/event.py EventRegistration docstring.
_EVENT_REGISTRATION_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS invited_by UUID REFERENCES users(id)",
    "ADD COLUMN IF NOT EXISTS claim_token_hash VARCHAR(64)",
    "ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ",
]

# Schema drift: the live table carries org_id/user_id/status/bookmarked/watch_seconds/
# join_count NOT NULL — columns from a richer registration/RSVP design that never made it
# into models/event.py's EventRegistration (create_all() only adds tables, so a reverted
# PR's migration outlived the code that used it). The current model never populates them,
# so every insert — self-serve registration AND host invites — hit a NotNullViolation.
# Relaxing the constraint is correct here: these columns aren't part of the current design,
# not values that were merely missing a default.
_EVENT_REGISTRATION_RELAX_NOT_NULL = ["org_id", "user_id", "status", "bookmarked", "watch_seconds", "join_count"]


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
        for clause in _EVENT_COLUMNS:
            conn.execute(text(f"ALTER TABLE events {clause}"))
        for clause in _LIVE_RECORDING_COLUMNS:
            conn.execute(text(f"ALTER TABLE live_recordings {clause}"))
        for clause in _FEATURE_FLAG_COLUMNS:
            conn.execute(text(f"ALTER TABLE feature_flags {clause}"))
        for clause in _SUPPORT_TICKET_COLUMNS:
            conn.execute(text(f"ALTER TABLE support_tickets {clause}"))
        for clause in _EVENT_REGISTRATION_COLUMNS:
            conn.execute(text(f"ALTER TABLE event_registrations {clause}"))
        for col in _EVENT_REGISTRATION_RELAX_NOT_NULL:
            conn.execute(text(f"ALTER TABLE event_registrations ALTER COLUMN {col} DROP NOT NULL"))
    print("Schema ready!")


if __name__ == "__main__":
    ensure_schema()
