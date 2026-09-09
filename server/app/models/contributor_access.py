"""Event contributor authorization (ZST-EC-001 CON-001 -> CON-005).

**The separation this file exists to create.** Before it, being an event contributor required
being an organization member: `routers/events._set_role` gates every assignee through
`crud.event.valid_member_ids` (which filters on `User.org_id == org_id`), and
`crud.event.upsert_contributor_invite` takes a `User` object and stores
`ContributorSession.user_id` as a NOT NULL foreign key. An external speaker therefore could
not be invited at all without first being given a tenant account - which grants organization
privileges as a side effect of appearing on one event.

Two distinct access domains are now modelled:

    ORGANIZATION MEMBERSHIP        User.org_id + User.role  (unchanged, untouched)
    EVENT CONTRIBUTOR AUTHORIZATION EventContributorGrant   (this file)

A grant is keyed on EMAIL, not on a user. `user_id` is nullable and is only filled when the
contributor happens to also hold a platform account - which is how an internal member and an
external guest travel the same path without either being forced into the other's shape.

**What is REUSED rather than rebuilt:**

  * `models.live.ContributorSession` is already the durable per-event backstage runtime record
    (consent, preflight, rehearsal, admit, connect/disconnect timestamps). It is NOT replaced.
    A `grant_id` column links it to the authorization above, and CON-005's authoritative
    end-of-session columns are added to it - because `last_disconnected_at` already exists
    there and a second session table would immediately disagree with it.
  * `models.event_planning.EventRehearsal` (LVE-005) stays the only rehearsal schedule.
    CON-003 reminders are derived from it; there is no second rehearsal model.
  * `ContributorSession.preflight_result` is already a real capability check
    (camera_ok / mic_ok / speaker_ok / browser_supported / framing_ok, written by
    `services/contributor._preflight_result`). CON-002 wraps a governed LIFECYCLE around that
    existing result rather than inventing a parallel set of checks.

**Roles** reuse the authoritative `ASSIGNMENT_ROLES` vocabulary (host / moderator / speaker)
plus `panelist` and `presenter`, which are genuinely distinct contribution shapes the product
copy already refers to. No `guest_contributor` role is added: "external" is a property of the
grant (no `user_id`), not a role, and encoding it as one would make an org member holding it
look external.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# -- CON-001 ----------------------------------------------------------------------------
GRANT_STATES = ("invited", "accepted", "revoked", "expired")
# Reuses the three authoritative EventAssignment roles and adds the two the product already
# distinguishes operationally. Deliberately NOT a new "guest" role - see the module docstring.
CONTRIBUTOR_ROLES = ("host", "moderator", "speaker", "panelist", "presenter")
# Which contributor roles may reach the moderation/host controls. A panelist or presenter
# contributes media only; they never get moderation authority from a contributor grant.
PRIVILEGED_CONTRIBUTOR_ROLES = ("host", "moderator")

# -- CON-002 ----------------------------------------------------------------------------
TECH_CHECK_STATES = ("required", "in_progress", "passed", "needs_attention", "expired")
# The capabilities `services/contributor._preflight_result` genuinely reports. Each maps to a
# real boolean the contributor's browser actually determined.
TECH_CHECK_CAPABILITIES = ("browser_supported", "camera_ok", "mic_ok", "speaker_ok",
                           "framing_ok")
# The three that decide PASSED. Copied from the existing admit gate in
# services/contributor._preflight_result so the two cannot disagree about what "passed" means.
TECH_CHECK_REQUIRED = ("browser_supported", "camera_ok", "mic_ok")
# Customer-safe failure categories. Raw diagnostics, user agents and addresses are never
# among them.
TECH_FAILURE_LABELS = {
    "browser_supported": "Unsupported browser",
    "camera_ok": "Camera unavailable or permission blocked",
    "mic_ok": "Microphone unavailable or permission blocked",
    "speaker_ok": "Audio output could not be confirmed",
    "framing_ok": "Camera framing could not be confirmed",
}

# There is NO measured network-quality test in this product. `network_quality` on
# preflight_result is a free-text string the client supplies, so it is reported as an
# unverified self-report and never counted toward PASSED.
NETWORK_TEST_SUPPORTED = False

# No governed technical-check validity period exists anywhere in this product, so no expiry
# duration is invented. The EXPIRED state stays reachable only if a policy is configured
# later; see services/contributor_access.TECH_CHECK_VALIDITY.
TECH_CHECK_VALIDITY_POLICY = None

# -- CON-004 ----------------------------------------------------------------------------
# Purpose-bound. A token issued for backstage access is valid for nothing else, and no other
# token type in this codebase is accepted here.
TOKEN_PURPOSE_EVENT_ACCESS = "contributor_event_access"
TOKEN_PURPOSE_INVITATION = "contributor_invitation"
TOKEN_PURPOSES = (TOKEN_PURPOSE_EVENT_ACCESS, TOKEN_PURPOSE_INVITATION)

# -- CON-005 ----------------------------------------------------------------------------
# Authoritative end reasons. A websocket drop is NOT among them: `left` requires a deliberate
# close, and the other three are state changes the platform itself commits.
SESSION_END_REASONS = ("left", "event_ended", "access_expired", "access_revoked")
SESSION_END_LABELS = {
    "left": "You left the session",
    "event_ended": "The event ended",
    "access_expired": "Your access window closed",
    "access_revoked": "Your contributor access was withdrawn",
}


class EventContributorGrant(Base):
    """Authorization for one person to contribute to one event.

    Email-keyed so an external contributor needs no platform account. Holding a grant confers
    NOTHING outside the named event: it is not a membership, not a role on the organization,
    and `services/contributor_access.authorize()` scopes every check to this event_id.
    """

    __tablename__ = "event_contributor_grants"
    __table_args__ = (
        # One grant per (event, email). A re-invite updates the existing row rather than
        # accumulating duplicates that could disagree about status.
        UniqueConstraint("event_id", "email", name="uq_event_contributor_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False,
                                                index=True)
    # The owning organization, copied from the event for org-isolated queries. This records
    # WHOSE event it is - it does not make the contributor a member of that organization.
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    # NULL for an external contributor with no platform account. Set when the invitee is (or
    # becomes) a real user, which is what allows identity-bound access for them - see
    # CON-004's forwardability note in services/contributor_access.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="invited", nullable=False)

    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Customer-safe only. Internal reasoning has no home here on purpose.
    revoke_reason: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    technical_check_required: Mapped[bool] = mapped_column(Boolean, default=True,
                                                           nullable=False)
    rehearsal_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The window in which a backstage token may be exchanged at all. Derived from the event
    # schedule by services/contributor_access, and re-derived when LVE-008 moves the event.
    access_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())

    invited_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped whenever the rehearsal this grant is reminded about is rescheduled, so a reminder
    # for the NEW time can be sent exactly once without a stale marker suppressing it.
    rehearsal_reminder_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rehearsal_reminded_version: Mapped[int | None] = mapped_column(Integer)


class ContributorAccessToken(Base):
    """A single-purpose, hashed, expiring credential for one contributor grant.

    Only the sha256 hash is stored - the same convention as
    `crud.event._hash_link_token`, `EventAccessLink` and the DEV-012 export tokens. The raw
    value exists once, in the message that carries it.

    This is emphatically NOT a LiveKit credential. Exchanging it server-side is what MINTS a
    short-lived LiveKit token; the media credential never travels by email.
    """

    __tablename__ = "contributor_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    grant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_contributor_grants.id"),
                                                nullable=False, index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,
                                                index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True,
                                            nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Recorded on exchange for audit. A coarse client hint, never a full user agent or an
    # address - neither is needed to answer "was this link used".
    used_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)


class ContributorTechnicalCheck(Base):
    """Governed lifecycle around one contributor's real preflight result.

    The RESULT comes from `ContributorSession.preflight_result`, which the contributor's own
    browser produces. This row adds the state machine, the customer-safe failure categories
    and the notification markers - none of which existed. It never invents a capability
    outcome: `apply()` copies the booleans the browser actually reported.
    """

    __tablename__ = "contributor_technical_checks"
    __table_args__ = (UniqueConstraint("grant_id", name="uq_contributor_tech_check"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    grant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_contributor_grants.id"),
                                                nullable=False, index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,
                                                index=True)
    status: Mapped[str] = mapped_column(String(20), default="required", nullable=False)

    browser_result: Mapped[bool | None] = mapped_column(Boolean)
    camera_result: Mapped[bool | None] = mapped_column(Boolean)
    microphone_result: Mapped[bool | None] = mapped_column(Boolean)
    speaker_result: Mapped[bool | None] = mapped_column(Boolean)
    framing_result: Mapped[bool | None] = mapped_column(Boolean)
    # The contributor's own unverified description, kept as a string because no measured
    # network test exists. Never counted toward PASSED - see NETWORK_TEST_SUPPORTED.
    network_self_report: Mapped[str | None] = mapped_column(String(40))

    failure_categories: Mapped[list | None] = mapped_column(JSON, default=list)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Populated only if a validity policy is ever configured. No default duration is invented.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())

    required_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    passed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attention_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
