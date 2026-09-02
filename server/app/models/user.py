import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .organization import Organization

# Phase 1 role set. "org_admin" is the existing slug for the organization admin
# (kept as-is — renaming to organization_admin would ripple through auth, dashboard,
# the frontend, and seeded rows). "host" was added for the streaming modules.
# "billing_admin" (doc ZST-LE-COM-001 Section 25) is a customer-side commercial role
# below org_admin: real commercial-acceptance/change authority, no refund/write-off
# authority, no elevated streaming privileges — deliberately NOT part of the linear
# _ROLE_RANK ladder in security.py, since it isn't "above" or "below" host on any single
# scale. Gated via security.commercial_can(), not require_min_role().
#
# "moderator" was REMOVED: host now covers every capability it had (it always did — an
# assigned host has always resolved to can_moderate AND can_host, see
# services/moderation.resolve_ctx). This column is a plain VARCHAR with no enum and no
# CHECK constraint, so a legacy row carrying "moderator" is still storable and readable;
# it simply matches no ladder rung and no route. See LEGACY_USER_ROLES below and
# retire_moderator_role.py for the one-shot data migration that clears them.
ROLES = ("super_admin", "org_admin", "billing_admin", "host", "speaker", "viewer")

# Retired role slugs that may still exist in `users.role` on a database written before the
# role was removed. Listed so a reader can tell "value we no longer issue" from "value that
# was never valid", and so a display layer can label such a row honestly instead of showing
# it as a generic member (see services/org._role_label). Grants nothing: no rung in
# security._ROLE_RANK, no route, no console capability.
LEGACY_USER_ROLES = ("moderator",)

# Zoiko-internal staff sub-roles (doc Section 25's five staff rows). Meaningful only on a
# super_admin row (see User.staff_commercial_role): unset means "full access, today's
# actual behavior, unchanged"; set narrows that one staff member to exactly what the doc's
# matrix grants that specific role (security.commercial_can). Not a replacement for
# super_admin — a scoping layer under it.
STAFF_COMMERCIAL_ROLES = ("sales", "finance_ops", "live_ops", "support", "security")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )

    full_name: Mapped[str] = mapped_column(String(120), nullable=False)

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    username: Mapped[str] = mapped_column(
        String(60),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        default="viewer",
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # ── Email verification (ZST-EC-001 IDN-001) ────────────────────────────────────────
    # Registration no longer activates an account: a new user is created with
    # email_verified=False and cannot sign in until they redeem the emailed challenge
    # (see models/identity.py, crud/identity.py, routers/auth.py).
    #
    # Distinct from is_active, which an admin toggles to disable an account. A user can be
    # active-but-unverified (just registered) or verified-but-inactive (suspended); login
    # checks both, independently.
    #
    # Existing rows are backfilled to TRUE by create_tables.py — accounts that predate this
    # column were created under the old flow and must not be locked out retroactively. New
    # inserts get FALSE from the ORM default below.
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    # ── IDN-002 "Account ready" send marker ────────────────────────────────────────────
    # Set once, in the same transaction that consumes the verification challenge, by
    # crud.identity.claim_account_ready(). Its only job is duplicate prevention: exactly one
    # caller can transition NULL -> timestamp, so a reused link, a retried request, a double
    # click or a worker restart cannot produce a second "your access is ready" email.
    #
    # NULL on every pre-existing row and never backfilled. That is deliberate — writing a
    # timestamp would assert we sent a message we did not. Those accounts simply have no
    # unconsumed challenge, so nothing can trigger a send for them.
    #
    # Not an audit record: it says "a send was attempted", not "a message was delivered".
    # Real delivery evidence needs the communication record that ZST-EC-001 Section 02
    # requires and this platform does not yet have.
    account_ready_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    # --- Recovery contact (ZST-EC-001 IDN-006 / IDN-007) --------------------------------
    # An alternate address the account holder nominates for recovery. It is only ever
    # honoured once VERIFIED: an unverified value is an attacker-supplied address, and
    # treating it as a recovery destination would turn "set recovery email" into account
    # takeover. `recovery_email_pending` holds the nominated address until the challenge
    # sent to it is redeemed, at which point it is promoted to `recovery_email`.
    recovery_email: Mapped[str | None] = mapped_column(String(255))
    recovery_email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_email_pending: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Operating team (e.g. "Platform Operations", "Live Events Ops"). Labels the actor on
    # the Command Center's privileged-activity feed; NULL falls back to the role label.
    department: Mapped[str | None] = mapped_column(String(80))

    # Scopes a super_admin down to one of STAFF_COMMERCIAL_ROLES for commercial actions
    # specifically (see security.commercial_can) — NULL (the default for every existing
    # account) keeps today's unrestricted behavior. Meaningless on a non-super_admin row.
    staff_commercial_role: Mapped[str | None] = mapped_column(String(20))

    reset_token: Mapped[str | None] = mapped_column(String(64))
    reset_token_expires: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # Soft delete: set on DELETE /organization/users/{id}. Distinct from is_active (which
    # PATCH-status toggles) — a soft-deleted member is excluded from listings and can't be
    # reactivated via a status change. Also flipped is_active=False so their tokens die.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped["Organization"] = relationship(
        back_populates="users",
        foreign_keys=[org_id],
    )