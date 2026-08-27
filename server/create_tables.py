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
    # Email verification (ZST-EC-001 IDN-001).
    #
    # DEFAULT TRUE here is deliberate and is the whole point of the two-step below: Postgres
    # backfills every EXISTING row with the column default, and those accounts were created
    # under the old flow where registration implied activation. Defaulting them to FALSE
    # would lock every current user out of login the moment this runs.
    #
    # The default is then flipped to FALSE so INSERTs made outside the ORM are unverified by
    # default too. New accounts get FALSE from models/user.py and routers/auth.py.
    "ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT TRUE",
    "ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ",
    "ALTER COLUMN email_verified SET DEFAULT FALSE",
    # IDN-002 duplicate-prevention marker. Left NULL on every existing row and deliberately
    # NOT backfilled: a timestamp here would assert a message was sent that never was.
    # Adding a NULL column cannot itself trigger mail — IDN-002 fires only from a consumed
    # verification challenge, and pre-existing accounts have none.
    "ADD COLUMN IF NOT EXISTS account_ready_sent_at TIMESTAMPTZ",
    # Recovery contact (IDN-006/IDN-007). All NULL on existing rows: nobody has
    # nominated one, and inventing a recovery destination would be a security defect.
    "ADD COLUMN IF NOT EXISTS recovery_email VARCHAR(255)",
    "ADD COLUMN IF NOT EXISTS recovery_email_verified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS recovery_email_pending VARCHAR(255)",
]

# Backfill the timestamp for the grandfathered rows above so `email_verified` is never
# TRUE with an unexplained NULL date. created_at is the honest approximation: it is when
# the account was activated under the previous flow.
_USER_BACKFILL = [
    "UPDATE users SET email_verified_at = created_at "
    "WHERE email_verified IS TRUE AND email_verified_at IS NULL",
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
    "ADD COLUMN IF NOT EXISTS expected_audience INTEGER",
]

# Commercial recording fields (doc Section 14/J — R2/R3 independent dual recording).
_LIVE_RECORDING_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS role VARCHAR(16)",
    "ADD COLUMN IF NOT EXISTS validation_status VARCHAR(16)",
    "ADD COLUMN IF NOT EXISTS validation_evidence JSONB",
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

# Watermark burn-in state (BRD "policy watermark", LE-AC-12) — added after
# customer_deliveries already existed, so create_all() alone won't add these.
_CUSTOMER_DELIVERY_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS watermark_status VARCHAR(16) NOT NULL DEFAULT 'pending'",
    "ADD COLUMN IF NOT EXISTS watermarked_file_key VARCHAR(500)",
    "ADD COLUMN IF NOT EXISTS watermark_error TEXT",
]

# Same watermark burn-in state, now also for the published (audience) replay itself — added
# after replay_entitlements already existed.
_REPLAY_ENTITLEMENT_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS watermark_status VARCHAR(20) NOT NULL DEFAULT 'not_applicable'",
    "ADD COLUMN IF NOT EXISTS watermarked_file_key VARCHAR(500)",
    "ADD COLUMN IF NOT EXISTS watermark_error TEXT",
    "ADD COLUMN IF NOT EXISTS source_recording_id UUID",
]

# Tax determination (ZST-LE-COM-001 L4/L6). event_orders.tax_amount was `NOT NULL DEFAULT 0`,
# which made "not determined yet" indistinguishable from "no tax due" — and since nothing
# ever computed it, every invoice carried zero tax structurally. Dropping NOT NULL makes NULL
# mean UNDETERMINED, which crud.issue_invoice now refuses to invoice against.
_EVENT_ORDER_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS tax_treatment VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS tax_jurisdiction VARCHAR(80)",
    "ADD COLUMN IF NOT EXISTS tax_source VARCHAR(80)",
    "ADD COLUMN IF NOT EXISTS tax_rule_version VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS tax_effective_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS tax_determined_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS tax_determined_by UUID REFERENCES users(id)",
    "ALTER COLUMN tax_amount DROP NOT NULL",
    "ALTER COLUMN tax_amount DROP DEFAULT",
]

# The tax facts an issued invoice snapshots off the order (immutable — see models/commercial.py
# Invoice). invoices.tax_amount stays NOT NULL: issuance is blocked without a determination.
_INVOICE_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS tax_treatment VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS tax_jurisdiction VARCHAR(80)",
    "ADD COLUMN IF NOT EXISTS tax_source VARCHAR(80)",
    "ADD COLUMN IF NOT EXISTS tax_rule_version VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS tax_effective_at TIMESTAMPTZ",
]

# Ledger-1 plan pricing. price_monthly was `NOT NULL DEFAULT 0`, so an unpriced plan looked
# free. NULL now means "no approved price published" (doc Section 26: no hard-coded fallback
# price exists). Existing rows keep whatever value they already hold — clearing previously
# seeded prices is a Finance decision, not a migration's, so this only relaxes the constraint.
# custom_pricing distinguishes "quote required / contact sales" from "not published yet".
_PLAN_COLUMNS = [
    "ALTER COLUMN price_monthly DROP NOT NULL",
    "ALTER COLUMN price_monthly DROP DEFAULT",
    "ADD COLUMN IF NOT EXISTS custom_pricing BOOLEAN NOT NULL DEFAULT FALSE",
]

# Phase 2 commercial foundation (ZST-LE-COM-001 C4, L1, L2, Section 27).
#
# event_order_lines.unit_basis   — freeze the price's unit basis onto the line so a line is
#                                  self-describing (per_event vs per_hour) even if the source
#                                  CatalogLine is later edited.
# commercial_accounts.seller_*   — drop the "zoiko_tech_inc" default; a seller entity must now
#                                  be an explicit, REGISTERED, ACTIVE SellerLegalEntity.
# commercial_quotes.tax_amount   — NULL = tax not determined (was NOT NULL DEFAULT 0, i.e. a
#                                  silent zero-tax quote).
# capacity_reservations.*        — bind a reservation to the pool and order version it drew
#                                  from, and record what was requested vs granted.
# invoices — the old GLOBAL unique on `number` is replaced by unique per (seller entity,
#            number), so each legal entity keeps its own series (doc Section 3).
_EVENT_ORDER_LINE_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS unit_basis VARCHAR(30)",
]
_COMMERCIAL_ACCOUNT_COLUMNS = [
    "ALTER COLUMN seller_legal_entity_id DROP NOT NULL",
    "ALTER COLUMN seller_legal_entity_id DROP DEFAULT",
]
_QUOTE_COLUMNS = [
    "ALTER COLUMN tax_amount DROP NOT NULL",
    "ALTER COLUMN tax_amount DROP DEFAULT",
]
_CAPACITY_RESERVATION_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS capacity_pool_id UUID REFERENCES capacity_pools(id)",
    "ADD COLUMN IF NOT EXISTS event_order_version_id UUID REFERENCES event_order_versions(id)",
    "ADD COLUMN IF NOT EXISTS requested_quantity INTEGER",
    "ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id)",
]
_INVOICE_TAX_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS tax_exemption_reason VARCHAR(200)",
]
_EVENT_ORDER_TAX_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS tax_exemption_reason VARCHAR(200)",
]
# Phase 3 payment-path correctness (ZST-LE-COM-001 P1/P4/P5, Section 25/28/30).
#
# payment_schedules.allocated_amount — how much captured money is attributed to a milestone,
#   so `satisfied` requires full coverage instead of any capture at all (CF-5).
# commercial_exceptions.*            — scope/decision/correlation fields the governed
#   override workflow needs to answer who/what/why/when/until-when (CF-4).
# audit_logs.correlation_id          — one id threading provider event -> payment -> invoice
#   -> order -> event -> audit (doc Section 30).
# provider_events / unmatched_settlements are new tables, so create_all() builds them.
_PAYMENT_SCHEDULE_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS allocated_amount NUMERIC(12,2) NOT NULL DEFAULT 0",
]
_COMMERCIAL_EXCEPTION_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS overridden_gate VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS previous_state VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS decision_notes TEXT",
    "ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(64)",
]
_AUDIT_LOG_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(64)",
]
_PHASE3_STATEMENTS = [
    # Provider event identity is enforced by the DATABASE, not an application check — two
    # concurrent deliveries of the same event race, and only a constraint can arbitrate (CF-1).
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_event_identity "
    "ON provider_events (provider, provider_event_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_correlation_id ON audit_logs (correlation_id)",
    "CREATE INDEX IF NOT EXISTS ix_unmatched_settlements_status "
    "ON unmatched_settlements (status, received_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_commercial_exceptions_lookup "
    "ON commercial_exceptions (event_order_id, exception_type, status)",
]

# Phase 4F — hosted checkout reconciliation.
#
# A provider-hosted checkout session has no payment reference until the payer submits, so the
# payment reference must be nullable and the SESSION becomes the correlation key until the real
# reference is adopted from a verified provider event.
_PHASE4F_STATEMENTS = [
    "ALTER TABLE payments ADD COLUMN IF NOT EXISTS checkout_session_ref VARCHAR(120)",
    "ALTER TABLE payments ALTER COLUMN provider_payment_ref DROP NOT NULL",
    # One session -> at most one payment, enforced by the database. NULLs are distinct in
    # Postgres, so payments that never came from a hosted checkout are unaffected.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_provider_checkout_session "
    "ON payments (provider, checkout_session_ref)",
]

# Phase 5 — commercial lifecycle completion (ZST-LE-COM-001 Sections 9/10/28).
#
# create_all() below builds the brand-new tables (commercial_state_transitions,
# event_reschedules); only the pre-existing tables need ALTERs. Every column is nullable or
# carries a DEFAULT, so existing rows keep working unchanged:
#   * assured_event defaults FALSE      — no existing order is retroactively an Assured Event.
#   * managed_only defaults FALSE       — no existing service profile becomes managed-only.
#   * lifecycle_state is NULL           — a NULL means "never yet computed", which
#                                         crud.sync_lifecycle treats as the initial observation
#                                         rather than as a transition from anywhere.
_PHASE5_STATEMENTS = [
    "ALTER TABLE event_orders ADD COLUMN IF NOT EXISTS assured_event BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE event_orders ADD COLUMN IF NOT EXISTS lifecycle_state VARCHAR(20)",
    "ALTER TABLE service_profiles ADD COLUMN IF NOT EXISTS managed_only BOOLEAN NOT NULL DEFAULT FALSE",
    # Lifecycle history is queried per order (the order timeline) and per event (the delivery
    # timeline), so both directions are indexed.
    "CREATE INDEX IF NOT EXISTS ix_commercial_state_transitions_order "
    "ON commercial_state_transitions (event_order_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_commercial_state_transitions_event "
    "ON commercial_state_transitions (event_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_event_reschedules_event "
    "ON event_reschedules (event_id, created_at)",
]

# Phase 5b — production hardening (ZST-LE-COM-001 Sections 11/25/28/30).
#
# Change-order provenance and lifecycle rationale. All nullable: existing change orders keep
# working with no requester/reason recorded (they predate the requirement), and new ones are
# refused without a reason at the CRUD layer rather than by a NOT NULL that would break the
# backfill.
_PHASE5B_STATEMENTS = [
    "ALTER TABLE change_orders ADD COLUMN IF NOT EXISTS requested_by UUID REFERENCES users(id)",
    "ALTER TABLE change_orders ADD COLUMN IF NOT EXISTS reason TEXT",
    "ALTER TABLE change_orders ADD COLUMN IF NOT EXISTS applied_lines JSON",
    "ALTER TABLE change_orders ADD COLUMN IF NOT EXISTS approval_exception_id UUID "
    "REFERENCES commercial_exceptions(id)",
    "ALTER TABLE commercial_state_transitions ADD COLUMN IF NOT EXISTS reason TEXT",
]

# Raw statements (not single-table ALTER fragments) — constraint swaps and index creation.
_PHASE2_STATEMENTS = [
    "ALTER TABLE invoices DROP CONSTRAINT IF EXISTS uq_invoice_number",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_seller_number "
    "ON invoices (seller_legal_entity_id, number)",
    # Capacity is claimed under a row lock on its pool; this index keeps the utilisation
    # sum that runs inside that lock cheap.
    "CREATE INDEX IF NOT EXISTS ix_capacity_reservations_pool_state "
    "ON capacity_reservations (capacity_pool_id, state)",
]


_IDENTITY_CHALLENGE_COLUMNS = [
    # IDN-007 lockout. The table predates it, so create_all() alone will not add it.
    "ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ",
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
        for clause in _IDENTITY_CHALLENGE_COLUMNS:
            conn.execute(text(f"ALTER TABLE identity_challenges {clause}"))
        for stmt in _USER_BACKFILL:
            conn.execute(text(stmt))
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
        for clause in _CUSTOMER_DELIVERY_COLUMNS:
            conn.execute(text(f"ALTER TABLE customer_deliveries {clause}"))
        for clause in _REPLAY_ENTITLEMENT_COLUMNS:
            conn.execute(text(f"ALTER TABLE replay_entitlements {clause}"))
        for clause in _EVENT_ORDER_COLUMNS:
            conn.execute(text(f"ALTER TABLE event_orders {clause}"))
        for clause in _INVOICE_COLUMNS:
            conn.execute(text(f"ALTER TABLE invoices {clause}"))
        for clause in _PLAN_COLUMNS:
            conn.execute(text(f"ALTER TABLE plans {clause}"))
        for clause in _EVENT_ORDER_TAX_COLUMNS:
            conn.execute(text(f"ALTER TABLE event_orders {clause}"))
        for clause in _INVOICE_TAX_COLUMNS:
            conn.execute(text(f"ALTER TABLE invoices {clause}"))
        for clause in _EVENT_ORDER_LINE_COLUMNS:
            conn.execute(text(f"ALTER TABLE event_order_lines {clause}"))
        for clause in _COMMERCIAL_ACCOUNT_COLUMNS:
            conn.execute(text(f"ALTER TABLE commercial_accounts {clause}"))
        for clause in _QUOTE_COLUMNS:
            conn.execute(text(f"ALTER TABLE commercial_quotes {clause}"))
        for clause in _CAPACITY_RESERVATION_COLUMNS:
            conn.execute(text(f"ALTER TABLE capacity_reservations {clause}"))
        for stmt in _PHASE2_STATEMENTS:
            conn.execute(text(stmt))
        for clause in _PAYMENT_SCHEDULE_COLUMNS:
            conn.execute(text(f"ALTER TABLE payment_schedules {clause}"))
        for clause in _COMMERCIAL_EXCEPTION_COLUMNS:
            conn.execute(text(f"ALTER TABLE commercial_exceptions {clause}"))
        for clause in _AUDIT_LOG_COLUMNS:
            conn.execute(text(f"ALTER TABLE audit_logs {clause}"))
        for stmt in _PHASE3_STATEMENTS:
            conn.execute(text(stmt))
        for stmt in _PHASE4F_STATEMENTS:
            conn.execute(text(stmt))
        for stmt in _PHASE5_STATEMENTS:
            conn.execute(text(stmt))
        for stmt in _PHASE5B_STATEMENTS:
            conn.execute(text(stmt))
    print("Schema ready!")


if __name__ == "__main__":
    ensure_schema()
