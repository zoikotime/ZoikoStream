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
    # Present on the model (models/event.py) but never in this list, so an events table that
    # predates the field could not self-heal — create_all() only creates missing TABLES.
    "ADD COLUMN IF NOT EXISTS auto_start_recording BOOLEAN NOT NULL DEFAULT FALSE",
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

# Phase 6 — maker-checker for period close and reconciliation-exception resolution (audit
# 2026-08-27, ZST-LE-COM-001 doc Section 25 principle extended to two flows that previously let
# one actor both prepare and finalize).
#
# financial_periods.status was VARCHAR(10); "pending_close" is 13 characters, so it must widen
# before any row can take that value — existing "open"/"closed" rows are unaffected by a wider
# column. All new columns are nullable: every pre-existing period/exception simply has no
# preparer on file, which is correct (there was no two-step process when they were written).
_PHASE6_STATEMENTS = [
    "ALTER TABLE financial_periods ALTER COLUMN status TYPE VARCHAR(20)",
    "ALTER TABLE financial_periods ADD COLUMN IF NOT EXISTS prepared_by UUID REFERENCES users(id)",
    "ALTER TABLE financial_periods ADD COLUMN IF NOT EXISTS prepared_at TIMESTAMPTZ",
    "ALTER TABLE reconciliation_exceptions ADD COLUMN IF NOT EXISTS prepared_by UUID REFERENCES users(id)",
    "ALTER TABLE reconciliation_exceptions ADD COLUMN IF NOT EXISTS prepared_at TIMESTAMPTZ",
    "ALTER TABLE reconciliation_exceptions ADD COLUMN IF NOT EXISTS proposed_status VARCHAR(20)",
]

# Phase 7 — invoice duplication guard (audit 2026-08-28).
#
# `issue_invoice` had no duplicate check, and UNIQUE(seller_legal_entity_id, number) does not
# imply one: it makes NUMBERS unique per seller, not DOCUMENTS per order. Two concurrent POSTs
# to /orders/{id}/invoices each allocated their own number and both succeeded, leaving one
# order with two valid invoices. Enforced in the database because only the database can
# arbitrate a race — same reasoning as the provider-event identity index above.
#
# PARTIAL (state <> 'void') so the model's declared draft|issued|paid|void vocabulary keeps its
# void-then-reissue path; a blanket unique would silently remove it.
#
# NOT idempotent in the ADD COLUMN sense: this CREATE fails if the target database already
# holds two non-void invoices for one order. That is deliberate — duplicate financial documents
# must be reconciled by Finance, never auto-deleted by a migration. Check before deploying:
#   SELECT event_order_id, count(*) FROM invoices WHERE state <> 'void'
#   GROUP BY event_order_id HAVING count(*) > 1;
_PHASE7_STATEMENTS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_active_per_order "
    "ON invoices (event_order_id) WHERE state <> 'void'",
]

# Phase 8 — subscription lifecycle vocabulary (ZST-COM-PLAN-001 Section 12).
#
# `subscriptions.status` was VARCHAR(20); the Section 12 state `plan_change_scheduled` is 21
# characters, so the column must widen before any row can take that value. Widening is
# non-destructive; existing rows are untouched.
#
# Deliberately NO data rewrite: rows written before Section 12 keep their `trial`/`cancelled`
# spellings and are translated on read/write by models.subscription (LEGACY_SUBSCRIPTION_STATES).
# Mass-updating historical commercial rows is the silent mutation Section 18 prohibits, and it
# is not needed for correctness.
_PHASE8_STATEMENTS = [
    "ALTER TABLE subscriptions ALTER COLUMN status TYPE VARCHAR(30)",
]

# Phase 9 — commercial overrides (ZST-COM-PLAN-001 Section 14 / Section 19).
#
# `commercial_overrides` is a brand-new table, so create_all() builds it and no ALTER is
# needed. The index is declared on the model; this statement exists so a database created
# before the model gained it still receives it, matching how every other index in this file
# is applied. Both are IF NOT EXISTS, so re-running is a no-op.
# Phase 10 — Ledger 1 Stripe references (ZST-COM-PLAN-001 Section 13 "Commerce Adapter").
#
# Correlation keys only: they let an inbound Stripe event find the right subscription. They are
# NOT the subscription's state — `status` (the Section 12 machine) remains the only authority.
# All nullable: every existing subscription predates Stripe and has none.
#
# The two unique indexes are what make webhook matching exact rather than fuzzy — one Stripe
# subscription and one checkout session can each belong to at most one tenant. NULLs are
# distinct in Postgres, so the many rows with no Stripe reference are unaffected.
_PHASE10_STATEMENTS = [
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(120)",
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(120)",
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS checkout_session_ref VARCHAR(120)",
    "CREATE INDEX IF NOT EXISTS ix_subscriptions_stripe_customer "
    "ON subscriptions (stripe_customer_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_stripe_subscription "
    "ON subscriptions (stripe_subscription_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_checkout_session "
    "ON subscriptions (checkout_session_ref)",
    # The cadence a subscription is billed on. Deliberately NULLABLE with no default: rows
    # written before this column existed genuinely have no recorded cadence, and defaulting
    # them to 'monthly' would assert something about live tenants that nobody verified.
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS billing_interval VARCHAR(16)",
    # Scheduled plan change (Section 12 PLAN_CHANGE_SCHEDULED). All three nullable and additive:
    # an existing subscription simply has no pending change, which is the correct reading of
    # NULL here. No backfill, no default, no data touched.
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_plan_id UUID "
    "REFERENCES plans(id)",
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_billing_interval VARCHAR(16)",
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS plan_change_effective_at TIMESTAMPTZ",
    # Lets the effective-date job find due changes without scanning every subscription. Partial:
    # only rows that actually have a pending change are of interest.
    "CREATE INDEX IF NOT EXISTS ix_subscriptions_plan_change_due "
    "ON subscriptions (plan_change_effective_at) WHERE pending_plan_id IS NOT NULL",
]

_PHASE9_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS ix_commercial_overrides_org_expiry "
    "ON commercial_overrides (org_id, expires_at)",
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


# ZST-EC-001 ORG-001/ORG-002 notification markers. One column per lifecycle transition,
# claimed by conditional UPDATE so a retry cannot duplicate the notice. All nullable with no
# default: an existing invitation has genuinely never been announced under the new scheme,
# and NULL is the honest representation of that.
# ZST-EC-001 ORG-007. Records HOW a reviewer was designated, so a review can be audited
# for who was asked and on what basis. Nullable: rows written before the designation rule
# existed genuinely have no recorded basis, and inventing one would be worse than NULL.
# ZST-EC-001 DEV-006/DEV-007. Verification + health state on an existing table, so
# create_all() cannot add them.
_WEBHOOK_ENDPOINT_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS status VARCHAR(24) NOT NULL DEFAULT 'pending_verification'",
    "ADD COLUMN IF NOT EXISTS verification_token_hash VARCHAR(64)",
    "ADD COLUMN IF NOT EXISTS verification_expires_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS verification_attempts INTEGER NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS verification_attempted_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS verification_reset_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS verification_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS reset_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS health VARCHAR(16) NOT NULL DEFAULT 'healthy'",
    "ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS last_delivery_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS degraded_since TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS disabled_reason VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS degraded_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS recovered_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS disabled_notified_at TIMESTAMPTZ",
]

# ZST-EC-001 DEV-008 signing-secret rotation.
_WEBHOOK_ROTATION_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS previous_secret VARCHAR(64)",
    "ADD COLUMN IF NOT EXISTS secret_version INTEGER NOT NULL DEFAULT 1",
    "ADD COLUMN IF NOT EXISTS previous_secret_version INTEGER",
    "ADD COLUMN IF NOT EXISTS rotation_started_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS rotation_ends_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS rotation_completed_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS rotated_by UUID",
    "ADD COLUMN IF NOT EXISTS rotation_started_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS rotation_ending_notified_at TIMESTAMPTZ",
]

_WEBHOOK_DELIVERY_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS dead_lettered_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS replayed_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS replayed_by UUID",
    "ADD COLUMN IF NOT EXISTS replay_count INTEGER NOT NULL DEFAULT 0",
]

_REVIEW_ASSIGNMENT_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS reviewer_source VARCHAR(30)",
]

_INVITATION_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS invited_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS expired_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS revoked_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS joined_notified_at TIMESTAMPTZ",
]

# ZST-EC-001 ORG-008. Organizations previously had no owner at all - just interchangeable
# org_admins - so there was nothing for an ownership transfer to move. Nullable because
# existing organizations genuinely have no recorded owner and picking an arbitrary admin
# would misattribute accountability.
_ORG_OWNER_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL",
]

_IDENTITY_CHALLENGE_COLUMNS = [
    # IDN-007 lockout. The table predates it, so create_all() alone will not add it.
    "ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ",
]


# Columns models/event.py no longer declares. See the call site for why they must be relaxed
# rather than re-added: main deliberately removed them from the model.
_EVENT_RELAX_NOT_NULL = ("auto_start_recording", "auto_end_event")


def _relax_not_null(table: str, column: str) -> str:
    """DROP NOT NULL on a column that may not exist (see the call site for why)."""
    return f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '{table}' AND column_name = '{column}'
            ) THEN
                EXECUTE 'ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL';
            END IF;
        END $$;
    """


# ── ZST-EC-001 MED-007 / MED-008 / MED-011 — recording governance ────────────────────────
# `validation_status` / `validation_evidence` / `retention_*` / `legal_hold` already existed;
# these are the health, finalization-announcement and deletion-lifecycle columns.
# MED-002 governed signal state + notification bookkeeping on live inputs. Same omission as
# _BROADCAST_SESSION_COLUMNS below: models/live.py declares these ten columns and nothing ever
# added them, so `create_all()` covered a brand-new database while every existing one raised
# UndefinedColumn — here it took down the media sweeper on every tick, not just one request.
#
# `signal_state` and `interruption_count` are NOT NULL in the model with Python-side defaults.
# A bare NOT NULL ADD COLUMN cannot be applied to a table that already has rows, so both carry
# the SAME default in SQL: existing inputs land on "healthy"/0, which is exactly what the model
# would have assigned them, rather than being invented here.
_LIVE_INGRESS_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS signal_state VARCHAR(16) NOT NULL DEFAULT 'healthy'",
    "ADD COLUMN IF NOT EXISTS signal_changed_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS signal_lost_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS last_signal_ok_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS interruption_count INTEGER NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS interruption_window_started_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS created_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS interrupted_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS intermittent_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS recovered_notified_at TIMESTAMPTZ",
]

# Broadcast health + notification bookkeeping. models/broadcast.py declares these eight
# columns, but nothing in this file ever added them — so `create_all()` produced them on a
# BRAND-NEW database and every pre-existing database silently lacked them, failing any
# broadcast_sessions query with UndefinedColumn. Same additive, IF NOT EXISTS shape as every
# other list here, so it is a no-op wherever they are already present.
_BROADCAST_SESSION_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS health_level VARCHAR(8)",
    "ADD COLUMN IF NOT EXISTS health_changed_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS health_issues TEXT",
    "ADD COLUMN IF NOT EXISTS started_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS ended_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS health_failed_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS health_degraded_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS health_recovered_notified_at TIMESTAMPTZ",
]

_MED_RECORDING_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS hold_category VARCHAR(40)",
    "ADD COLUMN IF NOT EXISTS hold_reference VARCHAR(80)",
    "ADD COLUMN IF NOT EXISTS hold_set_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS hold_set_by UUID",
    "ADD COLUMN IF NOT EXISTS hold_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS hold_released_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS health_state VARCHAR(16)",
    "ADD COLUMN IF NOT EXISTS health_changed_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS health_reason TEXT",
    "ADD COLUMN IF NOT EXISTS started_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS degraded_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS recovered_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS stopped_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS finalization_notified_state VARCHAR(16)",
    "ADD COLUMN IF NOT EXISTS finalization_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS deletion_status VARCHAR(16)",
    "ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS deletion_requested_by UUID",
    "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS deletion_failure_category VARCHAR(60)",
    "ADD COLUMN IF NOT EXISTS deletion_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS deletion_failed_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS retention_warned_at TIMESTAMPTZ",
]

# ── ZST-EC-001 MED-009 — replay lifecycle ────────────────────────────────────────────────
_MED_REPLAY_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS withdrawn_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS withdrawn_by UUID",
    "ADD COLUMN IF NOT EXISTS withdraw_reason VARCHAR(200)",
    "ADD COLUMN IF NOT EXISTS previous_publish_state VARCHAR(20)",
    "ADD COLUMN IF NOT EXISTS state_changed_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS prepared_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS published_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS access_changed_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS withdrawn_notified_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS expired_notified_at TIMESTAMPTZ",
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
        for clause in _INVITATION_COLUMNS:
            conn.execute(text(f"ALTER TABLE invitations {clause}"))
        for clause in _REVIEW_ASSIGNMENT_COLUMNS:
            conn.execute(text(f"ALTER TABLE access_review_assignments {clause}"))
        for clause in _WEBHOOK_ENDPOINT_COLUMNS:
            conn.execute(text(f"ALTER TABLE webhook_endpoints {clause}"))
        for clause in _WEBHOOK_ROTATION_COLUMNS:
            conn.execute(text(f"ALTER TABLE webhook_endpoints {clause}"))
        for clause in _WEBHOOK_DELIVERY_COLUMNS:
            conn.execute(text(f"ALTER TABLE webhook_deliveries {clause}"))
        for clause in _ORG_OWNER_COLUMNS:
            conn.execute(text(f"ALTER TABLE organizations {clause}"))
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
            # These relax LEGACY columns that only exist in databases predating the current
            # model. On a fresh database create_all() builds the table without them, and a
            # bare ALTER COLUMN then aborts the whole migration — which is why bootstrapping
            # an empty database used to fail here. Postgres has no ALTER COLUMN IF EXISTS,
            # so the guard is explicit.
            conn.execute(text(_relax_not_null("event_registrations", col)))
        for col in _EVENT_RELAX_NOT_NULL:
            # Same legacy problem, one table over, surfaced by merging main. main removed
            # `auto_start_recording` / `auto_end_event` from models/event.py, but the columns
            # survive in any database created while the model still had them — as NOT NULL
            # with NO server default, because the old model supplied the default Python-side.
            # SQLAlchemy no longer sends a value for a column it does not know about, so every
            # Event insert hit a NotNullViolation on exactly those databases (a fresh one is
            # unaffected: the ADD COLUMN above carries DEFAULT FALSE). Relaxing the constraint
            # is the non-destructive fix — the data stays, and the column stops being required
            # by a model that no longer manages it.
            #
            # This is also where the other side of this merge relaxed `auto_start_recording`
            # via its own separate pass: same table, same guard, and this tuple is a strict
            # superset (it also covers `auto_end_event`), so the events relax happens once here.
            conn.execute(text(_relax_not_null("events", col)))
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
        # UNION of both sides of this merge — neither is optional. The media columns and the
        # Phase 6-10 statements are independent migrations that happen to land in the same
        # block; dropping either leaves a database missing columns its models declare.
        for clause in _LIVE_INGRESS_COLUMNS:
            conn.execute(text(f"ALTER TABLE live_ingress_endpoints {clause}"))
        for clause in _BROADCAST_SESSION_COLUMNS:
            conn.execute(text(f"ALTER TABLE broadcast_sessions {clause}"))
        for clause in _MED_RECORDING_COLUMNS:
            conn.execute(text(f"ALTER TABLE live_recordings {clause}"))
        for clause in _MED_REPLAY_COLUMNS:
            conn.execute(text(f"ALTER TABLE replay_entitlements {clause}"))
        # Phase 10 carries the Ledger 1 provider columns (subscriptions.stripe_customer_id,
        # stripe_subscription_id, checkout_session_ref) that the subscription webhook path
        # correlates on, so this is load-bearing for Stripe billing.
        for stmt in _PHASE6_STATEMENTS:
            conn.execute(text(stmt))
        for stmt in _PHASE7_STATEMENTS:
            conn.execute(text(stmt))
        for stmt in _PHASE8_STATEMENTS:
            conn.execute(text(stmt))
        for stmt in _PHASE9_STATEMENTS:
            conn.execute(text(stmt))
        for stmt in _PHASE10_STATEMENTS:
            conn.execute(text(stmt))
        # The orphan-`events` DROP NOT NULL pass that the other side of this merge put here is
        # deliberately absent: the `_EVENT_RELAX_NOT_NULL` loop earlier in this function does the
        # same guarded relax on the same table and covers a superset of the columns.
    print("Schema ready!")


if __name__ == "__main__":
    ensure_schema()
