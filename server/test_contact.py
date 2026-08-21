"""Public contact endpoint — POST /api/contact

Replaces a frontend mail-protocol link, so the properties that matter are: the destination is
ours and not the caller's, every field is bounded, header injection is refused, and no internal
mail configuration leaks into a response.

The email service is patched in every test — nothing here sends real mail.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main as main_app
from app import ratelimit
from app.config import settings

VALID = {
    "first": "Ada",
    "last": "Lovelace",
    "email": "ada@example.com",
    "org": "Analytical Engines",
    "country": "United Kingdom",
    "topic": "Procurement, licensing & commercial terms",
    "message": "We would like to move to the Pro plan.",
}


@pytest.fixture
def client():
    """Fresh rate-limit budget per test: the limiter is per-IP process state, so without this
    the fourth test in a file would start seeing 429s from the third."""
    ratelimit._HITS.clear()
    with TestClient(main_app.app) as c:
        yield c
    ratelimit._HITS.clear()


@pytest.fixture
def sender():
    with patch("app.routers.contact.send_contact_message_email") as m:
        yield m


# ── happy path ────────────────────────────────────────────────────────────────────────────

def test_valid_submission_is_accepted(client, sender):
    r = client.post("/api/contact", json=VALID)
    assert r.status_code == 202, r.text
    assert r.json() == {"received": True}


def test_valid_submission_reaches_the_email_service(client, sender):
    client.post("/api/contact", json=VALID)
    assert sender.call_count == 1
    kwargs = sender.call_args.kwargs
    assert kwargs["email"] == "ada@example.com"
    assert kwargs["message"] == VALID["message"]


def test_the_caller_never_supplies_a_destination(client, sender):
    """The whole point: no argument handed to the mail service names a recipient."""
    client.post("/api/contact", json=VALID)
    kwargs = sender.call_args.kwargs
    assert set(kwargs) == {"first", "last", "email", "org", "country", "topic", "message"}
    for forbidden in ("to", "recipient", "bcc", "cc", "from_", "smtp_host"):
        assert forbidden not in kwargs


def test_organization_is_optional(client, sender):
    r = client.post("/api/contact", json={**VALID, "org": ""})
    assert r.status_code == 202


def test_response_leaks_no_mail_configuration(client, sender):
    r = client.post("/api/contact", json=VALID)
    body = r.text.lower()
    for leak in ("resend", "smtp", "api_key", "apikey", "@zoikostream.com",
                 settings.CONTACT_EMAIL.lower()):
        assert leak not in body, f"response leaked {leak!r}"


# ── required fields ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["first", "last", "email", "country", "topic", "message"])
def test_missing_required_field_is_refused(client, sender, field):
    r = client.post("/api/contact", json={**VALID, field: ""})
    assert r.status_code == 422
    assert sender.call_count == 0


@pytest.mark.parametrize("field", ["first", "last", "country", "topic", "message"])
def test_whitespace_only_field_is_refused(client, sender, field):
    r = client.post("/api/contact", json={**VALID, field: "     "})
    assert r.status_code == 422
    assert sender.call_count == 0


def test_absent_key_is_refused(client, sender):
    payload = {k: v for k, v in VALID.items() if k != "email"}
    assert client.post("/api/contact", json=payload).status_code == 422
    assert sender.call_count == 0


# ── malformed input ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["not-an-email", "a@b", "@example.com", "ada@", "ada example.com"])
def test_malformed_email_is_refused(client, sender, bad):
    assert client.post("/api/contact", json={**VALID, "email": bad}).status_code == 422
    assert sender.call_count == 0


def test_oversized_message_is_refused(client, sender):
    r = client.post("/api/contact", json={**VALID, "message": "x" * 4001})
    assert r.status_code == 422
    assert sender.call_count == 0


def test_message_at_the_limit_is_accepted(client, sender):
    r = client.post("/api/contact", json={**VALID, "message": "x" * 4000})
    assert r.status_code == 202


@pytest.mark.parametrize("field,length", [("first", 81), ("last", 81), ("org", 121),
                                           ("country", 81), ("topic", 61)])
def test_oversized_field_is_refused(client, sender, field, length):
    r = client.post("/api/contact", json={**VALID, field: "x" * length})
    assert r.status_code == 422
    assert sender.call_count == 0


@pytest.mark.parametrize("payload", [[], "a string", 42, None])
def test_non_object_payload_is_refused(client, sender, payload):
    assert client.post("/api/contact", json=payload).status_code == 422
    assert sender.call_count == 0


# ── injection ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("injected", ["to", "recipient", "bcc", "cc", "from",
                                       "smtp_host", "smtp_user", "smtp_password"])
def test_a_caller_cannot_add_a_recipient_field(client, sender, injected):
    """extra="forbid" — a payload that tries to name a destination is REJECTED, not ignored."""
    r = client.post("/api/contact", json={**VALID, injected: "attacker@evil.test"})
    assert r.status_code == 422
    assert sender.call_count == 0


@pytest.mark.parametrize("field", ["first", "last", "email", "org", "country", "topic"])
def test_header_injection_via_newlines_is_refused(client, sender, field):
    for breaker in ("\r\nBcc: attacker@evil.test", "\nBcc: attacker@evil.test", "a\rb"):
        r = client.post("/api/contact", json={**VALID, field: f"Ada{breaker}"})
        assert r.status_code == 422, f"{field} accepted a line break"
    assert sender.call_count == 0


def test_html_in_the_message_is_escaped_before_delivery():
    """The enquiry email is read by our own staff, so an unescaped payload would be stored XSS
    aimed at us. Checked on the real builder, not the route."""
    from app.email import _contact_html
    html_out = _contact_html("<script>alert(1)</script>", "a@b.co", "<img onerror=x>",
                             "GB", "topic", "line1\n<b>bold</b>")
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "<img onerror" not in html_out
    assert "<b>bold</b>" not in html_out
    assert "<br>" in html_out, "newlines should still render as breaks, after escaping"


def test_subject_cannot_carry_a_header_break():
    """Defence in depth: even if a line break reached the service, the subject is sanitized."""
    with patch("app.email._send") as send:
        from app.email import send_contact_message_email
        send_contact_message_email(
            first="Ada\r\nBcc: attacker@evil.test", last="L", email="a@b.co",
            org="", country="GB", topic="t\r\nX: y", message="hello")
        subject = send.call_args[0][1]
    assert "\r" not in subject and "\n" not in subject


def test_destination_comes_from_configuration_not_the_payload():
    with patch("app.email._send") as send, \
         patch.object(settings, "CONTACT_EMAIL", "configured@zoiko.test"):
        from app.email import send_contact_message_email
        send_contact_message_email(first="A", last="B", email="payer@evil.test", org="",
                                   country="GB", topic="t", message="m")
        recipient = send.call_args[0][0]
    assert recipient == "configured@zoiko.test"
    assert recipient != "payer@evil.test"


# ── abuse ─────────────────────────────────────────────────────────────────────────────────

def test_repeated_submissions_are_rate_limited(client, sender):
    codes = [client.post("/api/contact", json=VALID).status_code for _ in range(7)]
    assert 429 in codes, f"no rate limit applied: {codes}"
    assert codes.count(202) <= 5


def test_rate_limited_response_says_nothing_about_mail(client, sender):
    for _ in range(7):
        r = client.post("/api/contact", json=VALID)
    assert r.status_code == 429
    assert "resend" not in r.text.lower()


# ── mail outage ───────────────────────────────────────────────────────────────────────────

def test_a_mail_provider_failure_does_not_fail_the_request(client):
    """Delivery is a background task and _send already swallows provider errors, so a mail
    outage must not turn a received enquiry into a 500 for the visitor."""
    with patch("app.routers.contact.send_contact_message_email",
               side_effect=RuntimeError("provider down")):
        r = client.post("/api/contact", json=VALID)
    assert r.status_code == 202


def test_no_email_is_sent_when_the_provider_is_unconfigured(client):
    """RESEND_API_KEY blank is a supported local state: _send logs and returns."""
    with patch.object(settings, "RESEND_API_KEY", ""), patch("app.email.httpx.post") as post:
        r = client.post("/api/contact", json=VALID)
    assert r.status_code == 202
    assert post.call_count == 0, "no HTTP call may be made without a provider key"


# ── endpoint posture ──────────────────────────────────────────────────────────────────────

def test_endpoint_is_public(client, sender):
    """Prospects have no account; the endpoint must not require a token."""
    r = client.post("/api/contact", json=VALID)
    assert r.status_code != 401 and r.status_code != 403


def test_the_endpoint_is_write_only(client):
    """No GET surface: an enquiry can be submitted but never read back.

    404 rather than 405 because main.py deliberately refuses an unmatched /api GET instead of
    letting it fall through to the SPA catch-all (which would answer with index.html).
    """
    assert client.get("/api/contact").status_code in (404, 405)
