"""Envelope encryption, keyed hashing and user-secret redaction (Security/Privacy §9, §11)."""
import pytest
from cryptography.exceptions import InvalidTag

from app.guide import crypto
from app.guide.secrets_detect import detect


def test_encrypt_round_trip_and_no_plaintext_in_blob():
    blob = crypto.encrypt("jane@example.com", context="guide_lead_field_values.value")
    assert b"jane@example.com" not in blob
    assert crypto.decrypt(blob, context="guide_lead_field_values.value") == "jane@example.com"


def test_ciphertext_is_bound_to_its_column():
    blob = crypto.encrypt("hello", context="guide_messages.content")
    with pytest.raises(InvalidTag):
        crypto.decrypt(blob, context="guide_feedback.comment")


def test_tampered_ciphertext_fails():
    blob = bytearray(crypto.encrypt("hello", context="c"))
    blob[-1] ^= 1
    with pytest.raises(InvalidTag):
        crypto.decrypt(bytes(blob), context="c")


def test_each_encryption_uses_a_fresh_key_and_nonce():
    assert crypto.encrypt("same", context="c") != crypto.encrypt("same", context="c")


def test_keyed_hash_is_domain_separated():
    v = "token-value"
    assert crypto.keyed_hash(v, purpose="session-capability") != \
        crypto.keyed_hash(v, purpose="session-csrf")
    assert crypto.keyed_hash(v, purpose="x") == crypto.keyed_hash(v, purpose="x")


@pytest.mark.parametrize("text,cls", [
    ("my pin is 4821", "event_pin"),
    ("PIN: 993311", "event_pin"),
    ("the access code is ZK-7781", "event_pin"),
    ("password: hunter22", "password"),
    ("my password is Tr0ub4dor&3", "password"),
    ("card 4111 1111 1111 1111 exp 12/29", "payment_card"),
    ("use eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9P",
     "access_token"),
    ("sk_live_51HxYzAbCdEfGhIjKl", "access_token"),
    ("https://zoikostream.com/events/abc/watch?reg=8f7d6c5b4a39281", "secret_link"),
    ("https://zoikostream.com/deliveries/9f8e7d6c5b4a", "secret_link"),
])
def test_secrets_are_detected_and_not_echoed(text, cls):
    d = detect(text)
    assert cls in d.classes
    for fragment in ("4821", "993311", "7781", "hunter22", "Tr0ub4dor", "4111 1111",
                     "dozjgNry", "51HxYz", "8f7d6c5b4a39281", "9f8e7d6c5b4a"):
        assert fragment not in d.text


@pytest.mark.parametrize("text", [
    "I can't get into my event, the PIN isn't working",
    "where do I enter the PIN code for my event?",
    "my password is not working",
    "I forgot my password",
    "Can ZoikoStream stream a memorial service for 300 people?",
    "Is the event at 7:30 PM on 10/12/2026?",
    "Our phone number is 555 123 4567",
    "See https://zoikostream.com/pricing for plans",
])
def test_ordinary_text_is_left_alone(text):
    d = detect(text)
    assert not d.suspected, d.classes
    assert d.text == text
