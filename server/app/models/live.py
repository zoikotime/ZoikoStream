"""Live-event moderation tables: chat, Q&A, polls, announcements, activity.

Scoped like every other table: `org_id` is copied from the event so a query can be
org-isolated without a join. Presence/participants are deliberately NOT here — they
are ephemeral and live in Redis (services/bus.py).

Moderator actions are audited into the existing `audit_logs` table (models/audit_log.py)
rather than a parallel moderator_actions table — it already carries actor, action,
target, org and a meta blob.

ponytail: pinned/deleted messages are COLUMNS here, not their own tables — "persist
pinned messages / deleted messages" is a flag on the row, and a soft delete keeps the
chat log intact for compliance export.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

MESSAGE_STATUSES = ("approved", "pending", "deleted")
QUESTION_STATUSES = ("pending", "approved", "answered", "dismissed")
POLL_STATUSES = ("draft", "scheduled", "live", "closed")
ANNOUNCEMENT_PRIORITIES = ("normal", "important", "urgent")
# Feed kinds — keep in sync with ACT_ICON in components/moderation/ModeratorSidebar.jsx.
ACTIVITY_KINDS = ("join", "leave", "chat", "qa", "poll", "mod", "system", "role", "recording")

# The canonical contributor-session state machine (BRD Section 10). "invited" is not a
# state — it's `invited_at` non-null on a row that hasn't advanced past "waiting" yet.
CONTRIBUTOR_STATES = (
    "waiting", "connected", "ready", "on_standby", "live", "muted",
    "reconnecting", "removed", "failed",
)


class _EventScoped(Base):
    """Shared identity + scoping columns for every live table."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class LiveMessage(_EventScoped):
    """One chat message. `flags` holds automatic detections (profanity | spam | link |
    duplicate) so moderators can filter without re-scanning text."""

    __tablename__ = "live_messages"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    author_name: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="approved", nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    flags: Mapped[list | None] = mapped_column(JSON, default=list)
    reactions: Mapped[dict | None] = mapped_column(JSON, default=dict)   # emoji -> count
    reply_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)                       # moderator-only note
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class LiveQuestion(_EventScoped):
    __tablename__ = "live_questions"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    author_name: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    votes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assigned_name: Mapped[str | None] = mapped_column(String(120))       # speaker it's routed to
    flags: Mapped[list | None] = mapped_column(JSON, default=list)


class LiveQuestionVote(_EventScoped):
    """One row per (question, voter) — the ledger moderation._qa_vote checks so a page
    refresh can't add another upvote on top of one already cast. `user_id` is ctx.user_id,
    same identity concept as everywhere else in live.py's sibling module (a real User.id,
    an EventRegistration.id, or an EventAccessLink.id — see moderation.resolve_ctx*); a
    shared access link means every visitor on it is one collective voter, matching how
    chat slow-mode already treats them. This was flagged as deferred, not forgotten — see
    the ponytail note this table replaces in moderation._qa_vote's old single-column version."""

    __tablename__ = "live_question_votes"
    __table_args__ = (UniqueConstraint("question_id", "user_id", name="uq_live_question_vote"),)

    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("live_questions.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class LivePoll(_EventScoped):
    """`options` is [{label, votes}] — the exact shape the console already renders, so
    no per-option table and no mapping layer. `closes_at` drives the countdown; the
    scheduler in routers/live.py flips scheduled -> live and live -> closed."""

    __tablename__ = "live_polls"

    question: Mapped[str] = mapped_column(String(300), nullable=False)
    options: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class LivePollVote(_EventScoped):
    """One row per (poll, voter) — the ledger moderation._poll_vote checks so a page
    refresh (or a second tab) can't cast a second vote. Same user_id identity concept as
    LiveQuestionVote above. A poll vote is final by design (the console never offers
    "change your vote" — see components/watch/WatchPanel.jsx's Poll), so unlike Q&A's
    up/down toggle, this table only ever gets one insert per voter; a repeat attempt is
    rejected outright rather than updated."""

    __tablename__ = "live_poll_votes"
    __table_args__ = (UniqueConstraint("poll_id", "user_id", name="uq_live_poll_vote"),)

    poll_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("live_polls.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    option: Mapped[int] = mapped_column(Integer, nullable=False)


class LiveAnnouncement(_EventScoped):
    """`delivered_to` is the real connection count at send time — not an estimate."""

    __tablename__ = "live_announcements"

    text: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_to: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class BroadcastSession(_EventScoped):
    """One row per go-live attempt — the durable record of what the host did.

    `settings` holds the live control state (media targets + chat/Q&A/poll toggles). The
    HOT copy lives in the bus (services/bus.state_*) because chat checks it per message;
    this row is what a cold worker rehydrates from and what an audit reads afterwards.

    A pause does not end the session: ended_at is set once, so "how long was this
    broadcast" stays answerable and a resume doesn't create a second row.
    """

    __tablename__ = "broadcast_sessions"

    status: Mapped[str] = mapped_column(String(16), default="preview", nullable=False)
    settings: Mapped[dict | None] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Total time spent paused, so elapsed-live-time excludes it.
    paused_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    peak_viewers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ended_reason: Mapped[str | None] = mapped_column(String(40))   # host | emergency_stop | room_finished
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


BROADCAST_STATUSES = ("preview", "live", "paused", "ended")
RECORDING_STATUSES = ("idle", "recording", "paused", "stopped", "failed")


class LiveRecording(_EventScoped):
    """One row per start→stop recording cycle (a pause/resume stays on the same row, with
    the paused time accumulated). Gives the host console its timer, its recording log and
    its download/backup links from real data rather than a derived guess."""

    __tablename__ = "live_recordings"

    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="recording", nullable=False)
    quality: Mapped[str | None] = mapped_column(String(16))          # 720p | 1080p | 2k | 4k
    egress_id: Mapped[str | None] = mapped_column(String(80))        # LiveKit egress handle
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    file_url: Mapped[str | None] = mapped_column(String(500))
    auto_upload: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # False when LiveKit egress wasn't reachable/configured — the console shows the
    # difference instead of implying a file exists.
    enforced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # ── Commercial recording fields (ZST-LE-COM-001 Section 14/J) ──────────────────────
    # R2/R3 service profiles require independent dual recording (doc J1) — two LiveRecording
    # rows, one per role, both against the same event. `role` is nullable because most
    # events (R0/R1, or events created before the commercial layer) run a single recording
    # with no primary/secondary distinction at all.
    role: Mapped[str | None] = mapped_column(String(16))              # primary | secondary
    validation_status: Mapped[str | None] = mapped_column(String(16))  # captured|validating|valid|degraded|failed
    # services/validation.py's real ffprobe-based comparison for a dual-recording pair —
    # durations, stream presence, the delta, when it ran, and an explicit note on what
    # ISN'T checked (gap/black-frame detection, caption QA). Written identically to BOTH
    # rows of a pair, since the evidence describes the comparison, not one side of it.
    validation_evidence: Mapped[dict | None] = mapped_column(JSON)
    retention_policy_version: Mapped[str | None] = mapped_column(String(60))
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Overrides normal retention expiry for this specific asset (doc R6) — distinct from a
    # paid extended-retention add-on, which is an EventOrderLine, not this flag.
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AnalyticsSnapshot(_EventScoped):
    """Periodic sample of the live counters — this IS the retention graph. Written by the
    sampler in services/broadcast.py, one row per interval per live event.
    ponytail: a plain row per sample, not a timeseries DB. At one row / 15s an 8-hour
    event is ~1900 rows; revisit only if events get much longer or much more frequent."""

    __tablename__ = "analytics_snapshots"

    viewers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    participants: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    on_stage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    messages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    questions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reactions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hands: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class EventFeedback(_EventScoped):
    """One row per feedback submission — a viewer leaving the event (see
    services/moderation._feedback_submit, which now rejects a host/moderator connection's
    submission outright). `role` is kept as a column for backward compatibility with rows
    written before that change, but every current row is "viewer" and every reader
    (the host console's own Feedback tab and the organization's event detail page) filters
    on it. Both `rating` and `comment` are optional individually (the submitter can skip
    either), but the handler that writes this row requires at least one of them — an
    empty submission isn't persisted at all, it's just treated as a skip."""

    __tablename__ = "event_feedback"

    role: Mapped[str] = mapped_column(String(16), nullable=False)     # viewer (legacy rows: host)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    identity: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120))
    rating: Mapped[int | None] = mapped_column(Integer)                # 1-5
    comment: Mapped[str | None] = mapped_column(Text)


class ContributorSession(_EventScoped):
    """One row per (event, assigned speaker) — the durable invitation + backstage state a
    dropped LiveKit room must not lose (presence in services/bus.py is ephemeral and wiped
    on room end; consent/preflight/rehearsal have to survive that). `EventAssignment(role=
    "speaker")` stays the eligibility list; this is the per-event runtime record for one
    of those assignees. `state` is one of CONTRIBUTOR_STATES above."""

    __tablename__ = "contributor_sessions"
    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_contributor_session"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    identity: Mapped[str] = mapped_column(String(80), nullable=False)   # str(user_id) — matches Ctx.identity
    state: Mapped[str] = mapped_column(String(16), default="waiting", nullable=False)

    # invitation
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    join_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    join_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contribution_method: Mapped[str] = mapped_column(String(20), default="livekit_browser", nullable=False)
    consent_notice: Mapped[str | None] = mapped_column(Text)
    support_contact: Mapped[str | None] = mapped_column(String(300))

    # consent + preflight — Postgres, not presence, so a dropped room doesn't lose them
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preflight_result: Mapped[dict | None] = mapped_column(JSON)  # camera_ok/mic_ok/speaker_ok/
                                                                  # network_quality/framing_ok/
                                                                  # browser_supported/passed/tested_at
    rehearsal_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rehearsal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # operator actions
    admitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    brought_live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_reason: Mapped[str | None] = mapped_column(String(200))
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


INGRESS_TYPES = ("rtmp", "whip")
INGRESS_STATES = ("inactive", "buffering", "publishing", "error", "complete")


class LiveIngressEndpoint(_EventScoped):
    """One row per hardware/RTMP-or-WHIP ingest endpoint — the on-site primary/backup
    encoder contribution path (BRD Section 23.1), distinct from a browser contributor's
    app-mediated session (ContributorSession above). Mirrors LiveRecording's own reasoning
    for existing at all: LiveKit's Ingress API (services/livekit.py) is the enforcement
    layer, not the record-of-intent layer — list_ingress() alone has no title/description,
    no offline fallback when LiveKit is unconfigured, and a non-reusable ingress can vanish
    from LiveKit entirely once it completes, with nothing left to show an admin what used
    to be there.

    The raw `stream_key` is deliberately NOT a column here — it's fetched live from LiveKit
    (services.livekit.ingress_stream_key) only when the admin opens the detail sheet, same
    reveal-on-demand posture the old (deleted) /streams API had. Only `ingress_id` — an
    opaque handle, not a credential — is persisted."""

    __tablename__ = "live_ingress_endpoints"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    input_type: Mapped[str] = mapped_column(String(16), nullable=False)   # rtmp | whip
    ingress_id: Mapped[str | None] = mapped_column(String(80))            # null until provisioned
    state: Mapped[str] = mapped_column(String(16), default="inactive", nullable=False)
    participant_identity: Mapped[str | None] = mapped_column(String(120))
    # False when LiveKit was unconfigured/unreachable at creation — same honesty flag as
    # LiveRecording.enforced: the row still exists so the admin can see intent, but nothing
    # will actually arrive until this is fixed and the input is recreated.
    enforced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class LiveActivity(_EventScoped):
    """Append-only event timeline, written by ONE helper (services/moderation.record).
    Exists so the feed survives a reconnect and stays searchable/exportable without
    merging five tables at read time. Moderator actions land here AND in audit_logs —
    different readers: this one is the in-console timeline, that one is compliance."""

    __tablename__ = "live_activity"

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    actor_name: Mapped[str | None] = mapped_column(String(120))
    meta: Mapped[dict | None] = mapped_column(JSON)


DELIVERY_KINDS = ("export", "report")
WATERMARK_STATUSES = ("pending", "ready", "failed")


class CustomerDelivery(_EventScoped):
    """One authorized, expiring, single-recipient delivery of either a validated replay
    export or a generated event report to an external customer/family contact who is NOT
    a platform user — the BRD's "controlled customer export" (LE-AC-18) and post-event
    report release, sharing one mechanism since the spec repeatedly bundles them
    ("report/export", "replay/report").

    Same token convention as EventAccessLink/EventRegistration.claim_token_hash
    (crud/event.py::_hash_link_token, mirrored in crud/delivery.py) — the raw token is
    returned once at creation and only its sha256 hash is ever persisted. Deliberately NOT
    a public link: the token resolves to a metadata page (routers/deliveries.py), and for
    kind="export" the actual file URL is a separate, fresh, short-lived signed GCS call
    made only at the moment of download — nothing durable is embedded in the delivered
    page itself, which is what keeps this distinct from the disabled public player
    download.

    Distinct from models.commercial.ReplayEntitlement (scope="customer"): that row is
    commercial-tier bookkeeping (R2/R3 profiles only, doc J2) with no token, audit, or
    delivery mechanism of its own. This table is the actual delivery for ANY event,
    commercial or not."""

    __tablename__ = "customer_deliveries"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # export | report
    recording_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)  # kind="export"
    report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)      # kind="report"
    recipient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # ── Watermark burn-in (kind="export" only; BRD "policy watermark", LE-AC-12) ────────
    # Async — services/watermark.py's ffmpeg pass runs on a background ticker
    # (services/delivery.py::run_watermark_processor), not inline in the create call, since
    # a real recording can run 1-3 hours. No email goes out until "ready": an unwatermarked
    # delivery would be a compliance gap, not just a UX one.
    watermark_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # pending|ready|failed
    watermarked_file_key: Mapped[str | None] = mapped_column(String(500))  # burned-in copy; recording_id's file is never touched
    watermark_error: Mapped[str | None] = mapped_column(Text)


class EventReport(_EventScoped):
    """One generated snapshot of an event's audience + operations facts (BRD §18.2's
    "generated post-event audience and operations report" — explicitly not a live
    analytics console, no custom query). `data` is a point-in-time JSON snapshot, not a
    live query, so a released report reads the same to its recipient no matter what
    happens to the underlying rows afterward — `created_at` (from _EventScoped) doubles as
    "generated_at" for exactly that reason. `version` lets an operator regenerate after a
    correction without losing the history of what was actually released."""

    __tablename__ = "event_reports"

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
