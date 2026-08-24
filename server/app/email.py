import base64
import html
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

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


def _send(
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    *,
    sender: str | None = None,
) -> bool:
    """Post one email to Resend. Best-effort: logs and swallows failures so a mail
    outage never breaks the request that triggered it.

    Returns True only when Resend accepted the message, so a caller that needs to know
    (security-class mail, where a silent drop leaves the recipient stuck) can react.
    Existing callers ignore the return value and keep their previous behavior.

    `text_body` adds the plain-text alternative required for every HTML email
    (ZST-EC-001 doctrine rule 9). `sender` overrides the default identity for templates
    that must ship from a specific approved sender, e.g. Zoiko Steam Security.
    """
    if not settings.RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set; skipping email to %s", to)
        return False
    payload = {
        "from": sender or settings.MAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html_body,
    }
    if text_body:
        payload["text"] = text_body
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
        return True
    except httpx.HTTPError as e:
        # Resend returns the reason in the body — surface it for debugging.
        body = getattr(e, "response", None)
        log.error("Email to %s failed: %s %s", to, e, body.text if body else "")
        return False


# ── Approved sender identities (ZST-EC-001 Section 03) ──────────────────────────────────
# The baseline names eleven sender identities. Only the address in MAIL_FROM is
# domain-authenticated today, so an identity is the approved DISPLAY NAME re-wrapped
# around that same authenticated address — changing the address instead would send from an
# unauthenticated domain and fail SPF/DKIM alignment. When more addresses are verified in
# Resend, this is the one place that has to change.
SENDER_SECURITY = "Zoiko Steam Security"


def _mail_from_address() -> str:
    """The bare address out of MAIL_FROM, which may be `Name <addr>` or just `addr`."""
    raw = settings.MAIL_FROM.strip()
    if "<" in raw and ">" in raw:
        return raw[raw.index("<") + 1 : raw.rindex(">")].strip()
    return raw


def _sender_identity(display_name: str) -> str:
    return f"{display_name} <{_mail_from_address()}>"


def _shell(inner: str) -> str:
    # Inline styles only — email clients strip <style>/external CSS.
    return f"""\
<div style="background:#f2f2f4;padding:32px 0;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:6px;overflow:hidden;">
    {inner}
  </div>
</div>"""


def _base_url() -> str:
    return settings.APP_URL.rstrip("/")


class UnsafeLinkError(RuntimeError):
    """Raised instead of emailing a link that violates the secure-link standard."""


def public_base_url() -> str:
    """The base URL for links that leave the platform, asserted safe before use.

    Outside development an emailed link must be HTTPS and must not point at localhost.
    Both were live audit findings: APP_URL is absent from the deployed environment file, so
    it silently falls back to http://localhost:5173 and every emailed link becomes a
    non-TLS, unreachable address — while still carrying a single-use credential. Failing the
    send is strictly better than delivering that, so this raises rather than degrades.
    """
    base = _base_url()
    if settings.ENVIRONMENT.strip().lower() == "development":
        return base
    low = base.lower()
    if not low.startswith("https://"):
        raise UnsafeLinkError(
            f"APP_URL must be https outside development (got {base!r}). "
            "Set APP_URL in the environment."
        )
    if "localhost" in low or "127.0.0.1" in low:
        raise UnsafeLinkError(
            f"APP_URL must not point at localhost outside development (got {base!r})."
        )
    return base


def verification_url(token: str) -> str:
    """IDN-001 verification link.

    Carries nothing but the opaque token: no email address, no username, no user id, no
    organization id, no tracking parameters. The token is already URL-safe
    (secrets.token_urlsafe); quoting is belt-and-braces so a future token format cannot
    break out of the query value.
    """
    return f"{public_base_url()}/verify-email?token={quote(token, safe='')}"


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


# ── IDN-001 Email verification and passwordless sign-in ─────────────────────────────────
# Class A (Security) · Sender: Zoiko Steam Security · Recipient: account holder.
# Copy below is the approved production copy from ZST-EC-001 v2.0 and must not be reworded
# without a governance decision.
#
# Controls applied here, per the template's own control list:
#   * purpose-bound, single-use link (see crud/identity.py) — the link is the whole payload
#   * no tracking pixel and no remote image: the only asset is the logo, carried as an
#     inline cid attachment, so opening the mail makes no network request
#   * no PII in the URL (see verification_url)
#   * no marketing or promotional module
#   * plain-text alternative always sent alongside the HTML
#   * body text at 16px and a CTA at ~4.5:1 contrast, per the accessibility standard

IDN_001_SUBJECT = "Verify your email for Zoiko Steam"
IDN_001_HEADLINE = "Confirm your email address."
IDN_001_BODY = (
    "A request was made to verify this email address for Zoiko Steam. Use the secure "
    "action below only on the device where you started the request."
)
IDN_001_CTA = "Verify email"
IDN_001_IGNORE = "If you did not request this, you can safely ignore this email."


def _idn_001_preheader(expiry_minutes: int) -> str:
    return f"This secure link expires in {expiry_minutes} minutes."


def _verification_html(verify_url: str, expiry_minutes: int, expires_at_utc: str) -> str:
    preheader = _idn_001_preheader(expiry_minutes)
    return _shell(f"""
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html.escape(preheader)}</div>
    {_header("Verify your email")}
    <div style="padding:24px 32px 40px;color:#333;font-size:16px;line-height:1.6;">
      <h2 style="margin:0 0 16px;font-size:18px;font-weight:600;color:#2e2e4d;">{IDN_001_HEADLINE}</h2>
      <p style="margin:0 0 16px;">{IDN_001_BODY}</p>
      <p style="margin:0 0 8px;">{html.escape(preheader)}</p>
      <p style="margin:0 0 24px;color:#4a4a5a;font-size:14px;">
        Expires at {html.escape(expires_at_utc)}.
      </p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{html.escape(verify_url, quote=True)}"
           style="background:#2e2e4d;color:#fff;text-decoration:none;padding:16px 32px;
           border-radius:4px;font-weight:bold;font-size:16px;display:inline-block;">
          {IDN_001_CTA}
        </a>
      </p>
      <p style="margin:0 0 8px;font-size:14px;color:#4a4a5a;">
        If the button does not work, copy this address into your browser:
      </p>
      <p style="margin:0 0 24px;font-size:14px;word-break:break-all;">
        <a href="{html.escape(verify_url, quote=True)}"
           style="color:#2e2e4d;">{html.escape(verify_url)}</a>
      </p>
      <p style="margin:0;color:#4a4a5a;font-size:14px;">{IDN_001_IGNORE}</p>
    </div>""")


def _verification_text(verify_url: str, expiry_minutes: int, expires_at_utc: str) -> str:
    """Plain-text alternative. Same facts, same single action, no markup."""
    return "\n".join([
        IDN_001_HEADLINE,
        "",
        IDN_001_BODY,
        "",
        _idn_001_preheader(expiry_minutes),
        f"Expires at {expires_at_utc}.",
        "",
        f"{IDN_001_CTA}: {verify_url}",
        "",
        IDN_001_IGNORE,
        "",
        "Zoiko Steam Security",
    ])


def send_email_verification_email(
    to: str, verify_url: str, expiry_minutes: int, expires_at_utc: str
) -> bool:
    """IDN-001. Returns True only if Resend accepted the message.

    No recipient name is interpolated: the address is unverified at this point, so the
    display name supplied at registration is unattested input and has no place in a
    security-class message.
    """
    return _send(
        to,
        IDN_001_SUBJECT,
        _verification_html(verify_url, expiry_minutes, expires_at_utc),
        _verification_text(verify_url, expiry_minutes, expires_at_utc),
        sender=_sender_identity(SENDER_SECURITY),
    )


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


def _invite_html(org_name: str, inviter: str, invite_url: str) -> str:
    safe_org = html.escape(org_name or "an organization")
    safe_inviter = html.escape(inviter or "An admin")
    return _shell(f"""
    {_header("You're invited")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>{safe_inviter} has invited you to join <strong>{safe_org}</strong> on ZoikoStream.</p>
      <p>Click below to accept the invitation and set up your account. This link expires soon.</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{invite_url}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          Accept invitation
        </a>
      </p>
      <p style="color:#888;font-size:13px;">If you weren't expecting this, you can ignore this email.</p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
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


def _assignment_html(name: str, event_title: str, role: str, org_name: str, event_url: str) -> str:
    safe_name = html.escape(name or "there")
    safe_title = html.escape(event_title or "an event")
    safe_org = html.escape(org_name or "your organization")
    safe_role = html.escape(role.title())
    return _shell(f"""
    {_header("You've been assigned")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {safe_name},</p>
      <p>You've been added as a <strong>{safe_role}</strong> for
         <strong>{safe_title}</strong> on {safe_org}'s ZoikoStream account.</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{event_url}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          Open the event
        </a>
      </p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def _registration_html(name: str, event_title: str, event_url: str) -> str:
    safe_name = html.escape(name or "there")
    safe_title = html.escape(event_title or "the event")
    return _shell(f"""
    {_header("You're registered")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {safe_name},</p>
      <p>You're registered for <strong>{safe_title}</strong>. We'll see you there —
         come back to this link when it's time to watch.</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{event_url}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          View the event
        </a>
      </p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def _viewer_invite_html(name: str, event_title: str, watch_url: str, inviter_name: str) -> str:
    safe_name = html.escape(name or "there")
    safe_title = html.escape(event_title or "an event")
    safe_inviter = html.escape(inviter_name or "The host")
    return _shell(f"""
    {_header("You're invited to watch")}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {safe_name},</p>
      <p>{safe_inviter} has invited you to watch <strong>{safe_title}</strong> on ZoikoStream.
         This link is yours — no account or password needed.</p>
      <p style="text-align:center;margin:32px 0;">
        <a href="{watch_url}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          Watch the event
        </a>
      </p>
      <p style="color:#888;font-size:13px;">If you weren't expecting this, you can ignore this email.</p>
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


# ── Commercial/order lifecycle (ZST-LE-COM-001 Section 21/Q2) ───────────────────────────
# "What confirmations are required?" doc Q2's list, one function per line item (readiness
# actions excepted — an operational status update, not a customer-facing commercial
# confirmation). Idempotency (doc Q2: "duplicate events must not duplicate customer
# emails") is inherited from the caller: every trigger point below is a crud.commercial
# state transition that its own state-machine guard only allows once (accept_order raises
# on an already-accepted order, capture_payment raises outside 'pending', etc.) — so a
# retried request can't re-trigger the same email without also re-succeeding a transition
# that's specifically guarded against re-succeeding. A real duplicate provider webhook is
# separately deduplicated by Payment.idempotency_key before it ever reaches here.

def _commercial_html(title: str, name: str, lines: list[str], rows: list[tuple[str, str]] | None = None,
                      cta_label: str | None = None, cta_url: str | None = None) -> str:
    safe_name = html.escape(name or "there")
    body = "".join(f"<p>{line}</p>" for line in lines)
    table = ""
    if rows:
        row_html = "".join(
            f'<tr><td style="padding:10px 0;color:#888;">{html.escape(k)}</td>'
            f'<td style="padding:10px 0;text-align:right;">{html.escape(v)}</td></tr>'
            for k, v in rows
        )
        table = f'<table style="width:100%;border-collapse:collapse;margin:24px 0;font-size:14px;">{row_html}</table>'
    cta = ""
    if cta_label and cta_url:
        cta = f"""
      <p style="text-align:center;margin:32px 0;">
        <a href="{cta_url}" style="background:#7ac142;color:#fff;text-decoration:none;
           padding:14px 28px;border-radius:4px;font-weight:bold;display:inline-block;">
          {html.escape(cta_label)}
        </a>
      </p>"""
    return _shell(f"""
    {_header(title)}
    <div style="padding:24px 32px 40px;color:#333;font-size:15px;line-height:1.6;">
      <p>Hi {safe_name},</p>
      {body}
      {table}
      {cta}
      <p style="margin-bottom:0;">Team ZoikoStream</p>
    </div>""")


def send_order_accepted_email(to: str, name: str, event_title: str, total_amount: str, currency: str,
                               order_url: str) -> None:
    """doc Q2 'Quote/order acceptance' + 'booking confirmation' — this app's order
    acceptance is the booking commitment (crud.commercial.accept_order)."""
    safe_title = html.escape(event_title or "your event")
    _send(to, f"Booking confirmed: {event_title}", _commercial_html(
        "Booking confirmed", name,
        [f"Your order for <strong>{safe_title}</strong> has been accepted — this event is now booked."],
        rows=[("Total", f"{currency} {total_amount}")],
        cta_label="View order", cta_url=order_url,
    ))


def send_payment_receipt_email(to: str, name: str, event_title: str, amount: str, currency: str,
                                order_url: str) -> None:
    """doc Q2 'payment receipt' (crud.commercial.capture_payment success)."""
    safe_title = html.escape(event_title or "your event")
    _send(to, f"Payment received: {event_title}", _commercial_html(
        "Payment received", name,
        [f"We've received your payment for <strong>{safe_title}</strong>. Thank you."],
        rows=[("Amount paid", f"{currency} {amount}")],
        cta_label="View order", cta_url=order_url,
    ))


def send_payment_failed_email(to: str, name: str, event_title: str, amount: str, currency: str,
                               reason: str | None, order_url: str) -> None:
    """doc Q2 'payment failure' (crud.commercial.authorize_payment failure path)."""
    safe_title = html.escape(event_title or "your event")
    lines = [f"A payment attempt for <strong>{safe_title}</strong> was not successful."]
    if reason:
        lines.append(f"Reason: {html.escape(reason)}")
    lines.append("No charge was made. Please try again or contact us for help.")
    _send(to, f"Payment issue: {event_title}", _commercial_html(
        "Payment couldn't be completed", name, lines,
        rows=[("Amount", f"{currency} {amount}")],
        cta_label="Review order", cta_url=order_url,
    ))


def send_change_order_accepted_email(to: str, name: str, event_title: str, price_delta: str, currency: str,
                                      order_url: str) -> None:
    """doc Q2 'material change order' (crud.commercial.accept_change_order)."""
    safe_title = html.escape(event_title or "your event")
    delta_label = f"+{currency} {price_delta}" if not price_delta.startswith("-") else f"{currency} {price_delta}"
    _send(to, f"Order change confirmed: {event_title}", _commercial_html(
        "Change order confirmed", name,
        [f"A change to your order for <strong>{safe_title}</strong> has been accepted."],
        rows=[("Price change", delta_label)],
        cta_label="View order", cta_url=order_url,
    ))


def send_cancellation_email(to: str, name: str, event_title: str, refund_amount: str | None, currency: str,
                             order_url: str) -> None:
    """doc Q2 'cancellation/reschedule' (crud.commercial.cancel_order)."""
    safe_title = html.escape(event_title or "your event")
    lines = [f"Your booking for <strong>{safe_title}</strong> has been canceled, as requested."]
    rows = [("Refund", f"{currency} {refund_amount}")] if refund_amount and refund_amount != "0" else None
    if not rows:
        lines.append("No refund applies under the cancellation policy for this booking.")
    _send(to, f"Booking canceled: {event_title}", _commercial_html(
        "Booking canceled", name, lines, rows=rows, cta_label="View order", cta_url=order_url,
    ))


def send_replay_available_email(to: str, name: str, event_title: str, watch_url: str) -> None:
    """doc Q2 'event completion/replay availability' (crud.commercial.publish_replay)."""
    safe_title = html.escape(event_title or "your event")
    _send(to, f"Replay available: {event_title}", _commercial_html(
        "Your replay is ready", name,
        [f"The recording for <strong>{safe_title}</strong> is now available to watch."],
        cta_label="Watch replay", cta_url=watch_url,
    ))


def send_refund_credit_email(to: str, name: str, event_title: str, amount: str, currency: str,
                              credit_type: str, order_url: str) -> None:
    """doc Q2 'refund/credit' (crud.commercial.execute_refund_credit)."""
    safe_title = html.escape(event_title or "your event")
    verb = "credited" if credit_type == "credit" else "waived" if credit_type == "fee_waiver" else "refunded"
    _send(to, f"{credit_type.replace('_', ' ').title()} processed: {event_title}", _commercial_html(
        f"Your {credit_type.replace('_', ' ')} has been processed", name,
        [f"A {currency} {amount} {credit_type.replace('_', ' ')} for <strong>{safe_title}</strong> has been {verb}."],
        cta_label="View order", cta_url=order_url,
    ))


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


def send_invitation_email(to: str, org_name: str, inviter: str, invite_url: str) -> None:
    _send(to, f"You're invited to join {org_name} on ZoikoStream", _invite_html(org_name, inviter, invite_url))


def send_reset_otp_email(to: str, name: str, otp: str) -> None:
    _send(to, "Your ZoikoStream password reset code", _otp_html(name, otp))


def send_assignment_email(to: str, name: str, event_title: str, role: str, org_name: str, event_url: str) -> None:
    _send(to, f"You've been added as {role} for {event_title}",
          _assignment_html(name, event_title, role, org_name, event_url))


def send_registration_confirmation_email(to: str, name: str, event_title: str, event_url: str) -> None:
    _send(to, f"You're registered for {event_title}", _registration_html(name, event_title, event_url))


def send_viewer_invite_email(to: str, name: str, event_title: str, watch_url: str, inviter_name: str) -> None:
    _send(to, f"You're invited to watch {event_title}",
          _viewer_invite_html(name, event_title, watch_url, inviter_name))


if __name__ == "__main__":
    # Offline self-check: best-effort behavior + HTML escaping + OTP rendering. No network.
    from unittest.mock import patch

    with patch.object(settings, "RESEND_API_KEY", ""):
        send_welcome_email("nobody@example.com", "<script>")  # must not raise
        send_reset_otp_email("nobody@example.com", "<script>", "0421")
    assert "&lt;script&gt;" in _welcome_html("<script>"), "name not HTML-escaped"
    assert "0421" in _otp_html("Alice", "0421"), "otp not rendered"
    assert "Alice" in _otp_html("Alice", "0421")
    assert f"cid:{LOGO_CID}" in _welcome_html("Alice"), "logo cid missing from welcome email"
    assert f"cid:{LOGO_CID}" in _otp_html("Alice", "0421"), "logo cid missing from otp email"
    assert _logo_attachment() and _logo_attachment()["content_id"] == LOGO_CID, "logo attachment missing"
    invite = _invite_html("<b>Acme</b>", "<i>Bob</i>", "https://x/accept-invite?token=abc")
    assert "&lt;b&gt;Acme&lt;/b&gt;" in invite and "&lt;i&gt;Bob&lt;/i&gt;" in invite, "invite not escaped"
    ev = _event_created_html("<i>Bob</i>", "<b>Launch</b>", None, "draft")
    assert "&lt;b&gt;Launch&lt;/b&gt;" in ev and "&lt;i&gt;Bob&lt;/i&gt;" in ev, "event email not escaped"
    assert "Not scheduled" in ev, "missing start_time not handled"
    assert "01 Jan 2026" in _event_created_html("Bob", "Launch", datetime(2026, 1, 1, 9, 30), "live")
    assert "accept-invite?token=abc" in invite, "invite link missing"
    asn = _assignment_html("<i>Bob</i>", "<b>Launch</b>", "host", "<u>Acme</u>", "https://x/host/dashboard?event=1")
    assert "&lt;i&gt;Bob&lt;/i&gt;" in asn and "&lt;b&gt;Launch&lt;/b&gt;" in asn and "&lt;u&gt;Acme&lt;/u&gt;" in asn, "assignment email not escaped"
    assert "Host" in asn, "role not rendered"
    vinv = _viewer_invite_html("<i>Bob</i>", "<b>Launch</b>", "https://x/events/1/watch?reg=abc", "<u>Alice</u>")
    assert "&lt;i&gt;Bob&lt;/i&gt;" in vinv and "&lt;b&gt;Launch&lt;/b&gt;" in vinv and "&lt;u&gt;Alice&lt;/u&gt;" in vinv, "viewer invite not escaped"
    assert "reg=abc" in vinv, "viewer invite link missing the access token"

    # Commercial lifecycle (doc Q2) — _commercial_html escaping/rendering, then every
    # send_*_email wrapper with RESEND_API_KEY patched blank so _send stays a no-op
    # (same technique as send_welcome_email/send_reset_otp_email above — never a real call).
    ch = _commercial_html("<b>Title</b>", "<i>Name</i>", ["<u>line</u>"], rows=[("<s>Key</s>", "<s>Val</s>")],
                           cta_label="<em>Go</em>", cta_url="https://x/y")
    assert "&lt;i&gt;Name&lt;/i&gt;" in ch, "commercial_html name not escaped"
    assert "&lt;s&gt;Key&lt;/s&gt;" in ch and "&lt;s&gt;Val&lt;/s&gt;" in ch, "commercial_html row not escaped"
    assert "&lt;em&gt;Go&lt;/em&gt;" in ch, "commercial_html cta label not escaped"
    assert "https://x/y" in ch, "commercial_html cta url missing"
    assert _commercial_html("T", "N", ["line"]) and "margin:32px 0" not in _commercial_html("T", "N", ["line"]), \
        "commercial_html must omit the CTA block entirely when no cta_url is given"

    with patch.object(settings, "RESEND_API_KEY", ""):
        send_order_accepted_email("nobody@example.com", "<script>", "<b>Ev</b>", "100.00", "USD", "https://x/o")
        send_payment_receipt_email("nobody@example.com", "<script>", "<b>Ev</b>", "100.00", "USD", "https://x/o")
        send_payment_failed_email("nobody@example.com", "<script>", "<b>Ev</b>", "100.00", "USD", "declined", "https://x/o")
        send_change_order_accepted_email("nobody@example.com", "<script>", "<b>Ev</b>", "50.00", "USD", "https://x/o")
        send_cancellation_email("nobody@example.com", "<script>", "<b>Ev</b>", "80.00", "USD", "https://x/o")
        send_cancellation_email("nobody@example.com", "<script>", "<b>Ev</b>", None, "USD", "https://x/o")
        send_replay_available_email("nobody@example.com", "<script>", "<b>Ev</b>", "https://x/watch")
        send_refund_credit_email("nobody@example.com", "<script>", "<b>Ev</b>", "20.00", "USD", "fee_waiver", "https://x/o")
    print("ok")
