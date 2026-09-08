import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from .organization import Organization
    from .plan import Plan

# ── Subscription lifecycle (ZST-COM-PLAN-001 §12 "Subscription & Plan State Machines") ────
#
# The document's requirement is that commercial state be EXPLICIT: "Commercial state must be
# explicit so UI, API, support and billing never infer lifecycle from a payment event."
#
# Before this, `status` was a bare String(20) with a four-value tuple that nothing read — no
# enum, no CHECK constraint, no transition guard — so `PATCH /admin/subscriptions/{id}` with
# `{"status": "banana"}` persisted silently. The vocabulary and the graph below are transcribed
# from §12 verbatim; no state or edge is invented, and none is omitted.
#
# Spellings are lower_snake_case to match every other state vocabulary in this codebase
# (PAYMENT_STATES, CAPACITY_STATES, COMMERCIAL_LIFECYCLE_STATES). `canceled` follows the
# document's own American English (§00 "Language: American English").
SUBSCRIPTION_STATES = (
    "trial_eligible",
    "trialing",
    "conversion_pending",
    "pending_activation",
    "active",
    "plan_change_scheduled",
    "past_due",
    "suspended",
    "canceled",
    "closed",
    "trial_expired",
)

# Rows written before §12 existed use two spellings that the document renames rather than
# removes. They are TRANSLATED on read/write, never rewritten in place: a destructive UPDATE
# across historical subscriptions is exactly the kind of silent mutation §18 prohibits
# ("Historical subscriptions ... remain bound to their accepted versions"). `active` and
# `past_due` already match, so only these two need mapping.
LEGACY_SUBSCRIPTION_STATES = {
    "trial": "trialing",
    "cancelled": "canceled",
}

# Retained under its original name because `models/__init__.py` re-exports it and the admin
# console reads it; it now yields the canonical §12 vocabulary.
SUBSCRIPTION_STATUSES = SUBSCRIPTION_STATES


def normalize_subscription_state(value: str | None) -> str | None:
    """Canonical §12 state for a stored/submitted value, or None if unrecognised.

    Accepts the legacy spellings above so existing rows and existing callers keep working
    without a data migration.
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    v = LEGACY_SUBSCRIPTION_STATES.get(v, v)
    return v if v in SUBSCRIPTION_STATES else None


# §12's thirteen transitions, transcribed exactly. Two entries deserve note because they are
# one row in the document and two edges here:
#   * "PLAN_CHANGE_SCHEDULED -> ACTIVE(new version)" and "-> ACTIVE(old version)" are both
#     `plan_change_scheduled -> active`; which version applies is a property of the
#     entitlement snapshot, not of the state name.
#   * "ACTIVE/SUSPENDED -> CANCELED" is expanded into its two source states.
SUBSCRIPTION_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "trial_eligible":        ("trialing",),
    "trialing":              ("conversion_pending", "trial_expired"),
    "conversion_pending":    ("active",),
    "trial_expired":         (),
    "pending_activation":    ("active",),
    "active":                ("plan_change_scheduled", "past_due", "canceled"),
    "plan_change_scheduled": ("active",),
    "past_due":              ("suspended",),
    "suspended":             ("active", "canceled"),
    "canceled":              ("closed",),
    "closed":                (),
}


def _with_legacy(*states: str) -> tuple[str, ...]:
    """Add the pre-§12 spelling of any state in the set, so a DB query still matches rows
    written before the vocabulary was canonicalized. Nothing is rewritten in place."""
    out = set(states)
    for legacy, canonical in LEGACY_SUBSCRIPTION_STATES.items():
        if canonical in out:
            out.add(legacy)
    return tuple(sorted(out))


# Grouped state sets, so callers never re-spell a status tuple inline and drift apart. These
# are the two groupings the existing readers actually needed; both are query-safe.
#
# ENTITLED = the tenant may use the platform. `past_due` is included because §12 makes the
# entitlement impact of PAST_DUE a matter of "approved dunning policy" — which is UNDEFINED in
# ZST-COM-PLAN-001 — so this preserves the pre-existing behavior rather than inventing one.
#
# `plan_change_scheduled` is entitled by APPROVED COMMERCIAL DECISION: a tenant with a change
# pending "retains the CURRENT plan's entitlements", "the current plan remains active until
# current_period_end", and access is not removed "merely because a change has been requested".
# It was previously absent, which meant entering the state would have cut a paying customer's
# access mid-period — an unexamined consequence of the state sitting outside this tuple, never
# a documented rule. Note WHICH plan they keep is decided elsewhere: `plan_id` is left alone
# until the effective date, so this tuple grants access and the plan row supplies the limits.
SUBSCRIPTION_ENTITLED_STATES = _with_legacy(
    "active", "trialing", "past_due", "plan_change_scheduled")
# TERMINATED = no longer a live commercial relationship.
SUBSCRIPTION_TERMINATED_STATES = _with_legacy("canceled", "closed")
# REVENUE = counted toward MRR. Deliberately NARROWER than ENTITLED: it excludes `past_due`,
# preserving the exact pre-existing MRR semantics rather than silently changing a finance
# figure. Which states should count is a Finance question this document does not answer.
SUBSCRIPTION_REVENUE_STATES = _with_legacy("active", "trialing")


def subscription_transition_error(current: str | None, new: str | None) -> str | None:
    """None if `current -> new` is a §12-legal transition, else why it is refused.

    Same shape and posture as `crud.commercial.payment_transition_error`: re-asserting the
    current state is a no-op rather than an error (a retried request must not fail), and an
    unknown state on either side is refused rather than guessed at.
    """
    canonical_new = normalize_subscription_state(new)
    if canonical_new is None:
        allowed = ", ".join(SUBSCRIPTION_STATES)
        return f"Unknown subscription state '{new}' (allowed: {allowed})"

    canonical_current = normalize_subscription_state(current)
    if canonical_current is None:
        return (
            f"Subscription is in an unrecognised state '{current}'; it cannot be transitioned "
            "until that is corrected"
        )

    if canonical_current == canonical_new:
        return None

    permitted = SUBSCRIPTION_TRANSITIONS.get(canonical_current, ())
    if canonical_new not in permitted:
        allowed = ", ".join(permitted) if permitted else "nothing (terminal state)"
        return (
            f"Illegal subscription transition {canonical_current} -> {canonical_new}. "
            f"From '{canonical_current}' the only legal next states are: {allowed} "
            "(ZST-COM-PLAN-001 Section 12)"
        )
    return None


class Subscription(Base):
    """An organization's current plan enrollment. One active row per org (enforced in app logic)."""

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), nullable=False)
    # Widened from String(20): "plan_change_scheduled" is 21 characters. Default is the §12
    # entry state for a trial rather than the old "trial" spelling.
    status: Mapped[str] = mapped_column(String(30), default="trialing", nullable=False)
    seats: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Ledger 1 provider references (ZST-COM-PLAN-001 Section 13 "Commerce Adapter") ──────
    #
    # These live HERE, on Subscription, and never on Ledger 2's Payment/EventOrder. Section 18
    # keeps the two ledgers reconcilable but separate, and models/commercial.py's own doctrine
    # states "Ledger 1 stays in subscription.py". A shared provider-reference column would let
    # one ledger's webhook settle the other's balance — the exact cross-ledger settlement the
    # standard prohibits.
    #
    # Section 13 also fixes their AUTHORITY: the Commerce Adapter "never becomes product-state
    # authority". So these are correlation keys for matching an inbound Stripe event to the
    # right tenant — they are not the subscription's state. `status` (the Section 12 state
    # machine) remains the only authority on that.
    stripe_customer_id: Mapped[str | None] = mapped_column(String(120), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)
    # The Checkout Session that started the purchase. A hosted session has no subscription id
    # until the payer completes it, so this is the only correlation key available in between —
    # the same problem Ledger 2 solved with Payment.checkout_session_ref.
    checkout_session_ref: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)

    # Which published cadence this subscription is billed on ("monthly" | "annual"), recorded
    # when a checkout is started. NULLABLE and it must stay that way: every row written before
    # this column existed has no cadence, and guessing one would misstate what a tenant is
    # paying. NULL therefore means "not recorded", never "monthly".
    #
    # This is a RECORD of a fact the request already carried, not a second pricing authority —
    # the amount still comes only from the Stripe Price. It exists because the cadence was
    # resolved to a Price ID and then discarded, so nothing downstream could answer "what is
    # this tenant on?": Section 16 requires the console to show the current interval, and any
    # future plan-change logic has to know the cadence it is changing FROM.
    billing_interval: Mapped[str | None] = mapped_column(String(16))

    # ── Scheduled plan change (Section 12 PLAN_CHANGE_SCHEDULED) ──────────────────────────
    #
    # The smallest set that can represent "this tenant has asked to move to plan P on cadence
    # C, effective D" — three columns, each load-bearing:
    #
    #   pending_plan_id           WHAT they are moving to. Nullable FK, not a slug, so the
    #                             target cannot drift if a plan is renamed mid-schedule.
    #   pending_billing_interval  WHICH cadence. A plan change and a cadence change are the
    #                             same operation here, so one row covers monthly<->annual too.
    #   plan_change_effective_at  WHEN. Copied from `current_period_end` at request time rather
    #                             than re-derived later: it is the date the customer was shown
    #                             and agreed to, and a renewal that moves current_period_end
    #                             must not silently move the promise made to them.
    #
    # All three are NULL together or set together — that pairing IS the "is a change pending?"
    # flag, so no separate boolean can drift out of step with them.
    #
    # Deliberately NOT stored: the Stripe Price ID. It is re-resolved from approved
    # configuration when the change is applied, so a price an operator has since withdrawn
    # fails closed at that moment instead of being charged from a stale copy.
    pending_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    pending_billing_interval: Mapped[str | None] = mapped_column(String(16))
    plan_change_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="subscriptions")
    # `foreign_keys` is REQUIRED, not stylistic: `pending_plan_id` is a second foreign key to
    # plans.id, so SQLAlchemy can no longer infer which one this relationship joins on and
    # raises AmbiguousForeignKeysError at query time. `plan` is always the CURRENT plan — the
    # one being billed and entitled — and never the pending one.
    plan: Mapped["Plan"] = relationship(back_populates="subscriptions", foreign_keys=[plan_id])
    # The scheduled target, when one is pending. Read-only convenience with no back_populates:
    # a Plan does not need a collection of subscriptions merely scheduled to move onto it.
    pending_plan: Mapped["Plan | None"] = relationship(foreign_keys=[pending_plan_id],
                                                        viewonly=True)
