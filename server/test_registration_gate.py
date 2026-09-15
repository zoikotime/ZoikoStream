"""Who has to say who they are before an event will stream to them.

── THE BUG ─────────────────────────────────────────────────────────────────────────────
The gate read `not ev.registration_required`. That column defaults to False and the Create
Event form stopped sending it, so every event made since carried False — the condition
short-circuited and a first-time visitor in a fresh browser walked straight into a public
event's stream. The column was not wrong; it simply had no remaining way to be turned on.

The rule is now derived at request time from what the event IS: a link-shareable event
(public or unlisted) asks who is watching. No stored row is rewritten, and an explicit
registration_required=True is still honoured wherever it was already set.
"""
import pytest

from app.routers.events import registration_gate_required


class FakeEvent:
    def __init__(self, visibility, registration_required=False):
        self.visibility = visibility
        self.registration_required = registration_required


# ── the link-shareable cases: the gate is on regardless of the column ──────────────────

@pytest.mark.parametrize("visibility", ["public", "unlisted"])
def test_a_shareable_event_asks_who_is_watching(visibility):
    """The reported bug, directly: this is exactly the row a newly created public event has,
    and it used to mean 'no gate'."""
    assert registration_gate_required(FakeEvent(visibility, registration_required=False)) is True


@pytest.mark.parametrize("visibility", ["public", "unlisted"])
def test_an_explicit_true_is_still_honoured(visibility):
    assert registration_gate_required(FakeEvent(visibility, registration_required=True)) is True


# ── private stays as it was ────────────────────────────────────────────────────────────

def test_a_private_event_is_not_given_a_name_and_email_form():
    """It is already gated, and by something stronger: an invite token, an access link, or
    org membership. A form in front of that would ask an already-authorized guest to
    re-identify with data nothing checks."""
    assert registration_gate_required(FakeEvent("private", registration_required=False)) is False


def test_a_private_event_that_asked_for_registration_still_gets_it():
    """Whatever an operator already set stays true — the rule only ADDS the shareable cases,
    it never overrides a stored intent."""
    assert registration_gate_required(FakeEvent("private", registration_required=True)) is True


# ── the shape of the answer ────────────────────────────────────────────────────────────

def test_the_gate_is_a_real_boolean():
    """It lands in WatchOut.registration_required, which is typed bool — a truthy column
    value leaking through as something else would fail serialization at the edge."""
    for ev in (FakeEvent("public"), FakeEvent("private"), FakeEvent("unlisted", True)):
        assert isinstance(registration_gate_required(ev), bool)


def test_nothing_about_the_event_row_is_mutated():
    """Policy, not a migration. The reason this is computed per request rather than written
    once is that nobody's data should change underneath them."""
    ev = FakeEvent("public", registration_required=False)
    registration_gate_required(ev)
    assert ev.registration_required is False
    assert ev.visibility == "public"


def test_an_unknown_visibility_is_treated_as_private_rather_than_open():
    """Fail closed: an unrecognised value must not accidentally select the 'shareable'
    branch and start handing out forms — or, worse, be assumed already-gated elsewhere."""
    assert registration_gate_required(FakeEvent("something-new")) is False
