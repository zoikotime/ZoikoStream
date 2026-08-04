import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

from .event import ASSIGNMENT_ROLES

if TYPE_CHECKING:
    from .event import Event
    from .organization import Organization
    from .user import User

# ── Lifecycle ─────────────────────────────────────────────────────────────────
# `status` is the lifecycle and the ONLY authority. DELIVERY is deliberately NOT a status:
# an emailed invitation and a hand-delivered one are both `pending`, and the invitee clicking
# their link must work in exactly one state rather than three. Delivery facts live in
# sent_at / delivered_at / send_error, which nothing branches on.
#
# `rejected` is the stored spelling of what the product calls "Declined" — kept as-is so
# existing rows need no migration; the label is applied once, at the API boundary.
INVITATION_STATUSES = ("pending", "accepted", "expired", "cancelled", "rejected", "revoked")

# The one OPEN state: the token is live and redeemable. Every duplicate check, the token
# resolver and the partial unique indexes all read from this, so they cannot drift.
OPEN_STATUSES = ("pending",)

# States a resend may reopen. `accepted`, `rejected` and `revoked` are terminal on purpose:
# re-asking someone who declined, or re-admitting someone whose access was revoked, must
# create a NEW row so the decline/revocation survives as history instead of being erased.
RESENDABLE_STATUSES = ("pending", "expired", "cancelled")

# Guard rail on retries — a row that has been resent this many times is a conversation to
# have out of band, not a button to keep pressing.
MAX_RESENDS = 5

# ── Who may grant what ───────────────────────────────────────────────────────
# A rank ladder CANNOT express these rules: security._ROLE_RANK puts moderator(2) below
# host(3), so "never grant above your own rank" would happily let a host invite a moderator —
# the exact thing the spec forbids. So the permitted set is written out per inviter role.
#
# Only org_admin can reach the invitation endpoints today (require_org_admin). These tables
# are the enforcement point, so the day host/moderator gain invite rights the limits are
# already correct rather than needing to be remembered.

ORG_ASSIGNABLE_ROLES = ("org_admin", "host", "moderator", "speaker", "viewer")

# PLATFORM roles (users.role) an inviter may grant.
INVITE_PLATFORM_ROLES: dict[str, tuple[str, ...]] = {
    "super_admin": ORG_ASSIGNABLE_ROLES,
    "org_admin": ORG_ASSIGNABLE_ROLES,
    # A host or moderator may bring in people to appear on their event, never staff who
    # could run it. Note "moderator" is absent from the host row and "host" from both.
    "host": ("speaker", "viewer"),
    "moderator": ("speaker", "viewer"),
}

# EVENT roles (event_assignments.role) an inviter may grant.
INVITE_EVENT_ROLES: dict[str, tuple[str, ...]] = {
    "super_admin": ASSIGNMENT_ROLES,
    "org_admin": ASSIGNMENT_ROLES,
    "host": ("speaker", "panelist", "cohost"),
    "moderator": ("speaker", "panelist"),
}

# Default PLATFORM role suggested for each event role. Deliberately conservative: an event
# role never silently inflates the platform role, because a platform role outlives the
# assignment (revoking the assignment cannot un-grant it) and applies to every OTHER event in
# the org. cohost/producer/panelist carry no broadcast authority (see models/event.py), so
# they default to `speaker` — an admin who wants more must choose it explicitly on the form,
# where it is visible and lands in the audit row.
DEFAULT_PLATFORM_ROLE_FOR_EVENT_ROLE: dict[str, str] = {
    "host": "host",
    "moderator": "moderator",
    "speaker": "speaker",
    "panelist": "speaker",
    "cohost": "speaker",
    "producer": "speaker",
}


class Invitation(Base):
    """A pending offer of access. The raw token is emailed to the invitee and NEVER stored —
    only its sha256 hash lives here (token_hash), looked up on preview/accept/decline.

    Two shapes, one table:
      * event_id IS NULL     — join the organization with a platform role
      * event_id IS NOT NULL — join the organization AND fill one event role
                               (an EventAssignment is created on accept)

    A parallel `event_invitations` table would have needed its own copy of the token hashing,
    the unique token_hash index, the expiry clock, rotation-on-resend and the whole accept
    path — five things that must never diverge.

    Duplicate prevention is a pair of PARTIAL UNIQUE INDEXES over the open state (see
    create_tables.py). The application's pre-check exists only to produce a friendly message;
    the index is what actually holds under concurrency.
    """

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Platform role the account gets. For an event invitation this is a CEILING — the
    # EventAssignment below is what actually grants event powers (services/moderation.py).
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ── Event scope ──────────────────────────────────────────────────────────
    # NULL for an org-membership invitation. The FK does not (and cannot) protect against a
    # SOFT-deleted event, so every read path re-resolves the event org-scoped instead of
    # trusting this id — see routers/organization.py.
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    event_role: Mapped[str | None] = mapped_column(String(20))     # see ASSIGNMENT_ROLES

    # Optional note from the inviter, shown in the email. Escaped at render time.
    message: Mapped[str | None] = mapped_column(Text)

    # ── Delivery (facts, never lifecycle) ────────────────────────────────────
    # sent_at IS NULL distinguishes a MANUAL invitation (link handed over out of band) from a
    # mailed one — which is exactly why delivery is not a status.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Written only by the Resend webhook, which is signature-verified and matches on
    # provider_message_id alone.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_error: Mapped[str | None] = mapped_column(Text)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resend_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(120), index=True)

    # ── Transition timestamps ────────────────────────────────────────────────
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Soft delete: "Delete expired" HIDES rows. A hard delete would erase who invited whom
    # at what role with no trace, and the partial unique indexes exclude deleted rows so a
    # hidden row never blocks re-inviting.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped["Organization"] = relationship()
    inviter: Mapped["User | None"] = relationship(foreign_keys=[invited_by_id])
    event: Mapped["Event | None"] = relationship(foreign_keys=[event_id])


# ── Transition guard ──────────────────────────────────────────────────────────
# Same shape as crud.event.status_transition_error: ONE pure function, reused by every
# caller (single actions, bulk actions, the accept/decline token paths), so adding a rule
# closes it everywhere at once.

_ALLOWED: dict[str, tuple[str, ...]] = {
    "pending": ("accepted", "rejected", "expired", "cancelled"),
    # Terminal except for revoke, which withdraws access AFTER acceptance.
    "accepted": ("revoked",),
    # Reopenable: resend issues a new token and a new clock. This is the most common real
    # resend and is precisely what reusing the row is for.
    "expired": ("pending", "cancelled"),
    "cancelled": ("pending",),
    # The invitee answered. Re-asking creates a NEW row so "we asked three times" stays
    # visible rather than being overwritten.
    "rejected": (),
    # Offboarded. Silently re-issuing a token to a removed person is the worst failure in
    # this table; re-onboarding is a new row.
    "revoked": (),
}


def invitation_transition_error(current: str, new: str, *, has_event: bool = False,
                                allow_noop: bool = True) -> str | None:
    """Return an error message if current -> new is not allowed, else None.

    `allow_noop` exists because the same table answers two different questions. Applying a
    transition treats current == new as a harmless no-op (an admin clicking cancel twice is not
    an error). But asking "may this row be cancelled?" must NOT say yes just because it is
    already cancelled — that made the console offer Cancel on a cancelled row and turned a
    re-cancel into a silent 200. The can_* flags pass allow_noop=False.
    """
    if new == current:
        return None if allow_noop else f"This invitation is already {_LABELS.get(new, new).lower()}"
    if current not in _ALLOWED:
        return f"Unknown invitation status '{current}'"
    if new not in INVITATION_STATUSES:
        return f"Unknown invitation status '{new}'"
    if new not in _ALLOWED[current]:
        return f"An invitation that is {_LABELS.get(current, current).lower()} cannot be {_LABELS.get(new, new).lower()}"
    # Revoke's only effect is removing the EventAssignment. For an org-membership invitation
    # there is no assignment to remove, and soft-deleting the User here would bypass the
    # self-deletion and super-admin guards on DELETE /organization/users/{id}.
    if new == "revoked" and not has_event:
        return "Remove the person from Members & Access instead — this invitation has no event role"
    return None


# Display labels. `rejected` -> "Declined" is applied here, once, so no client re-derives it.
_LABELS = {
    "pending": "Pending",
    "accepted": "Accepted",
    "expired": "Expired",
    "cancelled": "Cancelled",
    "rejected": "Declined",
    "revoked": "Revoked",
}


def status_label(status: str) -> str:
    return _LABELS.get(status, (status or "").title())
