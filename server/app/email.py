import html
import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


def _send(to: str, subject: str, html_body: str) -> None:
    """Post one email to Resend. Best-effort: logs and swallows failures so a mail
    outage never breaks the request that triggered it."""
    if not settings.RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set; skipping email to %s", to)
        return
    try:
        resp = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json={"from": settings.MAIL_FROM, "to": [to], "subject": subject, "html": html_body},
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        # Resend returns the reason in the body — surface it for debugging.
        body = getattr(e, "response", None)
        log.error("Email to %s failed: %s %s", to, e, body.text if body else "")


def _shell(inner: str) -> str:
    # Inline styles only — email clients strip <style>/external CSS.
    return f"""\
<div style="background:#f2f2f4;padding:32px 0;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:6px;overflow:hidden;">
    {inner}
  </div>
</div>"""


def _base_url() -> str:
    return (settings.CORS_ORIGINS.split(",")[0].strip() or "https://zoikostream.com").rstrip("/")


def _header(title: str) -> str:
    # White band so the navy wordmark reads. Logo is served from the frontend's /public.
    return f"""
    <div style="background:#fff;padding:36px 24px 8px;text-align:center;">
      <img src="{_base_url()}/zoiko-logo.png" alt="ZoikoStream" height="40"
           style="height:40px;width:auto;display:inline-block;" />
    </div>
    <div style="padding:20px 24px 0;text-align:center;">
      <h1 style="color:#2e2e4d;font-weight:600;font-size:24px;margin:0;">{title}</h1>
    </div>"""


def _welcome_html(name: str) -> str:
    safe_name = html.escape(name or "there")
    app_url = _base_url()
    return _shell(f"""
    {_header("Welcome!")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {safe_name},</p>
      <p>Thanks for registering with <strong>ZoikoStream</strong>! Your account is
         active and ready to go.</p>
      <p>Head into the app to set up your organization, invite your team, and create
         your first event. We're happy to help with the rest.</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{app_url}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          Head to ZoikoStream
        </a>
      </p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def _otp_html(name: str, otp: str) -> str:
    safe_name = html.escape(name or "there")
    return _shell(f"""
    {_header("Password reset")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;text-align:center;">
      <p style="text-align:left;">Hi {safe_name},</p>
      <p style="text-align:left;">Use this code to reset your ZoikoStream password. It
         expires in 10 minutes.</p>
      <div style="margin:28px 0;font-size:40px;font-weight:bold;letter-spacing:12px;
           color:#2e2e4d;background:#f2f2f4;border-radius:8px;padding:20px 0;">{otp}</div>
      <p style="text-align:left;color:#888;font-size:13px;">If you didn't request this, you
         can safely ignore this email — your password won't change.</p>
      <p style="text-align:left;margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def send_welcome_email(to: str, name: str) -> None:
    _send(to, "Welcome to ZoikoStream 🎉", _welcome_html(name))


def send_reset_otp_email(to: str, name: str, otp: str) -> None:
    _send(to, "Your ZoikoStream password reset code", _otp_html(name, otp))


def _invitation_html(role: str, org_name: str, invite_link: str) -> str:
    safe_role = html.escape(role.capitalize())
    safe_org = html.escape(org_name or "an organization")
    return _shell(f"""
    {_header("You're invited!")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>You've been invited to join <strong>{safe_org}</strong> on ZoikoStream as a
         <strong>{safe_role}</strong>.</p>
      <p>This invitation expires in 7 days.</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{invite_link}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          Accept Invitation
        </a>
      </p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def send_invitation_email(to: str, role: str, org_name: str, invite_link: str) -> None:
    log.info("Invitation link for %s (%s @ %s): %s", to, role, org_name, invite_link)
    _send(to, f"You've been invited to join {org_name} on ZoikoStream", _invitation_html(role, org_name, invite_link))


def _registration_html(name: str, event_title: str, watch_url: str) -> str:
    safe_name = html.escape(name or "there")
    safe_title = html.escape(event_title)
    return _shell(f"""
    {_header("You're registered!")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {safe_name},</p>
      <p>You're registered for <strong>{safe_title}</strong> on ZoikoStream. Use the link
         below when it's time to join.</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{watch_url}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          Go to Event
        </a>
      </p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def send_registration_confirmation_email(to: str, name: str, event_title: str, stream_id) -> None:
    watch_url = f"{_base_url()}/events/{stream_id}/watch"
    _send(to, f"You're registered for {event_title}", _registration_html(name, event_title, watch_url))


def _role_assigned_html(name: str, role: str, event_title: str, studio_url: str) -> str:
    safe_name = html.escape(name or "there")
    safe_title = html.escape(event_title)
    cta = "Open Host Studio" if role == "host" else "Open Event"
    return _shell(f"""
    {_header("You've been assigned to an event")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {safe_name},</p>
      <p>You've been made the <strong>{html.escape(role)}</strong> for
         <strong>{safe_title}</strong> on ZoikoStream.</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{studio_url}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          {cta}
        </a>
      </p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


# role is "host" or "moderator" -- hosts land on the broadcasting studio, everyone else
# just gets sent to the event itself (no moderator console route exists to link to yet).
def send_role_assigned_email(to: str, name: str, role: str, event_title: str, stream_id) -> None:
    studio_url = f"{_base_url()}/host/dashboard" if role == "host" else f"{_base_url()}/events/{stream_id}/watch"
    _send(to, f"You're now {'hosting' if role == 'host' else 'the moderator for'} {event_title}", _role_assigned_html(name, role, event_title, studio_url))


if __name__ == "__main__":
    # Offline self-check: best-effort behavior + HTML escaping + OTP rendering. No network.
    from unittest.mock import patch

    with patch.object(settings, "RESEND_API_KEY", ""):
        send_welcome_email("nobody@example.com", "<script>")  # must not raise
        send_reset_otp_email("nobody@example.com", "<script>", "0421")
    assert "&lt;script&gt;" in _welcome_html("<script>"), "name not HTML-escaped"
    assert "0421" in _otp_html("Alice", "0421"), "otp not rendered"
    assert "Alice" in _otp_html("Alice", "0421")
    assert "zoiko-logo.png" in _welcome_html("Alice"), "logo missing from welcome email"
    assert "zoiko-logo.png" in _otp_html("Alice", "0421"), "logo missing from otp email"
    print("ok")
