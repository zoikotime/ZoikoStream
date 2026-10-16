"""The product is "Zoiko Stream".

Server mail said "Zoiko Steam" about 480 times: every sender display name, subjects,
headlines, sign-offs and body copy, while the client, the logo and IDN-001's own body said
Zoiko Stream. These checks keep it corrected.

Pure checks: no DB, no network. Every send goes through the real `_send` with the Resend
transport (`httpx.post`) intercepted, so nothing leaves the machine.
"""
import re
from pathlib import Path
from unittest.mock import patch

import app.email as email_mod
from app.config import settings

MISSPELLED = re.compile(r"zoiko[\s_\-]*steam", re.I)
ADDRESS = "info@zoikostream.com"
BASE = "https://app.zoikostream.example"


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


def _send_and_capture(fn, *args, **kwargs):
    """Run one send function and return the single Resend payload it produced."""
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json or {})
        return _Resp()

    with patch.object(email_mod.httpx, "post", post), \
            patch.object(settings, "RESEND_API_KEY", "re_dummy_key_for_tests"), \
            patch.object(settings, "MAIL_FROM", f"ZoikoStream <{ADDRESS}>"), \
            patch.object(settings, "ENVIRONMENT", "development"), \
            patch.object(settings, "APP_URL", BASE):
        assert fn(*args, **kwargs) is True
    assert len(calls) == 1, "exactly one message"
    return calls[0]


def _assert_no_misspelling(payload):
    for part in ("subject", "from", "html", "text"):
        assert not MISSPELLED.search(payload.get(part) or ""), f"misspelled brand in {part}"


def test_no_server_source_carries_the_misspelled_brand():
    app_dir = Path(email_mod.__file__).parent
    hits = [
        f"{path.relative_to(app_dir)}:{number}"
        for path in sorted(app_dir.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if MISSPELLED.search(line)
    ]
    assert hits == []


def test_every_sender_identity_is_zoiko_stream_on_the_same_address():
    names = {k: v for k, v in vars(email_mod).items()
             if k.startswith("SENDER_") and isinstance(v, str)}
    assert len(names) >= 15, "sender discovery found too few identities"
    with patch.object(settings, "MAIL_FROM", f"ZoikoStream <{ADDRESS}>"):
        for key, name in names.items():
            assert name.startswith("Zoiko Stream"), key
            # Only the display name is branded: the authenticated address never changes.
            assert email_mod._sender_identity(name) == f"{name} <{ADDRESS}>"
    # One security identity, spelled one way, whichever constant a template reaches for.
    assert {
        email_mod.SENDER_SECURITY, email_mod.SENDER_SECURITY_OPS,
        email_mod.SENDER_SECURITY_ADVISORY, email_mod.IDN_001_SENDER,
    } == {"Zoiko Stream Security"}


def test_account_ready_email_is_zoiko_stream_in_every_part():
    url = f"{BASE}/login"
    p = _send_and_capture(email_mod.send_account_ready_email, "holder@example.com", url)
    assert p["subject"] == "Your Zoiko Stream access is ready"
    # Class C: the default identity, never the security one.
    assert p["from"] == f"Zoiko Stream <{ADDRESS}>"
    _assert_no_misspelling(p)
    # HTML and plain text say the same thing, and the destination is untouched.
    for body in (p["html"], p["text"]):
        assert "Welcome to Zoiko Stream." in body
        assert "your Zoiko Stream access is active" in body
        assert "Open Zoiko Stream" in body
        assert url in body
    assert p["text"].rstrip().splitlines()[-1] == "Zoiko Stream"


def test_security_alert_and_verification_ship_from_one_identity():
    alert = _send_and_capture(
        email_mod.send_new_sign_in_email, "holder@example.com",
        signed_in_at="01 Jan 2026, 09:00 UTC", authentication_method="password",
        device="Chrome on Windows", location="Approximate location unavailable",
        session_reference="ses_ref_123")
    verify_url = f"{BASE}/verify-email?token=tok_abc-123_XYZ"
    verify = _send_and_capture(
        email_mod.send_email_verification_email, "holder@example.com",
        verify_url, 30, "01 Jan 2026, 09:30 UTC")

    assert alert["from"] == verify["from"] == f"Zoiko Stream Security <{ADDRESS}>"
    assert alert["subject"] == "New sign-in to your Zoiko Stream account"
    assert verify["subject"] == "Verify your email for Zoiko Stream"
    for p in (alert, verify):
        _assert_no_misspelling(p)
        assert p["text"].rstrip().splitlines()[-1] == "Zoiko Stream Security"
    for body in (alert["html"], alert["text"]):
        assert "sign in to Zoiko Stream at 01 Jan 2026, 09:00 UTC" in body
    # The security warning and both links are untouched by the copy change.
    assert "change your password immediately" in alert["text"]
    assert f"{BASE}/organization/settings?tab=security" in alert["text"]
    assert f"Verify email: {verify_url}" in verify["text"]
    assert f'href="{verify_url}"' in verify["html"]
