"""Registering for an event with a NAME and nothing else.

The viewer gate used to demand a name and an email address. It now asks for the name only,
so `POST /events/{id}/register` has to accept a body without `email` — while every other
caller of that same endpoint (the identify-yourself form, host-issued invites) keeps sending
one and keeps behaving exactly as before.

WHAT IS DELIBERATELY NOT CHANGED, and why these tests exist to say so:

`event_registrations.email` is still NOT NULL and still carries UNIQUE(event_id, email).
Relaxing the column would have needed a migration AND would have destroyed the idempotency
that key provides, because Postgres permits unlimited NULLs in a unique index — every reload
would have written another row. So a name-only registration is given a placeholder address
in the reserved `.invalid` TLD (RFC 2606), which has no DNS and therefore cannot be delivered
to, and the confirmation email is skipped rather than aimed at it.
"""
import pytest
from pydantic import ValidationError

from _testsupport import code_only

from app.schemas.event import RegistrationCreate


# ── the request contract ──────────────────────────────────────────────────────────────────

def test_a_name_alone_is_a_valid_registration():
    data = RegistrationCreate(name="Jane Doe")
    assert data.name == "Jane Doe"
    assert data.email is None, "absent means absent — not an empty string to be stored"


def test_an_email_is_still_accepted_from_the_callers_that_send_one():
    """The identify-yourself form and the invite path are untouched by this change."""
    data = RegistrationCreate(name="Jane Doe", email="jane@company.com")
    assert data.email == "jane@company.com"


@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_an_empty_name_is_still_refused(bad):
    """The name is the only thing left being asked for, so it is the only thing that can be
    wrong. min_length rejects "", and the whitespace cases must not slip through as a
    registration for somebody called " "."""
    with pytest.raises(ValidationError):
        RegistrationCreate(name=bad)


def test_a_malformed_email_is_still_refused_when_one_is_given():
    """Optional does not mean unvalidated: a caller that sends an address must send a real
    one, or the confirmation mail below would be aimed at nothing."""
    with pytest.raises(ValidationError):
        RegistrationCreate(name="Jane Doe", email="not-an-address")


# ── what the route does with a name-only body ─────────────────────────────────────────────

def test_the_placeholder_address_cannot_be_delivered_to():
    """`.invalid` is reserved by RFC 2606 and has no DNS, so a placeholder can never be
    routed to a real mailbox — and reads as obviously synthetic in an export."""
    from app.routers import events
    src = code_only(events.register_for_event)
    assert "no-email.invalid" in src, "the placeholder must sit in a non-routable TLD"
    assert "@example.com" not in src and "@zoikostream.com" not in src, \
        "a placeholder must never be spelled with a domain that could actually accept mail"


def test_the_placeholder_is_unique_per_registration_not_derived_from_the_name():
    """Deriving it from the name would collide two different people called Jane Doe on
    UNIQUE(event_id, email) — and the second would be handed the FIRST one's registration row
    and access token. Duplicate rows are the lesser evil by a wide margin."""
    from app.routers import events
    src = code_only(events.register_for_event)
    assert "uuid.uuid4()" in src, "the placeholder must be random per registration"
    # The name must not be part of how the address is built.
    placeholder_line = next(
        line for line in src.splitlines() if "no-email.invalid" in line
    )
    assert "data.name" not in placeholder_line and "name" not in placeholder_line.split("=")[0]


def test_no_confirmation_mail_is_aimed_at_a_placeholder():
    """Every one of those would bounce, which costs sender reputation and buries real
    delivery failures."""
    from app.routers import events
    src = code_only(events.register_for_event)
    assert "if not anonymous:" in src, \
        "the confirmation send must be conditional on a real address existing"
    send_at = src.index("send_registration_confirmation_email")
    guard_at = src.rindex("if not anonymous:", 0, send_at)
    assert guard_at < send_at, "the guard must precede the send"


def test_idempotency_is_only_claimed_where_it_can_hold():
    """get_registration matches on the address. A placeholder is unique by construction, so
    looking one up would always miss — and running the lookup anyway would imply a
    de-duplication guarantee that does not exist for anonymous viewers."""
    from app.routers import events
    src = code_only(events.register_for_event)
    lookup_at = src.index("crud.get_registration")
    guard_at = src.rindex("if not anonymous:", 0, lookup_at)
    assert guard_at < lookup_at


def test_the_column_is_not_relaxed_and_no_migration_is_introduced():
    """The whole point of the placeholder: the schema on disk is untouched."""
    from app.models.event import EventRegistration
    email_col = EventRegistration.__table__.columns["email"]
    assert email_col.nullable is False, "email must stay NOT NULL"
    constraints = {c.name for c in EventRegistration.__table__.constraints}
    assert "uq_event_registration_email" in constraints, \
        "the per-event uniqueness that makes re-submission idempotent must survive"


def test_private_events_are_still_closed_to_self_serve_registration():
    """Name-only must not become an easier side door than name+email was."""
    from app.routers import events
    src = code_only(events.register_for_event)
    # `code_only` round-trips through ast.unparse, which normalises string quoting — so the
    # comparison is asserted without committing to which quote character survives.
    assert "ev.visibility ==" in src and "private" in src
    assert "HTTP_403_FORBIDDEN" in src
