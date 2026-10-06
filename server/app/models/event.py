import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .organization import Organization
    from .user import User

# ── Event lifecycle: the ONE definition of what Event.status can hold ───────────────────
#
#   draft ──publish──> published ──(start time set)──> scheduled
#     │                    │                              │
#     │                    └──────────┬───────────────────┘
#     │                               v
#     │                     ready_to_arm <──disarm── armed      (Arm needs readiness to pass)
#     │                               └──arm──────────> armed
#     │          published / scheduled / armed ──Go Live──> live <──recovered── degraded
#     │                                                     │  └──media dropped──> degraded
#     │                                                     └──End──> ended <──End── degraded
#     ├──cancel── published / scheduled / ready_to_arm / armed ──> cancelled
#     └──archive── draft / ended / cancelled ──> archived ──unarchive──> (previous status)
#
# Who moves what (crud.event.status_transition_error enforces both halves):
#   * people (PATCH /events/{id}, the archive endpoints): publish, schedule, mark ready,
#     arm/disarm, cancel, archive/unarchive;
#   * the platform only: live (Go Live opens a real broadcast), degraded and its recovery
#     (the media sampler measures the producer), ended (the End teardown stops recording
#     and closes the room). A status write alone can never claim a broadcast.
#
# Retired values, each never written by anything in the product: "rehearsal" (no host
# rehearsal mode exists; planned rehearsals are their own records), "ending" (End is one
# synchronous teardown, live -> ended), "processing" and "replay_ready" (recordings and
# replays have their own lifecycles: LiveRecording.status and ReplayEntitlement, which the
# viewer reads as replay_state), and "blocked" (restriction lives on the Organization,
# ORG-010, and in the readiness verdict, LVE-006, both of which already refuse go-live).
# create_tables.py maps or refuses any row still holding one; ck_events_status keeps them out.
EVENT_STATUSES = (
    "draft", "published", "scheduled", "ready_to_arm", "armed", "live", "degraded",
    "ended", "cancelled", "archived",
)
RETIRED_EVENT_STATUSES = ("rehearsal", "ending", "processing", "replay_ready", "blocked")

# Before a broadcast. Every one can be cancelled; none hands a viewer a stream.
PRE_LIVE_STATUSES = ("draft", "published", "scheduled", "ready_to_arm", "armed")
# A broadcast is running.
ON_AIR_STATUSES = ("live", "degraded")
# Only the platform enters these (see above).
SYSTEM_STATUSES = ("live", "degraded", "ended")
# What can be archived, and so what an unarchive can restore.
ARCHIVABLE_STATUSES = ("draft", "ended", "cancelled")
EVENT_VISIBILITY = ("public", "private", "unlisted")
ASSIGNMENT_ROLES = ("host", "speaker")

# ── Retired: EventAssignment(role="moderator") ─────────────────────────────────────────
# The moderator role was removed. Host was never missing any of its capabilities: an
# assigned host has always resolved to can_moderate AND can_host together
# (services/moderation.resolve_ctx), so retiring moderator granted host nothing new and
# took nothing away from it — the change is purely subtractive.
#
# What it DID take away is the only thing a moderator assignment ever granted on its own:
# can_moderate without can_host. A real database can still hold such rows, written before
# this change, and they are the sole reason those people can open a console at all. Making
# them inert would have silently revoked audience management (chat, Q&A, polls,
# participants) mid-flight on already-published events, so resolve_ctx deliberately keeps
# reading them — as can_moderate ONLY, never can_host.
#
# That asymmetry is the whole point and must not be "tidied" into role="host": broadcast
# control (go live, end, emergency stop, recording) is host-only by design, and a large
# share of these rows are held by users whose platform role is speaker. Promoting them
# would hand the power to end a live broadcast to people explicitly denied it today.
#
# Nothing WRITES this value any more — no endpoint, no socket action, no UI, no fixture.
# It is a read-only grandfather clause with a defined end: run
# retire_moderator_role.py --assignments to list and then clear the rows, and once a
# deployment's count is zero the read in resolve_ctx can be deleted with no behaviour change.
LEGACY_ASSIGNMENT_ROLES = ("moderator",)


class Event(Base):
    """An organization's event (management layer only — no streaming/LiveKit here).
    Belongs to exactly one org; org_id/created_by always come from the JWT. Duration is
    derived from start/end at read time, not stored."""

    __tablename__ = "events"
    # The database refuses any status outside EVENT_STATUSES (create_tables.py adds the same
    # constraint to existing databases after mapping retired values).
    __table_args__ = (
        CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in EVENT_STATUSES) + ")",
                        name="ck_events_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    # Content
    title: Mapped[str | None] = mapped_column(String(200))          # optional as draft; required to publish
    slug: Mapped[str | None] = mapped_column(String(220), index=True)  # unique within org (crud-enforced)
    description: Mapped[str | None] = mapped_column(Text)
    short_description: Mapped[str | None] = mapped_column(String(300))
    banner_image: Mapped[str | None] = mapped_column(String(500))
    thumbnail: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(100))
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    language: Mapped[str | None] = mapped_column(String(40))
    timezone: Mapped[str | None] = mapped_column(String(60))

    # Schedule
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Access / registration
    visibility: Mapped[str] = mapped_column(String(20), default="public", nullable=False)
    registration_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    registration_limit: Mapped[int | None] = mapped_column(Integer)
    # Organizer's own estimate of peak concurrent viewers (doc Sec. 8 "audience qualification
    # band") — distinct from registration_limit, which caps signups, not concurrency. Null
    # means no estimate given; the capacity envelope gate in crud/commercial.py treats that as
    # "assume default band", not as "unlimited".
    expected_audience: Mapped[int | None] = mapped_column(Integer)

    # Feature toggles — stored config the streaming/chat phases will read; behavior not built here.
    waiting_room_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chat_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    qa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    polls_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raise_hand_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_screen_share: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_end_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # The status an archived event returns to on unarchive (one of ARCHIVABLE_STATUSES).
    previous_status: Mapped[str | None] = mapped_column(String(20))
    # Blast-radius class: standard | high | unrepeatable. Decides which readiness gates are
    # mandatory (see services.ops.event_readiness) and what reaches the Command Center's
    # high-impact list. "unrepeatable" = cannot be re-run (memorial, results broadcast).
    impact: Mapped[str] = mapped_column(String(20), default="standard", nullable=False)

    # ── Commercial classification (ZST-LE-COM-001 Section 27) ──────────────────────────
    # Every event is billing-classified from creation, defaulting to "internal" so existing
    # rows and every event created before the commercial layer existed do NOT silently
    # become billable (doc S1: "Prevents old demos or pilots from accidentally becoming
    # billable"). risk_tier/service_profile_id govern readiness gates in crud/commercial.py;
    # they do not affect streaming/moderation behavior built elsewhere in this file.
    billing_classification: Mapped[str] = mapped_column(String(20), default="internal", nullable=False)
    billing_source: Mapped[str] = mapped_column(String(30), default="direct_zoikostream", nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(4), default="r0", nullable=False)
    service_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("service_profiles.id"))
    commercial_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commercial_accounts.id"))

    # -- LVE-003 (ZST-EC-001) ----------------------------------------------------------
    # Communication markers for the approved-event and team lifecycles. They live here
    # rather than in a side table because they describe THIS row's announcement state, and a
    # claim column beside its evidence cannot drift out of step with it.
    approved_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    team_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The last ANNOUNCED team, as a stable signature of (user_id, role) pairs - deliberately
    # not names. A staff display-name edit therefore produces an identical signature and
    # sends nothing, while a genuine add/remove/role-change does not.
    team_signature: Mapped[str | None] = mapped_column(String(500))
    # The last ANNOUNCED team as displayed, so a Team Changed message can show a real
    # "previous" rather than re-deriving one that no longer exists.
    team_display: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped["Organization"] = relationship()
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    assignments: Mapped[list["EventAssignment"]] = relationship(back_populates="event", cascade="all, delete-orphan")

    @property
    def public_watch_url(self) -> str:
        """The attendee link, on the organization's active custom domain when it has one
        (services/public_urls.py is the single builder)."""
        from app.services.public_urls import event_watch_url

        return event_watch_url(self.id, self.organization)


class EventAssignment(Base):
    """Per-event role assignment. One table serves host/speaker — no parallel assignment
    tables. Org membership stays on User.org_id; this only records who fills which event
    role. Assignees must belong to the event's org (enforced in the router).

    `role` holds a value from ASSIGNMENT_ROLES, or — on a pre-existing row only — one from
    LEGACY_ASSIGNMENT_ROLES. Plain VARCHAR with no enum and no CHECK constraint, which is
    why retiring a role needed no schema migration."""

    __tablename__ = "event_assignments"
    __table_args__ = (UniqueConstraint("event_id", "user_id", "role", name="uq_event_user_role"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # host | speaker (legacy: moderator)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship(back_populates="assignments")
    user: Mapped["User"] = relationship()


class EventAccessLink(Base):
    """A revocable, shareable link that admits someone outside the org to a PRIVATE event —
    the link-based counterpart to EventRegistration's email-invite grant. The raw token is
    generated once (create/rotate) and only its sha256 hash is stored; see
    crud.event._hash_link_token. `uses`/`last_used_at` are updated by crud.event.find_access_link,
    the same lookup watch_event uses to accept a `link` query param."""

    __tablename__ = "event_access_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship()


class EventRegistration(Base):
    """A registrant for an event — self-serve (registration_required, `invited_by` NULL) or
    host-initiated (`invited_by` set, see routers/events.py invite_viewers). Either way, no
    User row: registrants are identified only by name/email, and access to the watch page's
    stream is granted via a signed token (see security.py), not login. A row also doubles as
    the access grant that lets a specific outside person into a PRIVATE event — see
    watch_event's visibility gate.

    claim_token_hash/claimed_at: a PRIVATE event's personal invite token is a long-lived
    bearer credential (90 days, see create_registration_token) — anyone who gets the URL,
    forwarded or not, could use it. The first browser to present a valid token for a private
    event "claims" this row (crud.claim_registration); watch_event then requires every later
    request to present the matching claim cookie, so a forwarded link stops working for
    anyone but the device that claimed it first. Only the hash is stored, same pattern as
    EventAccessLink.token_hash."""

    __tablename__ = "event_registrations"
    __table_args__ = (UniqueConstraint("event_id", "email", name="uq_event_registration_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    claim_token_hash: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["Event"] = relationship()
