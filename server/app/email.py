import base64
import html
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import httpx

from .config import settings

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"

# Logo is embedded as an inline (cid) attachment, not hotlinked: recipient mail
# clients can't reach a localhost / undeployed frontend, so the img travels with
# the email instead. Bundled in the package so it's always available at runtime.
LOGO_CID = "zoiko-logo"
_LOGO_PATH = Path(__file__).parent / "assets" / "zoiko-logo.png"


@lru_cache(maxsize=1)
def _logo_attachment() -> dict | None:
    try:
        content = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
    except OSError as e:
        log.error("Logo not found at %s: %s", _LOGO_PATH, e)
        return None
    return {"filename": "zoiko-logo.png", "content": content, "content_id": LOGO_CID}


def _send(to: str, subject: str, html_body: str) -> tuple[bool, str | None, str | None]:
    """Post one email to Resend. Returns (ok, provider_message_id, error).

    Still best-effort — it never raises, so a mail outage cannot break the request that
    triggered it. But it now REPORTS the outcome, because the invitation system records
    `sent` vs `failed` on the row and a silently swallowed failure would leave an
    invitation sitting at `pending` forever with nobody able to tell why.

    `provider_message_id` is Resend's id, stored so a later delivery webhook can be matched
    back to the invitation it belongs to.
    """
    if not settings.RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set; skipping email to %s", to)
        return False, None, "Email is not configured on this deployment"
    payload = {"from": settings.MAIL_FROM, "to": [to], "subject": subject, "html": html_body}
    logo = _logo_attachment()
    if logo:
        payload["attachments"] = [logo]
    try:
        resp = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        message_id = None
        try:
            message_id = (resp.json() or {}).get("id")
        except ValueError:
            pass   # a 2xx with an unparseable body still means it was accepted
        return True, message_id, None
    except httpx.HTTPError as e:
        # Resend returns the reason in the body — surface it for debugging AND for the row.
        body = getattr(e, "response", None)
        detail = body.text if body is not None else str(e)
        log.error("Email to %s failed: %s %s", to, e, detail)
        return False, None, detail[:400]


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
    # White band so the navy wordmark reads. Logo rides along as an inline cid
    # attachment (see _logo_attachment) so it renders without a public image host.
    return f"""
    <div style="background:#fff;padding:36px 24px 8px;text-align:center;">
      <img src="cid:{LOGO_CID}" alt="ZoikoStream" height="40"
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


def _event_created_html(organizer: str, title: str, start: datetime | None, status: str) -> str:
    safe_organizer = html.escape(organizer or "there")
    safe_title = html.escape(title or "Untitled event")
    when = start.strftime("%d %b %Y, %I:%M %p") if start else "Not scheduled"
    return _shell(f"""
    {_header("Event created")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {safe_organizer},</p>
      <p><strong>{safe_title}</strong> has been created on ZoikoStream.</p>
      <table style="width:100%;border-collapse:collapse;margin:24px 0;font-size:14px;">
        <tr><td style="padding:10px 0;color:#888;">Start time</td>
            <td style="padding:10px 0;text-align:right;">{when}</td></tr>
        <tr><td style="padding:10px 0;color:#888;">Status</td>
            <td style="padding:10px 0;text-align:right;">{html.escape(status.title())}</td></tr>
      </table>
      <p>You can now invite hosts, assign moderators, add speakers, and publish the event
         when you're ready.</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{_base_url()}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          Open ZoikoStream
        </a>
      </p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


# ── invitations ───────────────────────────────────────────────────────────────
# ONE template serves org-membership and event invitations: the event rows simply do not
# render when there is no event. Two near-identical templates would drift.
#
# Neither button ACTS on being opened. Both land on the accept page, which requires a
# deliberate click to POST. That is not politeness — mail scanners, link previewers and
# corporate security proxies fetch every URL in an email, and a GET that accepted (or
# declined) an invitation would be triggered by a robot before the human ever read it.

def _detail_rows(rows: list[tuple[str, str]]) -> str:
    """The grey label / right-aligned value table used by the invitation emails.
    Rows whose value is falsy are dropped, so an unscheduled event renders one fewer line
    instead of "Date: None"."""
    cells = "".join(
        f"""<tr><td style="padding:9px 0;color:#888;font-size:13px;">{html.escape(label)}</td>
              <td style="padding:9px 0;text-align:right;font-size:13px;color:#333;">
                <strong>{html.escape(str(value))}</strong></td></tr>"""
        for label, value in rows if value
    )
    return f"""<table role="presentation" width="100%" style="border-collapse:collapse;margin:22px 0;
                 border-top:1px solid #eee;border-bottom:1px solid #eee;">{cells}</table>"""


def _button(url: str, label: str, *, primary: bool = True) -> str:
    bg, color, border = ("#7ac142", "#fff", "#7ac142") if primary else ("#fff", "#555", "#d8d8dd")
    return f"""<a href="{url}" style="background:{bg};color:{color};text-decoration:none;
        padding:13px 26px;border:1px solid {border};border-radius:4px;font-weight:bold;
        font-size:14px;display:inline-block;margin:4px 6px;">{html.escape(label)}</a>"""


def _invitation_html(*, org_name: str, inviter: str, role_label: str, accept_url: str,
                     decline_url: str, expires: str, event_title: str | None = None,
                     event_when: str | None = None, message: str | None = None) -> str:
    safe_org = html.escape(org_name or "an organization")
    safe_inviter = html.escape(inviter or "An administrator")
    what = (
        f"invited you to join <strong>{html.escape(event_title)}</strong>"
        if event_title else f"invited you to join <strong>{safe_org}</strong> on ZoikoStream"
    )
    rows = _detail_rows([
        ("Organization", org_name),
        ("Event", event_title or ""),
        ("Date", event_when or ""),
        ("Your role", role_label),
        ("Invitation expires", expires),
    ])
    note = (
        f"""<div style="background:#f7f7f9;border-left:3px solid #7ac142;padding:12px 16px;
              margin:0 0 20px;font-size:14px;color:#444;">{html.escape(message)}</div>"""
        if message else ""
    )
    return _shell(f"""
    {_header("You're invited")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>{safe_inviter} has {what}.</p>
      {note}
      {rows}
      <p style="text-align:center;margin:28px 0 8px;">
        {_button(accept_url, "Accept invitation")}
        {_button(decline_url, "Decline", primary=False)}
      </p>
      <p style="color:#888;font-size:13px;text-align:center;margin-top:20px;">
        This invitation is personal to {html.escape("you")} and can only be used once.
        If you weren't expecting it, you can ignore this email or decline above.
      </p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def _invitation_outcome_html(*, heading: str, lead: str, rows: list[tuple[str, str]],
                             footer: str | None = None) -> str:
    """Notification to the INVITER when an invitation is accepted or declined, and to the
    invitee when access is revoked. No call to action — these report a fact."""
    return _shell(f"""
    {_header(heading)}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>{lead}</p>
      {_detail_rows(rows)}
      {f'<p style="color:#888;font-size:13px;">{footer}</p>' if footer else ""}
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def send_invitation_email(
    to: str, org_name: str, inviter: str, accept_url: str, decline_url: str, *,
    role_label: str, expires: str, event_title: str | None = None,
    event_when: str | None = None, message: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """The invitation itself. Returns _send's (ok, message_id, error) so the caller can
    record `sent` or `failed` on the row rather than guessing."""
    subject = (
        f"You're invited to {event_title} on ZoikoStream" if event_title
        else f"You're invited to join {org_name} on ZoikoStream"
    )
    return _send(to, subject, _invitation_html(
        org_name=org_name, inviter=inviter, role_label=role_label, accept_url=accept_url,
        decline_url=decline_url, expires=expires, event_title=event_title,
        event_when=event_when, message=message,
    ))


def send_invitation_accepted_email(to: str, invitee: str, org_name: str, role_label: str,
                                   event_title: str | None = None) -> None:
    """To the inviter. The platform has no in-app notification store, so this email IS the
    notification (the audit log is the durable record)."""
    _send(to, f"{invitee} accepted your invitation", _invitation_outcome_html(
        heading="Invitation accepted",
        lead=f"<strong>{html.escape(invitee)}</strong> has accepted your invitation and now has access.",
        rows=[("Organization", org_name), ("Event", event_title or ""), ("Role", role_label)],
    ))


def send_invitation_declined_email(to: str, invitee: str, org_name: str,
                                   event_title: str | None = None) -> None:
    _send(to, f"{invitee} declined your invitation", _invitation_outcome_html(
        heading="Invitation declined",
        lead=f"<strong>{html.escape(invitee)}</strong> has declined your invitation.",
        rows=[("Organization", org_name), ("Event", event_title or "")],
        footer="You can invite them again from the invitations page if this was unexpected.",
    ))


def send_invitation_revoked_email(to: str, org_name: str, role_label: str,
                                  event_title: str | None = None) -> None:
    """To the invitee, when an admin withdraws access AFTER acceptance. Telling them is the
    point — silently removing someone's access is how a host turns up to a locked studio."""
    _send(to, f"Your access to {event_title or org_name} was removed",
          _invitation_outcome_html(
              heading="Access removed",
              lead=f"An administrator at <strong>{html.escape(org_name)}</strong> has removed your access.",
              rows=[("Organization", org_name), ("Event", event_title or ""), ("Role removed", role_label)],
              footer="Your ZoikoStream account itself is unchanged. Contact the organizer if you think this is a mistake.",
          ))


def send_assignment_email(to: str, name: str | None, event_title: str | None, role: str,
                          org_name: str | None, event_url: str) -> None:
    """To someone an admin just put on an event's team. Not an invitation — they already have
    an account and the role is already granted, so there is nothing to accept; the button just
    opens their console. Built from the same _shell/_detail_rows/_button pieces as the
    invitation mail so the two don't drift into looking like different products."""
    safe_title = html.escape(event_title or "an event")
    _send(to, f"You've been added as {role} for {event_title or 'an event'}", _shell(f"""
    {_header("You've been assigned")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {html.escape(name or "there")},</p>
      <p>You've been added as a <strong>{html.escape(role.title())}</strong> for
         <strong>{safe_title}</strong> on {html.escape(org_name or "your organization")}'s
         ZoikoStream account.</p>
      {_detail_rows([("Organization", org_name or ""), ("Event", event_title or ""),
                     ("Your role", role.title())])}
      <p style="text-align:center;margin:28px 0 8px;">{_button(event_url, "Open the event")}</p>
    </div>"""))


def send_welcome_email(to: str, name: str) -> None:
    _send(to, "Welcome to ZoikoStream 🎉", _welcome_html(name))


def send_event_created_email(
    to: str, organizer_name: str, event_title: str, start_time: datetime | None, status: str
) -> None:
    _send(
        to,
        f"Event created: {event_title}",
        _event_created_html(organizer_name, event_title, start_time, status),
    )


def send_reset_otp_email(to: str, name: str, otp: str) -> None:
    _send(to, "Your ZoikoStream password reset code", _otp_html(name, otp))


def send_recording_failed_email(to: str, org_name: str, event_title: str, reason: str) -> None:
    """Tell the organization a capture did not happen, and why.

    Operational alerts are the one class of mail worth sending unprompted: a recording failure is
    invisible until somebody goes looking for a file that is not there, and by then the event is
    over and unrecapturable. The reason is included verbatim because it is usually actionable
    (no bucket configured, bad credentials, egress quota) — a generic "something went wrong"
    would send the admin to the logs for information we already have.
    """
    _send(
        to,
        f"Recording failed: {event_title}",
        _shell(
            _header("Recording failed")
            + f"<p style='margin:0 0 14px;color:#334155;font-size:15px'>Hi {html.escape(org_name)},</p>"
            "<p style='margin:0 0 14px;color:#334155;font-size:15px'>The recording for "
            f"<strong>{html.escape(event_title)}</strong> could not be captured, so no file was "
            "produced for this session.</p>"
            + _detail_rows([("Reason", reason)])
            + "<p style='margin:16px 0 0;color:#64748b;font-size:13px'>If the broadcast is still "
            "live you can retry the capture from the host console's recording panel.</p>"
        ),
    )


if __name__ == "__main__":
    # Offline self-check: best-effort behavior + HTML escaping + OTP rendering. No network.
    from unittest.mock import patch

    with patch.object(settings, "RESEND_API_KEY", ""):
        send_welcome_email("nobody@example.com", "<script>")  # must not raise
        send_reset_otp_email("nobody@example.com", "<script>", "0421")
        # Unconfigured mail must report the failure rather than silently claim success —
        # the invitation row's `sent` vs `failed` state depends on this return value.
        ok, mid, err = _send("nobody@example.com", "s", "<p>b</p>")
        assert ok is False and mid is None and err, "_send must report an unconfigured provider"
        send_invitation_accepted_email("a@example.com", "<i>Ann</i>", "Acme", "Host", "Launch")
        send_invitation_declined_email("a@example.com", "Ann", "Acme")
        send_invitation_revoked_email("a@example.com", "Acme", "Host", "Launch")
    assert "&lt;script&gt;" in _welcome_html("<script>"), "name not HTML-escaped"
    assert "0421" in _otp_html("Alice", "0421"), "otp not rendered"
    assert "Alice" in _otp_html("Alice", "0421")
    assert f"cid:{LOGO_CID}" in _welcome_html("Alice"), "logo cid missing from welcome email"
    assert f"cid:{LOGO_CID}" in _otp_html("Alice", "0421"), "logo cid missing from otp email"
    assert _logo_attachment() and _logo_attachment()["content_id"] == LOGO_CID, "logo attachment missing"
    ev = _event_created_html("<i>Bob</i>", "<b>Launch</b>", None, "draft")
    assert "&lt;b&gt;Launch&lt;/b&gt;" in ev and "&lt;i&gt;Bob&lt;/i&gt;" in ev, "event email not escaped"
    assert "Not scheduled" in ev, "missing start_time not handled"
    assert "01 Jan 2026" in _event_created_html("Bob", "Launch", datetime(2026, 1, 1, 9, 30), "live")

    # ── invitation email ──────────────────────────────────────────────────────
    invite = _invitation_html(
        org_name="<b>Acme</b>", inviter="<i>Bob</i>", role_label="Host",
        accept_url="https://x/accept-invitation?token=abc",
        decline_url="https://x/accept-invitation?token=abc&decline=1",
        expires="08 Aug 2026", event_title="<u>Launch</u>", event_when="01 Aug 2026, 10:00 AM",
        message="<script>alert(1)</script>",
    )
    for raw, escaped in [("<b>Acme</b>", "&lt;b&gt;Acme&lt;/b&gt;"),
                         ("<i>Bob</i>", "&lt;i&gt;Bob&lt;/i&gt;"),
                         ("<u>Launch</u>", "&lt;u&gt;Launch&lt;/u&gt;")]:
        assert escaped in invite, f"{raw} not HTML-escaped in the invitation email"
    assert "<script>alert(1)</script>" not in invite, "admin message not escaped — XSS in the invite"
    assert "accept-invitation?token=abc" in invite, "accept link missing"
    assert "decline=1" in invite, "decline link missing"
    assert "Accept invitation" in invite and "Decline" in invite, "both buttons required"
    assert "Host" in invite and "08 Aug 2026" in invite, "role + expiry must be stated"
    assert "01 Aug 2026, 10:00 AM" in invite, "event date must be stated"
    assert f"cid:{LOGO_CID}" in invite, "branding logo missing from the invitation"

    # An org-only invitation renders the SAME template with the event rows absent — not
    # "Event: None".
    org_only = _invitation_html(
        org_name="Acme", inviter="Bob", role_label="Viewer",
        accept_url="https://x/a", decline_url="https://x/d", expires="08 Aug 2026",
    )
    assert "Event" not in org_only, "event row must not render for an org-only invitation"
    assert "None" not in org_only, "missing fields must be dropped, never printed"
    print("ok")
