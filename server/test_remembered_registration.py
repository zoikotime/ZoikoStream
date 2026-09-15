"""The credential a remembered viewer presents, and what it is worth.

"Remember me for this event" changes only how long the browser KEEPS the credential that
POST /register already issued. It does not change what that credential is, what proves it, or
what it unlocks — so these pin the properties the remembering relies on:

    bound to ONE event      a pass for Event A does nothing on Event B
    signed                  an edited payload is not a credential
    not the email           the address inside it is data, never the proof

The corresponding viewer-side rule lives in RegistrationGate.test.jsx: a name and an email are
what somebody TYPES, and neither is ever sent back as evidence of having registered.
"""
import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.config import settings
from app.security import ALGORITHM, decode_registration_payload, decode_registration_token


class FakeRegistration:
    """Just the three fields create_registration_token reads."""

    def __init__(self, event_id, email="nani@example.com"):
        self.id = uuid.uuid4()
        self.event_id = event_id
        self.email = email


def mint(registration):
    from app.security import create_registration_token
    return create_registration_token(registration)


# ── bound to one event ─────────────────────────────────────────────────────────────────

def test_a_credential_validates_against_its_own_event():
    event_a = uuid.uuid4()
    token = mint(FakeRegistration(event_a))

    payload = decode_registration_payload(token, event_a)
    assert payload is not None
    assert payload["event_id"] == str(event_a)


def test_the_same_credential_is_worthless_on_another_event():
    """The whole point of remembering per event: Event A remembered must not admit anyone
    to Event B, however the browser stores it."""
    event_a, event_b = uuid.uuid4(), uuid.uuid4()
    token = mint(FakeRegistration(event_a))

    assert decode_registration_payload(token, event_a) is not None
    assert decode_registration_payload(token, event_b) is None


def test_the_event_id_cannot_be_edited_into_a_pass_for_another_event():
    """Re-signing is the attack a remembered credential invites, since it sits in storage
    where its owner can read it. The signature is what refuses."""
    event_a, event_b = uuid.uuid4(), uuid.uuid4()
    original = mint(FakeRegistration(event_a))

    # Decode without verifying, swap the event, re-encode with a key we don't have.
    claims = jwt.get_unverified_claims(original)
    claims["event_id"] = str(event_b)
    forged = jwt.encode(claims, "not-the-server-secret", algorithm=ALGORITHM)

    assert decode_registration_payload(forged, event_b) is None
    assert decode_registration_payload(forged, event_a) is None


def test_a_truncated_or_garbage_credential_is_refused():
    event = uuid.uuid4()
    token = mint(FakeRegistration(event))
    for bad in (token[:-6], token + "x", "not-a-jwt", "", "a.b.c"):
        assert decode_registration_payload(bad, event) is None, bad


def test_an_expired_credential_is_refused():
    """A remembered credential outliving its own expiry would be the one way persistence
    could widen access rather than merely save typing."""
    event = uuid.uuid4()
    reg = FakeRegistration(event)
    expired = jwt.encode(
        {
            "reg": str(reg.id),
            "event_id": str(event),
            "email": reg.email,
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )
    assert decode_registration_payload(expired, event) is None


# ── what is inside it ──────────────────────────────────────────────────────────────────

def test_the_credential_identifies_a_registration_row_not_just_an_address():
    """`reg` is what lets the server look the registration up — and therefore what lets a
    revoked or reassigned row stop working. An email alone could never do that."""
    event = uuid.uuid4()
    reg = FakeRegistration(event)
    payload = decode_registration_payload(mint(reg), event)

    assert payload["reg"] == str(reg.id)
    assert payload["email"] == reg.email
    # The email helper reads the same verified payload — it is never a separate, weaker path.
    assert decode_registration_token(mint(reg), event) == reg.email


def test_an_email_by_itself_is_not_a_credential():
    """Guards the failure this design exists to prevent: remembering a viewer by writing
    their address down and trusting it later."""
    event = uuid.uuid4()
    assert decode_registration_payload("nani@example.com", event) is None
    assert decode_registration_payload(str(event), event) is None
