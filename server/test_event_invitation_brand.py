"""Event invitations say "Zoiko Stream", whatever the deployment's MAIL_FROM says.

Reported: a recipient of an event invitation saw "Zoiko Steam". No current template carries
that spelling (test_brand_name.py scans the source). But two event invitations, the viewer
invite (POST /events/{id}/invite-viewers) and the host/speaker assignment, passed no sender, so
their From line was MAIL_FROM verbatim, display name included. That name is deployment
configuration, outside the code that was corrected. Both now use the approved SENDER_DEFAULT
"Zoiko Stream" around MAIL_FROM's authenticated address, as the organization invitations
already did. The address itself is unchanged.

Pure checks: no DB, no network. Every send goes through the real `_send` with the Resend
transport (`httpx.post`) intercepted, so nothing leaves the machine.
"""
import html
import re
from unittest.mock import patch

import app.email as email_mod
from app.config import settings

MISSPELLED = re.compile(r"zoiko[\s_\-]*steam", re.I)
ADDRESS = "events@zoikostream.example"
TITLE = "Q4 <All-Hands> & Roadmap"
WATCH_URL = "https://app.zoikostream.example/events/e1/watch?reg=TOKEN123"
EVENT_URL = "https://app.zoikostream.example/host/dashboard?event=e1"
ACCEPT_URL = "https://app.zoikostream.example/backstage/accept?token=TOKEN456"


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


def _capture(fn, *args, mail_from=f"ZoikoStream <{ADDRESS}>", **kwargs):
    """Run one send function with MAIL_FROM set to `mail_from`; return the one Resend payload."""
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json or {})
        return _Resp()

    with patch.object(email_mod.httpx, "post", post), \
            patch.object(settings, "RESEND_API_KEY", "re_dummy_key_for_tests"), \
            patch.object(settings, "MAIL_FROM", mail_from):
        fn(*args, **kwargs)
    assert len(calls) == 1, "exactly one message"
    return calls[0]


def _no_misspelling(payload):
    for part in ("from", "subject", "html", "text"):
        assert not MISSPELLED.search(payload.get(part) or ""), f"misspelled brand in {part}"


def _viewer_invite(**kw):
    return _capture(email_mod.send_viewer_invite_email, "guest@example.test", "Grace Guest", TITLE,
                    WATCH_URL, "Henry Host", **kw)


def _assignment(**kw):
    return _capture(email_mod.send_assignment_email, "speaker@example.test", "Sam Speaker", TITLE,
                    "speaker", "Acme Media", EVENT_URL, **kw)


# ── the viewer invite and the assignment: sender pinned in code ───────────────────────────

def test_viewer_invite_is_from_zoiko_stream_on_the_configured_address():
    payload = _viewer_invite()
    assert payload["from"] == f"Zoiko Stream <{ADDRESS}>"
    assert payload["subject"] == f"You're invited to watch {TITLE}"
    _no_misspelling(payload)


def test_assignment_is_from_zoiko_stream_on_the_configured_address():
    payload = _assignment()
    assert payload["from"] == f"Zoiko Stream <{ADDRESS}>"
    assert payload["subject"] == f"You've been added as speaker for {TITLE}"
    _no_misspelling(payload)


def test_a_misbranded_mail_from_no_longer_reaches_either_invitation():
    # The configuration that would have produced the report, for these two emails.
    for send in (_viewer_invite, _assignment):
        payload = send(mail_from=f"Zoiko Steam <{ADDRESS}>")
        assert payload["from"] == f"Zoiko Stream <{ADDRESS}>"
        _no_misspelling(payload)


def test_a_bare_mail_from_address_still_gets_the_approved_name():
    for send in (_viewer_invite, _assignment):
        assert send(mail_from=ADDRESS)["from"] == f"Zoiko Stream <{ADDRESS}>"


def test_viewer_invite_keeps_its_event_details_and_link():
    body = _viewer_invite()["html"]
    assert html.escape(TITLE) in body, "event title (escaped) missing"
    assert "Grace Guest" in body and "Henry Host" in body
    assert WATCH_URL in body, "watch link (with its access token) changed"
    assert "<All-Hands>" not in body, "title must stay escaped"


def test_assignment_keeps_its_event_details_and_link():
    body = _assignment()["html"]
    assert html.escape(TITLE) in body and "Sam Speaker" in body and "Acme Media" in body
    assert EVENT_URL in body


# ── the contributor invitation (CON-001), already on an approved sender ───────────────────

def test_contributor_invite_is_zoiko_stream_in_every_part():
    payload = _capture(
        email_mod.send_contributor_invite_email, "contrib@example.test",
        name="Cara Contributor", event_title=TITLE, role="Guest speaker", starts="22 Oct 2026, 15:00",
        zone="Asia/Kolkata", local_note="", technical_check="Required", technical_status="Not started",
        rehearsal="None scheduled", org_name="Acme Media", inviter="Henry Host", grants="Speak on stage",
        consent="Recorded", expires_at="29 Oct 2026", accept_url=ACCEPT_URL,
        mail_from=f"Zoiko Steam <{ADDRESS}>",
    )
    assert payload["from"] == f"Zoiko Stream Event Team <{ADDRESS}>"
    assert payload["subject"] == f"You're invited to contribute to {TITLE} on Zoiko Stream"
    assert payload.get("text"), "plain-text alternative expected"
    assert "Zoiko Stream" in payload["text"]
    assert ACCEPT_URL in payload["html"] and ACCEPT_URL in payload["text"]
    _no_misspelling(payload)
