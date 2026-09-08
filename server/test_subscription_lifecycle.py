"""Platform subscription lifecycle — ZST-COM-PLAN-001 Section 12 "Subscription & Plan State
Machines".

The document's requirement: "Commercial state must be explicit so UI, API, support and billing
never infer lifecycle from a payment event." Before this, `Subscription.status` was a bare
String(20) whose four-value tuple nothing read — no enum, no CHECK constraint, no transition
guard — so `PATCH /admin/subscriptions/{id}` with `{"status": "banana"}` persisted silently and
`canceled -> active` was a legal write.

Every state and edge asserted here is transcribed from Section 12. Nothing is invented: no
dunning policy (the document defers PAST_DUE's entitlement impact to an "approved dunning
policy" that is UNDEFINED here), no trial duration, no pricing.

Pure logic — no database needed for the state machine itself.
"""
import uuid
from types import SimpleNamespace

import pytest

from app.crud import admin as admin_crud
from app.models import (
    LEGACY_SUBSCRIPTION_STATES,
    SUBSCRIPTION_ENTITLED_STATES,
    SUBSCRIPTION_REVENUE_STATES,
    SUBSCRIPTION_STATES,
    SUBSCRIPTION_TERMINATED_STATES,
    SUBSCRIPTION_TRANSITIONS,
    Subscription,
    normalize_subscription_state,
    subscription_transition_error,
)


# ── Section 12 vocabulary ─────────────────────────────────────────────────────────────

# The eleven states named in the document's transition table, verbatim (lower_snake_cased to
# match every other state vocabulary in this codebase).
_DOC_STATES = {
    "trial_eligible", "trialing", "conversion_pending", "pending_activation", "active",
    "plan_change_scheduled", "past_due", "suspended", "canceled", "closed", "trial_expired",
}


def test_all_eleven_documented_states_exist():
    assert set(SUBSCRIPTION_STATES) == _DOC_STATES


def test_no_undocumented_state_was_invented():
    """A state not in Section 12 would be a business rule this code has no authority to add."""
    assert not (set(SUBSCRIPTION_STATES) - _DOC_STATES)


def test_every_state_has_a_transition_entry():
    """A state missing from the graph would silently permit nothing and be undebuggable."""
    assert set(SUBSCRIPTION_TRANSITIONS) == set(SUBSCRIPTION_STATES)


def test_transition_targets_are_all_real_states():
    for source, targets in SUBSCRIPTION_TRANSITIONS.items():
        for t in targets:
            assert t in SUBSCRIPTION_STATES, f"{source} -> {t} is not a declared state"


# ── The thirteen documented transitions ───────────────────────────────────────────────
# Section 12's table, one case per row. Two rows expand to two edges each:
#   "PLAN_CHANGE_SCHEDULED -> ACTIVE(new version)" / "-> ACTIVE(old version)" are both
#   plan_change_scheduled -> active; "ACTIVE/SUSPENDED -> CANCELED" is two source states.
@pytest.mark.parametrize("current,new", [
    ("trial_eligible", "trialing"),
    ("trialing", "conversion_pending"),
    ("conversion_pending", "active"),
    ("trialing", "trial_expired"),
    ("pending_activation", "active"),
    ("active", "plan_change_scheduled"),
    ("plan_change_scheduled", "active"),
    ("active", "past_due"),
    ("past_due", "suspended"),
    ("suspended", "active"),
    ("active", "canceled"),
    ("suspended", "canceled"),
    ("canceled", "closed"),
])
def test_documented_transitions_are_permitted(current, new):
    assert subscription_transition_error(current, new) is None


@pytest.mark.parametrize("current,new", [
    # The defect this exists to prevent: a terminated subscription silently reactivating.
    ("canceled", "active"),
    ("canceled", "trialing"),
    ("closed", "active"),
    # Trial rules: an expired trial is terminal, and a paid customer cannot re-enter a trial
    # ("Paid customers cannot 'downgrade' back into a trial", Section 09).
    ("trial_expired", "trialing"),
    ("trial_expired", "active"),
    ("active", "trialing"),
    # Skipping the conversion step entirely.
    ("trialing", "active"),
    # Suspension must come through past_due, not straight from active.
    ("active", "suspended"),
    # A scheduled change resolves to active, never straight to a terminal state.
    ("plan_change_scheduled", "canceled"),
])
def test_undocumented_transitions_are_refused(current, new):
    error = subscription_transition_error(current, new)
    assert error is not None
    assert "Illegal subscription transition" in error


def test_terminal_states_permit_nothing_onward():
    for terminal in ("closed", "trial_expired"):
        assert SUBSCRIPTION_TRANSITIONS[terminal] == ()


def test_re_asserting_the_same_state_is_a_no_op_not_an_error():
    """A retried request must not fail — same posture as the payment state machine."""
    for state in SUBSCRIPTION_STATES:
        assert subscription_transition_error(state, state) is None


def test_unknown_target_state_is_refused():
    error = subscription_transition_error("active", "banana")
    assert error is not None and "Unknown subscription state" in error


def test_unknown_current_state_is_refused_not_guessed():
    error = subscription_transition_error("something_stale", "active")
    assert error is not None and "unrecognised state" in error


# ── Legacy spellings ──────────────────────────────────────────────────────────────────

def test_legacy_spellings_normalize_to_canonical_states():
    assert normalize_subscription_state("trial") == "trialing"
    assert normalize_subscription_state("cancelled") == "canceled"


def test_legacy_rows_can_still_transition():
    """Existing rows are NOT rewritten by a migration, so the machine must accept their
    spelling on the way in."""
    assert subscription_transition_error("trial", "conversion_pending") is None
    assert subscription_transition_error("cancelled", "closed") is None


def test_normalization_is_case_and_whitespace_insensitive():
    assert normalize_subscription_state("  ACTIVE  ") == "active"


def test_unknown_value_normalizes_to_none_rather_than_a_guess():
    assert normalize_subscription_state("banana") is None
    assert normalize_subscription_state(None) is None


def test_every_legacy_alias_maps_to_a_real_state():
    for legacy, canonical in LEGACY_SUBSCRIPTION_STATES.items():
        assert canonical in SUBSCRIPTION_STATES
        assert legacy not in SUBSCRIPTION_STATES, f"{legacy} should be an alias, not a state"


# ── Grouped state sets ────────────────────────────────────────────────────────────────

def test_state_groups_include_their_legacy_spellings():
    """A query built from these groups must still match pre-Section-12 rows."""
    assert "trial" in SUBSCRIPTION_ENTITLED_STATES
    assert "cancelled" in SUBSCRIPTION_TERMINATED_STATES


def test_revenue_states_are_narrower_than_entitled_states():
    """MRR deliberately excludes past_due; widening it would silently change a finance figure
    on a question this document does not answer."""
    assert "past_due" in SUBSCRIPTION_ENTITLED_STATES
    assert "past_due" not in SUBSCRIPTION_REVENUE_STATES


def test_entitled_and_terminated_are_disjoint():
    assert not (set(SUBSCRIPTION_ENTITLED_STATES) & set(SUBSCRIPTION_TERMINATED_STATES))


# ── Enforcement at the write path ─────────────────────────────────────────────────────

class _Stub:
    def commit(self):
        pass

    def refresh(self, o):
        pass


def _sub(status):
    return Subscription(id=uuid.uuid4(), org_id=uuid.uuid4(), plan_id=uuid.uuid4(),
                        status=status, seats=1)


def _payload(**kw):
    fields = dict(status=None, seats=None, current_period_end=None, plan_slug=None)
    fields.update(kw)          # merge, not duplicate-kwarg
    return SimpleNamespace(**fields)


def test_update_subscription_refuses_an_unknown_status():
    """THE regression: `PATCH {"status": "banana"}` used to persist verbatim."""
    sub = _sub("active")
    with pytest.raises(ValueError, match="Unknown subscription state"):
        admin_crud.update_subscription(_Stub(), sub, _payload(status="banana"))
    assert sub.status == "active", "a refused update must not mutate the row"


def test_update_subscription_refuses_an_illegal_transition():
    sub = _sub("canceled")
    with pytest.raises(ValueError, match="Illegal subscription transition"):
        admin_crud.update_subscription(_Stub(), sub, _payload(status="active"))
    assert sub.status == "canceled"


def test_update_subscription_allows_a_documented_transition():
    sub = _sub("active")
    admin_crud.update_subscription(_Stub(), sub, _payload(status="past_due"))
    assert sub.status == "past_due"


def test_update_subscription_normalizes_a_legacy_input_spelling():
    sub = _sub("trialing")
    admin_crud.update_subscription(_Stub(), sub, _payload(status="conversion_pending"))
    assert sub.status == "conversion_pending"


def test_a_legacy_row_is_stored_canonically_after_a_transition():
    """Rows are not migrated in bulk, but a row that MOVES is written in the canonical
    spelling — so the vocabulary converges over time without a destructive UPDATE."""
    sub = _sub("trial")                       # pre-Section-12 spelling on the row
    admin_crud.update_subscription(_Stub(), sub, _payload(status="trial_expired"))
    assert sub.status == "trial_expired"


def test_seats_can_be_updated_without_touching_status():
    sub = _sub("active")
    admin_crud.update_subscription(_Stub(), sub, _payload(seats=5))
    assert sub.seats == 5 and sub.status == "active"


# ── Column width ──────────────────────────────────────────────────────────────────────

def test_status_column_fits_the_longest_documented_state():
    """`plan_change_scheduled` is 21 chars; the column was VARCHAR(20) and would have
    truncated or rejected it."""
    width = Subscription.__table__.c.status.type.length
    assert width >= max(len(s) for s in SUBSCRIPTION_STATES)
