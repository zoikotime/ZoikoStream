import time

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

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
    # Public organizer card on the viewer landing page. `verified` is the platform's badge
    # (super-admin set) and is deliberately separate from domain_verified (a DNS fact).
    "ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE",
    "ADD COLUMN IF NOT EXISTS social_links JSONB",
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
]

# Blast-radius class for an event. Drives which readiness gates are mandatory and which
# events surface on the Command Center's high-impact list.
_EVENT_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS impact VARCHAR(20) NOT NULL DEFAULT 'standard'",
    # Shown on the viewer landing page (location line + the accessibility row).
    "ADD COLUMN IF NOT EXISTS location VARCHAR(200)",
    "ADD COLUMN IF NOT EXISTS captions_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ADD COLUMN IF NOT EXISTS translation_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    # Event-management module: replay switch, encoder target, concurrency ceiling and the
    # optional audience passphrase. Defaults match models/event.py so existing rows keep
    # their current behaviour (no replay, 1080p, no cap, no passphrase).
    "ADD COLUMN IF NOT EXISTS replay_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ADD COLUMN IF NOT EXISTS stream_quality VARCHAR(16) NOT NULL DEFAULT '1080p'",
    "ADD COLUMN IF NOT EXISTS max_participants INTEGER",
    "ADD COLUMN IF NOT EXISTS access_password_hash VARCHAR(255)",
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

# Invitation & Access Management. `invitations` predates this, so create_all() will NOT add
# these — every existing database needs the ALTERs.
#
# Delivery is recorded as TIMESTAMPS, never as extra statuses: the accept and decline paths
# resolve tokens against one open state, and a "sent" status would have made every emailed
# invitation unredeemable. sent_at IS NULL is also what distinguishes a manual invitation
# (link handed over out of band) from a mailed one.
_INVITATION_COLUMNS = [
    # Event scope. NULL = an org-membership invitation (the original behaviour).
    "ADD COLUMN IF NOT EXISTS event_id UUID",
    "ADD COLUMN IF NOT EXISTS event_role VARCHAR(20)",
    "ADD COLUMN IF NOT EXISTS message TEXT",
    # Delivery facts.
    "ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS send_error TEXT",
    "ADD COLUMN IF NOT EXISTS send_attempts INTEGER NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS resend_count INTEGER NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS provider_message_id VARCHAR(120)",
    # Transition timestamps + soft delete.
    "ADD COLUMN IF NOT EXISTS declined_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
]

# Statements run verbatim, in this order. ensure_schema() executes everything in ONE
# transaction, so each must be individually idempotent — a single DuplicateTable would roll
# back the organizations/users/events ALTERs above it too.
_INVITATION_DDL = [
    # The FK goes in separately: ADD COLUMN IF NOT EXISTS skips the WHOLE clause once the
    # column exists, so an inline REFERENCES would silently leave no constraint behind on any
    # database where event_id was added first.
    """DO $$ BEGIN
         ALTER TABLE invitations ADD CONSTRAINT fk_invitations_event
           FOREIGN KEY (event_id) REFERENCES events(id);
       EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
    "CREATE INDEX IF NOT EXISTS ix_invitations_event_id ON invitations (event_id)",
    "CREATE INDEX IF NOT EXISTS ix_invitations_provider_message_id ON invitations (provider_message_id)",
    # Dedupe BEFORE creating the unique indexes: CREATE UNIQUE INDEX aborts outright if
    # duplicates already exist, and they are reachable on any database that ran the old code
    # (its pre-check and its INSERT were not one transaction). Keep the newest open row per
    # key and cancel the rest. Idempotent — a second run matches nothing.
    """UPDATE invitations SET status = 'cancelled'
       WHERE status = 'pending' AND deleted_at IS NULL AND id NOT IN (
         SELECT DISTINCT ON (org_id, lower(email), coalesce(event_id::text, '')) id
         FROM invitations
         WHERE status = 'pending' AND deleted_at IS NULL
         ORDER BY org_id, lower(email), coalesce(event_id::text, ''), created_at DESC
       )""",
    # Duplicate prevention — the REAL guarantee (the app's open_invite_exists check only
    # produces a friendlier message). Two partial indexes rather than one coalesce() index:
    # no sentinel UUID is needed, and an org-membership key is genuinely a different key from
    # an event key. Both WHERE clauses must stay in step with models.invitation.OPEN_STATUSES.
    #
    # event_role is deliberately OUT of the key: one open invitation per person per event.
    # Changing someone's intended role means cancel-and-reinvite, which keeps the history
    # readable instead of accumulating three open invitations for one person.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_invitations_open_org
         ON invitations (org_id, lower(email))
         WHERE event_id IS NULL AND status = 'pending' AND deleted_at IS NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_invitations_open_event
         ON invitations (org_id, lower(email), event_id)
         WHERE event_id IS NOT NULL AND status = 'pending' AND deleted_at IS NULL""",
]

# Live-recording drift. size_bytes was INTEGER (2 GB ceiling) and stayed NULL because nothing
# wrote it; the egress_ended webhook now records the real file size, so it must be BIGINT.
# USING is required — Postgres will not widen a column in place without it.
_LIVE_RECORDING_DDL = [
    """DO $$ BEGIN
         ALTER TABLE live_recordings ALTER COLUMN size_bytes TYPE BIGINT USING size_bytes::bigint;
       EXCEPTION WHEN undefined_table THEN NULL; END $$""",
]

# Moderation Center. Both tables predate this module, so create_all() will not add these.
#   highlighted — "read this one out", non-exclusive, distinct from the single pinned message.
#   waiting     — lobby depth per sample, so the moderator DASHBOARD can show queue sizes
#                 across many events from one query instead of a Redis read per event.
_LIVE_MESSAGE_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS highlighted BOOLEAN NOT NULL DEFAULT FALSE",
]
_ANALYTICS_SNAPSHOT_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS waiting INTEGER NOT NULL DEFAULT 0",
]

# Speaker & Panellist console. `speaker_assets` is a brand-new table, so create_all() builds it;
# these two tables already existed.
#
#   live_questions.answer_*      — the speaker's written answer, who gave it and when. Stored
#                                  rather than derived, so the Q&A export and the "questions
#                                  answered" figure can point at a real answer.
#   event_assignments.notes      — the assignee's PRIVATE notes for that event. This row is
#                                  already exactly per-event-per-person and already org-scoped,
#                                  which is why it is the home rather than a new table.
_LIVE_QUESTION_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS answer_text TEXT",
    "ADD COLUMN IF NOT EXISTS answered_by UUID",
    "ADD COLUMN IF NOT EXISTS answered_at TIMESTAMPTZ",
]
_EVENT_ASSIGNMENT_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS notes TEXT",
]

# Attendee & Viewer experience. `event_registrations` is a brand-new table (create_all builds it);
# these two columns are on tables that already existed.
#
#   users.preferences        — per-PERSON settings spanning every event (language, accessibility,
#                              favourite speakers). Read whole, for one person, by that person, so
#                              a JSON column beats four tables.
#   speaker_assets.shared    — makes an approved deck an attendee DOWNLOAD. Deliberately separate
#                              from `status`: presentable to the room is not the same decision as
#                              downloadable by ten thousand people.
_USER_PREFERENCE_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS preferences JSONB",
]
_SPEAKER_ASSET_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS shared BOOLEAN NOT NULL DEFAULT FALSE",
]
# `event_registrations` already EXISTED — scaffolded (empty, and referenced by no model or code)
# for the dummy public form on /e/:id, with only id/event_id/name/email/created_at. create_all()
# only creates MISSING tables, so it skipped it and every column below has to be added by hand.
# Adopted rather than dropped: it holds no rows, but dropping a table is not something a migration
# script should do quietly.
_REGISTRATION_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS org_id UUID",
    "ADD COLUMN IF NOT EXISTS user_id UUID",
    "ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'registered'",
    "ADD COLUMN IF NOT EXISTS registered_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS bookmarked BOOLEAN NOT NULL DEFAULT FALSE",
    "ADD COLUMN IF NOT EXISTS reminder_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS question_bookmarks JSONB",
    "ADD COLUMN IF NOT EXISTS first_joined_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS last_joined_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS watch_seconds INTEGER NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS join_count INTEGER NOT NULL DEFAULT 0",
    # name/email were NOT NULL for the guest form. A registration is keyed on user_id now, so they
    # become nullable snapshots — otherwise every insert would have to invent values for them.
    "ALTER COLUMN name DROP NOT NULL",
    "ALTER COLUMN email DROP NOT NULL",
]

# The dashboard reads "my events" by user; an organizer reads "who registered" by event (already
# indexed). The unique key is what makes register/cancel/re-register reuse one row.
_ATTENDEE_DDL = [
    "CREATE INDEX IF NOT EXISTS ix_event_registrations_user_status "
    "ON event_registrations (user_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_event_registrations_event_id "
    "ON event_registrations (event_id)",
    "CREATE INDEX IF NOT EXISTS ix_event_registrations_org_id "
    "ON event_registrations (org_id)",
    """DO $$ BEGIN
         ALTER TABLE event_registrations
           ADD CONSTRAINT uq_registration_event_user UNIQUE (event_id, user_id);
       EXCEPTION WHEN duplicate_table THEN NULL; WHEN duplicate_object THEN NULL; END $$""",
    """DO $$ BEGIN
         ALTER TABLE event_registrations ADD CONSTRAINT fk_registrations_user
           FOREIGN KEY (user_id) REFERENCES users(id);
       EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
    # ADD COLUMN cannot make a column NOT NULL on an existing table without inventing a default,
    # so org_id/user_id arrived nullable while the model declares them required — the ORM would
    # enforce it and the database would not. Tightened here, but ONLY when no row would violate
    # it: on a deployment that somehow has orphan rows this is a no-op rather than a failed
    # migration, and the mismatch stays visible instead of blocking every other statement.
    """DO $$ BEGIN
         IF NOT EXISTS (SELECT 1 FROM event_registrations
                        WHERE user_id IS NULL OR org_id IS NULL) THEN
           ALTER TABLE event_registrations ALTER COLUMN user_id SET NOT NULL;
           ALTER TABLE event_registrations ALTER COLUMN org_id SET NOT NULL;
         END IF;
       END $$""",
]


# Recording & Media Library. `live_recordings` is now the library item itself (see the model for
# why there is no second media_assets table), so every library field is a column here.
# `media_folders` and `media_marks` are brand-new tables that create_all() builds.
#
#   storage_key   — the bucket key we assigned. Signed URLs are minted from this ALONE, so a row
#                   whose key we never set can never be handed out as a download, whatever
#                   file_url happens to contain.
#   duration_ms   — real playable length from the egress result, not stopped_at - started_at
#                   (which includes paused stretches the file does not contain).
#   deleted_at    — recycle bin. Indexed because every library query filters on it.
_MEDIA_RECORDING_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS storage_key VARCHAR(500)",
    "ADD COLUMN IF NOT EXISTS title VARCHAR(200)",
    "ADD COLUMN IF NOT EXISTS description TEXT",
    "ADD COLUMN IF NOT EXISTS folder_id UUID",
    "ADD COLUMN IF NOT EXISTS tags JSONB",
    "ADD COLUMN IF NOT EXISTS category VARCHAR(40)",
    "ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'organization'",
    "ADD COLUMN IF NOT EXISTS duration_ms BIGINT",
    "ADD COLUMN IF NOT EXISTS view_count INTEGER NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS download_count INTEGER NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS last_viewed_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS archived_by UUID",
    "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS deleted_by UUID",
    "ADD COLUMN IF NOT EXISTS storage_class VARCHAR(20)",
    "ADD COLUMN IF NOT EXISTS retry_of UUID",
    "ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0",
    "ADD COLUMN IF NOT EXISTS download_policy VARCHAR(20)",
    "ADD COLUMN IF NOT EXISTS download_password_hash VARCHAR(200)",
    "ADD COLUMN IF NOT EXISTS download_expires_at TIMESTAMPTZ",
    "ADD COLUMN IF NOT EXISTS watermark BOOLEAN",
    "ADD COLUMN IF NOT EXISTS transcript JSONB",
    "ADD COLUMN IF NOT EXISTS insights JSONB",
]

_ORG_MEDIA_COLUMNS = [
    "ADD COLUMN IF NOT EXISTS media JSONB",
    "ADD COLUMN IF NOT EXISTS storage_quota_gb DOUBLE PRECISION NOT NULL DEFAULT 100",
]

# The library's own access paths. Every one of these backs a query the library runs on every page
# load, and each is composite because the org filter is never optional — a single-column index on
# deleted_at would have Postgres scan every tenant's rows to serve one tenant's shelf.
_MEDIA_DDL = [
    "CREATE INDEX IF NOT EXISTS ix_live_recordings_org_deleted "
    "ON live_recordings (org_id, deleted_at)",
    "CREATE INDEX IF NOT EXISTS ix_live_recordings_org_folder "
    "ON live_recordings (org_id, folder_id)",
    # Retention sweeps and the default "newest first" shelf both order by this.
    "CREATE INDEX IF NOT EXISTS ix_live_recordings_org_stopped "
    "ON live_recordings (org_id, stopped_at)",
    "CREATE INDEX IF NOT EXISTS ix_live_recordings_retry_of ON live_recordings (retry_of)",
    # One mark per person per position per recording: a double-click on Bookmark must not create
    # two marks at the same millisecond. Notes are exempt — several notes at one timestamp is a
    # legitimate thing to write — which is why the key is partial on note IS NULL.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_media_marks_bookmark
         ON media_marks (recording_id, user_id, at_ms)
         WHERE note IS NULL""",
    "CREATE INDEX IF NOT EXISTS ix_media_marks_recording_user "
    "ON media_marks (recording_id, user_id)",
    # Folder names are unique per parent per org, so "New folder" twice is a friendly refusal
    # rather than two identically-named shelves nobody can tell apart. coalesce() on parent_id
    # because NULL (a root folder) must participate in the key.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_media_folders_name
         ON media_folders (org_id, coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid),
                           lower(name))
         WHERE deleted_at IS NULL""",
]


def _ddl(conn):
    """Every ALTER/CREATE, in order. Split out of ensure_schema so it can be retried."""
    # ADD COLUMN takes ACCESS EXCLUSIVE even when it is a no-op, so a concurrent reader on any of
    # these tables can deadlock the whole batch — which it did, repeatedly, against the Supabase
    # pooler (PostgREST introspects the schema in the background). lock_timeout turns that into a
    # clean, retryable failure instead of a deadlock that rolls back every earlier statement.
    conn.execute(text("SET LOCAL lock_timeout = '4s'"))
    for clause in _ORG_COLUMNS:
        conn.execute(text(f"ALTER TABLE organizations {clause}"))
    for stmt in _ORG_INDEXES:
        conn.execute(text(stmt))
    for clause in _USER_COLUMNS:
        conn.execute(text(f"ALTER TABLE users {clause}"))
    for clause in _EVENT_COLUMNS:
        conn.execute(text(f"ALTER TABLE events {clause}"))
    for clause in _FEATURE_FLAG_COLUMNS:
        conn.execute(text(f"ALTER TABLE feature_flags {clause}"))
    for clause in _SUPPORT_TICKET_COLUMNS:
        conn.execute(text(f"ALTER TABLE support_tickets {clause}"))
    for clause in _INVITATION_COLUMNS:
        conn.execute(text(f"ALTER TABLE invitations {clause}"))
    # Order matters here: the columns above must exist, and the dedupe inside this list
    # must run before its unique indexes.
    for stmt in _INVITATION_DDL:
        conn.execute(text(stmt))
    for stmt in _LIVE_RECORDING_DDL:
        conn.execute(text(stmt))
    for clause in _LIVE_MESSAGE_COLUMNS:
        conn.execute(text(f"ALTER TABLE live_messages {clause}"))
    for clause in _ANALYTICS_SNAPSHOT_COLUMNS:
        conn.execute(text(f"ALTER TABLE analytics_snapshots {clause}"))
    for clause in _LIVE_QUESTION_COLUMNS:
        conn.execute(text(f"ALTER TABLE live_questions {clause}"))
    for clause in _EVENT_ASSIGNMENT_COLUMNS:
        conn.execute(text(f"ALTER TABLE event_assignments {clause}"))
    for clause in _USER_PREFERENCE_COLUMNS:
        conn.execute(text(f"ALTER TABLE users {clause}"))
    for clause in _SPEAKER_ASSET_COLUMNS:
        conn.execute(text(f"ALTER TABLE speaker_assets {clause}"))
    for clause in _REGISTRATION_COLUMNS:
        conn.execute(text(f"ALTER TABLE event_registrations {clause}"))
    for stmt in _ATTENDEE_DDL:
        conn.execute(text(stmt))
    for clause in _MEDIA_RECORDING_COLUMNS:
        conn.execute(text(f"ALTER TABLE live_recordings {clause}"))
    for clause in _ORG_MEDIA_COLUMNS:
        conn.execute(text(f"ALTER TABLE organizations {clause}"))
    # After the columns above — these index them.
    for stmt in _MEDIA_DDL:
        conn.execute(text(stmt))


def ensure_schema(attempts: int = 4):
    """Create any missing tables and add any missing columns. Idempotent — safe to re-run.

    Retries the DDL: every statement is individually idempotent, so a lock timeout or a deadlock
    against a background reader is a "try again", not a failure.
    """
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("Ensuring columns...")
    for attempt in range(1, attempts + 1):
        try:
            with engine.begin() as conn:
                _ddl(conn)
            break
        except OperationalError as exc:
            if attempt == attempts:
                raise
            print(f"  lock contention (attempt {attempt}/{attempts}) — retrying: "
                  f"{str(exc.orig).splitlines()[0]}")
            time.sleep(2 * attempt)
    print("Schema ready!")


if __name__ == "__main__":
    ensure_schema()
