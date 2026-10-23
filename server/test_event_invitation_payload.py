"""What an event invitation hands to the email provider: sender, subject, content, links, and
exactly one attachment (the inline logo), for each invitation type.

Reported: invitations land in spam and carry a "logo document". The application side was
traced end to end (Bug 15): each invitation is one Resend request whose only attachment is
the bundled logo, sent the way Resend documents inline images (`content_id`, referenced from
the HTML as cid:), with a .png filename so its type is derived as image/png. The MIME message
itself, and how a mail client presents an inline image, are Resend's and the client's. These
tests pin the request so a regression in it (a second attachment, an unreferenced logo, a
lost link, a misbranded sender) is caught.

Pure checks: no DB, no network. Every send goes through the real `_send` with the Resend
transport (`httpx.post`) intercepted, so nothing leaves the machine.
"""
import re
from unittest.mock import patch

import httpx

import app.email as email_mod
from app.config import settings

ADDRESS = "events@zoikostream.example"
APP = "https://app.zoikostream.example"
TITLE = "Quarterly Town Hall"
WATCH_URL = f"{APP}/events/e1/watch?reg=TOKEN123"
EVENT_URL = f"{APP}/host/dashboard?event=e1"
ACCEPT_URL = f"{APP}/backstage/accept?token=TOKEN456"
INVITE_URL = f"{APP}/accept-invite?token=TOKEN789"
MISSPELLED = re.compile(r"zoiko[\s_\-]*steam", re.I)


class _Resp:
    def __init__(self, status=200):
        self.status_code = status
        self.text = "{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("provider refused", request=None, response=self)


def _capture(fn, *args, status=200, **kwargs):
    """Run one send function; return (its return value, the list of Resend payloads)."""
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        assert url == email_mod.RESEND_URL
        calls.append(json or {})
        return _Resp(status)

    with patch.object(email_mod.httpx, "post", post), \
            patch.object(settings, "RESEND_API_KEY", "re_dummy_key_for_tests"), \
            patch.object(settings, "MAIL_FROM", f"ZoikoStream <{ADDRESS}>"), \
            patch.object(settings, "APP_URL", APP):
        result = fn(*args, **kwargs)
    return result, calls


SENDS = {
    "viewer invite": (
        lambda: email_mod.send_viewer_invite_email("guest@example.test", "Grace Guest", TITLE, WATCH_URL, "Henry Host"),
        "Zoiko Stream", f"You're invited to watch {TITLE}", [WATCH_URL], ["Grace Guest", "Henry Host", TITLE]),
    "host/speaker assignment": (
        lambda: email_mod.send_assignment_email("speaker@example.test", "Sam Speaker", TITLE, "host", "Acme Media", EVENT_URL),
        "Zoiko Stream", f"You've been added as host for {TITLE}", [EVENT_URL], ["Sam Speaker", "Acme Media", TITLE]),
    "contributor invite": (
        lambda: email_mod.send_contributor_invite_email(
            "contrib@example.test", name="Cara Contributor", event_title=TITLE, role="Guest speaker",
            starts="22 Oct 2026, 15:00", zone="Asia/Kolkata", local_note="", technical_check="Required",
            technical_status="Not started", rehearsal="None scheduled", org_name="Acme Media", inviter="Henry Host",
            grants="Speak on stage", consent="Recorded", expires_at="29 Oct 2026", accept_url=ACCEPT_URL),
        "Zoiko Stream Event Team", f"You're invited to contribute to {TITLE} on Zoiko Stream", [ACCEPT_URL],
        ["Cara Contributor", "Henry Host", "Acme Media", TITLE]),
    "organization invite": (
        lambda: email_mod.send_invitation_email(
            "member@example.test", org_name="Acme Media", inviter_name="Henry Host", role_name="Host",
            workspace_scope="All", expires_display="29 Oct 2026", invite_url=INVITE_URL),
        "Zoiko Stream", "You were invited to Acme Media on Zoiko Stream", [INVITE_URL], ["Acme Media", "Henry Host"]),
}


def test_each_invitation_is_one_request_from_the_right_sender_with_the_right_subject():
    for label, (send, sender, subject, _, _) in SENDS.items():
        _, calls = _capture(send)
        assert len(calls) == 1, f"{label}: sent {len(calls)} times"
        payload = calls[0]
        assert payload["from"] == f"{sender} <{ADDRESS}>", label
        assert payload["subject"] == subject, label
        assert payload["to"] == [payload["to"][0]] and payload["to"][0].endswith("@example.test"), label


def test_each_invitation_carries_its_event_details_and_link_and_no_misspelled_brand():
    for label, (send, _, _, links, details) in SENDS.items():
        payload = _capture(send)[1][0]
        for link in links:
            assert f'href="{link}"' in payload["html"], f"{label}: link missing or altered"
            if payload.get("text"):
                assert link in payload["text"], f"{label}: link missing from the plain-text part"
        for detail in details:
            assert detail in payload["html"], f"{label}: {detail!r} missing"
        for part in ("from", "subject", "html", "text"):
            assert not MISSPELLED.search(payload.get(part) or ""), f"{label}: misspelled brand in {part}"


def test_plain_text_part_is_provided_or_left_to_the_provider():
    # The contributor and organization invites carry their own text part. The viewer invite
    # and the assignment send HTML only; Resend documents that it then generates the plain-text
    # version from the HTML. Either way the request must not send an EMPTY text, which would
    # opt out of that.
    for label, (send, *_rest) in SENDS.items():
        payload = _capture(send)[1][0]
        assert payload.get("text", None) != "", f"{label}: empty text part opts out of the provider's"
    assert _capture(SENDS["contributor invite"][0])[1][0]["text"]
    assert _capture(SENDS["organization invite"][0])[1][0]["text"]


def test_the_only_attachment_is_the_inline_logo_and_the_html_shows_it():
    for label, (send, *_rest) in SENDS.items():
        payload = _capture(send)[1][0]
        attachments = payload.get("attachments") or []
        assert len(attachments) == 1, f"{label}: {len(attachments)} attachments"
        logo = attachments[0]
        assert set(logo) <= {"filename", "content", "content_id", "content_type"}, f"{label}: unexpected fields"
        # Resend's documented inline-image field, referenced from the HTML. A logo the HTML does
        # not reference is what a client shows as a separate file.
        assert logo["content_id"] == email_mod.LOGO_CID
        assert re.findall(r'src="cid:([^"]+)"', payload["html"]) == [email_mod.LOGO_CID], label
        # The type Resend derives from the filename when no content_type is given: image/png.
        assert logo["filename"] == "zoiko-logo.png"
        assert logo.get("content_type", "image/png") == "image/png"


def test_the_inline_logo_is_the_bundled_png():
    import base64
    content = base64.b64decode(email_mod._logo_attachment()["content"])
    assert content[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert content == email_mod._LOGO_PATH.read_bytes()


def test_no_html_references_an_image_the_message_does_not_carry():
    for label, (send, *_rest) in SENDS.items():
        payload = _capture(send)[1][0]
        sources = re.findall(r'<img[^>]+src="([^"]+)"', payload["html"])
        carried = {f"cid:{a['content_id']}" for a in payload.get("attachments") or []}
        assert all(src in carried for src in sources), f"{label}: {sources} not all carried"


def test_a_provider_refusal_is_logged_not_raised_and_reports_no_success():
    for label, (send, *_rest) in SENDS.items():
        _, calls = _capture(send, status=422)          # e.g. an unverified sender domain
        assert len(calls) == 1, label                  # attempted once, never retried in a loop
    result, _ = _capture(lambda: email_mod._send("x@example.test", "s", "<p>b</p>"), status=500)
    assert result is False


def test_a_missing_logo_file_still_sends_the_invitation_without_an_attachment():
    email_mod._logo_attachment.cache_clear()
    try:
        with patch.object(email_mod, "_LOGO_PATH", email_mod._LOGO_PATH.with_name("missing.png")):
            payload = _capture(SENDS["viewer invite"][0])[1][0]
        assert "attachments" not in payload
        assert f'href="{WATCH_URL}"' in payload["html"]
    finally:
        email_mod._logo_attachment.cache_clear()


def test_nothing_is_sent_without_a_provider_key():
    calls = []
    with patch.object(email_mod.httpx, "post", lambda *a, **k: calls.append(1)), \
            patch.object(settings, "RESEND_API_KEY", ""):
        SENDS["viewer invite"][0]()
    assert calls == []
