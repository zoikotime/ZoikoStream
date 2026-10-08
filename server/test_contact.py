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
from app.email import CONTACT_CONFIRMATION_SUBJECT
from app.routers import contact as contact_router

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
    the fourth test in a file would start seeing 429s from the third. The duplicate-enquiry
    guard is process state too, and every test posts the same VALID payload."""
    ratelimit._HITS.clear()
    contact_router._RECENT.clear()
    with TestClient(main_app.app) as c:
        yield c
    ratelimit._HITS.clear()
    contact_router._RECENT.clear()


@pytest.fixture
def sender():
    with patch("app.routers.contact.send_contact_message_email") as m:
        yield m


@pytest.fixture
def confirmer():
    with patch("app.routers.contact.send_contact_confirmation_email") as m:
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
    outage must not turn a received enquiry into a 500 for the visitor.

    The failure is injected at the TRANSPORT, which is what a provider outage actually is.
    Patching the sender itself to raise would step over `_send`'s own error handling - the
    very thing under test - and the exception would escape the background task instead.
    """
    import httpx

    with patch("app.email.httpx.post", side_effect=httpx.ConnectError("provider down")):
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


# ── the submitter's confirmation ──────────────────────────────────────────────────────────
# Every accepted enquiry now produces two emails: the team's notification (unchanged) and a
# confirmation to the submitted work email. The confirmation follows only a delivered team
# copy, goes only to the validated address, and is never sent twice for one enquiry.

def test_a_valid_submission_sends_the_team_copy_and_the_confirmation(client, sender, confirmer):
    r = client.post("/api/contact", json=VALID)
    assert r.status_code == 202 and r.json() == {"received": True}
    assert sender.call_count == 1 and confirmer.call_count == 1
    kw = confirmer.call_args.kwargs
    assert kw["email"] == "ada@example.com"
    assert kw["first"] == "Ada" and kw["org"] == VALID["org"]


def test_the_confirmation_goes_to_the_submitted_work_email_trimmed(client, sender, confirmer):
    client.post("/api/contact", json={**VALID, "email": "  ada@example.com  "})
    assert confirmer.call_args.kwargs["email"] == "ada@example.com"


@pytest.mark.parametrize("bad", ["not-an-email", "ada@", "@example.com", "ada example@x.com", ""])
def test_an_invalid_email_sends_nothing(client, sender, confirmer, bad):
    assert client.post("/api/contact", json={**VALID, "email": bad}).status_code == 422
    assert sender.call_count == 0 and confirmer.call_count == 0


def test_a_validation_failure_sends_no_email_at_all(client, sender, confirmer):
    assert client.post("/api/contact", json={**VALID, "message": "   "}).status_code == 422
    assert client.post("/api/contact", json={k: v for k, v in VALID.items() if k != "first"}).status_code == 422
    assert sender.call_count == 0 and confirmer.call_count == 0


def test_the_real_delivery_path_sends_both_emails_to_the_right_places(client):
    """Through the real email service down to `_send`: the team copy to the configured inbox
    with its existing subject, then the confirmation to the submitter with a fixed subject,
    a plain-text part, and replies routed to the team."""
    with patch.object(settings, "CONTACT_EMAIL", "team@zoiko.test"), \
         patch("app.email._send", return_value=True) as send:
        assert client.post("/api/contact", json=VALID).status_code == 202
    assert send.call_count == 2
    team, confirm = send.call_args_list
    assert team.args[0] == "team@zoiko.test"
    # The team subject is unchanged, including its existing 40-character cap on the topic.
    assert team.args[1] == f"[{VALID['topic'][:40]}] Enquiry from Ada Lovelace"
    assert "ada@example.com" in team.args[2]                  # body only, as before
    assert "reply_to" not in team.kwargs                      # team copy unchanged: no new header
    assert confirm.args[0] == "ada@example.com"
    assert confirm.args[1] == CONTACT_CONFIRMATION_SUBJECT == "We've received your ZoikoStream inquiry"
    assert confirm.kwargs["reply_to"] == "team@zoiko.test"
    assert "Hi Ada," in confirm.kwargs["text_body"]


def _confirmation(**over):
    """Render the confirmation through the real builder, capturing what would be sent."""
    from app.email import send_contact_confirmation_email
    args = dict(first="Ada", last="Lovelace", email="ada@example.com", org="Analytical Engines",
                topic="Procurement", message="We would like to move to the Pro plan.")
    args.update(over)
    with patch("app.email._send", return_value=True) as send:
        ok = send_contact_confirmation_email(**args)
    return ok, send


def test_the_confirmation_greets_by_first_name_and_summarises_the_enquiry():
    ok, send = _confirmation()
    assert ok
    html_body, text = send.call_args.args[2], send.call_args.kwargs["text_body"]
    for body in (html_body, text):
        assert "Hi Ada," in body
        assert "Thank you for contacting ZoikoStream" in body
        assert "Ada Lovelace" in body and "Analytical Engines" in body and "Procurement" in body
        assert "We would like to move to the Pro plan." in body
        assert "If you did not submit this request, you can ignore this email." in body
        assert "ZoikoStream Team" in body
    # No invented response-time promise.
    assert "24 hours" not in html_body and "within" not in text.lower()


def test_the_organization_row_appears_only_when_one_was_given():
    _, with_org = _confirmation(org="Analytical Engines")
    _, without = _confirmation(org="")
    assert "Organization" in with_org.call_args.kwargs["text_body"]
    assert "Organization" not in without.call_args.kwargs["text_body"]
    assert "Organization" not in without.call_args.args[2]


def test_the_confirmation_cannot_carry_markup_or_links_to_an_unverified_address():
    _, send = _confirmation(first="<script>alert(1)</script>",
                            message="Claim your prize at https://evil.example/win or www.bad.test " + "x" * 400)
    html_body, text = send.call_args.args[2], send.call_args.kwargs["text_body"]
    assert "<script>" not in html_body and "&lt;script&gt;" in html_body
    for body in (html_body, text):
        assert "evil.example" not in body and "www.bad.test" not in body
        assert "[link removed]" in body
    message_line = next(l for l in text.splitlines() if l.startswith("- Message:"))
    assert len(message_line) < 200 and message_line.endswith("…")


def test_the_confirmation_subject_never_contains_request_text():
    _, send = _confirmation(first="Ada\r\nBcc: attacker@evil.test", topic="t\r\nX: y")
    assert send.call_args.args[1] == CONTACT_CONFIRMATION_SUBJECT


@pytest.mark.parametrize("bad", ["", "   ", "no-at-sign", "ada@example.com\r\nBcc: x@evil.test",
                                 "ada@example.com, other@evil.test", "Ada <ada@example.com>"])
def test_the_confirmation_refuses_an_unsafe_recipient_without_calling_the_provider(bad):
    ok, send = _confirmation(email=bad)
    assert ok is False
    assert send.call_count == 0


def test_a_team_delivery_failure_withholds_the_confirmation(client, caplog):
    """The confirmation says our team will review the enquiry — it must not go out when the
    team never received it."""
    with patch("app.email._send", side_effect=[False]) as send, caplog.at_level("ERROR"):
        assert client.post("/api/contact", json=VALID).status_code == 202
    assert send.call_count == 1
    assert "NOT delivered to the team inbox" in caplog.text


def test_a_confirmation_failure_is_logged_once_and_never_retried(client, caplog):
    with patch("app.email._send", side_effect=[True, False]) as send, caplog.at_level("ERROR"):
        r = client.post("/api/contact", json=VALID)
    assert r.status_code == 202 and r.json() == {"received": True}
    assert send.call_count == 2                                # no retry, so no duplicate
    assert "confirmation NOT delivered to the submitter" in caplog.text


def test_no_provider_secret_reaches_the_response_or_the_log(client, caplog):
    import httpx

    secret = "re_live_SECRET_must_never_be_logged_123456"
    request = httpx.Request("POST", "https://api.resend.com/emails")
    failure = httpx.HTTPStatusError("422 Unprocessable", request=request,
                                    response=httpx.Response(422, request=request, text='{"message":"invalid"}'))
    with patch.object(settings, "RESEND_API_KEY", secret), \
         patch("app.email.httpx.post", side_effect=failure), caplog.at_level("DEBUG"):
        r = client.post("/api/contact", json=VALID)
    assert r.status_code == 202
    assert secret not in r.text and secret not in caplog.text
    assert "resend" not in r.text.lower()


def test_a_resubmitted_enquiry_is_accepted_but_not_mailed_twice(client, sender, confirmer):
    first = client.post("/api/contact", json=VALID)
    again = client.post("/api/contact", json=VALID)
    assert first.status_code == again.status_code == 202
    assert again.json() == {"received": True}
    assert sender.call_count == 1 and confirmer.call_count == 1
    # A different enquiry from the same person is a new enquiry.
    client.post("/api/contact", json={**VALID, "message": "A second, different question."})
    assert sender.call_count == 2 and confirmer.call_count == 2
